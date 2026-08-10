from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

from .daemon_api import DaemonApiOperation, DaemonApiRequest
from .daemon_mqtt import DaemonMqttConfiguration
from .state import RadioStateSnapshot
from .tui_controls import channel_navigation

DAEMON_MQTT_HOME_ASSISTANT_SUPPORT_URL = (
    "https://github.com/stevenboyd78/sds200-python"
)


@dataclass(frozen=True, slots=True)
class DaemonMqttHomeAssistantDiscovery:
    """One deterministic Home Assistant MQTT device discovery publication."""

    topic: str
    payload: bytes
    retain: bool = False


def _json_payload(value: Mapping[str, object]) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _device_identity(topic_prefix: str) -> tuple[str, str]:
    digest = hashlib.sha256(topic_prefix.encode("utf-8")).hexdigest()[:20]
    object_id = f"sds200_{digest}"
    identifier = f"sds200-mqtt-{digest}"
    return object_id, identifier


def _optional_text(
    snapshot: Mapping[str, object],
    key: str,
) -> str | None:
    value = snapshot.get(key)
    return value if isinstance(value, str) and value else None


def home_assistant_control_topics(
    config: DaemonMqttConfiguration,
) -> tuple[str, ...]:
    """Return the exact dedicated Home Assistant scanner-control topics."""

    if not isinstance(config, DaemonMqttConfiguration):
        raise TypeError(
            "Home Assistant controls require a DaemonMqttConfiguration."
        )
    if not config.home_assistant.controls_enabled:
        return ()

    prefix = f"{config.topic_prefix}/home_assistant/control"
    return (
        f"{prefix}/hold/system",
        f"{prefix}/hold/department",
        f"{prefix}/hold/site",
        f"{prefix}/hold/channel",
        f"{prefix}/previous/channel",
        f"{prefix}/next/channel",
        f"{prefix}/reconnect",
    )


def _home_assistant_channel_navigation(
    radio_state: Mapping[str, object] | None,
) -> tuple[str, int] | None:
    """Resolve the current daemon-owned channel for one HA navigation press."""

    if radio_state is None:
        return None

    channel_index = radio_state.get("channel_index")
    channel_kind = radio_state.get("channel_kind")
    if type(channel_index) is not int or not isinstance(channel_kind, str):
        return None

    return channel_navigation(
        RadioStateSnapshot(
            channel_kind=channel_kind,
            channel_index=channel_index,
        )
    )


def build_home_assistant_control_request(
    config: DaemonMqttConfiguration,
    topic: str,
    payload: bytes,
    *,
    request_id: str,
    radio_state: Mapping[str, object] | None = None,
) -> dict[str, object] | None:
    """Translate one exact HA command into a fresh semantic daemon request."""

    if not isinstance(config, DaemonMqttConfiguration):
        raise TypeError(
            "Home Assistant controls require a DaemonMqttConfiguration."
        )
    if not isinstance(topic, str):
        raise TypeError("Home Assistant control topic must be a string.")
    if not isinstance(payload, bytes):
        raise TypeError("Home Assistant control payload must be bytes.")

    topics = home_assistant_control_topics(config)
    if not topics or topic not in topics:
        return None

    control_prefix = f"{config.topic_prefix}/home_assistant/control"
    hold_topics = {
        f"{control_prefix}/hold/system": "system",
        f"{control_prefix}/hold/department": "department",
        f"{control_prefix}/hold/site": "site",
        f"{control_prefix}/hold/channel": "channel",
    }

    scope = hold_topics.get(topic)
    if scope is not None:
        if payload == b"ON":
            held = True
        elif payload == b"OFF":
            held = False
        else:
            return None

        operation = DaemonApiOperation.SCANNER_HOLD_STATE
        params: dict[str, object] = {
            "scope": scope,
            "held": held,
        }
    elif topic == f"{control_prefix}/reconnect":
        if payload != b"PRESS":
            return None
        operation = DaemonApiOperation.SCANNER_RECONNECT
        params = {}
    else:
        navigation_topics = {
            f"{control_prefix}/previous/channel": (
                DaemonApiOperation.SCANNER_PREVIOUS
            ),
            f"{control_prefix}/next/channel": DaemonApiOperation.SCANNER_NEXT,
        }
        navigation_operation = navigation_topics.get(topic)
        if navigation_operation is None or payload != b"PRESS":
            return None
        operation = navigation_operation

        selection = _home_assistant_channel_navigation(radio_state)
        if selection is None:
            return None

        target, first = selection
        params = {
            "target": target,
            "first": first,
        }

    return DaemonApiRequest(
        request_id=request_id,
        operation=operation.value,
        params=params,
    ).as_dict()


def build_home_assistant_device_discovery(
    config: DaemonMqttConfiguration,
    snapshot: Mapping[str, object],
) -> DaemonMqttHomeAssistantDiscovery | None:
    """Build one read-only multi-component Home Assistant device document."""

    if not isinstance(config, DaemonMqttConfiguration):
        raise TypeError(
            "Home Assistant discovery requires a DaemonMqttConfiguration."
        )
    if not isinstance(snapshot, Mapping):
        raise TypeError(
            "Home Assistant discovery snapshot must be a mapping."
        )

    home_assistant = config.home_assistant
    if not home_assistant.enabled:
        return None

    object_id, identifier = _device_identity(config.topic_prefix)
    unique_prefix = identifier.replace("-", "_")
    model = _optional_text(snapshot, "scanner_model")
    firmware = _optional_text(snapshot, "scanner_firmware")

    device: dict[str, object] = {
        "identifiers": [identifier],
        "manufacturer": "Uniden",
        "name": (
            f"Uniden {model}"
            if model is not None
            else "Uniden SDS Scanner"
        ),
    }
    if model is not None:
        device["model"] = model
    if firmware is not None:
        device["sw_version"] = firmware

    prefix = config.topic_prefix
    radio_topic = f"{prefix}/state/radio"

    components: dict[str, object] = {
        "daemon_state": {
            "platform": "sensor",
            "name": "Daemon State",
            "unique_id": f"{unique_prefix}_daemon_state",
            "state_topic": f"{prefix}/state/daemon",
            "value_template": "{{ value_json.state }}",
            "entity_category": "diagnostic",
        },
        "scanner_connected": {
            "platform": "binary_sensor",
            "name": "Scanner Connection",
            "unique_id": f"{unique_prefix}_scanner_connected",
            "state_topic": f"{prefix}/state/scanner/connection",
            "value_template": (
                "{{ 'ON' if value_json.scanner_connected else 'OFF' }}"
            ),
            "device_class": "connectivity",
            "entity_category": "diagnostic",
        },
        "system": {
            "platform": "sensor",
            "name": "System",
            "unique_id": f"{unique_prefix}_system",
            "state_topic": radio_topic,
            "value_template": "{{ value_json.system }}",
        },
        "department": {
            "platform": "sensor",
            "name": "Department",
            "unique_id": f"{unique_prefix}_department",
            "state_topic": radio_topic,
            "value_template": "{{ value_json.department }}",
        },
        "channel": {
            "platform": "sensor",
            "name": "Channel",
            "unique_id": f"{unique_prefix}_channel",
            "state_topic": radio_topic,
            "value_template": "{{ value_json.channel }}",
        },
        "signal": {
            "platform": "sensor",
            "name": "Signal",
            "unique_id": f"{unique_prefix}_signal",
            "state_topic": radio_topic,
            "value_template": "{{ value_json.signal }}",
        },
        "rssi": {
            "platform": "sensor",
            "name": "RSSI",
            "unique_id": f"{unique_prefix}_rssi",
            "state_topic": radio_topic,
            "value_template": "{{ value_json.rssi }}",
            "device_class": "signal_strength",
            "state_class": "measurement",
            "unit_of_measurement": "dBm",
        },
        "audio_running": {
            "platform": "binary_sensor",
            "name": "Audio",
            "unique_id": f"{unique_prefix}_audio_running",
            "state_topic": f"{prefix}/state/audio",
            "value_template": "{{ 'ON' if value_json.running else 'OFF' }}",
            "entity_category": "diagnostic",
        },
        "recording_active": {
            "platform": "binary_sensor",
            "name": "Recording",
            "unique_id": f"{unique_prefix}_recording_active",
            "state_topic": f"{prefix}/state/recording",
            "value_template": "{{ 'ON' if value_json.active else 'OFF' }}",
        },
        "recording_status": {
            "platform": "sensor",
            "name": "Recording Status",
            "unique_id": f"{unique_prefix}_recording_status",
            "state_topic": f"{prefix}/state/recording",
            "value_template": "{{ value_json.status }}",
            "entity_category": "diagnostic",
        },
    }

    if home_assistant.controls_enabled:
        control_prefix = f"{prefix}/home_assistant/control"

        for scope, label in (
            ("system", "System Hold"),
            ("department", "Department Hold"),
            ("site", "Site Hold"),
            ("channel", "Channel Hold"),
        ):
            hold_availability = [
                {
                    "topic": f"{prefix}/availability",
                },
                {
                    "topic": f"{prefix}/state/scanner/connection",
                    "value_template": (
                        "{{ 'online' if value_json.scanner_connected "
                        "else 'offline' }}"
                    ),
                },
                {
                    "topic": radio_topic,
                    "value_template": (
                        f"{{{{ 'online' if value_json.{scope}_hold "
                        "in ['On', 'Off'] else 'offline' }}"
                    ),
                },
            ]

            components[f"{scope}_hold"] = {
                "platform": "switch",
                "name": label,
                "unique_id": f"{unique_prefix}_{scope}_hold",
                "state_topic": radio_topic,
                "value_template": f"{{{{ value_json.{scope}_hold }}}}",
                "state_on": "On",
                "state_off": "Off",
                "command_topic": f"{control_prefix}/hold/{scope}",
                "payload_on": "ON",
                "payload_off": "OFF",
                "optimistic": False,
                "qos": 0,
                "retain": False,
                "availability": hold_availability,
                "availability_mode": "all",
            }

        components["scanner_reconnect"] = {
            "platform": "button",
            "name": "Reconnect Scanner",
            "unique_id": f"{unique_prefix}_scanner_reconnect",
            "command_topic": f"{control_prefix}/reconnect",
            "payload_press": "PRESS",
            "qos": 0,
            "retain": False,
        }

        navigation_availability = [
            {
                "topic": f"{prefix}/availability",
            },
            {
                "topic": f"{prefix}/state/scanner/connection",
                "value_template": (
                    "{{ 'online' if value_json.scanner_connected "
                    "else 'offline' }}"
                ),
            },
            {
                "topic": radio_topic,
                "value_template": (
                    "{{ 'online' if value_json.channel_kind in "
                    "['TGID', 'ConvFrequency'] and "
                    "value_json.channel_index is number and "
                    "0 <= value_json.channel_index < 4294967295 "
                    "else 'offline' }}"
                ),
            },
        ]

        for direction, label in (
            ("previous", "Previous Channel"),
            ("next", "Next Channel"),
        ):
            components[f"{direction}_channel"] = {
                "platform": "button",
                "name": label,
                "unique_id": f"{unique_prefix}_{direction}_channel",
                "command_topic": (
                    f"{control_prefix}/{direction}/channel"
                ),
                "payload_press": "PRESS",
                "qos": 0,
                "retain": False,
                "availability": navigation_availability,
                "availability_mode": "all",
            }

    payload: dict[str, object] = {
        "components": components,
        "device": device,
        "origin": {
            "name": "sds200",
            "support_url": DAEMON_MQTT_HOME_ASSISTANT_SUPPORT_URL,
        },
        "payload_available": "online",
        "payload_not_available": "offline",
        "qos": config.qos,
    }
    if home_assistant.controls_enabled:
        payload["availability"] = [
            {
                "topic": f"{prefix}/availability",
            }
        ]
        payload["availability_mode"] = "all"
    else:
        payload["availability_topic"] = f"{prefix}/availability"

    return DaemonMqttHomeAssistantDiscovery(
        topic=(
            f"{home_assistant.discovery_prefix}/device/"
            f"{object_id}/config"
        ),
        payload=_json_payload(payload),
    )
