from __future__ import annotations

import logging
import socket as socket_module
import threading
from contextlib import suppress
from dataclasses import dataclass
from math import isfinite
from time import monotonic
from typing import Protocol

from .daemon_api import DaemonApiErrorCode, DaemonApiResponse
from .daemon_ipc import DaemonSocketListener
from .exceptions import DaemonIpcError

logger = logging.getLogger(__name__)

DAEMON_API_DEFAULT_MAX_CLIENTS = 8
DAEMON_API_DEFAULT_MAX_REQUEST_BYTES = 64 * 1024
DAEMON_API_DEFAULT_MAX_RESPONSE_BYTES = 1024 * 1024
DAEMON_API_DEFAULT_CLIENT_TIMEOUT = 5.0
DAEMON_API_DEFAULT_ACCEPT_POLL_INTERVAL = 0.1
DAEMON_API_DEFAULT_SHUTDOWN_TIMEOUT = 2.0
_DAEMON_API_MIN_RESPONSE_BYTES = 256
_DAEMON_API_RECV_BYTES = 4096


class _DaemonApiLike(Protocol):
    def handle_json_line(self, data: bytes | str) -> bytes: ...


@dataclass(frozen=True, slots=True)
class DaemonApiServerSnapshot:
    """Immutable bounded-server activity and capacity state."""

    active: bool
    connected_clients: int
    max_clients: int
    max_request_bytes: int
    max_response_bytes: int
    accepted_clients: int
    rejected_clients: int
    requests: int
    responses: int
    oversized_requests: int
    oversized_responses: int
    last_error: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "active": self.active,
            "connected_clients": self.connected_clients,
            "max_clients": self.max_clients,
            "max_request_bytes": self.max_request_bytes,
            "max_response_bytes": self.max_response_bytes,
            "accepted_clients": self.accepted_clients,
            "rejected_clients": self.rejected_clients,
            "requests": self.requests,
            "responses": self.responses,
            "oversized_requests": self.oversized_requests,
            "oversized_responses": self.oversized_responses,
            "last_error": self.last_error,
        }


class DaemonApiServer:
    """Serve bounded local daemon API requests over one owned Unix socket."""

    def __init__(
        self,
        listener: DaemonSocketListener,
        api: _DaemonApiLike,
        *,
        max_clients: int = DAEMON_API_DEFAULT_MAX_CLIENTS,
        max_request_bytes: int = DAEMON_API_DEFAULT_MAX_REQUEST_BYTES,
        max_response_bytes: int = DAEMON_API_DEFAULT_MAX_RESPONSE_BYTES,
        client_timeout: float = DAEMON_API_DEFAULT_CLIENT_TIMEOUT,
        accept_poll_interval: float = DAEMON_API_DEFAULT_ACCEPT_POLL_INTERVAL,
        shutdown_timeout: float = DAEMON_API_DEFAULT_SHUTDOWN_TIMEOUT,
    ) -> None:
        _require_positive_integer(max_clients, label="Maximum daemon API clients")
        _require_positive_integer(
            max_request_bytes,
            label="Maximum daemon API request size",
        )
        _require_positive_integer(
            max_response_bytes,
            label="Maximum daemon API response size",
        )
        if max_response_bytes < _DAEMON_API_MIN_RESPONSE_BYTES:
            raise ValueError(
                "Maximum daemon API response size must be at least "
                f"{_DAEMON_API_MIN_RESPONSE_BYTES} bytes."
            )

        self.listener = listener
        self.api = api
        self.max_clients = max_clients
        self.max_request_bytes = max_request_bytes
        self.max_response_bytes = max_response_bytes
        self.client_timeout = _require_positive_number(
            client_timeout,
            label="Daemon API client timeout",
        )
        self.accept_poll_interval = _require_positive_number(
            accept_poll_interval,
            label="Daemon API accept poll interval",
        )
        self.shutdown_timeout = _require_positive_number(
            shutdown_timeout,
            label="Daemon API shutdown timeout",
        )

        self._lifecycle_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._accept_thread: threading.Thread | None = None
        self._clients: dict[socket_module.socket, threading.Thread] = {}
        self._started = False
        self._stopped = False
        self._active = False
        self._accepted_clients = 0
        self._rejected_clients = 0
        self._requests = 0
        self._responses = 0
        self._oversized_requests = 0
        self._oversized_responses = 0
        self._last_error: str | None = None

    @property
    def active(self) -> bool:
        with self._state_lock:
            return self._active

    @property
    def connected_clients(self) -> int:
        with self._state_lock:
            return len(self._clients)

    def snapshot(self) -> DaemonApiServerSnapshot:
        with self._state_lock:
            return DaemonApiServerSnapshot(
                active=self._active,
                connected_clients=len(self._clients),
                max_clients=self.max_clients,
                max_request_bytes=self.max_request_bytes,
                max_response_bytes=self.max_response_bytes,
                accepted_clients=self._accepted_clients,
                rejected_clients=self._rejected_clients,
                requests=self._requests,
                responses=self._responses,
                oversized_requests=self._oversized_requests,
                oversized_responses=self._oversized_responses,
                last_error=self._last_error,
            )

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._started:
                if self._stopped:
                    raise RuntimeError(
                        "Daemon API servers cannot be restarted after shutdown."
                    )
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
                    name="daemon-api-accept",
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
                try:
                    self.listener.stop()
                except BaseException as cleanup_error:
                    logger.error(
                        "daemon API startup cleanup failed startup_error=%s "
                        "cleanup_error=%s",
                        startup_error.__class__.__name__,
                        cleanup_error.__class__.__name__,
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

            alive = tuple(thread.name for thread in threads if thread.is_alive())

            with self._state_lock:
                self._active = False
            self._stopped = True

            if failures:
                raise failures[0]
            if alive:
                names = ", ".join(alive)
                raise DaemonIpcError(
                    "Daemon API workers did not stop before the shutdown "
                    f"deadline: {names}"
                )

    def __enter__(self) -> DaemonApiServer:
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
            client.settimeout(self.client_timeout)
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
                    name=f"daemon-api-client-{sequence}",
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
        buffer = bytearray()
        receive_size = min(
            _DAEMON_API_RECV_BYTES,
            self.max_request_bytes + 1,
        )

        try:
            while not self._stop_event.is_set():
                try:
                    chunk = client.recv(receive_size)
                except OSError:
                    return

                if not chunk:
                    return

                buffer.extend(chunk)
                while True:
                    newline = buffer.find(b"\n")
                    if newline < 0:
                        if len(buffer) > self.max_request_bytes:
                            self._send_oversized_request(client)
                            return
                        break

                    frame = bytes(buffer[:newline])
                    del buffer[: newline + 1]

                    if len(frame) > self.max_request_bytes:
                        self._send_oversized_request(client)
                        return
                    if not self._dispatch_frame(client, frame):
                        return
        finally:
            _close_client(client)
            with self._state_lock:
                self._clients.pop(client, None)

    def _dispatch_frame(
        self,
        client: socket_module.socket,
        frame: bytes,
    ) -> bool:
        with self._state_lock:
            self._requests += 1

        try:
            response = self.api.handle_json_line(frame)
        except Exception as error:
            self._record_error(error)
            response = DaemonApiResponse.failure(
                None,
                DaemonApiErrorCode.INTERNAL_ERROR,
                "The daemon could not complete the request.",
            ).to_json_line()

        if len(response) > self.max_response_bytes:
            with self._state_lock:
                self._oversized_responses += 1
            response = DaemonApiResponse.failure(
                None,
                DaemonApiErrorCode.INTERNAL_ERROR,
                "The daemon response exceeded the configured size limit.",
            ).to_json_line()

        try:
            client.sendall(response)
        except OSError:
            return False

        with self._state_lock:
            self._responses += 1
        return True

    def _send_oversized_request(
        self,
        client: socket_module.socket,
    ) -> None:
        with self._state_lock:
            self._oversized_requests += 1

        response = DaemonApiResponse.failure(
            None,
            DaemonApiErrorCode.REQUEST_TOO_LARGE,
            "The daemon API request exceeded the configured size limit.",
        ).to_json_line()

        with suppress(OSError):
            client.sendall(response)

    def _record_error(self, error: BaseException) -> None:
        with self._state_lock:
            self._last_error = error.__class__.__name__


def _close_client(client: socket_module.socket) -> None:
    with suppress(OSError):
        client.shutdown(socket_module.SHUT_RDWR)
    with suppress(OSError):
        client.close()


def _require_positive_integer(value: object, *, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer.")
    if value <= 0:
        raise ValueError(f"{label} must be greater than zero.")


def _require_positive_number(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be a number.")
    normalized = float(value)
    if not isfinite(normalized) or normalized <= 0:
        raise ValueError(f"{label} must be finite and greater than zero.")
    return normalized
