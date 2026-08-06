from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Protocol

from .audio import AudioChunk, AudioChunkHandler
from .daemon_ipc import DaemonSocketLocation
from .pcmu_subscriptions import PcmuPacketDelivery

logger = logging.getLogger(__name__)


class _DaemonPcmuReceiver(Protocol):
    """Minimal daemon PCMU client contract required by the audio adapter."""

    location: DaemonSocketLocation

    @property
    def connected(self) -> bool: ...

    def connect(self) -> object: ...

    def receive(self) -> PcmuPacketDelivery: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class DaemonPcmuAudioStatistics:
    """Immutable delivery and reliability state for daemon-owned TUI audio."""

    packets_received: int = 0
    payload_bytes_received: int = 0
    samples_received: int = 0
    first_stream_sequence: int | None = None
    last_stream_sequence: int | None = None
    stream_packets_skipped: int = 0
    queue_packets_dropped: int = 0
    queue_payload_bytes_dropped: int = 0
    queue_overflows: int = 0
    rtp_missing_packets: int = 0
    rtp_missing_samples: int = 0
    rtp_timestamp_backwards: int = 0
    packets_lost: int = 0
    duplicate_packets: int = 0
    late_packets: int = 0
    malformed_packets: int = 0
    unexpected_source_packets: int = 0
    ssrc_mismatch_packets: int = 0
    timestamp_discontinuities: int = 0
    receive_errors: int = 0
    callback_errors: int = 0


@dataclass(slots=True)
class _MutableDaemonPcmuAudioStatistics:
    packets_received: int = 0
    payload_bytes_received: int = 0
    samples_received: int = 0
    first_stream_sequence: int | None = None
    last_stream_sequence: int | None = None
    stream_packets_skipped: int = 0
    queue_packets_dropped: int = 0
    queue_payload_bytes_dropped: int = 0
    queue_overflows: int = 0
    rtp_missing_packets: int = 0
    rtp_missing_samples: int = 0
    rtp_timestamp_backwards: int = 0
    timestamp_discontinuities: int = 0
    receive_errors: int = 0
    callback_errors: int = 0

    def snapshot(self) -> DaemonPcmuAudioStatistics:
        return DaemonPcmuAudioStatistics(
            packets_received=self.packets_received,
            payload_bytes_received=self.payload_bytes_received,
            samples_received=self.samples_received,
            first_stream_sequence=self.first_stream_sequence,
            last_stream_sequence=self.last_stream_sequence,
            stream_packets_skipped=self.stream_packets_skipped,
            queue_packets_dropped=self.queue_packets_dropped,
            queue_payload_bytes_dropped=self.queue_payload_bytes_dropped,
            queue_overflows=self.queue_overflows,
            rtp_missing_packets=self.rtp_missing_packets,
            rtp_missing_samples=self.rtp_missing_samples,
            rtp_timestamp_backwards=self.rtp_timestamp_backwards,
            packets_lost=(
                self.queue_packets_dropped + self.rtp_missing_packets
            ),
            timestamp_discontinuities=self.timestamp_discontinuities,
            receive_errors=self.receive_errors,
            callback_errors=self.callback_errors,
        )


class DaemonPcmuAudioTransport:
    """Expose daemon-owned PCMU deliveries through the AudioTransport contract."""

    def __init__(self, client: _DaemonPcmuReceiver) -> None:
        self.client = client
        self._lifecycle_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._statistics_lock = threading.RLock()
        self._stop = threading.Event()
        self._handler: AudioChunkHandler | None = None
        self._thread: threading.Thread | None = None
        self._endpoint: str | None = None
        self._statistics = _MutableDaemonPcmuAudioStatistics()

    @property
    def endpoint(self) -> str:
        with self._state_lock:
            endpoint = self._endpoint
        if endpoint is not None:
            return endpoint
        return f"pcmu+unix://{self.client.location.path}"

    @property
    def running(self) -> bool:
        with self._state_lock:
            thread = self._thread
        return (
            thread is not None
            and thread.is_alive()
            and self.client.connected
        )

    @property
    def statistics(self) -> DaemonPcmuAudioStatistics:
        with self._statistics_lock:
            return self._statistics.snapshot()

    def start(self, handler: AudioChunkHandler) -> None:
        with self._lifecycle_lock:
            with self._state_lock:
                thread = self._thread
                if thread is not None and thread.is_alive():
                    return
                self._handler = handler
                self._endpoint = None
                self._stop.clear()
            with self._statistics_lock:
                self._statistics = _MutableDaemonPcmuAudioStatistics()

            try:
                self.client.connect()
            except BaseException:
                with self._state_lock:
                    self._handler = None
                raise

            thread = threading.Thread(
                target=self._receive_loop,
                name="sds200-daemon-pcmu-audio",
                daemon=True,
            )
            with self._state_lock:
                self._thread = thread
            try:
                thread.start()
            except BaseException:
                self.client.close()
                with self._state_lock:
                    if self._thread is thread:
                        self._thread = None
                    self._handler = None
                raise

    def stop(self) -> None:
        with self._lifecycle_lock:
            self._stop.set()
            with self._state_lock:
                thread = self._thread
            self.client.close()
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=5.0)
                if thread.is_alive():
                    raise RuntimeError(
                        "Timed out while stopping daemon PCMU audio."
                    )
            with self._state_lock:
                if self._thread is thread:
                    self._thread = None
                self._handler = None

    def close(self) -> None:
        self.stop()

    def _receive_loop(self) -> None:
        current = threading.current_thread()
        try:
            while not self._stop.is_set():
                try:
                    delivery = self.client.receive()
                except Exception:
                    if self._stop.is_set():
                        break
                    with self._statistics_lock:
                        self._statistics.receive_errors += 1
                    logger.exception(
                        "Daemon PCMU audio receive failed socket=%s",
                        self.client.location.path,
                    )
                    break

                if self._stop.is_set():
                    break

                self._record_delivery(delivery)
                handler = self._handler
                if handler is None or not delivery.packet.payload:
                    continue
                try:
                    handler(
                        AudioChunk(
                            delivery.packet.payload,
                            received_at=delivery.packet.observed_at,
                        )
                    )
                except Exception:
                    with self._statistics_lock:
                        self._statistics.callback_errors += 1
                    logger.exception(
                        "Daemon PCMU audio callback failed endpoint=%s",
                        delivery.packet.endpoint,
                    )
        finally:
            self.client.close()
            with self._state_lock:
                if self._thread is current:
                    self._thread = None
                self._handler = None

    def _record_delivery(self, delivery: PcmuPacketDelivery) -> None:
        packet = delivery.packet
        with self._state_lock:
            self._endpoint = packet.endpoint
        with self._statistics_lock:
            statistics = self._statistics
            previous_sequence = statistics.last_stream_sequence
            if previous_sequence is None:
                statistics.first_stream_sequence = delivery.stream_sequence
            else:
                statistics.stream_packets_skipped += max(
                    0,
                    delivery.stream_sequence - previous_sequence - 1,
                )
            statistics.last_stream_sequence = delivery.stream_sequence
            statistics.packets_received += 1
            statistics.payload_bytes_received += len(packet.payload)
            statistics.samples_received += packet.sample_count
            statistics.queue_packets_dropped = delivery.packets_dropped
            statistics.queue_payload_bytes_dropped = (
                delivery.payload_bytes_dropped
            )
            statistics.queue_overflows = delivery.overflows
            statistics.rtp_missing_packets += packet.missing_packets
            statistics.rtp_missing_samples += packet.missing_samples
            if packet.timestamp_backwards:
                statistics.rtp_timestamp_backwards += 1
            if packet.timestamp_discontinuity:
                statistics.timestamp_discontinuities += 1
