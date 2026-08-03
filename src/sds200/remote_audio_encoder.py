from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import BinaryIO, Literal, Protocol, cast, runtime_checkable

from .exceptions import AudioOutputError

AudioEncoderState = Literal["running", "stopping", "stopped"]


def _validate_name(value: str) -> None:
    if not value or value.strip() != value:
        raise ValueError("Audio encoder name must not be empty or padded.")
    if "\n" in value or "\r" in value:
        raise ValueError("Audio encoder name must not contain line breaks.")


@dataclass(frozen=True, slots=True)
class AudioEncoderConfig:
    """Immutable command and lifecycle settings for one encoder process."""

    name: str
    command: tuple[str, ...]
    stop_timeout: float = 2.0
    diagnostic_limit: int = 8192

    def __post_init__(self) -> None:
        _validate_name(self.name)

        command = tuple(self.command)
        if not command:
            raise ValueError("Audio encoder command must not be empty.")
        for index, argument in enumerate(command):
            if not isinstance(argument, str):
                raise TypeError("Audio encoder command arguments must be strings.")
            if "\x00" in argument:
                raise ValueError(
                    "Audio encoder command arguments must not contain NUL bytes."
                )
            if index == 0 and not argument:
                raise ValueError(
                    "Audio encoder command executable must not be empty."
                )

        if self.stop_timeout <= 0:
            raise ValueError(
                "Audio encoder stop timeout must be greater than zero."
            )
        if self.diagnostic_limit <= 0:
            raise ValueError(
                "Audio encoder diagnostic limit must be greater than zero."
            )

        object.__setattr__(self, "command", command)

    @property
    def executable(self) -> str:
        return Path(self.command[0]).name


@runtime_checkable
class AudioEncoderProcess(Protocol):
    """Pipe-backed process contract required by the managed encoder."""

    @property
    def stdin(self) -> BinaryIO: ...

    @property
    def stdout(self) -> BinaryIO: ...

    @property
    def stderr(self) -> BinaryIO: ...

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


AudioEncoderProcessFactory = Callable[
    [tuple[str, ...]],
    AudioEncoderProcess,
]


@dataclass(frozen=True, slots=True)
class AudioEncoderResult:
    """Immutable result of bounded encoder finalization."""

    returncode: int
    interrupted: bool
    diagnostic: str
    exit_reported: bool


@dataclass(frozen=True, slots=True)
class AudioEncoderSnapshot:
    """Immutable lifecycle state for one managed encoder process."""

    name: str
    command: tuple[str, ...]
    state: AudioEncoderState
    running: bool
    interrupted: bool
    returncode: int | None


class ManagedAudioEncoder:
    """Own one pipe-backed encoder process with deterministic shutdown."""

    def __init__(
        self,
        config: AudioEncoderConfig,
        *,
        process_factory: AudioEncoderProcessFactory | None = None,
    ) -> None:
        self.config = config
        factory = (
            start_audio_encoder_process
            if process_factory is None
            else process_factory
        )
        process = factory(config.command)
        if not isinstance(process, AudioEncoderProcess):
            raise TypeError(
                "Audio encoder process factories must return "
                "AudioEncoderProcess-compatible objects."
            )

        self._condition = threading.Condition(threading.RLock())
        self._process = process
        self._state: AudioEncoderState = "running"
        self._interrupted = False
        self._finalizing = False
        self._finalized = False
        self._exit_reported = False
        self._result: AudioEncoderResult | None = None
        self._cleanup_error: AudioOutputError | None = None
        self._diagnostic = bytearray()
        self._diagnostic_thread = threading.Thread(
            target=self._drain_diagnostics,
            name=f"sds200-encoder-diagnostics-{config.name}",
            daemon=True,
        )
        self._diagnostic_thread.start()

    def snapshot(self) -> AudioEncoderSnapshot:
        with self._condition:
            returncode = self._process.poll()
            return AudioEncoderSnapshot(
                name=self.config.name,
                command=self.config.command,
                state=self._state,
                running=(
                    self._state == "running"
                    and not self._interrupted
                    and returncode is None
                ),
                interrupted=self._interrupted,
                returncode=returncode,
            )

    def poll(self) -> int | None:
        with self._condition:
            return self._process.poll()

    def write_pcm(self, data: bytes) -> None:
        if not isinstance(data, bytes):
            raise TypeError("Audio encoder PCM input must be bytes.")
        if not data:
            return

        with self._condition:
            self._raise_if_unusable_locked()
            process = self._process
            self._raise_if_exited_locked()

        try:
            written = process.stdin.write(data)
            if written is not None and written != len(data):
                raise OSError(
                    "encoder accepted only "
                    f"{written} of {len(data)} input bytes"
                )
            process.stdin.flush()
        except Exception as error:
            with self._condition:
                interrupted = (
                    self._interrupted
                    or self._finalizing
                    or self._finalized
                )
                if process.poll() is not None:
                    self._exit_reported = True
            if interrupted:
                raise AudioOutputError(
                    f"{self.config.name} input was interrupted."
                ) from error
            raise AudioOutputError(
                f"{self.config.name} input failed: "
                f"{type(error).__name__}: {error}"
            ) from error

        with self._condition:
            self._raise_if_unusable_locked()
            self._raise_if_exited_locked()

    def read_encoded(self, size: int) -> bytes:
        if size <= 0:
            raise ValueError(
                "Audio encoder output read size must be greater than zero."
            )

        with self._condition:
            self._raise_if_unusable_locked()
            process = self._process

        try:
            return process.stdout.read(size)
        except Exception as error:
            with self._condition:
                interrupted = (
                    self._interrupted
                    or self._finalizing
                    or self._finalized
                )
            if interrupted:
                raise AudioOutputError(
                    f"{self.config.name} output was interrupted."
                ) from error
            raise AudioOutputError(
                f"{self.config.name} output failed: "
                f"{type(error).__name__}: {error}"
            ) from error

    def interrupt(self) -> None:
        with self._condition:
            if self._finalized or self._interrupted:
                return
            self._interrupted = True
            self._state = "stopping"
            process = self._process

        _close_binary_stream(process.stdin)
        if process.poll() is None:
            with suppress(OSError):
                process.terminate()

    def finalize(self) -> AudioEncoderResult:
        with self._condition:
            while self._finalizing and not self._finalized:
                self._condition.wait()

            if self._finalized:
                previous_cleanup_error = self._cleanup_error
                result = self._result
                if previous_cleanup_error is not None:
                    raise previous_cleanup_error
                assert result is not None
                return result

            self._finalizing = True
            self._state = "stopping"
            process = self._process
            interrupted_at_start = self._interrupted
            diagnostic_thread = self._diagnostic_thread

        if not interrupted_at_start:
            _close_binary_stream(process.stdin)

        returncode = process.poll()
        cleanup_error: AudioOutputError | None = None
        deadline = monotonic() + self.config.stop_timeout

        try:
            returncode = _terminate_process(
                process,
                deadline=deadline,
                name=self.config.name,
            )
        except AudioOutputError as error:
            cleanup_error = error
            polled = process.poll()
            if polled is not None:
                returncode = polled
        finally:
            _close_binary_stream(process.stdin)
            _close_binary_stream(process.stdout)

            if process.poll() is None:
                _close_binary_stream(process.stderr)

            diagnostic_thread.join(
                timeout=_remaining_timeout(deadline)
            )
            if diagnostic_thread.is_alive():
                _close_binary_stream(process.stderr)
                diagnostic_thread.join(
                    timeout=_remaining_timeout(deadline)
                )
            diagnostic_alive = diagnostic_thread.is_alive()
            _close_binary_stream(process.stderr)

        if returncode is None:
            returncode = -1

        with self._condition:
            interrupted = self._interrupted
            exit_reported = self._exit_reported

            diagnostic = ""
            if (
                not interrupted
                and returncode != 0
                and not diagnostic_alive
            ):
                diagnostic = bytes(self._diagnostic).decode(
                    "utf-8",
                    errors="replace",
                ).strip()

            if diagnostic_alive and cleanup_error is None:
                cleanup_error = AudioOutputError(
                    f"{self.config.name} diagnostic worker did not stop."
                )

            result = AudioEncoderResult(
                returncode=returncode,
                interrupted=interrupted,
                diagnostic=diagnostic,
                exit_reported=exit_reported,
            )
            self._result = result
            self._cleanup_error = cleanup_error
            self._finalized = True
            self._finalizing = False
            self._state = "stopped"
            self._condition.notify_all()

        if cleanup_error is not None:
            raise cleanup_error
        return result

    def close(self) -> AudioEncoderResult:
        return self.finalize()

    def _drain_diagnostics(self) -> None:
        try:
            while True:
                chunk = self._process.stderr.read(4096)
                if not chunk:
                    return

                remaining = (
                    self.config.diagnostic_limit
                    - len(self._diagnostic)
                )
                if remaining > 0:
                    self._diagnostic.extend(chunk[:remaining])
        except (OSError, ValueError):
            return

    def _raise_if_unusable_locked(self) -> None:
        if self._finalized:
            raise AudioOutputError(f"{self.config.name} is closed.")
        if self._interrupted or self._finalizing:
            raise AudioOutputError(f"{self.config.name} is stopping.")

    def _raise_if_exited_locked(self) -> None:
        returncode = self._process.poll()
        if returncode is None:
            return
        self._exit_reported = True
        raise AudioOutputError(
            f"{self.config.name} exited unexpectedly "
            f"with status {returncode}."
        )


class _PopenAudioEncoderProcess:
    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        stdin = process.stdin
        stdout = process.stdout
        stderr = process.stderr
        if stdin is None or stdout is None or stderr is None:
            process.kill()
            raise AudioOutputError(
                "Audio encoder process did not expose all required pipe streams."
            )
        self._process = process
        self._stdin = cast(BinaryIO, stdin)
        self._stdout = cast(BinaryIO, stdout)
        self._stderr = cast(BinaryIO, stderr)

    @property
    def stdin(self) -> BinaryIO:
        return self._stdin

    @property
    def stdout(self) -> BinaryIO:
        return self._stdout

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


def start_audio_encoder_process(
    command: tuple[str, ...],
) -> AudioEncoderProcess:
    """Start one encoder command with unbuffered standard pipes."""

    if not command or not command[0]:
        raise ValueError(
            "Audio encoder process command must include an executable."
        )

    try:
        process: subprocess.Popen[bytes] = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
    except FileNotFoundError as error:
        executable = Path(command[0]).name
        raise AudioOutputError(
            f"Audio encoder executable {executable!r} was not found."
        ) from error
    except OSError as error:
        raise AudioOutputError(
            f"Unable to start audio encoder process: {error}"
        ) from error

    return _PopenAudioEncoderProcess(process)


def _terminate_process(
    process: AudioEncoderProcess,
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
