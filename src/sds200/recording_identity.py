from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from .recording_metadata import RecordingMetadata

DEFAULT_RECORDING_COMPONENT = "unknown"
MAX_RECORDING_COMPONENT_LENGTH = 80

_WINDOWS_RESERVED_NAMES: Final[frozenset[str]] = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware.")


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _prefer_started(started: str | None, stopped: str | None) -> str | None:
    return _clean_optional(started) or _clean_optional(stopped)


def _normalized_component(value: str) -> str:
    output: list[str] = []
    separator_pending = False

    for character in unicodedata.normalize("NFKC", value.strip()):
        if character.isalnum():
            if separator_pending and output:
                output.append("-")
            output.append(character)
            separator_pending = False
        elif output:
            separator_pending = True

    return "".join(output)


def safe_recording_component(
    value: str | None,
    *,
    fallback: str = DEFAULT_RECORDING_COMPONENT,
    max_length: int = MAX_RECORDING_COMPONENT_LENGTH,
) -> str:
    """Return one deterministic portable filename or directory component."""

    if isinstance(max_length, bool) or max_length < 1:
        raise ValueError("Recording component maximum length must be positive.")

    component = _normalized_component(value or "")
    if not component:
        component = _normalized_component(fallback)
    if not component:
        raise ValueError("Recording component fallback must produce a usable name.")

    component = component[:max_length].rstrip("-")
    if not component:
        raise ValueError("Recording component maximum length produced an empty name.")

    if component.upper() in _WINDOWS_RESERVED_NAMES:
        component = f"recording-{component}"[:max_length].rstrip("-")

    return component


def _utc_timestamp(value: datetime) -> str:
    observed = value.astimezone(UTC)
    timestamp = observed.strftime("%Y%m%dT%H%M%S")
    if observed.microsecond:
        timestamp = f"{timestamp}-{observed.microsecond:06d}"
    return f"{timestamp}Z"


@dataclass(frozen=True, slots=True)
class RecordingIdentity:
    """Stable recording identity derived from immutable finalized metadata."""

    started_at: datetime
    stopped_at: datetime
    endpoint: str
    scanner: str | None = None
    mode: str | None = None
    system: str | None = None
    department: str | None = None
    site: str | None = None
    channel: str | None = None
    frequency: str | None = None
    modulation: str | None = None
    service_type: str | None = None
    talkgroup_id: str | None = None
    unit_id: str | None = None

    def __post_init__(self) -> None:
        _require_aware(self.started_at, "Recording identity start time")
        _require_aware(self.stopped_at, "Recording identity stop time")
        if self.stopped_at < self.started_at:
            raise ValueError("Recording identity stop time cannot precede its start time.")
        if not self.endpoint.strip():
            raise ValueError("Recording identity endpoint must not be empty.")

        for name, value in (
            ("scanner", self.scanner),
            ("mode", self.mode),
            ("system", self.system),
            ("department", self.department),
            ("site", self.site),
            ("channel", self.channel),
            ("frequency", self.frequency),
            ("modulation", self.modulation),
            ("service_type", self.service_type),
            ("talkgroup_id", self.talkgroup_id),
            ("unit_id", self.unit_id),
        ):
            if value is not None and not value.strip():
                raise ValueError(f"Recording identity {name} must not be empty when provided.")

    @classmethod
    def from_metadata(cls, metadata: RecordingMetadata) -> RecordingIdentity:
        """Prefer start-boundary state and fill absent fields from the stop boundary."""

        started = metadata.started_state
        stopped = metadata.stopped_state
        return cls(
            started_at=metadata.started_at,
            stopped_at=metadata.stopped_at,
            endpoint=metadata.source.endpoint.strip(),
            scanner=_clean_optional(metadata.source.scanner),
            mode=_prefer_started(started.mode, stopped.mode),
            system=_prefer_started(started.system, stopped.system),
            department=_prefer_started(started.department, stopped.department),
            site=_prefer_started(started.site, stopped.site),
            channel=_prefer_started(started.channel, stopped.channel),
            frequency=_prefer_started(started.frequency, stopped.frequency),
            modulation=_prefer_started(started.modulation, stopped.modulation),
            service_type=_prefer_started(
                started.service_type,
                stopped.service_type,
            ),
            talkgroup_id=_prefer_started(
                started.talkgroup_id,
                stopped.talkgroup_id,
            ),
            unit_id=_prefer_started(started.unit_id, stopped.unit_id),
        )

    @property
    def components(self) -> dict[str, str | None]:
        """Return raw organization components without including the current path."""

        return {
            "date": self.started_at.astimezone(UTC).strftime("%Y-%m-%d"),
            "timestamp": _utc_timestamp(self.started_at),
            "scanner": self.scanner,
            "endpoint": self.endpoint,
            "mode": self.mode,
            "system": self.system,
            "department": self.department,
            "site": self.site,
            "channel": self.channel,
            "frequency": self.frequency,
            "modulation": self.modulation,
            "service_type": self.service_type,
            "talkgroup_id": self.talkgroup_id,
            "unit_id": self.unit_id,
        }

    def filename_components(
        self,
        *,
        fallback: str = DEFAULT_RECORDING_COMPONENT,
        max_length: int = MAX_RECORDING_COMPONENT_LENGTH,
    ) -> dict[str, str]:
        """Return portable components suitable for later path-policy rendering."""

        return {
            name: safe_recording_component(
                value,
                fallback=fallback,
                max_length=max_length,
            )
            for name, value in self.components.items()
        }
