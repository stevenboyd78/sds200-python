from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from .presentation import (
    ActivityStatus,
    AvailabilityStatus,
    present_radio_state,
)
from .state import RadioStateSnapshot

DEFAULT_REMOTE_STREAM_TITLE_MAX_LENGTH = 160

_SCANNING_TITLE = "Scanning"
_IDLE_TITLE = "Scanner idle"
_STALE_TITLE = "Scanner state stale"
_UNAVAILABLE_TITLE = "Scanner unavailable"


def _normalize_optional(label: str, value: str | None) -> str | None:
    if value is None:
        return None
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise ValueError(f"Remote stream metadata {label} contains control characters.")
    normalized = " ".join(value.split())
    return normalized or None


def _unique_title_parts(
    values: tuple[str | None, ...],
) -> tuple[str, ...]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value is None:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        output.append(value)
    return tuple(output)


@dataclass(frozen=True, slots=True)
class RemoteStreamMetadata:
    """Normalized renderer-neutral metadata derived from one scanner state."""

    activity: ActivityStatus
    availability: AvailabilityStatus
    system: str | None = None
    department: str | None = None
    site: str | None = None
    channel: str | None = None
    frequency: str | None = None
    service_type: str | None = None
    talkgroup_id: str | None = None
    unit_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.activity, ActivityStatus):
            raise TypeError(
                "Remote stream metadata activity must be an ActivityStatus."
            )
        if not isinstance(self.availability, AvailabilityStatus):
            raise TypeError(
                "Remote stream metadata availability must be an "
                "AvailabilityStatus."
            )

        for name in (
            "system",
            "department",
            "site",
            "channel",
            "frequency",
            "service_type",
            "talkgroup_id",
            "unit_id",
        ):
            value = getattr(self, name)
            object.__setattr__(
                self,
                name,
                _normalize_optional(name.replace("_", " "), value),
            )

    def render_title(
        self,
        *,
        max_length: int = DEFAULT_REMOTE_STREAM_TITLE_MAX_LENGTH,
    ) -> str:
        """Render one deterministic bounded title for remote stream services."""

        if (
            isinstance(max_length, bool)
            or not isinstance(max_length, int)
            or max_length < 1
        ):
            raise ValueError(
                "Remote stream metadata title maximum length must be positive."
            )

        final_component = self.channel or self.frequency
        parts = _unique_title_parts(
            (self.system, self.department, final_component)
        )

        if self.availability is AvailabilityStatus.UNAVAILABLE:
            title = _UNAVAILABLE_TITLE
        elif self.availability is AvailabilityStatus.STALE:
            title = _STALE_TITLE
        elif self.activity is ActivityStatus.SCANNING:
            title = _SCANNING_TITLE
        elif parts:
            title = " | ".join(parts)
        elif self.activity is ActivityStatus.IDLE:
            title = _IDLE_TITLE
        else:
            title = _UNAVAILABLE_TITLE

        bounded = title[:max_length].rstrip()
        if bounded.endswith("|"):
            bounded = bounded[:-1].rstrip()
        return bounded

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-compatible representation including the default title."""

        return {
            "title": self.render_title(),
            "activity": self.activity.value,
            "availability": self.availability.value,
            "system": self.system,
            "department": self.department,
            "site": self.site,
            "channel": self.channel,
            "frequency": self.frequency,
            "service_type": self.service_type,
            "talkgroup_id": self.talkgroup_id,
            "unit_id": self.unit_id,
        }


def remote_stream_metadata_from_state(
    snapshot: RadioStateSnapshot,
    *,
    connected: bool | None = None,
    degraded: bool = False,
    stale: bool = False,
) -> RemoteStreamMetadata:
    """Derive immutable normalized stream metadata from one scanner snapshot."""

    presentation = present_radio_state(
        snapshot,
        connected=connected,
        degraded=degraded,
        stale=stale,
    )
    return RemoteStreamMetadata(
        activity=presentation.activity,
        availability=presentation.availability,
        system=snapshot.system,
        department=snapshot.department,
        site=snapshot.site,
        channel=snapshot.channel,
        frequency=snapshot.frequency,
        service_type=snapshot.service_type,
        talkgroup_id=snapshot.talkgroup_id,
        unit_id=snapshot.unit_id,
    )
