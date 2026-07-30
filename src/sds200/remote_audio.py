from __future__ import annotations

import logging
import os
import threading
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Protocol, runtime_checkable
from urllib.parse import urlsplit

from .audio_recording import (
    PCM_CHANNELS,
    PCM_SAMPLE_WIDTH,
    PCMU_SAMPLE_RATE,
)
from .audio_sinks import PcmSinkStatistics
from .exceptions import AudioOutputError
from .reliability import ReconnectPolicy

logger = logging.getLogger(__name__)

_PCM_BYTES_PER_SECOND = PCMU_SAMPLE_RATE * PCM_CHANNELS * PCM_SAMPLE_WIDTH

RemoteSinkState = Literal[
    "idle",
    "connecting",
    "connected",
    "backoff",
    "failed",
    "stopping",
    "stopped",
]


@dataclass(frozen=True, slots=True)
class EnvironmentSecret:
    """Reference a secret by environment-variable name without storing its value."""

    variable: str

    def __post_init__(self) -> None:
        if not self.variable or self.variable.strip() != self.variable:
            raise ValueError("Secret environment-variable name must not be empty or padded.")
        if any(character.isspace() for character in self.variable) or "=" in self.variable:
            raise ValueError("Secret environment-variable name is invalid.")

    def resolve(self, environ: Mapping[str, str] | None = None) -> str:
        source = os.environ if environ is None else environ
        value = source.get(self.variable)
        if not value:
            raise AudioOutputError(
                f"Secret environment variable {self.variable!r} is not set."
            )
        return value


@dataclass(frozen=True, slots=True)
class RemoteDestinationConfig:
    """Service-neutral settings shared by future remote audio adapters."""

    name: str
    endpoint: str
    secrets: Mapping[str, EnvironmentSecret] = field(default_factory=dict)
    buffer_seconds: float = 5.0
    stop_timeout: float = 5.0
    reconnect_policy: ReconnectPolicy = field(default_factory=ReconnectPolicy)

    def __post_init__(self) -> None:
        _validate_text("Remote destination name", self.name)
        _validate_text("Remote destination endpoint", self.endpoint)
        parsed = urlsplit(self.endpoint)
        if parsed.username is not None or parsed.password is not None:
            raise ValueError(
                "Remote destination endpoints must not contain embedded credentials."
            )
        if self.buffer_seconds <= 0:
            raise ValueError("Remote destination buffer must be greater than zero seconds.")
        if self.stop_timeout <= 0:
            raise ValueError("Remote destination stop timeout must be greater than zero.")
        copied_secrets = dict(self.secrets)
        for key, reference in copied_secrets.items():
            _validate_text("Remote destination secret name", key)
            if not isinstance(reference, EnvironmentSecret):
                raise TypeError(
                    "Remote destination secrets must contain EnvironmentSecret values."
                )
        object.__setattr__(self, "secrets", MappingProxyType(copied_secrets))

    def resolve_secrets(
        self,
        environ: Mapping[str, str] | None = None,
    ) -> Mapping[str, str]:
        return MappingProxyType(
            {
                name: reference.resolve(environ)
                for name, reference in self.secrets.items()
            }
        )


@runtime_checkable
class RemoteAudioConnection(Protocol):
    """Blocking adapter connection used only by the remote sink worker."""

    def write_pcm(self, data: bytes) -> None: ...

    def interrupt(self) -> None:
        """Promptly interrupt an in-flight write from another thread."""
        ...

    def close(self) -> None: ...


RemoteConnectionFactory = Callable[
    [RemoteDestinationConfig, Mapping[str, str]],
    RemoteAudioConnection,
]


@dataclass(frozen=True, slots=True)
class RemotePcmSinkSnapshot:
    """Immutable operational state for one worker-backed remote destination."""

    name: str
    endpoint: str
    state: RemoteSinkState
    running: bool
    connected: bool
    statistics: PcmSinkStatistics
    connection_attempts: int
    successful_connections: int
    reconnects: int
    failures: int
    retry_attempt: int
    next_retry_delay: float | None
    last_error: str | None


class RemotePcmSink:
    """Send PCM to one remote adapter without blocking the RTP receive callback."""

    def __init__(
        self,
        config: RemoteDestinationConfig,
        connection_factory: RemoteConnectionFactory,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.config = config
        self._connection_factory = connection_factory
        self._environ = environ
        capacity = max(
            PCM_SAMPLE_WIDTH,
            int(_PCM_BYTES_PER_SECOND * config.buffer_seconds),
        )
        self._capacity_bytes = capacity - capacity % PCM_SAMPLE_WIDTH
        self._condition = threading.Condition(threading.RLock())
        self._queue: deque[bytes] = deque()
        self._queued_bytes = 0
        self._thread: threading.Thread | None = None
        self._active_connection: RemoteAudioConnection | None = None
        self._active_secret_values: tuple[str, ...] = ()
        self._started = False
        self._stopping = False
        self._stopped = False
        self._state: RemoteSinkState = "idle"
        self._terminal_error: str | None = None
        self._bytes_submitted = 0
        self._bytes_written = 0
        self._bytes_dropped = 0
        self._overflows = 0
        self._connection_attempts = 0
        self._successful_connections = 0
        self._reconnects = 0
        self._failures = 0
        self._retry_attempt = 0
        self._next_retry_delay: float | None = None
        self._last_error: str | None = None

    @property
    def name(self) -> str:
        return f"remote:{self.config.name}"

    @property
    def running(self) -> bool:
        with self._condition:
            thread = self._thread
            return thread is not None and thread.is_alive() and not self._stopped

    @property
    def statistics(self) -> PcmSinkStatistics:
        with self._condition:
            return self._statistics_locked()

    def snapshot(self) -> RemotePcmSinkSnapshot:
        with self._condition:
            thread = self._thread
            running = thread is not None and thread.is_alive() and not self._stopped
            return RemotePcmSinkSnapshot(
                name=self.name,
                endpoint=self.config.endpoint,
                state=self._state,
                running=running,
                connected=self._state == "connected",
                statistics=self._statistics_locked(),
                connection_attempts=self._connection_attempts,
                successful_connections=self._successful_connections,
                reconnects=self._reconnects,
                failures=self._failures,
                retry_attempt=self._retry_attempt,
                next_retry_delay=self._next_retry_delay,
                last_error=self._last_error,
            )

    def start(self) -> None:
        with self._condition:
            if self._started:
                if self._stopped:
                    raise RuntimeError("Remote PCM sinks can only be started once.")
                return
            self._started = True
            self._state = "idle"
            thread = threading.Thread(
                target=self._run,
                name=f"sds200-remote-{self.config.name}",
                daemon=True,
            )
            self._thread = thread
            thread.start()
        logger.info(
            "remote audio sink started name=%s endpoint=%s",
            self.config.name,
            self.config.endpoint,
        )

    def submit_pcm(self, data: bytes) -> None:
        if len(data) % PCM_SAMPLE_WIDTH:
            raise ValueError("PCM data must contain complete 16-bit samples.")
        if not data:
            return
        with self._condition:
            thread = self._thread
            if (
                thread is None
                or not thread.is_alive()
                or self._stopping
                or self._stopped
            ):
                raise RuntimeError("Remote PCM sink is not running.")
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
            self._condition.notify_all()

    def stop(self) -> None:
        with self._condition:
            if not self._started or self._stopped:
                return
            if not self._stopping:
                self._stopping = True
                if self._state != "failed":
                    self._state = "stopping"
                self._drop_queued_locked()
            thread = self._thread
            connection = self._active_connection
            secret_values = self._active_secret_values
            self._condition.notify_all()

        interrupt_error: str | None = None
        if connection is not None:
            try:
                connection.interrupt()
            except Exception as error:
                interrupt_error = _redact_error(error, secret_values)
                with self._condition:
                    self._failures += 1
                    self._last_error = interrupt_error
                logger.warning(
                    "remote audio connection interrupt failed "
                    "name=%s endpoint=%s error=%s",
                    self.config.name,
                    self.config.endpoint,
                    interrupt_error,
                )

        if thread is not None:
            thread.join(timeout=self.config.stop_timeout)
            if thread.is_alive():
                raise AudioOutputError(
                    f"Timed out while stopping remote destination {self.config.name!r}."
                )

        with self._condition:
            self._thread = None
            self._stopped = True
            self._state = "stopped"
            terminal_error = self._terminal_error or interrupt_error

        logger.info(
            "remote audio sink stopped name=%s endpoint=%s",
            self.config.name,
            self.config.endpoint,
        )
        if terminal_error is not None:
            raise AudioOutputError(
                f"Remote destination {self.config.name!r} failed: {terminal_error}"
            )

    def close(self) -> None:
        self.stop()

    def _run(self) -> None:
        connection: RemoteAudioConnection | None = None
        active_secret_values: tuple[str, ...] = ()
        try:
            while True:
                with self._condition:
                    while not self._queue and not self._stopping:
                        self._condition.wait()
                    if self._stopping:
                        return

                if connection is None:
                    with self._condition:
                        self._state = "connecting"
                        self._connection_attempts += 1
                        attempt_number = self._connection_attempts
                        self._next_retry_delay = None

                    resolved_secrets: Mapping[str, str] = MappingProxyType({})
                    try:
                        resolved_secrets = self.config.resolve_secrets(self._environ)
                        candidate = self._connection_factory(
                            self.config,
                            resolved_secrets,
                        )
                    except Exception as error:
                        if not self._wait_after_failure(
                            error,
                            tuple(resolved_secrets.values()),
                        ):
                            return
                        continue

                    connection = candidate
                    active_secret_values = tuple(resolved_secrets.values())
                    with self._condition:
                        had_successful_connection = self._successful_connections > 0
                        self._successful_connections += 1
                        if had_successful_connection:
                            self._reconnects += 1
                        self._active_connection = connection
                        self._active_secret_values = active_secret_values
                        self._retry_attempt = 0
                        self._next_retry_delay = None
                        self._state = "connected"
                    logger.info(
                        "remote audio connected name=%s endpoint=%s attempt=%d",
                        self.config.name,
                        self.config.endpoint,
                        attempt_number,
                    )

                with self._condition:
                    if self._stopping:
                        return
                    if not self._queue:
                        continue
                    data = self._queue.popleft()
                    self._queued_bytes -= len(data)

                try:
                    connection.write_pcm(data)
                except Exception as error:
                    with self._condition:
                        self._bytes_dropped += len(data)
                        if self._active_connection is connection:
                            self._active_connection = None
                            self._active_secret_values = ()
                    self._close_connection(
                        connection,
                        active_secret_values,
                        terminal=False,
                    )
                    connection = None
                    with self._condition:
                        if self._stopping:
                            return
                    if not self._wait_after_failure(error, active_secret_values):
                        return
                    active_secret_values = ()
                    continue

                with self._condition:
                    self._bytes_written += len(data)
        finally:
            if connection is not None:
                with self._condition:
                    if self._active_connection is connection:
                        self._active_connection = None
                        self._active_secret_values = ()
                self._close_connection(
                    connection,
                    active_secret_values,
                    terminal=True,
                )
            with self._condition:
                self._condition.notify_all()

    def _wait_after_failure(
        self,
        error: BaseException,
        secret_values: tuple[str, ...],
    ) -> bool:
        safe_error = _redact_error(error, secret_values)
        with self._condition:
            self._failures += 1
            self._last_error = safe_error
            retry_attempt = self._retry_attempt + 1
            self._retry_attempt = retry_attempt
            policy = self.config.reconnect_policy
            if not policy.allows(retry_attempt):
                self._terminal_error = safe_error
                self._state = "failed"
                self._stopping = True
                self._next_retry_delay = None
                self._drop_queued_locked()
                self._condition.notify_all()
                logger.error(
                    "remote audio destination exhausted retries name=%s endpoint=%s "
                    "attempt=%d error=%s",
                    self.config.name,
                    self.config.endpoint,
                    retry_attempt,
                    safe_error,
                )
                return False
            delay = policy.delay_for(retry_attempt)
            self._state = "backoff"
            self._next_retry_delay = delay
            self._condition.notify_all()

        logger.warning(
            "remote audio destination retry scheduled name=%s endpoint=%s "
            "attempt=%d delay=%.3f error=%s",
            self.config.name,
            self.config.endpoint,
            retry_attempt,
            delay,
            safe_error,
        )
        with self._condition:
            self._condition.wait_for(
                lambda: self._stopping,
                timeout=delay,
            )
            return not self._stopping

    def _close_connection(
        self,
        connection: RemoteAudioConnection,
        secret_values: tuple[str, ...],
        *,
        terminal: bool,
    ) -> None:
        try:
            connection.close()
        except Exception as error:
            safe_error = _redact_error(error, secret_values)
            with self._condition:
                self._failures += 1
                self._last_error = safe_error
                if terminal and self._terminal_error is None:
                    self._terminal_error = safe_error
            logger.warning(
                "remote audio connection close failed name=%s endpoint=%s error=%s",
                self.config.name,
                self.config.endpoint,
                safe_error,
            )

    def _statistics_locked(self) -> PcmSinkStatistics:
        return PcmSinkStatistics(
            bytes_submitted=self._bytes_submitted,
            bytes_written=self._bytes_written,
            bytes_dropped=self._bytes_dropped,
            queued_bytes=self._queued_bytes,
            overflows=self._overflows,
        )

    def _drop_queued_locked(self) -> None:
        if self._queued_bytes:
            self._bytes_dropped += self._queued_bytes
            self._queue.clear()
            self._queued_bytes = 0


def _validate_text(label: str, value: str) -> None:
    if not value or value.strip() != value:
        raise ValueError(f"{label} must not be empty or padded.")
    if "\n" in value or "\r" in value:
        raise ValueError(f"{label} must not contain line breaks.")


def _redact_error(error: BaseException, secret_values: tuple[str, ...]) -> str:
    message = f"{type(error).__name__}: {error}"
    for value in sorted(
        (secret for secret in secret_values if secret),
        key=len,
        reverse=True,
    ):
        message = message.replace(value, "<redacted>")
    return message
