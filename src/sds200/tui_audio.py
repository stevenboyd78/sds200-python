from __future__ import annotations

import logging
import threading
import wave
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from string import Formatter
from time import monotonic, sleep
from typing import Self

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
from .audio_sinks import (
    AudioFanoutSession,
    MuteablePcmSink,
    PcmSink,
    PcmSinkStatistics,
    PcmWavSink,
    SoundDevicePlaybackSink,
)
from .events import EventBus
from .recording_identity import RecordingIdentity
from .recording_metadata import (
    RecordingMetadata,
    recording_metadata_path,
    write_recording_metadata,
)
from .recording_organization import RecordingOrganizationPolicy
from .state import RadioStateSnapshot

logger = logging.getLogger(__name__)
DEFAULT_RECORDING_TEMPLATE = "sds200-{timestamp}.wav"
_SAVED_PLAYBACK_CHUNK_FRAMES = PCMU_SAMPLE_RATE // 20


def _local_now() -> datetime:
    return datetime.now().astimezone()


def _error_message(error: BaseException) -> str:
    return str(error) or type(error).__name__


def _set_playback_muted(playback: PcmSink, muted: bool) -> None:
    if isinstance(playback, MuteablePcmSink):
        playback.set_muted(muted)


@dataclass(frozen=True, slots=True)
class RecordingEntry:
    """One compatible PCM WAV recording discovered in the configured library."""

    path: Path
    recorded_at: datetime
    duration_seconds: float
    size_bytes: int
    frames: int
    modified_ns: int


@dataclass(frozen=True, slots=True)
class RecordingPathPolicy:
    """Resolve explicit or collision-safe organized TUI recording paths."""

    output: Path | None = None
    directory: Path | None = None
    template: str = DEFAULT_RECORDING_TEMPLATE
    overwrite: bool = False
    organization: RecordingOrganizationPolicy = field(
        default_factory=RecordingOrganizationPolicy
    )

    def __post_init__(self) -> None:
        if self.output is not None and self.directory is not None:
            raise ValueError("Audio output and audio directory are mutually exclusive")
        if self.organization.enabled and self.directory is None:
            raise ValueError("Audio organization requires an audio directory")
        if not self.template.strip():
            raise ValueError("Audio filename template must not be empty")
        try:
            fields = tuple(Formatter().parse(self.template))
        except ValueError as error:
            raise ValueError(
                "Audio filename template must include only the {timestamp} field"
            ) from error
        replacements = tuple(
            field for _literal, field, _spec, _conversion in fields if field
        )
        if not replacements or any(
            field != "timestamp" or bool(spec) or conversion is not None
            for _literal, field, spec, conversion in fields
            if field is not None
        ):
            raise ValueError(
                "Audio filename template must include only the {timestamp} field"
            )
        try:
            rendered = self.template.format(timestamp="20000101-000000")
        except (KeyError, IndexError, ValueError) as error:
            raise ValueError(
                "Audio filename template must include only the {timestamp} field"
            ) from error
        if Path(rendered).is_absolute() or Path(rendered).name != rendered:
            raise ValueError("Audio filename template must produce a file name")
        if not rendered.casefold().endswith(".wav"):
            raise ValueError("Audio filename template must produce a .wav file")

    @property
    def enabled(self) -> bool:
        return self.output is not None or self.directory is not None

    @property
    def display_path(self) -> Path:
        if self.output is not None:
            return self.output.expanduser()
        if self.directory is not None:
            return self.directory.expanduser()
        return Path("-")

    @property
    def repeatable(self) -> bool:
        return self.directory is not None

    def next_path(
        self,
        now: datetime,
        *,
        explicit_used: bool,
        metadata: bool = False,
        identity: RecordingIdentity | None = None,
    ) -> Path:
        def available(candidate: Path) -> bool:
            return not candidate.exists() and (
                not metadata or not recording_metadata_path(candidate).exists()
            )

        if self.output is not None:
            if explicit_used:
                raise RuntimeError(
                    "The explicit TUI audio output has already been used; "
                    "restart the TUI or use --audio-directory"
                )
            output = self.output.expanduser()
            sidecar = recording_metadata_path(output)
            if metadata and not self.overwrite and sidecar.exists():
                raise FileExistsError(f"Recording metadata already exists: {sidecar}")
            return output
        if self.directory is None:
            raise RuntimeError("TUI audio recording is not configured")

        directory = self.directory.expanduser()
        if self.organization.enabled:
            if identity is None:
                raise RuntimeError(
                    "Recording organization requires a start-boundary identity"
                )
            directory = directory / self.organization.relative_directory(identity)
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = now.strftime("%Y%m%d-%H%M%S")
        name = self.template.format(timestamp=timestamp)
        candidate = directory / name
        if available(candidate):
            return candidate
        for sequence in range(2, 10_000):
            suffixed = candidate.with_name(
                f"{candidate.stem}-{sequence}{candidate.suffix}"
            )
            if available(suffixed):
                return suffixed
        raise RuntimeError("Could not allocate a collision-safe audio filename")

    def library_paths(self) -> tuple[Path, ...]:
        if self.directory is not None:
            directory = self.directory.expanduser()
            if not directory.exists():
                return ()
            return tuple(directory.rglob("*.wav"))
        if self.output is not None:
            output = self.output.expanduser()
            return (output,) if output.exists() else ()
        return ()


class SavedPlaybackStatus(StrEnum):
    """Lifecycle state for playback of one saved recording."""

    STOPPED = "stopped"
    PLAYING = "playing"
    PAUSED = "paused"
    FAILED = "failed"


class PcmSinkRouter:
    """Dynamically attach nonblocking PCM sinks behind one fanout destination."""

    def __init__(self) -> None:
        self._lifecycle_lock = threading.RLock()
        self._submit_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._sinks: list[PcmSink] = []
        self._running = False
        self._bytes_submitted = 0

    @property
    def name(self) -> str:
        return "tui-audio-router"

    @property
    def running(self) -> bool:
        with self._state_lock:
            return self._running

    @property
    def statistics(self) -> PcmSinkStatistics:
        with self._state_lock:
            sinks = tuple(self._sinks)
            submitted = self._bytes_submitted
        statistics = tuple(sink.statistics for sink in sinks)
        return PcmSinkStatistics(
            bytes_submitted=submitted,
            bytes_written=sum(item.bytes_written for item in statistics),
            bytes_dropped=sum(item.bytes_dropped for item in statistics),
            queued_bytes=sum(item.queued_bytes for item in statistics),
            underflows=sum(item.underflows for item in statistics),
            overflows=sum(item.overflows for item in statistics),
            callback_statuses=sum(item.callback_statuses for item in statistics),
        )

    def start(self) -> None:
        with self._lifecycle_lock:
            with self._state_lock:
                if self._running:
                    return
                sinks = tuple(self._sinks)
            started: list[PcmSink] = []
            try:
                for sink in sinks:
                    sink.start()
                    started.append(sink)
            except BaseException:
                for sink in reversed(started):
                    with suppress(Exception):
                        sink.stop()
                raise
            with self._state_lock:
                self._running = True

    def attach(self, sink: PcmSink) -> None:
        with self._lifecycle_lock:
            with self._state_lock:
                if sink in self._sinks:
                    return
                running = self._running
            if running:
                sink.start()
            with self._state_lock:
                self._sinks.append(sink)

    def detach(self, sink: PcmSink, *, stop: bool = True) -> None:
        with self._lifecycle_lock:
            with self._state_lock:
                if sink not in self._sinks:
                    return
                self._sinks.remove(sink)
            # Wait for a callback that already captured this sink to finish.
            with self._submit_lock:
                pass
            if stop:
                sink.stop()

    def submit_pcm(self, data: bytes) -> None:
        with self._submit_lock:
            with self._state_lock:
                if not self._running:
                    return
                sinks = tuple(self._sinks)
                self._bytes_submitted += len(data)
            for sink in sinks:
                try:
                    sink.submit_pcm(data)
                except Exception:
                    logger.exception("TUI audio sink rejected PCM sink=%s", sink.name)

    def stop(self) -> None:
        with self._lifecycle_lock:
            with self._state_lock:
                if not self._running:
                    return
                self._running = False
                sinks = tuple(reversed(self._sinks))
                self._sinks.clear()
            # Do not stop sinks while an in-flight callback is submitting PCM.
            with self._submit_lock:
                pass
            failure: BaseException | None = None
            for sink in sinks:
                try:
                    sink.stop()
                except BaseException as error:
                    if failure is None:
                        failure = error
            if failure is not None:
                raise failure


class TuiAudioSession:
    """Long-lived TUI audio stream with repeatable recording and playback."""

    def __init__(
        self,
        stream: AudioStream,
        path_policy: RecordingPathPolicy,
        *,
        live_playback: bool = False,
        device: str | int | None = None,
        buffer_ms: int = 250,
        history_limit: int = 100,
        metadata: bool = False,
        scanner: str | None = None,
        playback_sink: PcmSink | None = None,
        clock: Callable[[], float] = monotonic,
        now: Callable[[], datetime] = _local_now,
    ) -> None:
        if history_limit <= 0:
            raise ValueError("Audio history limit must be greater than zero")
        if scanner is not None and not scanner.strip():
            raise ValueError("Audio scanner identity must not be empty")
        self.stream = stream
        self.path_policy = path_policy
        self.events = EventBus()
        self._clock = clock
        self._now = now
        self._history_limit = history_limit
        self._metadata_enabled = metadata
        self._scanner = scanner.strip() if scanner is not None else None
        self._radio_state = RadioStateSnapshot()
        self._recording_started_snapshot: AudioSessionSnapshot | None = None
        self._recording_started_state: RadioStateSnapshot | None = None
        self._last_metadata_path: Path | None = None
        self._router = PcmSinkRouter()
        self._fanout = AudioFanoutSession(stream, (self._router,))
        self._playback: PcmSink = playback_sink or SoundDevicePlaybackSink(
            device=device,
            buffer_ms=buffer_ms,
        )
        _set_playback_muted(self._playback, True)
        self._live_playback_enabled = live_playback
        self._live_playback_attached = False
        self._lifecycle_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._open = False
        self._status = AudioSessionStatus.IDLE
        self._started_at: datetime | None = None
        self._stopped_at: datetime | None = None
        self._started_clock: float | None = None
        self._elapsed_seconds = 0.0
        self._error: str | None = None
        self._recording_sink: PcmWavSink | None = None
        self._recorder: PcmuWavRecorder | None = None
        self._output_path = path_policy.display_path
        self._last_packets = 0
        self._last_samples = 0
        self._explicit_used = False
        self._completed_count = 0
        self._last_completed: RecordingEntry | None = None
        self._recordings: tuple[RecordingEntry, ...] = ()
        self._saved_status = SavedPlaybackStatus.STOPPED
        self._saved_path: Path | None = None
        self._saved_error: str | None = None
        self._saved_stop = threading.Event()
        self._saved_pause = threading.Event()
        self._saved_thread: threading.Thread | None = None

    @property
    def status(self) -> AudioSessionStatus:
        with self._state_lock:
            return self._status

    @property
    def active(self) -> bool:
        return self.snapshot().active

    @property
    def open(self) -> bool:
        with self._state_lock:
            return self._open

    @property
    def recording_enabled(self) -> bool:
        return self.path_policy.enabled

    @property
    def repeatable(self) -> bool:
        return self.path_policy.repeatable

    @property
    def metadata_enabled(self) -> bool:
        return self._metadata_enabled

    @property
    def last_metadata_path(self) -> Path | None:
        with self._state_lock:
            return self._last_metadata_path

    @property
    def playback_available(self) -> bool:
        return True

    @property
    def playback_prepared(self) -> bool:
        return self._playback.running

    @property
    def live_playback_enabled(self) -> bool:
        with self._state_lock:
            return self._live_playback_enabled

    @property
    def live_playback_active(self) -> bool:
        with self._state_lock:
            return self._live_playback_attached

    @property
    def saved_playback_status(self) -> SavedPlaybackStatus:
        with self._state_lock:
            return self._saved_status

    @property
    def saved_playback_path(self) -> Path | None:
        with self._state_lock:
            return self._saved_path

    @property
    def saved_playback_error(self) -> str | None:
        with self._state_lock:
            return self._saved_error

    @property
    def recordings(self) -> tuple[RecordingEntry, ...]:
        with self._state_lock:
            return self._recordings

    @property
    def completed_recordings(self) -> int:
        with self._state_lock:
            return self._completed_count

    @property
    def last_completed(self) -> RecordingEntry | None:
        with self._state_lock:
            return self._last_completed

    @property
    def playback_statistics(self) -> PcmSinkStatistics | None:
        playback = self._playback
        return playback.statistics if playback is not None else None

    def update_radio_state(self, snapshot: RadioStateSnapshot) -> None:
        """Retain the latest immutable scanner state for recording boundaries."""

        with self._state_lock:
            self._radio_state = snapshot

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
            elapsed = self._elapsed_seconds
            error = self._error
            output_path = self._output_path
            recorder = self._recorder
            packets = self._last_packets
            samples = self._last_samples
        if started_clock is not None:
            elapsed = max(elapsed, self._clock() - started_clock)
        if recorder is not None:
            packets = recorder.packets
            samples = recorder.samples
        return AudioSessionSnapshot(
            status=status,
            endpoint=self.stream.endpoint,
            output_path=output_path,
            started_at=started_at,
            stopped_at=stopped_at,
            elapsed_seconds=max(0.0, elapsed),
            packets=packets,
            samples=samples,
            audio_duration_seconds=samples / PCMU_SAMPLE_RATE,
            reliability=self._reliability_snapshot(),
            error=error,
        )

    def open_audio(self) -> None:
        """Start the shared RTSP/RTP stream without opening the output device."""
        with self._lifecycle_lock:
            with self._state_lock:
                if self._open:
                    return
            self._fanout.start()
            with self._state_lock:
                self._open = True
            self.refresh_recordings()
            self._emit_state()

    def start(self) -> None:
        """Start one recording while retaining the shared audio stream."""
        with self._lifecycle_lock:
            if not self.open:
                self.open_audio()
            if not self.recording_enabled:
                raise RuntimeError(
                    "Start the TUI with --audio-directory or --audio-output "
                    "to enable recording"
                )
            started_at = self._now()
            with self._state_lock:
                if self._status in {
                    AudioSessionStatus.STARTING,
                    AudioSessionStatus.RECORDING,
                    AudioSessionStatus.STOPPING,
                }:
                    raise RuntimeError("An audio recording is already active")
                self._status = AudioSessionStatus.STARTING
                self._started_at = None
                self._started_clock = None
                self._stopped_at = None
                self._elapsed_seconds = 0.0
                self._last_packets = 0
                self._last_samples = 0
                self._error = None
                self._recording_started_snapshot = None
                self._recording_started_state = None
                self._last_metadata_path = None
                started_state = self._radio_state
            self._emit_state()

            identity = (
                RecordingIdentity.from_start_boundary(
                    started_at=started_at,
                    endpoint=self.stream.endpoint,
                    scanner=self._scanner,
                    state=started_state,
                )
                if self.path_policy.organization.enabled
                else None
            )
            path: Path | None = None
            recorder: PcmuWavRecorder | None = None
            sink: PcmWavSink | None = None
            try:
                path = self.path_policy.next_path(
                    started_at,
                    explicit_used=self._explicit_used,
                    metadata=self._metadata_enabled,
                    identity=identity,
                )
                recorder = PcmuWavRecorder(
                    path,
                    overwrite=self.path_policy.overwrite,
                )
                sink = PcmWavSink(recorder)
                self._router.attach(sink)
            except BaseException as error:
                if recorder is not None:
                    with suppress(Exception):
                        recorder.close()
                with self._state_lock:
                    self._status = AudioSessionStatus.FAILED
                    self._stopped_at = self._now()
                    self._error = _error_message(error)
                    self._output_path = path or self.path_policy.display_path
                self._emit_state()
                raise

            assert path is not None
            assert recorder is not None
            assert sink is not None
            with self._state_lock:
                self._recording_sink = sink
                self._recorder = recorder
                self._output_path = path
                self._started_clock = self._clock()
                self._started_at = started_at
                self._status = AudioSessionStatus.RECORDING
                if self.path_policy.output is not None:
                    self._explicit_used = True
            started_snapshot = self.snapshot()
            with self._state_lock:
                self._recording_started_snapshot = started_snapshot
                self._recording_started_state = started_state
            logger.info(
                "TUI audio recording started endpoint=%s output=%s",
                self.stream.endpoint,
                path,
            )
            self._emit_state()

    def stop(self) -> None:
        """Stop and finalize the active recording without stopping live audio."""
        with self._lifecycle_lock:
            with self._state_lock:
                if self._status not in {
                    AudioSessionStatus.STARTING,
                    AudioSessionStatus.RECORDING,
                    AudioSessionStatus.STOPPING,
                }:
                    return
                self._status = AudioSessionStatus.STOPPING
                sink, self._recording_sink = self._recording_sink, None
                recorder, self._recorder = self._recorder, None
                started_clock = self._started_clock
                started_snapshot = self._recording_started_snapshot
                started_state = self._recording_started_state
            self._emit_state()

            failure: BaseException | None = None
            if sink is not None:
                try:
                    self._router.detach(sink)
                except BaseException as error:
                    failure = error
            ended = self._clock()
            stopped_at = self._now()
            packets = recorder.packets if recorder is not None else 0
            samples = recorder.samples if recorder is not None else 0
            output_path = recorder.path if recorder is not None else self._output_path
            with self._state_lock:
                elapsed_seconds = self._elapsed_seconds
                if started_clock is not None:
                    elapsed_seconds = max(0.0, ended - started_clock)
                stopped_state = self._radio_state

            stopped_snapshot = AudioSessionSnapshot(
                status=(
                    AudioSessionStatus.FAILED
                    if failure is not None
                    else AudioSessionStatus.STOPPED
                ),
                endpoint=self.stream.endpoint,
                output_path=output_path,
                started_at=(
                    started_snapshot.started_at
                    if started_snapshot is not None
                    else self._started_at
                ),
                stopped_at=stopped_at,
                elapsed_seconds=elapsed_seconds,
                packets=packets,
                samples=samples,
                audio_duration_seconds=samples / PCMU_SAMPLE_RATE,
                reliability=self._reliability_snapshot(),
                error=_error_message(failure) if failure is not None else None,
            )

            metadata_path: Path | None = None
            if failure is None and self._metadata_enabled:
                if started_snapshot is None:
                    failure = RuntimeError(
                        "Recording metadata start boundary is unavailable"
                    )
                else:
                    try:
                        metadata = RecordingMetadata.from_snapshots(
                            started_snapshot,
                            stopped_snapshot,
                            scanner=self._scanner,
                            started_state=started_state,
                            stopped_state=stopped_state,
                        )
                        metadata_path = write_recording_metadata(
                            metadata,
                            overwrite=self.path_policy.overwrite,
                        )
                    except BaseException as error:
                        failure = error

            with self._state_lock:
                self._elapsed_seconds = elapsed_seconds
                self._started_clock = None
                self._stopped_at = stopped_at
                self._last_packets = packets
                self._last_samples = samples
                self._output_path = output_path
                self._status = (
                    AudioSessionStatus.FAILED
                    if failure is not None
                    else AudioSessionStatus.STOPPED
                )
                self._error = _error_message(failure) if failure is not None else None
                self._recording_started_snapshot = None
                self._recording_started_state = None
                self._last_metadata_path = metadata_path
                if failure is None:
                    self._completed_count += 1
            self.refresh_recordings()
            if failure is None:
                with self._state_lock:
                    self._last_completed = next(
                        (
                            entry
                            for entry in self._recordings
                            if entry.path == output_path
                        ),
                        None,
                    )
            logger.info(
                "TUI audio recording stopped endpoint=%s output=%s samples=%d metadata=%s",
                self.stream.endpoint,
                output_path,
                samples,
                metadata_path or "-",
            )
            self._emit_state()
            if failure is not None:
                raise failure

    def request_live_playback(self, enabled: bool) -> None:
        """Change the requested playback state without touching the output device."""
        with self._state_lock:
            self._live_playback_enabled = enabled

    def start_live_playback(self) -> None:
        """Prepare and attach live playback while retaining the shared stream."""
        playback = self._playback
        with self._lifecycle_lock:
            if not self.open:
                self.open_audio()
            with self._state_lock:
                self._live_playback_enabled = True
                saved_active = self._saved_status in {
                    SavedPlaybackStatus.PLAYING,
                    SavedPlaybackStatus.PAUSED,
                }
                attached = self._live_playback_attached
            if saved_active:
                self._emit_state()
                return
            if not attached:
                self._router.attach(playback)
                _set_playback_muted(playback, False)
                with self._state_lock:
                    self._live_playback_attached = True
            self._emit_state()

    def toggle_live_playback(self) -> None:
        playback = self._playback
        with self._lifecycle_lock:
            if not self.open:
                self.open_audio()
            with self._state_lock:
                enabled = not self._live_playback_enabled
                self._live_playback_enabled = enabled
                saved_active = self._saved_status in {
                    SavedPlaybackStatus.PLAYING,
                    SavedPlaybackStatus.PAUSED,
                }
                attached = self._live_playback_attached
            if saved_active:
                self._emit_state()
                return
            if enabled and not attached:
                self._router.attach(playback)
                _set_playback_muted(playback, False)
                with self._state_lock:
                    self._live_playback_attached = True
            elif not enabled and attached:
                self._router.detach(playback, stop=False)
                _set_playback_muted(playback, True)
                with self._state_lock:
                    self._live_playback_attached = False
            self._emit_state()

    def refresh_recordings(self) -> tuple[RecordingEntry, ...]:
        with self._state_lock:
            active_path = (
                self._output_path
                if self._status
                in {
                    AudioSessionStatus.STARTING,
                    AudioSessionStatus.RECORDING,
                    AudioSessionStatus.STOPPING,
                }
                else None
            )
        candidates: list[tuple[int, str, Path]] = []
        for path in self.path_policy.library_paths():
            if active_path is not None and path == active_path:
                continue
            try:
                modified_ns = path.stat().st_mtime_ns
            except OSError:
                continue
            candidates.append((modified_ns, path.name, path))
        candidates.sort(reverse=True)

        discovered: list[RecordingEntry] = []
        for _modified_ns, _name, path in candidates:
            entry = self._read_recording(path)
            if entry is not None:
                discovered.append(entry)
            if len(discovered) >= self._history_limit:
                break
        entries = tuple(discovered)
        with self._state_lock:
            self._recordings = entries
        return entries

    def play_recording(self, path: Path) -> None:
        playback = self._playback
        requested = path.expanduser().resolve()
        entry = next(
            (item for item in self.refresh_recordings() if item.path.resolve() == requested),
            None,
        )
        if entry is None:
            raise ValueError(f"Recording is unavailable or incompatible: {path}")
        self.stop_saved_playback()
        with self._lifecycle_lock:
            if not self.open:
                self.open_audio()
            with self._state_lock:
                attached = self._live_playback_attached
            if attached:
                self._router.detach(playback)
                with self._state_lock:
                    self._live_playback_attached = False
            _set_playback_muted(playback, False)
            playback.start()
            self._saved_stop.clear()
            self._saved_pause.clear()
            thread = threading.Thread(
                target=self._run_saved_playback,
                args=(entry.path,),
                name="sds200-tui-saved-audio",
                daemon=True,
            )
            with self._state_lock:
                self._saved_status = SavedPlaybackStatus.PLAYING
                self._saved_path = entry.path
                self._saved_error = None
                self._saved_thread = thread
            thread.start()
            self._emit_state()

    def toggle_saved_playback_pause(self) -> None:
        with self._state_lock:
            status = self._saved_status
            if status is SavedPlaybackStatus.PLAYING:
                self._saved_pause.set()
                self._saved_status = SavedPlaybackStatus.PAUSED
            elif status is SavedPlaybackStatus.PAUSED:
                self._saved_pause.clear()
                self._saved_status = SavedPlaybackStatus.PLAYING
            else:
                raise RuntimeError("No saved recording is currently playing")
        self._emit_state()

    def stop_saved_playback(self) -> None:
        with self._state_lock:
            thread = self._saved_thread
        if thread is None:
            return
        self._saved_stop.set()
        self._saved_pause.clear()
        if thread is not threading.current_thread():
            thread.join(timeout=3.0)
        if thread.is_alive():
            raise RuntimeError("Timed out while stopping saved-recording playback")

    def close(self) -> None:
        """Finalize recording and stop saved, live, and network audio."""
        with self._lifecycle_lock:
            self.stop_saved_playback()
            self.stop()
            with self._state_lock:
                if not self._open:
                    return
                self._open = False
            failure: BaseException | None = None
            try:
                self._fanout.stop()
            except BaseException as error:
                failure = error
            if self._playback.running:
                try:
                    self._playback.stop()
                except BaseException as error:
                    if failure is None:
                        failure = error
            with self._state_lock:
                self._live_playback_attached = False
            self._emit_state()
            if failure is not None:
                raise failure

    def __enter__(self) -> Self:
        self.open_audio()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _run_saved_playback(self, path: Path) -> None:
        failure: BaseException | None = None
        playback = self._playback
        if isinstance(playback, SoundDevicePlaybackSink):
            buffer_seconds = playback.buffer_ms / 1000
            chunk_frames = max(
                1,
                min(
                    _SAVED_PLAYBACK_CHUNK_FRAMES,
                    int(PCMU_SAMPLE_RATE * buffer_seconds / 4),
                ),
            )
            queue_limit = max(
                0.0,
                buffer_seconds - chunk_frames / PCMU_SAMPLE_RATE,
            )
        else:
            chunk_frames = _SAVED_PLAYBACK_CHUNK_FRAMES
            queue_limit = 0.15
        try:
            with wave.open(str(path), "rb") as recording:
                self._validate_wave(recording)
                while not self._saved_stop.is_set():
                    while self._saved_pause.is_set() and not self._saved_stop.is_set():
                        sleep(0.02)
                    if self._saved_stop.is_set():
                        break
                    queue_wait_started = monotonic()
                    while (
                        playback.statistics.queued_seconds > queue_limit
                        and not self._saved_stop.is_set()
                    ):
                        if monotonic() - queue_wait_started >= 2.0:
                            raise RuntimeError(
                                "Saved-recording playback output stopped draining"
                            )
                        sleep(0.01)
                    data = recording.readframes(chunk_frames)
                    if not data:
                        break
                    playback.submit_pcm(data)
                drain_deadline = monotonic() + 2.0
                while (
                    playback.statistics.queued_bytes > 0
                    and not self._saved_stop.is_set()
                    and monotonic() < drain_deadline
                ):
                    sleep(0.01)
        except BaseException as error:
            failure = error
        finally:
            with suppress(Exception):
                playback.stop()
            with self._state_lock:
                self._saved_thread = None
                self._saved_pause.clear()
                self._saved_status = (
                    SavedPlaybackStatus.FAILED
                    if failure is not None
                    else SavedPlaybackStatus.STOPPED
                )
                self._saved_error = (
                    _error_message(failure) if failure is not None else None
                )
                resume_live = self._open and self._live_playback_enabled
            if resume_live:
                try:
                    self._router.attach(playback)
                    _set_playback_muted(playback, False)
                except BaseException as error:
                    with self._state_lock:
                        self._saved_status = SavedPlaybackStatus.FAILED
                        self._saved_error = _error_message(error)
                else:
                    with self._state_lock:
                        self._live_playback_attached = True
            self._emit_state()

    def _read_recording(self, path: Path) -> RecordingEntry | None:
        try:
            with wave.open(str(path), "rb") as recording:
                self._validate_wave(recording)
                frames = recording.getnframes()
            statistics = path.stat()
        except (OSError, EOFError, wave.Error):
            return None
        return RecordingEntry(
            path=path,
            recorded_at=datetime.fromtimestamp(statistics.st_mtime).astimezone(),
            duration_seconds=frames / PCMU_SAMPLE_RATE,
            size_bytes=statistics.st_size,
            frames=frames,
            modified_ns=statistics.st_mtime_ns,
        )

    @staticmethod
    def _validate_wave(recording: wave.Wave_read) -> None:
        if (
            recording.getnchannels() != PCM_CHANNELS
            or recording.getsampwidth() != PCM_SAMPLE_WIDTH
            or recording.getframerate() != PCMU_SAMPLE_RATE
            or recording.getcomptype() != "NONE"
        ):
            raise wave.Error(
                "Saved playback requires 8 kHz mono signed 16-bit PCM WAV audio"
            )

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
