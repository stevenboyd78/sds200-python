from __future__ import annotations

import json

import pytest

from sds200 import (
    DAEMON_API_PROTOCOL,
    DAEMON_API_VERSION,
    DAEMON_MQTT_HOME_ASSISTANT_SUPPORT_URL,
    DaemonMqttConfiguration,
    DaemonMqttHomeAssistantConfiguration,
    build_home_assistant_device_discovery,
)
from sds200.daemon_mqtt_home_assistant import (
    build_home_assistant_control_request,
    home_assistant_control_topics,
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
    controls_enabled: bool = False,
) -> DaemonMqttConfiguration:
    return DaemonMqttConfiguration(
        host="mqtt.example.test",
        topic_prefix=topic_prefix,
        qos=qos,  # type: ignore[arg-type]
        home_assistant=DaemonMqttHomeAssistantConfiguration(
            enabled=True,
            discovery_prefix=discovery_prefix,
            controls_enabled=controls_enabled,
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


def test_discovery_adds_deliberate_home_assistant_control_entities() -> None:
    discovery = build_home_assistant_device_discovery(
        enabled_config(controls_enabled=True),
        SNAPSHOT,
    )

    assert discovery is not None
    payload = json.loads(discovery.payload)

    assert "availability_topic" not in payload
    assert payload["availability"] == [
        {"topic": "radio/sds200/availability"}
    ]
    assert payload["availability_mode"] == "all"

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
        "system_hold",
        "department_hold",
        "site_hold",
        "channel_hold",
        "previous_channel",
        "next_channel",
        "scanner_reconnect",
    }

    assert components["system_hold"] == {
        "availability": [
            {"topic": "radio/sds200/availability"},
            {
                "topic": "radio/sds200/state/scanner/connection",
                "value_template": (
                    "{{ 'online' if value_json.scanner_connected "
                    "else 'offline' }}"
                ),
            },
            {
                "topic": "radio/sds200/state/radio",
                "value_template": (
                    "{{ 'online' if value_json.system_hold "
                    "in ['On', 'Off'] else 'offline' }}"
                ),
            },
        ],
        "availability_mode": "all",
        "command_topic": (
            "radio/sds200/home_assistant/control/hold/system"
        ),
        "name": "System Hold",
        "optimistic": False,
        "payload_off": "OFF",
        "payload_on": "ON",
        "platform": "switch",
        "qos": 0,
        "retain": False,
        "state_off": "Off",
        "state_on": "On",
        "state_topic": "radio/sds200/state/radio",
        "unique_id": (
            "sds200_mqtt_a699eb0a0c0e654f5a52_system_hold"
        ),
        "value_template": "{{ value_json.system_hold }}",
    }
    assert components["scanner_reconnect"] == {
        "command_topic": (
            "radio/sds200/home_assistant/control/reconnect"
        ),
        "name": "Reconnect Scanner",
        "payload_press": "PRESS",
        "platform": "button",
        "qos": 0,
        "retain": False,
        "unique_id": (
            "sds200_mqtt_a699eb0a0c0e654f5a52_scanner_reconnect"
        ),
    }

    navigation_availability = [
        {"topic": "radio/sds200/availability"},
        {
            "topic": "radio/sds200/state/scanner/connection",
            "value_template": (
                "{{ 'online' if value_json.scanner_connected "
                "else 'offline' }}"
            ),
        },
        {
            "topic": "radio/sds200/state/radio",
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
        assert components[f"{direction}_channel"] == {
            "availability": navigation_availability,
            "availability_mode": "all",
            "command_topic": (
                "radio/sds200/home_assistant/control/"
                f"{direction}/channel"
            ),
            "name": label,
            "payload_press": "PRESS",
            "platform": "button",
            "qos": 0,
            "retain": False,
            "unique_id": (
                "sds200_mqtt_a699eb0a0c0e654f5a52_"
                f"{direction}_channel"
            ),
        }

    for scope in (
        "system",
        "department",
        "site",
        "channel",
    ):
        availability = components[f"{scope}_hold"]["availability"]

        assert availability[-1] == {
            "topic": "radio/sds200/state/radio",
            "value_template": (
                f"{{{{ 'online' if value_json.{scope}_hold "
                "in ['On', 'Off'] else 'offline' }}"
            ),
        }

    command_topics = {
        component["command_topic"]
        for component in components.values()
        if "command_topic" in component
    }
    assert command_topics == {
        "radio/sds200/home_assistant/control/hold/system",
        "radio/sds200/home_assistant/control/hold/department",
        "radio/sds200/home_assistant/control/hold/site",
        "radio/sds200/home_assistant/control/hold/channel",
        "radio/sds200/home_assistant/control/previous/channel",
        "radio/sds200/home_assistant/control/next/channel",
        "radio/sds200/home_assistant/control/reconnect",
    }
    assert "radio/sds200/commands" not in command_topics


def test_home_assistant_control_translation_uses_fresh_daemon_envelopes() -> None:
    config = enabled_config(controls_enabled=True)

    assert home_assistant_control_topics(config) == (
        "radio/sds200/home_assistant/control/hold/system",
        "radio/sds200/home_assistant/control/hold/department",
        "radio/sds200/home_assistant/control/hold/site",
        "radio/sds200/home_assistant/control/hold/channel",
        "radio/sds200/home_assistant/control/previous/channel",
        "radio/sds200/home_assistant/control/next/channel",
        "radio/sds200/home_assistant/control/reconnect",
    )

    assert build_home_assistant_control_request(
        config,
        "radio/sds200/home_assistant/control/hold/channel",
        b"ON",
        request_id="home-assistant-control-1",
    ) == {
        "operation": "scanner.hold_state",
        "params": {
            "held": True,
            "scope": "channel",
        },
        "protocol": DAEMON_API_PROTOCOL,
        "request_id": "home-assistant-control-1",
        "version": DAEMON_API_VERSION,
    }

    assert build_home_assistant_control_request(
        config,
        "radio/sds200/home_assistant/control/hold/channel",
        b"OFF",
        request_id="home-assistant-control-2",
    ) == {
        "operation": "scanner.hold_state",
        "params": {
            "held": False,
            "scope": "channel",
        },
        "protocol": DAEMON_API_PROTOCOL,
        "request_id": "home-assistant-control-2",
        "version": DAEMON_API_VERSION,
    }

    assert build_home_assistant_control_request(
        config,
        "radio/sds200/home_assistant/control/reconnect",
        b"PRESS",
        request_id="home-assistant-control-3",
    ) == {
        "operation": "scanner.reconnect",
        "params": {},
        "protocol": DAEMON_API_PROTOCOL,
        "request_id": "home-assistant-control-3",
        "version": DAEMON_API_VERSION,
    }


def test_home_assistant_navigation_translation_uses_current_channel() -> None:
    config = enabled_config(controls_enabled=True)

    assert build_home_assistant_control_request(
        config,
        "radio/sds200/home_assistant/control/previous/channel",
        b"PRESS",
        request_id="home-assistant-control-previous",
        radio_state={
            "channel_kind": "TGID",
            "channel_index": 400,
        },
    ) == {
        "operation": "scanner.previous",
        "params": {
            "first": 400,
            "target": "TGID",
        },
        "protocol": DAEMON_API_PROTOCOL,
        "request_id": "home-assistant-control-previous",
        "version": DAEMON_API_VERSION,
    }

    assert build_home_assistant_control_request(
        config,
        "radio/sds200/home_assistant/control/next/channel",
        b"PRESS",
        request_id="home-assistant-control-next",
        radio_state={
            "channel_kind": "ConvFrequency",
            "channel_index": 500,
        },
    ) == {
        "operation": "scanner.next",
        "params": {
            "first": 500,
            "target": "CFREQ",
        },
        "protocol": DAEMON_API_PROTOCOL,
        "request_id": "home-assistant-control-next",
        "version": DAEMON_API_VERSION,
    }


@pytest.mark.parametrize(
    "radio_state",
    [
        None,
        {
            "channel_kind": "SrchFrequency",
            "channel_index": 600,
        },
        {
            "channel_kind": "TGID",
            "channel_index": None,
        },
        {
            "channel_kind": "TGID",
            "channel_index": (1 << 32) - 1,
        },
    ],
)
def test_home_assistant_navigation_translation_rejects_unavailable_context(
    radio_state: dict[str, object] | None,
) -> None:
    config = enabled_config(controls_enabled=True)

    assert (
        build_home_assistant_control_request(
            config,
            "radio/sds200/home_assistant/control/next/channel",
            b"PRESS",
            request_id="navigation-unavailable",
            radio_state=radio_state,
        )
        is None
    )


def test_home_assistant_control_translation_is_exact_and_opt_in() -> None:
    disabled = enabled_config()
    enabled = enabled_config(controls_enabled=True)

    assert home_assistant_control_topics(disabled) == ()
    assert (
        build_home_assistant_control_request(
            disabled,
            "radio/sds200/home_assistant/control/reconnect",
            b"PRESS",
            request_id="disabled",
        )
        is None
    )
    assert (
        build_home_assistant_control_request(
            enabled,
            "radio/sds200/home_assistant/control/hold/system",
            b"on",
            request_id="wrong-case",
        )
        is None
    )
    assert (
        build_home_assistant_control_request(
            enabled,
            "radio/sds200/home_assistant/control/reconnect",
            b"press",
            request_id="wrong-button",
        )
        is None
    )
    assert (
        build_home_assistant_control_request(
            enabled,
            "radio/sds200/home_assistant/control/unknown",
            b"PRESS",
            request_id="unknown-topic",
        )
        is None
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
