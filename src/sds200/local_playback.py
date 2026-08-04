from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import BinaryIO, Literal, Protocol, cast, runtime_checkable

from .audio_recording import (
    PCM_CHANNELS,
    PCM_SAMPLE_WIDTH,
    PCMU_SAMPLE_RATE,
)
from .exceptions import AudioOutputError

_PCM_BYTES_PER_SECOND = PCMU_SAMPLE_RATE * PCM_CHANNELS * PCM_SAMPLE_WIDTH

CommandPlaybackState = Literal[
    "idle",
    "running",
    "stopping",
    "stopped",
    "failed",
]


def _validate_name(value: str) -> None:
    if not value or value.strip() != value:
        raise ValueError("Playback adapter name must not be empty or padded.")
    if "\n" in value or "\r" in value:
        raise ValueError("Playback adapter name must not contain line breaks.")


@dataclass(frozen=True, slots=True)
class CommandPlaybackConfig:
    """Immutable command and lifecycle settings for one playback process."""

    name: str
    command: tuple[str, ...]
    chunk_ms: int = 20
    stop_timeout: float = 2.0
    diagnostic_limit: int = 8192

    def __post_init__(self) -> None:
        _validate_name(self.name)

        command = tuple(self.command)
        if not command:
            raise ValueError("Playback command must not be empty.")
        for index, argument in enumerate(command):
            if not isinstance(argument, str):
                raise TypeError("Playback command arguments must be strings.")
            if "\x00" in argument:
                raise ValueError(
                    "Playback command arguments must not contain NUL bytes."
                )
            if index == 0 and not argument:
                raise ValueError(
                    "Playback command executable must not be empty."
                )

        if self.chunk_ms <= 0:
            raise ValueError(
                "Playback chunk size must be greater than zero milliseconds."
            )
        if self.stop_timeout <= 0:
            raise ValueError(
                "Playback stop timeout must be greater than zero."
            )
        if self.diagnostic_limit <= 0:
            raise ValueError(
                "Playback diagnostic limit must be greater than zero."
            )

        object.__setattr__(self, "command", command)

    @property
    def executable(self) -> str:
        return Path(self.command[0]).name

    @property
    def chunk_bytes(self) -> int:
        size = max(
            PCM_SAMPLE_WIDTH,
            _PCM_BYTES_PER_SECOND * self.chunk_ms // 1000,
        )
        return size - size % PCM_SAMPLE_WIDTH


@runtime_checkable
class CommandPlaybackProcess(Protocol):
    """Pipe-backed process contract required by command playback adapters."""

    @property
    def stdin(self) -> BinaryIO: ...

    @property
    def stderr(self) -> BinaryIO: ...

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


CommandPlaybackProcessFactory = Callable[
    [tuple[str, ...]],
    CommandPlaybackProcess,
]


@dataclass(frozen=True, slots=True)
class CommandPlaybackSnapshot:
    """Immutable lifecycle state for one command playback adapter."""

    name: str
    command: tuple[str, ...]
    state: CommandPlaybackState
    running: bool
    interrupted: bool
    returncode: int | None
    diagnostic: str
    last_error: str | None


class CommandPlaybackAdapter:
    """Feed fixed-format PCM to one bounded-lifecycle playback process."""

    def __init__(
        self,
        config: CommandPlaybackConfig,
        *,
        process_factory: CommandPlaybackProcessFactory | None = None,
    ) -> None:
        self.config = config
        self._process_factory = (
            start_command_playback_process
            if process_factory is None
            else process_factory
        )
        self._lifecycle_lock = threading.RLock()
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._process: CommandPlaybackProcess | None = None
        self._writer_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._state: CommandPlaybackState = "idle"
        self._interrupted = False
        self._returncode: int | None = None
        self._diagnostic = bytearray()
        self._last_error: str | None = None

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def running(self) -> bool:
        with self._lock:
            process = self._process
            thread = self._writer_thread
            state = self._state
        return (
            state == "running"
            and process is not None
            and process.poll() is None
            and thread is not None
            and thread.is_alive()
        )

    @property
    def last_error(self) -> str | None:
        with self._lock:
            return self._last_error

    def snapshot(self) -> CommandPlaybackSnapshot:
        with self._lock:
            process = self._process
            returncode = (
                self._returncode
                if process is None
                else process.poll()
            )
            state = self._state
            interrupted = self._interrupted
            diagnostic = bytes(self._diagnostic).decode(
                "utf-8",
                errors="replace",
            ).strip()
            last_error = self._last_error

        return CommandPlaybackSnapshot(
            name=self.name,
            command=self.config.command,
            state=state,
            running=self.running,
            interrupted=interrupted,
            returncode=returncode,
            diagnostic=diagnostic,
            last_error=last_error,
        )

    def start(
        self,
        pcm_reader: Callable[[int], bytes],
        status_reporter: Callable[[bool], None],
    ) -> None:
        with self._lifecycle_lock:
            with self._lock:
                if self._process is not None:
                    if self.running:
                        return
                    raise RuntimeError(
                        "Playback adapter must be closed before restart."
                    )

            process = self._process_factory(self.config.command)
            if not isinstance(process, CommandPlaybackProcess):
                raise TypeError(
                    "Playback process factories must return "
                    "CommandPlaybackProcess-compatible objects."
                )

            returncode = process.poll()
            if returncode is not None:
                diagnostic = _read_available_diagnostic(
                    process.stderr,
                    self.config.diagnostic_limit,
                )
                _close_binary_stream(process.stdin)
                _close_binary_stream(process.stderr)
                detail = f": {diagnostic}" if diagnostic else ""
                raise AudioOutputError(
                    f"{self.name} exited during startup with status "
                    f"{returncode}{detail}"
                )

            self._stop_event.clear()
            with self._lock:
                self._process = process
                self._state = "running"
                self._interrupted = False
                self._returncode = None
                self._diagnostic.clear()
                self._last_error = None

                stderr_thread = threading.Thread(
                    target=self._drain_stderr,
                    args=(process,),
                    name=f"sds200-{self.name}-stderr",
                    daemon=True,
                )
                writer_thread = threading.Thread(
                    target=self._write_pcm,
                    args=(process, pcm_reader, status_reporter),
                    name=f"sds200-{self.name}-writer",
                    daemon=True,
                )
                self._stderr_thread = stderr_thread
                self._writer_thread = writer_thread

            stderr_thread.start()
            writer_thread.start()

    def interrupt(self) -> None:
        with self._lifecycle_lock:
            with self._lock:
                process = self._process
                if process is None or self._interrupted:
                    return
                self._interrupted = True
                if self._state != "failed":
                    self._state = "stopping"
                self._stop_event.set()

            _close_binary_stream(process.stdin)
            with suppress(OSError):
                process.terminate()

    def close(self) -> None:
        with self._lifecycle_lock:
            with self._lock:
                process = self._process
                state = self._state
            if process is None:
                return

            returncode = process.poll()
            if returncode is not None and state == "running":
                self._record_failure(
                    f"{self.name} exited unexpectedly with status {returncode}."
                )

            self.interrupt()
            deadline = monotonic() + self.config.stop_timeout

            with self._lock:
                writer_thread = self._writer_thread
                stderr_thread = self._stderr_thread

            if writer_thread is not None:
                writer_thread.join(
                    timeout=_remaining_timeout(deadline) / 3.0
                )

            cleanup_error: str | None = None
            try:
                returncode = _terminate_process(
                    process,
                    deadline=deadline,
                    name=self.name,
                )
            except AudioOutputError as error:
                cleanup_error = str(error)
                returncode = process.poll()

            _close_binary_stream(process.stderr)
            if stderr_thread is not None:
                stderr_thread.join(timeout=_remaining_timeout(deadline))

            if writer_thread is not None and writer_thread.is_alive():
                cleanup_error = (
                    cleanup_error
                    or f"{self.name} playback writer did not stop."
                )
            if stderr_thread is not None and stderr_thread.is_alive():
                cleanup_error = (
                    cleanup_error
                    or f"{self.name} diagnostic reader did not stop."
                )

            with self._lock:
                self._process = None
                self._writer_thread = None
                self._stderr_thread = None
                self._returncode = returncode
                last_error = self._last_error
                diagnostic = bytes(self._diagnostic).decode(
                    "utf-8",
                    errors="replace",
                ).strip()
                self._state = (
                    "failed"
                    if last_error is not None or cleanup_error is not None
                    else "stopped"
                )

            if last_error is not None:
                detail = f" Diagnostic: {diagnostic}" if diagnostic else ""
                raise AudioOutputError(f"{last_error}{detail}")
            if cleanup_error is not None:
                raise AudioOutputError(cleanup_error)

    def _write_pcm(
        self,
        process: CommandPlaybackProcess,
        pcm_reader: Callable[[int], bytes],
        status_reporter: Callable[[bool], None],
    ) -> None:
        period = self.config.chunk_ms / 1000
        next_write = monotonic()

        try:
            while not self._stop_event.is_set():
                delay = next_write - monotonic()
                if delay > 0 and self._stop_event.wait(delay):
                    return
                if delay < -period:
                    next_write = monotonic()

                returncode = process.poll()
                if returncode is not None:
                    raise AudioOutputError(
                        f"{self.name} exited unexpectedly with status "
                        f"{returncode}."
                    )

                pcm = pcm_reader(self.config.chunk_bytes)
                if (
                    len(pcm) != self.config.chunk_bytes
                    or len(pcm) % PCM_SAMPLE_WIDTH
                ):
                    raise AudioOutputError(
                        f"{self.name} received an invalid PCM block."
                    )

                _write_all(process.stdin, pcm)
                process.stdin.flush()
                next_write += period
        except BaseException as error:
            if not self._stop_event.is_set():
                self._record_failure(_error_message(error))
                with suppress(Exception):
                    status_reporter(True)
        finally:
            _close_binary_stream(process.stdin)

    def _drain_stderr(
        self,
        process: CommandPlaybackProcess,
    ) -> None:
        try:
            while True:
                chunk = process.stderr.read(1024)
                if not chunk:
                    return
                with self._lock:
                    self._diagnostic.extend(chunk)
                    excess = (
                        len(self._diagnostic)
                        - self.config.diagnostic_limit
                    )
                    if excess > 0:
                        del self._diagnostic[:excess]
        except (OSError, ValueError):
            return

    def _record_failure(self, message: str) -> None:
        with self._lock:
            if self._last_error is None:
                self._last_error = message
            self._state = "failed"


class _PopenCommandPlaybackProcess:
    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        stdin = process.stdin
        stderr = process.stderr
        if stdin is None or stderr is None:
            process.kill()
            raise AudioOutputError(
                "Playback process did not expose the required pipe streams."
            )
        self._process = process
        self._stdin = cast(BinaryIO, stdin)
        self._stderr = cast(BinaryIO, stderr)

    @property
    def stdin(self) -> BinaryIO:
        return self._stdin

    @property
    def stderr(self) -> BinaryIO:
        return self._stderr

    def poll(self) -> int | None:
        return self._process.poll()

    def wait(self, timeout: float | None = None) -> int:
        return self._process.wait(timeout=timeout)

    def terminate(self) -> None:
        self._process.terminate()

    def kill(self) -> None:
        self._process.kill()


def start_command_playback_process(
    command: tuple[str, ...],
) -> CommandPlaybackProcess:
    """Start one playback command with unbuffered standard pipes."""

    if not command or not command[0]:
        raise ValueError(
            "Playback process command must include an executable."
        )

    try:
        process: subprocess.Popen[bytes] = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
    except FileNotFoundError as error:
        executable = Path(command[0]).name
        raise AudioOutputError(
            f"Playback executable {executable!r} was not found."
        ) from error
    except OSError as error:
        raise AudioOutputError(
            f"Unable to start playback process: {error}"
        ) from error

    return _PopenCommandPlaybackProcess(process)


def _write_all(stream: BinaryIO, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        written = stream.write(data[offset:])
        if written is None or written <= 0:
            raise AudioOutputError(
                "Playback process stopped accepting PCM."
            )
        offset += written


def _read_available_diagnostic(
    stream: BinaryIO,
    limit: int,
) -> str:
    try:
        return stream.read(limit).decode(
            "utf-8",
            errors="replace",
        ).strip()
    except (OSError, ValueError):
        return ""


def _terminate_process(
    process: CommandPlaybackProcess,
    *,
    deadline: float,
    name: str,
) -> int:
    try:
        return process.wait(timeout=_cleanup_stage_timeout(deadline))
    except subprocess.TimeoutExpired:
        with suppress(OSError):
            process.terminate()

    try:
        return process.wait(timeout=_cleanup_stage_timeout(deadline))
    except subprocess.TimeoutExpired:
        with suppress(OSError):
            process.kill()

    try:
        return process.wait(timeout=_cleanup_stage_timeout(deadline))
    except subprocess.TimeoutExpired as error:
        raise AudioOutputError(f"{name} did not stop.") from error


def _cleanup_stage_timeout(deadline: float) -> float:
    return _remaining_timeout(deadline) / 2.0


def _remaining_timeout(deadline: float) -> float:
    return max(0.0, deadline - monotonic())


def _close_binary_stream(stream: BinaryIO) -> None:
    with suppress(OSError, ValueError):
        stream.close()


def _error_message(error: BaseException) -> str:
    detail = str(error).strip()
    return detail or error.__class__.__name__
