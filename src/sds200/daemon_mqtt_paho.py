from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from contextlib import suppress
from importlib import import_module
from math import isfinite
from time import monotonic
from typing import Protocol, cast

from .daemon_mqtt import DaemonMqttConfiguration
from .daemon_mqtt_worker import DaemonMqttBrokerMessage
from .exceptions import DaemonMqttError

DAEMON_MQTT_PAHO_CONNECT_ACK_TIMEOUT = 5.0
DAEMON_MQTT_PAHO_PUBLISH_TIMEOUT = 5.0
DAEMON_MQTT_PAHO_SUBSCRIBE_ACK_TIMEOUT = 5.0
DAEMON_MQTT_PAHO_INBOUND_QUEUE_CAPACITY = 32
DAEMON_MQTT_INSTALL_ERROR = (
    "MQTT support is not installed; install it with: "
    'python -m pip install "sds200[mqtt]"'
)


class _PahoMessageInfo(Protocol):
    rc: object

    def wait_for_publish(self, timeout: float | None = None) -> None: ...

    def is_published(self) -> bool: ...


class _PahoInboundMessage(Protocol):
    topic: str
    payload: bytes
    qos: int
    retain: bool
    dup: bool
    mid: int


class _PahoClient(Protocol):
    on_connect: object
    on_connect_fail: object
    on_disconnect: object
    on_message: object
    on_subscribe: object
    connect_timeout: float

    def username_pw_set(
        self,
        username: str,
        password: str | None = None,
    ) -> None: ...

    def will_set(
        self,
        topic: str,
        payload: bytes | None = None,
        qos: int = 0,
        retain: bool = False,
    ) -> None: ...

    def connect(
        self,
        host: str,
        port: int = 1883,
        keepalive: int = 60,
    ) -> object: ...

    def loop_start(self) -> object: ...

    def publish(
        self,
        topic: str,
        payload: bytes,
        qos: int = 0,
        retain: bool = False,
    ) -> _PahoMessageInfo: ...

    def subscribe(
        self,
        topic: str,
        qos: int = 0,
    ) -> tuple[object, int | None]: ...

    def manual_ack_set(self, on: bool) -> None: ...

    def ack(self, mid: int, qos: int) -> object: ...

    def disconnect(self) -> object: ...

    def loop_stop(self) -> object: ...


class _CallbackApiVersion(Protocol):
    VERSION2: object


class _PahoModule(Protocol):
    CallbackAPIVersion: _CallbackApiVersion
    MQTTv311: object
    MQTT_ERR_SUCCESS: object

    def Client(
        self,
        callback_api_version: object,
        client_id: str = "",
        clean_session: bool | None = None,
        protocol: object = ...,
        reconnect_on_failure: bool = True,
    ) -> _PahoClient: ...


PahoModuleLoader = Callable[[str], object]


def _require_positive_seconds(label: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be a number.")
    normalized = float(value)
    if not isfinite(normalized) or normalized <= 0:
        raise ValueError(
            f"{label} must be finite and greater than zero."
        )
    return normalized


def _require_non_negative_seconds(label: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be a number.")
    normalized = float(value)
    if not isfinite(normalized) or normalized < 0:
        raise ValueError(
            f"{label} must be finite and not negative."
        )
    return normalized


def _require_positive_integer(label: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer.")
    if value <= 0:
        raise ValueError(f"{label} must be greater than zero.")
    return value


def _subscription_reason_failed(reason_code: object) -> bool:
    failure = getattr(reason_code, "is_failure", None)
    if isinstance(failure, bool):
        return failure
    if isinstance(reason_code, int):
        return reason_code >= 0x80
    value = getattr(reason_code, "value", None)
    return isinstance(value, int) and value >= 0x80


def _load_paho_mqtt(
    module_loader: PahoModuleLoader = import_module,
) -> _PahoModule:
    try:
        return cast(_PahoModule, module_loader("paho.mqtt.client"))
    except ModuleNotFoundError as error:
        missing = error.name or ""
        if missing == "paho" or missing.startswith("paho.mqtt"):
            raise DaemonMqttError(DAEMON_MQTT_INSTALL_ERROR) from error
        raise


class PahoMqttBrokerFactory:
    """Create isolated Paho-backed broker connections for one daemon worker."""

    def __init__(
        self,
        *,
        module_loader: PahoModuleLoader = import_module,
    ) -> None:
        self._mqtt = _load_paho_mqtt(module_loader)

    def __call__(
        self,
        config: DaemonMqttConfiguration,
        password: str | None,
    ) -> PahoMqttBrokerConnection:
        return PahoMqttBrokerConnection(
            config,
            password,
            mqtt=self._mqtt,
        )


class PahoMqttBrokerConnection:
    """One Paho MQTT 3.1.1 session with worker-owned reconnect semantics."""

    def __init__(
        self,
        config: DaemonMqttConfiguration,
        password: str | None,
        *,
        mqtt: _PahoModule,
        connect_ack_timeout: float = DAEMON_MQTT_PAHO_CONNECT_ACK_TIMEOUT,
        publish_timeout: float = DAEMON_MQTT_PAHO_PUBLISH_TIMEOUT,
        subscribe_ack_timeout: float = DAEMON_MQTT_PAHO_SUBSCRIBE_ACK_TIMEOUT,
        inbound_queue_capacity: int = DAEMON_MQTT_PAHO_INBOUND_QUEUE_CAPACITY,
    ) -> None:
        if not isinstance(config, DaemonMqttConfiguration):
            raise TypeError(
                "Paho MQTT connections require a DaemonMqttConfiguration."
            )
        validated_connect_ack_timeout = _require_positive_seconds(
            "Paho MQTT CONNACK timeout",
            connect_ack_timeout,
        )
        validated_publish_timeout = _require_positive_seconds(
            "Paho MQTT publish timeout",
            publish_timeout,
        )
        validated_subscribe_ack_timeout = _require_positive_seconds(
            "Paho MQTT SUBACK timeout",
            subscribe_ack_timeout,
        )
        validated_inbound_queue_capacity = _require_positive_integer(
            "Paho MQTT inbound queue capacity",
            inbound_queue_capacity,
        )

        self.config = config
        self.connect_ack_timeout = validated_connect_ack_timeout
        self.publish_timeout = validated_publish_timeout
        self.subscribe_ack_timeout = validated_subscribe_ack_timeout
        self.inbound_queue_capacity = validated_inbound_queue_capacity
        self._mqtt = mqtt
        self._lock = threading.RLock()
        self._subscription_condition = threading.Condition(self._lock)
        self._connect_result = threading.Event()
        self._subscription_results: dict[int, tuple[object, ...]] = {}
        self._inbound_messages: queue.Queue[DaemonMqttBrokerMessage] = (
            queue.Queue(maxsize=self.inbound_queue_capacity)
        )
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=config.client_id or "",
            clean_session=True,
            protocol=mqtt.MQTTv311,
            reconnect_on_failure=False,
        )
        self._client.connect_timeout = self.connect_ack_timeout
        self._client.on_connect = self._on_connect
        self._client.on_connect_fail = self._on_connect_fail
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._client.on_subscribe = self._on_subscribe
        if config.commands_enabled:
            self._client.manual_ack_set(True)

        if config.username is not None:
            self._client.username_pw_set(
                config.username,
                password,
            )
        self._client.will_set(
            f"{config.topic_prefix}/availability",
            payload=b"offline",
            qos=config.qos,
            retain=True,
        )

        self._connected = False
        self._closing = False
        self._closed = False
        self._loop_started = False
        self._failure: DaemonMqttError | None = None

    def connect(self) -> None:
        with self._lock:
            if self._closed:
                raise DaemonMqttError(
                    "Cannot connect a closed Paho MQTT broker connection."
                )
            if self._connected:
                return
            self._connect_result.clear()
            self._failure = None

        result = self._client.connect(
            self.config.host,
            self.config.port,
            self.config.keepalive_seconds,
        )
        self._require_success(result, operation="connect")

        with self._lock:
            if self._closing or self._closed:
                raise DaemonMqttError(
                    "MQTT broker connection was interrupted during connect."
                )

        loop_result = self._client.loop_start()
        self._require_success(
            loop_result,
            operation="network loop start",
            allow_none=True,
        )
        with self._lock:
            self._loop_started = True
            interrupted = self._closing or self._closed

        if interrupted:
            try:
                self._client.loop_stop()
            finally:
                with self._lock:
                    self._loop_started = False
            raise DaemonMqttError(
                "MQTT broker connection was interrupted during connect."
            )

        if not self._connect_result.wait(self.connect_ack_timeout):
            self.close()
            raise DaemonMqttError(
                "Timed out waiting for MQTT broker CONNACK."
            )

        self.check()

    def check(self) -> None:
        with self._lock:
            failure = self._failure
            connected = self._connected
            closed = self._closed
        if failure is not None:
            raise failure
        if closed:
            raise DaemonMqttError("MQTT broker connection is closed.")
        if not connected:
            raise DaemonMqttError(
                "MQTT broker connection is not active."
            )

    def publish(
        self,
        topic: str,
        payload: bytes,
        *,
        qos: int,
        retain: bool,
    ) -> None:
        self.check()
        info = self._client.publish(
            topic,
            payload,
            qos=qos,
            retain=retain,
        )
        self._require_success(info.rc, operation="publish")
        info.wait_for_publish(timeout=self.publish_timeout)
        if not info.is_published():
            self.check()
            raise DaemonMqttError(
                "Timed out waiting for MQTT publication acknowledgement."
            )
        self.check()

    def subscribe(self, topic: str, *, qos: int) -> None:
        if not self.config.commands_enabled:
            raise DaemonMqttError(
                "MQTT command subscriptions are disabled."
            )
        if not isinstance(topic, str) or not topic:
            raise ValueError("MQTT subscription topic must not be empty.")
        if isinstance(qos, bool) or not isinstance(qos, int):
            raise TypeError("MQTT subscription QoS must be an integer.")
        if not 0 <= qos <= 2:
            raise ValueError("MQTT subscription QoS must be between 0 and 2.")

        self.check()
        result, message_id = self._client.subscribe(topic, qos=qos)
        self._require_success(result, operation="subscribe")
        if message_id is None:
            raise DaemonMqttError(
                "Paho MQTT subscribe did not return a message ID."
            )

        deadline = monotonic() + self.subscribe_ack_timeout
        with self._subscription_condition:
            while message_id not in self._subscription_results:
                if (
                    self._failure is not None
                    or self._closed
                    or not self._connected
                ):
                    break
                remaining = deadline - monotonic()
                if remaining <= 0:
                    break
                self._subscription_condition.wait(timeout=remaining)
            reason_codes = self._subscription_results.pop(
                message_id,
                None,
            )

        if reason_codes is None:
            self.check()
            raise DaemonMqttError(
                "Timed out waiting for MQTT broker SUBACK."
            )
        if any(
            _subscription_reason_failed(reason_code)
            for reason_code in reason_codes
        ):
            raise DaemonMqttError(
                "MQTT broker rejected subscription."
            )
        self.check()

    def receive(
        self,
        *,
        timeout: float,
    ) -> DaemonMqttBrokerMessage | None:
        normalized_timeout = _require_non_negative_seconds(
            "Paho MQTT receive timeout",
            timeout,
        )
        self.check()
        try:
            message = self._inbound_messages.get(
                timeout=normalized_timeout
            )
        except queue.Empty:
            self.check()
            return None
        self.check()
        return message

    def acknowledge(self, message: DaemonMqttBrokerMessage) -> None:
        if not isinstance(message, DaemonMqttBrokerMessage):
            raise TypeError(
                "MQTT acknowledgement requires a broker message."
            )
        if message.qos == 0:
            return
        self.check()
        result = self._client.ack(message.message_id, message.qos)
        self._require_success(result, operation="acknowledgement")
        self.check()

    def interrupt(self) -> None:
        self._close_client()

    def close(self) -> None:
        self._close_client()

    def _close_client(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closing = True
            loop_started = self._loop_started

        try:
            if loop_started:
                with suppress(Exception):
                    self._client.disconnect()
        finally:
            if loop_started:
                with suppress(Exception):
                    self._client.loop_stop()
            with self._subscription_condition:
                self._connected = False
                self._closed = True
                self._loop_started = False
                self._connect_result.set()
                self._subscription_condition.notify_all()

    def _on_connect(
        self,
        client: object,
        userdata: object,
        flags: object,
        reason_code: object,
        properties: object,
    ) -> None:
        del client, userdata, flags, properties
        with self._lock:
            if self._closing or self._closed:
                self._connect_result.set()
                return
            if reason_code == 0:
                self._connected = True
                self._failure = None
            else:
                self._connected = False
                self._failure = DaemonMqttError(
                    "MQTT broker rejected connection "
                    f"(reason={reason_code})."
                )
            self._connect_result.set()

    def _on_connect_fail(
        self,
        client: object,
        userdata: object,
    ) -> None:
        del client, userdata
        with self._lock:
            if not self._closing and not self._closed:
                self._failure = DaemonMqttError(
                    "MQTT broker connection attempt failed."
                )
            self._connected = False
            self._connect_result.set()

    def _on_subscribe(
        self,
        client: object,
        userdata: object,
        message_id: int,
        reason_codes: list[object],
        properties: object,
    ) -> None:
        del client, userdata, properties
        with self._subscription_condition:
            if self._closing or self._closed:
                return
            self._subscription_results[message_id] = tuple(reason_codes)
            self._subscription_condition.notify_all()

    def _on_message(
        self,
        client: object,
        userdata: object,
        message: _PahoInboundMessage,
    ) -> None:
        del client, userdata
        if not self.config.commands_enabled:
            return

        try:
            inbound = DaemonMqttBrokerMessage(
                topic=message.topic,
                payload=bytes(message.payload),
                qos=message.qos,
                retain=message.retain,
                duplicate=message.dup,
                message_id=message.mid,
            )
        except (TypeError, ValueError):
            with self._subscription_condition:
                if not self._closing and not self._closed:
                    self._failure = DaemonMqttError(
                        "MQTT broker delivered an invalid inbound message."
                    )
                    self._subscription_condition.notify_all()
            return

        try:
            self._inbound_messages.put_nowait(inbound)
        except queue.Full:
            with self._subscription_condition:
                if not self._closing and not self._closed:
                    self._failure = DaemonMqttError(
                        "MQTT inbound message queue capacity was exceeded."
                    )
                    self._subscription_condition.notify_all()

    def _on_disconnect(
        self,
        client: object,
        userdata: object,
        disconnect_flags: object,
        reason_code: object,
        properties: object,
    ) -> None:
        del client, userdata, disconnect_flags, properties
        with self._subscription_condition:
            self._connected = False
            if not self._closing and not self._closed:
                self._failure = DaemonMqttError(
                    "MQTT broker connection was lost "
                    f"(reason={reason_code})."
                )
            self._connect_result.set()
            self._subscription_condition.notify_all()

    def _require_success(
        self,
        result: object,
        *,
        operation: str,
        allow_none: bool = False,
    ) -> None:
        if allow_none and result is None:
            return
        if result != self._mqtt.MQTT_ERR_SUCCESS:
            raise DaemonMqttError(
                f"Paho MQTT {operation} failed (code={result})."
            )
