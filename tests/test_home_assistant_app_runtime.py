from __future__ import annotations

from pathlib import Path

import pytest

from sds200.home_assistant_app import HomeAssistantAppOptions
from sds200.home_assistant_app_runtime import (
    HOME_ASSISTANT_APP_INGRESS_PORT,
    HOME_ASSISTANT_APP_RECORDING_DIRECTORY,
    HOME_ASSISTANT_APP_RTP_PORT,
    HOME_ASSISTANT_APP_RUNTIME_DIRECTORY,
    HomeAssistantAppRuntimePaths,
    build_home_assistant_daemon_command,
    build_home_assistant_web_command,
    default_home_assistant_app_runtime_paths,
)


def test_default_home_assistant_app_runtime_paths_are_private_and_absolute() -> None:
    paths = default_home_assistant_app_runtime_paths()

    assert paths.runtime_directory == HOME_ASSISTANT_APP_RUNTIME_DIRECTORY
    assert paths.mqtt_configuration == Path("/run/sdsctl/daemon-mqtt.toml")
    assert paths.daemon_socket == Path("/run/sdsctl/daemon.sock")
    assert paths.event_socket == Path("/run/sdsctl/events.sock")
    assert paths.pcmu_socket == Path("/run/sdsctl/pcmu.sock")
    assert paths.recording_file_socket == Path("/run/sdsctl/recordings.sock")
    assert paths.recording_directory == HOME_ASSISTANT_APP_RECORDING_DIRECTORY
    assert paths.recording_directory == Path("/media/sdsctl/recordings")


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("runtime_directory", Path("run/sdsctl")),
        ("mqtt_configuration", Path("daemon-mqtt.toml")),
        ("daemon_socket", Path("daemon.sock")),
        ("event_socket", Path("events.sock")),
        ("pcmu_socket", Path("pcmu.sock")),
        ("recording_file_socket", Path("recordings.sock")),
        ("recording_directory", Path("recordings")),
    ],
)
def test_home_assistant_app_runtime_paths_require_absolute_paths(
    field_name: str,
    value: Path,
) -> None:
    values = {
        "runtime_directory": Path("/run/sdsctl"),
        "mqtt_configuration": Path("/run/sdsctl/daemon-mqtt.toml"),
        "daemon_socket": Path("/run/sdsctl/daemon.sock"),
        "event_socket": Path("/run/sdsctl/events.sock"),
        "pcmu_socket": Path("/run/sdsctl/pcmu.sock"),
        "recording_file_socket": Path("/run/sdsctl/recordings.sock"),
        "recording_directory": Path("/media/sdsctl/recordings"),
    }
    values[field_name] = value

    with pytest.raises(ValueError, match="must be absolute"):
        HomeAssistantAppRuntimePaths(**values)  # type: ignore[arg-type]


def test_home_assistant_app_runtime_files_stay_inside_runtime_directory() -> None:
    with pytest.raises(ValueError, match="directly inside /run/sdsctl"):
        HomeAssistantAppRuntimePaths(
            runtime_directory=Path("/run/sdsctl"),
            mqtt_configuration=Path("/tmp/daemon-mqtt.toml"),
            daemon_socket=Path("/run/sdsctl/daemon.sock"),
            event_socket=Path("/run/sdsctl/events.sock"),
            pcmu_socket=Path("/run/sdsctl/pcmu.sock"),
            recording_file_socket=Path("/run/sdsctl/recordings.sock"),
            recording_directory=Path("/media/sdsctl/recordings"),
        )


def test_home_assistant_daemon_command_uses_explicit_private_paths() -> None:
    paths = default_home_assistant_app_runtime_paths()
    options = HomeAssistantAppOptions(
        scanner_host="192.0.2.25",
        mqtt_topic_prefix="scanner/main",
    )

    assert build_home_assistant_daemon_command(
        options,
        paths,
    ) == (
        "sdsctl",
        "--host",
        "192.0.2.25",
        "daemon",
        "--rtp-bind-port",
        str(HOME_ASSISTANT_APP_RTP_PORT),
        "--mqtt-config",
        "/run/sdsctl/daemon-mqtt.toml",
        "--recording-directory",
        "/media/sdsctl/recordings",
        "--socket-path",
        "/run/sdsctl/daemon.sock",
        "--event-socket-path",
        "/run/sdsctl/events.sock",
        "--pcmu-socket-path",
        "/run/sdsctl/pcmu.sock",
        "--recording-file-socket-path",
        "/run/sdsctl/recordings.sock",
    )


def test_home_assistant_daemon_command_never_contains_mqtt_password() -> None:
    command = build_home_assistant_daemon_command(
        HomeAssistantAppOptions(scanner_host="scanner.local"),
        default_home_assistant_app_runtime_paths(),
    )

    assert all("password" not in argument.casefold() for argument in command)


def test_home_assistant_runtime_uses_configured_media_subdirectory() -> None:
    options = HomeAssistantAppOptions(
        scanner_host="scanner.local",
        recording_directory="radio/sds200",
    )

    paths = default_home_assistant_app_runtime_paths(options)

    assert paths.recording_directory == Path("/media/radio/sds200")


def test_home_assistant_web_command_enables_ingress_and_private_clients() -> None:
    paths = default_home_assistant_app_runtime_paths()

    assert build_home_assistant_web_command(paths) == (
        "sdsctl",
        "web",
        "--home-assistant-ingress",
        "--daemon-socket-path",
        "/run/sdsctl/daemon.sock",
        "--daemon-event-socket-path",
        "/run/sdsctl/events.sock",
        "--daemon-pcmu-socket-path",
        "/run/sdsctl/pcmu.sock",
        "--daemon-recording-file-socket-path",
        "/run/sdsctl/recordings.sock",
        "--listen-port",
        str(HOME_ASSISTANT_APP_INGRESS_PORT),
    )


@pytest.mark.parametrize("port", [0, 65536])
def test_home_assistant_web_command_rejects_invalid_ingress_port(
    port: int,
) -> None:
    with pytest.raises(ValueError, match="between 1 and 65535"):
        build_home_assistant_web_command(
            default_home_assistant_app_runtime_paths(),
            ingress_port=port,
        )
