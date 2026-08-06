from __future__ import annotations

import logging
import signal
import threading
from dataclasses import dataclass
from types import FrameType
from typing import Any, Protocol, Self, cast

from .daemon_destination_reload import DaemonDestinationReloadResult

logger = logging.getLogger(__name__)


class _DaemonRuntimeLike(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...


class _DaemonDestinationCoordinatorLike(Protocol):
    def start(self) -> object: ...

    def stop(self) -> None: ...


class _DaemonDestinationReloaderLike(Protocol):
    def reload(self) -> DaemonDestinationReloadResult: ...


class _DaemonApiServerLike(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...


class _DaemonEventServerLike(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...


class _DaemonPcmuServerLike(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...


class _DaemonSignalControllerLike(Protocol):
    @property
    def last_signal(self) -> int | None: ...

    @property
    def stop_requested(self) -> bool: ...

    def consume_reload_request(self) -> bool: ...

    def wait(self, timeout: float | None = None) -> bool: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class DaemonProcessResult:
    """Immutable result from one foreground daemon-process run."""

    last_signal: int | None


class DaemonSignalController:
    """Translate stop and reload signals into process-loop wake-ups."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._previous: dict[int, object] = {}
        self._active = False
        self._last_signal: int | None = None
        self._stop_requested = False
        self._reload_requested = False

    @property
    def last_signal(self) -> int | None:
        return self._last_signal

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested

    def request_stop(self) -> None:
        self._stop_requested = True
        self._event.set()

    def request_reload(self) -> None:
        if self._stop_requested:
            return
        self._reload_requested = True
        self._event.set()

    def consume_reload_request(self) -> bool:
        if not self._reload_requested:
            return False
        self._reload_requested = False
        return True

    def wait(self, timeout: float | None = None) -> bool:
        triggered = self._event.wait(timeout)
        if triggered:
            self._event.clear()
        return triggered

    def __enter__(self) -> DaemonSignalController:
        if self._active:
            raise RuntimeError("Daemon signal controller is already active.")

        self._event.clear()
        self._last_signal = None
        self._stop_requested = False
        self._reload_requested = False
        installed: list[int] = []

        try:
            for signum in _daemon_managed_signals():
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
                    "daemon signal rollback failed installation_error=%s "
                    "rollback_error=%s",
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
                "daemon signal restoration failed process_error=%s "
                "restoration_error=%s",
                exception.__class__.__name__,
                restoration_failures[0].__class__.__name__,
            )
            return

        raise restoration_failures[0]

    def _handle(self, signum: int, frame: FrameType | None) -> None:
        del frame

        if signum == _daemon_reload_signal():
            self.request_reload()
            return

        self._last_signal = signum
        self.request_stop()


class DaemonProcess:
    """Host one runtime, local services, and destination reload behavior."""

    def __init__(
        self,
        runtime: _DaemonRuntimeLike,
        *,
        destination_coordinator: (
            _DaemonDestinationCoordinatorLike | None
        ) = None,
        destination_reloader: (
            _DaemonDestinationReloaderLike | None
        ) = None,
        api_server: _DaemonApiServerLike | None = None,
        event_server: _DaemonEventServerLike | None = None,
        pcmu_server: _DaemonPcmuServerLike | None = None,
        signals: _DaemonSignalControllerLike | None = None,
        poll_interval: float = 0.1,
    ) -> None:
        if poll_interval <= 0:
            raise ValueError(
                "Daemon process poll interval must be greater than zero."
            )
        if (
            destination_reloader is not None
            and destination_coordinator is None
        ):
            raise ValueError(
                "Daemon destination reload requires a destination "
                "coordinator."
            )

        self.runtime = runtime
        self.destination_coordinator = destination_coordinator
        self.destination_reloader = destination_reloader
        self.api_server = api_server
        self.event_server = event_server
        self.pcmu_server = pcmu_server
        self.signals = signals or DaemonSignalController()
        self.poll_interval = poll_interval

    def run(self) -> DaemonProcessResult:
        with self.signals:
            event_server_attempted = False
            pcmu_server_attempted = False
            runtime_attempted = False
            destination_coordinator_attempted = False
            api_server_attempted = False

            try:
                if self.event_server is not None:
                    event_server_attempted = True
                    self.event_server.start()

                if self.pcmu_server is not None:
                    pcmu_server_attempted = True
                    self.pcmu_server.start()

                runtime_attempted = True
                self.runtime.start()

                if self.destination_coordinator is not None:
                    destination_coordinator_attempted = True
                    self.destination_coordinator.start()

                if self.api_server is not None:
                    api_server_attempted = True
                    self.api_server.start()

                while True:
                    self.signals.wait(self.poll_interval)

                    if self.signals.stop_requested:
                        break

                    if self.signals.consume_reload_request():
                        self._reload_destinations()
            except BaseException as process_error:
                cleanup_failures = self._stop_components(
                    stop_api_server=api_server_attempted,
                    stop_destination_coordinator=(
                        destination_coordinator_attempted
                    ),
                    stop_runtime=runtime_attempted,
                    stop_pcmu_server=pcmu_server_attempted,
                    stop_event_server=event_server_attempted,
                )
                if cleanup_failures:
                    logger.error(
                        "daemon process cleanup failed process_error=%s "
                        "cleanup_error=%s",
                        process_error.__class__.__name__,
                        cleanup_failures[0].__class__.__name__,
                    )
                raise
            else:
                cleanup_failures = self._stop_components(
                    stop_api_server=api_server_attempted,
                    stop_destination_coordinator=(
                        destination_coordinator_attempted
                    ),
                    stop_runtime=runtime_attempted,
                    stop_pcmu_server=pcmu_server_attempted,
                    stop_event_server=event_server_attempted,
                )
                if cleanup_failures:
                    if len(cleanup_failures) > 1:
                        logger.error(
                            "daemon process cleanup encountered multiple "
                            "failures primary_error=%s cleanup_error=%s",
                            cleanup_failures[0].__class__.__name__,
                            cleanup_failures[1].__class__.__name__,
                        )
                    raise cleanup_failures[0]

        return DaemonProcessResult(last_signal=self.signals.last_signal)

    def _reload_destinations(self) -> None:
        if self.destination_reloader is None:
            logger.warning(
                "daemon destination reload requested without a configured "
                "reloader"
            )
            return

        try:
            result = self.destination_reloader.reload()
        except Exception as error:
            logger.error(
                "daemon destination reload failed error=%s",
                error.__class__.__name__,
            )
            return

        if result.clean:
            logger.info(
                "daemon destination reload completed changed=%s",
                result.changed,
            )
            return

        logger.warning(
            "daemon destination reload committed with cleanup failures "
            "cleanup_errors=%s",
            ",".join(
                failure.error_type
                for failure in result.cleanup_failures
            ),
        )

    def _stop_components(
        self,
        *,
        stop_api_server: bool,
        stop_destination_coordinator: bool,
        stop_runtime: bool,
        stop_pcmu_server: bool,
        stop_event_server: bool,
    ) -> list[BaseException]:
        failures: list[BaseException] = []

        if stop_api_server and self.api_server is not None:
            try:
                self.api_server.stop()
            except BaseException as error:
                failures.append(error)

        if (
            stop_destination_coordinator
            and self.destination_coordinator is not None
        ):
            try:
                self.destination_coordinator.stop()
            except BaseException as error:
                failures.append(error)

        if stop_runtime:
            try:
                self.runtime.stop()
            except BaseException as error:
                failures.append(error)

        if stop_pcmu_server and self.pcmu_server is not None:
            try:
                self.pcmu_server.stop()
            except BaseException as error:
                failures.append(error)

        if stop_event_server and self.event_server is not None:
            try:
                self.event_server.stop()
            except BaseException as error:
                failures.append(error)

        return failures


def _daemon_reload_signal() -> int | None:
    value = getattr(signal, "SIGHUP", None)
    return int(value) if isinstance(value, int) else None


def _daemon_stop_signals() -> tuple[int, ...]:
    signals: list[int] = []
    for name in ("SIGINT", "SIGTERM"):
        value = getattr(signal, name, None)
        if isinstance(value, int) and value not in signals:
            signals.append(int(value))
    return tuple(signals)


def _daemon_managed_signals() -> tuple[int, ...]:
    signals = list(_daemon_stop_signals())
    reload_signal = _daemon_reload_signal()
    if reload_signal is not None and reload_signal not in signals:
        signals.append(reload_signal)
    return tuple(signals)
