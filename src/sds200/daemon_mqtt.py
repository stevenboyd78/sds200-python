from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from typing import Literal, TypeAlias, cast

from .configuration import (
    DAEMON_MQTT_CONFIG_FILENAME,
    ConfigurationPaths,
    resolve_configuration_paths,
)
from .exceptions import ConfigurationError
from .reliability import ReconnectPolicy

DAEMON_MQTT_CONFIG_VERSION = 1

DaemonMqttQos: TypeAlias = Literal[0, 1, 2]


def _require_text(value: object, *, label: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string.")
    if not value or value.strip() != value:
        raise ValueError(f"{label} must not be empty or padded.")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{label} must not contain control characters.")
    return value


def _require_optional_text(value: object, *, label: str) -> str | None:
    if value is None:
        return None
    return _require_text(value, label=label)


def _require_host(value: object) -> str:
    host = _require_text(value, label="MQTT broker host")
    if "://" in host or "/" in host:
        raise ValueError(
            "MQTT broker host must be a hostname or address, not a URL."
        )
    return host


def _require_environment_variable(value: object) -> str | None:
    variable = _require_optional_text(
        value,
        label="MQTT password environment-variable name",
    )
    if variable is None:
        return None
    if any(character.isspace() for character in variable) or "=" in variable:
        raise ValueError(
            "MQTT password environment-variable name is invalid."
        )
    return variable


def _require_topic_prefix(value: object) -> str:
    prefix = _require_text(value, label="MQTT topic prefix")
    if prefix.startswith("/") or prefix.endswith("/"):
        raise ValueError(
            "MQTT topic prefix must not start or end with '/'."
        )
    if "//" in prefix:
        raise ValueError(
            "MQTT topic prefix must not contain empty topic levels."
        )
    if "#" in prefix or "+" in prefix:
        raise ValueError(
            "MQTT topic prefix must not contain subscription wildcards."
        )
    return prefix


def _require_integer(
    value: object,
    *,
    label: str,
    minimum: int,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer.")
    if value < minimum:
        raise ValueError(f"{label} must be at least {minimum}.")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label} must be at most {maximum}.")
    return value


def _require_number(
    value: object,
    *,
    label: str,
    minimum: float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be a number.")
    normalized = float(value)
    if not isfinite(normalized):
        raise ValueError(f"{label} must be finite.")
    if normalized < minimum:
        raise ValueError(f"{label} must be at least {minimum:g}.")
    return normalized


def _require_qos(value: object) -> DaemonMqttQos:
    qos = _require_integer(
        value,
        label="MQTT QoS",
        minimum=0,
        maximum=2,
    )
    return cast(DaemonMqttQos, qos)


@dataclass(frozen=True, slots=True)
class DaemonMqttConfiguration:
    """Validated broker settings for one optional daemon-owned MQTT service."""

    host: str
    port: int = 1883
    client_id: str | None = None
    username: str | None = None
    password_environment_variable: str | None = None
    topic_prefix: str = "sdsctl"
    qos: DaemonMqttQos = 1
    retain: bool = True
    keepalive_seconds: int = 60
    reconnect_policy: ReconnectPolicy = field(default_factory=ReconnectPolicy)

    def __post_init__(self) -> None:
        object.__setattr__(self, "host", _require_host(self.host))
        object.__setattr__(
            self,
            "port",
            _require_integer(
                self.port,
                label="MQTT broker port",
                minimum=1,
                maximum=65535,
            ),
        )
        object.__setattr__(
            self,
            "client_id",
            _require_optional_text(
                self.client_id,
                label="MQTT client ID",
            ),
        )
        object.__setattr__(
            self,
            "username",
            _require_optional_text(
                self.username,
                label="MQTT username",
            ),
        )
        object.__setattr__(
            self,
            "password_environment_variable",
            _require_environment_variable(
                self.password_environment_variable
            ),
        )
        if (
            self.password_environment_variable is not None
            and self.username is None
        ):
            raise ValueError(
                "MQTT password reference requires a username."
            )
        object.__setattr__(
            self,
            "topic_prefix",
            _require_topic_prefix(self.topic_prefix),
        )
        object.__setattr__(self, "qos", _require_qos(self.qos))
        if not isinstance(self.retain, bool):
            raise TypeError("MQTT retain setting must be a boolean.")
        object.__setattr__(
            self,
            "keepalive_seconds",
            _require_integer(
                self.keepalive_seconds,
                label="MQTT keepalive",
                minimum=1,
                maximum=65535,
            ),
        )
        if not isinstance(self.reconnect_policy, ReconnectPolicy):
            raise TypeError(
                "MQTT reconnect policy must be a ReconnectPolicy."
            )
        reconnect_values = (
            self.reconnect_policy.initial_delay,
            self.reconnect_policy.multiplier,
            self.reconnect_policy.max_delay,
        )
        if not all(isfinite(value) for value in reconnect_values):
            raise ValueError(
                "MQTT reconnect policy values must be finite."
            )

    def as_dict(self) -> dict[str, object]:
        return {
            "version": DAEMON_MQTT_CONFIG_VERSION,
            "broker": {
                "host": self.host,
                "port": self.port,
                "client_id": self.client_id,
                "username": self.username,
                "password_environment_variable": (
                    self.password_environment_variable
                ),
                "topic_prefix": self.topic_prefix,
                "qos": self.qos,
                "retain": self.retain,
                "keepalive_seconds": self.keepalive_seconds,
                "reconnect_initial_delay": (
                    self.reconnect_policy.initial_delay
                ),
                "reconnect_multiplier": (
                    self.reconnect_policy.multiplier
                ),
                "reconnect_max_delay": (
                    self.reconnect_policy.max_delay
                ),
                "reconnect_max_attempts": (
                    self.reconnect_policy.max_attempts
                ),
            },
        }


def default_daemon_mqtt_config_path(
    paths: ConfigurationPaths | None = None,
) -> Path:
    """Return the deterministic user daemon MQTT configuration path."""

    resolved = paths or resolve_configuration_paths()
    return resolved.user_config_dir / DAEMON_MQTT_CONFIG_FILENAME


def load_daemon_mqtt_configuration(
    path: str | Path | None = None,
    *,
    paths: ConfigurationPaths | None = None,
) -> DaemonMqttConfiguration | None:
    """Load one strict optional versioned daemon MQTT configuration."""

    if path is not None and paths is not None:
        raise ValueError(
            "Specify a daemon MQTT path or configuration paths, not both."
        )

    config_path = (
        default_daemon_mqtt_config_path(paths)
        if path is None
        else Path(path)
    )
    if not config_path.exists():
        return None

    try:
        document = tomllib.loads(
            config_path.read_text(encoding="utf-8")
        )
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigurationError(
            f"Could not read daemon MQTT configuration {config_path}: {error}"
        ) from error

    unexpected_top_level = sorted(
        str(field)
        for field in document
        if field not in {"version", "broker"}
    )
    if unexpected_top_level:
        fields = ", ".join(
            repr(field)
            for field in unexpected_top_level
        )
        raise ConfigurationError(
            "Daemon MQTT configuration "
            f"{config_path} has unsupported top-level field(s): {fields}."
        )

    version = document.get("version")
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version != DAEMON_MQTT_CONFIG_VERSION
    ):
        raise ConfigurationError(
            "Daemon MQTT configuration "
            f"{config_path} version must be {DAEMON_MQTT_CONFIG_VERSION}."
        )

    raw_broker = document.get("broker")
    if not isinstance(raw_broker, Mapping):
        raise ConfigurationError(
            "Daemon MQTT configuration "
            f"{config_path} must contain a [broker] table."
        )

    allowed_fields = {
        "host",
        "port",
        "client_id",
        "username",
        "password_environment_variable",
        "topic_prefix",
        "qos",
        "retain",
        "keepalive_seconds",
        "reconnect_initial_delay",
        "reconnect_multiplier",
        "reconnect_max_delay",
        "reconnect_max_attempts",
    }
    unexpected_fields = sorted(
        str(field)
        for field in raw_broker
        if field not in allowed_fields
    )
    if unexpected_fields:
        fields = ", ".join(
            repr(field)
            for field in unexpected_fields
        )
        raise ConfigurationError(
            "Daemon MQTT broker configuration "
            f"{config_path} has unsupported field(s): {fields}."
        )

    try:
        reconnect_policy = ReconnectPolicy(
            initial_delay=_number_field(
                raw_broker,
                "reconnect_initial_delay",
                default=1.0,
            ),
            multiplier=_number_field(
                raw_broker,
                "reconnect_multiplier",
                default=2.0,
            ),
            max_delay=_number_field(
                raw_broker,
                "reconnect_max_delay",
                default=30.0,
            ),
            max_attempts=_optional_integer_field(
                raw_broker,
                "reconnect_max_attempts",
            ),
        )
        return DaemonMqttConfiguration(
            host=_string_field(raw_broker, "host"),
            port=_integer_field(
                raw_broker,
                "port",
                default=1883,
            ),
            client_id=_optional_string_field(
                raw_broker,
                "client_id",
            ),
            username=_optional_string_field(
                raw_broker,
                "username",
            ),
            password_environment_variable=_optional_string_field(
                raw_broker,
                "password_environment_variable",
            ),
            topic_prefix=_string_field(
                raw_broker,
                "topic_prefix",
                default="sdsctl",
            ),
            qos=_require_qos(raw_broker.get("qos", 1)),
            retain=_boolean_field(
                raw_broker,
                "retain",
                default=True,
            ),
            keepalive_seconds=_integer_field(
                raw_broker,
                "keepalive_seconds",
                default=60,
            ),
            reconnect_policy=reconnect_policy,
        )
    except (TypeError, ValueError) as error:
        raise ConfigurationError(
            f"Invalid daemon MQTT configuration {config_path}: {error}"
        ) from error


_MISSING = object()


def _string_field(
    raw: Mapping[object, object],
    field: str,
    *,
    default: object = _MISSING,
) -> str:
    value = raw.get(field, default)
    if not isinstance(value, str):
        raise TypeError(f"MQTT broker {field} must be a string.")
    return value


def _optional_string_field(
    raw: Mapping[object, object],
    field: str,
) -> str | None:
    value = raw.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(
            f"MQTT broker {field} must be a string when supplied."
        )
    return value


def _integer_field(
    raw: Mapping[object, object],
    field: str,
    *,
    default: int,
) -> int:
    value = raw.get(field, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"MQTT broker {field} must be an integer.")
    return value


def _optional_integer_field(
    raw: Mapping[object, object],
    field: str,
) -> int | None:
    value = raw.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(
            f"MQTT broker {field} must be an integer when supplied."
        )
    return value


def _number_field(
    raw: Mapping[object, object],
    field: str,
    *,
    default: float,
) -> float:
    value = raw.get(field, default)
    return _require_number(
        value,
        label=f"MQTT broker {field}",
        minimum=0.0,
    )


def _boolean_field(
    raw: Mapping[object, object],
    field: str,
    *,
    default: bool,
) -> bool:
    value = raw.get(field, default)
    if not isinstance(value, bool):
        raise TypeError(f"MQTT broker {field} must be a boolean.")
    return value
