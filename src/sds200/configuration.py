from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal, TypeAlias

from .exceptions import ConfigurationError
from .logging_config import LOG_LEVEL_NAMES
from .reliability import ReconnectPolicy

APPLICATION_CONFIG_FILENAME = "config.toml"
CONFIG_DIRECTORY_NAME = "sdsctl"
CONNECTION_PROFILE_FILENAME = "profiles.toml"
DEFAULT_SYSTEM_CONFIG_DIR = Path("/etc/sdsctl")
LEGACY_CONFIG_DIRECTORY_NAME = "sds200"
REMOTE_AUDIO_PROFILE_FILENAME = "remote-audio-profiles.toml"

ConfigurationSource: TypeAlias = Literal[
    "default",
    "system",
    "user",
    "environment",
    "command-line",
]
ColorMode: TypeAlias = Literal["auto", "always", "never"]
ThemeName: TypeAlias = Literal["dark", "light"]

CONFIGURATION_SOURCE_PRECEDENCE: tuple[ConfigurationSource, ...] = (
    "default",
    "system",
    "user",
    "environment",
    "command-line",
)
APPLICATION_CONFIGURATION_FIELDS: tuple[str, ...] = (
    "max_xml_retries",
    "reconnect_attempts",
    "reconnect_initial_delay",
    "reconnect_multiplier",
    "reconnect_max_delay",
    "health_history_limit",
    "color",
    "theme",
    "log_level",
    "log_file",
)
_COLOR_MODES: tuple[ColorMode, ...] = ("auto", "always", "never")
_THEME_NAMES: tuple[ThemeName, ...] = ("dark", "light")


@dataclass(frozen=True, slots=True)
class ConfigurationPaths:
    """Deterministic application, state, cache, and legacy configuration paths."""

    system_config_dir: Path
    user_config_dir: Path
    user_state_dir: Path
    user_cache_dir: Path
    legacy_user_config_dir: Path

    @property
    def system_config_file(self) -> Path:
        return self.system_config_dir / APPLICATION_CONFIG_FILENAME

    @property
    def user_config_file(self) -> Path:
        return self.user_config_dir / APPLICATION_CONFIG_FILENAME

    @property
    def legacy_connection_profiles_file(self) -> Path:
        return self.legacy_user_config_dir / CONNECTION_PROFILE_FILENAME

    @property
    def legacy_remote_audio_profiles_file(self) -> Path:
        return self.legacy_user_config_dir / REMOTE_AUDIO_PROFILE_FILENAME


@dataclass(frozen=True, slots=True)
class LegacyConfigurationDiscovery:
    """Read-only discovery result for known legacy configuration locations."""

    root: Path
    root_exists: bool
    connection_profiles: Path
    connection_profiles_exists: bool
    remote_audio_profiles: Path
    remote_audio_profiles_exists: bool

    @property
    def found(self) -> bool:
        return (
            self.root_exists
            or self.connection_profiles_exists
            or self.remote_audio_profiles_exists
        )


@dataclass(frozen=True, slots=True)
class ApplicationConfiguration:
    """Validated renderer-neutral operational configuration values."""

    max_xml_retries: int = 2
    reconnect_attempts: int = 0
    reconnect_initial_delay: float = 1.0
    reconnect_multiplier: float = 2.0
    reconnect_max_delay: float = 30.0
    health_history_limit: int = 100
    color: ColorMode = "auto"
    theme: ThemeName = "dark"
    log_level: str | None = None
    log_file: Path | None = None

    def __post_init__(self) -> None:
        _require_integer(
            self.max_xml_retries,
            label="Maximum XML retries",
            minimum=0,
        )
        _require_integer(
            self.reconnect_attempts,
            label="Reconnect attempts",
            minimum=0,
        )
        _require_integer(
            self.health_history_limit,
            label="Health history limit",
            minimum=1,
        )

        initial_delay = _require_number(
            self.reconnect_initial_delay,
            label="Reconnect initial delay",
            minimum=0.0,
            minimum_inclusive=False,
        )
        multiplier = _require_number(
            self.reconnect_multiplier,
            label="Reconnect multiplier",
            minimum=1.0,
        )
        max_delay = _require_number(
            self.reconnect_max_delay,
            label="Reconnect maximum delay",
            minimum=0.0,
            minimum_inclusive=False,
        )
        if max_delay < initial_delay:
            raise ValueError(
                "Reconnect maximum delay must be at least the initial delay."
            )

        object.__setattr__(self, "reconnect_initial_delay", initial_delay)
        object.__setattr__(self, "reconnect_multiplier", multiplier)
        object.__setattr__(self, "reconnect_max_delay", max_delay)

        if not isinstance(self.color, str):
            raise TypeError("Color mode must be a string.")
        normalized_color = self.color.strip().lower()
        if normalized_color not in _COLOR_MODES:
            choices = ", ".join(_COLOR_MODES)
            raise ValueError(f"Color mode must be one of: {choices}.")
        object.__setattr__(self, "color", normalized_color)

        if not isinstance(self.theme, str):
            raise TypeError("Theme name must be a string.")
        normalized_theme = self.theme.strip().lower()
        if normalized_theme not in _THEME_NAMES:
            choices = ", ".join(_THEME_NAMES)
            raise ValueError(f"Theme name must be one of: {choices}.")
        object.__setattr__(self, "theme", normalized_theme)

        if self.log_level is not None:
            if not isinstance(self.log_level, str):
                raise TypeError("Log level must be a string or None.")
            normalized_level = self.log_level.strip().upper()
            if normalized_level not in LOG_LEVEL_NAMES:
                choices = ", ".join(LOG_LEVEL_NAMES)
                raise ValueError(f"Log level must be one of: {choices}.")
            object.__setattr__(self, "log_level", normalized_level)

        if self.log_file is not None:
            if not isinstance(self.log_file, (str, Path)):
                raise TypeError("Log file must be a path or None.")
            object.__setattr__(self, "log_file", Path(self.log_file))

    @property
    def reconnect_policy(self) -> ReconnectPolicy:
        return ReconnectPolicy(
            initial_delay=self.reconnect_initial_delay,
            multiplier=self.reconnect_multiplier,
            max_delay=self.reconnect_max_delay,
            max_attempts=self.reconnect_attempts or None,
        )


@dataclass(frozen=True, slots=True)
class ConfigurationOrigin:
    """Provenance for one resolved configuration value."""

    source: ConfigurationSource
    location: str | None = None

    def __post_init__(self) -> None:
        if self.source not in CONFIGURATION_SOURCE_PRECEDENCE:
            raise ValueError(f"Unsupported configuration source: {self.source!r}")


@dataclass(frozen=True, slots=True)
class ConfigurationLayer:
    """One typed configuration overlay from a single precedence source."""

    source: ConfigurationSource
    values: Mapping[str, object]
    location: str | None = None

    def __post_init__(self) -> None:
        if self.source == "default":
            raise ValueError(
                "Built-in defaults are supplied by ApplicationConfiguration."
            )
        if self.source not in CONFIGURATION_SOURCE_PRECEDENCE:
            raise ValueError(f"Unsupported configuration source: {self.source!r}")
        object.__setattr__(
            self,
            "values",
            MappingProxyType(dict(self.values)),
        )


@dataclass(frozen=True, slots=True)
class ResolvedApplicationConfiguration:
    """Validated values plus immutable per-field provenance."""

    configuration: ApplicationConfiguration
    origins: Mapping[str, ConfigurationOrigin]

    def __post_init__(self) -> None:
        copied = dict(self.origins)
        expected = set(APPLICATION_CONFIGURATION_FIELDS)
        actual = set(copied)
        if actual != expected:
            missing = sorted(expected - actual)
            unexpected = sorted(actual - expected)
            raise ValueError(
                "Configuration provenance fields do not match the application "
                f"configuration; missing={missing!r}, unexpected={unexpected!r}."
            )
        object.__setattr__(self, "origins", MappingProxyType(copied))

    def origin_for(self, field: str) -> ConfigurationOrigin:
        try:
            return self.origins[field]
        except KeyError as exc:
            raise KeyError(f"Unknown application configuration field: {field}") from exc

    def source_for(self, field: str) -> ConfigurationSource:
        return self.origin_for(field).source


def resolve_configuration_paths(
    *,
    environ: Mapping[str, str] | None = None,
    home: str | Path | None = None,
    system_config_dir: str | Path = DEFAULT_SYSTEM_CONFIG_DIR,
) -> ConfigurationPaths:
    """Resolve paths without reading, creating, or modifying filesystem entries."""

    source = os.environ if environ is None else environ
    home_path = _require_absolute(
        Path.home() if home is None else Path(home),
        label="Home directory",
    )
    resolved_system_dir = _require_absolute(
        Path(system_config_dir),
        label="System configuration directory",
    )

    config_home = _xdg_home(
        source,
        variable="XDG_CONFIG_HOME",
        fallback=home_path / ".config",
    )
    state_home = _xdg_home(
        source,
        variable="XDG_STATE_HOME",
        fallback=home_path / ".local" / "state",
    )
    cache_home = _xdg_home(
        source,
        variable="XDG_CACHE_HOME",
        fallback=home_path / ".cache",
    )

    return ConfigurationPaths(
        system_config_dir=resolved_system_dir,
        user_config_dir=config_home / CONFIG_DIRECTORY_NAME,
        user_state_dir=state_home / CONFIG_DIRECTORY_NAME,
        user_cache_dir=cache_home / CONFIG_DIRECTORY_NAME,
        legacy_user_config_dir=config_home / LEGACY_CONFIG_DIRECTORY_NAME,
    )


def discover_legacy_configuration(
    paths: ConfigurationPaths | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    home: str | Path | None = None,
) -> LegacyConfigurationDiscovery:
    """Detect known legacy paths without creating, moving, or rewriting them."""

    resolved = paths or resolve_configuration_paths(
        environ=environ,
        home=home,
    )
    connection_profiles = resolved.legacy_connection_profiles_file
    remote_audio_profiles = resolved.legacy_remote_audio_profiles_file

    return LegacyConfigurationDiscovery(
        root=resolved.legacy_user_config_dir,
        root_exists=resolved.legacy_user_config_dir.exists(),
        connection_profiles=connection_profiles,
        connection_profiles_exists=connection_profiles.exists(),
        remote_audio_profiles=remote_audio_profiles,
        remote_audio_profiles_exists=remote_audio_profiles.exists(),
    )


def resolve_application_configuration(
    layers: Sequence[ConfigurationLayer] = (),
    *,
    defaults: ApplicationConfiguration | None = None,
) -> ResolvedApplicationConfiguration:
    """Resolve ordered layers using fixed documented precedence."""

    configuration = defaults or ApplicationConfiguration()
    origins: dict[str, ConfigurationOrigin] = {
        field: ConfigurationOrigin("default")
        for field in APPLICATION_CONFIGURATION_FIELDS
    }
    previous_index = 0
    seen_sources: set[ConfigurationSource] = set()

    for layer in layers:
        source_index = CONFIGURATION_SOURCE_PRECEDENCE.index(layer.source)
        if layer.source in seen_sources:
            raise ConfigurationError(
                f"Configuration source {layer.source!r} was supplied more than once."
            )
        if source_index <= previous_index:
            raise ConfigurationError(
                "Configuration layers must follow precedence order: "
                + ", ".join(CONFIGURATION_SOURCE_PRECEDENCE)
                + "."
            )
        seen_sources.add(layer.source)
        previous_index = source_index

        unknown = sorted(
            field
            for field in layer.values
            if field not in APPLICATION_CONFIGURATION_FIELDS
        )
        if unknown:
            location = f" at {layer.location}" if layer.location else ""
            names = ", ".join(repr(field) for field in unknown)
            raise ConfigurationError(
                f"{layer.source} configuration{location} contains unsupported "
                f"field(s): {names}."
            )

        values = {
            field: getattr(configuration, field)
            for field in APPLICATION_CONFIGURATION_FIELDS
        }
        values.update(layer.values)

        try:
            configuration = ApplicationConfiguration(**values)
        except (TypeError, ValueError) as exc:
            location = f" at {layer.location}" if layer.location else ""
            raise ConfigurationError(
                f"Invalid {layer.source} configuration{location}: {exc}"
            ) from exc

        origin = ConfigurationOrigin(layer.source, layer.location)
        for field in layer.values:
            origins[field] = origin

    return ResolvedApplicationConfiguration(configuration, origins)


def _xdg_home(
    environ: Mapping[str, str],
    *,
    variable: str,
    fallback: Path,
) -> Path:
    value = environ.get(variable)
    if not value:
        return fallback
    return _require_absolute(Path(value), label=variable)


def _require_absolute(path: Path, *, label: str) -> Path:
    if not path.is_absolute():
        raise ValueError(f"{label} must be an absolute path: {path}")
    return path


def _require_integer(value: object, *, label: str, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer.")
    if value < minimum:
        raise ValueError(f"{label} must be at least {minimum}.")


def _require_number(
    value: object,
    *,
    label: str,
    minimum: float,
    minimum_inclusive: bool = True,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be a number.")
    normalized = float(value)
    invalid = (
        normalized < minimum
        if minimum_inclusive
        else normalized <= minimum
    )
    if invalid:
        comparison = "at least" if minimum_inclusive else "greater than"
        raise ValueError(f"{label} must be {comparison} {minimum:g}.")
    return normalized
