from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from sds200.pcmu import PcmuPacket


def make_packet(**overrides: object) -> PcmuPacket:
    values: dict[str, object] = {
        "endpoint": "rtsp://192.0.2.25/au:scanner.au",
        "sequence": 741,
        "timestamp": 1_407_173_956,
        "ssrc": 0x56650DAA,
        "payload": b"\x01\x02\x03\x04",
        "observed_at": datetime(2026, 8, 5, 7, 15, tzinfo=UTC),
    }
    values.update(overrides)
    return PcmuPacket(**values)  # type: ignore[arg-type]


def test_pcmu_packet_preserves_accepted_rtp_metadata() -> None:
    packet = make_packet(
        marker=True,
        expected_sequence=739,
        missing_packets=2,
        expected_timestamp=1_407_173_952,
        missing_samples=4,
    )

    assert packet.endpoint == "rtsp://192.0.2.25/au:scanner.au"
    assert packet.sequence == 741
    assert packet.timestamp == 1_407_173_956
    assert packet.ssrc == 0x56650DAA
    assert packet.payload == b"\x01\x02\x03\x04"
    assert packet.sample_count == 4
    assert packet.marker
    assert packet.expected_sequence == 739
    assert packet.missing_packets == 2
    assert packet.expected_timestamp == 1_407_173_952
    assert packet.missing_samples == 4
    assert not packet.timestamp_backwards
    assert packet.sequence_discontinuity
    assert packet.timestamp_discontinuity
    assert packet.discontinuous


def test_pcmu_packet_contiguous_defaults_have_no_discontinuity() -> None:
    packet = make_packet(
        expected_sequence=741,
        expected_timestamp=1_407_173_956,
    )

    assert packet.missing_packets == 0
    assert packet.missing_samples == 0
    assert not packet.timestamp_backwards
    assert not packet.sequence_discontinuity
    assert not packet.timestamp_discontinuity
    assert not packet.discontinuous


def test_pcmu_packet_supports_timestamp_backwards_observation() -> None:
    packet = make_packet(
        expected_timestamp=1_407_173_960,
        timestamp_backwards=True,
    )

    assert packet.timestamp_discontinuity
    assert packet.discontinuous


def test_pcmu_packet_is_immutable() -> None:
    packet = make_packet()

    with pytest.raises(FrozenInstanceError):
        packet.sequence = 742  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("endpoint", 1, TypeError),
        ("endpoint", "   ", ValueError),
        ("sequence", True, TypeError),
        ("sequence", -1, ValueError),
        ("sequence", 1 << 16, ValueError),
        ("timestamp", True, TypeError),
        ("timestamp", -1, ValueError),
        ("timestamp", 1 << 32, ValueError),
        ("ssrc", True, TypeError),
        ("ssrc", -1, ValueError),
        ("ssrc", 1 << 32, ValueError),
        ("payload", bytearray(b"audio"), TypeError),
        ("observed_at", "now", TypeError),
        ("observed_at", datetime(2026, 8, 5), ValueError),
        ("marker", 1, TypeError),
        ("expected_sequence", True, TypeError),
        ("expected_sequence", -1, ValueError),
        ("expected_sequence", 1 << 16, ValueError),
        ("missing_packets", True, TypeError),
        ("missing_packets", -1, ValueError),
        ("expected_timestamp", True, TypeError),
        ("expected_timestamp", -1, ValueError),
        ("expected_timestamp", 1 << 32, ValueError),
        ("missing_samples", True, TypeError),
        ("missing_samples", -1, ValueError),
        ("timestamp_backwards", 1, TypeError),
    ],
)
def test_pcmu_packet_rejects_invalid_fields(
    field: str,
    value: object,
    error: type[Exception],
) -> None:
    with pytest.raises(error):
        make_packet(**{field: value})


def test_pcmu_packet_loss_requires_expected_sequence() -> None:
    with pytest.raises(ValueError, match="expected sequence"):
        make_packet(missing_packets=1)


@pytest.mark.parametrize(
    "values",
    [
        {"missing_samples": 1},
        {"timestamp_backwards": True},
    ],
)
def test_pcmu_timestamp_discontinuity_requires_expected_timestamp(
    values: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="expected timestamp"):
        make_packet(**values)


def test_pcmu_timestamp_loss_and_backwards_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        make_packet(
            expected_timestamp=1234,
            missing_samples=1,
            timestamp_backwards=True,
        )
