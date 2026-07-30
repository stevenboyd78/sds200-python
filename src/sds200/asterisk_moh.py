from __future__ import annotations

import errno
import os
import select
import signal
import threading
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from time import monotonic
from types import FrameType
from typing import Any, BinaryIO, cast

from .audio_recording import PCM_CHANNELS, PCM_SAMPLE_WIDTH, PCMU_SAMPLE_RATE
from .audio_sinks import PcmSinkStatistics
from .exceptions import AudioOutputError

ASTERISK_MOH_FORMAT = "slin"
ASTERISK_MOH_SAMPLE_RATE = PCMU_SAMPLE_RATE
ASTERISK_MOH_CHANNELS = PCM_CHANNELS
ASTERISK_MOH_SAMPLE_WIDTH = PCM_SAMPLE_WIDTH

_PCM_BYTES_PER_SECOND = (
    ASTERISK_MOH_SAMPLE_RATE * ASTERISK_MOH_CHANNELS * ASTERISK_MOH_SAMPLE_WIDTH
)

_FdWrite = Callable[[int, bytes], int]
_WaitWritable = Callable[[int, float], bool]


@dataclass(frozen=True, slots=True)
class PcmStreamSinkSnapshot:
    """Immutable state for one worker-backed raw PCM stream destination."""

    name: str
    running: bool
    reader_closed: bool
    statistics: PcmSinkStatistics
    error: str | None


class PcmStreamSink:
    """Write PCM to a file descriptor without blocking the RTP receive thread."""

    def __init__(
        self,
        output: BinaryIO,
        *,
        name: str = "pcm-stream",
        buffer_seconds: float = 1.0,
        stop_timeout: float = 1.0,
        poll_interval: float = 0.05,
        fd_write: _FdWrite = os.write,
        wait_writable: _WaitWritable | None = None,
    ) -> None:
        if not name or name.strip() != name:
            raise ValueError("PCM stream sink name must not be empty or padded.")
        if buffer_seconds <= 0:
            raise ValueError("PCM stream buffer must be greater than zero seconds.")
        if stop_timeout <= 0:
            raise ValueError("PCM stream stop timeout must be greater than zero.")
        if poll_interval <= 0:
            raise ValueError("PCM stream poll interval must be greater than zero.")
        if poll_interval >= stop_timeout:
            raise ValueError("PCM stream poll interval must be shorter than stop timeout.")

        capacity = max(PCM_SAMPLE_WIDTH, int(_PCM_BYTES_PER_SECOND * buffer_seconds))
        self.output = output
        self._name = name
        self.buffer_seconds = buffer_seconds
        self.stop_timeout = stop_timeout
        self.poll_interval = poll_interval
        self._capacity_bytes = capacity - capacity % PCM_SAMPLE_WIDTH
        self._fd_write = fd_write
        self._wait_writable = _wait_writable if wait_writable is None else wait_writable
        self._condition = threading.Condition(threading.RLock())
        self._queue: deque[bytes] = deque()
        self._queued_bytes = 0
        self._thread: threading.Thread | None = None
        self._fd: int | None = None
        self._output_was_blocking: bool | None = None
        self._started = False
        self._stopping = False
        self._stopped = False
        self._reader_closed = False
        self._error: BaseException | None = None
        self._stop_event = threading.Event()
        self._done_event = threading.Event()
        self._bytes_submitted = 0
        self._bytes_written = 0
        self._bytes_dropped = 0
        self._overflows = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def running(self) -> bool:
        with self._condition:
            thread = self._thread
            return thread is not None and thread.is_alive() and not self._stopping

    @property
    def reader_closed(self) -> bool:
        with self._condition:
            return self._reader_closed

    @property
    def error(self) -> BaseException | None:
        with self._condition:
            return self._error

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

    def snapshot(self) -> PcmStreamSinkSnapshot:
        with self._condition:
            error = self._error
            return PcmStreamSinkSnapshot(
                name=self._name,
                running=self.running,
                reader_closed=self._reader_closed,
                statistics=self.statistics,
                error=None if error is None else f"{type(error).__name__}: {error}",
            )

    def start(self) -> None:
        with self._condition:
            if self._started:
                raise RuntimeError("PCM stream sinks can only be started once.")
            self._started = True

        duplicated_fd: int | None = None
        output_was_blocking: bool | None = None
        try:
            duplicated_fd = os.dup(self.output.fileno())
            output_was_blocking = os.get_blocking(duplicated_fd)
            os.set_blocking(duplicated_fd, False)
        except (OSError, ValueError) as error:
            if duplicated_fd is not None:
                _close_fd(duplicated_fd)
            with self._condition:
                self._stopped = True
            raise AudioOutputError(f"Could not prepare PCM stream output: {error}") from error

        with self._condition:
            self._fd = duplicated_fd
            self._output_was_blocking = output_was_blocking
            thread = threading.Thread(
                target=self._run,
                name=f"sds200-{self._name}",
                daemon=True,
            )
            self._thread = thread
            thread.start()

    def submit_pcm(self, data: bytes) -> None:
        if len(data) % PCM_SAMPLE_WIDTH:
            raise ValueError("PCM data must contain complete 16-bit samples.")
        if not data:
            return

        with self._condition:
            if not self._started:
                raise RuntimeError("PCM stream sink is not running.")
            self._bytes_submitted += len(data)
            if self._reader_closed:
                self._bytes_dropped += len(data)
                return
            if self._error is not None:
                raise AudioOutputError(f"PCM stream sink failed: {self._error}") from self._error
            if self._stopping or self._stopped:
                raise RuntimeError("PCM stream sink is stopping.")

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

    def wait(self, timeout: float | None = None) -> bool:
        """Return true when the output worker has stopped."""

        return self._done_event.wait(timeout)

    def stop(self) -> None:
        with self._condition:
            thread = self._thread
            if thread is None or self._stopped:
                return
            self._stopping = True
            self._stop_event.set()
            self._condition.notify_all()
            fd = self._fd

        deadline = monotonic() + self.stop_timeout
        thread.join(timeout=_remaining_timeout(deadline))
        if thread.is_alive() and fd is not None:
            _close_fd(fd)
            with self._condition:
                if self._fd == fd:
                    self._fd = None
            thread.join(timeout=_remaining_timeout(deadline))
        thread_alive = thread.is_alive()

        with self._condition:
            fd, self._fd = self._fd, None
            output_was_blocking = self._output_was_blocking
            self._thread = None
            self._stopped = True
            error = self._error
        _restore_blocking(self.output, output_was_blocking)
        if fd is not None:
            _close_fd(fd)

        if thread_alive:
            raise AudioOutputError("PCM stream output worker did not stop.")
        if error is not None:
            if isinstance(error, AudioOutputError):
                raise error
            raise AudioOutputError(f"PCM stream output failed: {error}") from error

    def _run(self) -> None:
        pending = b""
        try:
            while True:
                if not pending:
                    with self._condition:
                        while not self._queue and not self._stopping:
                            self._condition.wait()
                        if self._stopping:
                            self._drop_remaining_locked(pending)
                            return
                        pending = self._queue.popleft()
                        self._queued_bytes -= len(pending)

                if self._stop_event.is_set():
                    with self._condition:
                        self._drop_remaining_locked(pending)
                    return

                fd = self._fd
                if fd is None:
                    if self._stopping:
                        with self._condition:
                            self._drop_remaining_locked(pending)
                        return
                    raise AudioOutputError("PCM stream file descriptor is unavailable.")

                try:
                    written = self._fd_write(fd, pending)
                except BlockingIOError:
                    self._wait_writable(fd, self.poll_interval)
                    continue
                except BrokenPipeError:
                    with self._condition:
                        self._mark_reader_closed_locked(pending)
                    return
                except OSError as error:
                    if error.errno == errno.EPIPE:
                        with self._condition:
                            self._mark_reader_closed_locked(pending)
                        return
                    if self._stopping and error.errno == errno.EBADF:
                        with self._condition:
                            self._drop_remaining_locked(pending)
                        return
                    raise

                if written <= 0:
                    raise AudioOutputError("PCM stream write made no forward progress.")
                if written > len(pending):
                    raise AudioOutputError("PCM stream write reported an invalid byte count.")
                pending = pending[written:]
                with self._condition:
                    self._bytes_written += written
        except Exception as error:
            with self._condition:
                self._error = error
                self._stopping = True
                self._drop_remaining_locked(pending)
                self._condition.notify_all()
        finally:
            self._done_event.set()

    def _mark_reader_closed_locked(self, pending: bytes) -> None:
        self._reader_closed = True
        self._stopping = True
        self._drop_remaining_locked(pending)
        self._condition.notify_all()

    def _drop_remaining_locked(self, pending: bytes) -> None:
        dropped = len(pending) + self._queued_bytes
        if dropped:
            self._bytes_dropped += dropped
        self._queue.clear()
        self._queued_bytes = 0


class AsteriskMohSignalController:
    """Translate Asterisk's custom-process termination signals into one stop event."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._previous: dict[int, object] = {}
        self._active = False
        self._last_signal: int | None = None

    @property
    def last_signal(self) -> int | None:
        return self._last_signal

    def stop(self) -> None:
        self._event.set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)

    def __enter__(self) -> AsteriskMohSignalController:
        if self._active:
            raise RuntimeError("Asterisk MOH signal controller is already active.")
        self._event.clear()
        self._last_signal = None
        installed: list[int] = []
        try:
            for signum in _asterisk_stop_signals():
                self._previous[signum] = signal.getsignal(signum)
                signal.signal(signum, self._handle)
                installed.append(signum)
        except BaseException:
            for signum in reversed(installed):
                signal.signal(signum, cast(Any, self._previous[signum]))
            self._previous.clear()
            raise
        self._active = True
        return self

    def __exit__(self, *_: object) -> None:
        for signum, previous in self._previous.items():
            signal.signal(signum, cast(Any, previous))
        self._previous.clear()
        self._active = False

    def _handle(self, signum: int, frame: FrameType | None) -> None:
        del frame
        self._last_signal = signum
        self._event.set()


def _asterisk_stop_signals() -> tuple[int, ...]:
    signals: list[int] = []
    for name in ("SIGHUP", "SIGINT", "SIGTERM"):
        value = getattr(signal, name, None)
        if isinstance(value, int) and value not in signals:
            signals.append(value)
    return tuple(signals)


def _wait_writable(fd: int, timeout: float) -> bool:
    _, writable, _ = select.select([], [fd], [], timeout)
    return bool(writable)


def _remaining_timeout(deadline: float) -> float:
    return max(0.0, deadline - monotonic())


def _restore_blocking(output: BinaryIO, blocking: bool | None) -> None:
    if blocking is None:
        return
    with suppress(OSError, ValueError):
        os.set_blocking(output.fileno(), blocking)


def _close_fd(fd: int) -> None:
    with suppress(OSError):
        os.close(fd)
