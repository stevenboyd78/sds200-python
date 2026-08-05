from __future__ import annotations

import queue
import socket
import time
from collections.abc import Callable

import pytest

from sds200.audio import AudioChunk
from sds200.network_audio import NetworkAudioTransport
from sds200.pcmu import PcmuPacket
from sds200.rtsp import RtpTransportInfo


class FakeAudioDatagramSocket:
    def __init__(self) -> None:
        self.timeout: float | None = None
        self.bound: tuple[str, int] | None = None
        self.incoming: queue.Queue[
            tuple[bytes, tuple[str, int]] | OSError
        ] = queue.Queue()
        self.closed = False

    def settimeout(self, value: float | None) -> None:
        self.timeout = value

    def bind(self, address: tuple[str, int]) -> None:
        self.bound = address

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
        return value

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.incoming.put(OSError("closed"))

    def feed(
        self,
        data: bytes,
        *,
        source: tuple[str, int] = ("192.0.2.25", 56002),
    ) -> None:
        self.incoming.put((data, source))


class FakeRtspClient:
    def __init__(self) -> None:
        self.started_ports: list[int] = []
        self.keepalives = 0
        self.teardowns = 0
        self.closed = False

    def start(self, client_port: int) -> RtpTransportInfo:
        self.started_ports.append(client_port)
        return RtpTransportInfo(
            source="192.0.2.25",
            server_port=56002,
            ssrc=5678,
        )

    def get_parameter(self) -> object:
        self.keepalives += 1
        return object()

    def teardown(self) -> object:
        self.teardowns += 1
        return object()

    def close(self) -> None:
        self.closed = True


def wait_until(predicate: Callable[[], bool], *, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert predicate()


def make_rtp(
    payload: bytes,
    *,
    sequence: int = 741,
    timestamp: int = 1234,
    ssrc: int = 5678,
    marker: bool = False,
) -> bytes:
    return (
        bytes((0x80, 0x80 if marker else 0x00))
        + sequence.to_bytes(2, "big")
        + timestamp.to_bytes(4, "big")
        + ssrc.to_bytes(4, "big")
        + payload
    )


def test_network_audio_performs_handshake_and_emits_pcmu_payload() -> None:
    datagram = FakeAudioDatagramSocket()
    rtsp = FakeRtspClient()
    calls: list[tuple[int, int]] = []
    client_args: list[tuple[str, int, str, float]] = []

    def datagram_factory(family: int, socket_type: int) -> FakeAudioDatagramSocket:
        calls.append((family, socket_type))
        return datagram

    def client_factory(
        host: str,
        port: int,
        path: str,
        timeout: float,
    ) -> FakeRtspClient:
        client_args.append((host, port, path, timeout))
        return rtsp

    chunks: list[AudioChunk] = []
    packets: list[PcmuPacket] = []
    observed_order: list[str] = []
    transport = NetworkAudioTransport(
        "192.0.2.25",
        keepalive_interval=0.01,
        datagram_socket_factory=datagram_factory,
        rtsp_client_factory=client_factory,
        local_address_resolver=lambda _host, _port: "192.0.2.10",
    )
    transport.on_packet(
        lambda packet: (
            packets.append(packet),
            observed_order.append("packet"),
        )
    )

    def receive_chunk(chunk: AudioChunk) -> None:
        chunks.append(chunk)
        observed_order.append("chunk")

    transport.start(receive_chunk)
    try:
        datagram.feed(
            make_rtp(
                b"pcmu",
                sequence=741,
                timestamp=1_407_173_956,
                marker=True,
            )
        )
        wait_until(lambda: [chunk.data for chunk in chunks] == [b"pcmu"])
        wait_until(lambda: len(packets) == 1)
        wait_until(lambda: rtsp.keepalives >= 1)
        assert transport.running
    finally:
        transport.stop()

    assert calls == [(socket.AF_INET, socket.SOCK_DGRAM)]
    assert datagram.bound == ("192.0.2.10", 0)
    assert client_args == [("192.0.2.25", 554, "/au:scanner.au", 5.0)]
    assert rtsp.started_ports == [48607]
    assert rtsp.teardowns == 1
    assert rtsp.closed
    assert datagram.closed
    assert not transport.running

    assert observed_order == ["packet", "chunk"]
    packet = packets[0]
    assert packet.endpoint == "rtsp://192.0.2.25/au:scanner.au"
    assert packet.sequence == 741
    assert packet.timestamp == 1_407_173_956
    assert packet.ssrc == 5678
    assert packet.payload == b"pcmu"
    assert packet.marker
    assert packet.expected_sequence is None
    assert packet.expected_timestamp is None
    assert not packet.discontinuous
    assert packet.observed_at == chunks[0].received_at


def test_network_audio_discards_duplicate_and_out_of_order_packets() -> None:
    datagram = FakeAudioDatagramSocket()
    rtsp = FakeRtspClient()

    def datagram_factory(
        family: int,
        socket_type: int,
    ) -> FakeAudioDatagramSocket:
        del family, socket_type
        return datagram

    def client_factory(
        host: str,
        port: int,
        path: str,
        timeout: float,
    ) -> FakeRtspClient:
        del host, port, path, timeout
        return rtsp

    chunks: list[AudioChunk] = []
    packets: list[PcmuPacket] = []
    transport = NetworkAudioTransport(
        "192.0.2.25",
        datagram_socket_factory=datagram_factory,
        rtsp_client_factory=client_factory,
        local_address_resolver=lambda _host, _port: "192.0.2.10",
    )
    transport.on_packet(packets.append)

    transport.start(chunks.append)
    try:
        datagram.feed(
            make_rtp(
                b"first",
                sequence=10,
                timestamp=1000,
            )
        )
        datagram.feed(
            make_rtp(
                b"duplicate",
                sequence=10,
                timestamp=1000,
            )
        )
        datagram.feed(
            make_rtp(
                b"next",
                sequence=11,
                timestamp=1005,
            )
        )
        datagram.feed(
            make_rtp(
                b"late",
                sequence=9,
                timestamp=996,
            )
        )
        wait_until(lambda: len(chunks) == 2)
        wait_until(lambda: len(packets) == 2)
    finally:
        transport.stop()

    assert [chunk.data for chunk in chunks] == [b"first", b"next"]
    assert [packet.sequence for packet in packets] == [10, 11]
    assert not any(packet.discontinuous for packet in packets)


def test_network_audio_rejects_unexpected_source_and_ssrc() -> None:
    datagram = FakeAudioDatagramSocket()
    rtsp = FakeRtspClient()
    chunks: list[AudioChunk] = []
    transport = NetworkAudioTransport(
        "192.0.2.25",
        datagram_socket_factory=lambda _family, _type: datagram,
        rtsp_client_factory=lambda _host, _port, _path, _timeout: rtsp,
        local_address_resolver=lambda _host, _port: "192.0.2.10",
    )

    transport.start(chunks.append)
    try:
        datagram.feed(
            make_rtp(b"wrong source", sequence=10),
            source=("192.0.2.99", 56002),
        )
        datagram.feed(make_rtp(b"wrong ssrc", sequence=10, ssrc=9999))
        datagram.feed(make_rtp(b"accepted", sequence=10))
        wait_until(lambda: transport.statistics.datagrams_received == 3)
        wait_until(lambda: [chunk.data for chunk in chunks] == [b"accepted"])
    finally:
        transport.stop()

    statistics = transport.statistics
    assert statistics.unexpected_source_packets == 1
    assert statistics.ssrc_mismatch_packets == 1
    assert statistics.packets_delivered == 1



def test_network_audio_publishes_wraparound_and_gap_observations() -> None:
    datagram = FakeAudioDatagramSocket()
    rtsp = FakeRtspClient()
    chunks: list[AudioChunk] = []
    packets: list[PcmuPacket] = []
    transport = NetworkAudioTransport(
        "192.0.2.25",
        datagram_socket_factory=lambda _family, _type: datagram,
        rtsp_client_factory=lambda _host, _port, _path, _timeout: rtsp,
        local_address_resolver=lambda _host, _port: "192.0.2.10",
    )
    transport.on_packet(packets.append)

    transport.start(chunks.append)
    try:
        datagram.feed(
            make_rtp(
                b"abcd",
                sequence=65535,
                timestamp=0xFFFFFFFC,
            )
        )
        datagram.feed(
            make_rtp(
                b"efgh",
                sequence=0,
                timestamp=0,
            )
        )
        datagram.feed(
            make_rtp(
                b"ijkl",
                sequence=3,
                timestamp=8,
            )
        )
        wait_until(lambda: len(chunks) == 3)
        wait_until(lambda: len(packets) == 3)
    finally:
        transport.stop()

    first, wrapped, gap = packets
    assert first.expected_sequence is None
    assert first.expected_timestamp is None
    assert not first.discontinuous

    assert wrapped.expected_sequence == 0
    assert wrapped.expected_timestamp == 0
    assert not wrapped.discontinuous

    assert gap.expected_sequence == 1
    assert gap.missing_packets == 2
    assert gap.expected_timestamp == 4
    assert gap.missing_samples == 4
    assert not gap.timestamp_backwards
    assert gap.sequence_discontinuity
    assert gap.timestamp_discontinuity
    assert gap.discontinuous


def test_network_audio_packet_listener_failures_are_isolated() -> None:
    datagram = FakeAudioDatagramSocket()
    rtsp = FakeRtspClient()
    chunks: list[AudioChunk] = []
    packets: list[PcmuPacket] = []
    transport = NetworkAudioTransport(
        "192.0.2.25",
        datagram_socket_factory=lambda _family, _type: datagram,
        rtsp_client_factory=lambda _host, _port, _path, _timeout: rtsp,
        local_address_resolver=lambda _host, _port: "192.0.2.10",
    )

    def fail_listener(packet: PcmuPacket) -> None:
        del packet
        raise RuntimeError("packet listener failed")

    transport.on_packet(fail_listener)
    transport.on_packet(packets.append)

    transport.start(chunks.append)
    try:
        datagram.feed(make_rtp(b"accepted"))
        wait_until(lambda: len(packets) == 1)
        wait_until(lambda: len(chunks) == 1)
    finally:
        transport.stop()

    assert packets[0].payload == b"accepted"
    assert chunks[0].data == b"accepted"


def test_network_audio_packet_listener_can_unsubscribe() -> None:
    datagram = FakeAudioDatagramSocket()
    rtsp = FakeRtspClient()
    chunks: list[AudioChunk] = []
    packets: list[PcmuPacket] = []
    transport = NetworkAudioTransport(
        "192.0.2.25",
        datagram_socket_factory=lambda _family, _type: datagram,
        rtsp_client_factory=lambda _host, _port, _path, _timeout: rtsp,
        local_address_resolver=lambda _host, _port: "192.0.2.10",
    )
    unsubscribe = transport.on_packet(packets.append)
    unsubscribe()

    transport.start(chunks.append)
    try:
        datagram.feed(make_rtp(b"accepted"))
        wait_until(lambda: len(chunks) == 1)
    finally:
        transport.stop()

    assert packets == []
    assert chunks[0].data == b"accepted"

def test_network_audio_rejects_wildcard_bind_address() -> None:
    with pytest.raises(ValueError, match="must not bind all network interfaces"):
        NetworkAudioTransport("192.0.2.25", local_host="0.0.0.0")
