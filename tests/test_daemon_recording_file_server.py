from __future__ import annotations

import io
import socket
import struct
import time
from pathlib import Path

import pytest

from sds200.daemon_ipc import (
    DaemonSocketListener,
    DaemonSocketLocation,
    DaemonSocketSource,
)
from sds200.daemon_recording import (
    DaemonRecordingFile,
    DaemonRecordingFileNotFoundError,
    DaemonRecordingFileNotPlayableError,
    DaemonRecordingFileUnavailableError,
    DaemonRecordingIdentifierError,
    DaemonRecordingOperationError,
)
from sds200.daemon_recording_file_protocol import (
    RECORDING_FILE_MAGIC,
    RECORDING_FILE_REQUEST_HEADER_BYTES,
    RECORDING_FILE_RESPONSE_HEADER_BYTES,
    RecordingFileResponseStatus,
    decode_recording_file_response,
    encode_recording_file_request,
)
from sds200.daemon_recording_file_server import DaemonRecordingFileServer

_REQUEST_HEADER = struct.Struct("!4sBBHI")


class FakeRecordingManager:
    def __init__(
        self,
        files: dict[str, bytes] | None = None,
        errors: dict[str, BaseException] | None = None,
    ) -> None:
        self.files = files or {}
        self.errors = errors or {}
        self.identifiers: list[str] = []
        self.streams: list[io.BytesIO] = []

    def open_recording(self, identifier: str) -> DaemonRecordingFile:
        self.identifiers.append(identifier)
        error = self.errors.get(identifier)
        if error is not None:
            raise error
        payload = self.files[identifier]
        stream = io.BytesIO(payload)
        self.streams.append(stream)
        return DaemonRecordingFile(
            identifier=identifier,
            size_bytes=len(payload),
            stream=stream,
        )


def make_server(
    tmp_path: Path,
    manager: FakeRecordingManager,
    **kwargs: object,
) -> tuple[DaemonRecordingFileServer, Path]:
    path = tmp_path / "recordings.sock"
    listener = DaemonSocketListener(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )
    server = DaemonRecordingFileServer(
        listener,
        manager,
        **kwargs,  # type: ignore[arg-type]
    )
    return server, path


def connect(path: Path) -> socket.socket:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(1.0)
    client.connect(str(path))
    return client


def receive_exact(client: socket.socket, size: int) -> bytes:
    payload = bytearray()
    while len(payload) < size:
        chunk = client.recv(size - len(payload))
        if not chunk:
            break
        payload.extend(chunk)
    return bytes(payload)


def receive_response(
    client: socket.socket,
) -> tuple[RecordingFileResponseStatus, bytes]:
    header = receive_exact(client, RECORDING_FILE_RESPONSE_HEADER_BYTES)
    status, content_length = decode_recording_file_response(header)
    return status, receive_exact(client, content_length)


def wait_until(predicate: object, *, timeout: float = 1.0) -> None:
    assert callable(predicate)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Condition did not become true before timeout")


@pytest.mark.parametrize(
    ("keyword", "value", "error_type"),
    [
        ("max_clients", True, TypeError),
        ("max_clients", 0, ValueError),
        ("max_identifier_bytes", True, TypeError),
        ("max_identifier_bytes", 0, ValueError),
        ("client_timeout", 0, ValueError),
        ("accept_poll_interval", float("inf"), ValueError),
        ("shutdown_timeout", 0, ValueError),
        ("chunk_bytes", 0, ValueError),
    ],
)
def test_recording_file_server_rejects_invalid_limits(
    tmp_path: Path,
    keyword: str,
    value: object,
    error_type: type[Exception],
) -> None:
    with pytest.raises(error_type):
        make_server(
            tmp_path,
            FakeRecordingManager(),
            **{keyword: value},
        )


def test_recording_file_server_streams_exact_opened_file(
    tmp_path: Path,
) -> None:
    payload = b"RIFF" + (b"\x00" * 128)
    manager = FakeRecordingManager({"2026/test.wav": payload})
    server, path = make_server(tmp_path, manager)
    server.start()
    client = connect(path)

    try:
        client.sendall(encode_recording_file_request("2026/test.wav"))
        status, received = receive_response(client)
    finally:
        client.close()
        server.stop()

    assert status is RecordingFileResponseStatus.OK
    assert received == payload
    assert manager.identifiers == ["2026/test.wav"]
    assert manager.streams[0].closed

    snapshot = server.snapshot()
    assert snapshot.accepted_clients == 1
    assert snapshot.completed_requests == 1
    assert snapshot.content_bytes_sent == len(payload)
    assert snapshot.last_error is None
    assert not path.exists()


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (
            DaemonRecordingIdentifierError("secret"),
            RecordingFileResponseStatus.INVALID_IDENTIFIER,
        ),
        (
            DaemonRecordingFileNotFoundError("secret"),
            RecordingFileResponseStatus.NOT_FOUND,
        ),
        (
            DaemonRecordingFileNotPlayableError("secret"),
            RecordingFileResponseStatus.NOT_PLAYABLE,
        ),
        (
            DaemonRecordingFileUnavailableError("secret"),
            RecordingFileResponseStatus.UNAVAILABLE,
        ),
        (
            DaemonRecordingOperationError("secret"),
            RecordingFileResponseStatus.FAILED,
        ),
    ],
)
def test_recording_file_server_maps_manager_errors(
    tmp_path: Path,
    error: BaseException,
    status: RecordingFileResponseStatus,
) -> None:
    manager = FakeRecordingManager(errors={"test.wav": error})
    server, path = make_server(tmp_path, manager)
    server.start()
    client = connect(path)

    try:
        client.sendall(encode_recording_file_request("test.wav"))
        observed, payload = receive_response(client)
    finally:
        client.close()
        server.stop()

    assert observed is status
    assert payload == b""
    assert server.snapshot().last_error is None


def test_recording_file_server_rejects_oversized_identifier_from_header(
    tmp_path: Path,
) -> None:
    manager = FakeRecordingManager()
    server, path = make_server(
        tmp_path,
        manager,
        max_identifier_bytes=8,
    )
    server.start()
    client = connect(path)

    try:
        client.sendall(
            _REQUEST_HEADER.pack(
                RECORDING_FILE_MAGIC,
                1,
                0,
                RECORDING_FILE_REQUEST_HEADER_BYTES,
                1024 * 1024,
            )
        )
        status, payload = receive_response(client)
    finally:
        client.close()
        server.stop()

    assert status is RecordingFileResponseStatus.INVALID_IDENTIFIER
    assert payload == b""
    assert manager.identifiers == []


def test_recording_file_server_rejects_excess_clients(
    tmp_path: Path,
) -> None:
    manager = FakeRecordingManager()
    server, path = make_server(
        tmp_path,
        manager,
        max_clients=1,
        client_timeout=5.0,
    )
    server.start()
    first = connect(path)

    try:
        wait_until(lambda: server.connected_clients == 1)
        second = connect(path)
        try:
            wait_until(
                lambda: server.snapshot().rejected_clients == 1
            )
            assert second.recv(1) == b""
        finally:
            second.close()
    finally:
        first.close()
        server.stop()


def test_recording_file_client_disconnect_is_not_operational_error(
    tmp_path: Path,
) -> None:
    manager = FakeRecordingManager()
    server, path = make_server(tmp_path, manager)
    server.start()
    client = connect(path)

    client.close()
    wait_until(lambda: server.connected_clients == 0)
    server.stop()

    assert server.snapshot().last_error is None
