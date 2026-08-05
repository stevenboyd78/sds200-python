from __future__ import annotations

import struct
from datetime import UTC, datetime, timedelta

from .pcmu import PcmuPacket
from .pcmu_subscriptions import (
    PcmuPacketDelivery,
    PcmuPublication,
)

PCMU_STREAM_PROTOCOL = "sdsctl.daemon.pcmu"
PCMU_STREAM_VERSION = 1
PCMU_STREAM_SUPPORTED_VERSIONS = (PCMU_STREAM_VERSION,)
PCMU_STREAM_MAGIC = b"SDSP"
PCMU_STREAM_DEFAULT_MAX_ENDPOINT_BYTES = 4096
PCMU_STREAM_DEFAULT_MAX_FRAME_BYTES = 128 * 1024

_FLAG_MARKER = 1 << 0
_FLAG_EXPECTED_SEQUENCE = 1 << 1
_FLAG_EXPECTED_TIMESTAMP = 1 << 2
_FLAG_TIMESTAMP_BACKWARDS = 1 << 3
_KNOWN_FLAGS = (
    _FLAG_MARKER
    | _FLAG_EXPECTED_SEQUENCE
    | _FLAG_EXPECTED_TIMESTAMP
    | _FLAG_TIMESTAMP_BACKWARDS
)

_HEADER = struct.Struct("!4sBBHIQqHIIHIIIQQQHI")
_UINT16_MAX = (1 << 16) - 1
_UINT32_MAX = (1 << 32) - 1
_UINT64_MAX = (1 << 64) - 1
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class PcmuProtocolError(ValueError):
    """Raised when a local PCMU frame violates the protocol contract."""


def _require_positive_integer(
    value: object,
    *,
    description: str,
) -> int:
    if type(value) is not int:
        raise TypeError(f"{description} must be an integer.")
    if value <= 0:
        raise ValueError(f"{description} must be greater than zero.")
    return value


def _require_unsigned_integer(
    value: object,
    *,
    description: str,
    maximum: int,
) -> int:
    if type(value) is not int:
        raise TypeError(f"{description} must be an integer.")
    if value < 0:
        raise ValueError(f"{description} must not be negative.")
    if value > maximum:
        raise ValueError(
            f"{description} must not exceed {maximum}."
        )
    return value


def _datetime_to_microseconds(value: datetime) -> int:
    observed_at = value.astimezone(UTC)
    delta = observed_at - _EPOCH
    return (
        delta.days * 86_400_000_000
        + delta.seconds * 1_000_000
        + delta.microseconds
    )


def _datetime_from_microseconds(value: int) -> datetime:
    try:
        return _EPOCH + timedelta(microseconds=value)
    except OverflowError as error:
        raise PcmuProtocolError(
            "PCMU frame observation timestamp is out of range."
        ) from error


def encode_pcmu_delivery(
    delivery: PcmuPacketDelivery,
    *,
    max_endpoint_bytes: int = (
        PCMU_STREAM_DEFAULT_MAX_ENDPOINT_BYTES
    ),
    max_frame_bytes: int = PCMU_STREAM_DEFAULT_MAX_FRAME_BYTES,
) -> bytes:
    """Encode one bounded PCMU subscription delivery."""

    if not isinstance(delivery, PcmuPacketDelivery):
        raise TypeError(
            "PCMU frame encoding requires a PcmuPacketDelivery."
        )

    maximum_endpoint = _require_positive_integer(
        max_endpoint_bytes,
        description="PCMU maximum endpoint size",
    )
    maximum_frame = _require_positive_integer(
        max_frame_bytes,
        description="PCMU maximum frame size",
    )
    if maximum_frame < _HEADER.size:
        raise ValueError(
            "PCMU maximum frame size must accommodate the fixed header."
        )

    publication = delivery.publication
    if not isinstance(publication, PcmuPublication):
        raise TypeError(
            "PCMU delivery publication must be a PcmuPublication."
        )
    packet = publication.packet
    if not isinstance(packet, PcmuPacket):
        raise TypeError(
            "PCMU publication packet must be a PcmuPacket."
        )

    stream_sequence = _require_unsigned_integer(
        publication.stream_sequence,
        description="PCMU stream sequence",
        maximum=_UINT64_MAX,
    )
    if stream_sequence == 0:
        raise ValueError(
            "PCMU stream sequence must be greater than zero."
        )

    packets_dropped = _require_unsigned_integer(
        delivery.packets_dropped,
        description="PCMU dropped-packet count",
        maximum=_UINT64_MAX,
    )
    payload_bytes_dropped = _require_unsigned_integer(
        delivery.payload_bytes_dropped,
        description="PCMU dropped-payload byte count",
        maximum=_UINT64_MAX,
    )
    overflows = _require_unsigned_integer(
        delivery.overflows,
        description="PCMU overflow count",
        maximum=_UINT64_MAX,
    )

    try:
        endpoint = packet.endpoint.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError(
            "PCMU packet endpoint must be valid UTF-8."
        ) from error

    if len(endpoint) > maximum_endpoint:
        raise ValueError(
            "PCMU packet endpoint exceeds the maximum encoded size "
            f"of {maximum_endpoint} bytes."
        )
    if len(endpoint) > _UINT16_MAX:
        raise ValueError(
            "PCMU packet endpoint exceeds the protocol field size."
        )

    payload = packet.payload
    if len(payload) > _UINT32_MAX:
        raise ValueError(
            "PCMU packet payload exceeds the protocol field size."
        )

    frame_size = _HEADER.size + len(endpoint) + len(payload)
    if frame_size > maximum_frame:
        raise ValueError(
            "PCMU frame exceeds the maximum encoded size "
            f"of {maximum_frame} bytes."
        )
    if frame_size > _UINT32_MAX:
        raise ValueError(
            "PCMU frame exceeds the protocol field size."
        )

    flags = 0
    if packet.marker:
        flags |= _FLAG_MARKER
    if packet.expected_sequence is not None:
        flags |= _FLAG_EXPECTED_SEQUENCE
    if packet.expected_timestamp is not None:
        flags |= _FLAG_EXPECTED_TIMESTAMP
    if packet.timestamp_backwards:
        flags |= _FLAG_TIMESTAMP_BACKWARDS

    header = _HEADER.pack(
        PCMU_STREAM_MAGIC,
        PCMU_STREAM_VERSION,
        flags,
        _HEADER.size,
        frame_size,
        stream_sequence,
        _datetime_to_microseconds(packet.observed_at),
        packet.sequence,
        packet.timestamp,
        packet.ssrc,
        packet.expected_sequence or 0,
        packet.missing_packets,
        packet.expected_timestamp or 0,
        packet.missing_samples,
        packets_dropped,
        payload_bytes_dropped,
        overflows,
        len(endpoint),
        len(payload),
    )
    return header + endpoint + payload


def decode_pcmu_delivery(
    frame: bytes,
    *,
    max_endpoint_bytes: int = (
        PCMU_STREAM_DEFAULT_MAX_ENDPOINT_BYTES
    ),
    max_frame_bytes: int = PCMU_STREAM_DEFAULT_MAX_FRAME_BYTES,
) -> PcmuPacketDelivery:
    """Decode one complete versioned local PCMU frame."""

    if not isinstance(frame, bytes):
        raise TypeError("PCMU frame must be bytes.")

    maximum_endpoint = _require_positive_integer(
        max_endpoint_bytes,
        description="PCMU maximum endpoint size",
    )
    maximum_frame = _require_positive_integer(
        max_frame_bytes,
        description="PCMU maximum frame size",
    )
    if maximum_frame < _HEADER.size:
        raise ValueError(
            "PCMU maximum frame size must accommodate the fixed header."
        )
    if len(frame) < _HEADER.size:
        raise PcmuProtocolError(
            "PCMU frame is shorter than the fixed header."
        )
    if len(frame) > maximum_frame:
        raise PcmuProtocolError(
            "PCMU frame exceeds the maximum encoded size "
            f"of {maximum_frame} bytes."
        )

    (
        magic,
        version,
        flags,
        header_size,
        frame_size,
        stream_sequence,
        observed_microseconds,
        sequence,
        timestamp,
        ssrc,
        encoded_expected_sequence,
        missing_packets,
        encoded_expected_timestamp,
        missing_samples,
        packets_dropped,
        payload_bytes_dropped,
        overflows,
        endpoint_size,
        payload_size,
    ) = _HEADER.unpack_from(frame)

    if magic != PCMU_STREAM_MAGIC:
        raise PcmuProtocolError("PCMU frame magic is invalid.")
    if version not in PCMU_STREAM_SUPPORTED_VERSIONS:
        raise PcmuProtocolError(
            "Unsupported PCMU stream version: "
            f"{version}; "
            f"supported={list(PCMU_STREAM_SUPPORTED_VERSIONS)!r}."
        )
    if flags & ~_KNOWN_FLAGS:
        raise PcmuProtocolError(
            "PCMU frame contains unsupported flags."
        )
    if header_size != _HEADER.size:
        raise PcmuProtocolError(
            "PCMU frame header size is invalid."
        )
    if frame_size != len(frame):
        raise PcmuProtocolError(
            "PCMU frame length does not match its encoded size."
        )
    if endpoint_size > maximum_endpoint:
        raise PcmuProtocolError(
            "PCMU packet endpoint exceeds the maximum encoded size "
            f"of {maximum_endpoint} bytes."
        )
    if header_size + endpoint_size + payload_size != frame_size:
        raise PcmuProtocolError(
            "PCMU frame endpoint and payload sizes are inconsistent."
        )
    if stream_sequence == 0:
        raise PcmuProtocolError(
            "PCMU stream sequence must be greater than zero."
        )

    endpoint_start = header_size
    endpoint_end = endpoint_start + endpoint_size
    endpoint_bytes = frame[endpoint_start:endpoint_end]
    payload = frame[endpoint_end:]

    try:
        endpoint = endpoint_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PcmuProtocolError(
            "PCMU packet endpoint is not valid UTF-8."
        ) from error

    has_expected_sequence = bool(flags & _FLAG_EXPECTED_SEQUENCE)
    if not has_expected_sequence and encoded_expected_sequence:
        raise PcmuProtocolError(
            "PCMU frame has a sequence value without its presence flag."
        )
    expected_sequence = (
        encoded_expected_sequence
        if has_expected_sequence
        else None
    )

    has_expected_timestamp = bool(flags & _FLAG_EXPECTED_TIMESTAMP)
    if not has_expected_timestamp and encoded_expected_timestamp:
        raise PcmuProtocolError(
            "PCMU frame has a timestamp value without its presence flag."
        )
    expected_timestamp = (
        encoded_expected_timestamp
        if has_expected_timestamp
        else None
    )

    try:
        packet = PcmuPacket(
            endpoint=endpoint,
            sequence=sequence,
            timestamp=timestamp,
            ssrc=ssrc,
            payload=payload,
            observed_at=_datetime_from_microseconds(
                observed_microseconds
            ),
            marker=bool(flags & _FLAG_MARKER),
            expected_sequence=expected_sequence,
            missing_packets=missing_packets,
            expected_timestamp=expected_timestamp,
            missing_samples=missing_samples,
            timestamp_backwards=bool(
                flags & _FLAG_TIMESTAMP_BACKWARDS
            ),
        )
        publication = PcmuPublication(
            stream_sequence=stream_sequence,
            packet=packet,
        )
        return PcmuPacketDelivery(
            publication=publication,
            packets_dropped=packets_dropped,
            payload_bytes_dropped=payload_bytes_dropped,
            overflows=overflows,
        )
    except (TypeError, ValueError) as error:
        raise PcmuProtocolError(
            "PCMU frame contains invalid packet metadata."
        ) from error
