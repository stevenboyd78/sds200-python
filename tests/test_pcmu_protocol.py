from __future__ import annotations

import struct
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from sds200.pcmu import PcmuPacket
from sds200.pcmu_protocol import (
    PCMU_STREAM_HEADER_BYTES,
    PCMU_STREAM_MAGIC,
    PCMU_STREAM_PROTOCOL,
    PCMU_STREAM_SUPPORTED_VERSIONS,
    PCMU_STREAM_VERSION,
    PcmuProtocolError,
    decode_pcmu_delivery,
    encode_pcmu_delivery,
)
from sds200.pcmu_subscriptions import (
    PcmuPacketDelivery,
    PcmuPublication,
)

_HEADER = struct.Struct("!4sBBHIQqHIIHIIIQQQHI")


def make_delivery(
    *,
    stream_sequence: int = 7,
    endpoint: str = "rtsp://192.0.2.25/au:scanner.au",
    sequence: int = 741,
    timestamp: int = 1_407_173_956,
    ssrc: int = 0x56650DAA,
    payload: bytes = b"\x01\x02\x03\x04",
    observed_at: datetime = datetime(
        2026,
        8,
        5,
        7,
        45,
        12,
        345678,
        tzinfo=UTC,
    ),
    marker: bool = True,
    expected_sequence: int | None = 739,
    missing_packets: int = 2,
    expected_timestamp: int | None = 1_407_173_952,
    missing_samples: int = 4,
    timestamp_backwards: bool = False,
    packets_dropped: int = 3,
    payload_bytes_dropped: int = 960,
    overflows: int = 3,
) -> PcmuPacketDelivery:
    packet = PcmuPacket(
        endpoint=endpoint,
        sequence=sequence,
        timestamp=timestamp,
        ssrc=ssrc,
        payload=payload,
        observed_at=observed_at,
        marker=marker,
        expected_sequence=expected_sequence,
        missing_packets=missing_packets,
        expected_timestamp=expected_timestamp,
        missing_samples=missing_samples,
        timestamp_backwards=timestamp_backwards,
    )
    return PcmuPacketDelivery(
        publication=PcmuPublication(
            stream_sequence=stream_sequence,
            packet=packet,
        ),
        packets_dropped=packets_dropped,
        payload_bytes_dropped=payload_bytes_dropped,
        overflows=overflows,
    )


def replace_header_field(
    frame: bytes,
    index: int,
    value: object,
) -> bytes:
    values = list(_HEADER.unpack_from(frame))
    values[index] = value
    return _HEADER.pack(*values) + frame[_HEADER.size :]


def test_protocol_identity_is_versioned_and_stable() -> None:
    assert PCMU_STREAM_PROTOCOL == "sdsctl.daemon.pcmu"
    assert PCMU_STREAM_MAGIC == b"SDSP"
    assert _HEADER.size == PCMU_STREAM_HEADER_BYTES
    assert PCMU_STREAM_VERSION == 1
    assert PCMU_STREAM_SUPPORTED_VERSIONS == (1,)


def test_delivery_round_trip_preserves_packet_and_queue_loss_metadata() -> None:
    delivery = make_delivery()

    encoded = encode_pcmu_delivery(delivery)
    decoded = decode_pcmu_delivery(encoded)

    assert encoded.startswith(PCMU_STREAM_MAGIC)
    assert decoded.stream_sequence == 7
    assert decoded.packet.endpoint == (
        "rtsp://192.0.2.25/au:scanner.au"
    )
    assert decoded.packet.sequence == 741
    assert decoded.packet.timestamp == 1_407_173_956
    assert decoded.packet.ssrc == 0x56650DAA
    assert decoded.packet.payload == b"\x01\x02\x03\x04"
    assert decoded.packet.observed_at == datetime(
        2026,
        8,
        5,
        7,
        45,
        12,
        345678,
        tzinfo=UTC,
    )
    assert decoded.packet.marker
    assert decoded.packet.expected_sequence == 739
    assert decoded.packet.missing_packets == 2
    assert decoded.packet.expected_timestamp == 1_407_173_952
    assert decoded.packet.missing_samples == 4
    assert not decoded.packet.timestamp_backwards
    assert decoded.packet.discontinuous
    assert decoded.packets_dropped == 3
    assert decoded.payload_bytes_dropped == 960
    assert decoded.overflows == 3
    assert decoded.health == "degraded"


def test_round_trip_preserves_absent_expectations_and_timestamp_backwards() -> None:
    delivery = make_delivery(
        marker=False,
        expected_sequence=None,
        missing_packets=0,
        expected_timestamp=1_407_173_960,
        missing_samples=0,
        timestamp_backwards=True,
        packets_dropped=0,
        payload_bytes_dropped=0,
        overflows=0,
    )

    decoded = decode_pcmu_delivery(encode_pcmu_delivery(delivery))

    assert not decoded.packet.marker
    assert decoded.packet.expected_sequence is None
    assert decoded.packet.expected_timestamp == 1_407_173_960
    assert decoded.packet.timestamp_backwards
    assert decoded.packet.timestamp_discontinuity
    assert decoded.health == "healthy"


def test_round_trip_normalizes_timestamp_to_same_utc_instant() -> None:
    mountain = ZoneInfo("America/Denver")
    observed_at = datetime(
        2026,
        8,
        5,
        1,
        45,
        12,
        123456,
        tzinfo=mountain,
    )
    delivery = make_delivery(observed_at=observed_at)

    decoded = decode_pcmu_delivery(encode_pcmu_delivery(delivery))

    assert decoded.packet.observed_at == observed_at.astimezone(UTC)


def test_header_exposes_complete_frame_length_for_stream_readers() -> None:
    delivery = make_delivery(
        endpoint="rtsp://scanner/audio",
        payload=b"pcmu" * 40,
    )

    encoded = encode_pcmu_delivery(delivery)
    values = _HEADER.unpack_from(encoded)

    assert values[0] == PCMU_STREAM_MAGIC
    assert values[1] == PCMU_STREAM_VERSION
    assert values[3] == _HEADER.size
    assert values[4] == len(encoded)
    assert values[-2] == len(b"rtsp://scanner/audio")
    assert values[-1] == 160


@pytest.mark.parametrize(
    ("keyword", "value", "error"),
    [
        ("max_endpoint_bytes", True, TypeError),
        ("max_endpoint_bytes", 0, ValueError),
        ("max_frame_bytes", True, TypeError),
        ("max_frame_bytes", 0, ValueError),
    ],
)
def test_codec_rejects_invalid_limits(
    keyword: str,
    value: object,
    error: type[Exception],
) -> None:
    delivery = make_delivery()

    with pytest.raises(error):
        encode_pcmu_delivery(
            delivery,
            **{keyword: value},  # type: ignore[arg-type]
        )
    with pytest.raises(error):
        decode_pcmu_delivery(
            encode_pcmu_delivery(delivery),
            **{keyword: value},  # type: ignore[arg-type]
        )


def test_encoder_rejects_non_delivery() -> None:
    with pytest.raises(TypeError, match="PcmuPacketDelivery"):
        encode_pcmu_delivery(object())  # type: ignore[arg-type]


def test_encoder_rejects_oversized_endpoint() -> None:
    delivery = make_delivery(endpoint="x" * 17)

    with pytest.raises(ValueError, match="endpoint"):
        encode_pcmu_delivery(
            delivery,
            max_endpoint_bytes=16,
        )


def test_encoder_rejects_oversized_frame() -> None:
    delivery = make_delivery(payload=b"x" * 1024)

    with pytest.raises(ValueError, match="maximum encoded size"):
        encode_pcmu_delivery(
            delivery,
            max_frame_bytes=128,
        )


def test_encoder_rejects_non_utf8_endpoint_scalar() -> None:
    delivery = make_delivery(endpoint="bad\udcff")

    with pytest.raises(ValueError, match="UTF-8"):
        encode_pcmu_delivery(delivery)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("stream_sequence", 1 << 64),
        ("packets_dropped", -1),
        ("payload_bytes_dropped", 1 << 64),
        ("overflows", True),
    ],
)
def test_encoder_rejects_unrepresentable_counters(
    field: str,
    value: object,
) -> None:
    delivery = make_delivery()
    values: dict[str, object] = {
        "publication": delivery.publication,
        "packets_dropped": delivery.packets_dropped,
        "payload_bytes_dropped": delivery.payload_bytes_dropped,
        "overflows": delivery.overflows,
    }

    if field == "stream_sequence":
        publication = PcmuPublication(
            stream_sequence=1,
            packet=delivery.packet,
        )
        object.__setattr__(publication, "stream_sequence", value)
        values["publication"] = publication
    else:
        values[field] = value

    malformed = PcmuPacketDelivery(**values)  # type: ignore[arg-type]

    with pytest.raises((TypeError, ValueError)):
        encode_pcmu_delivery(malformed)


def test_decoder_rejects_non_bytes() -> None:
    with pytest.raises(TypeError, match="bytes"):
        decode_pcmu_delivery(bytearray(b"frame"))  # type: ignore[arg-type]


def test_decoder_rejects_short_frame() -> None:
    with pytest.raises(PcmuProtocolError, match="shorter"):
        decode_pcmu_delivery(b"SDSP")


@pytest.mark.parametrize(
    ("index", "value", "message"),
    [
        (0, b"NOPE", "magic"),
        (1, 2, "version"),
        (2, 0x80, "flags"),
        (3, _HEADER.size + 1, "header size"),
        (4, _HEADER.size, "length"),
        (5, 0, "sequence"),
    ],
)
def test_decoder_rejects_invalid_header_fields(
    index: int,
    value: object,
    message: str,
) -> None:
    encoded = encode_pcmu_delivery(make_delivery())
    malformed = replace_header_field(encoded, index, value)

    with pytest.raises(PcmuProtocolError, match=message):
        decode_pcmu_delivery(malformed)


def test_decoder_rejects_inconsistent_endpoint_and_payload_sizes() -> None:
    encoded = encode_pcmu_delivery(make_delivery())
    values = list(_HEADER.unpack_from(encoded))
    values[-2] = values[-2] + 1
    malformed = _HEADER.pack(*values) + encoded[_HEADER.size :]

    with pytest.raises(PcmuProtocolError, match="inconsistent"):
        decode_pcmu_delivery(malformed)


def test_decoder_rejects_endpoint_over_configured_limit() -> None:
    encoded = encode_pcmu_delivery(make_delivery(endpoint="x" * 32))

    with pytest.raises(PcmuProtocolError, match="endpoint"):
        decode_pcmu_delivery(
            encoded,
            max_endpoint_bytes=16,
        )


def test_decoder_rejects_frame_over_configured_limit() -> None:
    encoded = encode_pcmu_delivery(make_delivery(payload=b"x" * 256))

    with pytest.raises(PcmuProtocolError, match="maximum encoded size"):
        decode_pcmu_delivery(
            encoded,
            max_frame_bytes=len(encoded) - 1,
        )


def test_decoder_rejects_invalid_utf8_endpoint() -> None:
    encoded = bytearray(
        encode_pcmu_delivery(
            make_delivery(endpoint="valid"),
        )
    )
    encoded[_HEADER.size] = 0xFF

    with pytest.raises(PcmuProtocolError, match="UTF-8"):
        decode_pcmu_delivery(bytes(encoded))


@pytest.mark.parametrize(
    ("flag_index", "value_index", "message"),
    [
        (2, 10, "sequence value"),
        (2, 12, "timestamp value"),
    ],
)
def test_decoder_rejects_unflagged_optional_values(
    flag_index: int,
    value_index: int,
    message: str,
) -> None:
    delivery = make_delivery(
        expected_sequence=None,
        missing_packets=0,
        expected_timestamp=None,
        missing_samples=0,
    )
    encoded = encode_pcmu_delivery(delivery)
    values = list(_HEADER.unpack_from(encoded))
    values[flag_index] = 0
    values[value_index] = 1
    malformed = _HEADER.pack(*values) + encoded[_HEADER.size :]

    with pytest.raises(PcmuProtocolError, match=message):
        decode_pcmu_delivery(malformed)


def test_decoder_rejects_packet_metadata_inconsistency() -> None:
    delivery = make_delivery(
        expected_sequence=None,
        missing_packets=0,
    )
    encoded = encode_pcmu_delivery(delivery)
    values = list(_HEADER.unpack_from(encoded))
    values[11] = 1
    malformed = _HEADER.pack(*values) + encoded[_HEADER.size :]

    with pytest.raises(PcmuProtocolError, match="metadata"):
        decode_pcmu_delivery(malformed)


def test_round_trip_supports_rtp_sequence_and_timestamp_wrap_values() -> None:
    delivery = make_delivery(
        sequence=0xFFFF,
        timestamp=0xFFFFFFFF,
        expected_sequence=0,
        missing_packets=0,
        expected_timestamp=0,
        missing_samples=0,
    )

    decoded = decode_pcmu_delivery(encode_pcmu_delivery(delivery))

    assert decoded.packet.sequence == 0xFFFF
    assert decoded.packet.timestamp == 0xFFFFFFFF
    assert decoded.packet.expected_sequence == 0
    assert decoded.packet.expected_timestamp == 0


def test_round_trip_supports_pre_epoch_observation_timestamp() -> None:
    observed_at = datetime(1969, 12, 31, 23, 59, tzinfo=UTC)
    delivery = make_delivery(observed_at=observed_at)

    decoded = decode_pcmu_delivery(encode_pcmu_delivery(delivery))

    assert decoded.packet.observed_at == observed_at


def test_round_trip_preserves_microsecond_precision() -> None:
    observed_at = datetime(
        2026,
        8,
        5,
        7,
        45,
        tzinfo=UTC,
    ) + timedelta(microseconds=999999)
    delivery = make_delivery(observed_at=observed_at)

    decoded = decode_pcmu_delivery(encode_pcmu_delivery(delivery))

    assert decoded.packet.observed_at == observed_at
