from __future__ import annotations

import pytest

from sds200.rtp import RtpPacket, RtpProtocolError, RtpSequenceTracker


def make_packet(
    payload: bytes = b"audio",
    *,
    sequence: int = 1,
    timestamp: int = 2,
    ssrc: int = 3,
    payload_type: int = 0,
    marker: bool = False,
    csrc: tuple[int, ...] = (),
    extension: tuple[int, bytes] | None = None,
    padding: int = 0,
) -> bytes:
    first = 0x80 | len(csrc)
    if extension is not None:
        first |= 0x10
    if padding:
        first |= 0x20
    second = payload_type | (0x80 if marker else 0)
    packet = bytearray((first, second))
    packet.extend(sequence.to_bytes(2, "big"))
    packet.extend(timestamp.to_bytes(4, "big"))
    packet.extend(ssrc.to_bytes(4, "big"))
    for value in csrc:
        packet.extend(value.to_bytes(4, "big"))
    if extension is not None:
        profile, data = extension
        assert len(data) % 4 == 0
        packet.extend(profile.to_bytes(2, "big"))
        packet.extend((len(data) // 4).to_bytes(2, "big"))
        packet.extend(data)
    packet.extend(payload)
    if padding:
        packet.extend(bytes(padding - 1))
        packet.append(padding)
    return bytes(packet)


def test_parse_pcmu_rtp_packet() -> None:
    packet = RtpPacket.parse(
        make_packet(b"\x01\x02", sequence=741, timestamp=1407173956, ssrc=0x56650DAA)
    )

    assert packet.payload_type == 0
    assert packet.sequence == 741
    assert packet.timestamp == 1407173956
    assert packet.ssrc == 0x56650DAA
    assert packet.payload == b"\x01\x02"


def test_parse_csrc_extension_and_padding() -> None:
    packet = RtpPacket.parse(
        make_packet(
            b"payload",
            marker=True,
            csrc=(10, 20),
            extension=(0xBEDE, b"abcd"),
            padding=4,
        )
    )

    assert packet.marker
    assert packet.csrc == (10, 20)
    assert packet.extension_profile == 0xBEDE
    assert packet.extension_data == b"abcd"
    assert packet.padding_size == 4
    assert packet.payload == b"payload"


@pytest.mark.parametrize(
    "data, message",
    [
        (b"short", "too short"),
        (bytes(12), "version"),
        (make_packet(payload_type=8), "payload type"),
        (bytes((0x90, 0)) + bytes(10), "extension header"),
        (make_packet(padding=1)[:-1] + b"\xff", "padding"),
    ],
)
def test_reject_malformed_or_unsupported_packets(data: bytes, message: str) -> None:
    with pytest.raises(RtpProtocolError, match=message):
        RtpPacket.parse(data)


def test_sequence_tracker_starts_from_first_actual_packet_and_wraps() -> None:
    tracker = RtpSequenceTracker()

    first = tracker.observe(741)
    assert first.expected is None
    assert tracker.observe(742).missing == 0

    tracker.reset()
    tracker.observe(65535)
    wrapped = tracker.observe(0)
    assert wrapped.expected == 0
    assert not wrapped.out_of_order


def test_sequence_tracker_reports_gaps_duplicates_and_late_packets() -> None:
    tracker = RtpSequenceTracker()
    tracker.observe(10)

    gap = tracker.observe(13)
    assert gap.expected == 11
    assert gap.missing == 2

    duplicate = tracker.observe(13)
    assert duplicate.duplicate

    late = tracker.observe(12)
    assert late.out_of_order


def test_sequence_tracker_detects_non_adjacent_duplicate() -> None:
    tracker = RtpSequenceTracker()
    tracker.observe(100)
    tracker.observe(101)
    tracker.observe(102)

    duplicate = tracker.observe(100)

    assert duplicate.duplicate
    assert not duplicate.out_of_order


def test_timestamp_tracker_reports_gaps_backwards_and_wraparound() -> None:
    from sds200.rtp import RtpTimestampTracker

    tracker = RtpTimestampTracker()
    assert tracker.observe(0xFFFFFFFC, 4).expected is None
    wrapped = tracker.observe(0, 4)
    assert wrapped.expected == 0
    assert wrapped.missing_samples == 0

    gap = tracker.observe(8, 4)
    assert gap.expected == 4
    assert gap.missing_samples == 4

    backwards = tracker.observe(4, 4)
    assert backwards.expected == 12
    assert backwards.backwards
