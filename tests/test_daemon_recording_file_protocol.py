from __future__ import annotations

import struct

import pytest

from sds200.daemon_recording_file_protocol import (
    RECORDING_FILE_DEFAULT_MAX_IDENTIFIER_BYTES,
    RECORDING_FILE_MAGIC,
    RECORDING_FILE_REQUEST_HEADER_BYTES,
    RECORDING_FILE_RESPONSE_HEADER_BYTES,
    RECORDING_FILE_VERSION,
    RecordingFileProtocolError,
    RecordingFileResponseStatus,
    decode_recording_file_request,
    decode_recording_file_response,
    encode_recording_file_request,
    encode_recording_file_response,
)


def test_recording_file_request_round_trip() -> None:
    identifier = "SDS200/Metro/dispatch.wav"
    encoded = encode_recording_file_request(identifier)

    assert len(encoded) == (
        RECORDING_FILE_REQUEST_HEADER_BYTES
        + len(identifier.encode("utf-8"))
    )
    assert decode_recording_file_request(encoded) == identifier


def test_recording_file_request_rejects_oversized_identifier() -> None:
    identifier = "x" * (
        RECORDING_FILE_DEFAULT_MAX_IDENTIFIER_BYTES + 1
    )

    with pytest.raises(ValueError, match="maximum encoded size"):
        encode_recording_file_request(identifier)


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (
            lambda frame: b"NOPE" + frame[4:],
            "magic",
        ),
        (
            lambda frame: frame[:4] + bytes((2,)) + frame[5:],
            "version",
        ),
        (
            lambda frame: frame[:5] + bytes((1,)) + frame[6:],
            "flags",
        ),
        (
            lambda frame: frame[:-1],
            "length",
        ),
    ],
)
def test_recording_file_request_rejects_invalid_frames(
    mutator: object,
    message: str,
) -> None:
    assert callable(mutator)
    frame = encode_recording_file_request("dispatch.wav")

    with pytest.raises(RecordingFileProtocolError, match=message):
        decode_recording_file_request(mutator(frame))


@pytest.mark.parametrize("status", list(RecordingFileResponseStatus))
def test_recording_file_response_round_trip(
    status: RecordingFileResponseStatus,
) -> None:
    length = 741 if status is RecordingFileResponseStatus.OK else 0
    encoded = encode_recording_file_response(
        status,
        content_length=length,
    )

    assert len(encoded) == RECORDING_FILE_RESPONSE_HEADER_BYTES
    assert decode_recording_file_response(encoded) == (
        status,
        length,
    )


def test_recording_file_response_rejects_invalid_status() -> None:
    header = struct.pack(
        "!4sBBHQ",
        RECORDING_FILE_MAGIC,
        RECORDING_FILE_VERSION,
        255,
        RECORDING_FILE_RESPONSE_HEADER_BYTES,
        0,
    )

    with pytest.raises(
        RecordingFileProtocolError,
        match="status",
    ):
        decode_recording_file_response(header)


def test_failed_recording_file_response_cannot_carry_content() -> None:
    with pytest.raises(ValueError, match="cannot carry content"):
        encode_recording_file_response(
            RecordingFileResponseStatus.NOT_FOUND,
            content_length=1,
        )
