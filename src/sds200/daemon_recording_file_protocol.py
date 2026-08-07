from __future__ import annotations

import struct
from enum import IntEnum

RECORDING_FILE_PROTOCOL = "sdsctl.daemon.recording-file"
RECORDING_FILE_VERSION = 1
RECORDING_FILE_SUPPORTED_VERSIONS = (RECORDING_FILE_VERSION,)
RECORDING_FILE_MAGIC = b"SDSR"
RECORDING_FILE_DEFAULT_MAX_IDENTIFIER_BYTES = 4096

_REQUEST_HEADER = struct.Struct("!4sBBHI")
_RESPONSE_HEADER = struct.Struct("!4sBBHQ")
RECORDING_FILE_REQUEST_HEADER_BYTES = _REQUEST_HEADER.size
RECORDING_FILE_RESPONSE_HEADER_BYTES = _RESPONSE_HEADER.size


class RecordingFileProtocolError(ValueError):
    """Raised when a local recording-file frame violates its contract."""


class RecordingFileResponseStatus(IntEnum):
    """Stable recording-file response classifications."""

    OK = 0
    INVALID_IDENTIFIER = 1
    NOT_FOUND = 2
    NOT_PLAYABLE = 3
    UNAVAILABLE = 4
    FAILED = 5


def _maximum_identifier(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(
            "Recording-file maximum identifier size must be an integer."
        )
    if value <= 0:
        raise ValueError(
            "Recording-file maximum identifier size must be greater than zero."
        )
    return value


def encode_recording_file_request(
    identifier: str,
    *,
    max_identifier_bytes: int = RECORDING_FILE_DEFAULT_MAX_IDENTIFIER_BYTES,
) -> bytes:
    """Encode one bounded finalized-recording identifier request."""

    maximum = _maximum_identifier(max_identifier_bytes)
    if not isinstance(identifier, str):
        raise TypeError("Recording-file identifier must be a string.")
    try:
        encoded = identifier.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError(
            "Recording-file identifier must be valid UTF-8."
        ) from error
    if not encoded:
        raise ValueError("Recording-file identifier must not be empty.")
    if len(encoded) > maximum:
        raise ValueError(
            "Recording-file identifier exceeds the maximum encoded size "
            f"of {maximum} bytes."
        )

    return _REQUEST_HEADER.pack(
        RECORDING_FILE_MAGIC,
        RECORDING_FILE_VERSION,
        0,
        RECORDING_FILE_REQUEST_HEADER_BYTES,
        len(encoded),
    ) + encoded


def decode_recording_file_request(
    frame: bytes,
    *,
    max_identifier_bytes: int = RECORDING_FILE_DEFAULT_MAX_IDENTIFIER_BYTES,
) -> str:
    """Decode one complete bounded finalized-recording request."""

    maximum = _maximum_identifier(max_identifier_bytes)
    if not isinstance(frame, bytes):
        raise TypeError("Recording-file request frame must be bytes.")
    if len(frame) < RECORDING_FILE_REQUEST_HEADER_BYTES:
        raise RecordingFileProtocolError(
            "Recording-file request is shorter than its fixed header."
        )

    magic, version, flags, header_size, identifier_size = (
        _REQUEST_HEADER.unpack_from(frame)
    )
    if magic != RECORDING_FILE_MAGIC:
        raise RecordingFileProtocolError(
            "Recording-file request magic is invalid."
        )
    if version not in RECORDING_FILE_SUPPORTED_VERSIONS:
        raise RecordingFileProtocolError(
            "Unsupported recording-file request version: "
            f"{version}; "
            f"supported={list(RECORDING_FILE_SUPPORTED_VERSIONS)!r}."
        )
    if flags != 0:
        raise RecordingFileProtocolError(
            "Recording-file request contains unsupported flags."
        )
    if header_size != RECORDING_FILE_REQUEST_HEADER_BYTES:
        raise RecordingFileProtocolError(
            "Recording-file request header size is invalid."
        )
    if identifier_size == 0:
        raise RecordingFileProtocolError(
            "Recording-file request identifier is empty."
        )
    if identifier_size > maximum:
        raise RecordingFileProtocolError(
            "Recording-file request identifier exceeds the maximum "
            f"encoded size of {maximum} bytes."
        )
    if header_size + identifier_size != len(frame):
        raise RecordingFileProtocolError(
            "Recording-file request length is inconsistent."
        )

    try:
        return frame[header_size:].decode("utf-8")
    except UnicodeDecodeError as error:
        raise RecordingFileProtocolError(
            "Recording-file request identifier is not valid UTF-8."
        ) from error


def encode_recording_file_response(
    status: RecordingFileResponseStatus,
    *,
    content_length: int = 0,
) -> bytes:
    """Encode one fixed recording-file response header."""

    if not isinstance(status, RecordingFileResponseStatus):
        raise TypeError(
            "Recording-file response status must be a RecordingFileResponseStatus."
        )
    if isinstance(content_length, bool) or not isinstance(content_length, int):
        raise TypeError(
            "Recording-file response content length must be an integer."
        )
    if content_length < 0:
        raise ValueError(
            "Recording-file response content length must not be negative."
        )
    if status is not RecordingFileResponseStatus.OK and content_length:
        raise ValueError(
            "Failed recording-file responses cannot carry content."
        )

    return _RESPONSE_HEADER.pack(
        RECORDING_FILE_MAGIC,
        RECORDING_FILE_VERSION,
        int(status),
        RECORDING_FILE_RESPONSE_HEADER_BYTES,
        content_length,
    )


def decode_recording_file_response(
    header: bytes,
) -> tuple[RecordingFileResponseStatus, int]:
    """Decode one complete fixed recording-file response header."""

    if not isinstance(header, bytes):
        raise TypeError("Recording-file response header must be bytes.")
    if len(header) != RECORDING_FILE_RESPONSE_HEADER_BYTES:
        raise RecordingFileProtocolError(
            "Recording-file response header has an invalid size."
        )

    magic, version, encoded_status, header_size, content_length = (
        _RESPONSE_HEADER.unpack(header)
    )
    if magic != RECORDING_FILE_MAGIC:
        raise RecordingFileProtocolError(
            "Recording-file response magic is invalid."
        )
    if version not in RECORDING_FILE_SUPPORTED_VERSIONS:
        raise RecordingFileProtocolError(
            "Unsupported recording-file response version: "
            f"{version}; "
            f"supported={list(RECORDING_FILE_SUPPORTED_VERSIONS)!r}."
        )
    if header_size != RECORDING_FILE_RESPONSE_HEADER_BYTES:
        raise RecordingFileProtocolError(
            "Recording-file response header size is invalid."
        )
    try:
        status = RecordingFileResponseStatus(encoded_status)
    except ValueError as error:
        raise RecordingFileProtocolError(
            "Recording-file response status is invalid."
        ) from error
    if status is not RecordingFileResponseStatus.OK and content_length:
        raise RecordingFileProtocolError(
            "Failed recording-file response unexpectedly carries content."
        )
    return status, content_length
