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

from .daemon_ipc import DaemonSocketListener
from .exceptions import DaemonIpcError
from .pcmu_protocol import (
    PCMU_STREAM_DEFAULT_MAX_ENDPOINT_BYTES,
    PCMU_STREAM_DEFAULT_MAX_FRAME_BYTES,
    PCMU_STREAM_HEADER_BYTES,
    encode_pcmu_delivery,
)
from .pcmu_subscriptions import (
    PcmuSubscription,
    PcmuSubscriptionClosed,
)

logger = logging.getLogger(__name__)

DAEMON_PCMU_DEFAULT_MAX_CLIENTS = 8
DAEMON_PCMU_DEFAULT_SEND_TIMEOUT = 5.0
DAEMON_PCMU_DEFAULT_ACCEPT_POLL_INTERVAL = 0.1
DAEMON_PCMU_DEFAULT_SHUTDOWN_TIMEOUT = 2.0


class _PcmuStreamLike(Protocol):
    def subscribe(self) -> PcmuSubscription: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class DaemonPcmuServerSnapshot:
    """Immutable bounded PCMU-server activity and capacity state."""

    active: bool
    connected_clients: int
    max_clients: int
    max_endpoint_bytes: int
    max_frame_bytes: int
    accepted_clients: int
    rejected_clients: int
    frames_sent: int
    payload_bytes_sent: int
    last_stream_sequence_sent: int | None
    last_error: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "active": self.active,
            "connected_clients": self.connected_clients,
            "max_clients": self.max_clients,
            "max_endpoint_bytes": self.max_endpoint_bytes,
            "max_frame_bytes": self.max_frame_bytes,
            "accepted_clients": self.accepted_clients,
            "rejected_clients": self.rejected_clients,
            "frames_sent": self.frames_sent,
            "payload_bytes_sent": self.payload_bytes_sent,
            "last_stream_sequence_sent": (
                self.last_stream_sequence_sent
            ),
            "last_error": self.last_error,
        }


class DaemonPcmuServer:
    """Publish one bounded PCMU subscription per private Unix client."""

    def __init__(
        self,
        listener: DaemonSocketListener,
        stream: _PcmuStreamLike,
        *,
        max_clients: int = DAEMON_PCMU_DEFAULT_MAX_CLIENTS,
        max_endpoint_bytes: int = (
            PCMU_STREAM_DEFAULT_MAX_ENDPOINT_BYTES
        ),
        max_frame_bytes: int = PCMU_STREAM_DEFAULT_MAX_FRAME_BYTES,
        send_timeout: float = DAEMON_PCMU_DEFAULT_SEND_TIMEOUT,
        accept_poll_interval: float = (
            DAEMON_PCMU_DEFAULT_ACCEPT_POLL_INTERVAL
        ),
        shutdown_timeout: float = (
            DAEMON_PCMU_DEFAULT_SHUTDOWN_TIMEOUT
        ),
    ) -> None:
        _require_positive_integer(
            max_clients,
            label="Maximum daemon PCMU clients",
        )
        _require_positive_integer(
            max_endpoint_bytes,
            label="Maximum daemon PCMU endpoint size",
        )
        _require_positive_integer(
            max_frame_bytes,
            label="Maximum daemon PCMU frame size",
        )
        if max_frame_bytes < PCMU_STREAM_HEADER_BYTES:
            raise ValueError(
                "Maximum daemon PCMU frame size must be at least "
                f"{PCMU_STREAM_HEADER_BYTES} bytes."
            )

        self.listener = listener
        self.stream = stream
        self.max_clients = max_clients
        self.max_endpoint_bytes = max_endpoint_bytes
        self.max_frame_bytes = max_frame_bytes
        self.send_timeout = _require_positive_number(
            send_timeout,
            label="Daemon PCMU send timeout",
        )
        self.accept_poll_interval = _require_positive_number(
            accept_poll_interval,
            label="Daemon PCMU accept poll interval",
        )
        self.shutdown_timeout = _require_positive_number(
            shutdown_timeout,
            label="Daemon PCMU shutdown timeout",
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
        self._frames_sent = 0
        self._payload_bytes_sent = 0
        self._last_stream_sequence_sent: int | None = None
        self._last_error: str | None = None

    @property
    def active(self) -> bool:
        with self._state_lock:
            return self._active

    @property
    def connected_clients(self) -> int:
        with self._state_lock:
            return len(self._clients)

    def snapshot(self) -> DaemonPcmuServerSnapshot:
        with self._state_lock:
            return DaemonPcmuServerSnapshot(
                active=self._active,
                connected_clients=len(self._clients),
                max_clients=self.max_clients,
                max_endpoint_bytes=self.max_endpoint_bytes,
                max_frame_bytes=self.max_frame_bytes,
                accepted_clients=self._accepted_clients,
                rejected_clients=self._rejected_clients,
                frames_sent=self._frames_sent,
                payload_bytes_sent=self._payload_bytes_sent,
                last_stream_sequence_sent=(
                    self._last_stream_sequence_sent
                ),
                last_error=self._last_error,
            )

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._stopped:
                raise RuntimeError(
                    "Daemon PCMU servers cannot be restarted "
                    "after shutdown."
                )
            if self._started:
                return

            self._started = True
            self._stop_event.clear()

            try:
                listener_socket = self.listener.start()
                listener_socket.settimeout(self.accept_poll_interval)
                accept_thread = threading.Thread(
                    target=self._accept_loop,
                    args=(listener_socket,),
                    name="daemon-pcmu-accept",
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
                        cleanup_errors.append(
                            (component, cleanup_error)
                        )

                if cleanup_errors:
                    details = ",".join(
                        f"{component}:{error.__class__.__name__}"
                        for component, error in cleanup_errors
                    )
                    logger.error(
                        "daemon PCMU startup cleanup failed "
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
                    "Daemon PCMU workers did not stop before the "
                    f"shutdown deadline: {names}"
                )

    def __enter__(self) -> DaemonPcmuServer:
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
                    name=f"daemon-pcmu-client-{sequence}",
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
        subscription: PcmuSubscription | None = None

        try:
            subscription = self.stream.subscribe()

            while not self._stop_event.is_set():
                try:
                    delivery = subscription.get(
                        timeout=self.accept_poll_interval,
                    )
                except queue.Empty:
                    continue
                except PcmuSubscriptionClosed:
                    return

                try:
                    encoded = encode_pcmu_delivery(
                        delivery,
                        max_endpoint_bytes=self.max_endpoint_bytes,
                        max_frame_bytes=self.max_frame_bytes,
                    )
                except (TypeError, ValueError) as error:
                    raise DaemonIpcError(
                        "PCMU delivery could not be encoded within "
                        "the configured limits."
                    ) from error

                client.sendall(encoded)
                with self._state_lock:
                    self._frames_sent += 1
                    self._payload_bytes_sent += len(
                        delivery.packet.payload
                    )
                    self._last_stream_sequence_sent = (
                        delivery.stream_sequence
                    )
        except OSError:
            return
        except PcmuSubscriptionClosed:
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


def _require_positive_integer(
    value: object,
    *,
    label: str,
) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer.")
    if value <= 0:
        raise ValueError(f"{label} must be greater than zero.")


def _require_positive_number(
    value: object,
    *,
    label: str,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be a number.")

    normalized = float(value)
    if not isfinite(normalized) or normalized <= 0:
        raise ValueError(
            f"{label} must be finite and greater than zero."
        )
    return normalized


def _close_client(client: socket_module.socket) -> None:
    with suppress(OSError):
        client.shutdown(socket_module.SHUT_RDWR)
    with suppress(OSError):
        client.close()
