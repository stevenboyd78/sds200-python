from __future__ import annotations

import json
import logging
import socket
import struct
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sds200.daemon_ipc import (
    DaemonSocketListener,
    DaemonSocketLocation,
    DaemonSocketSource,
)
from sds200.daemon_pcmu_server import DaemonPcmuServer
from sds200.exceptions import DaemonIpcError
from sds200.pcmu import PcmuPacket
from sds200.pcmu_protocol import (
    PCMU_STREAM_HEADER_BYTES,
    decode_pcmu_delivery,
)
from sds200.pcmu_subscriptions import (
    PcmuPublication,
    PcmuPublisher,
    PcmuSubscription,
)

_PREFIX = struct.Struct("!4sBBHI")


class FakePcmuStream:
    def __init__(self) -> None:
        self.close_calls = 0
        self._publisher = PcmuPublisher(
            queue_capacity=4,
            max_subscribers=8,
        )

    @property
    def subscriber_count(self) -> int:
        return self._publisher.subscriber_count

    def subscribe(self) -> PcmuSubscription:
        return self._publisher.subscribe()

    def publish(self, packet: PcmuPacket) -> PcmuPublication:
        return self._publisher.publish(packet)

    def close(self) -> None:
        self.close_calls += 1
        self._publisher.close()


def make_packet(
    sequence: int,
    payload: bytes = b"\x01\x02\x03\x04",
) -> PcmuPacket:
    return PcmuPacket(
        endpoint="rtsp://192.0.2.25/au:scanner.au",
        sequence=sequence,
        timestamp=sequence * len(payload),
        ssrc=0x56650DAA,
        payload=payload,
        observed_at=datetime(2026, 8, 5, 7, 50, tzinfo=UTC),
    )


def make_server(
    tmp_path: Path,
    stream: FakePcmuStream,
    **kwargs: object,
) -> tuple[DaemonPcmuServer, Path]:
    path = tmp_path / "pcmu.sock"
    listener = DaemonSocketListener(
        DaemonSocketLocation(
            path,
            DaemonSocketSource.EXPLICIT,
        )
    )
    server = DaemonPcmuServer(
        listener,
        stream,
        **kwargs,  # type: ignore[arg-type]
    )
    return server, path


def connect(path: Path) -> socket.socket:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(1.0)
    client.connect(str(path))
    return client


def receive_exact(
    client: socket.socket,
    size: int,
) -> bytes:
    payload = bytearray()
    while len(payload) < size:
        chunk = client.recv(size - len(payload))
        if not chunk:
            break
        payload.extend(chunk)
    return bytes(payload)


def read_frame(client: socket.socket) -> bytes:
    prefix = receive_exact(client, _PREFIX.size)
    if not prefix:
        return b""
    if len(prefix) != _PREFIX.size:
        raise AssertionError("PCMU frame prefix was truncated")

    frame_size = _PREFIX.unpack(prefix)[4]
    remainder = receive_exact(
        client,
        frame_size - len(prefix),
    )
    if len(prefix) + len(remainder) != frame_size:
        raise AssertionError("PCMU frame was truncated")
    return prefix + remainder


def wait_until(
    predicate: object,
    *,
    timeout: float = 1.0,
) -> None:
    assert callable(predicate)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Condition did not become true before timeout")


@pytest.mark.parametrize(
    ("keyword", "value", "error_type"),
    [
        ("max_clients", True, TypeError),
        ("max_clients", 0, ValueError),
        ("max_endpoint_bytes", True, TypeError),
        ("max_endpoint_bytes", 0, ValueError),
        ("max_frame_bytes", True, TypeError),
        ("max_frame_bytes", 0, ValueError),
        (
            "max_frame_bytes",
            PCMU_STREAM_HEADER_BYTES - 1,
            ValueError,
        ),
        ("send_timeout", True, TypeError),
        ("send_timeout", 0, ValueError),
        ("accept_poll_interval", 0, ValueError),
        ("shutdown_timeout", float("inf"), ValueError),
    ],
)
def test_pcmu_server_rejects_invalid_limits(
    tmp_path: Path,
    keyword: str,
    value: object,
    error_type: type[Exception],
) -> None:
    stream = FakePcmuStream()

    with pytest.raises(error_type):
        make_server(
            tmp_path,
            stream,
            **{keyword: value},
        )


def test_pcmu_server_sends_complete_versioned_frame(
    tmp_path: Path,
) -> None:
    stream = FakePcmuStream()
    server, path = make_server(tmp_path, stream)
    server.start()
    client = connect(path)

    try:
        wait_until(lambda: stream.subscriber_count == 1)
        packet = make_packet(741)
        publication = stream.publish(packet)

        decoded = decode_pcmu_delivery(read_frame(client))

        assert decoded.stream_sequence == publication.stream_sequence
        assert decoded.packet == packet
        assert decoded.packets_dropped == 0
        assert decoded.payload_bytes_dropped == 0
        assert decoded.overflows == 0
    finally:
        client.close()
        server.stop()

    snapshot = server.snapshot()
    assert not snapshot.active
    assert snapshot.accepted_clients == 1
    assert snapshot.frames_sent == 1
    assert snapshot.payload_bytes_sent == 4
    assert snapshot.last_stream_sequence_sent == 1
    assert snapshot.last_error is None
    assert stream.close_calls == 1
    assert not path.exists()


def test_pcmu_server_gives_clients_independent_subscriptions(
    tmp_path: Path,
) -> None:
    stream = FakePcmuStream()
    server, path = make_server(tmp_path, stream)
    server.start()
    first = connect(path)
    second = connect(path)

    try:
        wait_until(lambda: stream.subscriber_count == 2)
        packet = make_packet(742)
        publication = stream.publish(packet)

        first_delivery = decode_pcmu_delivery(read_frame(first))
        second_delivery = decode_pcmu_delivery(read_frame(second))

        assert first_delivery.stream_sequence == (
            publication.stream_sequence
        )
        assert second_delivery.stream_sequence == (
            publication.stream_sequence
        )
        assert first_delivery.packet == packet
        assert second_delivery.packet == packet
    finally:
        first.close()
        second.close()
        server.stop()

    snapshot = server.snapshot()
    assert snapshot.accepted_clients == 2
    assert snapshot.frames_sent == 2
    assert snapshot.payload_bytes_sent == 8


def test_pcmu_server_rejects_excess_clients(
    tmp_path: Path,
) -> None:
    stream = FakePcmuStream()
    server, path = make_server(
        tmp_path,
        stream,
        max_clients=1,
    )
    server.start()
    first = connect(path)

    try:
        wait_until(lambda: stream.subscriber_count == 1)
        second = connect(path)
        try:
            wait_until(
                lambda: server.snapshot().rejected_clients == 1
            )
            assert second.recv(1) == b""
        finally:
            second.close()
    finally:
        first.close()
        server.stop()

    snapshot = server.snapshot()
    assert snapshot.max_clients == 1
    assert snapshot.accepted_clients == 1
    assert snapshot.rejected_clients == 1


def test_pcmu_server_stop_closes_clients_and_owned_stream(
    tmp_path: Path,
) -> None:
    stream = FakePcmuStream()
    server, path = make_server(
        tmp_path,
        stream,
        send_timeout=5.0,
    )
    server.start()
    server.start()
    client = connect(path)
    wait_until(lambda: stream.subscriber_count == 1)

    server.stop()
    server.stop()

    try:
        assert client.recv(1) == b""
    finally:
        client.close()

    assert not server.active
    assert server.connected_clients == 0
    assert stream.close_calls == 1
    assert not path.exists()

    with pytest.raises(RuntimeError, match="cannot be restarted"):
        server.start()


def test_pcmu_client_disconnect_is_not_an_operational_error(
    tmp_path: Path,
) -> None:
    stream = FakePcmuStream()
    server, path = make_server(tmp_path, stream)
    server.start()
    client = connect(path)

    try:
        wait_until(lambda: stream.subscriber_count == 1)
        client.shutdown(socket.SHUT_RDWR)
        client.close()
        stream.publish(make_packet(743))

        wait_until(lambda: server.connected_clients == 0)
        assert server.snapshot().last_error is None
    finally:
        client.close()
        server.stop()


def test_pcmu_server_startup_preserves_error_and_cleans_all_owners(
    caplog: pytest.LogCaptureFixture,
) -> None:
    order: list[str] = []
    startup_error = RuntimeError("secret startup failure")

    class FailingListener:
        def start(self) -> socket.socket:
            order.append("listener.start")
            raise startup_error

        def stop(self) -> None:
            order.append("listener.stop")
            raise OSError("secret listener cleanup failure")

    class FailingStream:
        def subscribe(self) -> PcmuSubscription:
            raise AssertionError("unreachable")

        def close(self) -> None:
            order.append("stream.close")
            raise ValueError("secret stream cleanup failure")

    server = DaemonPcmuServer(
        FailingListener(),  # type: ignore[arg-type]
        FailingStream(),
    )

    with (
        caplog.at_level(
            logging.ERROR,
            logger="sds200.daemon_pcmu_server",
        ),
        pytest.raises(RuntimeError) as raised,
    ):
        server.start()

    assert raised.value is startup_error
    assert order == [
        "listener.start",
        "listener.stop",
        "stream.close",
    ]
    assert "startup_error=RuntimeError" in caplog.text
    assert "listener:OSError" in caplog.text
    assert "stream:ValueError" in caplog.text
    assert "secret" not in caplog.text


def test_pcmu_server_stop_before_start_is_terminal(
    tmp_path: Path,
) -> None:
    stream = FakePcmuStream()
    server, path = make_server(tmp_path, stream)

    server.stop()
    server.stop()

    assert not server.active
    assert stream.close_calls == 1
    assert not path.exists()

    with pytest.raises(RuntimeError, match="cannot be restarted"):
        server.start()


def test_pcmu_server_disconnects_client_for_oversized_frame(
    tmp_path: Path,
) -> None:
    stream = FakePcmuStream()
    server, path = make_server(
        tmp_path,
        stream,
        max_frame_bytes=256,
    )
    server.start()
    client = connect(path)

    try:
        wait_until(lambda: stream.subscriber_count == 1)
        stream.publish(make_packet(744, b"x" * 1024))

        wait_until(lambda: server.connected_clients == 0)
        assert client.recv(1) == b""
    finally:
        client.close()
        server.stop()

    snapshot = server.snapshot()
    assert snapshot.frames_sent == 0
    assert snapshot.last_error == "DaemonIpcError"


def test_pcmu_server_snapshot_is_json_compatible(
    tmp_path: Path,
) -> None:
    stream = FakePcmuStream()
    server, _ = make_server(
        tmp_path,
        stream,
        max_clients=3,
        max_endpoint_bytes=512,
        max_frame_bytes=4096,
    )

    payload = server.snapshot().as_dict()

    assert json.loads(json.dumps(payload)) == payload
    assert payload == {
        "active": False,
        "connected_clients": 0,
        "max_clients": 3,
        "max_endpoint_bytes": 512,
        "max_frame_bytes": 4096,
        "accepted_clients": 0,
        "rejected_clients": 0,
        "frames_sent": 0,
        "payload_bytes_sent": 0,
        "last_stream_sequence_sent": None,
        "last_error": None,
    }


def test_listener_start_failure_is_propagated_and_server_stays_inactive(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing" / "pcmu.sock"
    listener = DaemonSocketListener(
        DaemonSocketLocation(
            missing,
            DaemonSocketSource.EXPLICIT,
        )
    )
    stream = FakePcmuStream()
    server = DaemonPcmuServer(listener, stream)

    with pytest.raises(DaemonIpcError, match="does not exist"):
        server.start()

    assert not server.active
    assert stream.close_calls == 1
    assert not missing.exists()
