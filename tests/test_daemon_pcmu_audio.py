from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from sds200 import (
    DaemonPcmuAudioTransport,
    DaemonProtocolError,
    DaemonSocketLocation,
    DaemonSocketSource,
    PcmuPacket,
    PcmuPacketDelivery,
    PcmuPublication,
)
from sds200.audio import AudioChunk
from sds200.audio_session import StatisticalAudioTransport


def make_delivery(
    stream_sequence: int,
    *,
    packets_dropped: int = 0,
    payload_bytes_dropped: int = 0,
    overflows: int = 0,
    missing_packets: int = 0,
    missing_samples: int = 0,
    timestamp_backwards: bool = False,
) -> PcmuPacketDelivery:
    sequence = stream_sequence % (1 << 16)
    timestamp = stream_sequence * 160
    return PcmuPacketDelivery(
        publication=PcmuPublication(
            stream_sequence=stream_sequence,
            packet=PcmuPacket(
                endpoint="rtsp://192.0.2.25/au:scanner.au",
                sequence=sequence,
                timestamp=timestamp,
                ssrc=0x56650DAA,
                payload=b"\xff" * 160,
                observed_at=datetime(2026, 8, 5, 23, tzinfo=UTC),
                expected_sequence=(
                    (sequence - missing_packets) % (1 << 16)
                    if missing_packets
                    else None
                ),
                missing_packets=missing_packets,
                expected_timestamp=(
                    timestamp - missing_samples
                    if missing_samples
                    else timestamp + 1
                    if timestamp_backwards
                    else None
                ),
                missing_samples=missing_samples,
                timestamp_backwards=timestamp_backwards,
            ),
        ),
        packets_dropped=packets_dropped,
        payload_bytes_dropped=payload_bytes_dropped,
        overflows=overflows,
    )


class FakeDaemonPcmuClient:
    def __init__(self, path: Path) -> None:
        self.location = DaemonSocketLocation(
            path,
            DaemonSocketSource.EXPLICIT,
        )
        self.connected = False
        self.connect_calls = 0
        self.close_calls = 0
        self.receive_calls = 0
        self._items: queue.Queue[
            PcmuPacketDelivery | Exception | object
        ] = queue.Queue()
        self._sentinel = object()
        self._closed = False

    def connect(self) -> object:
        self.connect_calls += 1
        self.connected = True
        self._closed = False
        return object()

    def receive(self) -> PcmuPacketDelivery:
        self.receive_calls += 1
        item = self._items.get()
        if item is self._sentinel:
            raise RuntimeError("daemon PCMU client closed")
        if isinstance(item, Exception):
            raise item
        assert isinstance(item, PcmuPacketDelivery)
        return item

    def push(self, item: PcmuPacketDelivery | Exception) -> None:
        self._items.put(item)

    def close(self) -> None:
        self.close_calls += 1
        self.connected = False
        if not self._closed:
            self._closed = True
            self._items.put(self._sentinel)


def wait_for(predicate: Callable[[], bool]) -> None:
    for _ in range(300):
        if predicate():
            return
        threading.Event().wait(0.005)
    raise AssertionError("Timed out waiting for daemon PCMU audio state.")


def test_daemon_pcmu_audio_transport_delivers_chunks_and_reliability(
    tmp_path: Path,
) -> None:
    client = FakeDaemonPcmuClient(tmp_path / "pcmu.sock")
    delivery = make_delivery(
        12,
        packets_dropped=2,
        payload_bytes_dropped=320,
        overflows=2,
        missing_packets=3,
        missing_samples=4,
    )
    client.push(delivery)
    chunks: list[AudioChunk] = []
    transport = DaemonPcmuAudioTransport(client)

    assert transport.endpoint == f"pcmu+unix://{client.location.path}"
    assert isinstance(transport, StatisticalAudioTransport)

    transport.start(chunks.append)
    wait_for(lambda: len(chunks) == 1)

    assert transport.running
    assert chunks == [
        AudioChunk(
            delivery.packet.payload,
            received_at=delivery.packet.observed_at,
        )
    ]
    assert transport.endpoint == delivery.packet.endpoint

    statistics = transport.statistics
    assert statistics.packets_received == 1
    assert statistics.payload_bytes_received == 160
    assert statistics.samples_received == 160
    assert statistics.first_stream_sequence == 12
    assert statistics.last_stream_sequence == 12
    assert statistics.stream_packets_skipped == 0
    assert statistics.queue_packets_dropped == 2
    assert statistics.queue_payload_bytes_dropped == 320
    assert statistics.queue_overflows == 2
    assert statistics.rtp_missing_packets == 3
    assert statistics.rtp_missing_samples == 4
    assert statistics.packets_lost == 5
    assert statistics.timestamp_discontinuities == 1
    assert statistics.receive_errors == 0
    assert statistics.callback_errors == 0

    transport.stop()

    assert not transport.running
    assert not client.connected
    assert client.connect_calls == 1
    assert client.close_calls >= 1


def test_daemon_pcmu_audio_transport_is_idempotent_and_counts_callbacks(
    tmp_path: Path,
) -> None:
    client = FakeDaemonPcmuClient(tmp_path / "pcmu.sock")
    client.push(make_delivery(20))
    client.push(make_delivery(22, timestamp_backwards=True))
    callback_count = 0
    completed = threading.Event()

    def receive(chunk: AudioChunk) -> None:
        nonlocal callback_count
        assert chunk.data
        callback_count += 1
        if callback_count == 1:
            raise RuntimeError("callback failed")
        completed.set()

    transport = DaemonPcmuAudioTransport(client)
    transport.start(receive)
    transport.start(receive)

    assert completed.wait(1.0)
    statistics = transport.statistics
    assert client.connect_calls == 1
    assert statistics.packets_received == 2
    assert statistics.stream_packets_skipped == 1
    assert statistics.rtp_timestamp_backwards == 1
    assert statistics.timestamp_discontinuities == 1
    assert statistics.callback_errors == 1

    transport.stop()


def test_daemon_pcmu_audio_transport_records_receive_failure(
    tmp_path: Path,
) -> None:
    client = FakeDaemonPcmuClient(tmp_path / "pcmu.sock")
    client.push(DaemonProtocolError("invalid daemon PCMU frame"))
    transport = DaemonPcmuAudioTransport(client)

    transport.start(lambda chunk: None)
    wait_for(lambda: not transport.running)

    statistics = transport.statistics
    assert statistics.packets_received == 0
    assert statistics.receive_errors == 1
    assert statistics.callback_errors == 0
    assert not client.connected
    assert client.close_calls >= 1

    transport.stop()
