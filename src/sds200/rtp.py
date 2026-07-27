from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .exceptions import ProtocolError

RTP_VERSION = 2
PCMU_PAYLOAD_TYPE = 0
_MINIMUM_HEADER_SIZE = 12
_SEQUENCE_MODULUS = 1 << 16
_TIMESTAMP_MODULUS = 1 << 32
_DEFAULT_SEQUENCE_HISTORY = 128


class RtpProtocolError(ProtocolError):
    """An RTP datagram is malformed or uses an unsupported format."""


@dataclass(frozen=True, slots=True)
class RtpPacket:
    """Parsed RTP version 2 packet carrying SDS200 network audio."""

    marker: bool
    payload_type: int
    sequence: int
    timestamp: int
    ssrc: int
    csrc: tuple[int, ...]
    extension_profile: int | None
    extension_data: bytes | None
    payload: bytes
    padding_size: int = 0

    @classmethod
    def parse(
        cls,
        data: bytes,
        *,
        expected_payload_type: int | None = PCMU_PAYLOAD_TYPE,
    ) -> RtpPacket:
        if len(data) < _MINIMUM_HEADER_SIZE:
            raise RtpProtocolError(
                f"RTP packet is too short: expected at least 12 bytes, got {len(data)}."
            )

        first = data[0]
        second = data[1]
        version = first >> 6
        if version != RTP_VERSION:
            raise RtpProtocolError(f"Unsupported RTP version {version}; expected version 2.")

        has_padding = bool(first & 0x20)
        has_extension = bool(first & 0x10)
        csrc_count = first & 0x0F
        marker = bool(second & 0x80)
        payload_type = second & 0x7F
        if expected_payload_type is not None and payload_type != expected_payload_type:
            raise RtpProtocolError(
                f"Unsupported RTP payload type {payload_type}; "
                f"expected {expected_payload_type}."
            )

        sequence = int.from_bytes(data[2:4], "big")
        timestamp = int.from_bytes(data[4:8], "big")
        ssrc = int.from_bytes(data[8:12], "big")

        offset = _MINIMUM_HEADER_SIZE
        csrc_size = csrc_count * 4
        if len(data) < offset + csrc_size:
            raise RtpProtocolError("RTP packet is truncated in its CSRC list.")
        csrc = tuple(
            int.from_bytes(data[index : index + 4], "big")
            for index in range(offset, offset + csrc_size, 4)
        )
        offset += csrc_size

        extension_profile: int | None = None
        extension_data: bytes | None = None
        if has_extension:
            if len(data) < offset + 4:
                raise RtpProtocolError("RTP packet is truncated before its extension header.")
            extension_profile = int.from_bytes(data[offset : offset + 2], "big")
            extension_words = int.from_bytes(data[offset + 2 : offset + 4], "big")
            offset += 4
            extension_size = extension_words * 4
            if len(data) < offset + extension_size:
                raise RtpProtocolError("RTP packet is truncated in its extension data.")
            extension_data = data[offset : offset + extension_size]
            offset += extension_size

        padding_size = 0
        payload_end = len(data)
        if has_padding:
            padding_size = data[-1]
            if padding_size == 0:
                raise RtpProtocolError("RTP padding bit is set with a zero padding length.")
            if padding_size > len(data) - offset:
                raise RtpProtocolError("RTP padding extends beyond the packet payload.")
            payload_end -= padding_size

        if payload_end < offset:
            raise RtpProtocolError("RTP packet has an invalid payload boundary.")

        return cls(
            marker=marker,
            payload_type=payload_type,
            sequence=sequence,
            timestamp=timestamp,
            ssrc=ssrc,
            csrc=csrc,
            extension_profile=extension_profile,
            extension_data=extension_data,
            payload=data[offset:payload_end],
            padding_size=padding_size,
        )


@dataclass(frozen=True, slots=True)
class RtpSequenceObservation:
    """Result of comparing one RTP sequence number with prior packets."""

    sequence: int
    expected: int | None
    missing: int = 0
    duplicate: bool = False
    out_of_order: bool = False


class RtpSequenceTracker:
    """Track RTP sequence continuity, wraparound, duplicates, and late packets."""

    def __init__(self, *, history_size: int = _DEFAULT_SEQUENCE_HISTORY) -> None:
        if history_size <= 0:
            raise ValueError("RTP sequence history size must be greater than zero.")
        self.history_size = history_size
        self._last_sequence: int | None = None
        self._recent_sequences: deque[int] = deque()
        self._seen_sequences: set[int] = set()

    @property
    def last_sequence(self) -> int | None:
        return self._last_sequence

    def reset(self) -> None:
        self._last_sequence = None
        self._recent_sequences.clear()
        self._seen_sequences.clear()

    def observe(self, sequence: int) -> RtpSequenceObservation:
        if not 0 <= sequence < _SEQUENCE_MODULUS:
            raise ValueError("RTP sequence number must be between 0 and 65535.")

        previous = self._last_sequence
        if previous is None:
            self._last_sequence = sequence
            self._remember(sequence)
            return RtpSequenceObservation(sequence=sequence, expected=None)

        expected = (previous + 1) & 0xFFFF
        if sequence in self._seen_sequences:
            return RtpSequenceObservation(
                sequence=sequence,
                expected=expected,
                duplicate=True,
            )
        if sequence == expected:
            self._last_sequence = sequence
            self._remember(sequence)
            return RtpSequenceObservation(sequence=sequence, expected=expected)

        forward_distance = (sequence - expected) & 0xFFFF
        if forward_distance < 0x8000:
            self._last_sequence = sequence
            self._remember(sequence)
            return RtpSequenceObservation(
                sequence=sequence,
                expected=expected,
                missing=forward_distance,
            )

        self._remember(sequence)
        return RtpSequenceObservation(
            sequence=sequence,
            expected=expected,
            out_of_order=True,
        )

    def _remember(self, sequence: int) -> None:
        if sequence in self._seen_sequences:
            return
        self._recent_sequences.append(sequence)
        self._seen_sequences.add(sequence)
        while len(self._recent_sequences) > self.history_size:
            expired = self._recent_sequences.popleft()
            self._seen_sequences.discard(expired)


@dataclass(frozen=True, slots=True)
class RtpTimestampObservation:
    """Result of comparing an RTP timestamp with the expected PCMU clock."""

    timestamp: int
    expected: int | None
    missing_samples: int = 0
    backwards: bool = False


class RtpTimestampTracker:
    """Track the 32-bit RTP timestamp clock using payload sample counts."""

    def __init__(self) -> None:
        self._last_timestamp: int | None = None
        self._last_samples = 0

    @property
    def last_timestamp(self) -> int | None:
        return self._last_timestamp

    def reset(self) -> None:
        self._last_timestamp = None
        self._last_samples = 0

    def observe(self, timestamp: int, sample_count: int) -> RtpTimestampObservation:
        if not 0 <= timestamp < _TIMESTAMP_MODULUS:
            raise ValueError("RTP timestamp must be between 0 and 4294967295.")
        if sample_count < 0:
            raise ValueError("RTP sample count must not be negative.")

        previous = self._last_timestamp
        if previous is None:
            self._last_timestamp = timestamp
            self._last_samples = sample_count
            return RtpTimestampObservation(timestamp=timestamp, expected=None)

        expected = (previous + self._last_samples) & 0xFFFFFFFF
        delta = (timestamp - expected) & 0xFFFFFFFF
        backwards = delta >= 0x80000000
        missing_samples = 0 if delta == 0 or backwards else delta
        self._last_timestamp = timestamp
        self._last_samples = sample_count
        return RtpTimestampObservation(
            timestamp=timestamp,
            expected=expected,
            missing_samples=missing_samples,
            backwards=backwards,
        )
