from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from sds200.pcmu import PcmuPacket, PcmuPacketHandler
from sds200.pcmu_stream import PcmuStream
from sds200.pcmu_subscriptions import PcmuSubscriptionClosed


class FakePacketSource:
    def __init__(self) -> None:
        self.callbacks: list[PcmuPacketHandler] = []
        self.start_calls = 0
        self.stop_calls = 0

    def on_packet(
        self,
        callback: PcmuPacketHandler,
    ) -> Callable[[], None]:
        self.callbacks.append(callback)

        def unsubscribe() -> None:
            if callback in self.callbacks:
                self.callbacks.remove(callback)

        return unsubscribe

    def start(self) -> None:
        self.start_calls += 1

    def stop(self) -> None:
        self.stop_calls += 1

    def emit(self, packet: PcmuPacket) -> None:
        for callback in tuple(self.callbacks):
            callback(packet)


def make_packet(
    sequence: int,
    payload: bytes = b"audio",
) -> PcmuPacket:
    return PcmuPacket(
        endpoint="rtsp://192.0.2.25/au:scanner.au",
        sequence=sequence,
        timestamp=sequence * len(payload),
        ssrc=5678,
        payload=payload,
        observed_at=datetime(2026, 8, 5, 7, 40, tzinfo=UTC),
    )


def test_stream_publishes_source_packets_without_owning_source_lifecycle() -> None:
    source = FakePacketSource()
    stream = PcmuStream(source)
    subscription = stream.subscribe()
    packet = make_packet(10)

    source.emit(packet)
    delivery = subscription.get(timeout=0)

    assert delivery.stream_sequence == 1
    assert delivery.packet is packet
    assert stream.stream_sequence == 1
    assert source.start_calls == 0
    assert source.stop_calls == 0


def test_stream_exposes_configured_bounds_and_publisher_snapshot() -> None:
    source = FakePacketSource()
    stream = PcmuStream(
        source,
        queue_capacity=3,
        max_subscribers=2,
        max_payload_bytes=512,
    )

    snapshot = stream.snapshot()

    assert stream.queue_capacity == 3
    assert stream.max_subscribers == 2
    assert stream.max_payload_bytes == 512
    assert snapshot.queue_capacity == 3
    assert snapshot.max_subscribers == 2
    assert snapshot.max_payload_bytes == 512
    assert snapshot.subscriber_count == 0
    assert not snapshot.closed


def test_stream_close_unsubscribes_and_closes_subscriptions() -> None:
    source = FakePacketSource()
    stream = PcmuStream(source)
    subscription = stream.subscribe()

    assert len(source.callbacks) == 1

    stream.close()
    stream.close()

    assert source.callbacks == []
    assert stream.closed
    assert stream.subscriber_count == 0
    assert stream.snapshot().closed
    assert source.start_calls == 0
    assert source.stop_calls == 0

    with pytest.raises(PcmuSubscriptionClosed):
        subscription.get(timeout=0)
    with pytest.raises(RuntimeError, match="closed"):
        stream.subscribe()

    source.emit(make_packet(10))
    assert stream.stream_sequence == 0


def test_stream_closes_publisher_when_source_subscription_fails() -> None:
    class FailingSource:
        def on_packet(
            self,
            callback: PcmuPacketHandler,
        ) -> Callable[[], None]:
            del callback
            raise RuntimeError("source subscription failed")

    with pytest.raises(RuntimeError, match="source subscription failed"):
        PcmuStream(FailingSource())


def test_stream_close_finishes_when_source_unsubscribe_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    callback: PcmuPacketHandler | None = None

    class FailingUnsubscribeSource:
        def on_packet(
            self,
            value: PcmuPacketHandler,
        ) -> Callable[[], None]:
            nonlocal callback
            callback = value

            def unsubscribe() -> None:
                raise RuntimeError("secret unsubscribe failure")

            return unsubscribe

    stream = PcmuStream(FailingUnsubscribeSource())
    subscription = stream.subscribe()

    with caplog.at_level(logging.ERROR, logger="sds200.pcmu_stream"):
        stream.close()

    assert stream.closed
    assert stream.snapshot().closed
    assert stream.subscriber_count == 0
    assert "PCMU packet source unsubscribe failed" in caplog.text
    assert "error=RuntimeError" in caplog.text
    assert "secret unsubscribe failure" not in caplog.text

    with pytest.raises(PcmuSubscriptionClosed):
        subscription.get(timeout=0)

    assert callback is not None
    callback(make_packet(10))
    assert stream.stream_sequence == 0


def test_stream_preserves_independent_subscription_overflow() -> None:
    source = FakePacketSource()
    stream = PcmuStream(source, queue_capacity=2)
    slow = stream.subscribe()
    healthy = stream.subscribe()

    for sequence in range(10, 15):
        packet = make_packet(sequence)
        source.emit(packet)
        assert healthy.get(timeout=0).packet is packet

    slow_delivery = slow.get(timeout=0)

    assert slow_delivery.stream_sequence == 4
    assert slow_delivery.packet.sequence == 13
    assert slow_delivery.packets_dropped == 3
    assert slow.snapshot().health == "degraded"
    assert healthy.snapshot().health == "healthy"
