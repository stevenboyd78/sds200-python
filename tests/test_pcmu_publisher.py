from __future__ import annotations

import json
import queue
import threading
from datetime import UTC, datetime

import pytest

from sds200.pcmu import PcmuPacket
from sds200.pcmu_subscriptions import (
    PcmuPublisher,
    PcmuSubscriptionClosed,
)


def make_packet(
    sequence: int,
    payload: bytes = b"audio",
) -> PcmuPacket:
    return PcmuPacket(
        endpoint="rtsp://192.0.2.25/au:scanner.au",
        sequence=sequence & 0xFFFF,
        timestamp=(sequence * len(payload)) & 0xFFFFFFFF,
        ssrc=5678,
        payload=payload,
        observed_at=datetime(2026, 8, 5, 7, 30, tzinfo=UTC),
    )


def test_publisher_delivers_one_ordered_publication_to_each_subscriber() -> None:
    publisher = PcmuPublisher()
    first = publisher.subscribe()
    second = publisher.subscribe()
    packet = make_packet(10)

    publication = publisher.publish(packet)
    first_delivery = first.get(timeout=0)
    second_delivery = second.get(timeout=0)

    assert publication.stream_sequence == 1
    assert publication.packet is packet
    assert first_delivery.publication is publication
    assert second_delivery.publication is publication
    assert first_delivery.packet is packet
    assert first_delivery.packets_dropped == 0
    assert first_delivery.health == "healthy"


def test_new_subscription_starts_with_the_next_publication() -> None:
    publisher = PcmuPublisher()
    publisher.publish(make_packet(10))
    subscription = publisher.subscribe()

    with pytest.raises(queue.Empty):
        subscription.get(timeout=0)

    publication = publisher.publish(make_packet(11))

    assert publication.stream_sequence == 2
    assert subscription.get(timeout=0).publication is publication


def test_overflow_drops_oldest_and_reports_cumulative_queue_loss() -> None:
    publisher = PcmuPublisher(queue_capacity=2)
    subscription = publisher.subscribe()

    publisher.publish(make_packet(10, b"aa"))
    publisher.publish(make_packet(11, b"bbb"))
    latest = publisher.publish(make_packet(12, b"cccc"))

    snapshot = subscription.snapshot()
    assert snapshot.health == "degraded"
    assert snapshot.queued_packets == 2
    assert snapshot.queued_payload_bytes == 7
    assert snapshot.packets_dropped == 1
    assert snapshot.payload_bytes_dropped == 2
    assert snapshot.overflows == 1
    assert snapshot.last_dropped_stream_sequence == 1

    first = subscription.get(timeout=0)
    second = subscription.get(timeout=0)

    assert first.packet.sequence == 11
    assert first.packets_dropped == 1
    assert first.payload_bytes_dropped == 2
    assert first.overflows == 1
    assert first.health == "degraded"

    assert second.publication is latest

    drained = subscription.snapshot()
    assert drained.queued_packets == 0
    assert drained.queued_payload_bytes == 0
    assert drained.packets_delivered == 2
    assert drained.payload_bytes_delivered == 7
    assert drained.last_delivered_stream_sequence == 3


def test_slow_subscription_does_not_drop_or_delay_healthy_subscription() -> None:
    publisher = PcmuPublisher(queue_capacity=2)
    slow = publisher.subscribe()
    healthy = publisher.subscribe()
    observed = []

    for sequence in range(10, 15):
        publication = publisher.publish(make_packet(sequence))
        delivery = healthy.get(timeout=0)
        assert delivery.publication is publication
        observed.append(delivery)

    assert [item.stream_sequence for item in observed] == [1, 2, 3, 4, 5]
    assert healthy.snapshot().packets_dropped == 0

    slow_delivery = slow.get(timeout=0)
    assert slow_delivery.stream_sequence == 4
    assert slow_delivery.packets_dropped == 3
    assert slow_delivery.overflows == 3
    assert slow.snapshot().health == "degraded"


def test_subscription_timeout_uses_queue_empty() -> None:
    subscription = PcmuPublisher().subscribe()

    with pytest.raises(queue.Empty):
        subscription.get(timeout=0.01)


@pytest.mark.parametrize(
    ("timeout", "error"),
    [
        (-0.01, ValueError),
        (float("inf"), ValueError),
        (float("nan"), ValueError),
        (True, TypeError),
        ("1", TypeError),
    ],
)
def test_subscription_rejects_invalid_timeout(
    timeout: object,
    error: type[Exception],
) -> None:
    subscription = PcmuPublisher().subscribe()

    with pytest.raises(error, match="timeout"):
        subscription.get(timeout=timeout)  # type: ignore[arg-type]


def test_subscription_close_is_idempotent_and_removes_subscriber() -> None:
    publisher = PcmuPublisher()
    subscription = publisher.subscribe()

    subscription.close()
    subscription.close()

    assert subscription.closed
    assert subscription.snapshot().health == "closed"
    assert publisher.subscriber_count == 0

    with pytest.raises(PcmuSubscriptionClosed):
        subscription.get(timeout=0)

    publication = publisher.publish(make_packet(10))
    assert publication.stream_sequence == 1


def test_publisher_close_wakes_blocked_subscription() -> None:
    publisher = PcmuPublisher()
    subscription = publisher.subscribe()
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
    assert isinstance(outcomes[0], PcmuSubscriptionClosed)
    assert publisher.closed
    assert publisher.subscriber_count == 0
    assert subscription.snapshot().health == "closed"

    publisher.close()

    with pytest.raises(RuntimeError, match="closed"):
        publisher.publish(make_packet(10))
    with pytest.raises(RuntimeError, match="closed"):
        publisher.subscribe()


def test_publisher_enforces_subscriber_limit() -> None:
    publisher = PcmuPublisher(max_subscribers=1)
    first = publisher.subscribe()

    with pytest.raises(RuntimeError, match="maximum"):
        publisher.subscribe()

    first.close()
    replacement = publisher.subscribe()

    assert publisher.subscriber_count == 1
    replacement.close()


@pytest.mark.parametrize(
    ("keyword", "value", "error"),
    [
        ("queue_capacity", True, TypeError),
        ("queue_capacity", 0, ValueError),
        ("max_subscribers", True, TypeError),
        ("max_subscribers", 0, ValueError),
        ("max_payload_bytes", True, TypeError),
        ("max_payload_bytes", 0, ValueError),
    ],
)
def test_publisher_rejects_invalid_bounds(
    keyword: str,
    value: object,
    error: type[Exception],
) -> None:
    with pytest.raises(error):
        PcmuPublisher(**{keyword: value})  # type: ignore[arg-type]


def test_publisher_rejects_non_packet_without_advancing_sequence() -> None:
    publisher = PcmuPublisher()

    with pytest.raises(TypeError, match="PcmuPacket"):
        publisher.publish(object())  # type: ignore[arg-type]

    assert publisher.stream_sequence == 0
    assert publisher.snapshot().packets_published == 0


def test_publisher_rejects_oversized_payload_without_delivery() -> None:
    publisher = PcmuPublisher(max_payload_bytes=4)
    subscription = publisher.subscribe()

    with pytest.raises(ValueError, match="maximum size"):
        publisher.publish(make_packet(10, b"12345"))

    assert publisher.stream_sequence == 0
    assert publisher.snapshot().packets_published == 0
    with pytest.raises(queue.Empty):
        subscription.get(timeout=0)


def test_snapshots_are_immutable_json_compatible_state() -> None:
    publisher = PcmuPublisher(
        queue_capacity=3,
        max_subscribers=2,
        max_payload_bytes=512,
    )
    subscription = publisher.subscribe()
    publisher.publish(make_packet(10, b"abcd"))
    delivery = subscription.get(timeout=0)

    publisher_payload = publisher.snapshot().as_dict()
    subscription_payload = subscription.snapshot().as_dict()

    assert json.loads(json.dumps(publisher_payload)) == publisher_payload
    assert publisher_payload == {
        "closed": False,
        "queue_capacity": 3,
        "max_subscribers": 2,
        "max_payload_bytes": 512,
        "subscriber_count": 1,
        "packets_published": 1,
        "payload_bytes_published": 4,
        "last_stream_sequence": 1,
    }
    assert subscription_payload["health"] == "healthy"
    assert subscription_payload["packets_delivered"] == 1
    assert subscription_payload["payload_bytes_delivered"] == 4
    assert subscription_payload["last_delivered_stream_sequence"] == 1
    assert delivery.health == "healthy"


def test_concurrent_publishers_preserve_global_delivery_order() -> None:
    publisher = PcmuPublisher(queue_capacity=128)
    subscription = publisher.subscribe()
    barrier = threading.Barrier(5)

    def publish_batch(worker: int) -> None:
        barrier.wait()
        for index in range(25):
            publisher.publish(
                make_packet(worker * 100 + index)
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

    deliveries = [
        subscription.get(timeout=0.1)
        for _ in range(100)
    ]

    assert [item.stream_sequence for item in deliveries] == list(
        range(1, 101)
    )
    assert subscription.snapshot().packets_dropped == 0
    assert publisher.snapshot().packets_published == 100
