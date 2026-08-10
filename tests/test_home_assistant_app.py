from __future__ import annotations

import json
from pathlib import Path

import pytest

from sds200.daemon_mqtt import load_daemon_mqtt_configuration
from sds200.exceptions import ConfigurationError, SDS200Error
from sds200.home_assistant_app import (
    HOME_ASSISTANT_APP_DEFAULT_MQTT_TOPIC_PREFIX,
    HOME_ASSISTANT_APP_DEFAULT_RECORDING_DIRECTORY,
    HOME_ASSISTANT_APP_MQTT_PASSWORD_VARIABLE,
    HOME_ASSISTANT_SUPERVISOR_TOKEN_VARIABLE,
    HomeAssistantAppOptions,
    HomeAssistantMqttService,
    build_home_assistant_daemon_mqtt_configuration,
    fetch_home_assistant_mqtt_service,
    home_assistant_mqtt_password_environment,
    load_home_assistant_app_options,
    parse_home_assistant_mqtt_service_response,
    render_home_assistant_daemon_mqtt_configuration,
    write_home_assistant_daemon_mqtt_configuration,
)


def mqtt_service_payload(
    *,
    ssl: bool = False,
    port: object = "1883",
) -> dict[str, object]:
    return {
        "result": "ok",
        "data": {
            "addon": "core_mosquitto",
            "host": "172.30.33.0",
            "port": port,
            "ssl": ssl,
            "username": "sdsctl",
            "password": "super-secret",
            "protocol": "3.1.1",
        },
    }


def test_load_home_assistant_app_options_uses_strict_defaults(
    tmp_path: Path,
) -> None:
    path = tmp_path / "options.json"
    path.write_text(
        json.dumps({"scanner_host": "192.0.2.25"}),
        encoding="utf-8",
    )

    options = load_home_assistant_app_options(path)

    assert options == HomeAssistantAppOptions(
        scanner_host="192.0.2.25",
        mqtt_topic_prefix=HOME_ASSISTANT_APP_DEFAULT_MQTT_TOPIC_PREFIX,
        recording_directory=HOME_ASSISTANT_APP_DEFAULT_RECORDING_DIRECTORY,
    )


def test_load_home_assistant_app_options_accepts_topic_prefix(
    tmp_path: Path,
) -> None:
    path = tmp_path / "options.json"
    path.write_text(
        json.dumps(
            {
                "scanner_host": "scanner.local",
                "mqtt_topic_prefix": "scanner/main",
                "recording_directory": "radio/sds200",
            }
        ),
        encoding="utf-8",
    )

    assert load_home_assistant_app_options(path) == HomeAssistantAppOptions(
        scanner_host="scanner.local",
        mqtt_topic_prefix="scanner/main",
        recording_directory="radio/sds200",
    )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({}, "require 'scanner_host'"),
        (
            {"scanner_host": "192.0.2.25", "extra": True},
            "unsupported field",
        ),
        (
            {"scanner_host": " 192.0.2.25 "},
            "must not be empty or padded",
        ),
        (
            {
                "scanner_host": "192.0.2.25",
                "mqtt_topic_prefix": "scanner/+/state",
            },
            "subscription wildcards",
        ),
        (
            {
                "scanner_host": "192.0.2.25",
                "recording_directory": "/media/sdsctl/recordings",
            },
            "must be relative to /media",
        ),
        (
            {
                "scanner_host": "192.0.2.25",
                "recording_directory": "../recordings",
            },
            "path components",
        ),
        (
            {
                "scanner_host": "192.0.2.25",
                "recording_directory": "sdsctl//recordings",
            },
            "path components",
        ),
    ],
)
def test_load_home_assistant_app_options_rejects_invalid_documents(
    tmp_path: Path,
    payload: object,
    message: str,
) -> None:
    path = tmp_path / "options.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ConfigurationError, match=message):
        load_home_assistant_app_options(path)


def test_parse_home_assistant_mqtt_service_response_validates_envelope() -> None:
    service = parse_home_assistant_mqtt_service_response(
        mqtt_service_payload()
    )

    assert service == HomeAssistantMqttService(
        host="172.30.33.0",
        port=1883,
        ssl=False,
        username="sdsctl",
        password="super-secret",
        protocol="3.1.1",
    )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"result": "error"}, "did not succeed"),
        ({"result": "ok", "data": []}, "data object"),
        (
            {
                "result": "ok",
                "data": {
                    "host": "172.30.33.0",
                    "port": "1883",
                    "ssl": False,
                    "username": "sdsctl",
                    "password": "super-secret",
                },
            },
            "omitted required",
        ),
        (mqtt_service_payload(port="not-a-port"), "decimal string"),
    ],
)
def test_parse_home_assistant_mqtt_service_response_rejects_invalid_data(
    payload: object,
    message: str,
) -> None:
    with pytest.raises(SDS200Error, match=message):
        parse_home_assistant_mqtt_service_response(payload)


def test_fetch_home_assistant_mqtt_service_uses_supervisor_token() -> None:
    calls: list[tuple[str, str, float]] = []

    def requester(url: str, token: str, timeout: float) -> object:
        calls.append((url, token, timeout))
        return mqtt_service_payload()

    service = fetch_home_assistant_mqtt_service(
        environ={HOME_ASSISTANT_SUPERVISOR_TOKEN_VARIABLE: "token-value"},
        requester=requester,
    )

    assert service.host == "172.30.33.0"
    assert calls == [
        (
            "http://supervisor/services/mqtt",
            "token-value",
            5.0,
        )
    ]


def test_fetch_home_assistant_mqtt_service_requires_supervisor_token() -> None:
    calls = 0

    def requester(url: str, token: str, timeout: float) -> object:
        nonlocal calls
        del url, token, timeout
        calls += 1
        return mqtt_service_payload()

    with pytest.raises(SDS200Error, match="SUPERVISOR_TOKEN"):
        fetch_home_assistant_mqtt_service(
            environ={},
            requester=requester,
        )

    assert calls == 0


def test_build_home_assistant_daemon_mqtt_configuration_enables_discovery() -> None:
    options = HomeAssistantAppOptions(
        scanner_host="192.0.2.25",
        mqtt_topic_prefix="scanner/main",
    )
    service = parse_home_assistant_mqtt_service_response(
        mqtt_service_payload()
    )

    config = build_home_assistant_daemon_mqtt_configuration(
        options,
        service,
    )

    assert config.host == "172.30.33.0"
    assert config.port == 1883
    assert config.username == "sdsctl"
    assert (
        config.password_environment_variable
        == HOME_ASSISTANT_APP_MQTT_PASSWORD_VARIABLE
    )
    assert config.topic_prefix == "scanner/main"
    assert config.retain is True
    assert config.commands_enabled is False
    assert config.home_assistant.enabled is True


def test_home_assistant_daemon_mqtt_configuration_rejects_tls_service() -> None:
    options = HomeAssistantAppOptions(scanner_host="192.0.2.25")
    service = parse_home_assistant_mqtt_service_response(
        mqtt_service_payload(ssl=True)
    )

    with pytest.raises(
        ConfigurationError,
        match="requires TLS.*not supported",
    ):
        build_home_assistant_daemon_mqtt_configuration(
            options,
            service,
        )


def test_rendered_home_assistant_mqtt_manifest_never_contains_password() -> None:
    options = HomeAssistantAppOptions(scanner_host="192.0.2.25")
    service = parse_home_assistant_mqtt_service_response(
        mqtt_service_payload()
    )

    rendered = render_home_assistant_daemon_mqtt_configuration(
        options,
        service,
    )

    assert "super-secret" not in rendered
    assert (
        f'password_environment_variable = '
        f'"{HOME_ASSISTANT_APP_MQTT_PASSWORD_VARIABLE}"'
    ) in rendered
    assert "commands_enabled = false" in rendered
    assert "[home_assistant]\nenabled = true" in rendered


def test_write_home_assistant_mqtt_manifest_is_loadable_and_password_free(
    tmp_path: Path,
) -> None:
    options = HomeAssistantAppOptions(
        scanner_host="192.0.2.25",
        mqtt_topic_prefix="scanner/main",
    )
    service = parse_home_assistant_mqtt_service_response(
        mqtt_service_payload()
    )
    path = tmp_path / "runtime" / "daemon-mqtt.toml"

    assert write_home_assistant_daemon_mqtt_configuration(
        path,
        options,
        service,
    ) == path

    config = load_daemon_mqtt_configuration(path)
    assert config is not None
    assert config.host == "172.30.33.0"
    assert config.port == 1883
    assert config.username == "sdsctl"
    assert config.topic_prefix == "scanner/main"
    assert config.commands_enabled is False
    assert config.home_assistant.enabled is True
    assert "super-secret" not in path.read_text(encoding="utf-8")
    assert list(path.parent.glob("*.tmp")) == []


def test_home_assistant_mqtt_password_environment_contains_only_password() -> None:
    service = parse_home_assistant_mqtt_service_response(
        mqtt_service_payload()
    )

    assert home_assistant_mqtt_password_environment(service) == {
        HOME_ASSISTANT_APP_MQTT_PASSWORD_VARIABLE: "super-secret"
    }
