from __future__ import annotations

import logging
import os
import stat
import threading
import wave
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from time import monotonic
from typing import BinaryIO, Protocol

from .audio import AudioStream
from .audio_recording import (
    PCM_CHANNELS,
    PCM_SAMPLE_WIDTH,
    PCMU_SAMPLE_RATE,
    PcmuWavRecorder,
)
from .audio_session import (
    AudioReliabilitySnapshot,
    AudioSessionSnapshot,
    AudioSessionStatus,
    StatisticalAudioTransport,
)
from .audio_sinks import PcmSink, PcmSinkStatistics, PcmWavSink
from .events import EventBus
from .recording_identity import RecordingIdentity
from .recording_inventory import (
    RecordingInventoryEntry,
    RecordingInventorySummary,
    scan_recording_inventory,
)
from .recording_metadata import RecordingMetadata, write_recording_metadata
from .recording_organization import RecordingOrganizationPolicy
from .recording_paths import DEFAULT_RECORDING_TEMPLATE, RecordingPathPolicy
from .state import RadioStateSnapshot

logger = logging.getLogger(__name__)

DAEMON_RECORDING_DEFAULT_INVENTORY_LIMIT = 50


class DaemonRecordingError(RuntimeError):
    """Base error for daemon-owned recording operations."""


class DaemonRecordingBusyError(DaemonRecordingError):
    """Raised when a second recording start is requested while one is active."""


class DaemonRecordingUnavailableError(DaemonRecordingError):
    """Raised when daemon recording cannot currently be started."""


class DaemonRecordingOperationError(DaemonRecordingError):
    """Raised when a recording start or finalization operation fails."""


class DaemonRecordingFileError(DaemonRecordingError):
    """Base error for finalized daemon recording-file access."""


class DaemonRecordingIdentifierError(DaemonRecordingFileError):
    """Raised when a recording identifier is not inventory-relative."""


class DaemonRecordingFileNotFoundError(DaemonRecordingFileError):
    """Raised when no finalized managed recording matches an identifier."""


class DaemonRecordingFileNotPlayableError(DaemonRecordingFileError):
    """Raised when a managed recording is not compatible WAV audio."""


class DaemonRecordingFileUnavailableError(DaemonRecordingFileError):
    """Raised when a matching recording is still active or changing."""


class _RadioStateSource(Protocol):
    @property
    def snapshot(self) -> RadioStateSnapshot: ...


class _ScannerLike(Protocol):
    @property
    def state(self) -> _RadioStateSource: ...


class _AudioLike(Protocol):
    @property
    def stream(self) -> AudioStream: ...


class _RuntimeLike(Protocol):
    @property
    def running(self) -> bool: ...

    @property
    def scanner(self) -> _ScannerLike: ...

    @property
    def audio(self) -> _AudioLike: ...

    def attach_sink(self, sink: PcmSink) -> None: ...

    def detach_sink(self, sink: PcmSink, *, stop: bool = True) -> None: ...


def _local_now() -> datetime:
    return datetime.now().astimezone()


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Daemon recording wall clock must be timezone-aware.")
    return value


def _redacted_error_type(error: BaseException) -> str:
    return error.__class__.__name__


def _sink_statistics_as_dict(
    statistics: PcmSinkStatistics,
) -> dict[str, int]:
    return {
        "bytes_submitted": statistics.bytes_submitted,
        "bytes_written": statistics.bytes_written,
        "bytes_dropped": statistics.bytes_dropped,
        "queued_bytes": statistics.queued_bytes,
        "underflows": statistics.underflows,
        "overflows": statistics.overflows,
        "callback_statuses": statistics.callback_statuses,
    }


@dataclass(frozen=True, slots=True)
class DaemonRecordingInventorySnapshot:
    """Bounded newest-first view of finalized daemon recordings."""

    entries: tuple[RecordingInventoryEntry, ...]
    summary: RecordingInventorySummary
    issues: tuple[str, ...]
    total_entries: int
    limit: int

    def as_dict(self) -> dict[str, object]:
        return {
            "limit": self.limit,
            "total_entries": self.total_entries,
            "summary": self.summary.as_dict(),
            "issues": list(self.issues),
            "entries": [entry.as_dict() for entry in self.entries],
        }


@dataclass(slots=True)
class DaemonRecordingFile:
    """One securely opened finalized recording beneath the daemon root."""

    identifier: str
    size_bytes: int
    stream: BinaryIO

    def close(self) -> None:
        self.stream.close()

    def __enter__(self) -> DaemonRecordingFile:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception_type, exception, traceback
        self.close()


@dataclass(frozen=True, slots=True)
class DaemonRecordingSnapshot:
    """Immutable state for one daemon-owned repeatable recording workflow."""

    status: AudioSessionStatus
    directory: Path
    recording_path: Path | None
    metadata_path: Path | None
    started_at: datetime | None
    stopped_at: datetime | None
    elapsed_seconds: float
    packets: int
    samples: int
    audio_duration_seconds: float
    reliability: AudioReliabilitySnapshot
    sink_statistics: PcmSinkStatistics
    completed_recordings: int
    closed: bool
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
            "active": self.active,
            "recording": self._relative_path(self.recording_path),
            "metadata": self._relative_path(self.metadata_path),
            "started_at": (
                self.started_at.isoformat()
                if self.started_at is not None
                else None
            ),
            "stopped_at": (
                self.stopped_at.isoformat()
                if self.stopped_at is not None
                else None
            ),
            "elapsed_seconds": self.elapsed_seconds,
            "packets": self.packets,
            "samples": self.samples,
            "audio_duration_seconds": self.audio_duration_seconds,
            "reliability": self.reliability.as_dict(),
            "sink": _sink_statistics_as_dict(self.sink_statistics),
            "completed_recordings": self.completed_recordings,
            "closed": self.closed,
            "error": self.error,
        }

    def _relative_path(self, path: Path | None) -> str | None:
        if path is None:
            return None
        return str(path.relative_to(self.directory))


class DaemonRecordingManager:
    """Own repeatable WAV recordings on the daemon's existing PCM router."""

    def __init__(
        self,
        runtime: _RuntimeLike,
        directory: str | Path,
        *,
        template: str = DEFAULT_RECORDING_TEMPLATE,
        organization: RecordingOrganizationPolicy | None = None,
        scanner: str | None = None,
        buffer_seconds: float = 5.0,
        clock: Callable[[], float] = monotonic,
        now: Callable[[], datetime] = _local_now,
    ) -> None:
        if buffer_seconds <= 0:
            raise ValueError(
                "Daemon recording buffer must be greater than zero seconds."
            )

        selected_directory = Path(directory).expanduser()
        self.runtime = runtime
        self.directory = selected_directory
        self.path_policy = RecordingPathPolicy(
            directory=selected_directory,
            template=template,
            overwrite=False,
            organization=(
                organization
                if organization is not None
                else RecordingOrganizationPolicy()
            ),
        )
        self.scanner = scanner
        self.buffer_seconds = buffer_seconds
        self.events = EventBus()

        self._clock = clock
        self._now = now
        _require_aware(self._now())

        self._lifecycle_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._status = AudioSessionStatus.IDLE
        self._recording_path: Path | None = None
        self._metadata_path: Path | None = None
        self._started_at: datetime | None = None
        self._stopped_at: datetime | None = None
        self._started_clock: float | None = None
        self._elapsed_seconds = 0.0
        self._last_packets = 0
        self._last_samples = 0
        self._last_sink_statistics = PcmSinkStatistics()
        self._error: str | None = None
        self._recorder: PcmuWavRecorder | None = None
        self._sink: PcmWavSink | None = None
        self._started_snapshot: AudioSessionSnapshot | None = None
        self._started_state: RadioStateSnapshot | None = None
        self._completed_recordings = 0
        self._closed = False

    @property
    def closed(self) -> bool:
        with self._state_lock:
            return self._closed

    @property
    def recording_path(self) -> Path | None:
        with self._state_lock:
            return self._recording_path

    @property
    def metadata_path(self) -> Path | None:
        with self._state_lock:
            return self._metadata_path

    def on_state(
        self,
        callback: Callable[[DaemonRecordingSnapshot], None],
    ) -> Callable[[], None]:
        return self.events.subscribe("state", callback)

    def snapshot(self) -> DaemonRecordingSnapshot:
        with self._state_lock:
            status = self._status
            recording_path = self._recording_path
            metadata_path = self._metadata_path
            started_at = self._started_at
            stopped_at = self._stopped_at
            started_clock = self._started_clock
            elapsed_seconds = self._elapsed_seconds
            packets = self._last_packets
            samples = self._last_samples
            sink_statistics = self._last_sink_statistics
            error = self._error
            recorder = self._recorder
            sink = self._sink
            completed_recordings = self._completed_recordings
            closed = self._closed

        if started_clock is not None:
            elapsed_seconds = max(
                elapsed_seconds,
                self._clock() - started_clock,
            )
        if recorder is not None:
            packets = recorder.packets
            samples = recorder.samples
        if sink is not None:
            sink_statistics = sink.statistics

        return DaemonRecordingSnapshot(
            status=status,
            directory=self.directory,
            recording_path=recording_path,
            metadata_path=metadata_path,
            started_at=started_at,
            stopped_at=stopped_at,
            elapsed_seconds=max(0.0, elapsed_seconds),
            packets=packets,
            samples=samples,
            audio_duration_seconds=samples / PCMU_SAMPLE_RATE,
            reliability=self._reliability_snapshot(),
            sink_statistics=sink_statistics,
            completed_recordings=completed_recordings,
            closed=closed,
            error=error,
        )

    def list_recordings(
        self,
        *,
        limit: int = DAEMON_RECORDING_DEFAULT_INVENTORY_LIMIT,
    ) -> DaemonRecordingInventorySnapshot:
        """Return finalized recordings without exposing a caller-selected root."""

        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError("Daemon recording inventory limit must be an integer.")
        if limit <= 0:
            raise ValueError(
                "Daemon recording inventory limit must be greater than zero."
            )

        with self._state_lock:
            pending_path = (
                self._recording_path
                if self._sink is not None
                else None
            )

        directory = self.directory.expanduser()
        if not directory.exists():
            return DaemonRecordingInventorySnapshot(
                entries=(),
                summary=RecordingInventorySummary(),
                issues=(),
                total_entries=0,
                limit=limit,
            )

        try:
            inventory = scan_recording_inventory(directory)
        except (OSError, RuntimeError, ValueError) as error:
            raise DaemonRecordingOperationError(
                "Could not list daemon recordings."
            ) from error

        pending_resolved = (
            pending_path.resolve(strict=False)
            if pending_path is not None
            else None
        )
        finalized = tuple(
            entry
            for entry in inventory.entries
            if (
                pending_resolved is None
                or entry.audio_path.resolve(strict=False) != pending_resolved
            )
        )
        ordered = tuple(
            sorted(
                finalized,
                key=_inventory_newest_first_key,
            )
        )
        summary = RecordingInventorySummary.from_entries(
            finalized,
            scan_issues=len(inventory.issues),
        )
        return DaemonRecordingInventorySnapshot(
            entries=ordered[:limit],
            summary=summary,
            issues=inventory.issues,
            total_entries=len(finalized),
            limit=limit,
        )

    def open_recording(self, identifier: str) -> DaemonRecordingFile:
        """Securely open one finalized playable inventory recording."""

        normalized, parts = _recording_identifier(identifier)

        with self._state_lock:
            pending_path = (
                self._recording_path
                if self._sink is not None
                else None
            )

        directory = self.directory.expanduser()
        if not directory.exists():
            raise DaemonRecordingFileNotFoundError(
                "Daemon recording was not found."
            )

        try:
            inventory = scan_recording_inventory(directory)
        except (OSError, RuntimeError, ValueError) as error:
            raise DaemonRecordingOperationError(
                "Could not inspect daemon recordings."
            ) from error

        entry = next(
            (
                candidate
                for candidate in inventory.entries
                if candidate.relative_audio_path.as_posix() == normalized
            ),
            None,
        )
        if entry is None:
            raise DaemonRecordingFileNotFoundError(
                "Daemon recording was not found."
            )

        if (
            pending_path is not None
            and entry.audio_path.resolve(strict=False)
            == pending_path.resolve(strict=False)
        ):
            raise DaemonRecordingFileUnavailableError(
                "Daemon recording is not finalized."
            )

        if not entry.playable:
            raise DaemonRecordingFileNotPlayableError(
                "Daemon recording is not playable."
            )

        try:
            return _open_recording_file(
                inventory.root,
                normalized,
                parts,
            )
        except DaemonRecordingFileError:
            raise
        except OSError as error:
            raise DaemonRecordingOperationError(
                "Could not open daemon recording."
            ) from error

    def start_recording(self) -> DaemonRecordingSnapshot:
        """Attach one new WAV sink without opening another scanner audio stream."""

        with self._lifecycle_lock:
            with self._state_lock:
                if self._closed:
                    raise DaemonRecordingUnavailableError(
                        "Daemon recording manager is closed."
                    )
                if self._status in {
                    AudioSessionStatus.STARTING,
                    AudioSessionStatus.RECORDING,
                    AudioSessionStatus.STOPPING,
                } or self._sink is not None:
                    raise DaemonRecordingBusyError(
                        "A daemon recording is already active or awaiting finalization."
                    )
            if not self.runtime.running:
                raise DaemonRecordingUnavailableError(
                    "Daemon audio runtime is not running."
                )

            started_at = _require_aware(self._now())
            started_state = self.runtime.scanner.state.snapshot
            with self._state_lock:
                self._status = AudioSessionStatus.STARTING
                self._recording_path = None
                self._metadata_path = None
                self._started_at = None
                self._stopped_at = None
                self._started_clock = None
                self._elapsed_seconds = 0.0
                self._last_packets = 0
                self._last_samples = 0
                self._last_sink_statistics = PcmSinkStatistics()
                self._error = None
                self._started_snapshot = None
                self._started_state = None
            self._emit_state()

            identity = (
                RecordingIdentity.from_start_boundary(
                    started_at=started_at,
                    endpoint=self.runtime.audio.stream.endpoint,
                    scanner=self.scanner,
                    state=started_state,
                )
                if self.path_policy.organization.enabled
                else None
            )

            path: Path | None = None
            recorder: PcmuWavRecorder | None = None
            sink: PcmWavSink | None = None
            attach_attempted = False
            try:
                path = self.path_policy.next_path(
                    started_at,
                    explicit_used=False,
                    metadata=True,
                    identity=identity,
                )
                recorder = PcmuWavRecorder(path)
                sink = PcmWavSink(
                    recorder,
                    buffer_seconds=self.buffer_seconds,
                )
                attach_attempted = True
                self.runtime.attach_sink(sink)
            except BaseException as error:
                if sink is not None and attach_attempted:
                    with suppress(Exception):
                        self.runtime.detach_sink(sink, stop=False)
                    with suppress(Exception):
                        sink.stop()
                if recorder is not None:
                    with suppress(Exception):
                        recorder.close()
                with self._state_lock:
                    self._status = AudioSessionStatus.FAILED
                    self._recording_path = path
                    self._stopped_at = _require_aware(self._now())
                    self._error = _redacted_error_type(error)
                self._emit_state()
                raise DaemonRecordingOperationError(
                    "Could not start daemon recording."
                ) from error

            assert path is not None
            assert recorder is not None
            assert sink is not None

            started_clock = self._clock()
            with self._state_lock:
                self._recording_path = path
                self._started_at = started_at
                self._started_clock = started_clock
                self._recorder = recorder
                self._sink = sink
                self._status = AudioSessionStatus.RECORDING
                self._started_state = started_state

            started_snapshot = self._session_snapshot(
                status=AudioSessionStatus.RECORDING,
                recording_path=path,
                started_at=started_at,
                stopped_at=None,
                elapsed_seconds=0.0,
                packets=0,
                samples=0,
                error=None,
            )
            with self._state_lock:
                self._started_snapshot = started_snapshot

            logger.info(
                "daemon recording started endpoint=%s recording=%s",
                self.runtime.audio.stream.endpoint,
                path,
            )
            self._emit_state()
            return self.snapshot()

    def stop_recording(self) -> DaemonRecordingSnapshot:
        """Detach and finalize the active recording without stopping daemon audio."""

        with self._lifecycle_lock:
            with self._state_lock:
                sink = self._sink
                if self._status not in {
                    AudioSessionStatus.STARTING,
                    AudioSessionStatus.RECORDING,
                    AudioSessionStatus.STOPPING,
                } and sink is None:
                    return self.snapshot()

                self._status = AudioSessionStatus.STOPPING
                recorder = self._recorder
                started_clock = self._started_clock
                prior_elapsed_seconds = self._elapsed_seconds
                started_snapshot = self._started_snapshot
                started_state = self._started_state
                recording_path = self._recording_path
            self._emit_state()

            failure: BaseException | None = None
            if sink is not None:
                try:
                    self.runtime.detach_sink(sink, stop=False)
                except BaseException as error:
                    failure = error
                try:
                    sink.stop()
                except BaseException as error:
                    if failure is None:
                        failure = error

            ended_clock = self._clock()
            stopped_at = _require_aware(self._now())
            packets = recorder.packets if recorder is not None else 0
            samples = recorder.samples if recorder is not None else 0
            sink_statistics = (
                sink.statistics
                if sink is not None
                else PcmSinkStatistics()
            )

            elapsed_seconds = prior_elapsed_seconds
            if started_clock is not None:
                elapsed_seconds = max(0.0, ended_clock - started_clock)

            stopped_snapshot = self._session_snapshot(
                status=(
                    AudioSessionStatus.FAILED
                    if failure is not None
                    else AudioSessionStatus.STOPPED
                ),
                recording_path=recording_path or self.directory,
                started_at=(
                    started_snapshot.started_at
                    if started_snapshot is not None
                    else self._started_at
                ),
                stopped_at=stopped_at,
                elapsed_seconds=elapsed_seconds,
                packets=packets,
                samples=samples,
                error=(
                    _redacted_error_type(failure)
                    if failure is not None
                    else None
                ),
            )

            metadata_path: Path | None = None
            if failure is None:
                if started_snapshot is None:
                    failure = RuntimeError(
                        "Recording metadata start boundary is unavailable."
                    )
                else:
                    try:
                        metadata = RecordingMetadata.from_snapshots(
                            started_snapshot,
                            stopped_snapshot,
                            scanner=self.scanner,
                            started_state=started_state,
                            stopped_state=self.runtime.scanner.state.snapshot,
                        )
                        metadata_path = write_recording_metadata(metadata)
                    except BaseException as error:
                        failure = error

            with self._state_lock:
                self._started_clock = None
                self._elapsed_seconds = elapsed_seconds
                self._last_packets = packets
                self._last_samples = samples
                self._last_sink_statistics = sink_statistics
                self._stopped_at = stopped_at
                self._metadata_path = metadata_path
                self._status = (
                    AudioSessionStatus.FAILED
                    if failure is not None
                    else AudioSessionStatus.STOPPED
                )
                self._error = (
                    _redacted_error_type(failure)
                    if failure is not None
                    else None
                )
                if failure is None:
                    self._recorder = None
                    self._sink = None
                    self._started_snapshot = None
                    self._started_state = None
                    self._completed_recordings += 1

            logger.info(
                "daemon recording stopped endpoint=%s recording=%s "
                "samples=%d metadata=%s",
                self.runtime.audio.stream.endpoint,
                recording_path or "-",
                samples,
                metadata_path or "-",
            )
            self._emit_state()

            if failure is not None:
                raise DaemonRecordingOperationError(
                    "Could not finalize daemon recording."
                ) from failure
            return self.snapshot()

    def close(self) -> None:
        """Finalize any active recording and permanently close this manager."""

        failure: BaseException | None = None
        with self._lifecycle_lock:
            try:
                self.stop_recording()
            except BaseException as error:
                failure = error
            with self._state_lock:
                if self._closed:
                    if failure is not None:
                        raise failure
                    return
                self._closed = True
            self._emit_state()

        if failure is not None:
            raise failure

    def _session_snapshot(
        self,
        *,
        status: AudioSessionStatus,
        recording_path: Path,
        started_at: datetime | None,
        stopped_at: datetime | None,
        elapsed_seconds: float,
        packets: int,
        samples: int,
        error: str | None,
    ) -> AudioSessionSnapshot:
        return AudioSessionSnapshot(
            status=status,
            endpoint=self.runtime.audio.stream.endpoint,
            output_path=recording_path,
            started_at=started_at,
            stopped_at=stopped_at,
            elapsed_seconds=elapsed_seconds,
            packets=packets,
            samples=samples,
            audio_duration_seconds=samples / PCMU_SAMPLE_RATE,
            reliability=self._reliability_snapshot(),
            error=error,
        )

    def _reliability_snapshot(self) -> AudioReliabilitySnapshot:
        transport = self.runtime.audio.stream.transport
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

    def _emit_state(self) -> None:
        self.events.emit("state", self.snapshot())


def _recording_identifier(value: object) -> tuple[str, tuple[str, ...]]:
    if not isinstance(value, str):
        raise DaemonRecordingIdentifierError(
            "Daemon recording identifier must be a string."
        )
    if not value or value.strip() != value:
        raise DaemonRecordingIdentifierError(
            "Daemon recording identifier must not be empty or padded."
        )
    if "\x00" in value:
        raise DaemonRecordingIdentifierError(
            "Daemon recording identifier must not contain a null byte."
        )

    parts = tuple(value.split("/"))
    if (
        value.startswith("/")
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise DaemonRecordingIdentifierError(
            "Daemon recording identifier must be inventory-relative."
        )

    normalized = PurePosixPath(*parts).as_posix()
    if normalized != value:
        raise DaemonRecordingIdentifierError(
            "Daemon recording identifier is not canonical."
        )
    if not parts[-1].casefold().endswith(".wav"):
        raise DaemonRecordingIdentifierError(
            "Daemon recording identifier must name a WAV file."
        )
    return normalized, parts


def _open_recording_file(
    root: Path,
    identifier: str,
    parts: tuple[str, ...],
) -> DaemonRecordingFile:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if (
        nofollow is None
        or directory_flag is None
        or os.open not in os.supports_dir_fd
    ):
        raise DaemonRecordingOperationError(
            "Secure daemon recording access is unavailable."
        )

    directory_flags = os.O_RDONLY | directory_flag | nofollow
    file_flags = os.O_RDONLY | nofollow
    directory_fd = os.open(root, directory_flags)
    current_fd = directory_fd
    opened_file_fd: int | None = None

    try:
        for component in parts[:-1]:
            next_fd = os.open(
                component,
                directory_flags,
                dir_fd=current_fd,
            )
            if current_fd != directory_fd:
                os.close(current_fd)
            current_fd = next_fd

        opened_file_fd = os.open(
            parts[-1],
            file_flags,
            dir_fd=current_fd,
        )
    finally:
        if current_fd != directory_fd:
            os.close(current_fd)
        os.close(directory_fd)

    assert opened_file_fd is not None
    try:
        statistics = os.fstat(opened_file_fd)
        if not stat.S_ISREG(statistics.st_mode):
            raise DaemonRecordingFileNotPlayableError(
                "Daemon recording is not a regular file."
            )

        stream = os.fdopen(opened_file_fd, "rb")
        opened_file_fd = None
        try:
            _validate_open_recording(stream)
            stream.seek(0)
        except BaseException:
            stream.close()
            raise

        return DaemonRecordingFile(
            identifier=identifier,
            size_bytes=statistics.st_size,
            stream=stream,
        )
    finally:
        if opened_file_fd is not None:
            os.close(opened_file_fd)


def _validate_open_recording(stream: BinaryIO) -> None:
    try:
        with wave.open(stream, "rb") as recording:
            compatible = (
                recording.getnchannels() == PCM_CHANNELS
                and recording.getsampwidth() == PCM_SAMPLE_WIDTH
                and recording.getframerate() == PCMU_SAMPLE_RATE
                and recording.getcomptype() == "NONE"
            )
    except (EOFError, OSError, wave.Error) as error:
        raise DaemonRecordingFileNotPlayableError(
            "Daemon recording WAV audio could not be read."
        ) from error

    if not compatible:
        raise DaemonRecordingFileNotPlayableError(
            "Daemon recording WAV audio is incompatible."
        )


def _inventory_newest_first_key(
    entry: RecordingInventoryEntry,
) -> tuple[float, int, str, str]:
    recorded_at = (
        entry.recorded_at.astimezone(UTC).timestamp()
        if entry.recorded_at is not None
        else entry.modified_ns / 1_000_000_000
    )
    relative = entry.relative_audio_path.as_posix()
    return (
        -recorded_at,
        -entry.modified_ns,
        relative.casefold(),
        relative,
    )
