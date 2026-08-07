from __future__ import annotations

from pathlib import Path

import pytest

from sds200 import (
    CONFIG_DIRECTORY_NAME,
    DAEMON_EVENT_SOCKET_FILENAME,
    DAEMON_PCMU_SOCKET_FILENAME,
    DAEMON_RECORDING_FILE_SOCKET_FILENAME,
    DAEMON_SOCKET_DIRECTORY_MODE,
    DAEMON_SOCKET_FILENAME,
    DAEMON_SOCKET_MODE,
    ConfigurationPaths,
    DaemonSocketLocation,
    DaemonSocketSource,
    resolve_configuration_paths,
    resolve_daemon_event_socket_location,
    resolve_daemon_pcmu_socket_location,
    resolve_daemon_recording_file_socket_location,
    resolve_daemon_socket_location,
)


def configuration_paths(tmp_path: Path) -> ConfigurationPaths:
    return ConfigurationPaths(
        system_config_dir=tmp_path / "etc" / CONFIG_DIRECTORY_NAME,
        user_config_dir=tmp_path / "config" / CONFIG_DIRECTORY_NAME,
        user_state_dir=tmp_path / "state" / CONFIG_DIRECTORY_NAME,
        user_cache_dir=tmp_path / "cache" / CONFIG_DIRECTORY_NAME,
        legacy_user_config_dir=tmp_path / "config" / "sds200",
    )


def test_socket_permission_contract_is_private() -> None:
    assert DAEMON_SOCKET_DIRECTORY_MODE == 0o700
    assert DAEMON_SOCKET_MODE == 0o600


def test_explicit_socket_path_is_absolute_and_caller_managed(
    tmp_path: Path,
) -> None:
    explicit = tmp_path / "custom" / "scanner.sock"

    location = resolve_daemon_socket_location(
        explicit,
        environ={"XDG_RUNTIME_DIR": str(tmp_path / "runtime")},
        configuration_paths=configuration_paths(tmp_path),
    )

    assert location == DaemonSocketLocation(
        explicit,
        DaemonSocketSource.EXPLICIT,
    )
    assert location.parent == explicit.parent
    assert location.managed_parent is False


@pytest.mark.parametrize("value", ["", "   ", "relative.sock"])
def test_explicit_socket_path_must_be_nonempty_and_absolute(value: str) -> None:
    with pytest.raises(ValueError, match="Daemon socket path"):
        resolve_daemon_socket_location(value)


def test_xdg_runtime_directory_is_preferred_for_default_socket(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"

    location = resolve_daemon_socket_location(
        environ={"XDG_RUNTIME_DIR": str(runtime)},
        configuration_paths=configuration_paths(tmp_path),
    )

    assert location == DaemonSocketLocation(
        runtime / CONFIG_DIRECTORY_NAME / DAEMON_SOCKET_FILENAME,
        DaemonSocketSource.XDG_RUNTIME,
    )
    assert location.managed_parent is True


def test_relative_xdg_runtime_directory_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="XDG_RUNTIME_DIR must be an absolute"):
        resolve_daemon_socket_location(
            environ={"XDG_RUNTIME_DIR": "relative/runtime"},
            configuration_paths=configuration_paths(tmp_path),
        )


@pytest.mark.parametrize("runtime_value", [None, ""])
def test_missing_xdg_runtime_directory_uses_user_state(
    runtime_value: str | None,
    tmp_path: Path,
) -> None:
    paths = configuration_paths(tmp_path)
    environment = (
        {}
        if runtime_value is None
        else {"XDG_RUNTIME_DIR": runtime_value}
    )

    location = resolve_daemon_socket_location(
        environ=environment,
        configuration_paths=paths,
    )

    assert location == DaemonSocketLocation(
        paths.user_state_dir / DAEMON_SOCKET_FILENAME,
        DaemonSocketSource.USER_STATE,
    )
    assert location.managed_parent is True


def test_user_state_fallback_can_be_resolved_from_home(tmp_path: Path) -> None:
    home = tmp_path / "home"

    location = resolve_daemon_socket_location(
        environ={},
        home=home,
    )

    assert location.path == (
        home
        / ".local"
        / "state"
        / CONFIG_DIRECTORY_NAME
        / DAEMON_SOCKET_FILENAME
    )
    assert location.source is DaemonSocketSource.USER_STATE


def test_resolution_does_not_create_parent_directories(tmp_path: Path) -> None:
    runtime = tmp_path / "missing-runtime"

    location = resolve_daemon_socket_location(
        environ={"XDG_RUNTIME_DIR": str(runtime)}
    )

    assert location.parent.exists() is False
    assert location.path.exists() is False


@pytest.mark.parametrize(
    "path",
    [
        Path("relative.sock"),
        Path("/"),
    ],
)
def test_socket_location_rejects_invalid_paths(path: Path) -> None:
    with pytest.raises(ValueError, match="Daemon socket path"):
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)


def test_socket_location_rejects_invalid_source(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="Daemon socket source"):
        DaemonSocketLocation(
            tmp_path / "daemon.sock",
            "explicit",  # type: ignore[arg-type]
        )


def test_event_socket_location_uses_explicit_absolute_path(
    tmp_path: Path,
) -> None:
    explicit = tmp_path / "custom" / "events.sock"

    location = resolve_daemon_event_socket_location(explicit)

    assert location.path == explicit
    assert location.source is DaemonSocketSource.EXPLICIT


@pytest.mark.parametrize("value", ["", "   "])
def test_event_socket_location_rejects_empty_explicit_path(
    value: str,
) -> None:
    with pytest.raises(ValueError, match="event socket path"):
        resolve_daemon_event_socket_location(value)


def test_event_socket_location_rejects_relative_explicit_path() -> None:
    with pytest.raises(ValueError, match="must be absolute"):
        resolve_daemon_event_socket_location("relative/events.sock")


def test_event_socket_location_prefers_xdg_runtime(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"

    location = resolve_daemon_event_socket_location(
        environ={"XDG_RUNTIME_DIR": str(runtime_root)},
        home=tmp_path / "home",
    )

    assert location.path == (
        runtime_root / "sdsctl" / DAEMON_EVENT_SOCKET_FILENAME
    )
    assert location.source is DaemonSocketSource.XDG_RUNTIME


def test_event_socket_location_uses_user_state_fallback(
    tmp_path: Path,
) -> None:
    paths = resolve_configuration_paths(
        environ={},
        home=tmp_path / "home",
        system_config_dir=tmp_path / "etc" / "sdsctl",
    )

    location = resolve_daemon_event_socket_location(
        environ={},
        configuration_paths=paths,
    )

    assert location.path == (
        paths.user_state_dir / DAEMON_EVENT_SOCKET_FILENAME
    )
    assert location.source is DaemonSocketSource.USER_STATE


def test_pcmu_socket_location_uses_explicit_absolute_path(
    tmp_path: Path,
) -> None:
    explicit = tmp_path / "custom" / "pcmu.sock"

    location = resolve_daemon_pcmu_socket_location(explicit)

    assert location.path == explicit
    assert location.source is DaemonSocketSource.EXPLICIT


@pytest.mark.parametrize("value", ["", "   "])
def test_pcmu_socket_location_rejects_empty_explicit_path(
    value: str,
) -> None:
    with pytest.raises(ValueError, match="PCMU socket path"):
        resolve_daemon_pcmu_socket_location(value)


def test_pcmu_socket_location_rejects_relative_explicit_path() -> None:
    with pytest.raises(ValueError, match="must be absolute"):
        resolve_daemon_pcmu_socket_location("relative/pcmu.sock")


def test_pcmu_socket_location_prefers_xdg_runtime(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"

    location = resolve_daemon_pcmu_socket_location(
        environ={"XDG_RUNTIME_DIR": str(runtime_root)},
        home=tmp_path / "home",
    )

    assert location.path == (
        runtime_root / "sdsctl" / DAEMON_PCMU_SOCKET_FILENAME
    )
    assert location.source is DaemonSocketSource.XDG_RUNTIME


def test_pcmu_socket_location_uses_user_state_fallback(
    tmp_path: Path,
) -> None:
    paths = resolve_configuration_paths(
        environ={},
        home=tmp_path / "home",
        system_config_dir=tmp_path / "etc" / "sdsctl",
    )

    location = resolve_daemon_pcmu_socket_location(
        environ={},
        configuration_paths=paths,
    )

    assert location.path == (
        paths.user_state_dir / DAEMON_PCMU_SOCKET_FILENAME
    )
    assert location.source is DaemonSocketSource.USER_STATE


def test_recording_file_socket_location_uses_explicit_absolute_path(
    tmp_path: Path,
) -> None:
    explicit = tmp_path / "custom" / "recordings.sock"

    location = resolve_daemon_recording_file_socket_location(explicit)

    assert location.path == explicit
    assert location.source is DaemonSocketSource.EXPLICIT


@pytest.mark.parametrize("value", ["", "   "])
def test_recording_file_socket_location_rejects_empty_explicit_path(
    value: str,
) -> None:
    with pytest.raises(ValueError, match="recording-file socket path"):
        resolve_daemon_recording_file_socket_location(value)


def test_recording_file_socket_location_rejects_relative_explicit_path() -> None:
    with pytest.raises(ValueError, match="must be absolute"):
        resolve_daemon_recording_file_socket_location(
            "relative/recordings.sock"
        )


def test_recording_file_socket_location_prefers_xdg_runtime(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"

    location = resolve_daemon_recording_file_socket_location(
        environ={"XDG_RUNTIME_DIR": str(runtime_root)},
        home=tmp_path / "home",
    )

    assert location.path == (
        runtime_root
        / "sdsctl"
        / DAEMON_RECORDING_FILE_SOCKET_FILENAME
    )
    assert location.source is DaemonSocketSource.XDG_RUNTIME


def test_recording_file_socket_location_uses_user_state_fallback(
    tmp_path: Path,
) -> None:
    paths = resolve_configuration_paths(
        environ={},
        home=tmp_path / "home",
        system_config_dir=tmp_path / "etc" / "sdsctl",
    )

    location = resolve_daemon_recording_file_socket_location(
        environ={},
        configuration_paths=paths,
    )

    assert location.path == (
        paths.user_state_dir / DAEMON_RECORDING_FILE_SOCKET_FILENAME
    )
    assert location.source is DaemonSocketSource.USER_STATE
