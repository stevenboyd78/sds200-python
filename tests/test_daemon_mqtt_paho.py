from __future__ import annotations

from dataclasses import dataclass

import pytest

from sds200 import DaemonMqttConfiguration, DaemonMqttError
from sds200.daemon_mqtt_paho import (
    PahoMqttBrokerConnection,
    PahoMqttBrokerFactory,
)
from sds200.daemon_mqtt_worker import DaemonMqttBrokerMessage


@dataclass
class FakeMessageInfo:
    rc: int = 0
    published: bool = True
    wait_calls: list[float | None] | None = None

    def wait_for_publish(self, timeout: float | None = None) -> None:
        if self.wait_calls is not None:
            self.wait_calls.append(timeout)

    def is_published(self) -> bool:
        return self.published


class FakeCallbackApiVersion:
    VERSION2 = object()


@dataclass(frozen=True)
class FakeReasonCode:
    is_failure: bool = False


@dataclass(frozen=True)
class FakeInboundMessage:
    topic: str
    payload: bytes
    qos: int
    retain: bool
    dup: bool
    mid: int


class FakeClient:
    def __init__(
        self,
        *,
        connect_reason: object = 0,
        connect_result: int = 0,
        publish_result: int = 0,
        published: bool = True,
        subscribe_result: int = 0,
        subscribe_failure: bool = False,
        emit_suback: bool = True,
    ) -> None:
        self.connect_reason = connect_reason
        self.connect_result = connect_result
        self.publish_result = publish_result
        self.published = published
        self.subscribe_result = subscribe_result
        self.subscribe_failure = subscribe_failure
        self.emit_suback = emit_suback
        self.on_connect = None
        self.on_connect_fail = None
        self.on_disconnect = None
        self.on_message = None
        self.on_subscribe = None
        self.connect_timeout = 0.0
        self.username_calls: list[tuple[str, str | None]] = []
        self.will_calls: list[tuple[str, bytes | None, int, bool]] = []
        self.connect_calls: list[tuple[str, int, int]] = []
        self.publish_calls: list[tuple[str, bytes, int, bool]] = []
        self.subscribe_calls: list[tuple[str, int]] = []
        self.manual_ack_calls: list[bool] = []
        self.ack_calls: list[tuple[int, int]] = []
        self.disconnect_calls = 0
        self.loop_start_calls = 0
        self.loop_stop_calls = 0
        self.wait_calls: list[float | None] = []
        self._next_message_id = 1

    def username_pw_set(
        self,
        username: str,
        password: str | None = None,
    ) -> None:
        self.username_calls.append((username, password))

    def will_set(
        self,
        topic: str,
        payload: bytes | None = None,
        qos: int = 0,
        retain: bool = False,
    ) -> None:
        self.will_calls.append((topic, payload, qos, retain))

    def connect(
        self,
        host: str,
        port: int = 1883,
        keepalive: int = 60,
    ) -> int:
        self.connect_calls.append((host, port, keepalive))
        return self.connect_result

    def loop_start(self) -> int:
        self.loop_start_calls += 1
        if self.on_connect is not None:
            self.on_connect(
                self,
                None,
                object(),
                self.connect_reason,
                None,
            )
        return 0

    def publish(
        self,
        topic: str,
        payload: bytes,
        qos: int = 0,
        retain: bool = False,
    ) -> FakeMessageInfo:
        self.publish_calls.append((topic, payload, qos, retain))
        return FakeMessageInfo(
            rc=self.publish_result,
            published=self.published,
            wait_calls=self.wait_calls,
        )

    def subscribe(
        self,
        topic: str,
        qos: int = 0,
    ) -> tuple[int, int]:
        self.subscribe_calls.append((topic, qos))
        message_id = self._next_message_id
        self._next_message_id += 1
        if self.emit_suback and self.on_subscribe is not None:
            self.on_subscribe(
                self,
                None,
                message_id,
                [FakeReasonCode(self.subscribe_failure)],
                None,
            )
        return self.subscribe_result, message_id

    def manual_ack_set(self, on: bool) -> None:
        self.manual_ack_calls.append(on)

    def ack(self, mid: int, qos: int) -> int:
        self.ack_calls.append((mid, qos))
        return 0

    def disconnect(self) -> int:
        self.disconnect_calls += 1
        return 0

    def loop_stop(self) -> int:
        self.loop_stop_calls += 1
        return 0

    def emit_disconnect(self, reason: object = 7) -> None:
        assert self.on_disconnect is not None
        self.on_disconnect(
            self,
            None,
            object(),
            reason,
            None,
        )

    def emit_message(
        self,
        *,
        topic: str = "sdsctl/commands",
        payload: bytes = b"{}",
        qos: int = 1,
        retain: bool = False,
        duplicate: bool = False,
        message_id: int = 42,
    ) -> None:
        assert self.on_message is not None
        self.on_message(
            self,
            None,
            FakeInboundMessage(
                topic=topic,
                payload=payload,
                qos=qos,
                retain=retain,
                dup=duplicate,
                mid=message_id,
            ),
        )


class FakePahoModule:
    CallbackAPIVersion = FakeCallbackApiVersion
    MQTTv311 = object()
    MQTT_ERR_SUCCESS = 0

    def __init__(self, client: FakeClient) -> None:
        self.client = client
        self.client_calls: list[dict[str, object]] = []

    def Client(
        self,
        callback_api_version: object,
        client_id: str = "",
        clean_session: bool | None = None,
        protocol: object = None,
        reconnect_on_failure: bool = True,
    ) -> FakeClient:
        self.client_calls.append(
            {
                "callback_api_version": callback_api_version,
                "client_id": client_id,
                "clean_session": clean_session,
                "protocol": protocol,
                "reconnect_on_failure": reconnect_on_failure,
            }
        )
        return self.client


def make_connection(
    client: FakeClient,
    *,
    config: DaemonMqttConfiguration | None = None,
) -> PahoMqttBrokerConnection:
    module = FakePahoModule(client)
    return PahoMqttBrokerConnection(
        config or DaemonMqttConfiguration(host="mqtt.example.test"),
        None,
        mqtt=module,  # type: ignore[arg-type]
        connect_ack_timeout=0.1,
        publish_timeout=0.2,
    )


def test_factory_reports_missing_optional_dependency() -> None:
    def missing(name: str) -> object:
        raise ModuleNotFoundError(name="paho")

    with pytest.raises(DaemonMqttError, match=r"sds200\[mqtt\]"):
        PahoMqttBrokerFactory(module_loader=missing)


def test_factory_preserves_unrelated_import_failure() -> None:
    def broken(name: str) -> object:
        raise ModuleNotFoundError(name="unrelated_dependency")

    with pytest.raises(ModuleNotFoundError) as raised:
        PahoMqttBrokerFactory(module_loader=broken)

    assert raised.value.name == "unrelated_dependency"


def test_connection_configures_paho_v2_auth_will_and_no_auto_reconnect() -> None:
    client = FakeClient()
    module = FakePahoModule(client)
    config = DaemonMqttConfiguration(
        host="mqtt.example.test",
        port=1884,
        client_id="sdsctl-lab",
        username="scanner",
        topic_prefix="radio/sds200",
        qos=2,
        retain=True,
        keepalive_seconds=45,
    )

    connection = PahoMqttBrokerConnection(
        config,
        "resolved-secret",
        mqtt=module,  # type: ignore[arg-type]
    )

    assert module.client_calls == [
        {
            "callback_api_version": FakeCallbackApiVersion.VERSION2,
            "client_id": "sdsctl-lab",
            "clean_session": True,
            "protocol": module.MQTTv311,
            "reconnect_on_failure": False,
        }
    ]
    assert client.username_calls == [
        ("scanner", "resolved-secret")
    ]
    assert client.will_calls == [
        (
            "radio/sds200/availability",
            b"offline",
            2,
            True,
        )
    ]
    assert client.connect_timeout == 5.0

    connection.connect()
    assert client.connect_calls == [
        ("mqtt.example.test", 1884, 45)
    ]
    assert client.loop_start_calls == 1

    connection.close()
    assert client.disconnect_calls == 1
    assert client.loop_stop_calls == 1


def test_lwt_remains_retained_when_state_retention_is_disabled() -> None:
    client = FakeClient()
    module = FakePahoModule(client)
    config = DaemonMqttConfiguration(
        host="mqtt.example.test",
        retain=False,
    )

    connection = PahoMqttBrokerConnection(
        config,
        None,
        mqtt=module,  # type: ignore[arg-type]
    )

    assert client.will_calls == [
        (
            "sdsctl/availability",
            b"offline",
            1,
            True,
        )
    ]
    connection.close()


def test_connection_rejects_invalid_timeouts() -> None:
    client = FakeClient()
    module = FakePahoModule(client)
    config = DaemonMqttConfiguration(host="mqtt.example.test")

    with pytest.raises(TypeError, match="CONNACK timeout.*number"):
        PahoMqttBrokerConnection(
            config,
            None,
            mqtt=module,  # type: ignore[arg-type]
            connect_ack_timeout=True,  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="CONNACK timeout.*finite"):
        PahoMqttBrokerConnection(
            config,
            None,
            mqtt=module,  # type: ignore[arg-type]
            connect_ack_timeout=float("inf"),
        )
    with pytest.raises(ValueError, match="publish timeout.*finite"):
        PahoMqttBrokerConnection(
            config,
            None,
            mqtt=module,  # type: ignore[arg-type]
            publish_timeout=float("nan"),
        )


def test_commands_enabled_uses_manual_ack_and_inbound_queue() -> None:
    client = FakeClient()
    connection = make_connection(
        client,
        config=DaemonMqttConfiguration(
            host="mqtt.example.test",
            commands_enabled=True,
        ),
    )

    assert client.manual_ack_calls == [True]

    connection.connect()
    connection.subscribe("sdsctl/commands", qos=1)
    assert client.subscribe_calls == [("sdsctl/commands", 1)]

    client.emit_message(
        payload=b'{"request_id":"mqtt-1"}',
        duplicate=True,
        message_id=17,
    )
    message = connection.receive(timeout=0.1)

    assert message == DaemonMqttBrokerMessage(
        topic="sdsctl/commands",
        payload=b'{"request_id":"mqtt-1"}',
        qos=1,
        retain=False,
        duplicate=True,
        message_id=17,
    )

    assert message is not None
    connection.acknowledge(message)
    assert client.ack_calls == [(17, 1)]
    connection.close()


def test_commands_disabled_rejects_subscription_without_manual_ack() -> None:
    client = FakeClient()
    connection = make_connection(client)
    connection.connect()

    assert client.manual_ack_calls == []
    with pytest.raises(DaemonMqttError, match="subscriptions are disabled"):
        connection.subscribe("sdsctl/commands", qos=1)

    assert client.subscribe_calls == []
    connection.close()


def test_subscription_rejection_is_reported() -> None:
    client = FakeClient(subscribe_failure=True)
    connection = make_connection(
        client,
        config=DaemonMqttConfiguration(
            host="mqtt.example.test",
            commands_enabled=True,
        ),
    )
    connection.connect()

    with pytest.raises(DaemonMqttError, match="rejected subscription"):
        connection.subscribe("sdsctl/commands", qos=1)

    connection.close()


def test_inbound_queue_overflow_marks_connection_failed() -> None:
    client = FakeClient()
    module = FakePahoModule(client)
    connection = PahoMqttBrokerConnection(
        DaemonMqttConfiguration(
            host="mqtt.example.test",
            commands_enabled=True,
        ),
        None,
        mqtt=module,  # type: ignore[arg-type]
        inbound_queue_capacity=1,
    )
    connection.connect()

    client.emit_message(message_id=1)
    client.emit_message(message_id=2)

    with pytest.raises(DaemonMqttError, match="queue capacity"):
        connection.check()

    connection.close()


def test_connection_publishes_and_waits_for_completion() -> None:
    client = FakeClient()
    connection = make_connection(client)
    connection.connect()

    connection.publish(
        "sdsctl/state/radio",
        b'{"channel":"Primary"}',
        qos=1,
        retain=True,
    )

    assert client.publish_calls == [
        (
            "sdsctl/state/radio",
            b'{"channel":"Primary"}',
            1,
            True,
        )
    ]
    assert client.wait_calls == [0.2]
    connection.close()


def test_connection_rejects_connack_failure() -> None:
    client = FakeClient(connect_reason=5)
    connection = make_connection(client)

    with pytest.raises(DaemonMqttError, match="rejected connection"):
        connection.connect()

    connection.close()


def test_connection_rejects_publish_result_and_timeout() -> None:
    failed = FakeClient(publish_result=4)
    connection = make_connection(failed)
    connection.connect()
    with pytest.raises(DaemonMqttError, match="publish failed"):
        connection.publish("sdsctl/events", b"{}", qos=1, retain=False)
    connection.close()

    timed_out = FakeClient(published=False)
    connection = make_connection(timed_out)
    connection.connect()
    with pytest.raises(DaemonMqttError, match="publication acknowledgement"):
        connection.publish("sdsctl/events", b"{}", qos=1, retain=False)
    connection.close()


def test_connection_health_reports_async_disconnect() -> None:
    client = FakeClient()
    connection = make_connection(client)
    connection.connect()

    client.emit_disconnect(reason=7)

    with pytest.raises(DaemonMqttError, match="connection was lost"):
        connection.check()

    connection.close()


def test_connection_close_is_idempotent() -> None:
    client = FakeClient()
    connection = make_connection(client)
    connection.connect()

    connection.close()
    connection.close()

    assert client.disconnect_calls == 1
    assert client.loop_stop_calls == 1
