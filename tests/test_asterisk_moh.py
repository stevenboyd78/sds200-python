from __future__ import annotations

import os
import signal
import threading
from collections.abc import Callable
from time import monotonic, sleep
from typing import BinaryIO, Self

import pytest

from sds200 import cli
from sds200.asterisk_moh import (
    ASTERISK_MOH_CHANNELS,
    ASTERISK_MOH_FORMAT,
    ASTERISK_MOH_SAMPLE_RATE,
    ASTERISK_MOH_SAMPLE_WIDTH,
    AsteriskMohSignalController,
    PcmStreamSink,
)
from sds200.audio import AudioChunk, AudioChunkHandler
from sds200.exceptions import AudioOutputError
from sds200.network_audio import NetworkAudioStatistics
from sds200.profiles import ConnectionProfile


def wait_until(predicate: Callable[[], bool], *, timeout: float = 1.0) -> None:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if predicate():
            return
        sleep(0.005)
    raise AssertionError("Condition was not satisfied before the timeout.")


def pipe_stream() -> tuple[int, BinaryIO]:
    read_fd, write_fd = os.pipe()
    return read_fd, os.fdopen(write_fd, "wb", buffering=0)


class FakeNetworkAudioTransport:
    def __init__(self) -> None:
        self._running = False
        self.started = False
        self.stopped = False

    @property
    def endpoint(self) -> str:
        return "rtsp://192.0.2.25/au:scanner.au"

    @property
    def running(self) -> bool:
        return self._running

    @property
    def statistics(self) -> NetworkAudioStatistics:
        return NetworkAudioStatistics(packets_delivered=1)

    def start(self, handler: AudioChunkHandler) -> None:
        self.started = True
        self._running = True
        handler(AudioChunk(bytes((0xFF, 0x80))))

    def stop(self) -> None:
        self.stopped = True
        self._running = False


class EventSignalController:
    def __init__(self, event: threading.Event) -> None:
        self.event = event

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def wait(self, timeout: float | None = None) -> bool:
        return self.event.wait(timeout)


def test_asterisk_profile_constants_match_decoded_pcm() -> None:
    assert ASTERISK_MOH_FORMAT == "slin"
    assert ASTERISK_MOH_SAMPLE_RATE == 8000
    assert ASTERISK_MOH_CHANNELS == 1
    assert ASTERISK_MOH_SAMPLE_WIDTH == 2


def test_pcm_stream_sink_handles_short_writes() -> None:
    read_fd, output = pipe_stream()

    def short_write(fd: int, data: bytes) -> int:
        return os.write(fd, data[:3])

    sink = PcmStreamSink(output, fd_write=short_write)
    payload = b"\x01\x00\x02\x00\x03\x00\x04\x00"
    original_blocking = os.get_blocking(output.fileno())
    sink.start()
    sink.submit_pcm(payload)
    wait_until(lambda: sink.statistics.bytes_written == len(payload))
    sink.stop()
    assert os.get_blocking(output.fileno()) is original_blocking
    output.close()

    assert os.read(read_fd, len(payload)) == payload
    os.close(read_fd)
    assert sink.statistics.bytes_written == len(payload)


def test_pcm_stream_sink_treats_broken_pipe_as_reader_close() -> None:
    read_fd, output = pipe_stream()
    os.close(read_fd)
    sink = PcmStreamSink(output)
    sink.start()
    sink.submit_pcm(b"\x01\x00")
    assert sink.wait(timeout=1.0)

    snapshot = sink.snapshot()
    assert snapshot.reader_closed
    assert snapshot.error is None
    assert snapshot.statistics.bytes_dropped == 2
    sink.stop()
    output.close()


def test_pcm_stream_sink_surfaces_invalid_writer_progress() -> None:
    read_fd, output = pipe_stream()
    sink = PcmStreamSink(output, fd_write=lambda fd, data: 0)
    sink.start()
    sink.submit_pcm(b"\x01\x00")
    assert sink.wait(timeout=1.0)

    with pytest.raises(AudioOutputError, match="no forward progress"):
        sink.stop()
    output.close()
    os.close(read_fd)


def test_pcm_stream_sink_shutdown_interrupts_nonwritable_output() -> None:
    read_fd, output = pipe_stream()

    def blocked_write(fd: int, data: bytes) -> int:
        del fd, data
        raise BlockingIOError

    sink = PcmStreamSink(
        output,
        stop_timeout=0.2,
        poll_interval=0.01,
        fd_write=blocked_write,
        wait_writable=lambda fd, timeout: sleep(timeout) is None,
    )
    sink.start()
    sink.submit_pcm(b"\x01\x00")
    started = monotonic()
    sink.stop()
    elapsed = monotonic() - started

    output.close()
    os.close(read_fd)
    assert elapsed < 0.2
    assert sink.statistics.bytes_dropped == 2


def test_signal_controller_installs_sets_and_restores_handlers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed: dict[int, object] = {}
    restored: list[tuple[int, object]] = []
    original = object()

    monkeypatch.setattr(signal, "getsignal", lambda signum: original)

    def install(signum: int, handler: object) -> object:
        if signum in installed:
            restored.append((signum, handler))
        else:
            installed[signum] = handler
        return original

    monkeypatch.setattr(signal, "signal", install)

    controller = AsteriskMohSignalController()
    with controller:
        term = int(signal.SIGTERM)
        handler = installed[term]
        assert callable(handler)
        handler(term, None)
        assert controller.wait(timeout=0)
        assert controller.last_signal == term

    assert restored
    assert all(handler is original for _, handler in restored)


def test_asterisk_moh_parser_accepts_profile_and_transport_options() -> None:
    args = cli.build_parser().parse_args(
        [
            "--profile",
            "scanner-moh",
            "asterisk-moh",
            "--rtsp-port",
            "8554",
            "--rtsp-timeout",
            "3",
            "--rtp-bind-address",
            "192.0.2.10",
            "--rtp-bind-port",
            "40000",
            "--keepalive-interval",
            "20",
            "--buffer-seconds",
            "2",
            "--stop-timeout",
            "1.5",
        ]
    )

    assert args.profile == "scanner-moh"
    assert args.rtsp_port == 8554
    assert args.rtsp_timeout == 3.0
    assert args.rtp_bind_address == "192.0.2.10"
    assert args.rtp_bind_port == 40000
    assert args.keepalive_interval == 20.0
    assert args.buffer_seconds == 2.0
    assert args.stop_timeout == 1.5


def test_asterisk_moh_profile_resolves_network_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Store:
        def __init__(self, path: object) -> None:
            del path

        def get(self, name: str) -> ConnectionProfile:
            assert name == "scanner-moh"
            return ConnectionProfile.network(name, "192.0.2.25")

    monkeypatch.setattr(cli, "ProfileStore", Store)
    args = cli.build_parser().parse_args(["--profile", "scanner-moh", "asterisk-moh"])

    assert cli._asterisk_moh_host(args) == "192.0.2.25"


def test_asterisk_moh_profile_requires_network_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Store:
        def __init__(self, path: object) -> None:
            del path

        def get(self, name: str) -> ConnectionProfile:
            assert name == "scanner-moh"
            return ConnectionProfile.serial(name, "/dev/ttyACM0", model="SDS200")

    monkeypatch.setattr(cli, "ProfileStore", Store)
    args = cli.build_parser().parse_args(["--profile", "scanner-moh", "asterisk-moh"])

    with pytest.raises(ValueError, match="network-capable"):
        cli._asterisk_moh_host(args)


def test_asterisk_moh_cli_writes_only_raw_pcm_to_stdout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    transport = FakeNetworkAudioTransport()
    read_fd, output = pipe_stream()
    received = bytearray()
    received_event = threading.Event()

    def read_output() -> None:
        received.extend(os.read(read_fd, 4))
        received_event.set()

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()

    monkeypatch.setattr(cli, "NetworkAudioTransport", lambda *args, **kwargs: transport)
    monkeypatch.setattr(cli, "_stdout_binary_stream", lambda: output)
    monkeypatch.setattr(
        cli,
        "AsteriskMohSignalController",
        lambda: EventSignalController(received_event),
    )

    result = cli.main(["--host", "192.0.2.25", "asterisk-moh"])
    reader.join(timeout=1.0)
    output.close()
    os.close(read_fd)

    assert result == 0
    assert transport.started
    assert transport.stopped
    assert bytes(received) == b"\x00\x00\x7c\x7d"
    assert capsys.readouterr().out == ""
