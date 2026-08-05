from __future__ import annotations

import queue
import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta

import pytest

from sds200 import (
    DaemonEventKind,
    DaemonEventPublisher,
    DaemonEventSubscriptionClosed,
)


def make_publisher(
    snapshot: Callable[[], Mapping[str, object]] | None = None,
    *,
    queue_capacity: int = 4,
    max_subscribers: int = 8,
    now: Callable[[], datetime] | None = None,
) -> DaemonEventPublisher:
    return DaemonEventPublisher(
        snapshot or (lambda: {"state": "idle"}),
        queue_capacity=queue_capacity,
        max_subscribers=max_subscribers,
        **({} if now is None else {"now": now}),
    )


def test_subscription_starts_with_authoritative_snapshot_checkpoint() -> None:
    state: dict[str, object] = {"state": "idle"}
    publisher = make_publisher(lambda: state)
    subscription = publisher.subscribe()

    state["state"] = "changed-after-subscribe"
    snapshot = subscription.get(timeout=0)

    assert snapshot.sequence == 0
    assert snapshot.kind == DaemonEventKind.SNAPSHOT
    assert snapshot.payload == {"state": "idle"}

    event = publisher.publish(
        DaemonEventKind.DAEMON_TRANSITION,
        {"state": "running"},
    )

    assert event.sequence == 1
    assert subscription.get(timeout=0) is event


def test_later_subscriber_snapshot_uses_current_global_checkpoint() -> None:
    state: dict[str, object] = {"state": "idle"}
    publisher = make_publisher(lambda: state)
    first = publisher.subscribe()

    assert first.get(timeout=0).sequence == 0

    first_event = publisher.publish(
        DaemonEventKind.DAEMON_TRANSITION,
        {"state": "starting"},
    )
    state["state"] = "starting"

    second = publisher.subscribe()
    second_snapshot = second.get(timeout=0)

    assert second_snapshot.sequence == first_event.sequence == 1
    assert second_snapshot.payload == {"state": "starting"}

    second_event = publisher.publish(
        DaemonEventKind.DAEMON_TRANSITION,
        {"state": "running"},
    )

    assert first.get(timeout=0) is first_event
    assert first.get(timeout=0) is second_event
    assert second.get(timeout=0) is second_event


def test_overflow_preserves_unread_snapshot_and_exposes_sequence_gap() -> None:
    publisher = make_publisher(queue_capacity=2)
    subscription = publisher.subscribe()

    for index in range(1, 4):
        publisher.publish(
            DaemonEventKind.RADIO_STATE,
            {"index": index},
        )

    snapshot = subscription.get(timeout=0)
    latest = subscription.get(timeout=0)

    assert snapshot.kind == DaemonEventKind.SNAPSHOT
    assert snapshot.sequence == 0
    assert latest.sequence == 3
    assert latest.payload == {"index": 3}
    assert subscription.dropped_events == 2


def test_slow_subscription_does_not_delay_or_drop_healthy_subscription() -> None:
    publisher = make_publisher(queue_capacity=2)
    slow = publisher.subscribe()
    healthy = publisher.subscribe()

    assert healthy.get(timeout=0).sequence == 0

    observed = []
    for index in range(1, 6):
        published = publisher.publish(
            DaemonEventKind.RADIO_STATE,
            {"index": index},
        )
        observed.append(healthy.get(timeout=0))
        assert observed[-1] is published

    assert [event.sequence for event in observed] == [1, 2, 3, 4, 5]
    assert healthy.dropped_events == 0

    assert slow.get(timeout=0).sequence == 0
    assert slow.get(timeout=0).sequence == 5
    assert slow.dropped_events == 4


def test_consumed_snapshot_allows_oldest_event_eviction() -> None:
    publisher = make_publisher(queue_capacity=2)
    subscription = publisher.subscribe()

    assert subscription.get(timeout=0).sequence == 0

    for index in range(1, 4):
        publisher.publish(
            DaemonEventKind.RADIO_STATE,
            {"index": index},
        )

    assert subscription.get(timeout=0).sequence == 2
    assert subscription.get(timeout=0).sequence == 3
    assert subscription.dropped_events == 1


def test_subscription_timeout_uses_queue_empty() -> None:
    publisher = make_publisher()
    subscription = publisher.subscribe()
    subscription.get(timeout=0)

    with pytest.raises(queue.Empty):
        subscription.get(timeout=0.01)


def test_subscription_rejects_negative_timeout() -> None:
    publisher = make_publisher()
    subscription = publisher.subscribe()

    with pytest.raises(ValueError, match="timeout"):
        subscription.get(timeout=-0.01)


@pytest.mark.parametrize("timeout", [float("inf"), float("nan")])
def test_subscription_rejects_nonfinite_timeout(timeout: float) -> None:
    publisher = make_publisher()
    subscription = publisher.subscribe()

    with pytest.raises(ValueError, match="finite"):
        subscription.get(timeout=timeout)


@pytest.mark.parametrize("timeout", [True, "1"])
def test_subscription_rejects_non_numeric_timeout(timeout: object) -> None:
    publisher = make_publisher()
    subscription = publisher.subscribe()

    with pytest.raises(TypeError, match="timeout"):
        subscription.get(timeout=timeout)  # type: ignore[arg-type]


def test_subscription_close_is_idempotent_and_removes_it_from_publisher() -> None:
    publisher = make_publisher()
    subscription = publisher.subscribe()

    assert publisher.subscriber_count == 1

    subscription.close()
    subscription.close()

    assert subscription.closed
    assert publisher.subscriber_count == 0

    with pytest.raises(DaemonEventSubscriptionClosed):
        subscription.get(timeout=0)

    event = publisher.publish(
        DaemonEventKind.RADIO_STATE,
        {"channel": "Primary"},
    )
    assert event.sequence == 1


def test_publisher_close_wakes_blocked_subscriptions() -> None:
    publisher = make_publisher()
    subscription = publisher.subscribe()
    subscription.get(timeout=0)
    outcomes: list[BaseException] = []

    def receive() -> None:
        try:
            subscription.get()
        except BaseException as error:
            outcomes.append(error)

    thread = threading.Thread(target=receive)
    thread.start()
    publisher.close()
    thread.join(timeout=2.0)

    assert not thread.is_alive()
    assert len(outcomes) == 1
    assert isinstance(outcomes[0], DaemonEventSubscriptionClosed)
    assert publisher.closed
    assert subscription.closed
    assert publisher.subscriber_count == 0

    publisher.close()

    with pytest.raises(RuntimeError, match="closed"):
        publisher.publish(DaemonEventKind.RADIO_STATE, {})

    with pytest.raises(RuntimeError, match="closed"):
        publisher.subscribe()


def test_publisher_enforces_subscriber_limit() -> None:
    publisher = make_publisher(max_subscribers=1)
    first = publisher.subscribe()

    with pytest.raises(RuntimeError, match="maximum"):
        publisher.subscribe()

    first.close()
    replacement = publisher.subscribe()

    assert publisher.subscriber_count == 1
    replacement.close()


def test_failed_snapshot_does_not_leak_subscription() -> None:
    def fail_snapshot() -> Mapping[str, object]:
        raise RuntimeError("snapshot failed")

    publisher = make_publisher(fail_snapshot)

    with pytest.raises(RuntimeError, match="snapshot failed"):
        publisher.subscribe()

    assert publisher.sequence == 0
    assert publisher.subscriber_count == 0


def test_failed_publish_does_not_advance_sequence() -> None:
    publisher = make_publisher()
    subscription = publisher.subscribe()
    subscription.get(timeout=0)

    with pytest.raises(TypeError, match="JSON-compatible"):
        publisher.publish(
            DaemonEventKind.RADIO_STATE,
            {"value": object()},
        )

    assert publisher.sequence == 0

    event = publisher.publish(
        DaemonEventKind.RADIO_STATE,
        {"channel": "Primary"},
    )

    assert event.sequence == 1
    assert subscription.get(timeout=0) is event


def test_snapshot_kind_is_reserved_for_subscription_initialization() -> None:
    publisher = make_publisher()

    with pytest.raises(ValueError, match="reserved"):
        publisher.publish(
            DaemonEventKind.SNAPSHOT,
            {"state": "idle"},
        )

    assert publisher.sequence == 0


@pytest.mark.parametrize(
    ("queue_capacity", "max_subscribers"),
    [
        (-1, 1),
        (0, 1),
        (1, 1),
        (2, 0),
        (2, -1),
    ],
)
def test_publisher_rejects_invalid_bounds(
    queue_capacity: int,
    max_subscribers: int,
) -> None:
    with pytest.raises(ValueError):
        make_publisher(
            queue_capacity=queue_capacity,
            max_subscribers=max_subscribers,
        )


@pytest.mark.parametrize(
    ("queue_capacity", "max_subscribers"),
    [
        (True, 1),
        (2.5, 1),
        ("2", 1),
        (2, True),
        (2, 1.5),
        (2, "1"),
    ],
)
def test_publisher_rejects_non_integer_bounds(
    queue_capacity: object,
    max_subscribers: object,
) -> None:
    with pytest.raises(TypeError, match="integer"):
        make_publisher(
            queue_capacity=queue_capacity,  # type: ignore[arg-type]
            max_subscribers=max_subscribers,  # type: ignore[arg-type]
        )


def test_publisher_uses_ordered_aware_timestamps() -> None:
    initial = datetime(2026, 8, 4, 22, 45, tzinfo=UTC)
    timestamps = iter(
        initial + timedelta(seconds=offset)
        for offset in range(2)
    )
    publisher = make_publisher(now=lambda: next(timestamps))
    subscription = publisher.subscribe()

    snapshot = subscription.get(timeout=0)
    event = publisher.publish(
        DaemonEventKind.SCANNER_CONNECTION,
        {"connected": True},
    )

    assert snapshot.observed_at == initial
    assert event.observed_at == initial + timedelta(seconds=1)


def test_concurrent_publishers_preserve_global_delivery_order() -> None:
    publisher = make_publisher(queue_capacity=128)
    subscription = publisher.subscribe()
    subscription.get(timeout=0)
    barrier = threading.Barrier(5)

    def publish_batch(worker: int) -> None:
        barrier.wait()
        for index in range(25):
            publisher.publish(
                DaemonEventKind.RADIO_STATE,
                {"worker": worker, "index": index},
            )

    threads = [
        threading.Thread(target=publish_batch, args=(worker,))
        for worker in range(4)
    ]
    for thread in threads:
        thread.start()

    barrier.wait()

    for thread in threads:
        thread.join(timeout=2.0)

    assert all(not thread.is_alive() for thread in threads)

    events = [
        subscription.get(timeout=0.1)
        for _ in range(100)
    ]

    assert [event.sequence for event in events] == list(range(1, 101))
    assert subscription.dropped_events == 0
