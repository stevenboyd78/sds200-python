from __future__ import annotations

import base64
import io
import queue
import subprocess
import threading
from collections.abc import Callable
from time import monotonic, sleep
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from sds200.broadcastify import (
    BROADCASTIFY_METADATA_PATH,
    BROADCASTIFY_MONO_BITRATE_KBPS,
    BROADCASTIFY_PASSWORD_SECRET,
    BROADCASTIFY_SAMPLE_RATE,
    BroadcastifyConfig,
    BroadcastifyConnection,
    BroadcastifyMetadataPublication,
    BroadcastifyMetadataPublicationFactory,
    create_broadcastify_metadata_publisher,
    create_broadcastify_sink,
)
from sds200.exceptions import AudioOutputError
from sds200.presentation import ActivityStatus, AvailabilityStatus
from sds200.remote_audio import EnvironmentSecret
from sds200.remote_audio_metadata import RemoteStreamMetadata


class FakeSocket:
    def __init__(
        self,
        response: bytes = b"HTTP/1.0 200 OK\r\n\r\n",
        *,
        fail_stream: bool = False,
    ) -> None:
        self._responses: queue.Queue[bytes] = queue.Queue()
        self._responses.put(response)
        self.timeout: float | None = None
        self.sent: list[bytes] = []
        self.fail_stream = fail_stream
        self.stream_failed = threading.Event()
        self.shutdown_calls = 0
        self.closed = False

    def settimeout(self, value: float | None) -> None:
        self.timeout = value

    def sendall(self, data: bytes) -> None:
        if self.closed:
            raise OSError("socket closed")
        if self.sent and self.fail_stream:
            self.stream_failed.set()
            raise OSError("stream disconnected")
        self.sent.append(data)

    def recv(self, size: int) -> bytes:
        del size
        try:
            return self._responses.get_nowait()
        except queue.Empty:
            return b""

    def shutdown(self, how: int) -> None:
        del how
        self.shutdown_calls += 1

    def close(self) -> None:
        self.closed = True


class QueueReader:
    def __init__(self) -> None:
        self._queue: queue.Queue[bytes | None] = queue.Queue()
        self.closed = False

    def emit(self, data: bytes) -> None:
        self._queue.put(data)

    def read(self, size: int = -1) -> bytes:
        del size
        item = self._queue.get()
        return b"" if item is None else item

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._queue.put(None)


class RecordingWriter:
    def __init__(
        self,
        *,
        gate: threading.Event | None = None,
        started: threading.Event | None = None,
    ) -> None:
        self.gate = gate
        self.started = started
        self.writes: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> int:
        if self.started is not None:
            self.started.set()
        if self.gate is not None:
            assert self.gate.wait(timeout=1.0)
        if self.closed:
            raise BrokenPipeError("encoder input closed")
        self.writes.append(data)
        return len(data)

    def flush(self) -> None:
        if self.closed:
            raise BrokenPipeError("encoder input closed")

    def close(self) -> None:
        self.closed = True
        if self.gate is not None:
            self.gate.set()


class FakeEncoder:
    def __init__(
        self,
        *,
        stdin: RecordingWriter | None = None,
        returncode: int = 0,
        error: bytes = b"",
    ) -> None:
        self.stdin = RecordingWriter() if stdin is None else stdin
        self.stdout = QueueReader()
        self.stderr = io.BytesIO(error)
        self.final_returncode = returncode
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        if self.returncode is None:
            self.returncode = self.final_returncode
            self.stdout.close()
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15
        self.stdout.close()

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self.stdout.close()


class FinalOutputEncoder(FakeEncoder):
    def wait(self, timeout: float | None = None) -> int:
        self.stdout.emit(b"final-mp3-a")
        self.stdout.emit(b"final-mp3-b")
        return super().wait(timeout=timeout)


class StubbornEncoder(FakeEncoder):
    def wait(self, timeout: float | None = None) -> int:
        delay = 0.0 if timeout is None else timeout
        sleep(delay)
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=delay)

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self.stdout.close()


def config(**overrides: object) -> BroadcastifyConfig:
    values: dict[str, Any] = {
        "name": "county-feed",
        "server": "audio1.broadcastify.com",
        "mount": "/abc123",
        "password": EnvironmentSecret("SDS200_BROADCASTIFY_PASSWORD"),
        "connect_timeout": 1.0,
        "socket_timeout": 1.0,
        "encoder_stop_timeout": 0.1,
        "stop_timeout": 1.0,
    }
    values.update(overrides)
    return BroadcastifyConfig(**values)


def wait_until(predicate: Callable[[], bool], *, timeout: float = 1.0) -> None:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if predicate():
            return
        sleep(0.005)
    raise AssertionError("Condition was not satisfied before the timeout.")


def test_broadcastify_config_builds_fixed_profile_and_remote_secret() -> None:
    feed = config()
    command = feed.ffmpeg_command()
    encoder_config = feed.encoder_config()
    remote = feed.remote_destination()

    assert feed.endpoint == "http://audio1.broadcastify.com:80/abc123"
    assert encoder_config.name == "Broadcastify FFmpeg encoder"
    assert encoder_config.command == command
    assert encoder_config.stop_timeout == feed.encoder_stop_timeout
    assert ("-ar", "8000") in tuple(zip(command, command[1:], strict=False))
    assert ("-ar", str(BROADCASTIFY_SAMPLE_RATE)) in tuple(
        zip(command, command[1:], strict=False)
    )
    assert ("-b:a", f"{BROADCASTIFY_MONO_BITRATE_KBPS}k") in tuple(
        zip(command, command[1:], strict=False)
    )
    assert remote.endpoint == feed.endpoint
    assert remote.secrets == {
        BROADCASTIFY_PASSWORD_SECRET: EnvironmentSecret(
            "SDS200_BROADCASTIFY_PASSWORD"
        )
    }


@pytest.mark.parametrize("port", [0, 443, 8443])
def test_broadcastify_config_rejects_unsupported_ports(port: int) -> None:
    with pytest.raises(ValueError, match="port must be one of"):
        config(port=port)


@pytest.mark.parametrize("mount", ["feed", "/", "/bad feed", "/feed?token=x"])
def test_broadcastify_config_rejects_invalid_mounts(mount: str) -> None:
    with pytest.raises(ValueError, match="mount"):
        config(mount=mount)


def test_broadcastify_connection_authenticates_and_streams_encoded_bytes() -> None:
    source_socket = FakeSocket()
    encoder = FakeEncoder()
    commands: list[tuple[str, ...]] = []
    connection = BroadcastifyConnection(
        config(stream_name="County Public Safety"),
        "feed-password",
        socket_factory=lambda address, timeout: source_socket,
        encoder_factory=lambda command: commands.append(command) or encoder,
    )
    pcm = b"\x01\x00\x02\x00"

    connection.write_pcm(pcm)
    encoder.stdout.emit(b"encoded-mp3")
    wait_until(lambda: source_socket.sent[-1:] == [b"encoded-mp3"])

    request = source_socket.sent[0].decode("utf-8")
    expected_credentials = base64.b64encode(b"source:feed-password").decode("ascii")
    assert request.startswith("SOURCE /abc123 ICE/1.0\r\n")
    assert "Host: audio1.broadcastify.com:80\r\n" in request
    assert f"Authorization: Basic {expected_credentials}\r\n" in request
    assert "feed-password" not in request
    assert "Content-Type: audio/mpeg\r\n" in request
    assert "Ice-Name: County Public Safety\r\n" in request
    assert "Ice-Genre: Scanner\r\n" in request
    assert "Ice-URL: https://www.broadcastify.com/\r\n" in request
    assert "Ice-Public: 1\r\n" in request
    assert f"Ice-Bitrate: {BROADCASTIFY_MONO_BITRATE_KBPS}\r\n" in request
    assert f"samplerate={BROADCASTIFY_SAMPLE_RATE};channels=1\r\n" in request
    assert encoder.stdin.writes == [pcm]
    assert commands == [config(stream_name="County Public Safety").ffmpeg_command()]

    connection.close()
    assert source_socket.closed
    assert encoder.stdin.closed
    assert encoder.returncode == 0


def test_broadcastify_connection_drains_final_encoder_output() -> None:
    source_socket = FakeSocket()
    encoder = FinalOutputEncoder()
    connection = BroadcastifyConnection(
        config(),
        "feed-password",
        socket_factory=lambda address, timeout: source_socket,
        encoder_factory=lambda command: encoder,
    )

    connection.close()

    assert source_socket.sent[-2:] == [
        b"final-mp3-a",
        b"final-mp3-b",
    ]


def test_broadcastify_connection_rejects_unauthorized_source() -> None:
    source_socket = FakeSocket(b"HTTP/1.0 401 Unauthorized\r\n\r\n")
    encoder_started = False

    def encoder_factory(command: tuple[str, ...]) -> FakeEncoder:
        nonlocal encoder_started
        del command
        encoder_started = True
        return FakeEncoder()

    with pytest.raises(AudioOutputError, match="status 401"):
        BroadcastifyConnection(
            config(),
            "feed-password",
            socket_factory=lambda address, timeout: source_socket,
            encoder_factory=encoder_factory,
        )

    assert source_socket.closed
    assert not encoder_started


def test_broadcastify_connection_surfaces_stream_disconnect() -> None:
    source_socket = FakeSocket(fail_stream=True)
    encoder = FakeEncoder()
    connection = BroadcastifyConnection(
        config(),
        "feed-password",
        socket_factory=lambda address, timeout: source_socket,
        encoder_factory=lambda command: encoder,
    )

    encoder.stdout.emit(b"encoded-mp3")
    assert source_socket.stream_failed.wait(timeout=1.0)
    with pytest.raises(AudioOutputError, match="stream failed"):
        connection.write_pcm(b"\x01\x00")

    connection.interrupt()
    connection.close()


def test_broadcastify_connection_reports_encoder_exit_diagnostic() -> None:
    source_socket = FakeSocket()
    encoder = FakeEncoder(
        returncode=3,
        error=b"invalid encoder configuration\n",
    )
    connection = BroadcastifyConnection(
        config(),
        "feed-password",
        socket_factory=lambda address, timeout: source_socket,
        encoder_factory=lambda command: encoder,
    )

    with pytest.raises(
        AudioOutputError,
        match=(
            "Broadcastify FFmpeg encoder exited with status 3: "
            "invalid encoder configuration"
        ),
    ):
        connection.close()

    assert source_socket.closed
    assert encoder.stdin.closed
    assert encoder.stdout.closed
    assert encoder.stderr.closed


def test_broadcastify_connection_interrupts_encoder_and_socket() -> None:
    source_socket = FakeSocket()
    gate = threading.Event()
    started = threading.Event()
    encoder = FakeEncoder(stdin=RecordingWriter(gate=gate, started=started))
    connection = BroadcastifyConnection(
        config(),
        "feed-password",
        socket_factory=lambda address, timeout: source_socket,
        encoder_factory=lambda command: encoder,
    )
    errors: list[BaseException] = []

    writer = threading.Thread(
        target=lambda: _capture_error(
            errors,
            lambda: connection.write_pcm(b"\x01\x00"),
        )
    )
    writer.start()
    assert started.wait(timeout=1.0)

    connection.interrupt()
    writer.join(timeout=1.0)
    connection.close()

    assert not writer.is_alive()
    assert encoder.terminated
    assert source_socket.closed
    assert source_socket.shutdown_calls >= 1
    assert len(errors) == 1
    assert isinstance(errors[0], AudioOutputError)


def test_broadcastify_sink_shutdown_bounds_stubborn_encoder_cleanup() -> None:
    source_socket = FakeSocket()
    encoder = StubbornEncoder()
    sink = create_broadcastify_sink(
        config(encoder_stop_timeout=0.08, stop_timeout=0.5),
        environ={"SDS200_BROADCASTIFY_PASSWORD": "feed-password"},
        socket_factory=lambda address, timeout: source_socket,
        encoder_factory=lambda command: encoder,
    )

    sink.start()
    sink.submit_pcm(b"\x01\x00")
    wait_until(lambda: sink.snapshot().statistics.bytes_written == 2)

    started = monotonic()
    with pytest.raises(AudioOutputError, match="FFmpeg encoder did not stop"):
        sink.stop()
    elapsed = monotonic() - started

    assert elapsed < 0.5
    assert source_socket.closed
    assert encoder.stdin.closed
    assert encoder.stdout.closed
    assert encoder.stderr.closed
    assert encoder.terminated
    assert encoder.killed
    assert sink.snapshot().state == "stopped"


def test_create_broadcastify_sink_uses_remote_retry_and_redaction_core() -> None:
    source_socket = FakeSocket()
    encoder = FakeEncoder()
    secret = "do-not-log-this"
    sink = create_broadcastify_sink(
        config(),
        environ={"SDS200_BROADCASTIFY_PASSWORD": secret},
        socket_factory=lambda address, timeout: source_socket,
        encoder_factory=lambda command: encoder,
    )
    pcm = b"\x01\x00"

    sink.start()
    sink.submit_pcm(pcm)
    wait_until(lambda: sink.snapshot().statistics.bytes_written == len(pcm))

    snapshot = sink.snapshot()
    assert snapshot.connected
    assert secret not in repr(snapshot)
    assert snapshot.statistics.bytes_written == len(pcm)

    sink.stop()
    assert source_socket.closed
    assert encoder.terminated


def _capture_error(
    errors: list[BaseException],
    action: Callable[[], None],
) -> None:
    try:
        action()
    except BaseException as error:
        errors.append(error)


def stream_metadata(title: str = "County | Fire | Dispatch") -> RemoteStreamMetadata:
    return RemoteStreamMetadata(
        activity=ActivityStatus.RECEIVING,
        availability=AvailabilityStatus.AVAILABLE,
        channel=title,
    )


def test_broadcastify_config_builds_remote_metadata_destination() -> None:
    feed = config()
    destination = feed.remote_metadata_destination(
        minimum_update_interval=2.5,
    )

    assert feed.metadata_endpoint == (
        "http://audio1.broadcastify.com:80/admin/metadata"
        "?mount=%2Fabc123&mode=updinfo"
    )
    assert destination.endpoint == feed.metadata_endpoint
    assert destination.secrets == {
        BROADCASTIFY_PASSWORD_SECRET: EnvironmentSecret(
            "SDS200_BROADCASTIFY_PASSWORD"
        )
    }
    assert destination.minimum_update_interval == 2.5
    assert destination.stop_timeout == feed.stop_timeout
    assert destination.reconnect_policy == feed.reconnect_policy


def test_broadcastify_metadata_publication_sends_encoded_update() -> None:
    feed = config()
    source_socket = FakeSocket()
    publication = BroadcastifyMetadataPublication(
        feed,
        "feed-password",
        stream_metadata("County | Fire & EMS"),
        socket_factory=lambda address, timeout: source_socket,
    )

    publication.publish()
    publication.close()
    publication.close()

    request = source_socket.sent[0].decode("utf-8")
    request_line = request.splitlines()[0]
    method, target, protocol = request_line.split()
    parsed = urlsplit(target)
    query = parse_qs(parsed.query)

    expected_credentials = base64.b64encode(
        b"source:feed-password"
    ).decode("ascii")
    assert method == "GET"
    assert protocol == "HTTP/1.0"
    assert parsed.path == BROADCASTIFY_METADATA_PATH
    assert query == {
        "mount": ["/abc123"],
        "mode": ["updinfo"],
        "song": ["County | Fire & EMS"],
    }
    assert "Host: audio1.broadcastify.com:80\r\n" in request
    assert f"Authorization: Basic {expected_credentials}\r\n" in request
    assert "feed-password" not in request
    assert "User-Agent: sds200-python\r\n" in request
    assert "Connection: close\r\n" in request
    assert source_socket.closed


def test_broadcastify_metadata_publication_rejects_error_response() -> None:
    source_socket = FakeSocket(b"HTTP/1.0 401 Unauthorized\r\n\r\n")
    publication = BroadcastifyMetadataPublication(
        config(),
        "feed-password",
        stream_metadata(),
        socket_factory=lambda address, timeout: source_socket,
    )

    with pytest.raises(AudioOutputError, match="status 401"):
        publication.publish()

    assert source_socket.closed


def test_broadcastify_metadata_publication_rejects_malformed_response() -> None:
    source_socket = FakeSocket(b"not-http\r\n\r\n")
    publication = BroadcastifyMetadataPublication(
        config(),
        "feed-password",
        stream_metadata(),
        socket_factory=lambda address, timeout: source_socket,
    )

    with pytest.raises(AudioOutputError, match="invalid Icecast metadata"):
        publication.publish()

    assert source_socket.closed


def test_broadcastify_metadata_factory_validates_endpoint_and_secret() -> None:
    feed = config()
    factory = BroadcastifyMetadataPublicationFactory(feed)
    destination = feed.remote_metadata_destination()
    metadata = stream_metadata()

    with pytest.raises(AudioOutputError, match="unexpected endpoint"):
        factory(
            type(destination)(
                name=destination.name,
                endpoint="https://example.invalid/admin/metadata",
                secrets=destination.secrets,
            ),
            {BROADCASTIFY_PASSWORD_SECRET: "feed-password"},
            metadata,
        )

    with pytest.raises(AudioOutputError, match="unexpected endpoint"):
        factory(
            config(mount="/other").remote_metadata_destination(),
            {BROADCASTIFY_PASSWORD_SECRET: "feed-password"},
            metadata,
        )

    with pytest.raises(AudioOutputError, match="was not resolved"):
        factory(destination, {}, metadata)


def test_broadcastify_metadata_publication_interrupts_during_connect() -> None:
    source_socket = FakeSocket()
    connect_started = threading.Event()
    release_connect = threading.Event()

    def socket_factory(
        address: tuple[str, int],
        timeout: float,
    ) -> FakeSocket:
        del address, timeout
        connect_started.set()
        assert release_connect.wait(timeout=1.0)
        return source_socket

    publication = BroadcastifyMetadataPublication(
        config(),
        "feed-password",
        stream_metadata(),
        socket_factory=socket_factory,
    )
    errors: list[BaseException] = []
    worker = threading.Thread(
        target=lambda: _capture_error(errors, publication.publish)
    )

    worker.start()
    assert connect_started.wait(timeout=1.0)
    publication.interrupt()
    release_connect.set()
    worker.join(timeout=1.0)
    publication.close()

    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], AudioOutputError)
    assert "interrupted" in str(errors[0])
    assert source_socket.closed
    assert source_socket.sent == []


def test_create_broadcastify_metadata_publisher_uses_worker() -> None:
    source_socket = FakeSocket()
    publisher = create_broadcastify_metadata_publisher(
        config(),
        environ={
            "SDS200_BROADCASTIFY_PASSWORD": "feed-password",
        },
        socket_factory=lambda address, timeout: source_socket,
    )

    publisher.start()
    publisher.submit(stream_metadata("Dispatch"))
    wait_until(lambda: publisher.snapshot().publications == 1)

    snapshot = publisher.snapshot()
    assert snapshot.last_published_title == "Dispatch"
    assert snapshot.failures == 0

    publisher.stop()

    request = source_socket.sent[0].decode("utf-8")
    target = request.splitlines()[0].split()[1]
    assert parse_qs(urlsplit(target).query)["song"] == ["Dispatch"]
    assert source_socket.closed
