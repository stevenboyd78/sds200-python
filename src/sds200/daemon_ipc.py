from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .configuration import (
    CONFIG_DIRECTORY_NAME,
    ConfigurationPaths,
    resolve_configuration_paths,
)

DAEMON_SOCKET_FILENAME = "daemon.sock"
DAEMON_SOCKET_DIRECTORY_MODE = 0o700
DAEMON_SOCKET_MODE = 0o600


class DaemonSocketSource(StrEnum):
    """Origin of one resolved local daemon socket path."""

    EXPLICIT = "explicit"
    XDG_RUNTIME = "xdg-runtime"
    USER_STATE = "user-state"


@dataclass(frozen=True, slots=True)
class DaemonSocketLocation:
    """One immutable local daemon socket-path decision."""

    path: Path
    source: DaemonSocketSource

    def __post_init__(self) -> None:
        path = Path(self.path)
        if not path.is_absolute():
            raise ValueError(f"Daemon socket path must be absolute: {path}")
        if not path.name or path.name in {".", ".."}:
            raise ValueError("Daemon socket path must name a socket file.")
        if "\x00" in os.fspath(path):
            raise ValueError("Daemon socket path must not contain a null byte.")
        if not isinstance(self.source, DaemonSocketSource):
            raise TypeError("Daemon socket source must be a DaemonSocketSource.")

        object.__setattr__(self, "path", path)

    @property
    def parent(self) -> Path:
        return self.path.parent

    @property
    def managed_parent(self) -> bool:
        return self.source is not DaemonSocketSource.EXPLICIT


def resolve_daemon_socket_location(
    socket_path: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    configuration_paths: ConfigurationPaths | None = None,
    home: str | Path | None = None,
) -> DaemonSocketLocation:
    """Resolve one daemon socket location without modifying the filesystem."""

    if socket_path is not None:
        if isinstance(socket_path, str) and not socket_path.strip():
            raise ValueError("Daemon socket path must not be empty.")
        return DaemonSocketLocation(
            Path(socket_path),
            DaemonSocketSource.EXPLICIT,
        )

    environment = os.environ if environ is None else environ
    runtime_value = environment.get("XDG_RUNTIME_DIR")
    if runtime_value:
        runtime_root = Path(runtime_value)
        if not runtime_root.is_absolute():
            raise ValueError(
                f"XDG_RUNTIME_DIR must be an absolute path: {runtime_root}"
            )
        return DaemonSocketLocation(
            runtime_root / CONFIG_DIRECTORY_NAME / DAEMON_SOCKET_FILENAME,
            DaemonSocketSource.XDG_RUNTIME,
        )

    paths = configuration_paths or resolve_configuration_paths(
        environ=environment,
        home=home,
    )
    return DaemonSocketLocation(
        paths.user_state_dir / DAEMON_SOCKET_FILENAME,
        DaemonSocketSource.USER_STATE,
    )
