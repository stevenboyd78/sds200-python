from __future__ import annotations

import socket
import struct
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sds200 import (
    PCMU_STREAM_HEADER_BYTES,
    DaemonDisconnectedError,
    DaemonPcmuClient,
    DaemonProtocolError,
    DaemonSocketLocation,
    DaemonSocketSource,
    DaemonUnavailableError,
    PcmuPacket,
    PcmuPacketDelivery,
    PcmuPublication,
    encode_pcmu_delivery,
)

_PREFIX = struct.Struct("!4sBBHI")


def make_delivery(
    stream_sequence: int,
    *,
    rtp_sequence: int | None = None,
    payload: bytes = b"\xff" * 160,
    packets_dropped: int = 0,
    payload_bytes_dropped: int = 0,
    overflows: int = 0,
    missing_packets: int = 0,
    missing_samples: int = 0,
    timestamp_backwards: bool = False,
) -> PcmuPacketDelivery:
    sequence = (
        stream_sequence % (1 << 16)
        if rtp_sequence is None
        else rtp_sequence
    )
    expected_sequence = (
        (sequence - missing_packets) % (1 << 16)
        if missing_packets
        else None
    )
    timestamp = stream_sequence * max(1, len(payload))
    expected_timestamp = (
        timestamp - missing_samples
        if missing_samples
        else timestamp + 1
        if timestamp_backwards
        else None
    )
    packet = PcmuPacket(
        endpoint="rtsp://192.0.2.25/au:scanner.au",
        sequence=sequence,
        timestamp=timestamp,
        ssrc=0x56650DAA,
        payload=payload,
        observed_at=datetime(2026, 8, 5, 13, tzinfo=UTC),
        expected_sequence=expected_sequence,
        missing_packets=missing_packets,
        expected_timestamp=expected_timestamp,
        missing_samples=missing_samples,
        timestamp_backwards=timestamp_backwards,
    )
    return PcmuPacketDelivery(
        publication=PcmuPublication(
            stream_sequence=stream_sequence,
            packet=packet,
        ),
        packets_dropped=packets_dropped,
        payload_bytes_dropped=payload_bytes_dropped,
        overflows=overflows,
    )


def start_scripted_server(
    path: Path,
    chunks: tuple[bytes, ...],
) -> threading.Thread:
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen(1)

    def serve() -> None:
        try:
            client, _ = listener.accept()
            with client:
                for chunk in chunks:
                    client.sendall(chunk)
        finally:
            listener.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return thread


@pytest.mark.parametrize(
    ("keyword", "value", "error_type"),
    [
        ("timeout", True, TypeError),
        ("timeout", 0, ValueError),
        ("timeout", float("inf"), ValueError),
        ("max_endpoint_bytes", True, TypeError),
        ("max_endpoint_bytes", 0, ValueError),
        ("max_frame_bytes", True, TypeError),
        (
            "max_frame_bytes",
            PCMU_STREAM_HEADER_BYTES - 1,
            ValueError,
        ),
    ],
)
def test_pcmu_client_rejects_invalid_limits(
    tmp_path: Path,
    keyword: str,
    value: object,
    error_type: type[Exception],
) -> None:
    location = DaemonSocketLocation(
        tmp_path / "pcmu.sock",
        DaemonSocketSource.EXPLICIT,
    )

    with pytest.raises(error_type):
        DaemonPcmuClient(
            location,
            **{keyword: value},  # type: ignore[arg-type]
        )


def test_pcmu_client_requires_socket_location() -> None:
    with pytest.raises(TypeError, match="DaemonSocketLocation"):
        DaemonPcmuClient(object())  # type: ignore[arg-type]


def test_pcmu_client_receives_frames_and_tracks_loss(
    tmp_path: Path,
) -> None:
    path = tmp_path / "pcmu.sock"
    first = make_delivery(10)
    second = make_delivery(
        12,
        packets_dropped=1,
        payload_bytes_dropped=160,
        overflows=1,
        missing_packets=2,
        missing_samples=3,
    )
    thread = start_scripted_server(
        path,
        (
            encode_pcmu_delivery(first)
            + encode_pcmu_delivery(second),
        ),
    )
    client = DaemonPcmuClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    assert client.receive() == first
    assert client.receive() == second
    snapshot = client.snapshot()

    client.close()
    thread.join(timeout=1.0)

    assert snapshot.connected
    assert snapshot.packets_received == 2
    assert snapshot.payload_bytes_received == 320
    assert snapshot.samples_received == 320
    assert snapshot.first_stream_sequence == 10
    assert snapshot.last_stream_sequence == 12
    assert snapshot.stream_packets_skipped == 1
    assert snapshot.packets_dropped == 1
    assert snapshot.payload_bytes_dropped == 160
    assert snapshot.overflows == 1
    assert snapshot.rtp_missing_packets == 2
    assert snapshot.rtp_missing_samples == 3
    assert snapshot.rtp_timestamp_backwards == 0
    assert snapshot.endpoint == "rtsp://192.0.2.25/au:scanner.au"
    assert snapshot.audio_duration_seconds == 320 / 8000
    assert not client.connected


def test_pcmu_client_accepts_fragmented_frame(tmp_path: Path) -> None:
    path = tmp_path / "fragmented.sock"
    delivery = make_delivery(7, payload=b"\xff\x80")
    frame = encode_pcmu_delivery(delivery)
    thread = start_scripted_server(
        path,
        tuple(frame[index : index + 1] for index in range(len(frame))),
    )
    client = DaemonPcmuClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    assert client.receive() == delivery

    client.close()
    thread.join(timeout=1.0)


def test_pcmu_client_reports_missing_socket(tmp_path: Path) -> None:
    path = tmp_path / "missing.sock"
    client = DaemonPcmuClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    with pytest.raises(
        DaemonUnavailableError,
        match="PCMU socket was not found",
    ):
        client.receive()

    assert not client.connected


def test_pcmu_client_reports_refused_stale_socket(tmp_path: Path) -> None:
    path = tmp_path / "stale.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.close()
    client = DaemonPcmuClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    with pytest.raises(DaemonUnavailableError, match="not accepting"):
        client.receive()

    assert not client.connected


def test_pcmu_client_reports_clean_disconnect(tmp_path: Path) -> None:
    path = tmp_path / "disconnect.sock"
    thread = start_scripted_server(path, ())
    client = DaemonPcmuClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    with pytest.raises(DaemonDisconnectedError, match="disconnected"):
        client.receive()

    thread.join(timeout=1.0)
    assert not client.connected


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"SD", "incomplete frame prefix"),
        (
            _PREFIX.pack(
                b"SDSP",
                1,
                0,
                PCMU_STREAM_HEADER_BYTES,
                PCMU_STREAM_HEADER_BYTES + 10,
            ),
            "incomplete frame body",
        ),
    ],
)
def test_pcmu_client_rejects_truncated_frame(
    tmp_path: Path,
    payload: bytes,
    message: str,
) -> None:
    path = tmp_path / "truncated.sock"
    thread = start_scripted_server(path, (payload,))
    client = DaemonPcmuClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    with pytest.raises(DaemonProtocolError, match=message):
        client.receive()

    thread.join(timeout=1.0)
    assert not client.connected


@pytest.mark.parametrize(
    ("prefix", "message"),
    [
        (
            _PREFIX.pack(
                b"NOPE",
                1,
                0,
                PCMU_STREAM_HEADER_BYTES,
                PCMU_STREAM_HEADER_BYTES,
            ),
            "magic",
        ),
        (
            _PREFIX.pack(
                b"SDSP",
                2,
                0,
                PCMU_STREAM_HEADER_BYTES,
                PCMU_STREAM_HEADER_BYTES,
            ),
            "version",
        ),
        (
            _PREFIX.pack(
                b"SDSP",
                1,
                0,
                PCMU_STREAM_HEADER_BYTES + 1,
                PCMU_STREAM_HEADER_BYTES,
            ),
            "header size",
        ),
        (
            _PREFIX.pack(
                b"SDSP",
                1,
                0,
                PCMU_STREAM_HEADER_BYTES,
                PCMU_STREAM_HEADER_BYTES - 1,
            ),
            "shorter",
        ),
    ],
)
def test_pcmu_client_rejects_invalid_prefix(
    tmp_path: Path,
    prefix: bytes,
    message: str,
) -> None:
    path = tmp_path / "invalid-prefix.sock"
    thread = start_scripted_server(path, (prefix,))
    client = DaemonPcmuClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    with pytest.raises(DaemonProtocolError, match=message):
        client.receive()

    thread.join(timeout=1.0)
    assert not client.connected


def test_pcmu_client_rejects_oversized_frame_before_body(
    tmp_path: Path,
) -> None:
    path = tmp_path / "oversized.sock"
    prefix = _PREFIX.pack(
        b"SDSP",
        1,
        0,
        PCMU_STREAM_HEADER_BYTES,
        4096,
    )
    thread = start_scripted_server(path, (prefix,))
    client = DaemonPcmuClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT),
        max_frame_bytes=512,
    )

    with pytest.raises(
        DaemonProtocolError,
        match="maximum accepted size",
    ):
        client.receive()

    thread.join(timeout=1.0)
    assert not client.connected


def test_pcmu_client_rejects_stream_sequence_regression(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sequence.sock"
    thread = start_scripted_server(
        path,
        (
            encode_pcmu_delivery(make_delivery(9))
            + encode_pcmu_delivery(make_delivery(9)),
        ),
    )
    client = DaemonPcmuClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    assert client.receive().stream_sequence == 9
    with pytest.raises(
        DaemonProtocolError,
        match="did not advance monotonically",
    ):
        client.receive()

    thread.join(timeout=1.0)
    assert not client.connected


def test_pcmu_client_rejects_queue_counter_regression(
    tmp_path: Path,
) -> None:
    path = tmp_path / "loss.sock"
    first = make_delivery(
        4,
        packets_dropped=2,
        payload_bytes_dropped=320,
        overflows=2,
    )
    second = make_delivery(
        5,
        packets_dropped=1,
        payload_bytes_dropped=160,
        overflows=1,
    )
    thread = start_scripted_server(
        path,
        (
            encode_pcmu_delivery(first)
            + encode_pcmu_delivery(second),
        ),
    )
    client = DaemonPcmuClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    assert client.receive().stream_sequence == 4
    with pytest.raises(DaemonProtocolError, match="counters regressed"):
        client.receive()

    thread.join(timeout=1.0)
    assert not client.connected


def test_pcmu_client_context_manager_closes_socket(
    tmp_path: Path,
) -> None:
    path = tmp_path / "context.sock"
    thread = start_scripted_server(
        path,
        (encode_pcmu_delivery(make_delivery(1)),),
    )
    location = DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)

    with DaemonPcmuClient(location) as client:
        assert client.connected
        assert client.receive().stream_sequence == 1

    thread.join(timeout=1.0)
    assert not client.connected
