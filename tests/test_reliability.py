from datetime import UTC, datetime, timedelta

import pytest

from sds200.models import RadioHealth
from sds200.reliability import (
    HealthHistory,
    HealthThresholds,
    ReconnectCounter,
    ReconnectPolicy,
)


def test_reconnect_policy_uses_capped_exponential_backoff() -> None:
    policy = ReconnectPolicy(
        initial_delay=0.5,
        multiplier=2.0,
        max_delay=2.0,
        max_attempts=4,
    )

    assert [policy.delay_for(attempt) for attempt in range(1, 5)] == [
        0.5,
        1.0,
        2.0,
        2.0,
    ]
    assert policy.allows(4)
    assert not policy.allows(5)


def test_reconnect_counter_honors_limit_and_reset() -> None:
    counter = ReconnectCounter(
        ReconnectPolicy(
            initial_delay=0.01,
            multiplier=1.0,
            max_delay=0.01,
            max_attempts=2,
        )
    )

    assert counter.next() == (1, 0.01)
    assert counter.next() == (2, 0.01)
    assert counter.next() is None

    counter.reset()

    assert counter.next() == (1, 0.01)


def test_reconnect_policy_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="initial delay"):
        ReconnectPolicy(initial_delay=0)
    with pytest.raises(ValueError, match="multiplier"):
        ReconnectPolicy(multiplier=0.5)
    with pytest.raises(ValueError, match="maximum delay"):
        ReconnectPolicy(initial_delay=2, max_delay=1)
    with pytest.raises(ValueError, match="maximum attempts"):
        ReconnectPolicy(max_attempts=0)


def test_health_history_is_bounded_and_summarizes_failures() -> None:
    history = HealthHistory(limit=3)
    first_at = datetime.now(UTC)

    for index, status in enumerate(("healthy", "degraded", "disconnected", "healthy")):
        history.record(
            RadioHealth.create(
                endpoint="fake://scanner",
                connected=status != "disconnected",
                model="SDS200",
                firmware="1.26.01",
                latency_ms=10.0 + index,
                status=status,
                connection_events=index,
                error="timeout" if status == "degraded" else None,
                statistics={"reconnects": index, "failovers": index // 2},
                checked_at=first_at + timedelta(seconds=index),
            )
        )

    snapshots = history.snapshots()
    summary = history.summary()

    assert len(snapshots) == 3
    assert summary.samples == 3
    assert summary.healthy_samples == 1
    assert summary.degraded_samples == 1
    assert summary.disconnected_samples == 1
    assert summary.error_rate == pytest.approx(2 / 3)
    assert summary.reconnects == 3
    assert summary.failovers == 1
    assert summary.recent_errors == ("timeout",)


def test_health_thresholds_classify_latency_and_connection() -> None:
    thresholds = HealthThresholds(
        degraded_latency_ms=100.0,
        unhealthy_latency_ms=500.0,
    )

    assert thresholds.classify(connected=False, latency_ms=None, error=None) == "disconnected"
    assert thresholds.classify(connected=True, latency_ms=50.0, error=None) == "healthy"
    assert thresholds.classify(connected=True, latency_ms=100.0, error=None) == "degraded"
    assert thresholds.classify(connected=True, latency_ms=500.0, error=None) == "unhealthy"
    assert thresholds.classify(connected=True, latency_ms=50.0, error="bad") == "degraded"
