from __future__ import annotations

import json
import queue
import threading
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite
from time import monotonic
from types import MappingProxyType
from typing import cast

DAEMON_EVENT_PROTOCOL = "sdsctl.daemon.events"
DAEMON_EVENT_VERSION = 1
DAEMON_EVENT_SUPPORTED_VERSIONS = (DAEMON_EVENT_VERSION,)
DAEMON_EVENT_DEFAULT_QUEUE_CAPACITY = 64
DAEMON_EVENT_DEFAULT_MAX_BYTES = 1024 * 1024


class DaemonEventKind(StrEnum):
    """Stable event kinds published by the local daemon event stream."""

    SNAPSHOT = "stream.snapshot"
    DAEMON_TRANSITION = "daemon.transition"
    SCANNER_CONNECTION = "scanner.connection"
    PSI_STATE = "scanner.psi"
    RADIO_STATE = "radio.state"
    AUDIO_STATE = "audio.state"
    DESTINATION_HEALTH = "destination.health"


@dataclass(frozen=True, slots=True)
class DaemonEvent:
    """One immutable JSON-compatible event-stream envelope."""

    sequence: int
    observed_at: datetime
    kind: str
    payload: Mapping[str, object] = field(default_factory=dict)
    protocol: str = DAEMON_EVENT_PROTOCOL
    version: int = DAEMON_EVENT_VERSION

    def __post_init__(self) -> None:
        if type(self.sequence) is not int:
            raise TypeError("Daemon event sequence must be an integer.")
        if self.sequence < 0:
            raise ValueError("Daemon event sequence must not be negative.")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("Daemon event timestamps must be timezone-aware.")
        if not isinstance(self.kind, str):
            raise TypeError("Daemon event kind must be a string.")
        if not self.kind or self.kind.strip() != self.kind:
            raise ValueError("Daemon event kind must not be empty or padded.")
        if any(ord(character) < 0x20 for character in self.kind):
            raise ValueError(
                "Daemon event kind must not contain control characters."
            )
        if self.protocol != DAEMON_EVENT_PROTOCOL:
            raise ValueError(
                f"Unsupported daemon event protocol: {self.protocol!r}."
            )
        if type(self.version) is not int:
            raise TypeError("Daemon event version must be an integer.")
        if self.version not in DAEMON_EVENT_SUPPORTED_VERSIONS:
            raise ValueError(
                "Unsupported daemon event version: "
                f"{self.version}; "
                f"supported={list(DAEMON_EVENT_SUPPORTED_VERSIONS)!r}."
            )
        if not isinstance(self.payload, Mapping):
            raise TypeError("Daemon event payload must be a mapping.")

        object.__setattr__(self, "kind", str(self.kind))
        object.__setattr__(
            self,
            "payload",
            _freeze_mapping(cast(Mapping[object, object], self.payload)),
        )

    @classmethod
    def create(
        cls,
        sequence: int,
        kind: str,
        payload: Mapping[str, object],
        *,
        observed_at: datetime | None = None,
    ) -> DaemonEvent:
        return cls(
            sequence=sequence,
            observed_at=observed_at or datetime.now(UTC),
            kind=kind,
            payload=payload,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "protocol": self.protocol,
            "version": self.version,
            "sequence": self.sequence,
            "observed_at": self.observed_at.isoformat(),
            "kind": self.kind,
            "payload": {
                key: _thaw_json(value)
                for key, value in self.payload.items()
            },
        }

    def to_json_line(self) -> bytes:
        return (
            json.dumps(
                self.as_dict(),
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")


class DaemonEventSubscriptionClosed(RuntimeError):
    """Raised when receiving from a closed daemon event subscription."""


class DaemonEventSubscription:
    """One independent bounded daemon event subscription."""

    def __init__(
        self,
        publisher: DaemonEventPublisher,
        snapshot: DaemonEvent,
        *,
        queue_capacity: int,
    ) -> None:
        self._publisher = publisher
        self._queue_capacity = queue_capacity
        self._condition = threading.Condition()
        self._queue: deque[DaemonEvent] = deque((snapshot,))
        self._dropped_events = 0
        self._closed = False

    @property
    def queue_capacity(self) -> int:
        return self._queue_capacity

    @property
    def dropped_events(self) -> int:
        with self._condition:
            return self._dropped_events

    @property
    def closed(self) -> bool:
        with self._condition:
            return self._closed

    def get(self, timeout: float | None = None) -> DaemonEvent:
        if timeout is not None:
            if isinstance(timeout, bool) or not isinstance(
                timeout,
                (int, float),
            ):
                raise TypeError(
                    "Subscription timeout must be a number or None."
                )
            if not isfinite(timeout):
                raise ValueError("Subscription timeout must be finite.")
            if timeout < 0:
                raise ValueError(
                    "Subscription timeout must not be negative."
                )

        deadline = None if timeout is None else monotonic() + timeout
        with self._condition:
            while True:
                if self._closed:
                    raise DaemonEventSubscriptionClosed(
                        "Daemon event subscription is closed."
                    )
                if self._queue:
                    return self._queue.popleft()
                if deadline is None:
                    self._condition.wait()
                    continue

                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise queue.Empty
                self._condition.wait(remaining)

    def close(self) -> None:
        self._publisher._close_subscription(self)

    def _enqueue(self, event: DaemonEvent) -> None:
        with self._condition:
            if self._closed:
                return

            if len(self._queue) >= self._queue_capacity:
                if (
                    self._queue
                    and self._queue[0].kind
                    == DaemonEventKind.SNAPSHOT
                ):
                    del self._queue[1]
                else:
                    self._queue.popleft()
                self._dropped_events += 1

            self._queue.append(event)
            self._condition.notify()

    def _close_from_publisher(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._queue.clear()
            self._condition.notify_all()


class DaemonEventPublisher:
    """Publish globally ordered events to isolated bounded subscriptions."""

    def __init__(
        self,
        snapshot: Callable[[], Mapping[str, object]],
        *,
        queue_capacity: int = DAEMON_EVENT_DEFAULT_QUEUE_CAPACITY,
        max_subscribers: int = 32,
        max_event_bytes: int = DAEMON_EVENT_DEFAULT_MAX_BYTES,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if type(queue_capacity) is not int:
            raise TypeError(
                "Daemon event queue capacity must be an integer."
            )
        if queue_capacity < 2:
            raise ValueError(
                "Daemon event queue capacity must be at least two."
            )
        if type(max_subscribers) is not int:
            raise TypeError(
                "Daemon event subscriber limit must be an integer."
            )
        if max_subscribers <= 0:
            raise ValueError(
                "Daemon event subscriber limit must be greater than zero."
            )
        if type(max_event_bytes) is not int:
            raise TypeError(
                "Daemon event maximum encoded size must be an integer."
            )
        if max_event_bytes <= 0:
            raise ValueError(
                "Daemon event maximum encoded size must be greater than zero."
            )

        self._snapshot = snapshot
        self._queue_capacity = queue_capacity
        self._max_subscribers = max_subscribers
        self._max_event_bytes = max_event_bytes
        self._now = now
        self._lock = threading.RLock()
        self._subscriptions: set[DaemonEventSubscription] = set()
        self._sequence = 0
        self._closed = False

    @property
    def queue_capacity(self) -> int:
        return self._queue_capacity

    @property
    def max_subscribers(self) -> int:
        return self._max_subscribers

    @property
    def max_event_bytes(self) -> int:
        return self._max_event_bytes

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

    def subscribe(self) -> DaemonEventSubscription:
        with self._lock:
            if self._closed:
                raise RuntimeError("Daemon event publisher is closed.")
            if len(self._subscriptions) >= self._max_subscribers:
                raise RuntimeError(
                    "Daemon event publisher reached its maximum "
                    "subscriber count."
                )

            snapshot = DaemonEvent(
                sequence=self._sequence,
                observed_at=self._now(),
                kind=DaemonEventKind.SNAPSHOT,
                payload=self._snapshot(),
            )
            _require_event_size(
                snapshot,
                max_event_bytes=self._max_event_bytes,
            )
            subscription = DaemonEventSubscription(
                self,
                snapshot,
                queue_capacity=self._queue_capacity,
            )
            self._subscriptions.add(subscription)
            return subscription

    def publish(
        self,
        kind: str,
        payload: Mapping[str, object],
        *,
        observed_at: datetime | None = None,
    ) -> DaemonEvent:
        with self._lock:
            if self._closed:
                raise RuntimeError("Daemon event publisher is closed.")
            if kind == DaemonEventKind.SNAPSHOT:
                raise ValueError(
                    "The daemon stream snapshot kind is reserved."
                )

            sequence = self._sequence + 1
            event = DaemonEvent(
                sequence=sequence,
                observed_at=(
                    self._now()
                    if observed_at is None
                    else observed_at
                ),
                kind=kind,
                payload=payload,
            )
            _require_event_size(
                event,
                max_event_bytes=self._max_event_bytes,
            )
            self._sequence = sequence

            for subscription in tuple(self._subscriptions):
                subscription._enqueue(event)

            return event

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
        subscription: DaemonEventSubscription,
    ) -> None:
        with self._lock:
            self._subscriptions.discard(subscription)
            subscription._close_from_publisher()


def _require_event_size(
    event: DaemonEvent,
    *,
    max_event_bytes: int,
) -> None:
    encoded_size = len(event.to_json_line())
    if encoded_size > max_event_bytes:
        raise ValueError(
            "Daemon event exceeds the maximum encoded size "
            f"of {max_event_bytes} bytes."
        )


def _freeze_mapping(
    value: Mapping[object, object],
) -> Mapping[str, object]:
    frozen: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError("Daemon event payload field names must be strings.")
        frozen[key] = _freeze_json(item)
    return MappingProxyType(frozen)


def _freeze_json(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError(
                "Daemon event payload numbers must be finite."
            )
        return value
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    raise TypeError(
        "Daemon event payload values must be JSON-compatible; "
        f"received {type(value).__name__}."
    )


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _thaw_json(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value
