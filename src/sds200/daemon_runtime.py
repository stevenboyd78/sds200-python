from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite
from time import monotonic
from typing import Protocol, Self

from .audio_sinks import (
    AudioFanoutSession,
    AudioFanoutSnapshot,
    PcmSink,
    PcmSinkRouter,
    PcmSinkRouterSnapshot,
)
from .events import EventBus
from .exceptions import (
    CommandTimeoutError,
    DaemonControlBusyError,
    DaemonControlUnavailableError,
    UnsupportedScannerFeatureError,
)
from .state import RadioStateSnapshot

logger = logging.getLogger(__name__)


class DaemonRuntimeState(StrEnum):
    """Lifecycle state for one renderer-neutral ownership runtime."""

    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class DaemonControlOperation(StrEnum):
    """Capability-checked scanner controls owned by one daemon runtime."""

    HOLD = "scanner.hold"
    NEXT = "scanner.next"
    PREVIOUS = "scanner.previous"
    RECONNECT = "scanner.reconnect"


class _RadioStateLike(Protocol):
    @property
    def snapshot(self) -> RadioStateSnapshot: ...


class _ScannerLike(Protocol):
    @property
    def endpoint(self) -> str: ...

    @property
    def connected(self) -> bool: ...

    @property
    def psi_active(self) -> bool: ...

    @property
    def supports_bounded_reconnect(self) -> bool: ...

    @property
    def state(self) -> _RadioStateLike: ...

    def hold(
        self,
        target: str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        timeout: float = 2.0,
    ) -> None: ...

    def next(
        self,
        target: str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        count: int = 1,
        timeout: float = 2.0,
    ) -> None: ...

    def previous(
        self,
        target: str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        count: int = 1,
        timeout: float = 2.0,
    ) -> None: ...

    def reconnect(self, *, timeout: float = 2.0) -> None: ...

    def connect(self) -> None: ...

    def start_scanner_info_push(
        self,
        interval_ms: int = 500,
        *,
        timeout: float = 3.0,
    ) -> object: ...

    def stop_scanner_info_push(self) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class DaemonRuntimeSnapshot:
    """Immutable operational state for one single-owner runtime."""

    state: DaemonRuntimeState
    scanner_endpoint: str
    scanner_connected: bool
    psi_interval_ms: int
    psi_active: bool
    radio_state: RadioStateSnapshot
    audio: AudioFanoutSnapshot
    router: PcmSinkRouterSnapshot
    started_at: datetime | None
    stopped_at: datetime | None
    state_changed_at: datetime
    transition_sequence: int
    last_failure_at: datetime | None
    last_error: str | None

    @property
    def active(self) -> bool:
        return self.state in {
            DaemonRuntimeState.STARTING,
            DaemonRuntimeState.RUNNING,
            DaemonRuntimeState.STOPPING,
        }

    def as_dict(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "scanner_endpoint": self.scanner_endpoint,
            "scanner_connected": self.scanner_connected,
            "psi_interval_ms": self.psi_interval_ms,
            "psi_active": self.psi_active,
            "radio_state": asdict(self.radio_state),
            "audio": asdict(self.audio),
            "router": self.router.as_dict(),
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
            "state_changed_at": self.state_changed_at.isoformat(),
            "transition_sequence": self.transition_sequence,
            "last_failure_at": (
                self.last_failure_at.isoformat()
                if self.last_failure_at is not None
                else None
            ),
            "last_error": self.last_error,
        }


@dataclass(frozen=True, slots=True)
class DaemonControlResult:
    """Immutable authoritative completion of one daemon-owned scanner control."""

    sequence: int
    operation: DaemonControlOperation
    started_at: datetime
    completed_at: datetime
    snapshot: DaemonRuntimeSnapshot

    def __post_init__(self) -> None:
        if self.sequence <= 0:
            raise ValueError("Daemon control sequence must be greater than zero.")
        _require_aware_datetime(self.started_at)
        _require_aware_datetime(self.completed_at)
        if self.completed_at < self.started_at:
            raise ValueError(
                "Daemon control completion cannot precede its start time."
            )

    def as_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "operation": self.operation.value,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "snapshot": self.snapshot.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class DaemonRuntimeTransition:
    """One ordered immutable runtime lifecycle transition."""

    sequence: int
    observed_at: datetime
    previous_state: DaemonRuntimeState
    state: DaemonRuntimeState
    snapshot: DaemonRuntimeSnapshot

    def as_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "observed_at": self.observed_at.isoformat(),
            "previous_state": self.previous_state.value,
            "state": self.state.value,
            "snapshot": self.snapshot.as_dict(),
        }


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _require_aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Daemon runtime timestamps must be timezone-aware.")
    return value


def _require_positive_control_timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("Daemon control timeout must be a number.")
    normalized = float(value)
    if not isfinite(normalized) or normalized <= 0:
        raise ValueError(
            "Daemon control timeout must be finite and greater than zero."
        )
    return normalized


def _redacted_error_type(error: BaseException) -> str:
    return error.__class__.__name__


class DaemonRuntime:
    """Own serialized scanner controls, PSI, audio, and dynamic PCM sinks."""

    def __init__(
        self,
        scanner: _ScannerLike,
        audio: AudioFanoutSession,
        router: PcmSinkRouter,
        *,
        psi_interval_ms: int = 500,
        psi_timeout: float = 3.0,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        if psi_interval_ms <= 0:
            raise ValueError("PSI interval must be greater than zero.")
        if psi_timeout <= 0:
            raise ValueError("PSI timeout must be greater than zero.")
        if not any(sink is router for sink in audio.sinks):
            raise ValueError(
                "Daemon runtime audio fanout must include its PCM sink router."
            )

        initial_at = _require_aware_datetime(now())
        self.scanner = scanner
        self.audio = audio
        self.router = router
        self.psi_interval_ms = psi_interval_ms
        self.psi_timeout = psi_timeout
        self._now = now

        self.events = EventBus()
        self._lifecycle_lock = threading.RLock()
        self._control_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._pending_transitions: deque[DaemonRuntimeTransition] = deque()
        self._emitting_transitions = False
        self._started = False
        self._stopped = False
        self._state = DaemonRuntimeState.IDLE
        self._state_changed_at = initial_at
        self._started_at: datetime | None = None
        self._stopped_at: datetime | None = None
        self._transition_sequence = 0
        self._control_sequence = 0
        self._last_failure_at: datetime | None = None
        self._last_error: str | None = None

    @property
    def running(self) -> bool:
        with self._state_lock:
            return self._state is DaemonRuntimeState.RUNNING

    def snapshot(self) -> DaemonRuntimeSnapshot:
        with self._state_lock:
            return self._snapshot_locked()

    def on_transition(
        self,
        callback: Callable[[DaemonRuntimeTransition], None],
    ) -> Callable[[], None]:
        return self.events.subscribe("transition", callback)

    def hold(
        self,
        target: str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        timeout: float = 2.0,
    ) -> DaemonControlResult:
        return self._execute_control(
            DaemonControlOperation.HOLD,
            timeout,
            lambda remaining: self.scanner.hold(
                target,
                first,
                second,
                timeout=remaining,
            ),
        )

    def next(
        self,
        target: str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        count: int = 1,
        timeout: float = 2.0,
    ) -> DaemonControlResult:
        return self._execute_control(
            DaemonControlOperation.NEXT,
            timeout,
            lambda remaining: self.scanner.next(
                target,
                first,
                second,
                count=count,
                timeout=remaining,
            ),
        )

    def previous(
        self,
        target: str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        count: int = 1,
        timeout: float = 2.0,
    ) -> DaemonControlResult:
        return self._execute_control(
            DaemonControlOperation.PREVIOUS,
            timeout,
            lambda remaining: self.scanner.previous(
                target,
                first,
                second,
                count=count,
                timeout=remaining,
            ),
        )

    def reconnect(
        self,
        *,
        timeout: float = 2.0,
    ) -> DaemonControlResult:
        def reconnect_with_deadline(remaining: float) -> None:
            if not self.scanner.supports_bounded_reconnect:
                raise UnsupportedScannerFeatureError(
                    "Daemon reconnect requires a directly owned bounded "
                    "network control transport."
                )
            self.scanner.reconnect(timeout=remaining)

        return self._execute_control(
            DaemonControlOperation.RECONNECT,
            timeout,
            reconnect_with_deadline,
            requires_connection=False,
        )

    def start(self) -> None:
        caught: BaseException | None = None

        with self._lifecycle_lock:
            with self._state_lock:
                if self._started:
                    if self._stopped:
                        raise RuntimeError(
                            "Daemon runtimes can only be started once."
                        )
                    return
                self._started = True
                self._transition_locked(DaemonRuntimeState.STARTING)

            scanner_attempted = False
            psi_attempted = False
            audio_attempted = False

            try:
                scanner_attempted = True
                self.scanner.connect()

                psi_attempted = True
                self.scanner.start_scanner_info_push(
                    self.psi_interval_ms,
                    timeout=self.psi_timeout,
                )

                audio_attempted = True
                self.audio.start()
            except BaseException as error:
                caught = error
                cleanup_failures: list[BaseException] = []

                if audio_attempted:
                    self._cleanup_step(
                        "audio fanout",
                        self.audio.stop,
                        cleanup_failures,
                    )
                if psi_attempted and self.scanner.psi_active:
                    self._cleanup_step(
                        "PSI stream",
                        self.scanner.stop_scanner_info_push,
                        cleanup_failures,
                    )
                if scanner_attempted:
                    self._cleanup_step(
                        "scanner control",
                        self.scanner.close,
                        cleanup_failures,
                    )

                observed_at = _require_aware_datetime(self._now())
                with self._state_lock:
                    self._stopped = True
                    self._stopped_at = observed_at
                    self._last_failure_at = observed_at
                    self._last_error = _redacted_error_type(error)
                    self._transition_locked(
                        DaemonRuntimeState.FAILED,
                        observed_at=observed_at,
                    )
            else:
                observed_at = _require_aware_datetime(self._now())
                with self._state_lock:
                    self._started_at = observed_at
                    self._transition_locked(
                        DaemonRuntimeState.RUNNING,
                        observed_at=observed_at,
                    )

        self._emit_pending_transitions()
        if caught is not None:
            raise caught

        logger.info(
            "daemon runtime started scanner=%s audio=%s psi_interval_ms=%d",
            self.scanner.endpoint,
            self.audio.snapshot().endpoint,
            self.psi_interval_ms,
        )

    def stop(self) -> None:
        failures: list[BaseException] = []

        with self._lifecycle_lock:
            with self._state_lock:
                if not self._started or self._stopped:
                    return
                self._transition_locked(DaemonRuntimeState.STOPPING)

            self._cleanup_step("audio fanout", self.audio.stop, failures)
            if self.scanner.psi_active:
                self._cleanup_step(
                    "PSI stream",
                    self.scanner.stop_scanner_info_push,
                    failures,
                )
            self._cleanup_step("scanner control", self.scanner.close, failures)

            observed_at = _require_aware_datetime(self._now())
            with self._state_lock:
                self._stopped = True
                self._stopped_at = observed_at
                if failures:
                    self._last_failure_at = observed_at
                    self._last_error = _redacted_error_type(failures[0])
                    terminal_state = DaemonRuntimeState.FAILED
                else:
                    terminal_state = DaemonRuntimeState.STOPPED
                self._transition_locked(
                    terminal_state,
                    observed_at=observed_at,
                )

        self._emit_pending_transitions()

        snapshot = self.snapshot()
        logger.info(
            "daemon runtime stopped scanner=%s state=%s",
            snapshot.scanner_endpoint,
            snapshot.state.value,
        )
        if failures:
            raise failures[0]

    def close(self) -> None:
        self.stop()

    def attach_sink(self, sink: PcmSink) -> None:
        with self._lifecycle_lock:
            with self._state_lock:
                if self._stopped:
                    raise RuntimeError(
                        "Cannot attach a sink to a stopped daemon runtime."
                    )
            self.router.attach(sink)

    def detach_sink(self, sink: PcmSink, *, stop: bool = True) -> None:
        with self._lifecycle_lock:
            self.router.detach(sink, stop=stop)

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()

    def _execute_control(
        self,
        operation: DaemonControlOperation,
        timeout: float,
        action: Callable[[float], None],
        *,
        requires_connection: bool = True,
    ) -> DaemonControlResult:
        normalized_timeout = _require_positive_control_timeout(timeout)
        deadline = monotonic() + normalized_timeout

        if not self._control_lock.acquire(blocking=False):
            raise DaemonControlBusyError(
                "Another daemon scanner control is already in progress."
            )

        lifecycle_acquired = False
        try:
            remaining = deadline - monotonic()
            if remaining <= 0 or not self._lifecycle_lock.acquire(
                timeout=max(0.0, remaining)
            ):
                raise CommandTimeoutError(
                    "Daemon scanner control timed out before execution."
                )
            lifecycle_acquired = True

            with self._state_lock:
                if self._state is not DaemonRuntimeState.RUNNING:
                    raise DaemonControlUnavailableError(
                        "Daemon scanner controls require a running runtime."
                    )
                if requires_connection and not self.scanner.connected:
                    raise DaemonControlUnavailableError(
                        "Daemon scanner controls require a connected scanner."
                    )

            remaining = deadline - monotonic()
            if remaining <= 0:
                raise CommandTimeoutError(
                    "Daemon scanner control timed out before execution."
                )

            started_at = _require_aware_datetime(self._now())
            action(remaining)
            completed_at = _require_aware_datetime(self._now())

            with self._state_lock:
                self._control_sequence += 1
                return DaemonControlResult(
                    sequence=self._control_sequence,
                    operation=operation,
                    started_at=started_at,
                    completed_at=completed_at,
                    snapshot=self._snapshot_locked(),
                )
        finally:
            if lifecycle_acquired:
                self._lifecycle_lock.release()
            self._control_lock.release()

    def _cleanup_step(
        self,
        name: str,
        action: Callable[[], None],
        failures: list[BaseException],
    ) -> None:
        try:
            action()
        except BaseException as error:
            failures.append(error)
            logger.exception("Daemon runtime cleanup failed component=%s", name)

    def _transition_locked(
        self,
        state: DaemonRuntimeState,
        *,
        observed_at: datetime | None = None,
    ) -> DaemonRuntimeTransition | None:
        if state is self._state:
            return None

        timestamp = _require_aware_datetime(
            self._now() if observed_at is None else observed_at
        )
        previous_state = self._state
        self._transition_sequence += 1
        self._state = state
        self._state_changed_at = timestamp

        transition = DaemonRuntimeTransition(
            sequence=self._transition_sequence,
            observed_at=timestamp,
            previous_state=previous_state,
            state=state,
            snapshot=self._snapshot_locked(),
        )
        self._pending_transitions.append(transition)
        return transition

    def _emit_pending_transitions(self) -> None:
        with self._state_lock:
            if self._emitting_transitions:
                return
            self._emitting_transitions = True

        while True:
            with self._state_lock:
                if not self._pending_transitions:
                    self._emitting_transitions = False
                    return
                transition = self._pending_transitions.popleft()

            try:
                self.events.emit("transition", transition)
            except BaseException:
                with self._state_lock:
                    self._emitting_transitions = False
                raise

    def _snapshot_locked(self) -> DaemonRuntimeSnapshot:
        return DaemonRuntimeSnapshot(
            state=self._state,
            scanner_endpoint=self.scanner.endpoint,
            scanner_connected=self.scanner.connected,
            psi_interval_ms=self.psi_interval_ms,
            psi_active=self.scanner.psi_active,
            radio_state=self.scanner.state.snapshot,
            audio=self.audio.snapshot(),
            router=self.router.snapshot(),
            started_at=self._started_at,
            stopped_at=self._stopped_at,
            state_changed_at=self._state_changed_at,
            transition_sequence=self._transition_sequence,
            last_failure_at=self._last_failure_at,
            last_error=self._last_error,
        )
