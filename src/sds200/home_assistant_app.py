from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile
from typing import TypeAlias
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .daemon_mqtt import (
    DAEMON_MQTT_CONFIG_VERSION,
    DaemonMqttConfiguration,
    DaemonMqttHomeAssistantConfiguration,
    load_daemon_mqtt_configuration,
)
from .exceptions import ConfigurationError, SDS200Error

HOME_ASSISTANT_APP_OPTIONS_PATH = Path("/data/options.json")
HOME_ASSISTANT_SUPERVISOR_URL = "http://supervisor"
HOME_ASSISTANT_SUPERVISOR_TOKEN_VARIABLE = "SUPERVISOR_TOKEN"
HOME_ASSISTANT_APP_MQTT_PASSWORD_VARIABLE = "SDSCTL_HOME_ASSISTANT_MQTT_PASSWORD"
HOME_ASSISTANT_APP_DEFAULT_MQTT_TOPIC_PREFIX = "sdsctl"
HOME_ASSISTANT_APP_DEFAULT_RECORDING_DIRECTORY = "sdsctl/recordings"
HOME_ASSISTANT_APP_SUPERVISOR_TIMEOUT = 5.0

SupervisorJsonRequester: TypeAlias = Callable[[str, str, float], object]


def _require_text(value: object, *, label: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string.")
    if not value or value.strip() != value:
        raise ValueError(f"{label} must not be empty or padded.")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{label} must not contain control characters.")
    return value


def _require_topic_prefix(value: object) -> str:
    topic = _require_text(value, label="Home Assistant App MQTT topic prefix")
    if topic.startswith("/") or topic.endswith("/"):
        raise ValueError(
            "Home Assistant App MQTT topic prefix must not start or end with '/'."
        )
    if "//" in topic:
        raise ValueError(
            "Home Assistant App MQTT topic prefix must not contain empty topic levels."
        )
    if "#" in topic or "+" in topic:
        raise ValueError(
            "Home Assistant App MQTT topic prefix must not contain subscription wildcards."
        )
    return topic


def _require_recording_directory(value: object) -> str:
    directory = _require_text(
        value,
        label="Home Assistant App recording directory",
    )
    if "\\" in directory:
        raise ValueError(
            "Home Assistant App recording directory must use '/' separators."
        )

    path = PurePosixPath(directory)
    if path.is_absolute():
        raise ValueError(
            "Home Assistant App recording directory must be relative to /media."
        )

    parts = directory.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(
            "Home Assistant App recording directory must not contain "
            "empty, '.' or '..' path components."
        )

    return path.as_posix()


@dataclass(frozen=True, slots=True)
class HomeAssistantAppOptions:
    """Strict user-editable options consumed from /data/options.json."""

    scanner_host: str
    mqtt_topic_prefix: str = HOME_ASSISTANT_APP_DEFAULT_MQTT_TOPIC_PREFIX
    recording_directory: str = HOME_ASSISTANT_APP_DEFAULT_RECORDING_DIRECTORY

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "scanner_host",
            _require_text(
                self.scanner_host,
                label="Home Assistant App scanner host",
            ),
        )
        object.__setattr__(
            self,
            "mqtt_topic_prefix",
            _require_topic_prefix(self.mqtt_topic_prefix),
        )
        object.__setattr__(
            self,
            "recording_directory",
            _require_recording_directory(self.recording_directory),
        )


@dataclass(frozen=True, slots=True)
class HomeAssistantMqttService:
    """Validated Supervisor-provided MQTT service connection details."""

    host: str
    port: int
    ssl: bool
    username: str
    password: str = field(repr=False)
    protocol: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "host",
            _require_text(self.host, label="Supervisor MQTT host"),
        )
        if type(self.port) is not int:
            raise TypeError("Supervisor MQTT port must be an integer.")
        if not 1 <= self.port <= 65535:
            raise ValueError(
                "Supervisor MQTT port must be between 1 and 65535."
            )
        if type(self.ssl) is not bool:
            raise TypeError("Supervisor MQTT SSL setting must be boolean.")
        object.__setattr__(
            self,
            "username",
            _require_text(self.username, label="Supervisor MQTT username"),
        )
        object.__setattr__(
            self,
            "password",
            _require_text(self.password, label="Supervisor MQTT password"),
        )
        object.__setattr__(
            self,
            "protocol",
            _require_text(self.protocol, label="Supervisor MQTT protocol"),
        )


def load_home_assistant_app_options(
    path: str | Path = HOME_ASSISTANT_APP_OPTIONS_PATH,
) -> HomeAssistantAppOptions:
    """Load the strict Home Assistant App user options document."""

    options_path = Path(path)
    try:
        raw = options_path.read_text(encoding="utf-8")
    except OSError as error:
        raise ConfigurationError(
            f"Could not read Home Assistant App options {options_path}: {error}"
        ) from error

    try:
        payload: object = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ConfigurationError(
            f"Home Assistant App options {options_path} must contain valid JSON."
        ) from error

    if not isinstance(payload, Mapping):
        raise ConfigurationError(
            f"Home Assistant App options {options_path} must contain one JSON object."
        )

    allowed = {
        "scanner_host",
        "mqtt_topic_prefix",
        "recording_directory",
    }
    unexpected = sorted(str(key) for key in payload if key not in allowed)
    if unexpected:
        fields = ", ".join(repr(field) for field in unexpected)
        raise ConfigurationError(
            f"Home Assistant App options {options_path} have unsupported field(s): {fields}."
        )

    if "scanner_host" not in payload:
        raise ConfigurationError(
            f"Home Assistant App options {options_path} require 'scanner_host'."
        )

    try:
        return HomeAssistantAppOptions(
            scanner_host=payload["scanner_host"],
            mqtt_topic_prefix=payload.get(
                "mqtt_topic_prefix",
                HOME_ASSISTANT_APP_DEFAULT_MQTT_TOPIC_PREFIX,
            ),
            recording_directory=payload.get(
                "recording_directory",
                HOME_ASSISTANT_APP_DEFAULT_RECORDING_DIRECTORY,
            ),
        )
    except (TypeError, ValueError) as error:
        raise ConfigurationError(
            f"Invalid Home Assistant App options {options_path}: {error}"
        ) from error


def _supervisor_token(
    environ: Mapping[str, str] | None = None,
) -> str:
    source = os.environ if environ is None else environ
    token = source.get(HOME_ASSISTANT_SUPERVISOR_TOKEN_VARIABLE)
    if token is None or not token or token.strip() != token:
        raise SDS200Error(
            f"{HOME_ASSISTANT_SUPERVISOR_TOKEN_VARIABLE} is missing or invalid."
        )
    return token


def _request_supervisor_json(
    url: str,
    token: str,
    timeout: float,
) -> object:
    request = Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise SDS200Error(
            "Could not query the Home Assistant Supervisor MQTT service "
            f"({error.__class__.__name__})."
        ) from error

    try:
        return json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SDS200Error(
            "Home Assistant Supervisor returned invalid MQTT service JSON."
        ) from error


def _supervisor_mqtt_port(value: object) -> int:
    if isinstance(value, bool):
        raise TypeError("Supervisor MQTT port must be an integer or decimal string.")
    if isinstance(value, int):
        port = value
    elif isinstance(value, str) and value.isdecimal():
        port = int(value)
    else:
        raise TypeError("Supervisor MQTT port must be an integer or decimal string.")
    if not 1 <= port <= 65535:
        raise ValueError("Supervisor MQTT port must be between 1 and 65535.")
    return port


def parse_home_assistant_mqtt_service_response(
    payload: object,
) -> HomeAssistantMqttService:
    """Validate one raw Supervisor API response envelope for /services/mqtt."""

    if not isinstance(payload, Mapping):
        raise SDS200Error(
            "Home Assistant Supervisor MQTT response must be a JSON object."
        )
    if payload.get("result") != "ok":
        raise SDS200Error(
            "Home Assistant Supervisor MQTT service request did not succeed."
        )

    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise SDS200Error(
            "Home Assistant Supervisor MQTT response must contain a data object."
        )

    required = {"host", "port", "ssl", "username", "password", "protocol"}
    missing = sorted(field for field in required if field not in data)
    if missing:
        fields = ", ".join(repr(field) for field in missing)
        raise SDS200Error(
            "Home Assistant Supervisor MQTT response omitted required "
            f"field(s): {fields}."
        )

    try:
        return HomeAssistantMqttService(
            host=data["host"],
            port=_supervisor_mqtt_port(data["port"]),
            ssl=data["ssl"],
            username=data["username"],
            password=data["password"],
            protocol=data["protocol"],
        )
    except (TypeError, ValueError) as error:
        raise SDS200Error(
            f"Invalid Home Assistant Supervisor MQTT service response: {error}"
        ) from error


def fetch_home_assistant_mqtt_service(
    *,
    environ: Mapping[str, str] | None = None,
    supervisor_url: str = HOME_ASSISTANT_SUPERVISOR_URL,
    timeout: float = HOME_ASSISTANT_APP_SUPERVISOR_TIMEOUT,
    requester: SupervisorJsonRequester | None = None,
) -> HomeAssistantMqttService:
    """Fetch and validate the MQTT service selected by Home Assistant Supervisor."""

    normalized_url = _require_text(
        supervisor_url,
        label="Home Assistant Supervisor URL",
    ).rstrip("/")
    if not normalized_url:
        raise ValueError("Home Assistant Supervisor URL must not be empty.")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise TypeError("Home Assistant Supervisor timeout must be a number.")
    normalized_timeout = float(timeout)
    if normalized_timeout <= 0:
        raise ValueError(
            "Home Assistant Supervisor timeout must be greater than zero."
        )

    token = _supervisor_token(environ)
    selected_requester = requester or _request_supervisor_json
    payload = selected_requester(
        f"{normalized_url}/services/mqtt",
        token,
        normalized_timeout,
    )
    return parse_home_assistant_mqtt_service_response(payload)


def build_home_assistant_daemon_mqtt_configuration(
    options: HomeAssistantAppOptions,
    service: HomeAssistantMqttService,
) -> DaemonMqttConfiguration:
    """Map Home Assistant App settings to the existing generic daemon MQTT model."""

    if not isinstance(options, HomeAssistantAppOptions):
        raise TypeError(
            "Home Assistant daemon MQTT configuration requires App options."
        )
    if not isinstance(service, HomeAssistantMqttService):
        raise TypeError(
            "Home Assistant daemon MQTT configuration requires an MQTT service."
        )
    if service.ssl:
        raise ConfigurationError(
            "The Home Assistant MQTT service requires TLS, but daemon MQTT TLS "
            "is not supported yet."
        )

    return DaemonMqttConfiguration(
        host=service.host,
        port=service.port,
        username=service.username,
        password_environment_variable=(
            HOME_ASSISTANT_APP_MQTT_PASSWORD_VARIABLE
        ),
        topic_prefix=options.mqtt_topic_prefix,
        qos=1,
        retain=True,
        commands_enabled=False,
        home_assistant=DaemonMqttHomeAssistantConfiguration(
            enabled=True,
            controls_enabled=True,
        ),
    )


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def render_home_assistant_daemon_mqtt_configuration(
    options: HomeAssistantAppOptions,
    service: HomeAssistantMqttService,
) -> str:
    """Render a password-free daemon MQTT manifest for the App runtime."""

    config = build_home_assistant_daemon_mqtt_configuration(options, service)
    assert config.username is not None
    assert config.password_environment_variable is not None

    return (
        f"version = {DAEMON_MQTT_CONFIG_VERSION}\n"
        "\n"
        "[broker]\n"
        f"host = {_toml_string(config.host)}\n"
        f"port = {config.port}\n"
        f"username = {_toml_string(config.username)}\n"
        "password_environment_variable = "
        f"{_toml_string(config.password_environment_variable)}\n"
        f"topic_prefix = {_toml_string(config.topic_prefix)}\n"
        f"qos = {config.qos}\n"
        f"retain = {str(config.retain).lower()}\n"
        f"commands_enabled = {str(config.commands_enabled).lower()}\n"
        "\n"
        "[home_assistant]\n"
        "enabled = true\n"
        "controls_enabled = true\n"
    )


def write_home_assistant_daemon_mqtt_configuration(
    path: str | Path,
    options: HomeAssistantAppOptions,
    service: HomeAssistantMqttService,
) -> Path:
    """Atomically write and validate the generated password-free MQTT manifest."""

    target = Path(path)
    if not target.is_absolute():
        raise ValueError(
            "Home Assistant daemon MQTT configuration path must be absolute."
        )
    if not target.name:
        raise ValueError(
            "Home Assistant daemon MQTT configuration path must identify a file."
        )

    rendered = render_home_assistant_daemon_mqtt_configuration(
        options,
        service,
    )
    target.parent.mkdir(parents=True, exist_ok=True)

    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())

        assert temporary is not None
        parsed = load_daemon_mqtt_configuration(temporary)
        if parsed is None:
            raise AssertionError(
                "Generated Home Assistant daemon MQTT configuration disappeared."
            )
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            with suppress(FileNotFoundError):
                temporary.unlink()

    return target


def home_assistant_mqtt_password_environment(
    service: HomeAssistantMqttService,
) -> dict[str, str]:
    """Return only the secret environment entry required by the daemon child."""

    if not isinstance(service, HomeAssistantMqttService):
        raise TypeError(
            "Home Assistant MQTT password environment requires an MQTT service."
        )
    return {
        HOME_ASSISTANT_APP_MQTT_PASSWORD_VARIABLE: service.password,
    }


__all__ = [
    "HOME_ASSISTANT_APP_DEFAULT_MQTT_TOPIC_PREFIX",
    "HOME_ASSISTANT_APP_DEFAULT_RECORDING_DIRECTORY",
    "HOME_ASSISTANT_APP_MQTT_PASSWORD_VARIABLE",
    "HOME_ASSISTANT_APP_OPTIONS_PATH",
    "HOME_ASSISTANT_APP_SUPERVISOR_TIMEOUT",
    "HOME_ASSISTANT_SUPERVISOR_TOKEN_VARIABLE",
    "HOME_ASSISTANT_SUPERVISOR_URL",
    "HomeAssistantAppOptions",
    "HomeAssistantMqttService",
    "build_home_assistant_daemon_mqtt_configuration",
    "fetch_home_assistant_mqtt_service",
    "home_assistant_mqtt_password_environment",
    "load_home_assistant_app_options",
    "parse_home_assistant_mqtt_service_response",
    "render_home_assistant_daemon_mqtt_configuration",
    "write_home_assistant_daemon_mqtt_configuration",
]
