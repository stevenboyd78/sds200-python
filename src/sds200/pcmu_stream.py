from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Protocol

from .pcmu import PcmuPacket, PcmuPacketHandler
from .pcmu_subscriptions import (
    PCMU_DEFAULT_MAX_PAYLOAD_BYTES,
    PCMU_DEFAULT_QUEUE_CAPACITY,
    PcmuPublisher,
    PcmuPublisherSnapshot,
    PcmuSubscription,
)

logger = logging.getLogger(__name__)


class PcmuPacketSource(Protocol):
    """Source of accepted PCMU packets from one authoritative audio session."""

    def on_packet(
        self,
        callback: PcmuPacketHandler,
    ) -> Callable[[], None]: ...


class PcmuStream:
    """Expose accepted packets through one bounded publisher.

    This adapter owns only its packet-source subscription and publisher. It
    deliberately does not start or stop the underlying network-audio transport.
    """

    def __init__(
        self,
        source: PcmuPacketSource,
        *,
        queue_capacity: int = PCMU_DEFAULT_QUEUE_CAPACITY,
        max_subscribers: int = 8,
        max_payload_bytes: int = PCMU_DEFAULT_MAX_PAYLOAD_BYTES,
    ) -> None:
        self.source = source
        self._lock = threading.RLock()
        self._closed = False
        self._publisher = PcmuPublisher(
            queue_capacity=queue_capacity,
            max_subscribers=max_subscribers,
            max_payload_bytes=max_payload_bytes,
        )
        self._unsubscribe: Callable[[], None] | None = None

        try:
            self._unsubscribe = source.on_packet(self._receive_packet)
        except BaseException:
            self._closed = True
            self._publisher.close()
            raise

    @property
    def queue_capacity(self) -> int:
        return self._publisher.queue_capacity

    @property
    def max_subscribers(self) -> int:
        return self._publisher.max_subscribers

    @property
    def max_payload_bytes(self) -> int:
        return self._publisher.max_payload_bytes

    @property
    def stream_sequence(self) -> int:
        return self._publisher.stream_sequence

    @property
    def subscriber_count(self) -> int:
        return self._publisher.subscriber_count

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def snapshot(self) -> PcmuPublisherSnapshot:
        return self._publisher.snapshot()

    def subscribe(self) -> PcmuSubscription:
        return self._publisher.subscribe()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return

            self._closed = True
            unsubscribe, self._unsubscribe = self._unsubscribe, None

            if unsubscribe is not None:
                try:
                    unsubscribe()
                except Exception as error:
                    logger.error(
                        "PCMU packet source unsubscribe failed error=%s",
                        error.__class__.__name__,
                    )

            self._publisher.close()

    def _receive_packet(self, packet: PcmuPacket) -> None:
        with self._lock:
            if self._closed:
                return
            self._publisher.publish(packet)
