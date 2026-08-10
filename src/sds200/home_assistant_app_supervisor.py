from __future__ import annotations

import filecmp
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from types import FrameType
from typing import Any, Protocol, Self, cast

from .daemon_client import DaemonApiClient
from .daemon_ipc import DaemonSocketLocation, DaemonSocketSource
from .exceptions import DaemonClientError, SDS200Error
from .home_assistant_app import (
    HOME_ASSISTANT_APP_MQTT_PASSWORD_VARIABLE,
    HOME_ASSISTANT_APP_OPTIONS_PATH,
    HOME_ASSISTANT_SUPERVISOR_TOKEN_VARIABLE,
    HomeAssistantAppOptions,
    HomeAssistantMqttService,
    fetch_home_assistant_mqtt_service,
    home_assistant_mqtt_password_environment,
    load_home_assistant_app_options,
    write_home_assistant_daemon_mqtt_configuration,
)
from .home_assistant_app_runtime import (
    HOME_ASSISTANT_APP_LEGACY_RECORDING_DIRECTORY,
    HomeAssistantAppRuntimePaths,
    build_home_assistant_daemon_command,
    build_home_assistant_web_command,
    default_home_assistant_app_runtime_paths,
)

logger = logging.getLogger(__name__)

HOME_ASSISTANT_APP_DAEMON_READY_TIMEOUT = 15.0
HOME_ASSISTANT_APP_DAEMON_READY_POLL_INTERVAL = 0.1
HOME_ASSISTANT_APP_DAEMON_PROBE_TIMEOUT = 0.5
HOME_ASSISTANT_APP_WEB_STOP_TIMEOUT = 5.0
HOME_ASSISTANT_APP_DAEMON_STOP_TIMEOUT = 30.0
HOME_ASSISTANT_APP_FORCE_STOP_TIMEOUT = 5.0
HOME_ASSISTANT_APP_SUPERVISOR_POLL_INTERVAL = 0.1
HOME_ASSISTANT_APP_RUNTIME_DIRECTORY_MODE = 0o700
HOME_ASSISTANT_APP_RECORDING_DIRECTORY_MODE = 0o755


class HomeAssistantAppChildProcess(Protocol):
    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...


class HomeAssistantAppSignalControllerLike(Protocol):
    @property
    def stop_requested(self) -> bool: ...

    @property
    def last_signal(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> bool: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None: ...


HomeAssistantAppProcessFactory = Callable[
    [Sequence[str], Mapping[str, str]],
    HomeAssistantAppChildProcess,
]
HomeAssistantAppDaemonReadyProbe = Callable[[Path, float], bool]


@dataclass(frozen=True, slots=True)
class HomeAssistantAppLaunchPlan:
    """Prepared child commands and least-privilege child environments."""

    options: HomeAssistantAppOptions
    mqtt_service: HomeAssistantMqttService
    paths: HomeAssistantAppRuntimePaths
    daemon_command: tuple[str, ...]
    web_command: tuple[str, ...]
    daemon_environment: Mapping[str, str] = field(repr=False)
    web_environment: Mapping[str, str] = field(repr=False)


class HomeAssistantAppSignalController:
    """Translate container stop signals into one supervisor stop request."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._previous: dict[int, object] = {}
        self._active = False
        self._last_signal: int | None = None
        self._stop_requested = False

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested

    @property
    def last_signal(self) -> int | None:
        return self._last_signal

    def wait(self, timeout: float | None = None) -> bool:
        triggered = self._event.wait(timeout)
        if triggered:
            self._event.clear()
        return triggered

    def __enter__(self) -> HomeAssistantAppSignalController:
        if self._active:
            raise RuntimeError(
                "Home Assistant App signal controller is already active."
            )

        self._event.clear()
        self._last_signal = None
        self._stop_requested = False
        installed: list[int] = []
        try:
            for signum in _home_assistant_app_stop_signals():
                self._previous[signum] = signal.getsignal(signum)
                signal.signal(signum, self._handle)
                installed.append(signum)
        except BaseException as installation_error:
            rollback_failures: list[BaseException] = []
            for signum in reversed(installed):
                try:
                    signal.signal(
                        signum,
                        cast(Any, self._previous[signum]),
                    )
                except BaseException as rollback_error:
                    rollback_failures.append(rollback_error)
            self._previous.clear()

            if rollback_failures:
                logger.error(
                    "Home Assistant App signal rollback failed "
                    "installation_error=%s rollback_error=%s",
                    installation_error.__class__.__name__,
                    rollback_failures[0].__class__.__name__,
                )
            raise

        self._active = True
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception_type, traceback

        restoration_failures: list[BaseException] = []
        for signum, previous in self._previous.items():
            try:
                signal.signal(signum, cast(Any, previous))
            except BaseException as restoration_error:
                restoration_failures.append(restoration_error)

        self._previous.clear()
        self._active = False

        if not restoration_failures:
            return
        if exception is not None:
            logger.error(
                "Home Assistant App signal restoration failed "
                "process_error=%s restoration_error=%s",
                exception.__class__.__name__,
                restoration_failures[0].__class__.__name__,
            )
            return
        raise restoration_failures[0]

    def _handle(self, signum: int, frame: FrameType | None) -> None:
        del frame
        self._last_signal = signum
        self._stop_requested = True
        self._event.set()


def _home_assistant_app_stop_signals() -> tuple[int, ...]:
    signals: list[int] = []
    for name in ("SIGINT", "SIGTERM"):
        value = getattr(signal, name, None)
        if isinstance(value, int) and value not in signals:
            signals.append(int(value))
    return tuple(signals)


def _default_process_factory(
    command: Sequence[str],
    environment: Mapping[str, str],
) -> HomeAssistantAppChildProcess:
    return subprocess.Popen(
        tuple(command),
        env=dict(environment),
    )


def _default_daemon_ready_probe(
    socket_path: Path,
    timeout: float,
) -> bool:
    location = DaemonSocketLocation(
        socket_path,
        DaemonSocketSource.EXPLICIT,
    )
    try:
        with DaemonApiClient(location, timeout=timeout) as client:
            client.hello()
    except DaemonClientError:
        return False
    return True


def _require_positive_seconds(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be a number.")
    normalized = float(value)
    if not isfinite(normalized) or normalized <= 0:
        raise ValueError(
            f"{label} must be finite and greater than zero."
        )
    return normalized


def _prepare_runtime_directories(paths: HomeAssistantAppRuntimePaths) -> None:
    paths.runtime_directory.mkdir(
        parents=True,
        exist_ok=True,
        mode=HOME_ASSISTANT_APP_RUNTIME_DIRECTORY_MODE,
    )
    paths.runtime_directory.chmod(
        HOME_ASSISTANT_APP_RUNTIME_DIRECTORY_MODE
    )
    recording_directory_existed = paths.recording_directory.exists()
    paths.recording_directory.mkdir(
        parents=True,
        exist_ok=True,
        mode=HOME_ASSISTANT_APP_RECORDING_DIRECTORY_MODE,
    )
    if not recording_directory_existed:
        paths.recording_directory.chmod(
            HOME_ASSISTANT_APP_RECORDING_DIRECTORY_MODE
        )


def migrate_home_assistant_app_recordings(
    destination: str | Path,
    *,
    legacy_directory: str | Path = HOME_ASSISTANT_APP_LEGACY_RECORDING_DIRECTORY,
) -> int:
    """Safely migrate persistent v0.20.0 recordings into mapped media storage."""

    source = Path(legacy_directory)
    target = Path(destination)

    if not source.is_absolute() or not target.is_absolute():
        raise ValueError(
            "Home Assistant recording migration paths must be absolute."
        )
    if source == target or not source.exists():
        return 0
    if not source.is_dir():
        raise SDS200Error(
            f"Legacy Home Assistant recording path is not a directory: {source}"
        )

    target.mkdir(
        parents=True,
        exist_ok=True,
        mode=HOME_ASSISTANT_APP_RECORDING_DIRECTORY_MODE,
    )

    source_files = sorted(
        path
        for path in source.rglob("*")
        if not path.is_dir()
    )

    # Preflight the complete migration before moving anything. Existing
    # identical destination files are safe leftovers from an interrupted
    # migration; differing collisions abort without overwriting either copy.
    for source_file in source_files:
        if source_file.is_symlink():
            raise SDS200Error(
                "Legacy Home Assistant recording migration refuses symlinks: "
                f"{source_file}"
            )

        relative = source_file.relative_to(source)
        destination_file = target / relative
        if not destination_file.exists():
            continue
        if (
            not destination_file.is_file()
            or not filecmp.cmp(
                source_file,
                destination_file,
                shallow=False,
            )
        ):
            raise SDS200Error(
                "Legacy Home Assistant recording migration found a "
                f"conflicting destination file: {destination_file}"
            )

    migrated = 0
    for source_file in source_files:
        relative = source_file.relative_to(source)
        destination_file = target / relative
        destination_file.parent.mkdir(
            parents=True,
            exist_ok=True,
            mode=HOME_ASSISTANT_APP_RECORDING_DIRECTORY_MODE,
        )

        if destination_file.exists():
            source_file.unlink()
            continue

        shutil.copy2(source_file, destination_file)
        if not filecmp.cmp(
            source_file,
            destination_file,
            shallow=False,
        ):
            destination_file.unlink(missing_ok=True)
            raise SDS200Error(
                "Legacy Home Assistant recording migration verification "
                f"failed for: {source_file}"
            )

        source_file.unlink()
        migrated += 1

    directories = sorted(
        (path for path in source.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for directory in directories:
        with suppress(OSError):
            directory.rmdir()

    with suppress(OSError):
        source.rmdir()

    if migrated:
        logger.info(
            "Migrated %d Home Assistant recording file(s) from %s to %s",
            migrated,
            source,
            target,
        )
    return migrated


def _child_environments(
    service: HomeAssistantMqttService,
    environ: Mapping[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    base = dict(environ)
    base.pop(HOME_ASSISTANT_SUPERVISOR_TOKEN_VARIABLE, None)
    base.pop(HOME_ASSISTANT_APP_MQTT_PASSWORD_VARIABLE, None)

    daemon_environment = dict(base)
    daemon_environment.update(
        home_assistant_mqtt_password_environment(service)
    )
    web_environment = dict(base)
    return daemon_environment, web_environment


def prepare_home_assistant_app_launch_plan(
    *,
    options_path: str | Path = HOME_ASSISTANT_APP_OPTIONS_PATH,
    paths: HomeAssistantAppRuntimePaths | None = None,
    environ: Mapping[str, str] | None = None,
) -> HomeAssistantAppLaunchPlan:
    """Load App state and prepare child commands without starting processes."""

    source_environment = (
        dict(os.environ)
        if environ is None
        else dict(environ)
    )
    options = load_home_assistant_app_options(options_path)
    using_default_paths = paths is None
    selected_paths = (
        default_home_assistant_app_runtime_paths(options)
        if using_default_paths
        else paths
    )
    if not isinstance(selected_paths, HomeAssistantAppRuntimePaths):
        raise TypeError(
            "Home Assistant App launch plan requires App runtime paths."
        )

    service = fetch_home_assistant_mqtt_service(
        environ=source_environment,
    )
    _prepare_runtime_directories(selected_paths)
    if using_default_paths:
        migrate_home_assistant_app_recordings(
            selected_paths.recording_directory
        )
    write_home_assistant_daemon_mqtt_configuration(
        selected_paths.mqtt_configuration,
        options,
        service,
    )
    daemon_environment, web_environment = _child_environments(
        service,
        source_environment,
    )

    return HomeAssistantAppLaunchPlan(
        options=options,
        mqtt_service=service,
        paths=selected_paths,
        daemon_command=build_home_assistant_daemon_command(
            options,
            selected_paths,
        ),
        web_command=build_home_assistant_web_command(selected_paths),
        daemon_environment=daemon_environment,
        web_environment=web_environment,
    )


class HomeAssistantAppSupervisor:
    """Run the daemon first, gate web startup on readiness, and stop in order."""

    def __init__(
        self,
        launch_plan: HomeAssistantAppLaunchPlan,
        *,
        process_factory: HomeAssistantAppProcessFactory | None = None,
        daemon_ready_probe: HomeAssistantAppDaemonReadyProbe | None = None,
        signals: HomeAssistantAppSignalControllerLike | None = None,
        daemon_ready_timeout: float = HOME_ASSISTANT_APP_DAEMON_READY_TIMEOUT,
        daemon_ready_poll_interval: float = (
            HOME_ASSISTANT_APP_DAEMON_READY_POLL_INTERVAL
        ),
        daemon_probe_timeout: float = HOME_ASSISTANT_APP_DAEMON_PROBE_TIMEOUT,
        web_stop_timeout: float = HOME_ASSISTANT_APP_WEB_STOP_TIMEOUT,
        daemon_stop_timeout: float = HOME_ASSISTANT_APP_DAEMON_STOP_TIMEOUT,
        force_stop_timeout: float = HOME_ASSISTANT_APP_FORCE_STOP_TIMEOUT,
        supervisor_poll_interval: float = (
            HOME_ASSISTANT_APP_SUPERVISOR_POLL_INTERVAL
        ),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(launch_plan, HomeAssistantAppLaunchPlan):
            raise TypeError(
                "Home Assistant App supervisor requires a launch plan."
            )
        self.launch_plan = launch_plan
        self.process_factory = process_factory or _default_process_factory
        self.daemon_ready_probe = (
            daemon_ready_probe or _default_daemon_ready_probe
        )
        self.signals = signals or HomeAssistantAppSignalController()
        self.daemon_ready_timeout = _require_positive_seconds(
            daemon_ready_timeout,
            label="Home Assistant App daemon readiness timeout",
        )
        self.daemon_ready_poll_interval = _require_positive_seconds(
            daemon_ready_poll_interval,
            label="Home Assistant App daemon readiness poll interval",
        )
        self.daemon_probe_timeout = _require_positive_seconds(
            daemon_probe_timeout,
            label="Home Assistant App daemon probe timeout",
        )
        self.web_stop_timeout = _require_positive_seconds(
            web_stop_timeout,
            label="Home Assistant App web stop timeout",
        )
        self.daemon_stop_timeout = _require_positive_seconds(
            daemon_stop_timeout,
            label="Home Assistant App daemon stop timeout",
        )
        self.force_stop_timeout = _require_positive_seconds(
            force_stop_timeout,
            label="Home Assistant App forced-stop timeout",
        )
        self.supervisor_poll_interval = _require_positive_seconds(
            supervisor_poll_interval,
            label="Home Assistant App supervisor poll interval",
        )
        self.monotonic = monotonic

    def run(self) -> int:
        daemon: HomeAssistantAppChildProcess | None = None
        web: HomeAssistantAppChildProcess | None = None
        process_error: BaseException | None = None

        with self.signals:
            try:
                daemon = self.process_factory(
                    self.launch_plan.daemon_command,
                    self.launch_plan.daemon_environment,
                )
                self._wait_for_daemon_ready(daemon)
                if self.signals.stop_requested:
                    return 0

                web = self.process_factory(
                    self.launch_plan.web_command,
                    self.launch_plan.web_environment,
                )
                self._wait_for_stop_or_child_exit(daemon, web)
            except BaseException as error:
                process_error = error
            finally:
                cleanup_errors: list[BaseException] = []
                for label, child, graceful_timeout in (
                    ("web", web, self.web_stop_timeout),
                    ("daemon", daemon, self.daemon_stop_timeout),
                ):
                    if child is None:
                        continue
                    try:
                        self._stop_child(
                            label,
                            child,
                            graceful_timeout=graceful_timeout,
                        )
                    except BaseException as cleanup_error:
                        cleanup_errors.append(cleanup_error)

                if process_error is not None:
                    if cleanup_errors:
                        logger.error(
                            "Home Assistant App cleanup failed "
                            "process_error=%s cleanup_error=%s",
                            process_error.__class__.__name__,
                            cleanup_errors[0].__class__.__name__,
                        )
                    raise process_error
                if cleanup_errors:
                    raise cleanup_errors[0]

        return 0

    def _wait_for_daemon_ready(
        self,
        daemon: HomeAssistantAppChildProcess,
    ) -> None:
        deadline = self.monotonic() + self.daemon_ready_timeout

        while True:
            returncode = daemon.poll()
            if returncode is not None:
                raise SDS200Error(
                    "Home Assistant App daemon exited before readiness "
                    f"with status {returncode}."
                )
            if self.signals.stop_requested:
                return

            remaining = deadline - self.monotonic()
            if remaining <= 0:
                raise SDS200Error(
                    "Home Assistant App daemon did not become ready before "
                    f"the {self.daemon_ready_timeout:g}-second deadline."
                )

            probe_timeout = min(
                self.daemon_probe_timeout,
                remaining,
            )
            if self.daemon_ready_probe(
                self.launch_plan.paths.daemon_socket,
                probe_timeout,
            ):
                return
            self.signals.wait(
                min(self.daemon_ready_poll_interval, remaining)
            )

    def _wait_for_stop_or_child_exit(
        self,
        daemon: HomeAssistantAppChildProcess,
        web: HomeAssistantAppChildProcess,
    ) -> None:
        while True:
            if self.signals.stop_requested:
                return

            daemon_returncode = daemon.poll()
            if daemon_returncode is not None:
                raise SDS200Error(
                    "Home Assistant App daemon exited unexpectedly "
                    f"with status {daemon_returncode}."
                )

            web_returncode = web.poll()
            if web_returncode is not None:
                raise SDS200Error(
                    "Home Assistant App web process exited unexpectedly "
                    f"with status {web_returncode}."
                )

            self.signals.wait(self.supervisor_poll_interval)

    def _stop_child(
        self,
        label: str,
        child: HomeAssistantAppChildProcess,
        *,
        graceful_timeout: float,
    ) -> None:
        if child.poll() is not None:
            return

        child.terminate()
        try:
            child.wait(timeout=graceful_timeout)
            return
        except subprocess.TimeoutExpired:
            logger.warning(
                "Home Assistant App %s process did not stop within %.1fs; "
                "forcing termination",
                label,
                graceful_timeout,
            )

        child.kill()
        child.wait(timeout=self.force_stop_timeout)


def run_home_assistant_app(
    *,
    options_path: str | Path = HOME_ASSISTANT_APP_OPTIONS_PATH,
    paths: HomeAssistantAppRuntimePaths | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Prepare and run the complete Home Assistant App process pair."""

    plan = prepare_home_assistant_app_launch_plan(
        options_path=options_path,
        paths=paths,
        environ=environ,
    )
    return HomeAssistantAppSupervisor(plan).run()


def main() -> int:
    """Console entry point used as PID 1 inside the Home Assistant App image."""

    try:
        return run_home_assistant_app()
    except (SDS200Error, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "HOME_ASSISTANT_APP_DAEMON_PROBE_TIMEOUT",
    "HOME_ASSISTANT_APP_DAEMON_STOP_TIMEOUT",
    "HOME_ASSISTANT_APP_FORCE_STOP_TIMEOUT",
    "HOME_ASSISTANT_APP_DAEMON_READY_POLL_INTERVAL",
    "HOME_ASSISTANT_APP_DAEMON_READY_TIMEOUT",
    "HOME_ASSISTANT_APP_RECORDING_DIRECTORY_MODE",
    "HOME_ASSISTANT_APP_RUNTIME_DIRECTORY_MODE",
    "HOME_ASSISTANT_APP_SUPERVISOR_POLL_INTERVAL",
    "HOME_ASSISTANT_APP_WEB_STOP_TIMEOUT",
    "HomeAssistantAppChildProcess",
    "HomeAssistantAppLaunchPlan",
    "HomeAssistantAppSignalController",
    "HomeAssistantAppSupervisor",
    "prepare_home_assistant_app_launch_plan",
    "run_home_assistant_app",
]
