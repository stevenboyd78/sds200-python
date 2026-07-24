from __future__ import annotations

import threading
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass

from .models import HealthSummary, RadioHealth


@dataclass(frozen=True, slots=True)
class ReconnectPolicy:
    """Deterministic exponential-backoff policy for transport recovery.

    ``max_attempts=None`` means retry indefinitely. An attempt is one transport
    reopen or one complete fallback sweep after a live disconnect.
    """

    initial_delay: float = 1.0
    multiplier: float = 2.0
    max_delay: float = 30.0
    max_attempts: int | None = None

    def __post_init__(self) -> None:
        if self.initial_delay <= 0:
            raise ValueError("Reconnect initial delay must be greater than zero.")
        if self.multiplier < 1:
            raise ValueError("Reconnect multiplier must be at least 1.")
        if self.max_delay < self.initial_delay:
            raise ValueError(
                "Reconnect maximum delay must be at least the initial delay."
            )
        if self.max_attempts is not None and self.max_attempts < 1:
            raise ValueError("Reconnect maximum attempts must be positive or None.")

    def allows(self, attempt: int) -> bool:
        if attempt < 1:
            raise ValueError("Reconnect attempt numbers start at 1.")
        return self.max_attempts is None or attempt <= self.max_attempts

    def delay_for(self, attempt: int) -> float:
        if attempt < 1:
            raise ValueError("Reconnect attempt numbers start at 1.")
        delay = self.initial_delay * self.multiplier ** (attempt - 1)
        return min(delay, self.max_delay)

    def as_dict(self) -> dict[str, object]:
        return {
            "initial_delay": self.initial_delay,
            "multiplier": self.multiplier,
            "max_delay": self.max_delay,
            "max_attempts": self.max_attempts,
        }


class ReconnectCounter:
    """Thread-safe attempt counter shared by transport reconnect loops."""

    def __init__(self, policy: ReconnectPolicy) -> None:
        self.policy = policy
        self._attempts = 0
        self._lock = threading.Lock()

    @property
    def attempts(self) -> int:
        with self._lock:
            return self._attempts

    def reset(self) -> None:
        with self._lock:
            self._attempts = 0

    def next(self) -> tuple[int, float] | None:
        with self._lock:
            attempt = self._attempts + 1
            if not self.policy.allows(attempt):
                return None
            self._attempts = attempt
        return attempt, self.policy.delay_for(attempt)


class HealthHistory:
    """Bounded, thread-safe history of radio health observations."""

    def __init__(self, limit: int = 100) -> None:
        if limit < 1:
            raise ValueError("Health history limit must be positive.")
        self.limit = limit
        self._snapshots: deque[RadioHealth] = deque(maxlen=limit)
        self._lock = threading.RLock()

    def record(self, health: RadioHealth) -> RadioHealth:
        with self._lock:
            self._snapshots.append(health)
        return health

    def clear(self) -> None:
        with self._lock:
            self._snapshots.clear()

    def snapshots(self) -> tuple[RadioHealth, ...]:
        with self._lock:
            return tuple(self._snapshots)

    def summary(self) -> HealthSummary:
        snapshots = self.snapshots()
        if not snapshots:
            return HealthSummary.empty()

        latencies = [
            snapshot.latency_ms
            for snapshot in snapshots
            if snapshot.latency_ms is not None
        ]
        healthy = sum(snapshot.status == "healthy" for snapshot in snapshots)
        degraded = sum(snapshot.status == "degraded" for snapshot in snapshots)
        unhealthy = sum(snapshot.status == "unhealthy" for snapshot in snapshots)
        disconnected = sum(snapshot.status == "disconnected" for snapshot in snapshots)
        failures = degraded + unhealthy + disconnected
        first = snapshots[0]
        last = snapshots[-1]
        reconnects = _latest_statistic(snapshots, ("reconnects", "active_reconnects"))
        failovers = _latest_statistic(snapshots, ("failovers",))

        recent_errors = tuple(
            snapshot.error
            for snapshot in reversed(snapshots)
            if snapshot.error is not None
        )[:5]
        return HealthSummary.create(
            samples=len(snapshots),
            healthy_samples=healthy,
            degraded_samples=degraded,
            unhealthy_samples=unhealthy,
            disconnected_samples=disconnected,
            error_rate=failures / len(snapshots),
            average_latency_ms=(sum(latencies) / len(latencies) if latencies else None),
            maximum_latency_ms=max(latencies) if latencies else None,
            first_checked_at=first.checked_at,
            last_checked_at=last.checked_at,
            connection_events_delta=max(
                0, last.connection_events - first.connection_events
            ),
            reconnects=reconnects,
            failovers=failovers,
            recent_errors=recent_errors,
        )


def _latest_statistic(
    snapshots: Sequence[RadioHealth],
    names: Sequence[str],
) -> int:
    for snapshot in reversed(snapshots):
        for name in names:
            value = snapshot.statistics.get(name)
            if isinstance(value, int):
                return value
    return 0


@dataclass(frozen=True, slots=True)
class HealthThresholds:
    degraded_latency_ms: float = 750.0
    unhealthy_latency_ms: float = 2000.0

    def __post_init__(self) -> None:
        if self.degraded_latency_ms <= 0:
            raise ValueError("Degraded latency threshold must be positive.")
        if self.unhealthy_latency_ms < self.degraded_latency_ms:
            raise ValueError(
                "Unhealthy latency threshold must not be below degraded latency."
            )

    def classify(
        self,
        *,
        connected: bool,
        latency_ms: float | None,
        error: str | None,
    ) -> str:
        if not connected:
            return "disconnected"
        if error is not None:
            return "degraded"
        if latency_ms is not None and latency_ms >= self.unhealthy_latency_ms:
            return "unhealthy"
        if latency_ms is not None and latency_ms >= self.degraded_latency_ms:
            return "degraded"
        return "healthy"
