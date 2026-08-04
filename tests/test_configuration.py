from __future__ import annotations

from pathlib import Path

import pytest

from sds200 import (
    APPLICATION_CONFIG_FILENAME,
    CONFIG_DIRECTORY_NAME,
    CONNECTION_PROFILE_FILENAME,
    DEFAULT_SYSTEM_CONFIG_DIR,
    LEGACY_CONFIG_DIRECTORY_NAME,
    REMOTE_AUDIO_PROFILE_FILENAME,
    ConfigurationPaths,
    LegacyConfigurationDiscovery,
    discover_legacy_configuration,
    resolve_configuration_paths,
)
from sds200.profiles import default_profile_path
from sds200.remote_audio_profiles import default_remote_audio_profile_path


def test_configuration_paths_use_documented_defaults(tmp_path: Path) -> None:
    home = tmp_path / "home"

    paths = resolve_configuration_paths(environ={}, home=home)

    assert paths == ConfigurationPaths(
        system_config_dir=DEFAULT_SYSTEM_CONFIG_DIR,
        user_config_dir=home / ".config" / CONFIG_DIRECTORY_NAME,
        user_state_dir=home / ".local" / "state" / CONFIG_DIRECTORY_NAME,
        user_cache_dir=home / ".cache" / CONFIG_DIRECTORY_NAME,
        legacy_user_config_dir=home
        / ".config"
        / LEGACY_CONFIG_DIRECTORY_NAME,
    )
    assert paths.system_config_file == (
        DEFAULT_SYSTEM_CONFIG_DIR / APPLICATION_CONFIG_FILENAME
    )
    assert paths.user_config_file == (
        home / ".config" / CONFIG_DIRECTORY_NAME / APPLICATION_CONFIG_FILENAME
    )
    assert paths.legacy_connection_profiles_file.name == (
        CONNECTION_PROFILE_FILENAME
    )
    assert paths.legacy_remote_audio_profiles_file.name == (
        REMOTE_AUDIO_PROFILE_FILENAME
    )


def test_configuration_paths_honor_xdg_overrides(tmp_path: Path) -> None:
    config_home = tmp_path / "xdg-config"
    state_home = tmp_path / "xdg-state"
    cache_home = tmp_path / "xdg-cache"
    system_dir = tmp_path / "etc" / CONFIG_DIRECTORY_NAME

    paths = resolve_configuration_paths(
        environ={
            "XDG_CONFIG_HOME": str(config_home),
            "XDG_STATE_HOME": str(state_home),
            "XDG_CACHE_HOME": str(cache_home),
        },
        home=tmp_path / "unused-home",
        system_config_dir=system_dir,
    )

    assert paths.system_config_dir == system_dir
    assert paths.user_config_dir == config_home / CONFIG_DIRECTORY_NAME
    assert paths.user_state_dir == state_home / CONFIG_DIRECTORY_NAME
    assert paths.user_cache_dir == cache_home / CONFIG_DIRECTORY_NAME
    assert paths.legacy_user_config_dir == (
        config_home / LEGACY_CONFIG_DIRECTORY_NAME
    )


def test_empty_xdg_values_use_home_defaults(tmp_path: Path) -> None:
    home = tmp_path / "home"

    paths = resolve_configuration_paths(
        environ={
            "XDG_CONFIG_HOME": "",
            "XDG_STATE_HOME": "",
            "XDG_CACHE_HOME": "",
        },
        home=home,
    )

    assert paths.user_config_dir == home / ".config" / CONFIG_DIRECTORY_NAME
    assert paths.user_state_dir == (
        home / ".local" / "state" / CONFIG_DIRECTORY_NAME
    )
    assert paths.user_cache_dir == home / ".cache" / CONFIG_DIRECTORY_NAME


@pytest.mark.parametrize(
    "variable",
    ["XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"],
)
def test_configuration_paths_reject_relative_xdg_values(
    variable: str,
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match=variable):
        resolve_configuration_paths(
            environ={variable: "relative/path"},
            home=tmp_path,
        )


def test_configuration_paths_reject_relative_home() -> None:
    with pytest.raises(ValueError, match="Home directory"):
        resolve_configuration_paths(environ={}, home="relative-home")


def test_legacy_discovery_does_not_create_paths(tmp_path: Path) -> None:
    paths = resolve_configuration_paths(environ={}, home=tmp_path)

    discovery = discover_legacy_configuration(paths)

    assert discovery == LegacyConfigurationDiscovery(
        root=paths.legacy_user_config_dir,
        root_exists=False,
        connection_profiles=paths.legacy_connection_profiles_file,
        connection_profiles_exists=False,
        remote_audio_profiles=paths.legacy_remote_audio_profiles_file,
        remote_audio_profiles_exists=False,
    )
    assert discovery.found is False
    assert paths.legacy_user_config_dir.exists() is False


def test_legacy_discovery_reports_known_files(tmp_path: Path) -> None:
    paths = resolve_configuration_paths(environ={}, home=tmp_path)
    paths.legacy_user_config_dir.mkdir(parents=True)
    paths.legacy_connection_profiles_file.write_text(
        "version = 4\n",
        encoding="utf-8",
    )
    paths.legacy_remote_audio_profiles_file.write_text(
        "version = 1\n",
        encoding="utf-8",
    )

    discovery = discover_legacy_configuration(paths)

    assert discovery.found is True
    assert discovery.root_exists is True
    assert discovery.connection_profiles_exists is True
    assert discovery.remote_audio_profiles_exists is True


def test_existing_profile_defaults_remain_in_legacy_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    assert default_profile_path() == (
        tmp_path / LEGACY_CONFIG_DIRECTORY_NAME / CONNECTION_PROFILE_FILENAME
    )
    assert default_remote_audio_profile_path() == (
        tmp_path
        / LEGACY_CONFIG_DIRECTORY_NAME
        / REMOTE_AUDIO_PROFILE_FILENAME
    )
