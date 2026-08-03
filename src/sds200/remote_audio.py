from __future__ import annotations

import logging
import os
import threading
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Literal, Protocol, runtime_checkable
from urllib.parse import urlsplit

from .audio_recording import (
    PCM_CHANNELS,
    PCM_SAMPLE_WIDTH,
    PCMU_SAMPLE_RATE,
)
from .audio_sinks import PcmSinkStatistics
from .events import EventBus
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

RemoteSinkHealth = Literal[
    "inactive",
    "healthy",
    "degraded",
    "failed",
]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _require_aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(
            "Remote audio wall clock must return a timezone-aware datetime."
        )
    return value


def _health_for_state(state: RemoteSinkState) -> RemoteSinkHealth:
    if state == "connected":
        return "healthy"
    if state in {"connecting", "backoff"}:
        return "degraded"
    if state == "failed":
        return "failed"
    return "inactive"


def _statistics_as_dict(statistics: PcmSinkStatistics) -> dict[str, int]:
    return {
        "bytes_submitted": statistics.bytes_submitted,
        "bytes_written": statistics.bytes_written,
        "bytes_dropped": statistics.bytes_dropped,
        "queued_bytes": statistics.queued_bytes,
        "underflows": statistics.underflows,
        "overflows": statistics.overflows,
        "callback_statuses": statistics.callback_statuses,
    }


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


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
    health: RemoteSinkHealth
    running: bool
    connected: bool
    statistics: PcmSinkStatistics
    connection_attempts: int
    successful_connections: int
    reconnects: int
    failures: int
    retry_attempt: int
    next_retry_delay: float | None
    transition_sequence: int
    state_changed_at: datetime
    last_connected_at: datetime | None
    last_failure_at: datetime | None
    last_error: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "endpoint": self.endpoint,
            "state": self.state,
            "health": self.health,
            "running": self.running,
            "connected": self.connected,
            "statistics": _statistics_as_dict(self.statistics),
            "connection_attempts": self.connection_attempts,
            "successful_connections": self.successful_connections,
            "reconnects": self.reconnects,
            "failures": self.failures,
            "retry_attempt": self.retry_attempt,
            "next_retry_delay": self.next_retry_delay,
            "transition_sequence": self.transition_sequence,
            "state_changed_at": self.state_changed_at.isoformat(),
            "last_connected_at": _isoformat(self.last_connected_at),
            "last_failure_at": _isoformat(self.last_failure_at),
            "last_error": self.last_error,
        }


@dataclass(frozen=True, slots=True)
class RemotePcmSinkTransition:
    """One immutable remote-destination lifecycle state change."""

    sequence: int
    observed_at: datetime
    previous_state: RemoteSinkState
    state: RemoteSinkState
    previous_health: RemoteSinkHealth
    health: RemoteSinkHealth
    snapshot: RemotePcmSinkSnapshot

    def as_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "observed_at": self.observed_at.isoformat(),
            "previous_state": self.previous_state,
            "state": self.state,
            "previous_health": self.previous_health,
            "health": self.health,
            "snapshot": self.snapshot.as_dict(),
        }


class RemotePcmSink:
    """Send PCM to one remote adapter without blocking the RTP receive callback."""

    def __init__(
        self,
        config: RemoteDestinationConfig,
        connection_factory: RemoteConnectionFactory,
        *,
        environ: Mapping[str, str] | None = None,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.config = config
        self._connection_factory = connection_factory
        self._environ = environ
        self.events = EventBus()
        self._now = now
        initial_state_at = _require_aware_datetime(now())
        capacity = max(
            PCM_SAMPLE_WIDTH,
            int(_PCM_BYTES_PER_SECOND * config.buffer_seconds),
        )
        self._capacity_bytes = capacity - capacity % PCM_SAMPLE_WIDTH
        self._condition = threading.Condition(threading.RLock())
        self._pending_transitions: deque[RemotePcmSinkTransition] = deque()
        self._emitting_transitions = False
        self._queue: deque[bytes] = deque()
        self._queued_bytes = 0
        self._thread: threading.Thread | None = None
        self._active_connection: RemoteAudioConnection | None = None
        self._active_secret_values: tuple[str, ...] = ()
        self._started = False
        self._stopping = False
        self._stopped = False
        self._worker_finishes_stop = False
        self._state: RemoteSinkState = "idle"
        self._transition_sequence = 0
        self._state_changed_at = initial_state_at
        self._last_connected_at: datetime | None = None
        self._last_failure_at: datetime | None = None
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

    def on_transition(
        self,
        callback: Callable[[RemotePcmSinkTransition], None],
    ) -> Callable[[], None]:
        """Subscribe to immutable lifecycle state changes."""

        return self.events.subscribe("transition", callback)

    def snapshot(self) -> RemotePcmSinkSnapshot:
        with self._condition:
            return self._snapshot_locked()

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
        transition: RemotePcmSinkTransition | None = None
        with self._condition:
            if not self._started or self._stopped:
                return
            if self._stopping and self._state == "stopping":
                return
            if not self._stopping:
                self._stopping = True
                if self._state != "failed":
                    transition = self._transition_locked("stopping")
                self._drop_queued_locked()
            thread = self._thread
            connection = self._active_connection
            secret_values = self._active_secret_values
            self._condition.notify_all()

        self._emit_transition(transition)
        interrupt_error: str | None = None
        if connection is not None:
            try:
                connection.interrupt()
            except Exception as error:
                interrupt_error = _redact_error(error, secret_values)
                observed_at = _require_aware_datetime(self._now())
                with self._condition:
                    self._failures += 1
                    self._last_failure_at = observed_at
                    self._last_error = interrupt_error
                logger.warning(
                    "remote audio connection interrupt failed "
                    "name=%s endpoint=%s error=%s",
                    self.config.name,
                    self.config.endpoint,
                    interrupt_error,
                )

        if thread is threading.current_thread():
            with self._condition:
                self._worker_finishes_stop = True
                if interrupt_error is not None and self._terminal_error is None:
                    self._terminal_error = interrupt_error
            return

        if thread is not None:
            thread.join(timeout=self.config.stop_timeout)
            if thread.is_alive():
                raise AudioOutputError(
                    f"Timed out while stopping remote destination {self.config.name!r}."
                )

        with self._condition:
            self._thread = None
            self._stopped = True
            transition = self._transition_locked("stopped")
            terminal_error = self._terminal_error or interrupt_error

        self._emit_transition(transition)
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
                        self._connection_attempts += 1
                        attempt_number = self._connection_attempts
                        self._next_retry_delay = None
                        transition = self._transition_locked("connecting")
                    self._emit_transition(transition)

                    with self._condition:
                        if self._stopping:
                            return

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
                        if self._stopping:
                            return
                        had_successful_connection = self._successful_connections > 0
                        self._successful_connections += 1
                        if had_successful_connection:
                            self._reconnects += 1
                        self._active_connection = connection
                        self._active_secret_values = active_secret_values
                        self._retry_attempt = 0
                        self._next_retry_delay = None
                        transition = self._transition_locked("connected")
                    self._emit_transition(transition)
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
            final_transition: RemotePcmSinkTransition | None = None
            with self._condition:
                if self._worker_finishes_stop:
                    self._thread = None
                    self._stopped = True
                    final_transition = self._transition_locked("stopped")
                self._condition.notify_all()
            self._emit_transition(final_transition)

    def _wait_after_failure(
        self,
        error: BaseException,
        secret_values: tuple[str, ...],
    ) -> bool:
        safe_error = _redact_error(error, secret_values)
        observed_at = _require_aware_datetime(self._now())
        exhausted = False
        delay: float | None = None
        with self._condition:
            self._failures += 1
            self._last_failure_at = observed_at
            self._last_error = safe_error
            retry_attempt = self._retry_attempt + 1
            self._retry_attempt = retry_attempt
            policy = self.config.reconnect_policy
            if not policy.allows(retry_attempt):
                exhausted = True
                self._terminal_error = safe_error
                self._stopping = True
                self._next_retry_delay = None
                self._drop_queued_locked()
                transition = self._transition_locked(
                    "failed",
                    observed_at=observed_at,
                )
                self._condition.notify_all()
            else:
                delay = policy.delay_for(retry_attempt)
                self._next_retry_delay = delay
                transition = self._transition_locked(
                    "backoff",
                    observed_at=observed_at,
                )
                self._condition.notify_all()

        self._emit_transition(transition)
        if exhausted:
            logger.error(
                "remote audio destination exhausted retries name=%s endpoint=%s "
                "attempt=%d error=%s",
                self.config.name,
                self.config.endpoint,
                retry_attempt,
                safe_error,
            )
            return False

        assert delay is not None
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
            observed_at = _require_aware_datetime(self._now())
            with self._condition:
                self._failures += 1
                self._last_failure_at = observed_at
                self._last_error = safe_error
                if terminal and self._terminal_error is None:
                    self._terminal_error = safe_error
            logger.warning(
                "remote audio connection close failed name=%s endpoint=%s error=%s",
                self.config.name,
                self.config.endpoint,
                safe_error,
            )

    def _transition_locked(
        self,
        state: RemoteSinkState,
        *,
        observed_at: datetime | None = None,
    ) -> RemotePcmSinkTransition | None:
        if state == self._state:
            return None

        timestamp = _require_aware_datetime(
            self._now() if observed_at is None else observed_at
        )
        previous_state = self._state
        previous_health = _health_for_state(previous_state)
        self._state = state
        self._transition_sequence += 1
        self._state_changed_at = timestamp
        if state == "connected":
            self._last_connected_at = timestamp

        snapshot = self._snapshot_locked()
        transition = RemotePcmSinkTransition(
            sequence=self._transition_sequence,
            observed_at=timestamp,
            previous_state=previous_state,
            state=state,
            previous_health=previous_health,
            health=snapshot.health,
            snapshot=snapshot,
        )
        self._pending_transitions.append(transition)
        return transition

    def _emit_transition(
        self,
        transition: RemotePcmSinkTransition | None,
    ) -> None:
        del transition
        with self._condition:
            if self._emitting_transitions:
                return
            self._emitting_transitions = True

        while True:
            with self._condition:
                if not self._pending_transitions:
                    self._emitting_transitions = False
                    return
                pending = self._pending_transitions.popleft()

            try:
                self.events.emit("transition", pending)
            except BaseException:
                with self._condition:
                    self._emitting_transitions = False
                raise

    def _snapshot_locked(self) -> RemotePcmSinkSnapshot:
        thread = self._thread
        running = thread is not None and thread.is_alive() and not self._stopped
        return RemotePcmSinkSnapshot(
            name=self.name,
            endpoint=self.config.endpoint,
            state=self._state,
            health=_health_for_state(self._state),
            running=running,
            connected=self._state == "connected",
            statistics=self._statistics_locked(),
            connection_attempts=self._connection_attempts,
            successful_connections=self._successful_connections,
            reconnects=self._reconnects,
            failures=self._failures,
            retry_attempt=self._retry_attempt,
            next_retry_delay=self._next_retry_delay,
            transition_sequence=self._transition_sequence,
            state_changed_at=self._state_changed_at,
            last_connected_at=self._last_connected_at,
            last_failure_at=self._last_failure_at,
            last_error=self._last_error,
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
