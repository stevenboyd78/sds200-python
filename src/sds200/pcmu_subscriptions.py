from __future__ import annotations

import queue
import threading
from collections import deque
from dataclasses import dataclass
from math import isfinite
from time import monotonic
from typing import Literal

from .pcmu import PcmuPacket

PCMU_DEFAULT_QUEUE_CAPACITY = 64
PCMU_DEFAULT_MAX_PAYLOAD_BYTES = 65535

PcmuSubscriptionHealth = Literal["healthy", "degraded", "closed"]


def _require_integer(
    value: object,
    *,
    description: str,
    minimum: int,
) -> int:
    if type(value) is not int:
        raise TypeError(f"{description} must be an integer.")
    if value < minimum:
        raise ValueError(f"{description} must be at least {minimum}.")
    return value


@dataclass(frozen=True, slots=True)
class PcmuPublication:
    """One globally ordered accepted PCMU packet publication."""

    stream_sequence: int
    packet: PcmuPacket

    def __post_init__(self) -> None:
        _require_integer(
            self.stream_sequence,
            description="PCMU stream sequence",
            minimum=1,
        )
        if not isinstance(self.packet, PcmuPacket):
            raise TypeError(
                "PCMU publications must contain a PcmuPacket."
            )


@dataclass(frozen=True, slots=True)
class PcmuPacketDelivery:
    """One subscription delivery with cumulative queue-loss information."""

    publication: PcmuPublication
    packets_dropped: int
    payload_bytes_dropped: int
    overflows: int

    @property
    def stream_sequence(self) -> int:
        return self.publication.stream_sequence

    @property
    def packet(self) -> PcmuPacket:
        return self.publication.packet

    @property
    def health(self) -> PcmuSubscriptionHealth:
        return "degraded" if self.packets_dropped else "healthy"


@dataclass(frozen=True, slots=True)
class PcmuSubscriptionSnapshot:
    """Immutable queue and delivery health for one PCMU subscription."""

    health: PcmuSubscriptionHealth
    closed: bool
    queue_capacity: int
    queued_packets: int
    queued_payload_bytes: int
    packets_delivered: int
    payload_bytes_delivered: int
    packets_dropped: int
    payload_bytes_dropped: int
    overflows: int
    last_delivered_stream_sequence: int | None
    last_dropped_stream_sequence: int | None

    def as_dict(self) -> dict[str, object]:
        return {
            "health": self.health,
            "closed": self.closed,
            "queue_capacity": self.queue_capacity,
            "queued_packets": self.queued_packets,
            "queued_payload_bytes": self.queued_payload_bytes,
            "packets_delivered": self.packets_delivered,
            "payload_bytes_delivered": self.payload_bytes_delivered,
            "packets_dropped": self.packets_dropped,
            "payload_bytes_dropped": self.payload_bytes_dropped,
            "overflows": self.overflows,
            "last_delivered_stream_sequence": (
                self.last_delivered_stream_sequence
            ),
            "last_dropped_stream_sequence": (
                self.last_dropped_stream_sequence
            ),
        }


@dataclass(frozen=True, slots=True)
class PcmuPublisherSnapshot:
    """Immutable activity and capacity state for one PCMU publisher."""

    closed: bool
    queue_capacity: int
    max_subscribers: int
    max_payload_bytes: int
    subscriber_count: int
    packets_published: int
    payload_bytes_published: int
    last_stream_sequence: int | None

    def as_dict(self) -> dict[str, object]:
        return {
            "closed": self.closed,
            "queue_capacity": self.queue_capacity,
            "max_subscribers": self.max_subscribers,
            "max_payload_bytes": self.max_payload_bytes,
            "subscriber_count": self.subscriber_count,
            "packets_published": self.packets_published,
            "payload_bytes_published": self.payload_bytes_published,
            "last_stream_sequence": self.last_stream_sequence,
        }


class PcmuSubscriptionClosed(RuntimeError):
    """Raised when receiving from a closed PCMU subscription."""


class PcmuSubscription:
    """One independent bounded PCMU packet subscription."""

    def __init__(
        self,
        publisher: PcmuPublisher,
        *,
        queue_capacity: int,
    ) -> None:
        self._publisher = publisher
        self._queue_capacity = queue_capacity
        self._condition = threading.Condition()
        self._queue: deque[PcmuPublication] = deque()
        self._queued_payload_bytes = 0
        self._packets_delivered = 0
        self._payload_bytes_delivered = 0
        self._packets_dropped = 0
        self._payload_bytes_dropped = 0
        self._overflows = 0
        self._last_delivered_stream_sequence: int | None = None
        self._last_dropped_stream_sequence: int | None = None
        self._closed = False

    @property
    def queue_capacity(self) -> int:
        return self._queue_capacity

    @property
    def closed(self) -> bool:
        with self._condition:
            return self._closed

    def snapshot(self) -> PcmuSubscriptionSnapshot:
        with self._condition:
            return self._snapshot_locked()

    def get(
        self,
        timeout: float | None = None,
    ) -> PcmuPacketDelivery:
        if timeout is not None:
            if isinstance(timeout, bool) or not isinstance(
                timeout,
                (int, float),
            ):
                raise TypeError(
                    "PCMU subscription timeout must be a number or None."
                )
            if not isfinite(timeout):
                raise ValueError(
                    "PCMU subscription timeout must be finite."
                )
            if timeout < 0:
                raise ValueError(
                    "PCMU subscription timeout must not be negative."
                )

        deadline = None if timeout is None else monotonic() + timeout
        with self._condition:
            while True:
                if self._closed:
                    raise PcmuSubscriptionClosed(
                        "PCMU subscription is closed."
                    )
                if self._queue:
                    publication = self._queue.popleft()
                    payload_bytes = len(publication.packet.payload)
                    self._queued_payload_bytes -= payload_bytes
                    self._packets_delivered += 1
                    self._payload_bytes_delivered += payload_bytes
                    self._last_delivered_stream_sequence = (
                        publication.stream_sequence
                    )
                    return PcmuPacketDelivery(
                        publication=publication,
                        packets_dropped=self._packets_dropped,
                        payload_bytes_dropped=(
                            self._payload_bytes_dropped
                        ),
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

    def _enqueue(self, publication: PcmuPublication) -> None:
        with self._condition:
            if self._closed:
                return

            if len(self._queue) >= self._queue_capacity:
                dropped = self._queue.popleft()
                dropped_bytes = len(dropped.packet.payload)
                self._queued_payload_bytes -= dropped_bytes
                self._packets_dropped += 1
                self._payload_bytes_dropped += dropped_bytes
                self._overflows += 1
                self._last_dropped_stream_sequence = (
                    dropped.stream_sequence
                )

            self._queue.append(publication)
            self._queued_payload_bytes += len(
                publication.packet.payload
            )
            self._condition.notify()

    def _close_from_publisher(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._queue.clear()
            self._queued_payload_bytes = 0
            self._condition.notify_all()

    def _snapshot_locked(self) -> PcmuSubscriptionSnapshot:
        health: PcmuSubscriptionHealth
        if self._closed:
            health = "closed"
        elif self._packets_dropped:
            health = "degraded"
        else:
            health = "healthy"

        return PcmuSubscriptionSnapshot(
            health=health,
            closed=self._closed,
            queue_capacity=self._queue_capacity,
            queued_packets=len(self._queue),
            queued_payload_bytes=self._queued_payload_bytes,
            packets_delivered=self._packets_delivered,
            payload_bytes_delivered=self._payload_bytes_delivered,
            packets_dropped=self._packets_dropped,
            payload_bytes_dropped=self._payload_bytes_dropped,
            overflows=self._overflows,
            last_delivered_stream_sequence=(
                self._last_delivered_stream_sequence
            ),
            last_dropped_stream_sequence=(
                self._last_dropped_stream_sequence
            ),
        )


class PcmuPublisher:
    """Publish ordered PCMU packets to isolated bounded subscriptions."""

    def __init__(
        self,
        *,
        queue_capacity: int = PCMU_DEFAULT_QUEUE_CAPACITY,
        max_subscribers: int = 8,
        max_payload_bytes: int = PCMU_DEFAULT_MAX_PAYLOAD_BYTES,
    ) -> None:
        self._queue_capacity = _require_integer(
            queue_capacity,
            description="PCMU queue capacity",
            minimum=1,
        )
        self._max_subscribers = _require_integer(
            max_subscribers,
            description="PCMU subscriber limit",
            minimum=1,
        )
        self._max_payload_bytes = _require_integer(
            max_payload_bytes,
            description="PCMU maximum payload size",
            minimum=1,
        )
        self._lock = threading.RLock()
        self._subscriptions: set[PcmuSubscription] = set()
        self._stream_sequence = 0
        self._packets_published = 0
        self._payload_bytes_published = 0
        self._closed = False

    @property
    def queue_capacity(self) -> int:
        return self._queue_capacity

    @property
    def max_subscribers(self) -> int:
        return self._max_subscribers

    @property
    def max_payload_bytes(self) -> int:
        return self._max_payload_bytes

    @property
    def stream_sequence(self) -> int:
        with self._lock:
            return self._stream_sequence

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscriptions)

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def snapshot(self) -> PcmuPublisherSnapshot:
        with self._lock:
            return PcmuPublisherSnapshot(
                closed=self._closed,
                queue_capacity=self._queue_capacity,
                max_subscribers=self._max_subscribers,
                max_payload_bytes=self._max_payload_bytes,
                subscriber_count=len(self._subscriptions),
                packets_published=self._packets_published,
                payload_bytes_published=(
                    self._payload_bytes_published
                ),
                last_stream_sequence=(
                    self._stream_sequence
                    if self._stream_sequence
                    else None
                ),
            )

    def subscribe(self) -> PcmuSubscription:
        with self._lock:
            if self._closed:
                raise RuntimeError("PCMU publisher is closed.")
            if len(self._subscriptions) >= self._max_subscribers:
                raise RuntimeError(
                    "PCMU publisher reached its maximum subscriber count."
                )

            subscription = PcmuSubscription(
                self,
                queue_capacity=self._queue_capacity,
            )
            self._subscriptions.add(subscription)
            return subscription

    def publish(self, packet: PcmuPacket) -> PcmuPublication:
        if not isinstance(packet, PcmuPacket):
            raise TypeError(
                "PCMU publishers accept only PcmuPacket values."
            )
        payload_bytes = len(packet.payload)
        if payload_bytes > self._max_payload_bytes:
            raise ValueError(
                "PCMU packet payload exceeds the maximum size "
                f"of {self._max_payload_bytes} bytes."
            )

        with self._lock:
            if self._closed:
                raise RuntimeError("PCMU publisher is closed.")

            stream_sequence = self._stream_sequence + 1
            publication = PcmuPublication(
                stream_sequence=stream_sequence,
                packet=packet,
            )
            self._stream_sequence = stream_sequence
            self._packets_published += 1
            self._payload_bytes_published += payload_bytes

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

    def _close_subscription(
        self,
        subscription: PcmuSubscription,
    ) -> None:
        with self._lock:
            self._subscriptions.discard(subscription)
            subscription._close_from_publisher()
