from __future__ import annotations

import logging
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
    last_failure: str | None = None
    last_failure_reason: str | None = None
    last_switch_at: datetime | None = None
    last_switch_from: str | None = None
    last_switch_to: str | None = None


class FallbackTransport:
    """Control transport that switches between ordered candidates.

    Candidate transports must have their own reconnect loops disabled. This
    coordinator owns reconnect and failover so a failed preferred transport
    cannot block use of the next candidate indefinitely.
    """

    def __init__(
        self,
        candidates: Sequence[TransportCandidate],
        *,
        retry_interval: float = 2.0,
        failover_timeout: float = 3.0,
        reconnect_policy: ReconnectPolicy | None = None,
    ) -> None:
        if not candidates:
            raise ValueError("At least one fallback transport candidate is required.")
        if retry_interval <= 0:
            raise ValueError("Fallback retry interval must be positive.")
        if failover_timeout <= 0:
            raise ValueError("Fallback timeout must be positive.")

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
        self._active_changed = threading.Condition(self._lock)
        self._activation_lock = threading.Lock()
        self._reported_connected = False
        self._statistics = _MutableFallbackStatistics()

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
                "last_failure": self._statistics.last_failure,
                "last_failure_reason": self._statistics.last_failure_reason,
                "last_switch_from": self._statistics.last_switch_from,
                "last_switch_to": self._statistics.last_switch_to,
                "last_switch_at": (
                    self._statistics.last_switch_at.isoformat()
                    if self._statistics.last_switch_at is not None
                    else None
                ),
            }
        if active is not None and isinstance(active, StatisticalControlTransport):
            for name, value in active.statistics.items():
                values[f"active_{name}"] = value
        return MappingProxyType(values)

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
        with self._active_changed:
            active, self._active = self._active, None
            self._active_index = None
            self._active_changed.notify_all()
        if active is not None:
            active.stop()

        thread = self._manager_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(1.0, self.retry_interval * 2))
        self._manager_thread = None
        self._forward_connection(False)

    def write_command(self, command: str) -> None:
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
            self._switch_requested.wait()
            self._switch_requested.clear()
            if self._stop.is_set():
                return

            with self._active_changed:
                previous, self._active = self._active, None
                previous_index, self._active_index = self._active_index, None
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
                    break
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
                    self._statistics.last_switch_at = datetime.now(UTC)
                    self._statistics.last_switch_from = previous_endpoint
                    self._statistics.last_switch_to = transport.endpoint
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
