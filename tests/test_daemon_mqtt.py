from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from sds200 import (
    DAEMON_MQTT_CONFIG_FILENAME,
    DAEMON_MQTT_CONFIG_VERSION,
    ConfigurationError,
    DaemonMqttConfiguration,
    ReconnectPolicy,
    default_daemon_mqtt_config_path,
    load_daemon_mqtt_configuration,
    resolve_configuration_paths,
)


def test_default_mqtt_path_and_missing_configuration_are_read_only(
    tmp_path: Path,
) -> None:
    paths = resolve_configuration_paths(
        environ={},
        home=tmp_path / "home",
        system_config_dir=tmp_path / "etc" / "sdsctl",
    )
    expected = paths.user_config_dir / DAEMON_MQTT_CONFIG_FILENAME

    assert default_daemon_mqtt_config_path(paths) == expected
    assert load_daemon_mqtt_configuration(paths=paths) is None
    assert expected.exists() is False


def test_mqtt_configuration_loads_minimal_document_with_defaults(
    tmp_path: Path,
) -> None:
    path = tmp_path / DAEMON_MQTT_CONFIG_FILENAME
    path.write_text(
        'version = 1\n'
        '\n'
        '[broker]\n'
        'host = "mqtt.example.test"\n',
        encoding="utf-8",
    )

    configuration = load_daemon_mqtt_configuration(path)

    assert configuration == DaemonMqttConfiguration(
        host="mqtt.example.test"
    )
    assert configuration is not None
    assert configuration.port == 1883
    assert configuration.client_id is None
    assert configuration.username is None
    assert configuration.password_environment_variable is None
    assert configuration.topic_prefix == "sdsctl"
    assert configuration.qos == 1
    assert configuration.retain is True
    assert configuration.commands_enabled is False
    assert configuration.keepalive_seconds == 60
    assert configuration.reconnect_policy == ReconnectPolicy()

    serialized = configuration.as_dict()
    assert serialized["version"] == DAEMON_MQTT_CONFIG_VERSION
    broker = serialized["broker"]
    assert isinstance(broker, dict)
    assert broker["host"] == "mqtt.example.test"
    json.dumps(serialized)


def test_mqtt_configuration_loads_complete_document(
    tmp_path: Path,
) -> None:
    path = tmp_path / DAEMON_MQTT_CONFIG_FILENAME
    path.write_text(
        'version = 1\n'
        '\n'
        '[broker]\n'
        'host = "mqtt.example.test"\n'
        'port = 1884\n'
        'client_id = "sdsctl-lab"\n'
        'username = "scanner"\n'
        'password_environment_variable = "SDSCTL_MQTT_PASSWORD"\n'
        'topic_prefix = "radio/sds200"\n'
        'qos = 2\n'
        'retain = false\n'
        'commands_enabled = true\n'
        'keepalive_seconds = 45\n'
        'reconnect_initial_delay = 0.5\n'
        'reconnect_multiplier = 1.5\n'
        'reconnect_max_delay = 12.0\n'
        'reconnect_max_attempts = 7\n',
        encoding="utf-8",
    )

    configuration = load_daemon_mqtt_configuration(path)

    assert configuration == DaemonMqttConfiguration(
        host="mqtt.example.test",
        port=1884,
        client_id="sdsctl-lab",
        username="scanner",
        password_environment_variable="SDSCTL_MQTT_PASSWORD",
        topic_prefix="radio/sds200",
        qos=2,
        retain=False,
        commands_enabled=True,
        keepalive_seconds=45,
        reconnect_policy=ReconnectPolicy(
            initial_delay=0.5,
            multiplier=1.5,
            max_delay=12.0,
            max_attempts=7,
        ),
    )


def test_mqtt_configuration_is_immutable() -> None:
    configuration = DaemonMqttConfiguration(host="mqtt.example.test")

    with pytest.raises(FrozenInstanceError):
        configuration.port = 1884  # type: ignore[misc]


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            lambda: DaemonMqttConfiguration(host=""),
            "host must not be empty",
        ),
        (
            lambda: DaemonMqttConfiguration(
                host="mqtt://mqtt.example.test",
            ),
            "not a URL",
        ),
        (
            lambda: DaemonMqttConfiguration(
                host="mqtt.example.test",
                port=0,
            ),
            "port must be at least 1",
        ),
        (
            lambda: DaemonMqttConfiguration(
                host="mqtt.example.test",
                port=65536,
            ),
            "port must be at most 65535",
        ),
        (
            lambda: DaemonMqttConfiguration(
                host="mqtt.example.test",
                qos=3,  # type: ignore[arg-type]
            ),
            "QoS must be at most 2",
        ),
        (
            lambda: DaemonMqttConfiguration(
                host="mqtt.example.test",
                retain=1,  # type: ignore[arg-type]
            ),
            "retain setting must be a boolean",
        ),
        (
            lambda: DaemonMqttConfiguration(
                host="mqtt.example.test",
                commands_enabled=1,  # type: ignore[arg-type]
            ),
            "commands_enabled setting must be a boolean",
        ),
        (
            lambda: DaemonMqttConfiguration(
                host="mqtt.example.test",
                topic_prefix="/sdsctl",
            ),
            "must not start or end",
        ),
        (
            lambda: DaemonMqttConfiguration(
                host="mqtt.example.test",
                topic_prefix="sdsctl/+",
            ),
            "must not contain subscription wildcards",
        ),
        (
            lambda: DaemonMqttConfiguration(
                host="mqtt.example.test",
                password_environment_variable="SDSCTL_MQTT_PASSWORD",
            ),
            "requires a username",
        ),
        (
            lambda: DaemonMqttConfiguration(
                host="mqtt.example.test",
                username="scanner",
                password_environment_variable=" padded ",
            ),
            "environment-variable name must not be empty or padded",
        ),
        (
            lambda: DaemonMqttConfiguration(
                host="mqtt.example.test",
                keepalive_seconds=0,
            ),
            "keepalive must be at least 1",
        ),
    ],
)
def test_mqtt_configuration_rejects_unsafe_values(
    factory: object,
    message: str,
) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        factory()  # type: ignore[operator]


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ("", "version must be 1"),
        ("version = 2\n", "version must be 1"),
        ("version = true\n", "version must be 1"),
        ("version = [\n", "Could not read"),
        (
            "version = 1\nfuture = true\n",
            "unsupported top-level field",
        ),
        (
            'version = 1\nbroker = "invalid"\n',
            r"\[broker\] table",
        ),
        (
            "version = 1\n"
            "[broker]\n"
            'host = "mqtt.example.test"\n'
            "future = true\n",
            "unsupported field",
        ),
        (
            "version = 1\n"
            "[broker]\n"
            "host = 123\n",
            "host must be a string",
        ),
        (
            "version = 1\n"
            "[broker]\n"
            'host = "mqtt.example.test"\n'
            'qos = "1"\n',
            "QoS must be an integer",
        ),
        (
            "version = 1\n"
            "[broker]\n"
            'host = "mqtt.example.test"\n'
            'commands_enabled = "yes"\n',
            "commands_enabled must be a boolean",
        ),
        (
            "version = 1\n"
            "[broker]\n"
            'host = "mqtt.example.test"\n'
            "reconnect_max_attempts = true\n",
            "reconnect_max_attempts must be an integer",
        ),
        (
            "version = 1\n"
            "[broker]\n"
            'host = "mqtt.example.test"\n'
            "reconnect_initial_delay = 10.0\n"
            "reconnect_max_delay = 5.0\n",
            "Reconnect maximum delay",
        ),
    ],
)
def test_mqtt_configuration_rejects_invalid_documents(
    tmp_path: Path,
    document: str,
    message: str,
) -> None:
    path = tmp_path / DAEMON_MQTT_CONFIG_FILENAME
    path.write_text(document, encoding="utf-8")

    with pytest.raises(ConfigurationError, match=message):
        load_daemon_mqtt_configuration(path)


def test_mqtt_configuration_diagnostics_do_not_echo_unknown_values(
    tmp_path: Path,
) -> None:
    secret = "resolved-production-password"
    path = tmp_path / DAEMON_MQTT_CONFIG_FILENAME
    path.write_text(
        "version = 1\n"
        "[broker]\n"
        'host = "mqtt.example.test"\n'
        f'password = "{secret}"\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError) as exc_info:
        load_daemon_mqtt_configuration(path)

    message = str(exc_info.value)
    assert "password" in message
    assert str(path) in message
    assert secret not in message


def test_mqtt_configuration_path_and_paths_are_mutually_exclusive(
    tmp_path: Path,
) -> None:
    paths = resolve_configuration_paths(
        environ={},
        home=tmp_path / "home",
        system_config_dir=tmp_path / "etc" / "sdsctl",
    )

    with pytest.raises(ValueError, match="path or configuration paths"):
        load_daemon_mqtt_configuration(
            tmp_path / DAEMON_MQTT_CONFIG_FILENAME,
            paths=paths,
        )
