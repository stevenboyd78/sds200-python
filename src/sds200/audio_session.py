from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from time import monotonic
from typing import Protocol, Self, runtime_checkable

from .audio import AudioStream, AudioTransport
from .audio_recording import PcmuWavRecorder
from .events import EventBus

logger = logging.getLogger(__name__)


class AudioSessionStatus(StrEnum):
    """Lifecycle state for one audio recording session."""

    IDLE = "idle"
    STARTING = "starting"
    RECORDING = "recording"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class AudioTransportStatistics(Protocol):
    """Reliability counters exposed by a statistical audio transport."""

    @property
    def packets_lost(self) -> int: ...

    @property
    def duplicate_packets(self) -> int: ...

    @property
    def late_packets(self) -> int: ...

    @property
    def malformed_packets(self) -> int: ...

    @property
    def unexpected_source_packets(self) -> int: ...

    @property
    def ssrc_mismatch_packets(self) -> int: ...

    @property
    def timestamp_discontinuities(self) -> int: ...

    @property
    def receive_errors(self) -> int: ...

    @property
    def callback_errors(self) -> int: ...


@runtime_checkable
class StatisticalAudioTransport(AudioTransport, Protocol):
    """Audio transport that exposes a snapshot of reliability counters."""

    @property
    def statistics(self) -> AudioTransportStatistics: ...


@dataclass(frozen=True, slots=True)
class AudioReliabilitySnapshot:
    """Renderer-neutral reliability counters for an audio session."""

    packets_lost: int = 0
    duplicate_packets: int = 0
    late_packets: int = 0
    malformed_packets: int = 0
    unexpected_source_packets: int = 0
    ssrc_mismatch_packets: int = 0
    timestamp_discontinuities: int = 0
    receive_errors: int = 0
    callback_errors: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "packets_lost": self.packets_lost,
            "duplicate_packets": self.duplicate_packets,
            "late_packets": self.late_packets,
            "malformed_packets": self.malformed_packets,
            "unexpected_source_packets": self.unexpected_source_packets,
            "ssrc_mismatch_packets": self.ssrc_mismatch_packets,
            "timestamp_discontinuities": self.timestamp_discontinuities,
            "receive_errors": self.receive_errors,
            "callback_errors": self.callback_errors,
        }


@dataclass(frozen=True, slots=True)
class AudioSessionSnapshot:
    """Immutable presentation state for one audio recording session."""

    status: AudioSessionStatus
    endpoint: str
    output_path: Path
    started_at: datetime | None
    stopped_at: datetime | None
    elapsed_seconds: float
    packets: int
    samples: int
    audio_duration_seconds: float
    reliability: AudioReliabilitySnapshot
    error: str | None = None

    @property
    def active(self) -> bool:
        return self.status in {
            AudioSessionStatus.STARTING,
            AudioSessionStatus.RECORDING,
            AudioSessionStatus.STOPPING,
        }

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "endpoint": self.endpoint,
            "output_path": str(self.output_path),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "stopped_at": self.stopped_at.isoformat() if self.stopped_at else None,
            "elapsed_seconds": self.elapsed_seconds,
            "packets": self.packets,
            "samples": self.samples,
            "audio_duration_seconds": self.audio_duration_seconds,
            "reliability": self.reliability.as_dict(),
            "error": self.error,
        }


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _error_message(error: BaseException) -> str:
    return str(error) or type(error).__name__


class AudioRecordingSession:
    """Coordinate an audio stream and WAV recorder as one reusable session."""

    def __init__(
        self,
        stream: AudioStream,
        recorder: PcmuWavRecorder,
        *,
        clock: Callable[[], float] = monotonic,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.stream = stream
        self.recorder = recorder
        self.events = EventBus()
        self._clock = clock
        self._now = now
        self._lifecycle_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._status = AudioSessionStatus.IDLE
        self._started_at: datetime | None = None
        self._stopped_at: datetime | None = None
        self._started_clock: float | None = None
        self._elapsed_seconds = 0.0
        self._error: str | None = None
        self._unsubscribe: Callable[[], None] | None = None

    @property
    def status(self) -> AudioSessionStatus:
        with self._state_lock:
            return self._status

    @property
    def active(self) -> bool:
        return self.snapshot().active

    def on_state(
        self,
        callback: Callable[[AudioSessionSnapshot], None],
    ) -> Callable[[], None]:
        return self.events.subscribe("state", callback)

    def snapshot(self) -> AudioSessionSnapshot:
        with self._state_lock:
            status = self._status
            started_at = self._started_at
            stopped_at = self._stopped_at
            started_clock = self._started_clock
            elapsed_seconds = self._elapsed_seconds
            error = self._error

        if started_clock is not None:
            elapsed_seconds = max(elapsed_seconds, self._clock() - started_clock)

        return AudioSessionSnapshot(
            status=status,
            endpoint=self.stream.endpoint,
            output_path=self.recorder.path,
            started_at=started_at,
            stopped_at=stopped_at,
            elapsed_seconds=max(0.0, elapsed_seconds),
            packets=self.recorder.packets,
            samples=self.recorder.samples,
            audio_duration_seconds=self.recorder.duration_seconds,
            reliability=self._reliability_snapshot(),
            error=error,
        )

    def start(self) -> None:
        with self._lifecycle_lock:
            logger.info(
                "audio recording starting endpoint=%s output=%s",
                self.stream.endpoint,
                self.recorder.path,
            )
            with self._state_lock:
                if self._status is not AudioSessionStatus.IDLE:
                    raise RuntimeError("Audio recording sessions can only be started once.")
                self._status = AudioSessionStatus.STARTING
                self._error = None
            self._emit_state()

            unsubscribe: Callable[[], None] | None = None
            try:
                self.recorder.start()
                unsubscribe = self.stream.on_chunk(self.recorder.write_chunk)
                self.stream.start()
            except BaseException as error:
                if unsubscribe is not None:
                    unsubscribe()
                with suppress(Exception):
                    self.stream.stop()
                with suppress(Exception):
                    self.recorder.close()
                with self._state_lock:
                    self._unsubscribe = None
                    self._status = AudioSessionStatus.FAILED
                    self._stopped_at = self._now()
                    self._error = _error_message(error)
                self._emit_state()
                logger.exception(
                    "audio recording start failed endpoint=%s output=%s",
                    self.stream.endpoint,
                    self.recorder.path,
                )
                raise

            with self._state_lock:
                self._unsubscribe = unsubscribe
                self._started_clock = self._clock()
                self._started_at = self._now()
                self._stopped_at = None
                self._elapsed_seconds = 0.0
                self._status = AudioSessionStatus.RECORDING
            self._emit_state()
            logger.info(
                "audio recording started endpoint=%s output=%s",
                self.stream.endpoint,
                self.recorder.path,
            )

    def stop(self) -> None:
        with self._lifecycle_lock:
            with self._state_lock:
                if self._status in {
                    AudioSessionStatus.IDLE,
                    AudioSessionStatus.STOPPED,
                    AudioSessionStatus.FAILED,
                }:
                    return
                self._status = AudioSessionStatus.STOPPING
                unsubscribe, self._unsubscribe = self._unsubscribe, None
            self._emit_state()

            failure: BaseException | None = None
            try:
                self.stream.stop()
            except Exception as error:
                failure = error

            if unsubscribe is not None:
                try:
                    unsubscribe()
                except Exception as error:
                    if failure is None:
                        failure = error

            try:
                self.recorder.close()
            except BaseException as error:
                if failure is None:
                    failure = error

            ended_clock = self._clock()
            with self._state_lock:
                if self._started_clock is not None:
                    self._elapsed_seconds = max(
                        0.0,
                        ended_clock - self._started_clock,
                    )
                self._started_clock = None
                self._stopped_at = self._now()
                self._status = (
                    AudioSessionStatus.FAILED
                    if failure is not None
                    else AudioSessionStatus.STOPPED
                )
                self._error = _error_message(failure) if failure is not None else None
            self._emit_state()

            snapshot = self.snapshot()
            if failure is None:
                logger.info(
                    "audio recording stopped endpoint=%s output=%s "
                    "duration_seconds=%.1f packets=%d samples=%d",
                    snapshot.endpoint,
                    snapshot.output_path,
                    snapshot.elapsed_seconds,
                    snapshot.packets,
                    snapshot.samples,
                )
            else:
                logger.error(
                    "audio recording stop failed endpoint=%s output=%s error=%s",
                    snapshot.endpoint,
                    snapshot.output_path,
                    snapshot.error,
                )
            if any(snapshot.reliability.as_dict().values()):
                logger.warning(
                    "audio recording reliability counters nonzero endpoint=%s counters=%s",
                    snapshot.endpoint,
                    snapshot.reliability.as_dict(),
                )

            if failure is not None:
                raise failure

    def close(self) -> None:
        self.stop()

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()

    def _emit_state(self) -> None:
        self.events.emit("state", self.snapshot())

    def _reliability_snapshot(self) -> AudioReliabilitySnapshot:
        transport = self.stream.transport
        if not isinstance(transport, StatisticalAudioTransport):
            return AudioReliabilitySnapshot()
        statistics = transport.statistics
        return AudioReliabilitySnapshot(
            packets_lost=statistics.packets_lost,
            duplicate_packets=statistics.duplicate_packets,
            late_packets=statistics.late_packets,
            malformed_packets=statistics.malformed_packets,
            unexpected_source_packets=statistics.unexpected_source_packets,
            ssrc_mismatch_packets=statistics.ssrc_mismatch_packets,
            timestamp_discontinuities=statistics.timestamp_discontinuities,
            receive_errors=statistics.receive_errors,
            callback_errors=statistics.callback_errors,
        )
