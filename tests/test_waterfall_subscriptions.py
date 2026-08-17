import json
import queue
import threading

import pytest

from sds200.models import GwfResponse, PwfResponse
from sds200.parser import PacketParser
from sds200.waterfall_subscriptions import (
    WATERFALL_DEFAULT_QUEUE_CAPACITY,
    WaterfallPublisher,
    WaterfallSubscriptionClosed,
)


def response(identifier: str) -> PwfResponse:
    parser = PacketParser()
    parsed = parser.parse_typed(
        parser.parse_packet(f"PWF,{identifier},,FUTURE")
    )
    assert isinstance(parsed, PwfResponse)
    return parsed


def gwf_response() -> GwfResponse:
    parser = PacketParser()
    values = tuple(str(index) for index in range(240))
    parsed = parser.parse_typed(
        parser.parse_packet("GWF," + ",".join(values))
    )
    assert isinstance(parsed, GwfResponse)
    return parsed


def test_ordered_publication_to_multiple_subscribers() -> None:
    publisher = WaterfallPublisher()
    first = publisher.subscribe()
    second = publisher.subscribe()

    publications = [publisher.publish(response(str(index))) for index in range(3)]

    assert [first.get(0).publication for _ in publications] == publications
    assert [second.get(0).publication for _ in publications] == publications
    assert [publication.sequence for publication in publications] == [1, 2, 3]


def test_new_subscription_begins_empty() -> None:
    publisher = WaterfallPublisher()
    publisher.publish(response("before"))
    subscription = publisher.subscribe()

    with pytest.raises(queue.Empty):
        subscription.get(0)


def test_overflow_drops_oldest_and_exposes_cumulative_loss() -> None:
    publisher = WaterfallPublisher(queue_capacity=2)
    subscription = publisher.subscribe()
    for index in range(4):
        publisher.publish(response(str(index)))

    first = subscription.get(0)
    second = subscription.get(0)
    snapshot = subscription.snapshot()

    assert (first.sequence, second.sequence) == (3, 4)
    assert (first.responses_dropped, first.overflows, first.health) == (2, 2, "degraded")
    assert (second.responses_dropped, second.overflows) == (2, 2)
    assert snapshot.responses_delivered == 2
    assert snapshot.last_delivered_sequence == 4
    assert snapshot.last_dropped_sequence == 2
    assert snapshot.health == "degraded"


def test_slow_subscriber_cannot_delay_or_drop_healthy_subscriber() -> None:
    publisher = WaterfallPublisher(queue_capacity=1)
    slow = publisher.subscribe()
    healthy = publisher.subscribe()

    publisher.publish(response("first"))
    assert healthy.get(0).sequence == 1
    publisher.publish(response("second"))

    assert healthy.get(0).sequence == 2
    assert healthy.snapshot().responses_dropped == 0
    assert slow.get(0).sequence == 2
    assert slow.snapshot().responses_dropped == 1


@pytest.mark.parametrize("timeout", [True, False, "1", object()])
def test_timeout_rejects_non_numeric_values(timeout: object) -> None:
    subscription = WaterfallPublisher().subscribe()
    with pytest.raises(TypeError):
        subscription.get(timeout)  # type: ignore[arg-type]


@pytest.mark.parametrize("timeout", [-1, float("inf"), float("-inf"), float("nan")])
def test_timeout_rejects_negative_or_non_finite_values(timeout: float) -> None:
    subscription = WaterfallPublisher().subscribe()
    with pytest.raises(ValueError):
        subscription.get(timeout)


def test_timeout_expiration_raises_queue_empty() -> None:
    with pytest.raises(queue.Empty):
        WaterfallPublisher().subscribe().get(0)


def test_subscription_close_is_idempotent_and_removes_subscriber() -> None:
    publisher = WaterfallPublisher()
    subscription = publisher.subscribe()

    subscription.close()
    subscription.close()

    assert publisher.subscriber_count == 0
    assert subscription.snapshot().health == "closed"
    with pytest.raises(WaterfallSubscriptionClosed):
        subscription.get(0)


def test_publisher_close_wakes_blocked_subscription() -> None:
    publisher = WaterfallPublisher()
    subscription = publisher.subscribe()
    result: list[type[BaseException]] = []

    def receive() -> None:
        try:
            subscription.get()
        except BaseException as exc:
            result.append(type(exc))

    thread = threading.Thread(target=receive)
    thread.start()
    publisher.close()
    thread.join(timeout=1)

    assert result == [WaterfallSubscriptionClosed]
    publisher.close()
    with pytest.raises(RuntimeError, match="closed"):
        publisher.subscribe()
    with pytest.raises(RuntimeError, match="closed"):
        publisher.publish(response("after"))


def test_subscriber_limit() -> None:
    publisher = WaterfallPublisher(max_subscribers=1)
    publisher.subscribe()
    with pytest.raises(RuntimeError, match="maximum subscriber"):
        publisher.subscribe()


@pytest.mark.parametrize("field", ["queue_capacity", "max_subscribers"])
@pytest.mark.parametrize("value", [True, False, 1.0, "1"])
def test_constructor_bounds_reject_non_integer_values(field: str, value: object) -> None:
    with pytest.raises(TypeError):
        WaterfallPublisher(**{field: value})  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["queue_capacity", "max_subscribers"])
@pytest.mark.parametrize("value", [0, -1])
def test_constructor_bounds_reject_values_below_one(field: str, value: int) -> None:
    with pytest.raises(ValueError):
        WaterfallPublisher(**{field: value})


def test_defaults_and_publish_type_validation() -> None:
    publisher = WaterfallPublisher()
    assert publisher.queue_capacity == WATERFALL_DEFAULT_QUEUE_CAPACITY
    assert publisher.max_subscribers == 8
    with pytest.raises(TypeError):
        publisher.publish(object())  # type: ignore[arg-type]
    assert publisher.sequence == 0
    assert publisher.snapshot().responses_published == 0


def test_snapshot_dictionaries_are_json_compatible() -> None:
    publisher = WaterfallPublisher(queue_capacity=1)
    subscription = publisher.subscribe()
    publisher.publish(response("first"))
    publisher.publish(response("second"))
    subscription.get(0)

    assert json.loads(json.dumps(publisher.snapshot().as_dict())) == {
        "closed": False,
        "queue_capacity": 1,
        "max_subscribers": 8,
        "subscriber_count": 1,
        "responses_published": 2,
        "last_sequence": 2,
    }
    assert json.loads(json.dumps(subscription.snapshot().as_dict())) == {
        "health": "degraded",
        "closed": False,
        "queue_capacity": 1,
        "queued_responses": 0,
        "responses_delivered": 1,
        "responses_dropped": 1,
        "overflows": 1,
        "last_delivered_sequence": 2,
        "last_dropped_sequence": 1,
    }


def test_concurrent_publishers_preserve_global_sequence_order() -> None:
    publisher = WaterfallPublisher(queue_capacity=200)
    subscriptions = [publisher.subscribe(), publisher.subscribe()]
    barrier = threading.Barrier(5)

    def publish_many(worker: int) -> None:
        barrier.wait()
        for index in range(25):
            publisher.publish(response(f"{worker}-{index}"))

    threads = [threading.Thread(target=publish_many, args=(index,)) for index in range(4)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    for subscription in subscriptions:
        assert [subscription.get(0).sequence for _ in range(100)] == list(range(1, 101))



def test_publisher_accepts_exact_240_value_gwf_response() -> None:
    publisher = WaterfallPublisher()
    subscription = publisher.subscribe()
    response_value = gwf_response()

    publication = publisher.publish(response_value)
    delivery = subscription.get(0)

    assert publication.response is response_value
    assert delivery.response is response_value
    assert delivery.sequence == 1
