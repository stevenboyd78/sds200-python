from __future__ import annotations

import json

import pytest

from sds200 import (
    DAEMON_MQTT_HOME_ASSISTANT_SUPPORT_URL,
    DaemonMqttConfiguration,
    DaemonMqttHomeAssistantConfiguration,
    build_home_assistant_device_discovery,
)

SNAPSHOT: dict[str, object] = {
    "state": "running",
    "scanner_endpoint": "192.0.2.25:50536",
    "scanner_model": "SDS200",
    "scanner_firmware": "1.26.01",
    "scanner_connected": True,
    "radio_state": {
        "system": "County",
        "department": "Dispatch",
        "channel": "Primary",
        "signal": 4,
        "rssi": -83.0,
    },
    "audio": {
        "running": True,
    },
    "recording": {
        "status": "idle",
        "active": False,
    },
}


def enabled_config(
    *,
    topic_prefix: str = "radio/sds200",
    discovery_prefix: str = "homeassistant",
    qos: int = 1,
) -> DaemonMqttConfiguration:
    return DaemonMqttConfiguration(
        host="mqtt.example.test",
        topic_prefix=topic_prefix,
        qos=qos,  # type: ignore[arg-type]
        home_assistant=DaemonMqttHomeAssistantConfiguration(
            enabled=True,
            discovery_prefix=discovery_prefix,
        ),
    )


def test_discovery_is_disabled_by_default() -> None:
    assert (
        build_home_assistant_device_discovery(
            DaemonMqttConfiguration(host="mqtt.example.test"),
            SNAPSHOT,
        )
        is None
    )


def test_device_discovery_uses_stable_topic_identity_and_read_only_entities() -> None:
    discovery = build_home_assistant_device_discovery(
        enabled_config(),
        SNAPSHOT,
    )

    assert discovery is not None
    assert discovery.topic == (
        "homeassistant/device/sds200_a699eb0a0c0e654f5a52/config"
    )
    assert discovery.retain is False

    payload = json.loads(discovery.payload)
    assert payload["device"] == {
        "identifiers": ["sds200-mqtt-a699eb0a0c0e654f5a52"],
        "manufacturer": "Uniden",
        "model": "SDS200",
        "name": "Uniden SDS200",
        "sw_version": "1.26.01",
    }
    assert payload["origin"] == {
        "name": "sds200",
        "support_url": DAEMON_MQTT_HOME_ASSISTANT_SUPPORT_URL,
    }
    assert payload["availability_topic"] == "radio/sds200/availability"
    assert payload["payload_available"] == "online"
    assert payload["payload_not_available"] == "offline"
    assert payload["qos"] == 1

    components = payload["components"]
    assert set(components) == {
        "daemon_state",
        "scanner_connected",
        "system",
        "department",
        "channel",
        "signal",
        "rssi",
        "audio_running",
        "recording_active",
        "recording_status",
    }
    assert all(
        component["unique_id"].startswith(
            "sds200_mqtt_a699eb0a0c0e654f5a52_"
        )
        for component in components.values()
    )
    assert all("command_topic" not in component for component in components.values())

    assert components["scanner_connected"] == {
        "device_class": "connectivity",
        "entity_category": "diagnostic",
        "name": "Scanner Connection",
        "platform": "binary_sensor",
        "state_topic": "radio/sds200/state/scanner/connection",
        "unique_id": (
            "sds200_mqtt_a699eb0a0c0e654f5a52_scanner_connected"
        ),
        "value_template": (
            "{{ 'ON' if value_json.scanner_connected else 'OFF' }}"
        ),
    }
    assert components["rssi"] == {
        "device_class": "signal_strength",
        "name": "RSSI",
        "platform": "sensor",
        "state_class": "measurement",
        "state_topic": "radio/sds200/state/radio",
        "unique_id": "sds200_mqtt_a699eb0a0c0e654f5a52_rssi",
        "unit_of_measurement": "dBm",
        "value_template": "{{ value_json.rssi }}",
    }


def test_discovery_honors_configured_prefix_and_qos() -> None:
    discovery = build_home_assistant_device_discovery(
        enabled_config(
            topic_prefix="scanner/main",
            discovery_prefix="ha",
            qos=2,
        ),
        SNAPSHOT,
    )

    assert discovery is not None
    payload = json.loads(discovery.payload)
    assert discovery.topic.startswith("ha/device/sds200_")
    assert payload["availability_topic"] == "scanner/main/availability"
    assert payload["qos"] == 2
    assert payload["components"]["channel"]["state_topic"] == (
        "scanner/main/state/radio"
    )


def test_discovery_tolerates_missing_scanner_identity() -> None:
    snapshot = dict(SNAPSHOT)
    snapshot["scanner_model"] = None
    snapshot["scanner_firmware"] = None

    discovery = build_home_assistant_device_discovery(
        enabled_config(),
        snapshot,
    )

    assert discovery is not None
    device = json.loads(discovery.payload)["device"]
    assert device == {
        "identifiers": ["sds200-mqtt-a699eb0a0c0e654f5a52"],
        "manufacturer": "Uniden",
        "name": "Uniden SDS Scanner",
    }


def test_discovery_rejects_invalid_inputs() -> None:
    config = enabled_config()

    with pytest.raises(TypeError, match="DaemonMqttConfiguration"):
        build_home_assistant_device_discovery(  # type: ignore[arg-type]
            object(),
            SNAPSHOT,
        )

    with pytest.raises(TypeError, match="snapshot must be a mapping"):
        build_home_assistant_device_discovery(  # type: ignore[arg-type]
            config,
            object(),
        )
