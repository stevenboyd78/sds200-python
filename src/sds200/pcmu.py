from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

_SEQUENCE_MAX = (1 << 16) - 1
_TIMESTAMP_MAX = (1 << 32) - 1
_SSRC_MAX = (1 << 32) - 1


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _require_integer(
    value: object,
    *,
    description: str,
    minimum: int,
    maximum: int | None = None,
) -> int:
    if type(value) is not int:
        raise TypeError(f"{description} must be an integer.")
    if value < minimum:
        raise ValueError(f"{description} must be at least {minimum}.")
    if maximum is not None and value > maximum:
        raise ValueError(f"{description} must not exceed {maximum}.")
    return value


def _require_optional_integer(
    value: object,
    *,
    description: str,
    minimum: int,
    maximum: int,
) -> int | None:
    if value is None:
        return None
    return _require_integer(
        value,
        description=description,
        minimum=minimum,
        maximum=maximum,
    )


@dataclass(frozen=True, slots=True)
class PcmuPacket:
    """One accepted SDS200 RTP PCMU packet and its continuity observations."""

    endpoint: str
    sequence: int
    timestamp: int
    ssrc: int
    payload: bytes
    observed_at: datetime = field(default_factory=_utc_now)
    marker: bool = False
    expected_sequence: int | None = None
    missing_packets: int = 0
    expected_timestamp: int | None = None
    missing_samples: int = 0
    timestamp_backwards: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, str):
            raise TypeError("PCMU packet endpoint must be a string.")
        if not self.endpoint.strip():
            raise ValueError("PCMU packet endpoint must not be empty.")

        _require_integer(
            self.sequence,
            description="PCMU packet sequence",
            minimum=0,
            maximum=_SEQUENCE_MAX,
        )
        _require_integer(
            self.timestamp,
            description="PCMU packet timestamp",
            minimum=0,
            maximum=_TIMESTAMP_MAX,
        )
        _require_integer(
            self.ssrc,
            description="PCMU packet SSRC",
            minimum=0,
            maximum=_SSRC_MAX,
        )

        if not isinstance(self.payload, bytes):
            raise TypeError("PCMU packet payload must be bytes.")
        if not isinstance(self.observed_at, datetime):
            raise TypeError("PCMU packet observation time must be a datetime.")
        if (
            self.observed_at.tzinfo is None
            or self.observed_at.utcoffset() is None
        ):
            raise ValueError(
                "PCMU packet observation time must be timezone-aware."
            )
        if type(self.marker) is not bool:
            raise TypeError("PCMU packet marker flag must be a boolean.")
        if type(self.timestamp_backwards) is not bool:
            raise TypeError(
                "PCMU packet timestamp-backwards flag must be a boolean."
            )

        expected_sequence = _require_optional_integer(
            self.expected_sequence,
            description="PCMU packet expected sequence",
            minimum=0,
            maximum=_SEQUENCE_MAX,
        )
        missing_packets = _require_integer(
            self.missing_packets,
            description="PCMU packet missing-packet count",
            minimum=0,
        )
        expected_timestamp = _require_optional_integer(
            self.expected_timestamp,
            description="PCMU packet expected timestamp",
            minimum=0,
            maximum=_TIMESTAMP_MAX,
        )
        missing_samples = _require_integer(
            self.missing_samples,
            description="PCMU packet missing-sample count",
            minimum=0,
        )

        if missing_packets and expected_sequence is None:
            raise ValueError(
                "PCMU packet loss requires an expected sequence."
            )
        if (
            missing_samples or self.timestamp_backwards
        ) and expected_timestamp is None:
            raise ValueError(
                "PCMU timestamp discontinuity requires an expected timestamp."
            )
        if missing_samples and self.timestamp_backwards:
            raise ValueError(
                "PCMU timestamp loss and backwards movement are mutually exclusive."
            )

    @property
    def sample_count(self) -> int:
        return len(self.payload)

    @property
    def sequence_discontinuity(self) -> bool:
        return self.missing_packets > 0

    @property
    def timestamp_discontinuity(self) -> bool:
        return self.missing_samples > 0 or self.timestamp_backwards

    @property
    def discontinuous(self) -> bool:
        return self.sequence_discontinuity or self.timestamp_discontinuity


PcmuPacketHandler = Callable[[PcmuPacket], None]
