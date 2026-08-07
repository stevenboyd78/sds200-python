from __future__ import annotations

import errno
import os
import socket as socket_module
import threading
from contextlib import suppress
from math import isfinite

from .daemon_ipc import DaemonSocketLocation
from .daemon_recording_file_protocol import (
    RECORDING_FILE_DEFAULT_MAX_IDENTIFIER_BYTES,
    RECORDING_FILE_RESPONSE_HEADER_BYTES,
    RecordingFileProtocolError,
    RecordingFileResponseStatus,
    decode_recording_file_response,
    encode_recording_file_request,
)
from .exceptions import (
    DaemonDisconnectedError,
    DaemonProtocolError,
    DaemonUnavailableError,
)

DAEMON_RECORDING_FILE_CLIENT_DEFAULT_TIMEOUT = 5.0
DAEMON_RECORDING_FILE_CLIENT_DEFAULT_MAX_CONTENT_BYTES = 8 * 1024 * 1024 * 1024


class DaemonRecordingFileRequestError(RuntimeError):
    """One stable failed recording-file response from the local daemon."""

    def __init__(self, status: RecordingFileResponseStatus) -> None:
        if not isinstance(status, RecordingFileResponseStatus):
            raise TypeError(
                "Recording-file request status must be a "
                "RecordingFileResponseStatus."
            )
        if status is RecordingFileResponseStatus.OK:
            raise ValueError(
                "Successful recording-file responses are not request errors."
            )
        self.status = status
        super().__init__(_status_message(status))


class DaemonRecordingFileDownload:
    """One exact-length recording-file response body."""

    def __init__(
        self,
        identifier: str,
        content_length: int,
        client: socket_module.socket,
    ) -> None:
        self.identifier = identifier
        self.content_length = content_length
        self._client = client
        self._remaining = content_length
        self._lock = threading.Lock()
        self._closed = False
        self._verified = False

    @property
    def remaining(self) -> int:
        with self._lock:
            return self._remaining

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def read(self, size: int = -1) -> bytes:
        if isinstance(size, bool) or not isinstance(size, int):
            raise TypeError("Recording-file read size must be an integer.")

        with self._lock:
            if self._closed:
                raise ValueError("Recording-file download is closed.")
            if size == 0:
                return b""

            target = (
                self._remaining if size < 0 else min(size, self._remaining)
            )
            payload = bytearray()
            while len(payload) < target:
                try:
                    chunk = self._client.recv(target - len(payload))
                except OSError as error:
                    self._close_locked()
                    raise DaemonDisconnectedError(
                        "The daemon recording-file response disconnected "
                        "while receiving content."
                    ) from error
                if not chunk:
                    self._close_locked()
                    raise DaemonProtocolError(
                        "The daemon recording-file response was truncated."
                    )
                payload.extend(chunk)

            self._remaining -= len(payload)
            if self._remaining == 0:
                self._verify_complete_locked()
            return bytes(payload)

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def __enter__(self) -> DaemonRecordingFileDownload:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception_type, exception, traceback
        self.close()

    def _verify_complete_locked(self) -> None:
        if self._verified:
            return
        try:
            extra = self._client.recv(1)
        except OSError as error:
            self._close_locked()
            raise DaemonProtocolError(
                "The daemon recording-file response did not terminate "
                "after its declared content."
            ) from error

        if extra:
            self._close_locked()
            raise DaemonProtocolError(
                "The daemon recording-file response exceeded its declared "
                "content length."
            )

        self._verified = True
        self._close_locked()

    def _close_locked(self) -> None:
        if self._closed:
            return
        self._closed = True
        _close_socket(self._client)


class DaemonRecordingFileClient:
    """Fetch one finalized recording per private Unix-domain connection."""

    def __init__(
        self,
        location: DaemonSocketLocation,
        *,
        timeout: float = DAEMON_RECORDING_FILE_CLIENT_DEFAULT_TIMEOUT,
        max_identifier_bytes: int = RECORDING_FILE_DEFAULT_MAX_IDENTIFIER_BYTES,
        max_content_bytes: int = (
            DAEMON_RECORDING_FILE_CLIENT_DEFAULT_MAX_CONTENT_BYTES
        ),
    ) -> None:
        if not isinstance(location, DaemonSocketLocation):
            raise TypeError(
                "Daemon recording-file client location must be a "
                "DaemonSocketLocation."
            )
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise TypeError(
                "Daemon recording-file connect timeout must be a number."
            )
        normalized_timeout = float(timeout)
        if not isfinite(normalized_timeout) or normalized_timeout <= 0:
            raise ValueError(
                "Daemon recording-file connect timeout must be finite and "
                "greater than zero."
            )
        _require_positive_integer(
            max_identifier_bytes,
            label="Maximum daemon recording-file identifier size",
        )
        _require_positive_integer(
            max_content_bytes,
            label="Maximum daemon recording-file content size",
        )

        self.location = location
        self.timeout = normalized_timeout
        self.max_identifier_bytes = max_identifier_bytes
        self.max_content_bytes = max_content_bytes

    def open(self, identifier: str) -> DaemonRecordingFileDownload:
        request = encode_recording_file_request(
            identifier,
            max_identifier_bytes=self.max_identifier_bytes,
        )

        client = self._connect()
        try:
            client.sendall(request)
            header = _receive_exact(
                client,
                RECORDING_FILE_RESPONSE_HEADER_BYTES,
            )
            try:
                status, content_length = decode_recording_file_response(
                    header
                )
            except RecordingFileProtocolError as error:
                raise DaemonProtocolError(
                    "Invalid daemon recording-file response header."
                ) from error

            if status is not RecordingFileResponseStatus.OK:
                raise DaemonRecordingFileRequestError(status)
            if content_length > self.max_content_bytes:
                raise DaemonProtocolError(
                    "The daemon recording-file response exceeds the maximum "
                    "accepted content size."
                )

            return DaemonRecordingFileDownload(
                identifier,
                content_length,
                client,
            )
        except BaseException:
            _close_socket(client)
            raise

    def _connect(self) -> socket_module.socket:
        client = socket_module.socket(
            socket_module.AF_UNIX,
            socket_module.SOCK_STREAM,
        )
        client.settimeout(self.timeout)
        try:
            client.connect(os.fspath(self.location.path))
        except OSError as error:
            _close_socket(client)
            self._raise_connect_error(error)
        return client

    def _raise_connect_error(self, error: OSError) -> None:
        path = self.location.path
        if error.errno == errno.ENOENT:
            raise DaemonUnavailableError(
                f"Daemon recording-file socket was not found: {path}"
            ) from error
        if error.errno == errno.ECONNREFUSED:
            raise DaemonUnavailableError(
                "Daemon recording-file socket is present but not accepting "
                f"connections: {path}"
            ) from error
        if error.errno in {errno.EACCES, errno.EPERM}:
            raise DaemonUnavailableError(
                "Permission denied while connecting to daemon recording-file "
                f"socket: {path}"
            ) from error
        if isinstance(error, TimeoutError):
            raise DaemonUnavailableError(
                f"Timed out connecting to daemon recording-file socket: {path}"
            ) from error

        detail = error.strerror or error.__class__.__name__
        raise DaemonUnavailableError(
            "Could not connect to daemon recording-file socket "
            f"{path}: {detail}"
        ) from error


def _status_message(status: RecordingFileResponseStatus) -> str:
    messages = {
        RecordingFileResponseStatus.INVALID_IDENTIFIER: (
            "The daemon rejected the recording identifier."
        ),
        RecordingFileResponseStatus.NOT_FOUND: (
            "The requested daemon recording was not found."
        ),
        RecordingFileResponseStatus.NOT_PLAYABLE: (
            "The requested daemon recording is not playable."
        ),
        RecordingFileResponseStatus.UNAVAILABLE: (
            "The requested daemon recording is not currently available."
        ),
        RecordingFileResponseStatus.FAILED: (
            "The daemon could not provide the requested recording."
        ),
    }
    return messages[status]


def _receive_exact(client: socket_module.socket, size: int) -> bytes:
    payload = bytearray()
    while len(payload) < size:
        try:
            chunk = client.recv(size - len(payload))
        except OSError as error:
            raise DaemonDisconnectedError(
                "The daemon recording-file response disconnected."
            ) from error
        if not chunk:
            if not payload:
                raise DaemonDisconnectedError(
                    "The daemon recording-file response disconnected."
                )
            raise DaemonProtocolError(
                "The daemon recording-file response header was truncated."
            )
        payload.extend(chunk)
    return bytes(payload)


def _require_positive_integer(value: object, *, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer.")
    if value <= 0:
        raise ValueError(f"{label} must be greater than zero.")


def _close_socket(client: socket_module.socket) -> None:
    with suppress(OSError):
        client.shutdown(socket_module.SHUT_RDWR)
    with suppress(OSError):
        client.close()
