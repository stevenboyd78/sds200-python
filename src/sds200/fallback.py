from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from time import monotonic
from types import MappingProxyType

from .exceptions import ScannerConnectionError
from .reliability import ReconnectCounter, ReconnectPolicy
from .transport import (
    ConnectionHandler,
    ControlTransport,
    DiagnosticControlTransport,
    DiagnosticHandler,
    LineHandler,
    StatisticalControlTransport,
    TransportDiagnostic,
)

logger = logging.getLogger(__name__)
TransportFactory = Callable[[], ControlTransport]
RecoveryGuard = Callable[[], bool]
RecoveryProbeValidator = Callable[[str], bool]


@dataclass(frozen=True, slots=True)
class PreferredRecoveryPolicy:
    """Policy for returning from a fallback transport to a preferred candidate.

    Recovery is opt-in: pass an instance to :class:`FallbackTransport`. The
    candidate is validated with a real scanner command before it replaces the
    current fallback transport.
    """

    probe_interval: float = 30.0
    probe_timeout: float = 2.0
    stability_window: float = 5.0
    cooldown: float = 30.0

    def __post_init__(self) -> None:
        if self.probe_interval <= 0:
            raise ValueError("Preferred recovery probe interval must be positive.")
        if self.probe_timeout <= 0:
            raise ValueError("Preferred recovery probe timeout must be positive.")
        if self.stability_window < 0:
            raise ValueError(
                "Preferred recovery stability window must not be negative."
            )
        if self.cooldown < 0:
            raise ValueError("Preferred recovery cooldown must not be negative.")

    def as_dict(self) -> dict[str, float]:
        return {
            "probe_interval": self.probe_interval,
            "probe_timeout": self.probe_timeout,
            "stability_window": self.stability_window,
            "cooldown": self.cooldown,
        }


@dataclass(frozen=True, slots=True)
class TransportCandidate:
    name: str
    endpoint: str
    factory: TransportFactory

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Transport candidate name must not be empty.")
        if not self.endpoint.strip():
            raise ValueError("Transport candidate endpoint must not be empty.")


@dataclass(slots=True)
class _MutableFallbackStatistics:
    activation_attempts: int = 0
    activation_failures: int = 0
    successful_activations: int = 0
    failovers: int = 0
    write_retries: int = 0
    reconnect_attempts: int = 0
    reconnect_failures: int = 0
    reconnect_exhausted: int = 0
    preferred_recovery_probe_attempts: int = 0
    preferred_recovery_probe_failures: int = 0
    preferred_recovery_deferred: int = 0
    preferred_recoveries: int = 0
    last_failure: str | None = None
    last_failure_reason: str | None = None
    last_switch_at: datetime | None = None
    last_switch_from: str | None = None
    last_switch_to: str | None = None
    last_recovery_probe_at: datetime | None = None
    last_recovery_at: datetime | None = None
    last_recovery_failure: str | None = None


@dataclass(slots=True)
class _ProbedTransport:
    transport: ControlTransport
    promoted: threading.Event


class FallbackTransport:
    """Control transport that switches between ordered candidates.

    Candidate transports must have their own reconnect loops disabled. This
    coordinator owns reconnect and failover so a failed preferred transport
    cannot block use of the next candidate indefinitely. Preferred recovery is
    disabled unless ``preferred_recovery_policy`` is supplied.
    """

    def __init__(
        self,
        candidates: Sequence[TransportCandidate],
        *,
        retry_interval: float = 2.0,
        failover_timeout: float = 3.0,
        reconnect_policy: ReconnectPolicy | None = None,
        preferred_recovery_policy: PreferredRecoveryPolicy | None = None,
        recovery_probe_command: str = "MDL",
        recovery_probe_validator: RecoveryProbeValidator | None = None,
    ) -> None:
        if not candidates:
            raise ValueError("At least one fallback transport candidate is required.")
        if retry_interval <= 0:
            raise ValueError("Fallback retry interval must be positive.")
        if failover_timeout <= 0:
            raise ValueError("Fallback timeout must be positive.")
        if preferred_recovery_policy is not None and len(candidates) < 2:
            raise ValueError(
                "Preferred recovery requires at least two transport candidates."
            )
        if not recovery_probe_command.strip():
            raise ValueError("Preferred recovery probe command must not be empty.")

        names = [candidate.name for candidate in candidates]
        if len(names) != len(set(names)):
            raise ValueError("Fallback transport candidate names must be unique.")

        self.candidates = tuple(candidates)
        self.retry_interval = retry_interval
        self.failover_timeout = failover_timeout
        self.reconnect_policy = reconnect_policy or ReconnectPolicy(
            initial_delay=retry_interval,
            multiplier=1.0,
            max_delay=retry_interval,
        )
        self.preferred_recovery_policy = preferred_recovery_policy
        self.recovery_probe_command = recovery_probe_command.strip()
        self._recovery_probe_validator = (
            recovery_probe_validator or self._default_recovery_probe_validator
        )
        self._recovery_guard: RecoveryGuard = lambda: True
        self._reconnect_counter = ReconnectCounter(self.reconnect_policy)
        self._handler: LineHandler | None = None
        self._connection_handler: ConnectionHandler | None = None
        self._diagnostic_handler: DiagnosticHandler | None = None
        self._active: ControlTransport | None = None
        self._active_index: int | None = None
        self._manager_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._switch_requested = threading.Event()
        self._lock = threading.RLock()
        self._command_lock = threading.RLock()
        self._active_changed = threading.Condition(self._lock)
        self._activation_lock = threading.Lock()
        self._reported_connected = False
        self._statistics = _MutableFallbackStatistics()
        self._next_recovery_probe_at: float | None = None

    @property
    def endpoint(self) -> str:
        with self._lock:
            active = self._active
        if active is not None:
            return active.endpoint
        endpoints = ",".join(candidate.endpoint for candidate in self.candidates)
        return f"fallback://{endpoints}"

    @property
    def connected(self) -> bool:
        with self._lock:
            active = self._active
        return active is not None and active.connected

    @property
    def active_candidate(self) -> str | None:
        with self._lock:
            index = self._active_index
        return self.candidates[index].name if index is not None else None

    @property
    def statistics(self) -> Mapping[str, object]:
        with self._lock:
            active = self._active
            active_index = self._active_index
            policy = self.preferred_recovery_policy
            next_probe = self._next_recovery_probe_at
            values: dict[str, object] = {
                "preferred_candidate": self.candidates[0].name,
                "active_candidate": (
                    self.candidates[active_index].name
                    if active_index is not None
                    else None
                ),
                "active_endpoint": active.endpoint if active is not None else None,
                "candidate_count": len(self.candidates),
                "activation_attempts": self._statistics.activation_attempts,
                "activation_failures": self._statistics.activation_failures,
                "successful_activations": self._statistics.successful_activations,
                "failovers": self._statistics.failovers,
                "write_retries": self._statistics.write_retries,
                "reconnect_attempts": self._statistics.reconnect_attempts,
                "reconnect_failures": self._statistics.reconnect_failures,
                "reconnect_exhausted": self._statistics.reconnect_exhausted,
                "preferred_recovery_enabled": policy is not None,
                "preferred_recovery_probe_attempts": (
                    self._statistics.preferred_recovery_probe_attempts
                ),
                "preferred_recovery_probe_failures": (
                    self._statistics.preferred_recovery_probe_failures
                ),
                "preferred_recovery_deferred": (
                    self._statistics.preferred_recovery_deferred
                ),
                "preferred_recoveries": self._statistics.preferred_recoveries,
                "last_failure": self._statistics.last_failure,
                "last_failure_reason": self._statistics.last_failure_reason,
                "last_switch_from": self._statistics.last_switch_from,
                "last_switch_to": self._statistics.last_switch_to,
                "last_switch_at": self._isoformat(self._statistics.last_switch_at),
                "last_recovery_probe_at": self._isoformat(
                    self._statistics.last_recovery_probe_at
                ),
                "last_recovery_at": self._isoformat(
                    self._statistics.last_recovery_at
                ),
                "last_recovery_failure": self._statistics.last_recovery_failure,
                "next_recovery_probe_seconds": (
                    max(0.0, next_probe - monotonic())
                    if next_probe is not None
                    else None
                ),
            }
            if policy is not None:
                for name, policy_value in policy.as_dict().items():
                    values[f"preferred_recovery_{name}"] = policy_value
        if active is not None and isinstance(active, StatisticalControlTransport):
            for name, active_value in active.statistics.items():
                values[f"active_{name}"] = active_value
        return MappingProxyType(values)

    def set_recovery_guard(self, guard: RecoveryGuard | None) -> None:
        """Set a callback that must approve a preferred-transport promotion."""

        with self._lock:
            self._recovery_guard = guard or (lambda: True)

    def set_diagnostic_handler(
        self,
        handler: DiagnosticHandler | None,
    ) -> None:
        self._diagnostic_handler = handler
        with self._lock:
            active = self._active
        if active is not None and isinstance(active, DiagnosticControlTransport):
            active.set_diagnostic_handler(self._forward_diagnostic)

    def start(
        self,
        handler: LineHandler,
        connection_handler: ConnectionHandler | None = None,
    ) -> None:
        if self._manager_thread is not None and self._manager_thread.is_alive():
            return
        self._handler = handler
        self._connection_handler = connection_handler
        self._stop.clear()
        self._switch_requested.clear()
        self._next_recovery_probe_at = None

        if not self._activate_from(0, reason="initial connection"):
            raise ScannerConnectionError(
                "Could not connect using any configured scanner transport: "
                + ", ".join(candidate.endpoint for candidate in self.candidates)
            )

        self._manager_thread = threading.Thread(
            target=self._manager_loop,
            name="sds200-fallback-manager",
            daemon=True,
        )
        self._manager_thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._switch_requested.set()
        with self._command_lock:
            with self._active_changed:
                active, self._active = self._active, None
                self._active_index = None
                self._next_recovery_probe_at = None
                self._active_changed.notify_all()
            if active is not None:
                active.stop()

        thread = self._manager_thread
        if thread is not None and thread is not threading.current_thread():
            policy = self.preferred_recovery_policy
            probe_timeout = policy.probe_timeout if policy is not None else 0.0
            thread.join(timeout=max(1.0, self.retry_interval * 2, probe_timeout * 2))
        self._manager_thread = None
        self._forward_connection(False)

    def write_command(self, command: str) -> None:
        with self._command_lock:
            with self._lock:
                active = self._active
            if active is None:
                raise ScannerConnectionError("No scanner fallback transport is active.")

            try:
                active.write_command(command)
            except ScannerConnectionError as exc:
                self._request_switch(active, f"Write failed on {active.endpoint}: {exc}")
                replacement = self._wait_for_replacement(active)
                if replacement is None:
                    raise
                replacement.write_command(command)
                with self._lock:
                    self._statistics.write_retries += 1

    def _wait_for_replacement(
        self,
        previous: ControlTransport,
    ) -> ControlTransport | None:
        deadline = monotonic() + self.failover_timeout
        while not self._stop.is_set():
            remaining = deadline - monotonic()
            if remaining <= 0:
                return None
            with self._active_changed:
                active = self._active
                if active is not None and active is not previous and active.connected:
                    return active
                self._active_changed.wait(timeout=min(0.1, remaining))
        return None

    def _request_switch(self, active: ControlTransport, reason: str) -> None:
        with self._lock:
            if self._active is not active:
                return
            self._statistics.last_failure = reason
            self._statistics.last_failure_reason = reason
        self._forward_connection(False)
        self._emit_diagnostic(
            TransportDiagnostic(
                kind="failover_requested",
                endpoint=active.endpoint,
                message=reason,
            )
        )
        self._switch_requested.set()

    def _manager_loop(self) -> None:
        while not self._stop.is_set():
            timeout = self._recovery_wait_timeout()
            switch_requested = self._switch_requested.wait(timeout)
            if self._stop.is_set():
                return
            if switch_requested:
                self._switch_requested.clear()
                self._handle_failover()
                continue
            self._attempt_preferred_recovery()

    def _handle_failover(self) -> None:
        with self._active_changed:
            previous, self._active = self._active, None
            previous_index, self._active_index = self._active_index, None
            self._next_recovery_probe_at = None
            self._active_changed.notify_all()
        if previous is not None:
            previous.stop()

        start_index = (
            (previous_index + 1) % len(self.candidates)
            if previous_index is not None
            else 0
        )
        while not self._stop.is_set():
            if self._activate_from(start_index, reason="automatic failover"):
                self._reconnect_counter.reset()
                return
            scheduled = self._reconnect_counter.next()
            if scheduled is None:
                with self._lock:
                    self._statistics.reconnect_exhausted += 1
                self._emit_diagnostic(
                    TransportDiagnostic(
                        kind="reconnect_exhausted",
                        message=(
                            "Fallback reconnect policy exhausted after "
                            f"{self._reconnect_counter.attempts} attempts"
                        ),
                        attempt=self._reconnect_counter.attempts,
                    )
                )
                return
            attempt, delay = scheduled
            with self._lock:
                self._statistics.reconnect_attempts += 1
                self._statistics.reconnect_failures += 1
            self._emit_diagnostic(
                TransportDiagnostic(
                    kind="reconnect_scheduled",
                    message=(
                        "All scanner transport candidates failed; "
                        f"retry attempt {attempt} in {delay:.1f} seconds"
                    ),
                    attempt=attempt,
                    delay_seconds=delay,
                )
            )
            if self._stop.wait(delay):
                return
            start_index = 0

    def _activate_from(self, start_index: int, *, reason: str) -> bool:
        assert self._handler is not None
        with self._activation_lock:
            for offset in range(len(self.candidates)):
                if self._stop.is_set():
                    return False
                index = (start_index + offset) % len(self.candidates)
                candidate = self.candidates[index]
                with self._lock:
                    self._statistics.activation_attempts += 1
                self._emit_diagnostic(
                    TransportDiagnostic(
                        kind="activation_attempt",
                        endpoint=candidate.endpoint,
                        message=f"Trying {candidate.name} transport for {reason}",
                    )
                )

                transport = candidate.factory()
                if isinstance(transport, DiagnosticControlTransport):
                    transport.set_diagnostic_handler(self._forward_diagnostic)
                with self._active_changed:
                    self._active = transport
                    self._active_index = index
                    self._active_changed.notify_all()

                try:
                    transport.start(
                        self._handler,
                        partial(self._candidate_connection_changed, transport),
                    )
                    if not transport.connected:
                        raise ScannerConnectionError(
                            f"Transport {candidate.endpoint} did not remain connected."
                        )
                except (OSError, ScannerConnectionError) as exc:
                    with self._active_changed:
                        if self._active is transport:
                            self._active = None
                            self._active_index = None
                            self._active_changed.notify_all()
                    transport.stop()
                    message = f"Could not activate {candidate.endpoint}: {exc}"
                    with self._lock:
                        self._statistics.activation_failures += 1
                        self._statistics.last_failure = message
                    self._emit_diagnostic(
                        TransportDiagnostic(
                            kind="activation_failed",
                            endpoint=candidate.endpoint,
                            message=message,
                        )
                    )
                    continue

                with self._lock:
                    previous_endpoint = self._statistics.last_switch_to
                    if self._statistics.successful_activations > 0:
                        self._statistics.failovers += 1
                    self._statistics.successful_activations += 1
                    self._record_switch(previous_endpoint, transport.endpoint)
                    self._schedule_recovery_locked(index)
                self._forward_connection(True)
                self._emit_diagnostic(
                    TransportDiagnostic(
                        kind="transport_activated",
                        endpoint=transport.endpoint,
                        previous_endpoint=previous_endpoint,
                        message=f"Activated {candidate.name} transport",
                    )
                )
                return True
        return False

    def _attempt_preferred_recovery(self) -> None:
        policy = self.preferred_recovery_policy
        if policy is None or self._stop.is_set():
            return
        with self._lock:
            active = self._active
            active_index = self._active_index
        if active is None or active_index is None or active_index == 0:
            return

        if not self._recovery_guard():
            self._defer_recovery(
                active.endpoint,
                "Preferred recovery deferred while a scanner command is pending",
            )
            return

        for candidate_index in range(active_index):
            if self._stop.is_set():
                return
            candidate = self.candidates[candidate_index]
            probed = self._probe_candidate(candidate)
            if probed is None:
                continue
            if self._promote_recovered_transport(
                candidate_index,
                candidate,
                probed,
                expected_active=active,
            ):
                return
            probed.transport.stop()
            return
        self._schedule_next_recovery_probe()

    def _probe_candidate(self, candidate: TransportCandidate) -> _ProbedTransport | None:
        policy = self.preferred_recovery_policy
        assert policy is not None
        assert self._handler is not None

        with self._lock:
            self._statistics.preferred_recovery_probe_attempts += 1
            self._statistics.last_recovery_probe_at = datetime.now(UTC)
        self._emit_diagnostic(
            TransportDiagnostic(
                kind="preferred_recovery_probe",
                endpoint=candidate.endpoint,
                message=f"Probing preferred {candidate.name} transport",
                command=self.recovery_probe_command,
            )
        )

        transport = candidate.factory()
        promoted = threading.Event()
        responses: queue.Queue[str] = queue.Queue()
        disconnected = threading.Event()

        def receive(line: str) -> None:
            if promoted.is_set():
                assert self._handler is not None
                self._handler(line)
                return
            if self._recovery_probe_validator(line):
                responses.put(line)

        def connection_changed(connected: bool) -> None:
            if promoted.is_set():
                self._candidate_connection_changed(transport, connected)
                return
            if not connected:
                disconnected.set()

        if isinstance(transport, DiagnosticControlTransport):
            transport.set_diagnostic_handler(self._forward_diagnostic)

        try:
            transport.start(receive, connection_changed)
            if not transport.connected:
                raise ScannerConnectionError(
                    f"Transport {candidate.endpoint} did not remain connected."
                )
            self._send_probe_and_wait(
                transport,
                responses,
                disconnected,
                policy.probe_timeout,
            )
            if policy.stability_window > 0:
                self._wait_for_probe_stability(
                    transport,
                    disconnected,
                    policy.stability_window,
                )
                self._send_probe_and_wait(
                    transport,
                    responses,
                    disconnected,
                    policy.probe_timeout,
                )
            return _ProbedTransport(transport=transport, promoted=promoted)
        except (OSError, ScannerConnectionError) as exc:
            transport.stop()
            message = f"Preferred recovery probe failed for {candidate.endpoint}: {exc}"
            with self._lock:
                self._statistics.preferred_recovery_probe_failures += 1
                self._statistics.last_recovery_failure = message
            self._emit_diagnostic(
                TransportDiagnostic(
                    kind="preferred_recovery_probe_failed",
                    endpoint=candidate.endpoint,
                    message=message,
                    command=self.recovery_probe_command,
                )
            )
            return None

    def _send_probe_and_wait(
        self,
        transport: ControlTransport,
        responses: queue.Queue[str],
        disconnected: threading.Event,
        timeout: float,
    ) -> str:
        while not responses.empty():
            try:
                responses.get_nowait()
            except queue.Empty:
                break
        transport.write_command(self.recovery_probe_command)
        deadline = monotonic() + timeout
        while not self._stop.is_set():
            if disconnected.is_set() or not transport.connected:
                raise ScannerConnectionError(
                    f"Probe transport {transport.endpoint} disconnected."
                )
            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            try:
                return responses.get(timeout=min(0.05, remaining))
            except queue.Empty:
                continue
        if self._stop.is_set():
            raise ScannerConnectionError("Preferred recovery probe was stopped.")
        raise ScannerConnectionError(
            f"Timed out waiting for {self.recovery_probe_command} probe response."
        )

    def _wait_for_probe_stability(
        self,
        transport: ControlTransport,
        disconnected: threading.Event,
        stability_window: float,
    ) -> None:
        deadline = monotonic() + stability_window
        while not self._stop.is_set():
            if disconnected.is_set() or not transport.connected:
                raise ScannerConnectionError(
                    f"Probe transport {transport.endpoint} disconnected during "
                    "the stability window."
                )
            remaining = deadline - monotonic()
            if remaining <= 0:
                return
            self._stop.wait(min(0.05, remaining))
        raise ScannerConnectionError("Preferred recovery probe was stopped.")

    def _promote_recovered_transport(
        self,
        candidate_index: int,
        candidate: TransportCandidate,
        probed: _ProbedTransport,
        *,
        expected_active: ControlTransport,
    ) -> bool:
        with self._command_lock:
            if self._switch_requested.is_set():
                return False
            if not self._recovery_guard():
                self._defer_recovery(
                    candidate.endpoint,
                    "Preferred recovery deferred because scanner activity began "
                    "during the probe",
                )
                return False
            with self._active_changed:
                if (
                    self._stop.is_set()
                    or self._switch_requested.is_set()
                    or self._active is not expected_active
                    or self._active_index is None
                    or not expected_active.connected
                    or not probed.transport.connected
                ):
                    return False
                previous = expected_active
                previous_endpoint = previous.endpoint
                self._active = probed.transport
                self._active_index = candidate_index
                probed.promoted.set()
                self._statistics.successful_activations += 1
                self._statistics.preferred_recoveries += 1
                self._statistics.last_recovery_at = datetime.now(UTC)
                self._statistics.last_recovery_failure = None
                self._record_switch(previous_endpoint, probed.transport.endpoint)
                self._schedule_recovery_locked(candidate_index)
                self._active_changed.notify_all()
            previous.stop()

        self._emit_diagnostic(
            TransportDiagnostic(
                kind="preferred_recovery_succeeded",
                endpoint=probed.transport.endpoint,
                previous_endpoint=previous_endpoint,
                message=f"Recovered preferred {candidate.name} transport",
                command=self.recovery_probe_command,
            )
        )
        self._emit_diagnostic(
            TransportDiagnostic(
                kind="transport_activated",
                endpoint=probed.transport.endpoint,
                previous_endpoint=previous_endpoint,
                message=f"Activated recovered {candidate.name} transport",
            )
        )
        return True

    def _defer_recovery(self, endpoint: str, message: str) -> None:
        with self._lock:
            self._statistics.preferred_recovery_deferred += 1
        self._emit_diagnostic(
            TransportDiagnostic(
                kind="preferred_recovery_deferred",
                endpoint=endpoint,
                message=message,
            )
        )
        self._schedule_next_recovery_probe()

    def _schedule_recovery_locked(self, active_index: int) -> None:
        policy = self.preferred_recovery_policy
        if policy is None or active_index == 0:
            self._next_recovery_probe_at = None
            return
        delay = max(policy.cooldown, policy.probe_interval)
        self._next_recovery_probe_at = monotonic() + delay

    def _schedule_next_recovery_probe(self) -> None:
        policy = self.preferred_recovery_policy
        if policy is None:
            return
        with self._lock:
            if self._active_index in {None, 0}:
                self._next_recovery_probe_at = None
            else:
                self._next_recovery_probe_at = monotonic() + policy.probe_interval

    def _recovery_wait_timeout(self) -> float | None:
        with self._lock:
            next_probe = self._next_recovery_probe_at
        if next_probe is None:
            return None
        return max(0.0, next_probe - monotonic())

    def _candidate_connection_changed(
        self,
        transport: ControlTransport,
        connected: bool,
    ) -> None:
        with self._lock:
            if self._active is not transport:
                return
        if self._activation_lock.locked():
            return
        self._forward_connection(connected)
        if not connected and not self._stop.is_set():
            self._request_switch(
                transport,
                f"Scanner transport disconnected: {transport.endpoint}",
            )

    def _record_switch(self, previous_endpoint: str | None, endpoint: str) -> None:
        self._statistics.last_switch_at = datetime.now(UTC)
        self._statistics.last_switch_from = previous_endpoint
        self._statistics.last_switch_to = endpoint

    def _forward_connection(self, connected: bool) -> None:
        with self._lock:
            if self._reported_connected == connected:
                return
            self._reported_connected = connected
            handler = self._connection_handler
        if handler is None:
            return
        try:
            handler(connected)
        except Exception:
            logger.exception("Unhandled exception in fallback connection callback")

    def _forward_diagnostic(self, diagnostic: TransportDiagnostic) -> None:
        self._emit_diagnostic(diagnostic)

    def _emit_diagnostic(self, diagnostic: TransportDiagnostic) -> None:
        handler = self._diagnostic_handler
        if handler is None:
            return
        try:
            handler(diagnostic)
        except Exception:
            logger.exception("Unhandled exception in fallback diagnostic callback")

    @staticmethod
    def _default_recovery_probe_validator(line: str) -> bool:
        command, separator, value = line.strip().partition(",")
        return command.upper() == "MDL" and bool(separator and value.strip())

    @staticmethod
    def _isoformat(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None
