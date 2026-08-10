from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .daemon_ipc import (
    DAEMON_EVENT_SOCKET_FILENAME,
    DAEMON_PCMU_SOCKET_FILENAME,
    DAEMON_RECORDING_FILE_SOCKET_FILENAME,
    DAEMON_SOCKET_FILENAME,
)
from .home_assistant_app import (
    HOME_ASSISTANT_APP_DEFAULT_RECORDING_DIRECTORY,
    HomeAssistantAppOptions,
)

HOME_ASSISTANT_APP_RUNTIME_DIRECTORY = Path("/run/sdsctl")
HOME_ASSISTANT_APP_MQTT_CONFIG_FILENAME = "daemon-mqtt.toml"
HOME_ASSISTANT_APP_MEDIA_DIRECTORY = Path("/media")
HOME_ASSISTANT_APP_LEGACY_RECORDING_DIRECTORY = Path("/data/recordings")
HOME_ASSISTANT_APP_RECORDING_DIRECTORY = (
    HOME_ASSISTANT_APP_MEDIA_DIRECTORY
    / HOME_ASSISTANT_APP_DEFAULT_RECORDING_DIRECTORY
)
HOME_ASSISTANT_APP_INGRESS_PORT = 8099
HOME_ASSISTANT_APP_RTP_PORT = 50000
HOME_ASSISTANT_APP_EXECUTABLE = "sdsctl"


def _require_absolute_path(value: object, *, label: str) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        raise TypeError(f"{label} must be a path.")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{label} must be absolute: {path}")
    return path


def _require_executable(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("Home Assistant App executable must be a string.")
    if not value or value.strip() != value:
        raise ValueError(
            "Home Assistant App executable must not be empty or padded."
        )
    if "\x00" in value:
        raise ValueError(
            "Home Assistant App executable must not contain a null byte."
        )
    return value


@dataclass(frozen=True, slots=True)
class HomeAssistantAppRuntimePaths:
    """Absolute runtime paths shared by the daemon and Ingress web children."""

    runtime_directory: Path
    mqtt_configuration: Path
    daemon_socket: Path
    event_socket: Path
    pcmu_socket: Path
    recording_file_socket: Path
    recording_directory: Path

    def __post_init__(self) -> None:
        runtime_directory = _require_absolute_path(
            self.runtime_directory,
            label="Home Assistant App runtime directory",
        )
        object.__setattr__(self, "runtime_directory", runtime_directory)

        for field_name, label in (
            ("mqtt_configuration", "Home Assistant App MQTT configuration"),
            ("daemon_socket", "Home Assistant App daemon socket"),
            ("event_socket", "Home Assistant App event socket"),
            ("pcmu_socket", "Home Assistant App PCMU socket"),
            (
                "recording_file_socket",
                "Home Assistant App recording-file socket",
            ),
        ):
            path = _require_absolute_path(
                getattr(self, field_name),
                label=label,
            )
            if path.parent != runtime_directory:
                raise ValueError(
                    f"{label} must be directly inside "
                    f"{runtime_directory}: {path}"
                )
            object.__setattr__(self, field_name, path)

        object.__setattr__(
            self,
            "recording_directory",
            _require_absolute_path(
                self.recording_directory,
                label="Home Assistant App recording directory",
            ),
        )


def default_home_assistant_app_runtime_paths(
    options: HomeAssistantAppOptions | None = None,
) -> HomeAssistantAppRuntimePaths:
    """Return private runtime paths plus the configured mapped-media library."""

    runtime = HOME_ASSISTANT_APP_RUNTIME_DIRECTORY
    recording_relative = (
        HOME_ASSISTANT_APP_DEFAULT_RECORDING_DIRECTORY
        if options is None
        else options.recording_directory
    )
    return HomeAssistantAppRuntimePaths(
        runtime_directory=runtime,
        mqtt_configuration=runtime / HOME_ASSISTANT_APP_MQTT_CONFIG_FILENAME,
        daemon_socket=runtime / DAEMON_SOCKET_FILENAME,
        event_socket=runtime / DAEMON_EVENT_SOCKET_FILENAME,
        pcmu_socket=runtime / DAEMON_PCMU_SOCKET_FILENAME,
        recording_file_socket=(
            runtime / DAEMON_RECORDING_FILE_SOCKET_FILENAME
        ),
        recording_directory=(
            HOME_ASSISTANT_APP_MEDIA_DIRECTORY / recording_relative
        ),
    )


def build_home_assistant_daemon_command(
    options: HomeAssistantAppOptions,
    paths: HomeAssistantAppRuntimePaths,
    *,
    executable: str = HOME_ASSISTANT_APP_EXECUTABLE,
) -> tuple[str, ...]:
    """Build the single-owner daemon child command without embedding secrets."""

    if not isinstance(options, HomeAssistantAppOptions):
        raise TypeError(
            "Home Assistant daemon command requires App options."
        )
    if not isinstance(paths, HomeAssistantAppRuntimePaths):
        raise TypeError(
            "Home Assistant daemon command requires App runtime paths."
        )

    program = _require_executable(executable)
    return (
        program,
        "--host",
        options.scanner_host,
        "daemon",
        "--rtp-bind-port",
        str(HOME_ASSISTANT_APP_RTP_PORT),
        "--mqtt-config",
        os.fspath(paths.mqtt_configuration),
        "--recording-directory",
        os.fspath(paths.recording_directory),
        "--socket-path",
        os.fspath(paths.daemon_socket),
        "--event-socket-path",
        os.fspath(paths.event_socket),
        "--pcmu-socket-path",
        os.fspath(paths.pcmu_socket),
        "--recording-file-socket-path",
        os.fspath(paths.recording_file_socket),
    )


def build_home_assistant_web_command(
    paths: HomeAssistantAppRuntimePaths,
    *,
    executable: str = HOME_ASSISTANT_APP_EXECUTABLE,
    ingress_port: int = HOME_ASSISTANT_APP_INGRESS_PORT,
) -> tuple[str, ...]:
    """Build the Ingress web child command against the private daemon sockets."""

    if not isinstance(paths, HomeAssistantAppRuntimePaths):
        raise TypeError(
            "Home Assistant web command requires App runtime paths."
        )
    if type(ingress_port) is not int:
        raise TypeError(
            "Home Assistant App Ingress port must be an integer."
        )
    if not 1 <= ingress_port <= 65535:
        raise ValueError(
            "Home Assistant App Ingress port must be between 1 and 65535."
        )

    program = _require_executable(executable)
    return (
        program,
        "web",
        "--home-assistant-ingress",
        "--daemon-socket-path",
        os.fspath(paths.daemon_socket),
        "--daemon-event-socket-path",
        os.fspath(paths.event_socket),
        "--daemon-pcmu-socket-path",
        os.fspath(paths.pcmu_socket),
        "--daemon-recording-file-socket-path",
        os.fspath(paths.recording_file_socket),
        "--listen-port",
        str(ingress_port),
    )


__all__ = [
    "HOME_ASSISTANT_APP_EXECUTABLE",
    "HOME_ASSISTANT_APP_INGRESS_PORT",
    "HOME_ASSISTANT_APP_MQTT_CONFIG_FILENAME",
    "HOME_ASSISTANT_APP_MEDIA_DIRECTORY",
    "HOME_ASSISTANT_APP_LEGACY_RECORDING_DIRECTORY",
    "HOME_ASSISTANT_APP_RECORDING_DIRECTORY",
    "HOME_ASSISTANT_APP_RTP_PORT",
    "HOME_ASSISTANT_APP_RUNTIME_DIRECTORY",
    "HomeAssistantAppRuntimePaths",
    "build_home_assistant_daemon_command",
    "build_home_assistant_web_command",
    "default_home_assistant_app_runtime_paths",
]
