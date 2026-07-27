from __future__ import annotations

import json
import queue
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sds200.audio import AudioChunk
from sds200.network_audio import NetworkAudioTransport


class FixtureDatagramSocket:
    def __init__(self) -> None:
        self.timeout: float | None = None
        self.incoming: queue.Queue[bytes | OSError] = queue.Queue()
        self.closed = False

    def settimeout(self, value: float | None) -> None:
        self.timeout = value

    def bind(self, address: tuple[str, int]) -> None:
        del address

    def getsockname(self) -> tuple[str, int]:
        return ("0.0.0.0", 48607)

    def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:
        del size
        try:
            value = self.incoming.get(timeout=self.timeout or 0.05)
        except queue.Empty as exc:
            raise TimeoutError from exc
        if isinstance(value, OSError):
            raise value
        return value, ("192.0.2.25", 56002)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.incoming.put(OSError("closed"))

    def feed(self, value: bytes | OSError) -> None:
        self.incoming.put(value)


class FixtureRtspClient:
    def __init__(self) -> None:
        self.teardowns = 0

    def start(self, client_port: int) -> None:
        assert client_port == 48607

    def get_parameter(self) -> object:
        return object()

    def teardown(self) -> object:
        self.teardowns += 1
        return object()

    def close(self) -> None:
        pass


def wait_until(predicate: Callable[[], bool], *, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert predicate()


def make_rtp(packet: dict[str, Any]) -> bytes:
    return (
        bytes((0x80, 0x00))
        + int(packet["sequence"]).to_bytes(2, "big")
        + int(packet["timestamp"]).to_bytes(4, "big")
        + int(packet["ssrc"]).to_bytes(4, "big")
        + bytes.fromhex(str(packet["payload_hex"]))
    )


def test_sanitized_rtp_fixture_reports_reliability_statistics() -> None:
    fixture_path = Path(__file__).parent / "fixtures" / "rtp" / "sds200-pcmu.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    datagram = FixtureDatagramSocket()
    rtsp = FixtureRtspClient()
    chunks: list[AudioChunk] = []
    transport = NetworkAudioTransport(
        "192.0.2.25",
        datagram_socket_factory=lambda _family, _type: datagram,
        rtsp_client_factory=lambda _host, _port, _path, _timeout: rtsp,
    )

    transport.start(chunks.append)
    try:
        for packet in fixture["packets"]:
            datagram.feed(make_rtp(packet))
        datagram.feed(bytes.fromhex(fixture["malformed_hex"]))
        wait_until(lambda: transport.statistics.datagrams_received == 6)
    finally:
        transport.stop()

    statistics = transport.statistics
    assert [chunk.data for chunk in chunks] == [b"\xff" * 4] * 3
    assert statistics.sessions_started == 1
    assert statistics.datagrams_received == 6
    assert statistics.packets_delivered == 3
    assert statistics.payload_bytes_delivered == 12
    assert statistics.sequence_gaps == 1
    assert statistics.packets_lost == 1
    assert statistics.duplicate_packets == 1
    assert statistics.late_packets == 1
    assert statistics.malformed_packets == 1
    assert statistics.timestamp_discontinuities == 1
    assert statistics.timestamp_samples_missing == 4
    assert statistics.first_sequence == 741
    assert statistics.last_sequence == 744
    assert statistics.last_timestamp == 1012
    assert statistics.ssrc == 1449463210
    assert statistics.teardowns_sent == 1


def test_receive_failure_is_counted_and_session_can_stop_cleanly() -> None:
    datagram = FixtureDatagramSocket()
    rtsp = FixtureRtspClient()
    transport = NetworkAudioTransport(
        "192.0.2.25",
        datagram_socket_factory=lambda _family, _type: datagram,
        rtsp_client_factory=lambda _host, _port, _path, _timeout: rtsp,
    )

    transport.start(lambda _chunk: None)
    datagram.feed(OSError("simulated receive failure"))
    wait_until(lambda: transport.statistics.receive_errors == 1)
    transport.stop()

    assert datagram.closed
    assert rtsp.teardowns == 1
    assert transport.statistics.receive_errors == 1
