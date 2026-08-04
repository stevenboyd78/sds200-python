from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

APPLICATION_CONFIG_FILENAME = "config.toml"
CONFIG_DIRECTORY_NAME = "sdsctl"
CONNECTION_PROFILE_FILENAME = "profiles.toml"
DEFAULT_SYSTEM_CONFIG_DIR = Path("/etc/sdsctl")
LEGACY_CONFIG_DIRECTORY_NAME = "sds200"
REMOTE_AUDIO_PROFILE_FILENAME = "remote-audio-profiles.toml"


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
