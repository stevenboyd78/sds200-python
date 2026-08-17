from __future__ import annotations

import queue
import threading
from collections import deque
from dataclasses import dataclass
from math import isfinite
from time import monotonic
from typing import Literal

from .models import GwfResponse, PwfResponse, WaterfallResponse

WATERFALL_DEFAULT_QUEUE_CAPACITY = 64

WaterfallSubscriptionHealth = Literal["healthy", "degraded", "closed"]


def _require_integer(value: object, *, description: str, minimum: int) -> int:
    if type(value) is not int:
        raise TypeError(f"{description} must be an integer.")
    if value < minimum:
        raise ValueError(f"{description} must be at least {minimum}.")
    return value


@dataclass(frozen=True, slots=True)
class WaterfallPublication:
    """One globally ordered accepted waterfall response publication."""

    sequence: int
    response: WaterfallResponse

    def __post_init__(self) -> None:
        _require_integer(self.sequence, description="Waterfall sequence", minimum=1)
        if not isinstance(self.response, (PwfResponse, GwfResponse)):
            raise TypeError("Waterfall publications must contain a PwfResponse or GwfResponse.")


@dataclass(frozen=True, slots=True)
class WaterfallDelivery:
    """One subscription delivery with cumulative queue-loss information."""

    publication: WaterfallPublication
    responses_dropped: int
    overflows: int

    @property
    def sequence(self) -> int:
        return self.publication.sequence

    @property
    def response(self) -> WaterfallResponse:
        return self.publication.response

    @property
    def health(self) -> WaterfallSubscriptionHealth:
        return "degraded" if self.responses_dropped else "healthy"


@dataclass(frozen=True, slots=True)
class WaterfallSubscriptionSnapshot:
    """Immutable queue and delivery health for one waterfall subscription."""

    health: WaterfallSubscriptionHealth
    closed: bool
    queue_capacity: int
    queued_responses: int
    responses_delivered: int
    responses_dropped: int
    overflows: int
    last_delivered_sequence: int | None
    last_dropped_sequence: int | None

    def as_dict(self) -> dict[str, object]:
        return {
            "health": self.health,
            "closed": self.closed,
            "queue_capacity": self.queue_capacity,
            "queued_responses": self.queued_responses,
            "responses_delivered": self.responses_delivered,
            "responses_dropped": self.responses_dropped,
            "overflows": self.overflows,
            "last_delivered_sequence": self.last_delivered_sequence,
            "last_dropped_sequence": self.last_dropped_sequence,
        }


@dataclass(frozen=True, slots=True)
class WaterfallPublisherSnapshot:
    """Immutable activity and capacity state for one waterfall publisher."""

    closed: bool
    queue_capacity: int
    max_subscribers: int
    subscriber_count: int
    responses_published: int
    last_sequence: int | None

    def as_dict(self) -> dict[str, object]:
        return {
            "closed": self.closed,
            "queue_capacity": self.queue_capacity,
            "max_subscribers": self.max_subscribers,
            "subscriber_count": self.subscriber_count,
            "responses_published": self.responses_published,
            "last_sequence": self.last_sequence,
        }


class WaterfallSubscriptionClosed(RuntimeError):
    """Raised when receiving from a closed waterfall subscription."""


class WaterfallSubscription:
    """One independent bounded waterfall response subscription."""

    def __init__(self, publisher: WaterfallPublisher, *, queue_capacity: int) -> None:
        self._publisher = publisher
        self._queue_capacity = queue_capacity
        self._condition = threading.Condition()
        self._queue: deque[WaterfallPublication] = deque()
        self._responses_delivered = 0
        self._responses_dropped = 0
        self._overflows = 0
        self._last_delivered_sequence: int | None = None
        self._last_dropped_sequence: int | None = None
        self._closed = False

    @property
    def queue_capacity(self) -> int:
        return self._queue_capacity

    @property
    def closed(self) -> bool:
        with self._condition:
            return self._closed

    def snapshot(self) -> WaterfallSubscriptionSnapshot:
        with self._condition:
            health: WaterfallSubscriptionHealth
            if self._closed:
                health = "closed"
            elif self._responses_dropped:
                health = "degraded"
            else:
                health = "healthy"
            return WaterfallSubscriptionSnapshot(
                health=health,
                closed=self._closed,
                queue_capacity=self._queue_capacity,
                queued_responses=len(self._queue),
                responses_delivered=self._responses_delivered,
                responses_dropped=self._responses_dropped,
                overflows=self._overflows,
                last_delivered_sequence=self._last_delivered_sequence,
                last_dropped_sequence=self._last_dropped_sequence,
            )

    def get(self, timeout: float | None = None) -> WaterfallDelivery:
        if timeout is not None:
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
                raise TypeError("Waterfall subscription timeout must be a number or None.")
            if not isfinite(timeout):
                raise ValueError("Waterfall subscription timeout must be finite.")
            if timeout < 0:
                raise ValueError("Waterfall subscription timeout must not be negative.")

        deadline = None if timeout is None else monotonic() + timeout
        with self._condition:
            while True:
                if self._closed:
                    raise WaterfallSubscriptionClosed("Waterfall subscription is closed.")
                if self._queue:
                    publication = self._queue.popleft()
                    self._responses_delivered += 1
                    self._last_delivered_sequence = publication.sequence
                    return WaterfallDelivery(
                        publication=publication,
                        responses_dropped=self._responses_dropped,
                        overflows=self._overflows,
                    )
                if deadline is None:
                    self._condition.wait()
                    continue
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise queue.Empty
                self._condition.wait(remaining)

    def close(self) -> None:
        self._publisher._close_subscription(self)

    def _enqueue(self, publication: WaterfallPublication) -> None:
        with self._condition:
            if self._closed:
                return
            if len(self._queue) >= self._queue_capacity:
                dropped = self._queue.popleft()
                self._responses_dropped += 1
                self._overflows += 1
                self._last_dropped_sequence = dropped.sequence
            self._queue.append(publication)
            self._condition.notify()

    def _close_from_publisher(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._queue.clear()
            self._condition.notify_all()


class WaterfallPublisher:
    """Publish ordered waterfall responses to isolated bounded subscriptions."""

    def __init__(
        self,
        *,
        queue_capacity: int = WATERFALL_DEFAULT_QUEUE_CAPACITY,
        max_subscribers: int = 8,
    ) -> None:
        self._queue_capacity = _require_integer(
            queue_capacity, description="Waterfall queue capacity", minimum=1
        )
        self._max_subscribers = _require_integer(
            max_subscribers, description="Waterfall subscriber limit", minimum=1
        )
        self._lock = threading.RLock()
        self._subscriptions: set[WaterfallSubscription] = set()
        self._sequence = 0
        self._responses_published = 0
        self._closed = False

    @property
    def queue_capacity(self) -> int:
        return self._queue_capacity

    @property
    def max_subscribers(self) -> int:
        return self._max_subscribers

    @property
    def sequence(self) -> int:
        with self._lock:
            return self._sequence

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscriptions)

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def snapshot(self) -> WaterfallPublisherSnapshot:
        with self._lock:
            return WaterfallPublisherSnapshot(
                closed=self._closed,
                queue_capacity=self._queue_capacity,
                max_subscribers=self._max_subscribers,
                subscriber_count=len(self._subscriptions),
                responses_published=self._responses_published,
                last_sequence=self._sequence if self._sequence else None,
            )

    def subscribe(self) -> WaterfallSubscription:
        with self._lock:
            if self._closed:
                raise RuntimeError("Waterfall publisher is closed.")
            if len(self._subscriptions) >= self._max_subscribers:
                raise RuntimeError(
                    "Waterfall publisher reached its maximum subscriber count."
                )
            subscription = WaterfallSubscription(
                self, queue_capacity=self._queue_capacity
            )
            self._subscriptions.add(subscription)
            return subscription

    def publish(self, response: WaterfallResponse) -> WaterfallPublication:
        if not isinstance(response, (PwfResponse, GwfResponse)):
            raise TypeError("Waterfall publishers accept only WaterfallResponse values.")
        with self._lock:
            if self._closed:
                raise RuntimeError("Waterfall publisher is closed.")
            sequence = self._sequence + 1
            publication = WaterfallPublication(sequence=sequence, response=response)
            self._sequence = sequence
            self._responses_published += 1
            for subscription in tuple(self._subscriptions):
                subscription._enqueue(publication)
            return publication

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            subscriptions = tuple(self._subscriptions)
            self._subscriptions.clear()
            for subscription in subscriptions:
                subscription._close_from_publisher()

    def _close_subscription(self, subscription: WaterfallSubscription) -> None:
        with self._lock:
            self._subscriptions.discard(subscription)
            subscription._close_from_publisher()
