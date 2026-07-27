from __future__ import annotations

import queue
import socket
import time
from collections.abc import Callable

import pytest

from sds200.audio import AudioChunk
from sds200.network_audio import NetworkAudioTransport
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
    ssrc: int = 5678,
) -> bytes:
    return (
        bytes((0x80, 0x00))
        + sequence.to_bytes(2, "big")
        + (1234).to_bytes(4, "big")
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
    transport = NetworkAudioTransport(
        "192.0.2.25",
        keepalive_interval=0.01,
        datagram_socket_factory=datagram_factory,
        rtsp_client_factory=client_factory,
        local_address_resolver=lambda _host, _port: "192.0.2.10",
    )

    transport.start(chunks.append)
    try:
        datagram.feed(make_rtp(b"pcmu"))
        wait_until(lambda: [chunk.data for chunk in chunks] == [b"pcmu"])
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
    transport = NetworkAudioTransport(
        "192.0.2.25",
        datagram_socket_factory=datagram_factory,
        rtsp_client_factory=client_factory,
        local_address_resolver=lambda _host, _port: "192.0.2.10",
    )

    transport.start(chunks.append)
    try:
        datagram.feed(make_rtp(b"first", sequence=10))
        datagram.feed(make_rtp(b"duplicate", sequence=10))
        datagram.feed(make_rtp(b"next", sequence=11))
        datagram.feed(make_rtp(b"late", sequence=9))
        wait_until(lambda: len(chunks) == 2)
    finally:
        transport.stop()

    assert [chunk.data for chunk in chunks] == [b"first", b"next"]


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


def test_network_audio_rejects_wildcard_bind_address() -> None:
    with pytest.raises(ValueError, match="must not bind all network interfaces"):
        NetworkAudioTransport("192.0.2.25", local_host="0.0.0.0")
