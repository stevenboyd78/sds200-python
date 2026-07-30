from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from .audio_recording import PCM_CHANNELS, PCM_SAMPLE_WIDTH, PCMU_SAMPLE_RATE
from .audio_session import AudioReliabilitySnapshot, AudioSessionSnapshot
from .state import RadioStateSnapshot

RECORDING_METADATA_SCHEMA = "sds200.recording-metadata"
RECORDING_METADATA_VERSION = 1


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware.")


def _utc_isoformat(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class RecordingSource:
    """Scanner identity associated with one recording."""

    endpoint: str
    scanner: str | None = None

    def __post_init__(self) -> None:
        if not self.endpoint.strip():
            raise ValueError("Recording source endpoint must not be empty.")
        if self.scanner is not None and not self.scanner.strip():
            raise ValueError("Recording source scanner must not be empty.")

    def as_dict(self) -> dict[str, str]:
        payload = {"endpoint": self.endpoint}
        if self.scanner is not None:
            payload["scanner"] = self.scanner
        return payload


@dataclass(frozen=True, slots=True)
class RecordingState:
    """Channel-oriented scanner state captured at a recording boundary."""

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

    @classmethod
    def from_radio_state(cls, snapshot: RadioStateSnapshot | None) -> RecordingState:
        if snapshot is None:
            return cls()
        return cls(
            mode=snapshot.mode,
            system=snapshot.system,
            department=snapshot.department,
            site=snapshot.site,
            channel=snapshot.channel,
            frequency=snapshot.frequency,
            modulation=snapshot.modulation,
            service_type=snapshot.service_type,
            talkgroup_id=snapshot.talkgroup_id,
            unit_id=snapshot.unit_id,
        )

    def as_dict(self) -> dict[str, str]:
        return {
            name: value
            for name, value in (
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
            )
            if value is not None
        }


@dataclass(frozen=True, slots=True)
class RecordingMetadata:
    """Versioned metadata for one finalized PCM WAV recording."""

    recording_path: Path
    source: RecordingSource
    started_at: datetime
    stopped_at: datetime
    elapsed_seconds: float
    packets: int
    samples: int
    audio_duration_seconds: float
    reliability: AudioReliabilitySnapshot
    started_state: RecordingState = field(default_factory=RecordingState)
    stopped_state: RecordingState = field(default_factory=RecordingState)
    error: str | None = None

    def __post_init__(self) -> None:
        if not self.recording_path.name:
            raise ValueError("Recording path must identify a file.")
        if self.recording_path.suffix.casefold() != ".wav":
            raise ValueError("Recording metadata requires a .wav recording path.")
        _require_aware(self.started_at, "Recording start time")
        _require_aware(self.stopped_at, "Recording stop time")
        if self.stopped_at < self.started_at:
            raise ValueError("Recording stop time cannot precede its start time.")
        for name, value in (
            ("elapsed_seconds", self.elapsed_seconds),
            ("packets", self.packets),
            ("samples", self.samples),
            ("audio_duration_seconds", self.audio_duration_seconds),
        ):
            if value < 0:
                raise ValueError(f"Recording {name} cannot be negative.")

    @classmethod
    def from_snapshots(
        cls,
        started: AudioSessionSnapshot,
        stopped: AudioSessionSnapshot,
        *,
        scanner: str | None = None,
        started_state: RadioStateSnapshot | None = None,
        stopped_state: RadioStateSnapshot | None = None,
    ) -> RecordingMetadata:
        """Build finalized metadata from recording and radio-state boundaries."""

        if started.endpoint != stopped.endpoint:
            raise ValueError("Recording snapshots must use the same endpoint.")
        if started.output_path != stopped.output_path:
            raise ValueError("Recording snapshots must use the same output path.")
        started_at = started.started_at
        stopped_at = stopped.stopped_at
        if started_at is None:
            raise ValueError("Started recording snapshot has no start time.")
        if stopped_at is None:
            raise ValueError("Stopped recording snapshot has no stop time.")

        return cls(
            recording_path=stopped.output_path,
            source=RecordingSource(endpoint=stopped.endpoint, scanner=scanner),
            started_at=started_at,
            stopped_at=stopped_at,
            elapsed_seconds=stopped.elapsed_seconds,
            packets=stopped.packets,
            samples=stopped.samples,
            audio_duration_seconds=stopped.audio_duration_seconds,
            reliability=stopped.reliability,
            started_state=RecordingState.from_radio_state(started_state),
            stopped_state=RecordingState.from_radio_state(stopped_state),
            error=stopped.error,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": RECORDING_METADATA_SCHEMA,
            "version": RECORDING_METADATA_VERSION,
            "recording": {
                "file": self.recording_path.name,
                "format": "wav",
                "sample_rate_hz": PCMU_SAMPLE_RATE,
                "channels": PCM_CHANNELS,
                "sample_width_bytes": PCM_SAMPLE_WIDTH,
            },
            "source": self.source.as_dict(),
            "boundaries": {
                "started": {
                    "at": _utc_isoformat(self.started_at),
                    "state": self.started_state.as_dict(),
                },
                "stopped": {
                    "at": _utc_isoformat(self.stopped_at),
                    "state": self.stopped_state.as_dict(),
                },
            },
            "statistics": {
                "elapsed_seconds": self.elapsed_seconds,
                "packets": self.packets,
                "samples": self.samples,
                "audio_duration_seconds": self.audio_duration_seconds,
                "reliability": self.reliability.as_dict(),
            },
            "error": self.error,
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), indent=2, sort_keys=True) + "\n"


def recording_metadata_path(recording_path: str | Path) -> Path:
    """Return the adjacent, unambiguous sidecar path for a recording."""

    path = Path(recording_path)
    if not path.name:
        raise ValueError("Recording path must identify a file.")
    return path.with_name(f"{path.name}.json")


def write_recording_metadata(
    metadata: RecordingMetadata,
    path: str | Path | None = None,
    *,
    overwrite: bool = False,
) -> Path:
    """Atomically write metadata without replacing an existing sidecar by default."""

    target = recording_metadata_path(metadata.recording_path) if path is None else Path(path)
    if not target.name:
        raise ValueError("Recording metadata path must identify a file.")
    target.parent.mkdir(parents=True, exist_ok=True)

    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(metadata.to_json())
            handle.flush()
            os.fsync(handle.fileno())

        assert temporary is not None
        if overwrite:
            os.replace(temporary, target)
            temporary = None
        else:
            os.link(temporary, target)
            temporary.unlink()
            temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

    return target
