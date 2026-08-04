from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from importlib import import_module
from typing import Protocol, Self, cast, runtime_checkable

from .audio import AudioChunk, AudioStream
from .audio_recording import (
    PCM_CHANNELS,
    PCM_SAMPLE_WIDTH,
    PCMU_SAMPLE_RATE,
    PcmuWavRecorder,
    decode_mulaw,
)
from .exceptions import AudioOutputError

logger = logging.getLogger(__name__)
_PCM_BYTES_PER_SECOND = PCMU_SAMPLE_RATE * PCM_CHANNELS * PCM_SAMPLE_WIDTH


@dataclass(frozen=True, slots=True)
class PcmSinkStatistics:
    """Immutable counters for one decoded-PCM destination."""

    bytes_submitted: int = 0
    bytes_written: int = 0
    bytes_dropped: int = 0
    queued_bytes: int = 0
    underflows: int = 0
    overflows: int = 0
    callback_statuses: int = 0

    @property
    def queued_seconds(self) -> float:
        return self.queued_bytes / _PCM_BYTES_PER_SECOND


@runtime_checkable
class PcmSink(Protocol):
    """Nonblocking destination for 8 kHz mono signed 16-bit PCM."""

    @property
    def name(self) -> str: ...

    @property
    def running(self) -> bool: ...

    @property
    def statistics(self) -> PcmSinkStatistics: ...

    def start(self) -> None: ...

    def submit_pcm(self, data: bytes) -> None: ...

    def stop(self) -> None: ...


@runtime_checkable
class MuteablePcmSink(PcmSink, Protocol):
    """PCM sink that can stay prepared while intentional silence is emitted."""

    @property
    def muted(self) -> bool: ...

    def set_muted(self, muted: bool) -> None: ...


@dataclass(frozen=True, slots=True)
class AudioFanoutSnapshot:
    """Current state of one transport-independent PCM fanout session."""

    endpoint: str
    running: bool
    packets: int
    samples: int
    sinks: tuple[tuple[str, PcmSinkStatistics], ...]

    @property
    def audio_duration_seconds(self) -> float:
        return self.samples / PCMU_SAMPLE_RATE


class AudioFanoutSession:
    """Decode one PCMU stream once and fan PCM out to independent sinks."""

    def __init__(self, stream: AudioStream, sinks: Iterable[PcmSink]) -> None:
        self.stream = stream
        self.sinks = tuple(sinks)
        if not self.sinks:
            raise ValueError("Audio fanout requires at least one PCM sink")
        self._lifecycle_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._unsubscribe: Callable[[], None] | None = None
        self._started = False
        self._stopped = False
        self._packets = 0
        self._samples = 0

    @property
    def running(self) -> bool:
        with self._state_lock:
            return self._started and not self._stopped and self.stream.running

    def snapshot(self) -> AudioFanoutSnapshot:
        with self._state_lock:
            packets = self._packets
            samples = self._samples
        return AudioFanoutSnapshot(
            endpoint=self.stream.endpoint,
            running=self.running,
            packets=packets,
            samples=samples,
            sinks=tuple((sink.name, sink.statistics) for sink in self.sinks),
        )

    def start(self) -> None:
        with self._lifecycle_lock:
            with self._state_lock:
                if self._started:
                    raise RuntimeError("Audio fanout sessions can only be started once.")
                self._started = True

            started_sinks: list[PcmSink] = []
            unsubscribe: Callable[[], None] | None = None
            try:
                for sink in self.sinks:
                    sink.start()
                    started_sinks.append(sink)
                unsubscribe = self.stream.on_chunk(self._receive_chunk)
                self.stream.start()
            except BaseException:
                if unsubscribe is not None:
                    unsubscribe()
                try:
                    self.stream.stop()
                except Exception:
                    logger.exception("Audio stream cleanup failed after start error")
                for sink in reversed(started_sinks):
                    try:
                        sink.stop()
                    except Exception:
                        logger.exception("Audio sink cleanup failed sink=%s", sink.name)
                with self._state_lock:
                    self._stopped = True
                raise

            with self._state_lock:
                self._unsubscribe = unsubscribe
            logger.info(
                "audio fanout started endpoint=%s sinks=%s",
                self.stream.endpoint,
                ",".join(sink.name for sink in self.sinks),
            )

    def stop(self) -> None:
        with self._lifecycle_lock:
            with self._state_lock:
                if not self._started or self._stopped:
                    return
                self._stopped = True
                unsubscribe, self._unsubscribe = self._unsubscribe, None

            failures: list[BaseException] = []
            try:
                self.stream.stop()
            except BaseException as error:
                failures.append(error)
            if unsubscribe is not None:
                try:
                    unsubscribe()
                except BaseException as error:
                    failures.append(error)
            for sink in reversed(self.sinks):
                try:
                    sink.stop()
                except BaseException as error:
                    failures.append(error)

            snapshot = self.snapshot()
            logger.info(
                "audio fanout stopped endpoint=%s packets=%d samples=%d",
                snapshot.endpoint,
                snapshot.packets,
                snapshot.samples,
            )
            if failures:
                raise failures[0]

    def close(self) -> None:
        self.stop()

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()

    def _receive_chunk(self, chunk: AudioChunk) -> None:
        if not chunk.data:
            return
        pcm = decode_mulaw(chunk.data)
        with self._state_lock:
            self._packets += 1
            self._samples += len(chunk.data)
        for sink in self.sinks:
            try:
                sink.submit_pcm(pcm)
            except Exception:
                logger.exception("Audio sink rejected PCM sink=%s", sink.name)

class PcmSinkRouter:
    """Dynamically attach nonblocking PCM sinks behind one fanout destination."""

    def __init__(self, *, name: str = "pcm-sink-router") -> None:
        if not name or name.strip() != name:
            raise ValueError("PCM sink router name must not be empty or padded")
        self._name = name
        self._lifecycle_lock = threading.RLock()
        self._submit_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._sinks: list[PcmSink] = []
        self._running = False
        self._bytes_submitted = 0

    @property
    def name(self) -> str:
        return self._name

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
                    logger.exception(
                        "PCM sink router subscriber rejected PCM sink=%s",
                        sink.name,
                    )

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


class _PcmBuffer:
    def __init__(self, capacity_bytes: int) -> None:
        if capacity_bytes < PCM_SAMPLE_WIDTH:
            raise ValueError("PCM buffer must hold at least one sample")
        self.capacity_bytes = capacity_bytes - capacity_bytes % PCM_SAMPLE_WIDTH
        self._data = bytearray()
        self._lock = threading.RLock()

    @property
    def queued_bytes(self) -> int:
        with self._lock:
            return len(self._data)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def push(self, data: bytes) -> int:
        if len(data) % PCM_SAMPLE_WIDTH:
            raise ValueError("PCM data must contain complete 16-bit samples")
        if not data:
            return 0
        with self._lock:
            total = len(self._data) + len(data)
            dropped = max(0, total - self.capacity_bytes)
            if dropped:
                drop_from_existing = min(dropped, len(self._data))
                del self._data[:drop_from_existing]
                drop_from_new = dropped - drop_from_existing
                if drop_from_new:
                    data = data[drop_from_new:]
            self._data.extend(data)
            return dropped

    def pop(self, size: int) -> bytes:
        if size < 0:
            raise ValueError("PCM read size must not be negative")
        size -= size % PCM_SAMPLE_WIDTH
        with self._lock:
            available = min(size, len(self._data))
            result = bytes(self._data[:available])
            del self._data[:available]
            return result


class _WritableBuffer(Protocol):
    def __setitem__(self, key: slice, value: bytes) -> None: ...


class _RawOutputStream(Protocol):
    def start(self) -> object: ...

    def stop(self) -> object: ...

    def close(self) -> object: ...


@runtime_checkable
class LocalPlaybackAdapter(Protocol):
    """Backend-specific local PCM consumer used by a buffered playback sink."""

    @property
    def name(self) -> str: ...

    @property
    def running(self) -> bool: ...

    def start(
        self,
        pcm_reader: Callable[[int], bytes],
        status_reporter: Callable[[bool], None],
    ) -> None: ...

    def interrupt(self) -> None:
        """Promptly interrupt backend playback from another thread."""
        ...

    def close(self) -> None: ...


LocalPlaybackAdapterFactory = Callable[[], LocalPlaybackAdapter]


class _SoundDeviceDefaults(Protocol):
    device: object


class _SoundDeviceModule(Protocol):
    default: _SoundDeviceDefaults

    def RawOutputStream(
        self,
        *,
        samplerate: int,
        channels: int,
        dtype: str,
        device: str | int | None,
        callback: Callable[[object, int, object, object], None],
    ) -> _RawOutputStream: ...

    def get_portaudio_version(self) -> tuple[int, str]: ...

    def query_hostapis(self) -> object: ...

    def query_devices(self) -> object: ...


@dataclass(frozen=True, slots=True)
class AudioHostApiInfo:
    """One local PortAudio host API."""

    index: int
    name: str
    default_output_device: int | None


@dataclass(frozen=True, slots=True)
class AudioOutputDeviceInfo:
    """One local output-capable audio device."""

    index: int
    name: str
    host_api_index: int
    host_api_name: str
    max_output_channels: int
    default_samplerate: float
    default: bool


@dataclass(frozen=True, slots=True)
class AudioBackendInfo:
    """Immutable local-audio backend and output-device inventory."""

    backend: str
    version: str
    default_output_device: int | None
    host_apis: tuple[AudioHostApiInfo, ...]
    output_devices: tuple[AudioOutputDeviceInfo, ...]


def _load_sounddevice(
    module_loader: Callable[[str], object] = import_module,
) -> _SoundDeviceModule:
    try:
        return cast(_SoundDeviceModule, module_loader("sounddevice"))
    except ModuleNotFoundError as error:
        raise AudioOutputError(
            "Live playback support is not installed; install it with: "
            'python -m pip install "sds200[playback]"'
        ) from error
    except OSError as error:
        detail = str(error)
        if "portaudio" in detail.casefold():
            raise AudioOutputError(
                "PortAudio is required for local playback but its shared library "
                "was not found. On Debian or Raspberry Pi OS, install it with: "
                "sudo apt install libportaudio2"
            ) from error
        raise AudioOutputError(
            f"Could not load local audio playback support: {detail}"
        ) from error


def _mapping_entries(value: object, *, label: str) -> tuple[Mapping[str, object], ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise AudioOutputError(f"{label} returned an unexpected value")
    entries: list[Mapping[str, object]] = []
    for entry in value:
        if not isinstance(entry, Mapping):
            raise AudioOutputError(f"{label} returned an unexpected entry")
        entries.append(cast(Mapping[str, object], entry))
    return tuple(entries)


def _required_text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise AudioOutputError(f"{label} is missing")
    return value


def _required_integer(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AudioOutputError(f"{label} is not an integer")
    return value


def _optional_device_index(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _default_output_device(value: object) -> int | None:
    try:
        output = cast(Sequence[object], value)[1]
    except (IndexError, TypeError):
        return _optional_device_index(value)
    return _optional_device_index(output)


def _required_number(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AudioOutputError(f"{label} is not numeric")
    return float(value)


def inspect_audio_backend(
    *,
    module_loader: Callable[[str], object] = import_module,
) -> AudioBackendInfo:
    """Return PortAudio host APIs and output devices without opening a stream."""

    module = _load_sounddevice(module_loader)
    try:
        _, version = module.get_portaudio_version()
        host_entries = _mapping_entries(
            module.query_hostapis(),
            label="PortAudio host API query",
        )
        device_entries = _mapping_entries(
            module.query_devices(),
            label="PortAudio device query",
        )
        default_output = _default_output_device(module.default.device)

        host_apis: list[AudioHostApiInfo] = []
        host_names: dict[int, str] = {}
        for index, entry in enumerate(host_entries):
            name = _required_text(
                entry.get("name"),
                label=f"PortAudio host API {index} name",
            )
            host_names[index] = name
            host_apis.append(
                AudioHostApiInfo(
                    index=index,
                    name=name,
                    default_output_device=_optional_device_index(
                        entry.get("default_output_device")
                    ),
                )
            )

        output_devices: list[AudioOutputDeviceInfo] = []
        for fallback_index, entry in enumerate(device_entries):
            max_output_channels = _required_integer(
                entry.get("max_output_channels"),
                label=f"PortAudio device {fallback_index} output channels",
            )
            if max_output_channels <= 0:
                continue
            index = _required_integer(
                entry.get("index", fallback_index),
                label=f"PortAudio device {fallback_index} index",
            )
            host_api_index = _required_integer(
                entry.get("hostapi"),
                label=f"PortAudio device {index} host API",
            )
            output_devices.append(
                AudioOutputDeviceInfo(
                    index=index,
                    name=_required_text(
                        entry.get("name"),
                        label=f"PortAudio device {index} name",
                    ),
                    host_api_index=host_api_index,
                    host_api_name=host_names.get(host_api_index, "unknown"),
                    max_output_channels=max_output_channels,
                    default_samplerate=_required_number(
                        entry.get("default_samplerate"),
                        label=f"PortAudio device {index} default sample rate",
                    ),
                    default=index == default_output,
                )
            )
    except AudioOutputError:
        raise
    except Exception as error:
        raise AudioOutputError(
            f"Could not inspect local audio devices: {error}"
        ) from error

    return AudioBackendInfo(
        backend="PortAudio",
        version=version,
        default_output_device=default_output,
        host_apis=tuple(host_apis),
        output_devices=tuple(output_devices),
    )


class BufferedPlaybackSink:
    """Bounded newest-audio PCM sink backed by one local playback adapter."""

    def __init__(
        self,
        *,
        name: str,
        adapter_factory: LocalPlaybackAdapterFactory,
        buffer_ms: int = 250,
    ) -> None:
        if not name or name.strip() != name:
            raise ValueError("Playback sink name must not be empty or padded")
        if buffer_ms <= 0:
            raise ValueError("Playback buffer must be greater than zero milliseconds")

        capacity = max(
            PCM_SAMPLE_WIDTH,
            _PCM_BYTES_PER_SECOND * buffer_ms // 1000,
        )
        self._name = name
        self.buffer_ms = buffer_ms
        self._adapter_factory = adapter_factory
        self._buffer = _PcmBuffer(capacity)
        self._lifecycle_lock = threading.RLock()
        self._lock = threading.RLock()
        self._adapter: LocalPlaybackAdapter | None = None
        self._bytes_submitted = 0
        self._bytes_written = 0
        self._bytes_dropped = 0
        self._underflows = 0
        self._overflows = 0
        self._callback_statuses = 0
        self._muted = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def running(self) -> bool:
        with self._lock:
            adapter = self._adapter
        return adapter is not None and adapter.running

    @property
    def muted(self) -> bool:
        with self._lock:
            return self._muted

    @property
    def statistics(self) -> PcmSinkStatistics:
        with self._lock:
            return PcmSinkStatistics(
                bytes_submitted=self._bytes_submitted,
                bytes_written=self._bytes_written,
                bytes_dropped=self._bytes_dropped,
                queued_bytes=self._buffer.queued_bytes,
                underflows=self._underflows,
                overflows=self._overflows,
                callback_statuses=self._callback_statuses,
            )

    def start(self) -> None:
        with self._lifecycle_lock:
            with self._lock:
                if self._adapter is not None:
                    return

            adapter = self._adapter_factory()
            if not isinstance(adapter, LocalPlaybackAdapter):
                raise TypeError(
                    "Local playback adapter factories must return "
                    "LocalPlaybackAdapter-compatible objects."
                )

            try:
                adapter.start(self._read_pcm, self._report_status)
            except BaseException:
                try:
                    adapter.interrupt()
                except Exception:
                    logger.exception(
                        "Local playback adapter interrupt failed after start error "
                        "adapter=%s",
                        adapter.name,
                    )
                try:
                    adapter.close()
                except Exception:
                    logger.exception(
                        "Local playback adapter cleanup failed after start error "
                        "adapter=%s",
                        adapter.name,
                    )
                raise

            with self._lock:
                self._adapter = adapter

        logger.info(
            "audio playback started sink=%s adapter=%s",
            self.name,
            adapter.name,
        )

    def set_muted(self, muted: bool) -> None:
        with self._lock:
            self._muted = muted
            if muted:
                self._buffer.clear()

    def submit_pcm(self, data: bytes) -> None:
        with self._lock:
            if self._muted:
                return
            dropped = self._buffer.push(data)
            self._bytes_submitted += len(data)
            self._bytes_dropped += dropped
            if dropped:
                self._overflows += 1

    def interrupt(self) -> None:
        with self._lock:
            adapter = self._adapter
        if adapter is not None:
            adapter.interrupt()

    def stop(self) -> None:
        with self._lifecycle_lock:
            with self._lock:
                adapter, self._adapter = self._adapter, None
            if adapter is None:
                return

            failure: BaseException | None = None
            try:
                adapter.interrupt()
            except BaseException as error:
                failure = error
            try:
                adapter.close()
            except BaseException as error:
                if failure is None:
                    failure = error

            self._buffer.clear()
            logger.info(
                "audio playback stopped sink=%s adapter=%s",
                self.name,
                adapter.name,
            )
            if failure is not None:
                if isinstance(failure, AudioOutputError):
                    raise failure
                raise AudioOutputError(
                    f"Could not close audio output device: {failure}"
                ) from failure

    def _read_pcm(self, size: int) -> bytes:
        if size < 0 or size % PCM_SAMPLE_WIDTH:
            raise ValueError(
                "Playback adapter reads must request complete 16-bit samples"
            )

        with self._lock:
            if self._muted:
                return bytes(size)

            pcm = self._buffer.pop(size)
            missing = size - len(pcm)
            self._bytes_written += len(pcm)
            if missing:
                self._underflows += 1
            return pcm + bytes(missing)

    def _report_status(self, active: bool) -> None:
        if not active:
            return
        with self._lock:
            self._callback_statuses += 1


class SoundDevicePlaybackAdapter:
    """Local playback adapter implemented with sounddevice and PortAudio."""

    def __init__(
        self,
        *,
        device: str | int | None = None,
        module_loader: Callable[[str], object] = import_module,
    ) -> None:
        self.device = device
        self._module_loader = module_loader
        self._lock = threading.RLock()
        self._stream: _RawOutputStream | None = None
        self._interrupted = False

    @property
    def name(self) -> str:
        return (
            "portaudio:default"
            if self.device is None
            else f"portaudio:{self.device}"
        )

    @property
    def running(self) -> bool:
        with self._lock:
            return self._stream is not None and not self._interrupted

    def start(
        self,
        pcm_reader: Callable[[int], bytes],
        status_reporter: Callable[[bool], None],
    ) -> None:
        with self._lock:
            if self._stream is not None:
                return

            module = _load_sounddevice(self._module_loader)

            def playback_callback(
                outdata: object,
                frames: int,
                time_info: object,
                status: object,
            ) -> None:
                del time_info
                requested = frames * PCM_CHANNELS * PCM_SAMPLE_WIDTH
                cast(_WritableBuffer, outdata)[:] = pcm_reader(requested)
                status_reporter(bool(status))

            stream: _RawOutputStream | None = None
            try:
                stream = module.RawOutputStream(
                    samplerate=PCMU_SAMPLE_RATE,
                    channels=PCM_CHANNELS,
                    dtype="int16",
                    device=self.device,
                    callback=playback_callback,
                )
                stream.start()
            except Exception as error:
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        logger.exception(
                            "Audio output cleanup failed after start error"
                        )
                raise AudioOutputError(
                    f"Could not open audio output device: {error}"
                ) from error

            self._stream = stream
            self._interrupted = False

    def interrupt(self) -> None:
        with self._lock:
            stream = self._stream
            if stream is None or self._interrupted:
                return
            self._interrupted = True

        try:
            stream.stop()
        except Exception as error:
            raise AudioOutputError(
                f"Could not interrupt audio output device: {error}"
            ) from error

    def close(self) -> None:
        with self._lock:
            stream, self._stream = self._stream, None
            interrupted = self._interrupted
            self._interrupted = False

        if stream is None:
            return

        failure: BaseException | None = None
        if not interrupted:
            try:
                stream.stop()
            except BaseException as error:
                failure = error
        try:
            stream.close()
        except BaseException as error:
            if failure is None:
                failure = error

        if failure is not None:
            raise AudioOutputError(
                f"Could not close audio output device: {failure}"
            ) from failure


class SoundDevicePlaybackSink(BufferedPlaybackSink):
    """Compatibility sink for sounddevice/PortAudio local playback."""

    def __init__(
        self,
        *,
        device: str | int | None = None,
        buffer_ms: int = 250,
        module_loader: Callable[[str], object] = import_module,
    ) -> None:
        self.device = device
        self._module_loader = module_loader
        super().__init__(
            name=(
                "playback:default"
                if device is None
                else f"playback:{device}"
            ),
            buffer_ms=buffer_ms,
            adapter_factory=lambda: SoundDevicePlaybackAdapter(
                device=device,
                module_loader=module_loader,
            ),
        )


class PcmWavSink:
    """Buffer decoded PCM on the RTP thread and write it from a worker thread."""

    def __init__(
        self,
        recorder: PcmuWavRecorder,
        *,
        buffer_seconds: float = 5.0,
    ) -> None:
        if buffer_seconds <= 0:
            raise ValueError("WAV sink buffer must be greater than zero seconds")
        self.recorder = recorder
        self.buffer_seconds = buffer_seconds
        capacity = max(
            PCM_SAMPLE_WIDTH,
            int(_PCM_BYTES_PER_SECOND * buffer_seconds),
        )
        self._capacity_bytes = capacity - capacity % PCM_SAMPLE_WIDTH
        self._condition = threading.Condition(threading.RLock())
        self._queue: deque[bytes] = deque()
        self._queued_bytes = 0
        self._stopping = False
        self._thread: threading.Thread | None = None
        self._error: BaseException | None = None
        self._bytes_submitted = 0
        self._bytes_written = 0
        self._bytes_dropped = 0
        self._overflows = 0

    @property
    def name(self) -> str:
        return f"wav:{self.recorder.path}"

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def statistics(self) -> PcmSinkStatistics:
        with self._condition:
            return PcmSinkStatistics(
                bytes_submitted=self._bytes_submitted,
                bytes_written=self._bytes_written,
                bytes_dropped=self._bytes_dropped,
                queued_bytes=self._queued_bytes,
                overflows=self._overflows,
            )

    def start(self) -> None:
        with self._condition:
            if self._thread is not None:
                return
            self.recorder.start()
            self._stopping = False
            self._error = None
            thread = threading.Thread(
                target=self._run,
                name="sds200-pcm-wav",
                daemon=True,
            )
            self._thread = thread
            thread.start()

    def submit_pcm(self, data: bytes) -> None:
        if len(data) % PCM_SAMPLE_WIDTH:
            raise ValueError("PCM data must contain complete 16-bit samples")
        if not data:
            return
        with self._condition:
            if self._thread is None or self._stopping:
                raise RuntimeError("WAV sink is not running")
            self._bytes_submitted += len(data)
            dropped = 0
            if len(data) > self._capacity_bytes:
                dropped += len(data) - self._capacity_bytes
                data = data[-self._capacity_bytes :]
            while self._queue and self._queued_bytes + len(data) > self._capacity_bytes:
                removed = self._queue.popleft()
                self._queued_bytes -= len(removed)
                dropped += len(removed)
            if dropped:
                self._bytes_dropped += dropped
                self._overflows += 1
            self._queue.append(data)
            self._queued_bytes += len(data)
            self._condition.notify()

    def stop(self) -> None:
        with self._condition:
            thread = self._thread
            if thread is None:
                return
            self._stopping = True
            self._condition.notify_all()
        thread.join(timeout=5.0)
        if thread.is_alive():
            raise AudioOutputError("Timed out while finalizing the PCM WAV sink")
        with self._condition:
            self._thread = None
            error = self._error
        try:
            self.recorder.close()
        except BaseException as close_error:
            if error is None:
                error = close_error
        if error is not None:
            raise AudioOutputError(f"PCM WAV sink failed: {error}") from error

    def _run(self) -> None:
        try:
            while True:
                with self._condition:
                    while not self._queue and not self._stopping:
                        self._condition.wait()
                    if not self._queue and self._stopping:
                        return
                    data = self._queue.popleft()
                    self._queued_bytes -= len(data)
                self.recorder.write_pcm(data)
                with self._condition:
                    self._bytes_written += len(data)
        except BaseException as error:
            with self._condition:
                self._error = error
                self._stopping = True
                self._queue.clear()
                self._queued_bytes = 0
                self._condition.notify_all()
