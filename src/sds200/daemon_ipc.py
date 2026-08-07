from __future__ import annotations

import errno
import os
import socket as socket_module
import stat
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .configuration import (
    CONFIG_DIRECTORY_NAME,
    ConfigurationPaths,
    resolve_configuration_paths,
)
from .exceptions import DaemonIpcError

DAEMON_EVENT_SOCKET_FILENAME = "events.sock"
DAEMON_PCMU_SOCKET_FILENAME = "pcmu.sock"
DAEMON_RECORDING_FILE_SOCKET_FILENAME = "recordings.sock"
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


def resolve_daemon_event_socket_location(
    socket_path: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    configuration_paths: ConfigurationPaths | None = None,
    home: str | Path | None = None,
) -> DaemonSocketLocation:
    """Resolve one daemon event socket location without filesystem changes."""

    if socket_path is not None:
        if isinstance(socket_path, str) and not socket_path.strip():
            raise ValueError("Daemon event socket path must not be empty.")
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
            runtime_root
            / CONFIG_DIRECTORY_NAME
            / DAEMON_EVENT_SOCKET_FILENAME,
            DaemonSocketSource.XDG_RUNTIME,
        )

    paths = configuration_paths or resolve_configuration_paths(
        environ=environment,
        home=home,
    )
    return DaemonSocketLocation(
        paths.user_state_dir / DAEMON_EVENT_SOCKET_FILENAME,
        DaemonSocketSource.USER_STATE,
    )


def resolve_daemon_pcmu_socket_location(
    socket_path: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    configuration_paths: ConfigurationPaths | None = None,
    home: str | Path | None = None,
) -> DaemonSocketLocation:
    """Resolve one daemon PCMU socket location without filesystem changes."""

    if socket_path is not None:
        if isinstance(socket_path, str) and not socket_path.strip():
            raise ValueError("Daemon PCMU socket path must not be empty.")
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
            runtime_root
            / CONFIG_DIRECTORY_NAME
            / DAEMON_PCMU_SOCKET_FILENAME,
            DaemonSocketSource.XDG_RUNTIME,
        )

    paths = configuration_paths or resolve_configuration_paths(
        environ=environment,
        home=home,
    )
    return DaemonSocketLocation(
        paths.user_state_dir / DAEMON_PCMU_SOCKET_FILENAME,
        DaemonSocketSource.USER_STATE,
    )


def resolve_daemon_recording_file_socket_location(
    socket_path: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    configuration_paths: ConfigurationPaths | None = None,
    home: str | Path | None = None,
) -> DaemonSocketLocation:
    """Resolve the daemon recording-file socket without filesystem changes."""

    if socket_path is not None:
        if isinstance(socket_path, str) and not socket_path.strip():
            raise ValueError(
                "Daemon recording-file socket path must not be empty."
            )
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
            runtime_root
            / CONFIG_DIRECTORY_NAME
            / DAEMON_RECORDING_FILE_SOCKET_FILENAME,
            DaemonSocketSource.XDG_RUNTIME,
        )

    paths = configuration_paths or resolve_configuration_paths(
        environ=environment,
        home=home,
    )
    return DaemonSocketLocation(
        paths.user_state_dir / DAEMON_RECORDING_FILE_SOCKET_FILENAME,
        DaemonSocketSource.USER_STATE,
    )


@dataclass(frozen=True, slots=True)
class _DaemonSocketIdentity:
    device: int
    inode: int


class DaemonSocketListener:
    """Own one private Unix-domain listening socket and its filesystem entry."""

    def __init__(
        self,
        location: DaemonSocketLocation,
        *,
        backlog: int = 8,
        probe_timeout: float = 0.2,
    ) -> None:
        if isinstance(backlog, bool) or not isinstance(backlog, int):
            raise TypeError("Daemon socket backlog must be an integer.")
        if backlog <= 0:
            raise ValueError("Daemon socket backlog must be greater than zero.")
        if isinstance(probe_timeout, bool) or not isinstance(
            probe_timeout,
            (int, float),
        ):
            raise TypeError("Daemon socket probe timeout must be a number.")
        if probe_timeout <= 0:
            raise ValueError(
                "Daemon socket probe timeout must be greater than zero."
            )

        self.location = location
        self.backlog = backlog
        self.probe_timeout = float(probe_timeout)
        self._lifecycle_lock = threading.RLock()
        self._listener: socket_module.socket | None = None
        self._identity: _DaemonSocketIdentity | None = None
        self._stopped = False

    @property
    def active(self) -> bool:
        with self._lifecycle_lock:
            return self._listener is not None

    @property
    def socket(self) -> socket_module.socket:
        with self._lifecycle_lock:
            if self._listener is None:
                raise RuntimeError("Daemon socket listener is not active.")
            return self._listener

    def start(self) -> socket_module.socket:
        with self._lifecycle_lock:
            if self._listener is not None:
                return self._listener
            if self._stopped:
                raise RuntimeError(
                    "Daemon socket listeners cannot be restarted after shutdown."
                )

            self._prepare_parent()
            self._remove_stale_socket()

            listener = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
            identity: _DaemonSocketIdentity | None = None
            try:
                listener.bind(os.fspath(self.location.path))
                identity = _socket_identity(self.location.path)
                os.chmod(self.location.path, DAEMON_SOCKET_MODE)
                listener.listen(self.backlog)
            except BaseException:
                listener.close()
                if identity is not None:
                    _unlink_matching_socket(self.location.path, identity)
                raise

            self._listener = listener
            self._identity = identity
            return listener

    def stop(self) -> None:
        with self._lifecycle_lock:
            listener = self._listener
            identity = self._identity
            self._listener = None
            self._identity = None
            self._stopped = True

            close_error: BaseException | None = None
            if listener is not None:
                try:
                    listener.close()
                except BaseException as error:
                    close_error = error

            unlink_error: BaseException | None = None
            if identity is not None:
                try:
                    _unlink_matching_socket(self.location.path, identity)
                except BaseException as error:
                    unlink_error = error

            if close_error is not None:
                raise close_error
            if unlink_error is not None:
                raise unlink_error

    def __enter__(self) -> DaemonSocketListener:
        self.start()
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception_type, traceback

        try:
            self.stop()
        except BaseException:
            if exception is None:
                raise

    def _prepare_parent(self) -> None:
        parent = self.location.parent

        if self.location.managed_parent:
            parent.mkdir(
                mode=DAEMON_SOCKET_DIRECTORY_MODE,
                parents=True,
                exist_ok=True,
            )

        try:
            observed = parent.lstat()
        except FileNotFoundError as error:
            raise DaemonIpcError(
                f"Daemon socket parent directory does not exist: {parent}"
            ) from error

        if stat.S_ISLNK(observed.st_mode):
            raise DaemonIpcError(
                f"Daemon socket parent must not be a symlink: {parent}"
            )
        if not stat.S_ISDIR(observed.st_mode):
            raise DaemonIpcError(
                f"Daemon socket parent is not a directory: {parent}"
            )

        if self.location.managed_parent:
            effective_uid = getattr(os, "geteuid", lambda: observed.st_uid)()
            if observed.st_uid != effective_uid:
                raise DaemonIpcError(
                    "Managed daemon socket directory is not owned by the "
                    f"current user: {parent}"
                )
            os.chmod(parent, DAEMON_SOCKET_DIRECTORY_MODE)

    def _remove_stale_socket(self) -> None:
        path = self.location.path

        try:
            before = path.lstat()
        except FileNotFoundError:
            return

        if stat.S_ISLNK(before.st_mode):
            raise DaemonIpcError(
                f"Daemon socket path must not be a symlink: {path}"
            )
        if not stat.S_ISSOCK(before.st_mode):
            raise DaemonIpcError(
                f"Daemon socket path is occupied by a non-socket entry: {path}"
            )

        probe = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
        probe.settimeout(self.probe_timeout)
        try:
            probe.connect(os.fspath(path))
        except OSError as error:
            if error.errno == errno.ENOENT:
                return
            if error.errno != errno.ECONNREFUSED:
                raise DaemonIpcError(
                    f"Could not safely probe daemon socket ownership: {path}"
                ) from error
        else:
            raise DaemonIpcError(f"Daemon socket is already active: {path}")
        finally:
            probe.close()

        try:
            after = path.lstat()
        except FileNotFoundError:
            return

        before_identity = _DaemonSocketIdentity(before.st_dev, before.st_ino)
        after_identity = _DaemonSocketIdentity(after.st_dev, after.st_ino)
        if (
            before_identity != after_identity
            or not stat.S_ISSOCK(after.st_mode)
        ):
            raise DaemonIpcError(
                f"Daemon socket ownership changed while probing: {path}"
            )

        path.unlink()


def _socket_identity(path: Path) -> _DaemonSocketIdentity:
    observed = path.lstat()
    if not stat.S_ISSOCK(observed.st_mode):
        raise DaemonIpcError(
            f"Daemon socket path did not become a socket: {path}"
        )
    return _DaemonSocketIdentity(observed.st_dev, observed.st_ino)


def _unlink_matching_socket(
    path: Path,
    expected: _DaemonSocketIdentity,
) -> None:
    try:
        observed = path.lstat()
    except FileNotFoundError:
        return

    actual = _DaemonSocketIdentity(observed.st_dev, observed.st_ino)
    if actual != expected or not stat.S_ISSOCK(observed.st_mode):
        return

    path.unlink()
