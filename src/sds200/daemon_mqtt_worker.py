from __future__ import annotations

import json
import logging
import os
import queue
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from typing import Literal, Protocol
from urllib.parse import quote

from .daemon_events import (
    DaemonEvent,
    DaemonEventKind,
    DaemonEventSubscription,
    DaemonEventSubscriptionClosed,
)
from .daemon_mqtt import DaemonMqttConfiguration

logger = logging.getLogger(__name__)

DAEMON_MQTT_DEFAULT_EVENT_POLL_INTERVAL = 0.25
DAEMON_MQTT_DEFAULT_STOP_TIMEOUT = 5.0

DaemonMqttWorkerState = Literal[
    "idle",
    "connecting",
    "connected",
    "backoff",
    "failed",
    "stopping",
    "stopped",
]


@dataclass(frozen=True, slots=True)
class DaemonMqttBrokerMessage:
    # One immutable inbound MQTT message handed off by a broker adapter.

    topic: str
    payload: bytes
    qos: int
    retain: bool
    duplicate: bool
    message_id: int

    def __post_init__(self) -> None:
        if not isinstance(self.topic, str) or not self.topic:
            raise ValueError("MQTT inbound message topic must not be empty.")
        if not isinstance(self.payload, bytes):
            raise TypeError("MQTT inbound message payload must be bytes.")
        if isinstance(self.qos, bool) or not isinstance(self.qos, int):
            raise TypeError("MQTT inbound message QoS must be an integer.")
        if not 0 <= self.qos <= 2:
            raise ValueError("MQTT inbound message QoS must be between 0 and 2.")
        if not isinstance(self.retain, bool):
            raise TypeError("MQTT inbound retain flag must be a boolean.")
        if not isinstance(self.duplicate, bool):
            raise TypeError("MQTT inbound duplicate flag must be a boolean.")
        if (
            isinstance(self.message_id, bool)
            or not isinstance(self.message_id, int)
        ):
            raise TypeError("MQTT inbound message ID must be an integer.")
        if self.message_id < 0:
            raise ValueError("MQTT inbound message ID must not be negative.")


class DaemonMqttBrokerConnection(Protocol):
    """One interruptible blocking broker connection owned by the MQTT worker."""

    def connect(self) -> None: ...

    def publish(
        self,
        topic: str,
        payload: bytes,
        *,
        qos: int,
        retain: bool,
    ) -> None: ...

    def subscribe(self, topic: str, *, qos: int) -> None: ...

    def receive(
        self,
        *,
        timeout: float,
    ) -> DaemonMqttBrokerMessage | None: ...

    def acknowledge(self, message: DaemonMqttBrokerMessage) -> None: ...

    def check(self) -> None: ...

    def interrupt(self) -> None: ...

    def close(self) -> None: ...


DaemonMqttBrokerFactory = Callable[
    [DaemonMqttConfiguration, str | None],
    DaemonMqttBrokerConnection,
]


class _DaemonEventStreamLike(Protocol):
    def subscribe(self) -> DaemonEventSubscription: ...


@dataclass(frozen=True, slots=True)
class DaemonMqttWorkerSnapshot:
    """Immutable operational state for the daemon MQTT publication worker."""

    host: str
    port: int
    topic_prefix: str
    state: DaemonMqttWorkerState
    running: bool
    connected: bool
    connection_attempts: int
    successful_connections: int
    publications: int
    retained_publications: int
    event_publications: int
    psi_events_skipped: int
    resynchronizations: int
    failures: int
    retry_attempt: int
    next_retry_delay: float | None
    state_changed_at: datetime
    last_connected_at: datetime | None
    last_published_at: datetime | None
    last_failure_at: datetime | None
    last_error: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "host": self.host,
            "port": self.port,
            "topic_prefix": self.topic_prefix,
            "state": self.state,
            "running": self.running,
            "connected": self.connected,
            "connection_attempts": self.connection_attempts,
            "successful_connections": self.successful_connections,
            "publications": self.publications,
            "retained_publications": self.retained_publications,
            "event_publications": self.event_publications,
            "psi_events_skipped": self.psi_events_skipped,
            "resynchronizations": self.resynchronizations,
            "failures": self.failures,
            "retry_attempt": self.retry_attempt,
            "next_retry_delay": self.next_retry_delay,
            "state_changed_at": self.state_changed_at.isoformat(),
            "last_connected_at": _isoformat(self.last_connected_at),
            "last_published_at": _isoformat(self.last_published_at),
            "last_failure_at": _isoformat(self.last_failure_at),
            "last_error": self.last_error,
        }


@dataclass(frozen=True, slots=True)
class _MqttPublication:
    topic: str
    payload: bytes
    retain: bool
    event: bool = False


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _require_aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(
            "Daemon MQTT wall clock must return a timezone-aware datetime."
        )
    return value


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _require_positive_seconds(label: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be a number.")
    normalized = float(value)
    if not isfinite(normalized) or normalized <= 0:
        raise ValueError(
            f"{label} must be finite and greater than zero."
        )
    return normalized


def _json_compatible(value: object) -> object:
    if isinstance(value, Mapping):
        converted: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(
                    "Daemon MQTT JSON mappings require string keys."
                )
            converted[key] = _json_compatible(item)
        return converted
    if isinstance(value, (list, tuple)):
        return [_json_compatible(item) for item in value]
    return value


def _json_payload(value: Mapping[str, object]) -> bytes:
    return json.dumps(
        _json_compatible(value),
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _redact_error(
    error: BaseException,
    secret_values: tuple[str, ...],
) -> str:
    message = f"{type(error).__name__}: {error}"
    for value in sorted(
        (secret for secret in secret_values if secret),
        key=len,
        reverse=True,
    ):
        message = message.replace(value, "<redacted>")
    return message


class DaemonMqttWorker:
    """Mirror semantic daemon state to one broker without blocking daemon events."""

    def __init__(
        self,
        config: DaemonMqttConfiguration,
        event_stream: _DaemonEventStreamLike,
        broker_factory: DaemonMqttBrokerFactory,
        *,
        environ: Mapping[str, str] | None = None,
        event_poll_interval: float = DAEMON_MQTT_DEFAULT_EVENT_POLL_INTERVAL,
        stop_timeout: float = DAEMON_MQTT_DEFAULT_STOP_TIMEOUT,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not isinstance(config, DaemonMqttConfiguration):
            raise TypeError(
                "Daemon MQTT workers require a DaemonMqttConfiguration."
            )
        validated_event_poll_interval = _require_positive_seconds(
            "Daemon MQTT event poll interval",
            event_poll_interval,
        )
        validated_stop_timeout = _require_positive_seconds(
            "Daemon MQTT stop timeout",
            stop_timeout,
        )

        initial_at = _require_aware_datetime(now())
        self.config = config
        self.event_stream = event_stream
        self.broker_factory = broker_factory
        self._environ = None if environ is None else dict(environ)
        self.event_poll_interval = validated_event_poll_interval
        self.stop_timeout = validated_stop_timeout
        self._now = now

        self._condition = threading.Condition(threading.RLock())
        self._thread: threading.Thread | None = None
        self._active_connection: DaemonMqttBrokerConnection | None = None
        self._active_subscription: DaemonEventSubscription | None = None
        self._active_secret_values: tuple[str, ...] = ()
        self._started = False
        self._stopping = False
        self._stopped = False
        self._state: DaemonMqttWorkerState = "idle"
        self._state_changed_at = initial_at
        self._connected = False
        self._connection_attempts = 0
        self._successful_connections = 0
        self._publications = 0
        self._retained_publications = 0
        self._event_publications = 0
        self._psi_events_skipped = 0
        self._resynchronizations = 0
        self._failures = 0
        self._retry_attempt = 0
        self._next_retry_delay: float | None = None
        self._last_connected_at: datetime | None = None
        self._last_published_at: datetime | None = None
        self._last_failure_at: datetime | None = None
        self._last_error: str | None = None

    @property
    def running(self) -> bool:
        with self._condition:
            thread = self._thread
            return (
                thread is not None
                and thread.is_alive()
                and not self._stopped
            )

    def snapshot(self) -> DaemonMqttWorkerSnapshot:
        with self._condition:
            return self._snapshot_locked()

    def start(self) -> None:
        with self._condition:
            if self._started:
                if self._stopped:
                    raise RuntimeError(
                        "Daemon MQTT workers can only be started once."
                    )
                return

            self._started = True
            self._set_state_locked("connecting")
            thread = threading.Thread(
                target=self._run,
                name="sds200-daemon-mqtt",
                daemon=True,
            )
            self._thread = thread
            thread.start()

        logger.info(
            "daemon MQTT worker started broker=%s:%d topic_prefix=%s",
            self.config.host,
            self.config.port,
            self.config.topic_prefix,
        )

    def stop(self) -> None:
        with self._condition:
            if not self._started or self._stopped:
                return
            if not self._stopping:
                self._stopping = True
                self._set_state_locked("stopping")
                self._condition.notify_all()
            thread = self._thread
            subscription = self._active_subscription

        if subscription is not None:
            subscription.close()

        if thread is threading.current_thread():
            return

        first_wait = self.stop_timeout / 2.0
        if thread is not None:
            thread.join(timeout=first_wait)

        if thread is not None and thread.is_alive():
            with self._condition:
                connection = self._active_connection
                secret_values = self._active_secret_values
            if connection is not None:
                try:
                    connection.interrupt()
                except Exception as error:
                    self._record_failure(error, secret_values)
            thread.join(timeout=self.stop_timeout - first_wait)

        if thread is not None and thread.is_alive():
            raise RuntimeError(
                "Timed out while stopping daemon MQTT worker."
            )

        logger.info(
            "daemon MQTT worker stopped broker=%s:%d",
            self.config.host,
            self.config.port,
        )

    def close(self) -> None:
        self.stop()

    def _run(self) -> None:
        try:
            while True:
                with self._condition:
                    if self._stopping:
                        return

                secret_values: tuple[str, ...] = ()
                connection: DaemonMqttBrokerConnection | None = None
                availability_online = False
                terminal_failure = False
                try:
                    password = self._resolve_password()
                    secret_values = (
                        () if password is None else (password,)
                    )
                    connection = self.broker_factory(
                        self.config,
                        password,
                    )
                    with self._condition:
                        if self._stopping:
                            connection.interrupt()
                            return
                        self._active_connection = connection
                        self._active_secret_values = secret_values
                        self._connection_attempts += 1
                        self._set_state_locked("connecting")

                    connection.connect()
                    observed_at = _require_aware_datetime(self._now())
                    with self._condition:
                        if self._stopping:
                            return
                        self._connected = True
                        self._successful_connections += 1
                        self._last_connected_at = observed_at
                        self._set_state_locked(
                            "connected",
                            observed_at=observed_at,
                        )

                    self._publish_availability(connection, online=True)
                    availability_online = True
                    self._consume_connected(connection)
                    with self._condition:
                        stopping = self._stopping
                    if stopping:
                        return
                    raise RuntimeError(
                        "Daemon MQTT event subscription ended unexpectedly."
                    )
                except Exception as error:
                    with self._condition:
                        stopping = self._stopping
                    if stopping:
                        return
                    self._record_failure(error, secret_values)
                    if not self._backoff_after_failure():
                        terminal_failure = True
                finally:
                    subscription: DaemonEventSubscription | None
                    with self._condition:
                        subscription = self._active_subscription
                        self._active_subscription = None
                        if self._active_connection is connection:
                            self._active_connection = None
                            self._active_secret_values = ()
                        self._connected = False
                    if subscription is not None:
                        subscription.close()
                    if connection is not None:
                        if availability_online:
                            self._publish_availability(
                                connection,
                                online=False,
                                suppress_errors=True,
                            )
                        try:
                            connection.close()
                        except Exception as error:
                            self._record_failure(error, secret_values)

                if terminal_failure:
                    with self._condition:
                        if self._stopping:
                            return
                        self._set_state_locked("failed")
                        self._condition.notify_all()
                        self._condition.wait_for(
                            lambda: self._stopping
                        )
                    return
        finally:
            with self._condition:
                self._active_connection = None
                self._active_subscription = None
                self._active_secret_values = ()
                self._connected = False
                self._thread = None
                self._stopped = True
                self._next_retry_delay = None
                self._set_state_locked("stopped")
                self._condition.notify_all()

    def _consume_connected(
        self,
        connection: DaemonMqttBrokerConnection,
    ) -> None:
        subscription = self.event_stream.subscribe()
        with self._condition:
            if self._stopping:
                subscription.close()
                return
            self._active_subscription = subscription

        expected_sequence: int | None = None
        while True:
            with self._condition:
                if self._stopping:
                    return

            connection.check()
            try:
                event = subscription.get(
                    timeout=self.event_poll_interval
                )
            except queue.Empty:
                continue
            except DaemonEventSubscriptionClosed:
                with self._condition:
                    if self._stopping:
                        return
                raise

            if event.kind == DaemonEventKind.SNAPSHOT:
                expected_sequence = event.sequence + 1
                self._publish_event(connection, event)
                self._mark_connection_healthy()
                continue

            if (
                expected_sequence is not None
                and event.sequence != expected_sequence
            ):
                subscription.close()
                with self._condition:
                    self._resynchronizations += 1
                    if self._active_subscription is subscription:
                        self._active_subscription = None
                subscription = self.event_stream.subscribe()
                with self._condition:
                    if self._stopping:
                        subscription.close()
                        return
                    self._active_subscription = subscription
                expected_sequence = None
                continue

            expected_sequence = event.sequence + 1
            self._publish_event(connection, event)

    def _publish_event(
        self,
        connection: DaemonMqttBrokerConnection,
        event: DaemonEvent,
    ) -> None:
        if event.kind == DaemonEventKind.PSI_STATE:
            with self._condition:
                self._psi_events_skipped += 1
            return

        for publication in self._publications_for_event(event):
            connection.publish(
                publication.topic,
                publication.payload,
                qos=self.config.qos,
                retain=publication.retain,
            )
            observed_at = _require_aware_datetime(self._now())
            with self._condition:
                self._publications += 1
                if publication.retain:
                    self._retained_publications += 1
                if publication.event:
                    self._event_publications += 1
                self._last_published_at = observed_at

    def _publications_for_event(
        self,
        event: DaemonEvent,
    ) -> tuple[_MqttPublication, ...]:
        prefix = self.config.topic_prefix
        retained = self.config.retain
        state: list[_MqttPublication] = []

        if event.kind == DaemonEventKind.SNAPSHOT:
            state.extend(
                self._snapshot_publications(
                    event.payload,
                    retain=retained,
                )
            )
        elif event.kind == DaemonEventKind.DAEMON_TRANSITION:
            snapshot = event.payload.get("snapshot")
            if isinstance(snapshot, Mapping):
                state.extend(
                    self._runtime_snapshot_publications(
                        snapshot,
                        retain=retained,
                    )
                )
        elif event.kind == DaemonEventKind.SCANNER_CONNECTION:
            scanner_connection = {
                "scanner_endpoint": event.payload.get("endpoint"),
                "scanner_connected": event.payload.get("connected"),
            }
            state.append(
                _MqttPublication(
                    f"{prefix}/state/scanner/connection",
                    _json_payload(scanner_connection),
                    retained,
                )
            )
        elif event.kind == DaemonEventKind.RADIO_STATE:
            current = event.payload.get("current")
            if isinstance(current, Mapping):
                state.append(
                    _MqttPublication(
                        f"{prefix}/state/radio",
                        _json_payload(current),
                        retained,
                    )
                )
        elif event.kind == DaemonEventKind.AUDIO_STATE:
            state.append(
                _MqttPublication(
                    f"{prefix}/state/audio",
                    _json_payload(event.payload),
                    retained,
                )
            )
        elif event.kind == DaemonEventKind.RECORDING_STATE:
            state.append(
                _MqttPublication(
                    f"{prefix}/state/recording",
                    _json_payload(event.payload),
                    retained,
                )
            )
        elif event.kind == DaemonEventKind.DESTINATION_HEALTH:
            snapshot = event.payload.get("snapshot")
            if isinstance(snapshot, Mapping):
                state.append(
                    self._destination_publication(
                        snapshot,
                        retain=retained,
                    )
                )

        if event.kind != DaemonEventKind.SNAPSHOT:
            state.append(
                _MqttPublication(
                    f"{prefix}/events",
                    event.to_json_line().rstrip(b"\n"),
                    False,
                    event=True,
                )
            )
        return tuple(state)

    def _snapshot_publications(
        self,
        snapshot: Mapping[str, object],
        *,
        retain: bool,
    ) -> tuple[_MqttPublication, ...]:
        output = list(
            self._runtime_snapshot_publications(
                snapshot,
                retain=retain,
            )
        )
        recording = snapshot.get("recording")
        if isinstance(recording, Mapping):
            output.append(
                _MqttPublication(
                    f"{self.config.topic_prefix}/state/recording",
                    _json_payload(recording),
                    retain,
                )
            )
        return tuple(output)

    def _runtime_snapshot_publications(
        self,
        snapshot: Mapping[str, object],
        *,
        retain: bool,
    ) -> tuple[_MqttPublication, ...]:
        prefix = self.config.topic_prefix
        output: list[_MqttPublication] = []

        daemon = {
            key: snapshot[key]
            for key in (
                "state",
                "started_at",
                "stopped_at",
                "state_changed_at",
                "transition_sequence",
                "last_failure_at",
                "last_error",
            )
            if key in snapshot
        }
        if daemon:
            output.append(
                _MqttPublication(
                    f"{prefix}/state/daemon",
                    _json_payload(daemon),
                    retain,
                )
            )

        scanner_info = {
            key: snapshot[key]
            for key in (
                "scanner_endpoint",
                "scanner_model",
                "scanner_firmware",
                "psi_interval_ms",
                "psi_active",
            )
            if key in snapshot
        }
        if scanner_info:
            output.append(
                _MqttPublication(
                    f"{prefix}/state/scanner/info",
                    _json_payload(scanner_info),
                    retain,
                )
            )

        scanner_connection = {
            key: snapshot[key]
            for key in (
                "scanner_endpoint",
                "scanner_connected",
            )
            if key in snapshot
        }
        if scanner_connection:
            output.append(
                _MqttPublication(
                    f"{prefix}/state/scanner/connection",
                    _json_payload(scanner_connection),
                    retain,
                )
            )

        radio = snapshot.get("radio_state")
        if isinstance(radio, Mapping):
            output.append(
                _MqttPublication(
                    f"{prefix}/state/radio",
                    _json_payload(radio),
                    retain,
                )
            )

        audio = snapshot.get("audio")
        if isinstance(audio, Mapping):
            output.append(
                _MqttPublication(
                    f"{prefix}/state/audio",
                    _json_payload(audio),
                    retain,
                )
            )

        router = snapshot.get("router")
        if isinstance(router, Mapping):
            output.extend(
                self._destination_publications(
                    router,
                    retain=retain,
                )
            )

        return tuple(output)

    def _destination_publications(
        self,
        router: Mapping[str, object],
        *,
        retain: bool,
    ) -> tuple[_MqttPublication, ...]:
        converted = _json_compatible(router.get("subscribers"))
        if not isinstance(converted, list):
            return ()

        output: list[_MqttPublication] = []
        for subscriber in converted:
            if isinstance(subscriber, Mapping):
                output.append(
                    self._destination_publication(
                        subscriber,
                        retain=retain,
                    )
                )
        return tuple(output)

    def _destination_publication(
        self,
        snapshot: Mapping[str, object],
        *,
        retain: bool,
    ) -> _MqttPublication:
        subscriber_id = snapshot.get("subscriber_id")
        if not isinstance(subscriber_id, str) or not subscriber_id:
            raise TypeError(
                "Daemon MQTT destination state requires a subscriber ID."
            )
        topic_segment = quote(subscriber_id, safe="")
        return _MqttPublication(
            f"{self.config.topic_prefix}/state/destinations/{topic_segment}",
            _json_payload(snapshot),
            retain,
        )

    def _publish_availability(
        self,
        connection: DaemonMqttBrokerConnection,
        *,
        online: bool,
        suppress_errors: bool = False,
    ) -> None:
        try:
            connection.publish(
                f"{self.config.topic_prefix}/availability",
                b"online" if online else b"offline",
                qos=self.config.qos,
                retain=True,
            )
        except Exception:
            if suppress_errors:
                return
            raise

        observed_at = _require_aware_datetime(self._now())
        with self._condition:
            self._publications += 1
            self._retained_publications += 1
            self._last_published_at = observed_at

    def _mark_connection_healthy(self) -> None:
        with self._condition:
            self._retry_attempt = 0
            self._next_retry_delay = None
            self._last_error = None

    def _resolve_password(self) -> str | None:
        variable = self.config.password_environment_variable
        if variable is None:
            return None
        source = os.environ if self._environ is None else self._environ
        value = source.get(variable)
        if not value:
            raise RuntimeError(
                f"MQTT password environment variable {variable!r} is not set."
            )
        return value

    def _record_failure(
        self,
        error: BaseException,
        secret_values: tuple[str, ...],
    ) -> None:
        safe_error = _redact_error(error, secret_values)
        observed_at = _require_aware_datetime(self._now())
        with self._condition:
            self._failures += 1
            self._last_failure_at = observed_at
            self._last_error = safe_error

        logger.warning(
            "daemon MQTT worker failure broker=%s:%d error=%s",
            self.config.host,
            self.config.port,
            safe_error,
        )

    def _backoff_after_failure(self) -> bool:
        with self._condition:
            if self._stopping:
                return False

            retry_attempt = self._retry_attempt + 1
            self._retry_attempt = retry_attempt
            policy = self.config.reconnect_policy

            if not policy.allows(retry_attempt):
                self._next_retry_delay = None
                return False

            delay = policy.delay_for(retry_attempt)
            self._next_retry_delay = delay
            self._set_state_locked("backoff")
            self._condition.notify_all()

        logger.warning(
            "daemon MQTT retry scheduled broker=%s:%d attempt=%d delay=%.3f",
            self.config.host,
            self.config.port,
            retry_attempt,
            delay,
        )

        with self._condition:
            self._condition.wait_for(
                lambda: self._stopping,
                timeout=delay,
            )
            if self._stopping:
                return False
            self._next_retry_delay = None
            self._set_state_locked("connecting")
            return True

    def _set_state_locked(
        self,
        state: DaemonMqttWorkerState,
        *,
        observed_at: datetime | None = None,
    ) -> None:
        if state == self._state:
            return
        self._state = state
        self._state_changed_at = _require_aware_datetime(
            self._now() if observed_at is None else observed_at
        )

    def _snapshot_locked(self) -> DaemonMqttWorkerSnapshot:
        thread = self._thread
        return DaemonMqttWorkerSnapshot(
            host=self.config.host,
            port=self.config.port,
            topic_prefix=self.config.topic_prefix,
            state=self._state,
            running=(
                thread is not None
                and thread.is_alive()
                and not self._stopped
            ),
            connected=self._connected,
            connection_attempts=self._connection_attempts,
            successful_connections=self._successful_connections,
            publications=self._publications,
            retained_publications=self._retained_publications,
            event_publications=self._event_publications,
            psi_events_skipped=self._psi_events_skipped,
            resynchronizations=self._resynchronizations,
            failures=self._failures,
            retry_attempt=self._retry_attempt,
            next_retry_delay=self._next_retry_delay,
            state_changed_at=self._state_changed_at,
            last_connected_at=self._last_connected_at,
            last_published_at=self._last_published_at,
            last_failure_at=self._last_failure_at,
            last_error=self._last_error,
        )
