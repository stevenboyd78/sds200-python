from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from sds200 import (
    DAEMON_API_PROTOCOL,
    DAEMON_API_VERSION,
    DaemonApiErrorCode,
    DaemonApiResponse,
    DaemonEventKind,
    DaemonEventPublisher,
    DaemonMqttConfiguration,
    DaemonMqttHomeAssistantConfiguration,
    DaemonMqttWorker,
    ReconnectPolicy,
)
from sds200.daemon_mqtt_worker import DaemonMqttBrokerMessage

SNAPSHOT: dict[str, object] = {
    "state": "running",
    "scanner_endpoint": "192.0.2.25:50536",
    "scanner_model": "SDS200",
    "scanner_firmware": "1.26.01",
    "scanner_connected": True,
    "psi_interval_ms": 500,
    "psi_active": True,
    "radio_state": {
        "system": "County",
        "department": "Dispatch",
        "channel": "Primary",
        "signal": 4,
        "rssi": -83.0,
    },
    "audio": {
        "running": True,
        "packets": 10,
    },
    "router": {
        "name": "daemon-pcm",
        "running": True,
        "subscribers": [],
    },
    "recording": {
        "status": "idle",
        "active": False,
    },
}


class FakeEventStream:
    def __init__(self) -> None:
        self.snapshot_payload = dict(SNAPSHOT)
        self.publisher = DaemonEventPublisher(
            lambda: self.snapshot_payload,
            queue_capacity=8,
        )

    def subscribe(self):
        return self.publisher.subscribe()

    def publish(
        self,
        kind: DaemonEventKind,
        payload: Mapping[str, object],
    ) -> None:
        self.publisher.publish(kind, payload)


@dataclass(frozen=True)
class Published:
    topic: str
    payload: bytes
    qos: int
    retain: bool


class FakeBrokerConnection:
    def __init__(
        self,
        *,
        connect_error: BaseException | None = None,
        publish_error_after: int | None = None,
    ) -> None:
        self.connect_error = connect_error
        self.publish_error_after = publish_error_after
        self.connected = False
        self.interrupted = False
        self.closed = False
        self.publications: list[Published] = []
        self.subscriptions: list[tuple[str, int]] = []
        self.inbound_messages: queue.Queue[DaemonMqttBrokerMessage] = (
            queue.Queue()
        )
        self.acknowledged: list[DaemonMqttBrokerMessage] = []

    def connect(self) -> None:
        if self.connect_error is not None:
            raise self.connect_error
        self.connected = True

    def publish(
        self,
        topic: str,
        payload: bytes,
        *,
        qos: int,
        retain: bool,
    ) -> None:
        if (
            self.publish_error_after is not None
            and len(self.publications) >= self.publish_error_after
        ):
            raise OSError("secret broker publish failure")
        self.publications.append(
            Published(topic, payload, qos, retain)
        )

    def subscribe(self, topic: str, *, qos: int) -> None:
        self.subscriptions.append((topic, qos))

    def receive(
        self,
        *,
        timeout: float,
    ) -> DaemonMqttBrokerMessage | None:
        try:
            return self.inbound_messages.get(timeout=timeout)
        except queue.Empty:
            return None

    def acknowledge(self, message: DaemonMqttBrokerMessage) -> None:
        self.acknowledged.append(message)

    def deliver(self, message: DaemonMqttBrokerMessage) -> None:
        self.inbound_messages.put(message)

    def check(self) -> None:
        return

    def interrupt(self) -> None:
        self.interrupted = True

    def close(self) -> None:
        self.closed = True


class FakeControlApi:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def handle_control_payload(
        self,
        payload: object,
    ) -> DaemonApiResponse:
        self.calls.append(payload)
        assert isinstance(payload, Mapping)
        request_id = payload["request_id"]
        operation = payload["operation"]
        assert isinstance(request_id, str)
        assert isinstance(operation, str)
        return DaemonApiResponse.success(
            request_id,
            {"operation": operation},
        )


def command_payload(
    request_id: str,
    *,
    operation: str = "scanner.next",
) -> bytes:
    return json.dumps(
        {
            "protocol": DAEMON_API_PROTOCOL,
            "version": DAEMON_API_VERSION,
            "request_id": request_id,
            "operation": operation,
            "params": {"target": "SYS"},
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def command_message(
    request_id: str,
    *,
    operation: str = "scanner.next",
    retain: bool = False,
    duplicate: bool = False,
    message_id: int = 1,
) -> DaemonMqttBrokerMessage:
    return DaemonMqttBrokerMessage(
        topic="sdsctl/commands",
        payload=command_payload(request_id, operation=operation),
        qos=1,
        retain=retain,
        duplicate=duplicate,
        message_id=message_id,
    )


def wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float = 2.0,
) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("condition was not reached before timeout")
        time.sleep(0.005)


def decode_json(publication: Published) -> Any:
    return json.loads(publication.payload)


def make_worker(
    stream: FakeEventStream,
    factory: Callable[
        [DaemonMqttConfiguration, str | None],
        FakeBrokerConnection,
    ],
    *,
    config: DaemonMqttConfiguration | None = None,
    control_api: FakeControlApi | None = None,
    environ: Mapping[str, str] | None = None,
    max_command_bytes: int | None = None,
    command_cache_capacity: int | None = None,
    now: Callable[[], datetime] | None = None,
) -> DaemonMqttWorker:
    kwargs: dict[str, object] = {}
    if max_command_bytes is not None:
        kwargs["max_command_bytes"] = max_command_bytes
    if command_cache_capacity is not None:
        kwargs["command_cache_capacity"] = command_cache_capacity
    if now is not None:
        kwargs["now"] = now
    return DaemonMqttWorker(
        config or DaemonMqttConfiguration(host="mqtt.example.test"),
        stream,
        factory,
        control_api=control_api,
        environ=environ,
        event_poll_interval=0.01,
        stop_timeout=1.0,
        **kwargs,  # type: ignore[arg-type]
    )


def test_commands_require_explicit_control_api() -> None:
    stream = FakeEventStream()
    connection = FakeBrokerConnection()

    with pytest.raises(ValueError, match="require a daemon control API"):
        make_worker(
            stream,
            lambda config, password: connection,
            config=DaemonMqttConfiguration(
                host="mqtt.example.test",
                commands_enabled=True,
            ),
        )


def test_worker_executes_commands_after_initial_snapshot() -> None:
    stream = FakeEventStream()
    connection = FakeBrokerConnection()
    control_api = FakeControlApi()
    worker = make_worker(
        stream,
        lambda config, password: connection,
        config=DaemonMqttConfiguration(
            host="mqtt.example.test",
            commands_enabled=True,
        ),
        control_api=control_api,
    )

    worker.start()
    wait_until(lambda: connection.subscriptions == [("sdsctl/commands", 1)])
    assert len(connection.publications) >= 7

    message = command_message("mqtt-1", message_id=17)
    connection.deliver(message)
    wait_until(lambda: len(connection.acknowledged) == 1)

    responses = [
        item
        for item in connection.publications
        if item.topic == "sdsctl/responses"
    ]
    assert len(responses) == 1
    assert responses[0].retain is False
    assert decode_json(responses[0]) == {
        "ok": True,
        "protocol": DAEMON_API_PROTOCOL,
        "request_id": "mqtt-1",
        "result": {"operation": "scanner.next"},
        "version": DAEMON_API_VERSION,
    }
    assert control_api.calls == [json.loads(message.payload)]
    assert connection.acknowledged == [message]

    worker.stop()


def test_worker_rejects_retained_commands_without_dispatch() -> None:
    stream = FakeEventStream()
    connection = FakeBrokerConnection()
    control_api = FakeControlApi()
    worker = make_worker(
        stream,
        lambda config, password: connection,
        config=DaemonMqttConfiguration(
            host="mqtt.example.test",
            commands_enabled=True,
        ),
        control_api=control_api,
    )

    worker.start()
    wait_until(lambda: bool(connection.subscriptions))
    message = command_message(
        "mqtt-retained",
        retain=True,
        message_id=18,
    )
    connection.deliver(message)
    wait_until(lambda: connection.acknowledged == [message])

    response = next(
        decode_json(item)
        for item in connection.publications
        if item.topic == "sdsctl/responses"
    )
    assert response["request_id"] == "mqtt-retained"
    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_request"
    assert "Retained MQTT" in response["error"]["message"]
    assert control_api.calls == []

    worker.stop()


def test_worker_replays_cached_response_for_duplicate_request() -> None:
    stream = FakeEventStream()
    connection = FakeBrokerConnection()
    control_api = FakeControlApi()
    worker = make_worker(
        stream,
        lambda config, password: connection,
        config=DaemonMqttConfiguration(
            host="mqtt.example.test",
            commands_enabled=True,
        ),
        control_api=control_api,
    )

    worker.start()
    wait_until(lambda: bool(connection.subscriptions))

    first = command_message("mqtt-duplicate", message_id=20)
    duplicate = command_message(
        "mqtt-duplicate",
        duplicate=True,
        message_id=21,
    )
    connection.deliver(first)
    wait_until(lambda: len(connection.acknowledged) == 1)
    connection.deliver(duplicate)
    wait_until(lambda: len(connection.acknowledged) == 2)

    responses = [
        item.payload
        for item in connection.publications
        if item.topic == "sdsctl/responses"
    ]
    assert len(responses) == 2
    assert responses[0] == responses[1]
    assert len(control_api.calls) == 1

    worker.stop()


def test_worker_replays_cached_response_after_response_publish_failure() -> None:
    class FailFirstResponseConnection(FakeBrokerConnection):
        def __init__(self) -> None:
            super().__init__()
            self.failed_response_publication = False

        def publish(
            self,
            topic: str,
            payload: bytes,
            *,
            qos: int,
            retain: bool,
        ) -> None:
            if (
                topic == "sdsctl/responses"
                and not self.failed_response_publication
            ):
                self.failed_response_publication = True
                raise OSError("broker response publish failure")
            super().publish(
                topic,
                payload,
                qos=qos,
                retain=retain,
            )

    stream = FakeEventStream()
    first = FailFirstResponseConnection()
    second = FakeBrokerConnection()
    control_api = FakeControlApi()
    connections: list[FakeBrokerConnection] = [first, second]

    def factory(
        config: DaemonMqttConfiguration,
        password: str | None,
    ) -> FakeBrokerConnection:
        del config, password
        return connections.pop(0)

    worker = make_worker(
        stream,
        factory,
        config=DaemonMqttConfiguration(
            host="mqtt.example.test",
            commands_enabled=True,
            reconnect_policy=ReconnectPolicy(
                initial_delay=0.01,
                multiplier=1.0,
                max_delay=0.01,
                max_attempts=2,
            ),
        ),
        control_api=control_api,
    )

    worker.start()
    wait_until(lambda: first.subscriptions == [("sdsctl/commands", 1)])

    first_message = command_message(
        "mqtt-publish-retry",
        message_id=50,
    )
    first.deliver(first_message)

    wait_until(lambda: first.failed_response_publication)
    wait_until(lambda: worker.snapshot().failures == 1)
    wait_until(lambda: second.subscriptions == [("sdsctl/commands", 1)])

    duplicate = command_message(
        "mqtt-publish-retry",
        duplicate=True,
        message_id=51,
    )
    second.deliver(duplicate)
    wait_until(lambda: second.acknowledged == [duplicate])

    responses = [
        item
        for item in second.publications
        if item.topic == "sdsctl/responses"
    ]
    assert len(responses) == 1
    assert decode_json(responses[0]) == {
        "ok": True,
        "protocol": DAEMON_API_PROTOCOL,
        "request_id": "mqtt-publish-retry",
        "result": {"operation": "scanner.next"},
        "version": DAEMON_API_VERSION,
    }
    assert len(control_api.calls) == 1
    assert first.acknowledged == []
    assert first.closed

    worker.stop()
    assert second.closed


def test_worker_rejects_request_id_reuse_with_different_command() -> None:
    stream = FakeEventStream()
    connection = FakeBrokerConnection()
    control_api = FakeControlApi()
    worker = make_worker(
        stream,
        lambda config, password: connection,
        config=DaemonMqttConfiguration(
            host="mqtt.example.test",
            commands_enabled=True,
        ),
        control_api=control_api,
    )

    worker.start()
    wait_until(lambda: bool(connection.subscriptions))

    connection.deliver(command_message("mqtt-reuse", message_id=30))
    wait_until(lambda: len(connection.acknowledged) == 1)
    connection.deliver(
        command_message(
            "mqtt-reuse",
            operation="scanner.previous",
            message_id=31,
        )
    )
    wait_until(lambda: len(connection.acknowledged) == 2)

    responses = [
        decode_json(item)
        for item in connection.publications
        if item.topic == "sdsctl/responses"
    ]
    assert len(responses) == 2
    assert responses[0]["ok"] is True
    assert responses[1]["ok"] is False
    assert responses[1]["error"]["code"] == "invalid_request"
    assert "reused" in responses[1]["error"]["message"]
    assert len(control_api.calls) == 1

    worker.stop()


def test_worker_bounds_and_rejects_invalid_command_payloads() -> None:
    stream = FakeEventStream()
    connection = FakeBrokerConnection()
    control_api = FakeControlApi()
    worker = make_worker(
        stream,
        lambda config, password: connection,
        config=DaemonMqttConfiguration(
            host="mqtt.example.test",
            commands_enabled=True,
        ),
        control_api=control_api,
        max_command_bytes=16,
    )

    worker.start()
    wait_until(lambda: bool(connection.subscriptions))

    invalid = DaemonMqttBrokerMessage(
        topic="sdsctl/commands",
        payload=b"{not-json",
        qos=1,
        retain=False,
        duplicate=False,
        message_id=40,
    )
    oversized = DaemonMqttBrokerMessage(
        topic="sdsctl/commands",
        payload=b"x" * 17,
        qos=1,
        retain=False,
        duplicate=False,
        message_id=41,
    )
    connection.deliver(invalid)
    wait_until(lambda: len(connection.acknowledged) == 1)
    connection.deliver(oversized)
    wait_until(lambda: len(connection.acknowledged) == 2)

    responses = [
        decode_json(item)
        for item in connection.publications
        if item.topic == "sdsctl/responses"
    ]
    assert [item["error"]["code"] for item in responses] == [
        "invalid_request",
        "request_too_large",
    ]
    assert all(item["request_id"] is None for item in responses)
    assert control_api.calls == []

    worker.stop()


def test_worker_publishes_availability_and_authoritative_snapshot_topics() -> None:
    stream = FakeEventStream()
    connection = FakeBrokerConnection()
    worker = make_worker(stream, lambda config, password: connection)

    worker.start()
    wait_until(lambda: len(connection.publications) >= 7)

    topics = [publication.topic for publication in connection.publications]
    assert topics[:7] == [
        "sdsctl/availability",
        "sdsctl/state/daemon",
        "sdsctl/state/scanner/info",
        "sdsctl/state/scanner/connection",
        "sdsctl/state/radio",
        "sdsctl/state/audio",
        "sdsctl/state/recording",
    ]
    assert all(publication.qos == 1 for publication in connection.publications)
    assert all(publication.retain for publication in connection.publications[:7])
    assert connection.publications[0].payload == b"online"

    daemon = decode_json(connection.publications[1])
    scanner_info = decode_json(connection.publications[2])
    scanner_connection = decode_json(connection.publications[3])
    radio = decode_json(connection.publications[4])
    assert daemon["state"] == "running"
    assert "radio_state" not in daemon
    assert scanner_info == {
        "psi_active": True,
        "psi_interval_ms": 500,
        "scanner_endpoint": "192.0.2.25:50536",
        "scanner_firmware": "1.26.01",
        "scanner_model": "SDS200",
    }
    assert scanner_connection == {
        "scanner_connected": True,
        "scanner_endpoint": "192.0.2.25:50536",
    }
    assert radio["channel"] == "Primary"

    worker.stop()

    assert connection.publications[-1] == Published(
        "sdsctl/availability",
        b"offline",
        1,
        True,
    )
    assert connection.closed
    snapshot = worker.snapshot()
    assert snapshot.state == "stopped"
    assert snapshot.connected is False
    assert snapshot.successful_connections == 1
    assert snapshot.retained_publications >= 8


def test_availability_remains_retained_when_state_retention_is_disabled() -> None:
    stream = FakeEventStream()
    connection = FakeBrokerConnection()
    worker = make_worker(
        stream,
        lambda config, password: connection,
        config=DaemonMqttConfiguration(
            host="mqtt.example.test",
            retain=False,
        ),
    )

    worker.start()
    wait_until(lambda: len(connection.publications) >= 7)

    assert connection.publications[0] == Published(
        "sdsctl/availability",
        b"online",
        1,
        True,
    )
    assert all(
        not publication.retain
        for publication in connection.publications[1:7]
    )

    worker.stop()

    assert connection.publications[-1] == Published(
        "sdsctl/availability",
        b"offline",
        1,
        True,
    )
    assert worker.snapshot().retained_publications == 2


def test_worker_publishes_semantic_changes_but_skips_packet_rate_psi() -> None:
    stream = FakeEventStream()
    connection = FakeBrokerConnection()
    worker = make_worker(stream, lambda config, password: connection)
    worker.start()
    wait_until(lambda: len(connection.publications) >= 7)
    baseline = len(connection.publications)

    stream.publish(
        DaemonEventKind.PSI_STATE,
        {
            "command": "PSI",
            "received_at": "2026-08-08T20:00:00+00:00",
            "state": {"channel": "Primary"},
        },
    )
    stream.publish(
        DaemonEventKind.RADIO_STATE,
        {
            "fields": ["channel"],
            "previous": {"channel": "Primary"},
            "current": {"channel": "Secondary", "signal": 5},
        },
    )

    wait_until(lambda: worker.snapshot().psi_events_skipped == 1)
    wait_until(lambda: len(connection.publications) >= baseline + 2)

    new = connection.publications[baseline:]
    assert [item.topic for item in new] == [
        "sdsctl/state/radio",
        "sdsctl/events",
    ]
    assert decode_json(new[0]) == {
        "channel": "Secondary",
        "signal": 5,
    }
    event = decode_json(new[1])
    assert event["kind"] == "radio.state"
    assert new[0].retain is True
    assert new[1].retain is False
    assert worker.snapshot().event_publications == 1

    worker.stop()


def test_scanner_connection_updates_do_not_replace_scanner_info_state() -> None:
    stream = FakeEventStream()
    connection = FakeBrokerConnection()
    worker = make_worker(stream, lambda config, password: connection)
    worker.start()
    wait_until(lambda: len(connection.publications) >= 7)
    baseline = len(connection.publications)

    stream.publish(
        DaemonEventKind.SCANNER_CONNECTION,
        {
            "endpoint": "192.0.2.25:50536",
            "connected": False,
        },
    )

    wait_until(lambda: len(connection.publications) >= baseline + 2)
    new = connection.publications[baseline:]
    assert [item.topic for item in new] == [
        "sdsctl/state/scanner/connection",
        "sdsctl/events",
    ]
    assert decode_json(new[0]) == {
        "scanner_connected": False,
        "scanner_endpoint": "192.0.2.25:50536",
    }
    assert all(
        item.topic != "sdsctl/state/scanner/info"
        for item in new
    )

    worker.stop()


def test_destination_health_uses_stable_encoded_per_destination_topic() -> None:
    stream = FakeEventStream()
    connection = FakeBrokerConnection()
    worker = make_worker(stream, lambda config, password: connection)
    worker.start()
    wait_until(lambda: len(connection.publications) >= 7)
    baseline = len(connection.publications)

    stream.publish(
        DaemonEventKind.DESTINATION_HEALTH,
        {
            "sequence": 1,
            "snapshot": {
                "subscriber_id": "feed/a+b",
                "name": "County Feed",
                "state": "running",
                "health": "healthy",
                "attached": True,
            },
        },
    )

    wait_until(lambda: len(connection.publications) >= baseline + 2)
    new = connection.publications[baseline:]
    assert [item.topic for item in new] == [
        "sdsctl/state/destinations/feed%2Fa%2Bb",
        "sdsctl/events",
    ]
    assert decode_json(new[0])["health"] == "healthy"
    assert new[0].retain is True

    worker.stop()


def test_worker_detects_broker_health_failure_without_semantic_event() -> None:
    stream = FakeEventStream()
    first = FakeBrokerConnection()
    second = FakeBrokerConnection()
    calls = 0

    def first_check() -> None:
        nonlocal calls
        calls += 1
        if calls >= 3:
            raise OSError("broker disconnected")

    first.check = first_check  # type: ignore[method-assign]
    connections = [first, second]

    def factory(
        config: DaemonMqttConfiguration,
        password: str | None,
    ) -> FakeBrokerConnection:
        del config, password
        return connections.pop(0)

    worker = make_worker(
        stream,
        factory,
        config=DaemonMqttConfiguration(
            host="mqtt.example.test",
            reconnect_policy=ReconnectPolicy(
                initial_delay=0.01,
                multiplier=1.0,
                max_delay=0.01,
                max_attempts=2,
            ),
        ),
    )
    worker.start()

    wait_until(lambda: worker.snapshot().successful_connections == 2)
    wait_until(lambda: len(second.publications) >= 7)

    snapshot = worker.snapshot()
    assert snapshot.connection_attempts == 2
    assert snapshot.failures == 1
    assert first.closed
    worker.stop()


def test_worker_reconnects_with_fresh_snapshot_after_broker_failure() -> None:
    stream = FakeEventStream()
    first = FakeBrokerConnection(connect_error=OSError("broker unavailable"))
    second = FakeBrokerConnection()
    connections = [first, second]

    def factory(
        config: DaemonMqttConfiguration,
        password: str | None,
    ) -> FakeBrokerConnection:
        del config, password
        return connections.pop(0)

    worker = make_worker(
        stream,
        factory,
        config=DaemonMqttConfiguration(
            host="mqtt.example.test",
            reconnect_policy=ReconnectPolicy(
                initial_delay=0.01,
                multiplier=1.0,
                max_delay=0.01,
                max_attempts=2,
            ),
        ),
    )
    worker.start()

    wait_until(lambda: worker.snapshot().successful_connections == 1)
    wait_until(lambda: len(second.publications) >= 7)

    snapshot = worker.snapshot()
    assert snapshot.connection_attempts == 2
    assert snapshot.failures == 1
    assert snapshot.retry_attempt == 0
    assert second.publications[1].topic == "sdsctl/state/daemon"

    worker.stop()
    assert first.closed
    assert second.closed


def test_worker_resynchronizes_sequence_gap_with_authoritative_snapshot() -> None:
    stream = FakeEventStream()
    connection = FakeBrokerConnection()
    worker = make_worker(stream, lambda config, password: connection)
    worker.start()
    wait_until(lambda: len(connection.publications) >= 7)

    # Fill the bounded worker subscription quickly enough to force a gap.
    for index in range(40):
        stream.publish(
            DaemonEventKind.RADIO_STATE,
            {
                "fields": ["channel"],
                "previous": {"channel": str(index)},
                "current": {"channel": str(index + 1)},
            },
        )

    wait_until(lambda: worker.snapshot().resynchronizations >= 1)
    wait_until(
        lambda: sum(
            item.topic == "sdsctl/state/daemon"
            for item in connection.publications
        )
        >= 2
    )

    worker.stop()


def test_initial_publish_failures_exhaust_bounded_retries() -> None:
    stream = FakeEventStream()
    connections: list[FakeBrokerConnection] = []

    def factory(
        config: DaemonMqttConfiguration,
        password: str | None,
    ) -> FakeBrokerConnection:
        del config, password
        connection = FakeBrokerConnection(publish_error_after=0)
        connections.append(connection)
        return connection

    worker = make_worker(
        stream,
        factory,
        config=DaemonMqttConfiguration(
            host="mqtt.example.test",
            reconnect_policy=ReconnectPolicy(
                initial_delay=0.01,
                multiplier=1.0,
                max_delay=0.01,
                max_attempts=1,
            ),
        ),
    )
    worker.start()
    wait_until(lambda: worker.snapshot().state == "failed")

    snapshot = worker.snapshot()
    assert snapshot.connection_attempts == 2
    assert snapshot.successful_connections == 2
    assert snapshot.failures == 2
    assert snapshot.retry_attempt == 2
    assert len(connections) == 2
    assert all(connection.closed for connection in connections)

    worker.stop()


def test_local_session_failure_publishes_offline_before_retry_close() -> None:
    class FailingEventStream(FakeEventStream):
        def subscribe(self):
            raise RuntimeError("local event subscription failure")

    stream = FailingEventStream()
    connections: list[FakeBrokerConnection] = []

    def factory(
        config: DaemonMqttConfiguration,
        password: str | None,
    ) -> FakeBrokerConnection:
        del config, password
        connection = FakeBrokerConnection()
        connections.append(connection)
        return connection

    worker = make_worker(
        stream,
        factory,
        config=DaemonMqttConfiguration(
            host="mqtt.example.test",
            reconnect_policy=ReconnectPolicy(
                initial_delay=0.01,
                multiplier=1.0,
                max_delay=0.01,
                max_attempts=1,
            ),
        ),
    )
    worker.start()
    wait_until(lambda: worker.snapshot().state == "failed")

    assert len(connections) == 2
    for connection in connections:
        assert connection.publications == [
            Published(
                "sdsctl/availability",
                b"online",
                1,
                True,
            ),
            Published(
                "sdsctl/availability",
                b"offline",
                1,
                True,
            ),
        ]
        assert connection.closed

    snapshot = worker.snapshot()
    assert snapshot.failures == 2
    assert snapshot.retained_publications == 4
    worker.stop()


def test_worker_redacts_resolved_password_from_failure_diagnostics() -> None:
    stream = FakeEventStream()
    secret = "resolved-production-password"

    def factory(
        config: DaemonMqttConfiguration,
        password: str | None,
    ) -> FakeBrokerConnection:
        del config
        assert password == secret
        return FakeBrokerConnection(
            connect_error=RuntimeError(f"bad password {secret}")
        )

    worker = make_worker(
        stream,
        factory,
        config=DaemonMqttConfiguration(
            host="mqtt.example.test",
            username="scanner",
            password_environment_variable="SDSCTL_MQTT_PASSWORD",
            reconnect_policy=ReconnectPolicy(
                initial_delay=0.01,
                multiplier=1.0,
                max_delay=0.01,
                max_attempts=1,
            ),
        ),
        environ={"SDSCTL_MQTT_PASSWORD": secret},
    )
    worker.start()
    wait_until(lambda: worker.snapshot().state == "failed")

    snapshot = worker.snapshot()
    assert snapshot.failures == 2
    assert snapshot.last_error is not None
    assert secret not in snapshot.last_error
    assert "<redacted>" in snapshot.last_error

    worker.stop()


def test_worker_missing_password_reference_fails_without_factory_call() -> None:
    stream = FakeEventStream()
    calls = 0

    def factory(
        config: DaemonMqttConfiguration,
        password: str | None,
    ) -> FakeBrokerConnection:
        nonlocal calls
        del config, password
        calls += 1
        return FakeBrokerConnection()

    worker = make_worker(
        stream,
        factory,
        config=DaemonMqttConfiguration(
            host="mqtt.example.test",
            username="scanner",
            password_environment_variable="SDSCTL_MQTT_PASSWORD",
            reconnect_policy=ReconnectPolicy(
                initial_delay=0.01,
                multiplier=1.0,
                max_delay=0.01,
                max_attempts=1,
            ),
        ),
        environ={},
    )
    worker.start()
    wait_until(lambda: worker.snapshot().state == "failed")

    snapshot = worker.snapshot()
    assert calls == 0
    assert snapshot.connection_attempts == 0
    assert snapshot.failures == 2
    assert "SDSCTL_MQTT_PASSWORD" in (snapshot.last_error or "")

    worker.stop()


def test_worker_stop_interrupts_long_connect_after_grace_period() -> None:
    stream = FakeEventStream()
    entered = threading.Event()
    release = threading.Event()

    class BlockingConnection(FakeBrokerConnection):
        def connect(self) -> None:
            entered.set()
            release.wait()

        def interrupt(self) -> None:
            super().interrupt()
            release.set()

    connection = BlockingConnection()
    worker = DaemonMqttWorker(
        DaemonMqttConfiguration(host="mqtt.example.test"),
        stream,
        lambda config, password: connection,
        event_poll_interval=0.01,
        stop_timeout=0.1,
    )
    worker.start()
    assert entered.wait(timeout=1.0)

    worker.stop()

    assert connection.interrupted
    assert connection.closed
    assert worker.snapshot().state == "stopped"


def test_worker_snapshot_is_json_compatible_and_uses_aware_times() -> None:
    stream = FakeEventStream()
    connection = FakeBrokerConnection()
    initial = datetime(2026, 8, 8, 20, 0, tzinfo=UTC)
    values = iter(
        initial + timedelta(milliseconds=index)
        for index in range(100)
    )
    worker = make_worker(
        stream,
        lambda config, password: connection,
        now=lambda: next(values),
    )
    worker.start()
    wait_until(lambda: worker.snapshot().successful_connections == 1)

    payload = worker.snapshot().as_dict()
    json.dumps(payload)
    assert payload["host"] == "mqtt.example.test"
    assert payload["state"] == "connected"
    assert str(payload["state_changed_at"]).endswith("+00:00")

    worker.stop()


def test_worker_rejects_invalid_construction_and_is_one_shot() -> None:
    stream = FakeEventStream()
    connection = FakeBrokerConnection()

    with pytest.raises(ValueError, match="poll interval.*finite"):
        DaemonMqttWorker(
            DaemonMqttConfiguration(host="mqtt.example.test"),
            stream,
            lambda config, password: connection,
            event_poll_interval=0,
        )
    with pytest.raises(TypeError, match="poll interval.*number"):
        DaemonMqttWorker(
            DaemonMqttConfiguration(host="mqtt.example.test"),
            stream,
            lambda config, password: connection,
            event_poll_interval=True,  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="stop timeout.*finite"):
        DaemonMqttWorker(
            DaemonMqttConfiguration(host="mqtt.example.test"),
            stream,
            lambda config, password: connection,
            stop_timeout=float("inf"),
        )

    worker = make_worker(stream, lambda config, password: connection)
    worker.start()
    worker.start()
    wait_until(lambda: worker.snapshot().successful_connections == 1)
    worker.stop()

    with pytest.raises(RuntimeError, match="only be started once"):
        worker.start()

def test_worker_publishes_discovery_and_republishes_on_home_assistant_birth() -> None:
    stream = FakeEventStream()
    connection = FakeBrokerConnection()
    worker = make_worker(
        stream,
        lambda config, password: connection,
        config=DaemonMqttConfiguration(
            host="mqtt.example.test",
            home_assistant=DaemonMqttHomeAssistantConfiguration(
                enabled=True,
            ),
        ),
    )

    worker.start()
    wait_until(
        lambda: connection.subscriptions == [("homeassistant/status", 0)]
    )
    wait_until(
        lambda: sum(
            item.topic.startswith("homeassistant/device/sds200_")
            and item.topic.endswith("/config")
            for item in connection.publications
        )
        == 1
    )
    first = next(
        item
        for item in connection.publications
        if item.topic.startswith("homeassistant/device/sds200_")
        and item.topic.endswith("/config")
    )
    assert first.qos == 1
    assert first.retain is False
    payload = decode_json(first)
    assert payload["availability_topic"] == "sdsctl/availability"
    assert payload["components"]["channel"]["state_topic"] == (
        "sdsctl/state/radio"
    )

    birth = DaemonMqttBrokerMessage(
        topic="homeassistant/status",
        payload=b"online",
        qos=0,
        retain=False,
        duplicate=False,
        message_id=60,
    )
    connection.deliver(birth)
    wait_until(
        lambda: sum(
            item.topic == first.topic
            for item in connection.publications
        )
        == 2
    )
    assert connection.acknowledged == []

    worker.stop()


def test_worker_ignores_non_birth_home_assistant_status_payload() -> None:
    stream = FakeEventStream()
    connection = FakeBrokerConnection()
    worker = make_worker(
        stream,
        lambda config, password: connection,
        config=DaemonMqttConfiguration(
            host="mqtt.example.test",
            home_assistant=DaemonMqttHomeAssistantConfiguration(
                enabled=True,
            ),
        ),
    )

    worker.start()
    wait_until(lambda: bool(connection.subscriptions))
    wait_until(
        lambda: any(
            item.topic.startswith("homeassistant/device/sds200_")
            and item.topic.endswith("/config")
            for item in connection.publications
        )
    )
    discovery_topic = next(
        item.topic
        for item in connection.publications
        if item.topic.startswith("homeassistant/device/sds200_")
        and item.topic.endswith("/config")
    )
    baseline = sum(
        item.topic == discovery_topic
        for item in connection.publications
    )

    connection.deliver(
        DaemonMqttBrokerMessage(
            topic="homeassistant/status",
            payload=b"offline",
            qos=0,
            retain=False,
            duplicate=False,
            message_id=61,
        )
    )
    stream.publish(
        DaemonEventKind.RADIO_STATE,
        {
            "fields": ["channel"],
            "previous": {"channel": "Primary"},
            "current": {"channel": "Secondary"},
        },
    )
    wait_until(
        lambda: any(
            item.topic == "sdsctl/events"
            for item in connection.publications
        )
    )
    assert (
        sum(
            item.topic == discovery_topic
            for item in connection.publications
        )
        == baseline
    )

    worker.stop()

def test_home_assistant_discovery_republishes_after_broker_reconnect() -> None:
    stream = FakeEventStream()
    first = FakeBrokerConnection()
    second = FakeBrokerConnection()
    checks = 0

    def fail_after_initial_snapshot() -> None:
        nonlocal checks
        checks += 1
        if checks >= 3:
            raise OSError("broker disconnected")

    first.check = fail_after_initial_snapshot  # type: ignore[method-assign]
    connections = [first, second]

    def factory(
        config: DaemonMqttConfiguration,
        password: str | None,
    ) -> FakeBrokerConnection:
        del config, password
        return connections.pop(0)

    worker = make_worker(
        stream,
        factory,
        config=DaemonMqttConfiguration(
            host="mqtt.example.test",
            home_assistant=DaemonMqttHomeAssistantConfiguration(
                enabled=True,
            ),
            reconnect_policy=ReconnectPolicy(
                initial_delay=0.01,
                multiplier=1.0,
                max_delay=0.01,
                max_attempts=2,
            ),
        ),
    )

    worker.start()
    wait_until(lambda: worker.snapshot().successful_connections == 2)
    wait_until(
        lambda: second.subscriptions == [("homeassistant/status", 0)]
    )

    first_discovery = [
        item
        for item in first.publications
        if item.topic.startswith("homeassistant/device/sds200_")
        and item.topic.endswith("/config")
    ]
    second_discovery = [
        item
        for item in second.publications
        if item.topic.startswith("homeassistant/device/sds200_")
        and item.topic.endswith("/config")
    ]
    assert len(first_discovery) == 1
    assert len(second_discovery) == 1
    assert first_discovery[0].topic == second_discovery[0].topic
    assert first_discovery[0].payload == second_discovery[0].payload

    worker.stop()
    assert first.closed
    assert second.closed


def test_home_assistant_discovery_republishes_after_sequence_resynchronization() -> None:
    stream = FakeEventStream()
    connection = FakeBrokerConnection()
    worker = make_worker(
        stream,
        lambda config, password: connection,
        config=DaemonMqttConfiguration(
            host="mqtt.example.test",
            home_assistant=DaemonMqttHomeAssistantConfiguration(
                enabled=True,
            ),
        ),
    )

    worker.start()
    wait_until(
        lambda: sum(
            item.topic.startswith("homeassistant/device/sds200_")
            and item.topic.endswith("/config")
            for item in connection.publications
        )
        == 1
    )

    for index in range(40):
        stream.publish(
            DaemonEventKind.RADIO_STATE,
            {
                "fields": ["channel"],
                "previous": {"channel": str(index)},
                "current": {"channel": str(index + 1)},
            },
        )

    wait_until(lambda: worker.snapshot().resynchronizations >= 1)
    wait_until(
        lambda: sum(
            item.topic.startswith("homeassistant/device/sds200_")
            and item.topic.endswith("/config")
            for item in connection.publications
        )
        >= 2
    )

    worker.stop()


def test_home_assistant_birth_and_semantic_commands_share_connection() -> None:
    stream = FakeEventStream()
    connection = FakeBrokerConnection()
    control_api = FakeControlApi()
    worker = make_worker(
        stream,
        lambda config, password: connection,
        config=DaemonMqttConfiguration(
            host="mqtt.example.test",
            commands_enabled=True,
            home_assistant=DaemonMqttHomeAssistantConfiguration(
                enabled=True,
            ),
        ),
        control_api=control_api,
    )

    worker.start()
    wait_until(
        lambda: connection.subscriptions
        == [
            ("homeassistant/status", 0),
            ("sdsctl/commands", 1),
        ]
    )
    discovery_topic = next(
        item.topic
        for item in connection.publications
        if item.topic.startswith("homeassistant/device/sds200_")
        and item.topic.endswith("/config")
    )

    birth = DaemonMqttBrokerMessage(
        topic="homeassistant/status",
        payload=b"online",
        qos=0,
        retain=False,
        duplicate=False,
        message_id=70,
    )
    command = command_message("mqtt-ha-command", message_id=71)

    connection.deliver(birth)
    wait_until(
        lambda: sum(
            item.topic == discovery_topic
            for item in connection.publications
        )
        == 2
    )
    connection.deliver(command)
    wait_until(lambda: connection.acknowledged == [command])

    responses = [
        item
        for item in connection.publications
        if item.topic == "sdsctl/responses"
    ]
    assert len(responses) == 1
    assert decode_json(responses[0]) == {
        "ok": True,
        "protocol": DAEMON_API_PROTOCOL,
        "request_id": "mqtt-ha-command",
        "result": {"operation": "scanner.next"},
        "version": DAEMON_API_VERSION,
    }
    assert control_api.calls == [json.loads(command.payload)]
    assert birth not in connection.acknowledged

    worker.stop()


def home_assistant_control_message(
    topic: str,
    payload: bytes,
    *,
    qos: int = 0,
    retain: bool = False,
    duplicate: bool = False,
    message_id: int = 100,
) -> DaemonMqttBrokerMessage:
    return DaemonMqttBrokerMessage(
        topic=topic,
        payload=payload,
        qos=qos,
        retain=retain,
        duplicate=duplicate,
        message_id=message_id,
    )


def home_assistant_control_config() -> DaemonMqttConfiguration:
    return DaemonMqttConfiguration(
        host="mqtt.example.test",
        home_assistant=DaemonMqttHomeAssistantConfiguration(
            enabled=True,
            controls_enabled=True,
        ),
    )


def test_home_assistant_controls_require_explicit_control_api() -> None:
    stream = FakeEventStream()
    connection = FakeBrokerConnection()

    with pytest.raises(
        ValueError,
        match="require a daemon control API",
    ):
        make_worker(
            stream,
            lambda config, password: connection,
            config=home_assistant_control_config(),
        )


def test_home_assistant_control_adapter_dispatches_exact_semantic_requests() -> None:
    stream = FakeEventStream()
    connection = FakeBrokerConnection()
    control_api = FakeControlApi()
    worker = make_worker(
        stream,
        lambda config, password: connection,
        config=home_assistant_control_config(),
        control_api=control_api,
    )

    worker.start()
    wait_until(
        lambda: connection.subscriptions
        == [
            ("homeassistant/status", 0),
            (
                "sdsctl/home_assistant/control/hold/system",
                0,
            ),
            (
                "sdsctl/home_assistant/control/hold/department",
                0,
            ),
            (
                "sdsctl/home_assistant/control/hold/site",
                0,
            ),
            (
                "sdsctl/home_assistant/control/hold/channel",
                0,
            ),
            (
                "sdsctl/home_assistant/control/previous/channel",
                0,
            ),
            (
                "sdsctl/home_assistant/control/next/channel",
                0,
            ),
            (
                "sdsctl/home_assistant/control/reconnect",
                0,
            ),
        ]
    )

    messages = [
        home_assistant_control_message(
            "sdsctl/home_assistant/control/hold/system",
            b"ON",
            message_id=101,
        ),
        home_assistant_control_message(
            "sdsctl/home_assistant/control/hold/system",
            b"OFF",
            message_id=102,
        ),
        home_assistant_control_message(
            "sdsctl/home_assistant/control/reconnect",
            b"PRESS",
            message_id=103,
        ),
    ]

    for expected_calls, message in enumerate(messages, start=1):
        connection.deliver(message)
        wait_until(
            lambda expected_calls=expected_calls: (
                len(control_api.calls) == expected_calls
            )
        )

    assert control_api.calls == [
        {
            "protocol": DAEMON_API_PROTOCOL,
            "version": DAEMON_API_VERSION,
            "request_id": "home-assistant-control-1",
            "operation": "scanner.hold_state",
            "params": {
                "scope": "system",
                "held": True,
            },
        },
        {
            "protocol": DAEMON_API_PROTOCOL,
            "version": DAEMON_API_VERSION,
            "request_id": "home-assistant-control-2",
            "operation": "scanner.hold_state",
            "params": {
                "scope": "system",
                "held": False,
            },
        },
        {
            "protocol": DAEMON_API_PROTOCOL,
            "version": DAEMON_API_VERSION,
            "request_id": "home-assistant-control-3",
            "operation": "scanner.reconnect",
            "params": {},
        },
    ]
    assert connection.acknowledged == []
    assert all(
        publication.topic != "sdsctl/responses"
        for publication in connection.publications
    )
    assert (
        ("sdsctl/commands", 1)
        not in connection.subscriptions
    )

    worker.stop()


def test_home_assistant_control_adapter_rejects_unsafe_delivery_shapes() -> None:
    stream = FakeEventStream()
    connection = FakeBrokerConnection()
    control_api = FakeControlApi()
    worker = make_worker(
        stream,
        lambda config, password: connection,
        config=home_assistant_control_config(),
        control_api=control_api,
    )

    worker.start()
    wait_until(lambda: len(connection.subscriptions) == 8)

    unsafe = [
        home_assistant_control_message(
            "sdsctl/home_assistant/control/hold/channel",
            b"ON",
            retain=True,
            message_id=110,
        ),
        home_assistant_control_message(
            "sdsctl/home_assistant/control/hold/channel",
            b"ON",
            duplicate=True,
            message_id=111,
        ),
        home_assistant_control_message(
            "sdsctl/home_assistant/control/hold/channel",
            b"ON",
            qos=1,
            message_id=112,
        ),
        home_assistant_control_message(
            "sdsctl/home_assistant/control/hold/channel",
            b"INVALID",
            message_id=113,
        ),
    ]

    for message in unsafe:
        connection.deliver(message)

    valid = home_assistant_control_message(
        "sdsctl/home_assistant/control/reconnect",
        b"PRESS",
        message_id=114,
    )
    connection.deliver(valid)

    wait_until(lambda: len(control_api.calls) == 1)

    assert control_api.calls[0]["operation"] == "scanner.reconnect"
    assert worker.snapshot().failures == 0
    assert connection.acknowledged == []

    worker.stop()


def test_home_assistant_semantic_control_failure_does_not_fail_mqtt_worker(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class RejectingControlApi(FakeControlApi):
        def handle_control_payload(
            self,
            payload: object,
        ) -> DaemonApiResponse:
            self.calls.append(payload)
            assert isinstance(payload, Mapping)
            request_id = payload["request_id"]
            assert isinstance(request_id, str)
            return DaemonApiResponse.failure(
                request_id,
                DaemonApiErrorCode.CONTROL_UNAVAILABLE,
                "Scanner control unavailable.",
            )

    stream = FakeEventStream()
    connection = FakeBrokerConnection()
    control_api = RejectingControlApi()
    worker = make_worker(
        stream,
        lambda config, password: connection,
        config=home_assistant_control_config(),
        control_api=control_api,
    )

    caplog.set_level(logging.WARNING)
    worker.start()
    wait_until(lambda: len(connection.subscriptions) == 8)

    message = home_assistant_control_message(
        "sdsctl/home_assistant/control/hold/department",
        b"ON",
        message_id=120,
    )
    connection.deliver(message)

    wait_until(lambda: len(control_api.calls) == 1)
    wait_until(lambda: "control_unavailable" in caplog.text)

    snapshot = worker.snapshot()
    assert snapshot.connected is True
    assert snapshot.failures == 0
    assert "Home Assistant MQTT control rejected" in caplog.text
    assert "control_unavailable" in caplog.text
    assert connection.acknowledged == []
    assert all(
        publication.topic != "sdsctl/responses"
        for publication in connection.publications
    )

    worker.stop()

def test_home_assistant_navigation_uses_latest_ordered_radio_context(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stream = FakeEventStream()
    stream.snapshot_payload["radio_state"] = {
        "system": "County",
        "department": "Dispatch",
        "channel": "Primary",
        "channel_kind": "TGID",
        "channel_index": 400,
        "signal": 4,
        "rssi": -83.0,
    }
    connection = FakeBrokerConnection()
    control_api = FakeControlApi()
    worker = make_worker(
        stream,
        lambda config, password: connection,
        config=home_assistant_control_config(),
        control_api=control_api,
    )

    caplog.set_level(logging.WARNING)
    worker.start()
    wait_until(lambda: len(connection.subscriptions) == 8)

    previous = home_assistant_control_message(
        "sdsctl/home_assistant/control/previous/channel",
        b"PRESS",
        message_id=130,
    )
    connection.deliver(previous)
    wait_until(lambda: len(control_api.calls) == 1)

    assert control_api.calls[-1] == {
        "protocol": DAEMON_API_PROTOCOL,
        "version": DAEMON_API_VERSION,
        "request_id": "home-assistant-control-1",
        "operation": "scanner.previous",
        "params": {
            "target": "TGID",
            "first": 400,
        },
    }

    stream.publish(
        DaemonEventKind.RADIO_STATE,
        {
            "fields": ["channel_kind", "channel_index"],
            "previous": {
                "channel_kind": "TGID",
                "channel_index": 400,
            },
            "current": {
                "channel_kind": "ConvFrequency",
                "channel_index": 500,
            },
        },
    )
    wait_until(
        lambda: any(
            item.topic == "sdsctl/state/radio"
            and decode_json(item).get("channel_index") == 500
            for item in connection.publications
        )
    )

    next_message = home_assistant_control_message(
        "sdsctl/home_assistant/control/next/channel",
        b"PRESS",
        message_id=131,
    )
    connection.deliver(next_message)
    wait_until(lambda: len(control_api.calls) == 2)

    assert control_api.calls[-1] == {
        "protocol": DAEMON_API_PROTOCOL,
        "version": DAEMON_API_VERSION,
        "request_id": "home-assistant-control-2",
        "operation": "scanner.next",
        "params": {
            "target": "CFREQ",
            "first": 500,
        },
    }

    stream.publish(
        DaemonEventKind.SCANNER_CONNECTION,
        {
            "endpoint": "192.0.2.25:50536",
            "connected": False,
        },
    )
    wait_until(
        lambda: any(
            item.topic == "sdsctl/state/scanner/connection"
            and decode_json(item).get("scanner_connected") is False
            for item in connection.publications
        )
    )

    caplog.clear()
    unavailable = home_assistant_control_message(
        "sdsctl/home_assistant/control/next/channel",
        b"PRESS",
        message_id=132,
    )
    connection.deliver(unavailable)
    wait_until(lambda: "unavailable state" in caplog.text)

    assert len(control_api.calls) == 2
    assert worker.snapshot().failures == 0
    assert connection.acknowledged == []

    worker.stop()


def test_home_assistant_navigation_rejects_non_navigable_snapshot_context(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stream = FakeEventStream()
    stream.snapshot_payload["radio_state"] = {
        "channel_kind": "SrchFrequency",
        "channel_index": 600,
    }
    connection = FakeBrokerConnection()
    control_api = FakeControlApi()
    worker = make_worker(
        stream,
        lambda config, password: connection,
        config=home_assistant_control_config(),
        control_api=control_api,
    )

    caplog.set_level(logging.WARNING)
    worker.start()
    wait_until(lambda: len(connection.subscriptions) == 8)

    message = home_assistant_control_message(
        "sdsctl/home_assistant/control/previous/channel",
        b"PRESS",
        message_id=140,
    )
    connection.deliver(message)
    wait_until(lambda: "unavailable state" in caplog.text)

    assert control_api.calls == []
    assert worker.snapshot().failures == 0
    assert connection.acknowledged == []

    worker.stop()
