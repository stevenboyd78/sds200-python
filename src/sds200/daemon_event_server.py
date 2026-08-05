from __future__ import annotations

import logging
import queue
import socket as socket_module
import threading
from contextlib import suppress
from dataclasses import dataclass
from math import isfinite
from time import monotonic
from typing import Protocol

from .daemon_events import (
    DaemonEventSubscription,
    DaemonEventSubscriptionClosed,
)
from .daemon_ipc import DaemonSocketListener
from .exceptions import DaemonIpcError

logger = logging.getLogger(__name__)

DAEMON_EVENT_DEFAULT_MAX_CLIENTS = 8
DAEMON_EVENT_DEFAULT_SEND_TIMEOUT = 5.0
DAEMON_EVENT_DEFAULT_ACCEPT_POLL_INTERVAL = 0.1
DAEMON_EVENT_DEFAULT_SHUTDOWN_TIMEOUT = 2.0


class _DaemonEventStreamLike(Protocol):
    def subscribe(self) -> DaemonEventSubscription: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class DaemonEventServerSnapshot:
    """Immutable bounded event-server activity and capacity state."""

    active: bool
    connected_clients: int
    max_clients: int
    accepted_clients: int
    rejected_clients: int
    events_sent: int
    last_error: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "active": self.active,
            "connected_clients": self.connected_clients,
            "max_clients": self.max_clients,
            "accepted_clients": self.accepted_clients,
            "rejected_clients": self.rejected_clients,
            "events_sent": self.events_sent,
            "last_error": self.last_error,
        }


class DaemonEventServer:
    """Publish one bounded daemon event subscription per Unix client."""

    def __init__(
        self,
        listener: DaemonSocketListener,
        stream: _DaemonEventStreamLike,
        *,
        max_clients: int = DAEMON_EVENT_DEFAULT_MAX_CLIENTS,
        send_timeout: float = DAEMON_EVENT_DEFAULT_SEND_TIMEOUT,
        accept_poll_interval: float = (
            DAEMON_EVENT_DEFAULT_ACCEPT_POLL_INTERVAL
        ),
        shutdown_timeout: float = DAEMON_EVENT_DEFAULT_SHUTDOWN_TIMEOUT,
    ) -> None:
        _require_positive_integer(
            max_clients,
            label="Maximum daemon event clients",
        )

        self.listener = listener
        self.stream = stream
        self.max_clients = max_clients
        self.send_timeout = _require_positive_number(
            send_timeout,
            label="Daemon event send timeout",
        )
        self.accept_poll_interval = _require_positive_number(
            accept_poll_interval,
            label="Daemon event accept poll interval",
        )
        self.shutdown_timeout = _require_positive_number(
            shutdown_timeout,
            label="Daemon event shutdown timeout",
        )

        self._lifecycle_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._accept_thread: threading.Thread | None = None
        self._clients: dict[
            socket_module.socket,
            threading.Thread,
        ] = {}
        self._started = False
        self._stopped = False
        self._active = False
        self._accepted_clients = 0
        self._rejected_clients = 0
        self._events_sent = 0
        self._last_error: str | None = None

    @property
    def active(self) -> bool:
        with self._state_lock:
            return self._active

    @property
    def connected_clients(self) -> int:
        with self._state_lock:
            return len(self._clients)

    def snapshot(self) -> DaemonEventServerSnapshot:
        with self._state_lock:
            return DaemonEventServerSnapshot(
                active=self._active,
                connected_clients=len(self._clients),
                max_clients=self.max_clients,
                accepted_clients=self._accepted_clients,
                rejected_clients=self._rejected_clients,
                events_sent=self._events_sent,
                last_error=self._last_error,
            )

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._stopped:
                raise RuntimeError(
                    "Daemon event servers cannot be restarted "
                    "after shutdown."
                )
            if self._started:
                return

            self._started = True
            self._stop_event.clear()
            accept_thread: threading.Thread | None = None

            try:
                listener_socket = self.listener.start()
                listener_socket.settimeout(self.accept_poll_interval)
                accept_thread = threading.Thread(
                    target=self._accept_loop,
                    args=(listener_socket,),
                    name="daemon-event-accept",
                    daemon=True,
                )
                self._accept_thread = accept_thread
                with self._state_lock:
                    self._active = True
                accept_thread.start()
            except BaseException as startup_error:
                self._accept_thread = None
                self._stop_event.set()
                self._stopped = True
                with self._state_lock:
                    self._active = False
                self._record_error(startup_error)

                cleanup_errors: list[tuple[str, BaseException]] = []
                for component, cleanup in (
                    ("listener", self.listener.stop),
                    ("stream", self.stream.close),
                ):
                    try:
                        cleanup()
                    except BaseException as cleanup_error:
                        cleanup_errors.append((component, cleanup_error))

                if cleanup_errors:
                    details = ",".join(
                        f"{component}:{error.__class__.__name__}"
                        for component, error in cleanup_errors
                    )
                    logger.error(
                        "daemon event startup cleanup failed "
                        "startup_error=%s cleanup_errors=%s",
                        startup_error.__class__.__name__,
                        details,
                    )
                raise

    def stop(self) -> None:
        with self._lifecycle_lock:
            if self._stopped:
                return

            self._stop_event.set()
            failures: list[BaseException] = []

            if self._started:
                try:
                    self.listener.stop()
                except BaseException as error:
                    failures.append(error)

            try:
                self.stream.close()
            except BaseException as error:
                failures.append(error)

            with self._state_lock:
                clients = tuple(self._clients)
                workers = tuple(self._clients.values())
                accept_thread = self._accept_thread

            for client in clients:
                _close_client(client)

            deadline = monotonic() + self.shutdown_timeout
            threads = tuple(
                thread
                for thread in (accept_thread, *workers)
                if thread is not None
                and thread is not threading.current_thread()
            )
            for thread in threads:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    break
                thread.join(remaining)

            alive = tuple(
                thread.name
                for thread in threads
                if thread.is_alive()
            )

            with self._state_lock:
                self._active = False
                for client in tuple(self._clients):
                    _close_client(client)

            self._stopped = True

            if failures:
                raise failures[0]
            if alive:
                names = ", ".join(alive)
                raise DaemonIpcError(
                    "Daemon event workers did not stop before the "
                    f"shutdown deadline: {names}"
                )

    def __enter__(self) -> DaemonEventServer:
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

    def _accept_loop(
        self,
        listener_socket: socket_module.socket,
    ) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    client, _ = listener_socket.accept()
                except TimeoutError:
                    continue
                except OSError as error:
                    if self._stop_event.is_set():
                        return
                    self._record_error(error)
                    return

                self._admit_client(client)
        finally:
            with self._state_lock:
                self._active = False

    def _admit_client(self, client: socket_module.socket) -> None:
        try:
            client.settimeout(self.send_timeout)
        except OSError as error:
            _close_client(client)
            self._record_error(error)
            return

        start_error: BaseException | None = None
        with self._state_lock:
            if self._stop_event.is_set():
                worker = None
            elif len(self._clients) >= self.max_clients:
                self._rejected_clients += 1
                worker = None
            else:
                sequence = self._accepted_clients + 1
                worker = threading.Thread(
                    target=self._serve_client,
                    args=(client,),
                    name=f"daemon-event-client-{sequence}",
                    daemon=True,
                )
                self._clients[client] = worker
                try:
                    worker.start()
                except BaseException as error:
                    self._clients.pop(client, None)
                    start_error = error
                else:
                    self._accepted_clients = sequence

        if worker is None or start_error is not None:
            _close_client(client)
        if start_error is not None:
            self._record_error(start_error)

    def _serve_client(self, client: socket_module.socket) -> None:
        subscription: DaemonEventSubscription | None = None

        try:
            subscription = self.stream.subscribe()

            while not self._stop_event.is_set():
                try:
                    event = subscription.get(
                        timeout=self.accept_poll_interval,
                    )
                except queue.Empty:
                    continue
                except DaemonEventSubscriptionClosed:
                    return

                client.sendall(event.to_json_line())
                with self._state_lock:
                    self._events_sent += 1
        except OSError:
            return
        except DaemonEventSubscriptionClosed:
            return
        except Exception as error:
            if not self._stop_event.is_set():
                self._record_error(error)
        finally:
            if subscription is not None:
                subscription.close()
            with self._state_lock:
                self._clients.pop(client, None)
            _close_client(client)

    def _record_error(self, error: BaseException) -> None:
        with self._state_lock:
            self._last_error = error.__class__.__name__


def _require_positive_integer(value: int, *, label: str) -> None:
    if type(value) is not int:
        raise TypeError(f"{label} must be an integer.")
    if value <= 0:
        raise ValueError(f"{label} must be greater than zero.")


def _require_positive_number(
    value: float,
    *,
    label: str,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be a number.")

    normalized = float(value)
    if not isfinite(normalized) or normalized <= 0:
        raise ValueError(
            f"{label} must be a finite number greater than zero."
        )
    return normalized


def _close_client(client: socket_module.socket) -> None:
    with suppress(OSError):
        client.shutdown(socket_module.SHUT_RDWR)
    with suppress(OSError):
        client.close()
