from __future__ import annotations

import socket
import threading
from pathlib import Path

import pytest

from sds200 import (
    DaemonDisconnectedError,
    DaemonProtocolError,
    DaemonSocketLocation,
    DaemonSocketSource,
    DaemonUnavailableError,
)
from sds200.daemon_recording_file_client import (
    DaemonRecordingFileClient,
    DaemonRecordingFileRequestError,
)
from sds200.daemon_recording_file_protocol import (
    RecordingFileResponseStatus,
    encode_recording_file_response,
)


def start_scripted_server(
    path: Path,
    response: bytes,
) -> threading.Thread:
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen(1)

    def serve() -> None:
        try:
            client, _ = listener.accept()
            with client:
                client.recv(8192)
                if response:
                    client.sendall(response)
        finally:
            listener.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return thread


@pytest.mark.parametrize(
    ("keyword", "value", "error_type"),
    [
        ("timeout", True, TypeError),
        ("timeout", 0, ValueError),
        ("timeout", float("inf"), ValueError),
        ("max_identifier_bytes", True, TypeError),
        ("max_identifier_bytes", 0, ValueError),
        ("max_content_bytes", True, TypeError),
        ("max_content_bytes", 0, ValueError),
    ],
)
def test_recording_file_client_rejects_invalid_limits(
    tmp_path: Path,
    keyword: str,
    value: object,
    error_type: type[Exception],
) -> None:
    location = DaemonSocketLocation(
        tmp_path / "recordings.sock",
        DaemonSocketSource.EXPLICIT,
    )

    with pytest.raises(error_type):
        DaemonRecordingFileClient(
            location,
            **{keyword: value},  # type: ignore[arg-type]
        )


def test_recording_file_client_requires_socket_location() -> None:
    with pytest.raises(TypeError, match="DaemonSocketLocation"):
        DaemonRecordingFileClient(object())  # type: ignore[arg-type]


def test_recording_file_client_reads_exact_content(
    tmp_path: Path,
) -> None:
    path = tmp_path / "recordings.sock"
    payload = b"RIFF" + (b"\x01" * 128)
    response = (
        encode_recording_file_response(
            RecordingFileResponseStatus.OK,
            content_length=len(payload),
        )
        + payload
    )
    thread = start_scripted_server(path, response)
    client = DaemonRecordingFileClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    download = client.open("2026/test.wav")
    assert download.content_length == len(payload)
    assert download.remaining == len(payload)
    assert download.read(4) == b"RIFF"
    assert download.read() == payload[4:]
    assert download.remaining == 0
    assert download.closed

    thread.join(timeout=1.0)


@pytest.mark.parametrize(
    "status",
    [
        RecordingFileResponseStatus.INVALID_IDENTIFIER,
        RecordingFileResponseStatus.NOT_FOUND,
        RecordingFileResponseStatus.NOT_PLAYABLE,
        RecordingFileResponseStatus.UNAVAILABLE,
        RecordingFileResponseStatus.FAILED,
    ],
)
def test_recording_file_client_classifies_failed_status_without_identifier(
    tmp_path: Path,
    status: RecordingFileResponseStatus,
) -> None:
    path = tmp_path / f"{status.name.lower()}.sock"
    thread = start_scripted_server(
        path,
        encode_recording_file_response(status),
    )
    client = DaemonRecordingFileClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )
    identifier = "secret/path/test.wav"

    with pytest.raises(DaemonRecordingFileRequestError) as raised:
        client.open(identifier)

    thread.join(timeout=1.0)
    assert raised.value.status is status
    assert identifier not in str(raised.value)


def test_recording_file_client_rejects_truncated_header(
    tmp_path: Path,
) -> None:
    path = tmp_path / "truncated-header.sock"
    thread = start_scripted_server(path, b"SDSR")
    client = DaemonRecordingFileClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    with pytest.raises(DaemonProtocolError, match="header was truncated"):
        client.open("test.wav")

    thread.join(timeout=1.0)


def test_recording_file_client_rejects_truncated_content(
    tmp_path: Path,
) -> None:
    path = tmp_path / "truncated-content.sock"
    response = (
        encode_recording_file_response(
            RecordingFileResponseStatus.OK,
            content_length=10,
        )
        + b"1234"
    )
    thread = start_scripted_server(path, response)
    client = DaemonRecordingFileClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    download = client.open("test.wav")
    with pytest.raises(DaemonProtocolError, match="truncated"):
        download.read()

    thread.join(timeout=1.0)
    assert download.closed


def test_recording_file_client_rejects_oversized_content(
    tmp_path: Path,
) -> None:
    path = tmp_path / "oversized.sock"
    thread = start_scripted_server(
        path,
        encode_recording_file_response(
            RecordingFileResponseStatus.OK,
            content_length=11,
        ),
    )
    client = DaemonRecordingFileClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT),
        max_content_bytes=10,
    )

    with pytest.raises(DaemonProtocolError, match="maximum accepted"):
        client.open("test.wav")

    thread.join(timeout=1.0)


def test_recording_file_client_rejects_extra_content(
    tmp_path: Path,
) -> None:
    path = tmp_path / "extra.sock"
    response = (
        encode_recording_file_response(
            RecordingFileResponseStatus.OK,
            content_length=4,
        )
        + b"12345"
    )
    thread = start_scripted_server(path, response)
    client = DaemonRecordingFileClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    download = client.open("test.wav")
    with pytest.raises(DaemonProtocolError, match="exceeded"):
        download.read()

    thread.join(timeout=1.0)
    assert download.closed


def test_recording_file_client_reports_missing_socket(
    tmp_path: Path,
) -> None:
    path = tmp_path / "missing.sock"
    client = DaemonRecordingFileClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    with pytest.raises(
        DaemonUnavailableError,
        match="recording-file socket was not found",
    ):
        client.open("test.wav")


def test_recording_file_client_reports_clean_disconnect(
    tmp_path: Path,
) -> None:
    path = tmp_path / "disconnect.sock"
    thread = start_scripted_server(path, b"")
    client = DaemonRecordingFileClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    with pytest.raises(DaemonDisconnectedError, match="disconnected"):
        client.open("test.wav")

    thread.join(timeout=1.0)
