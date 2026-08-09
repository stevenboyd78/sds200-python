from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

from .daemon_mqtt import DaemonMqttConfiguration

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

    payload: dict[str, object] = {
        "availability_topic": f"{prefix}/availability",
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
    return DaemonMqttHomeAssistantDiscovery(
        topic=(
            f"{home_assistant.discovery_prefix}/device/"
            f"{object_id}/config"
        ),
        payload=_json_payload(payload),
    )
