from __future__ import annotations

import logging
import socket as socket_module
import struct
import threading
from contextlib import suppress
from dataclasses import dataclass
from math import isfinite
from time import monotonic
from typing import Protocol

from .daemon_ipc import DaemonSocketListener
from .daemon_recording import (
    DaemonRecordingFile,
    DaemonRecordingFileNotFoundError,
    DaemonRecordingFileNotPlayableError,
    DaemonRecordingFileUnavailableError,
    DaemonRecordingIdentifierError,
    DaemonRecordingOperationError,
)
from .daemon_recording_file_protocol import (
    RECORDING_FILE_DEFAULT_MAX_IDENTIFIER_BYTES,
    RECORDING_FILE_MAGIC,
    RECORDING_FILE_REQUEST_HEADER_BYTES,
    RECORDING_FILE_SUPPORTED_VERSIONS,
    RecordingFileProtocolError,
    RecordingFileResponseStatus,
    decode_recording_file_request,
    encode_recording_file_response,
)
from .exceptions import DaemonIpcError

logger = logging.getLogger(__name__)

DAEMON_RECORDING_FILE_DEFAULT_MAX_CLIENTS = 8
DAEMON_RECORDING_FILE_DEFAULT_CLIENT_TIMEOUT = 5.0
DAEMON_RECORDING_FILE_DEFAULT_ACCEPT_POLL_INTERVAL = 0.1
DAEMON_RECORDING_FILE_DEFAULT_SHUTDOWN_TIMEOUT = 2.0
DAEMON_RECORDING_FILE_DEFAULT_CHUNK_BYTES = 64 * 1024

_REQUEST_HEADER = struct.Struct("!4sBBHI")


class _RecordingManagerLike(Protocol):
    def open_recording(self, identifier: str) -> DaemonRecordingFile: ...


@dataclass(frozen=True, slots=True)
class DaemonRecordingFileServerSnapshot:
    """Immutable bounded recording-file server activity state."""

    active: bool
    connected_clients: int
    max_clients: int
    max_identifier_bytes: int
    accepted_clients: int
    rejected_clients: int
    completed_requests: int
    content_bytes_sent: int
    last_error: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "active": self.active,
            "connected_clients": self.connected_clients,
            "max_clients": self.max_clients,
            "max_identifier_bytes": self.max_identifier_bytes,
            "accepted_clients": self.accepted_clients,
            "rejected_clients": self.rejected_clients,
            "completed_requests": self.completed_requests,
            "content_bytes_sent": self.content_bytes_sent,
            "last_error": self.last_error,
        }


class DaemonRecordingFileServer:
    """Serve finalized daemon recordings over one-request Unix connections."""

    def __init__(
        self,
        listener: DaemonSocketListener,
        recording_manager: _RecordingManagerLike,
        *,
        max_clients: int = DAEMON_RECORDING_FILE_DEFAULT_MAX_CLIENTS,
        max_identifier_bytes: int = RECORDING_FILE_DEFAULT_MAX_IDENTIFIER_BYTES,
        client_timeout: float = DAEMON_RECORDING_FILE_DEFAULT_CLIENT_TIMEOUT,
        accept_poll_interval: float = (
            DAEMON_RECORDING_FILE_DEFAULT_ACCEPT_POLL_INTERVAL
        ),
        shutdown_timeout: float = (
            DAEMON_RECORDING_FILE_DEFAULT_SHUTDOWN_TIMEOUT
        ),
        chunk_bytes: int = DAEMON_RECORDING_FILE_DEFAULT_CHUNK_BYTES,
    ) -> None:
        _require_positive_integer(
            max_clients,
            label="Maximum daemon recording-file clients",
        )
        _require_positive_integer(
            max_identifier_bytes,
            label="Maximum daemon recording-file identifier size",
        )
        _require_positive_integer(
            chunk_bytes,
            label="Daemon recording-file chunk size",
        )

        self.listener = listener
        self.recording_manager = recording_manager
        self.max_clients = max_clients
        self.max_identifier_bytes = max_identifier_bytes
        self.client_timeout = _require_positive_number(
            client_timeout,
            label="Daemon recording-file client timeout",
        )
        self.accept_poll_interval = _require_positive_number(
            accept_poll_interval,
            label="Daemon recording-file accept poll interval",
        )
        self.shutdown_timeout = _require_positive_number(
            shutdown_timeout,
            label="Daemon recording-file shutdown timeout",
        )
        self.chunk_bytes = chunk_bytes

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
        self._completed_requests = 0
        self._content_bytes_sent = 0
        self._last_error: str | None = None

    @property
    def active(self) -> bool:
        with self._state_lock:
            return self._active

    @property
    def connected_clients(self) -> int:
        with self._state_lock:
            return len(self._clients)

    def snapshot(self) -> DaemonRecordingFileServerSnapshot:
        with self._state_lock:
            return DaemonRecordingFileServerSnapshot(
                active=self._active,
                connected_clients=len(self._clients),
                max_clients=self.max_clients,
                max_identifier_bytes=self.max_identifier_bytes,
                accepted_clients=self._accepted_clients,
                rejected_clients=self._rejected_clients,
                completed_requests=self._completed_requests,
                content_bytes_sent=self._content_bytes_sent,
                last_error=self._last_error,
            )

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._stopped:
                raise RuntimeError(
                    "Daemon recording-file servers cannot be restarted "
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
                    name="daemon-recording-file-accept",
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
                        "daemon recording-file startup cleanup failed "
                        "startup_error=%s cleanup_error=%s",
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
                    "Daemon recording-file workers did not stop before the "
                    f"shutdown deadline: {names}"
                )

    def __enter__(self) -> DaemonRecordingFileServer:
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

    def _accept_loop(self, listener_socket: socket_module.socket) -> None:
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
                    name=f"daemon-recording-file-client-{sequence}",
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
        try:
            try:
                identifier = self._receive_request(client)
            except _ClientDisconnected:
                return
            except RecordingFileProtocolError:
                self._send_status(
                    client,
                    RecordingFileResponseStatus.INVALID_IDENTIFIER,
                )
                return
            except OSError:
                return

            try:
                recording = self.recording_manager.open_recording(identifier)
            except DaemonRecordingIdentifierError:
                self._send_status(
                    client,
                    RecordingFileResponseStatus.INVALID_IDENTIFIER,
                )
                return
            except DaemonRecordingFileNotFoundError:
                self._send_status(client, RecordingFileResponseStatus.NOT_FOUND)
                return
            except DaemonRecordingFileNotPlayableError:
                self._send_status(
                    client,
                    RecordingFileResponseStatus.NOT_PLAYABLE,
                )
                return
            except DaemonRecordingFileUnavailableError:
                self._send_status(
                    client,
                    RecordingFileResponseStatus.UNAVAILABLE,
                )
                return
            except DaemonRecordingOperationError:
                self._send_status(client, RecordingFileResponseStatus.FAILED)
                return
            except Exception as error:
                if not self._stop_event.is_set():
                    self._record_error(error)
                self._send_status(client, RecordingFileResponseStatus.FAILED)
                return

            with recording:
                client.sendall(
                    encode_recording_file_response(
                        RecordingFileResponseStatus.OK,
                        content_length=recording.size_bytes,
                    )
                )
                remaining = recording.size_bytes
                sent = 0
                while remaining:
                    payload = recording.stream.read(
                        min(self.chunk_bytes, remaining)
                    )
                    if not payload:
                        raise DaemonIpcError(
                            "Daemon recording changed while being served."
                        )
                    if len(payload) > remaining:
                        raise DaemonIpcError(
                            "Daemon recording exceeded its declared size."
                        )
                    client.sendall(payload)
                    sent += len(payload)
                    remaining -= len(payload)

            with self._state_lock:
                self._completed_requests += 1
                self._content_bytes_sent += sent
        except OSError:
            return
        except Exception as error:
            if not self._stop_event.is_set():
                self._record_error(error)
        finally:
            with self._state_lock:
                self._clients.pop(client, None)
            _close_client(client)

    def _receive_request(self, client: socket_module.socket) -> str:
        header = _receive_exact(
            client,
            RECORDING_FILE_REQUEST_HEADER_BYTES,
            clean_eof=True,
        )
        magic, version, flags, header_size, identifier_size = (
            _REQUEST_HEADER.unpack(header)
        )

        if magic != RECORDING_FILE_MAGIC:
            raise RecordingFileProtocolError(
                "Recording-file request magic is invalid."
            )
        if version not in RECORDING_FILE_SUPPORTED_VERSIONS:
            raise RecordingFileProtocolError(
                "Recording-file request version is unsupported."
            )
        if flags != 0:
            raise RecordingFileProtocolError(
                "Recording-file request flags are unsupported."
            )
        if header_size != RECORDING_FILE_REQUEST_HEADER_BYTES:
            raise RecordingFileProtocolError(
                "Recording-file request header size is invalid."
            )
        if identifier_size == 0:
            raise RecordingFileProtocolError(
                "Recording-file request identifier is empty."
            )
        if identifier_size > self.max_identifier_bytes:
            raise RecordingFileProtocolError(
                "Recording-file request identifier is oversized."
            )

        identifier = _receive_exact(
            client,
            identifier_size,
            clean_eof=False,
        )
        return decode_recording_file_request(
            header + identifier,
            max_identifier_bytes=self.max_identifier_bytes,
        )

    def _send_status(
        self,
        client: socket_module.socket,
        status: RecordingFileResponseStatus,
    ) -> None:
        try:
            client.sendall(encode_recording_file_response(status))
        except OSError:
            return

    def _record_error(self, error: BaseException) -> None:
        with self._state_lock:
            self._last_error = error.__class__.__name__


class _ClientDisconnected(Exception):
    pass


def _receive_exact(
    client: socket_module.socket,
    size: int,
    *,
    clean_eof: bool,
) -> bytes:
    payload = bytearray()
    while len(payload) < size:
        chunk = client.recv(size - len(payload))
        if not chunk:
            if clean_eof and not payload:
                raise _ClientDisconnected
            raise RecordingFileProtocolError(
                "Recording-file request was truncated."
            )
        payload.extend(chunk)
    return bytes(payload)


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


def _close_client(client: socket_module.socket) -> None:
    with suppress(OSError):
        client.shutdown(socket_module.SHUT_RDWR)
    with suppress(OSError):
        client.close()
