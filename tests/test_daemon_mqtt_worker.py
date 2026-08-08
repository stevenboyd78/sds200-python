from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from sds200 import (
    DaemonEventKind,
    DaemonEventPublisher,
    DaemonMqttConfiguration,
    DaemonMqttWorker,
    ReconnectPolicy,
)

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

    def check(self) -> None:
        return

    def interrupt(self) -> None:
        self.interrupted = True

    def close(self) -> None:
        self.closed = True


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
    environ: Mapping[str, str] | None = None,
    now: Callable[[], datetime] | None = None,
) -> DaemonMqttWorker:
    kwargs: dict[str, object] = {}
    if now is not None:
        kwargs["now"] = now
    return DaemonMqttWorker(
        config or DaemonMqttConfiguration(host="mqtt.example.test"),
        stream,
        factory,
        environ=environ,
        event_poll_interval=0.01,
        stop_timeout=1.0,
        **kwargs,  # type: ignore[arg-type]
    )


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
