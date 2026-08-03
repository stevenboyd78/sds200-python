from __future__ import annotations

import json
import os
import wave
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from .audio_recording import PCM_CHANNELS, PCM_SAMPLE_WIDTH, PCMU_SAMPLE_RATE
from .recording_metadata import (
    RECORDING_METADATA_SCHEMA,
    RECORDING_METADATA_VERSION,
    recording_metadata_path,
)


class RecordingAudioStatus(StrEnum):
    """Inventory classification for one expected WAV recording."""

    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"
    UNREADABLE = "unreadable"
    MISSING = "missing"


class RecordingMetadataStatus(StrEnum):
    """Inventory classification for one expected metadata sidecar."""

    MISSING = "missing"
    VALID = "valid"
    UNREADABLE = "unreadable"
    INVALID = "invalid"
    MISMATCHED = "mismatched"
    ORPHANED = "orphaned"


@dataclass(frozen=True, slots=True)
class RecordingInventoryEntry:
    """One read-only WAV-and-sidecar managed unit beneath an inventory root."""

    root: Path
    audio_path: Path
    metadata_path: Path
    audio_status: RecordingAudioStatus
    metadata_status: RecordingMetadataStatus
    recorded_at: datetime | None
    duration_seconds: float | None
    frames: int | None
    audio_size_bytes: int
    metadata_size_bytes: int
    modified_ns: int
    issue: str | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("audio_size_bytes", self.audio_size_bytes),
            ("metadata_size_bytes", self.metadata_size_bytes),
            ("modified_ns", self.modified_ns),
        ):
            if value < 0:
                raise ValueError(f"Recording inventory {name} cannot be negative.")
        if self.duration_seconds is not None and self.duration_seconds < 0:
            raise ValueError(
                "Recording inventory duration_seconds cannot be negative."
            )
        if self.frames is not None and self.frames < 0:
            raise ValueError("Recording inventory frames cannot be negative.")
        if self.recorded_at is not None and (
            self.recorded_at.tzinfo is None
            or self.recorded_at.utcoffset() is None
        ):
            raise ValueError("Recording inventory timestamps must be timezone-aware.")

    @property
    def relative_audio_path(self) -> Path:
        return self.audio_path.relative_to(self.root)

    @property
    def total_size_bytes(self) -> int:
        return self.audio_size_bytes + self.metadata_size_bytes

    @property
    def playable(self) -> bool:
        return self.audio_status is RecordingAudioStatus.COMPATIBLE

    @property
    def requires_attention(self) -> bool:
        return (
            self.audio_status is not RecordingAudioStatus.COMPATIBLE
            or self.metadata_status is not RecordingMetadataStatus.VALID
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "audio": str(self.relative_audio_path),
            "metadata": str(self.metadata_path.relative_to(self.root)),
            "audio_status": self.audio_status.value,
            "metadata_status": self.metadata_status.value,
            "recorded_at": (
                self.recorded_at.astimezone(UTC).isoformat()
                if self.recorded_at is not None
                else None
            ),
            "duration_seconds": self.duration_seconds,
            "frames": self.frames,
            "audio_size_bytes": self.audio_size_bytes,
            "metadata_size_bytes": self.metadata_size_bytes,
            "total_size_bytes": self.total_size_bytes,
            "modified_ns": self.modified_ns,
            "playable": self.playable,
            "requires_attention": self.requires_attention,
            "issue": self.issue,
        }


@dataclass(frozen=True, slots=True)
class RecordingInventorySummary:
    """Aggregate read-only statistics for one recording inventory."""

    managed_units: int = 0
    compatible_recordings: int = 0
    incompatible_recordings: int = 0
    unreadable_recordings: int = 0
    missing_recordings: int = 0
    recordings_without_metadata: int = 0
    valid_sidecars: int = 0
    invalid_sidecars: int = 0
    orphan_sidecars: int = 0
    attention_units: int = 0
    audio_bytes: int = 0
    metadata_bytes: int = 0
    total_bytes: int = 0
    compatible_duration_seconds: float = 0.0
    scan_issues: int = 0

    @classmethod
    def from_entries(
        cls,
        entries: tuple[RecordingInventoryEntry, ...],
        *,
        scan_issues: int = 0,
    ) -> RecordingInventorySummary:
        return cls(
            managed_units=len(entries),
            compatible_recordings=sum(
                entry.audio_status is RecordingAudioStatus.COMPATIBLE
                for entry in entries
            ),
            incompatible_recordings=sum(
                entry.audio_status is RecordingAudioStatus.INCOMPATIBLE
                for entry in entries
            ),
            unreadable_recordings=sum(
                entry.audio_status is RecordingAudioStatus.UNREADABLE
                for entry in entries
            ),
            missing_recordings=sum(
                entry.audio_status is RecordingAudioStatus.MISSING
                for entry in entries
            ),
            recordings_without_metadata=sum(
                entry.audio_status is not RecordingAudioStatus.MISSING
                and entry.metadata_status is RecordingMetadataStatus.MISSING
                for entry in entries
            ),
            valid_sidecars=sum(
                entry.metadata_status is RecordingMetadataStatus.VALID
                for entry in entries
            ),
            invalid_sidecars=sum(
                entry.metadata_status
                in {
                    RecordingMetadataStatus.UNREADABLE,
                    RecordingMetadataStatus.INVALID,
                    RecordingMetadataStatus.MISMATCHED,
                }
                for entry in entries
            ),
            orphan_sidecars=sum(
                entry.metadata_status is RecordingMetadataStatus.ORPHANED
                for entry in entries
            ),
            attention_units=sum(entry.requires_attention for entry in entries),
            audio_bytes=sum(entry.audio_size_bytes for entry in entries),
            metadata_bytes=sum(entry.metadata_size_bytes for entry in entries),
            total_bytes=sum(entry.total_size_bytes for entry in entries),
            compatible_duration_seconds=sum(
                entry.duration_seconds or 0.0
                for entry in entries
                if entry.audio_status is RecordingAudioStatus.COMPATIBLE
            ),
            scan_issues=scan_issues,
        )

    def as_dict(self) -> dict[str, int | float]:
        return {
            "managed_units": self.managed_units,
            "compatible_recordings": self.compatible_recordings,
            "incompatible_recordings": self.incompatible_recordings,
            "unreadable_recordings": self.unreadable_recordings,
            "missing_recordings": self.missing_recordings,
            "recordings_without_metadata": self.recordings_without_metadata,
            "valid_sidecars": self.valid_sidecars,
            "invalid_sidecars": self.invalid_sidecars,
            "orphan_sidecars": self.orphan_sidecars,
            "attention_units": self.attention_units,
            "audio_bytes": self.audio_bytes,
            "metadata_bytes": self.metadata_bytes,
            "total_bytes": self.total_bytes,
            "compatible_duration_seconds": self.compatible_duration_seconds,
            "scan_issues": self.scan_issues,
        }


@dataclass(frozen=True, slots=True)
class RecordingInventory:
    """Deterministic read-only inventory beneath one resolved recording root."""

    root: Path
    entries: tuple[RecordingInventoryEntry, ...]
    summary: RecordingInventorySummary
    issues: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "root": str(self.root),
            "summary": self.summary.as_dict(),
            "issues": list(self.issues),
            "entries": [entry.as_dict() for entry in self.entries],
        }


@dataclass(frozen=True, slots=True)
class _AudioInspection:
    status: RecordingAudioStatus
    recorded_at: datetime | None = None
    duration_seconds: float | None = None
    frames: int | None = None
    size_bytes: int = 0
    modified_ns: int = 0
    issue: str | None = None


@dataclass(frozen=True, slots=True)
class _MetadataInspection:
    status: RecordingMetadataStatus
    started_at: datetime | None = None
    size_bytes: int = 0
    modified_ns: int = 0
    issue: str | None = None


def _join_issues(*issues: str | None) -> str | None:
    retained = tuple(issue for issue in issues if issue)
    return "; ".join(retained) if retained else None


def _is_managed_artifact(path: Path) -> bool:
    name = path.name.casefold()
    return name.endswith(".wav") or name.endswith(".wav.json")


def _discover_artifacts(root: Path) -> tuple[tuple[Path, ...], tuple[str, ...]]:
    pending = [root]
    artifacts: list[Path] = []
    issues: list[str] = []

    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as handle:
                entries = sorted(
                    handle,
                    key=lambda entry: (entry.name.casefold(), entry.name),
                )
        except OSError as error:
            relative = directory.relative_to(root)
            issues.append(
                f"Could not scan directory {relative or Path('.')}: {error}"
            )
            continue

        for entry in entries:
            path = Path(entry.path)
            try:
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                    continue
                if entry.is_symlink():
                    if _is_managed_artifact(path):
                        artifacts.append(path)
                    continue
            except OSError as error:
                relative = path.relative_to(root)
                issues.append(f"Could not inspect {relative}: {error}")
                continue
            if _is_managed_artifact(path):
                artifacts.append(path)

    artifacts.sort(
        key=lambda path: (
            path.relative_to(root).as_posix().casefold(),
            path.relative_to(root).as_posix(),
        )
    )
    issues.sort(key=lambda issue: (issue.casefold(), issue))
    return tuple(artifacts), tuple(issues)


def _safe_target(path: Path, root: Path) -> tuple[Path | None, str | None]:
    try:
        target = path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        return None, f"Could not resolve {path.relative_to(root)}: {error}"

    try:
        target.relative_to(root)
    except ValueError:
        return (
            None,
            f"Managed artifact resolves outside inventory root: "
            f"{path.relative_to(root)}",
        )
    if not target.is_file():
        return None, f"Managed artifact is not a file: {path.relative_to(root)}"
    return target, None


def _link_statistics(path: Path) -> tuple[int, int]:
    try:
        statistics = path.lstat()
    except OSError:
        return 0, 0
    return statistics.st_size, statistics.st_mtime_ns


def _inspect_audio(path: Path, root: Path, *, exists: bool) -> _AudioInspection:
    if not exists:
        return _AudioInspection(
            status=RecordingAudioStatus.MISSING,
            issue="Recording audio file is missing.",
        )

    target, target_issue = _safe_target(path, root)
    if target is None:
        size_bytes, modified_ns = _link_statistics(path)
        return _AudioInspection(
            status=RecordingAudioStatus.UNREADABLE,
            size_bytes=size_bytes,
            modified_ns=modified_ns,
            issue=target_issue,
        )

    try:
        statistics = target.stat()
        with wave.open(str(target), "rb") as recording:
            channels = recording.getnchannels()
            sample_width = recording.getsampwidth()
            sample_rate = recording.getframerate()
            compression = recording.getcomptype()
            frames = recording.getnframes()
    except (OSError, EOFError, wave.Error) as error:
        try:
            statistics = target.stat()
        except OSError:
            size_bytes = 0
            modified_ns = 0
        else:
            size_bytes = statistics.st_size
            modified_ns = statistics.st_mtime_ns
        return _AudioInspection(
            status=RecordingAudioStatus.UNREADABLE,
            size_bytes=size_bytes,
            modified_ns=modified_ns,
            issue=f"Could not read WAV audio: {error}",
        )

    duration_seconds = frames / sample_rate if sample_rate > 0 else None
    recorded_at = datetime.fromtimestamp(statistics.st_mtime, tz=UTC)
    compatible = (
        channels == PCM_CHANNELS
        and sample_width == PCM_SAMPLE_WIDTH
        and sample_rate == PCMU_SAMPLE_RATE
        and compression == "NONE"
    )
    if compatible:
        return _AudioInspection(
            status=RecordingAudioStatus.COMPATIBLE,
            recorded_at=recorded_at,
            duration_seconds=duration_seconds,
            frames=frames,
            size_bytes=statistics.st_size,
            modified_ns=statistics.st_mtime_ns,
        )

    return _AudioInspection(
        status=RecordingAudioStatus.INCOMPATIBLE,
        recorded_at=recorded_at,
        duration_seconds=duration_seconds,
        frames=frames,
        size_bytes=statistics.st_size,
        modified_ns=statistics.st_mtime_ns,
        issue=(
            "Incompatible WAV format: expected 8 kHz mono signed 16-bit PCM; "
            f"received rate={sample_rate}, channels={channels}, "
            f"sample_width={sample_width}, compression={compression}."
        ),
    )


def _parse_started_at(payload: dict[str, object]) -> datetime:
    boundaries = payload.get("boundaries")
    if not isinstance(boundaries, dict):
        raise ValueError("metadata boundaries must be an object")
    started = boundaries.get("started")
    if not isinstance(started, dict):
        raise ValueError("metadata started boundary must be an object")
    value = started.get("at")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("metadata started boundary timestamp must be a string")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        observed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise ValueError("metadata started boundary timestamp is invalid") from error
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ValueError("metadata started boundary timestamp must be timezone-aware")
    return observed.astimezone(UTC)


def _validate_metadata_payload(
    payload: object,
    *,
    audio_name: str,
) -> tuple[RecordingMetadataStatus, datetime | None, str | None]:
    if not isinstance(payload, dict):
        return (
            RecordingMetadataStatus.INVALID,
            None,
            "Recording metadata root must be an object.",
        )
    if payload.get("schema") != RECORDING_METADATA_SCHEMA:
        return (
            RecordingMetadataStatus.INVALID,
            None,
            "Recording metadata schema is unsupported.",
        )
    version = payload.get("version")
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or version != RECORDING_METADATA_VERSION
    ):
        return (
            RecordingMetadataStatus.INVALID,
            None,
            "Recording metadata version is unsupported.",
        )
    recording = payload.get("recording")
    if not isinstance(recording, dict):
        return (
            RecordingMetadataStatus.INVALID,
            None,
            "Recording metadata recording section must be an object.",
        )
    recorded_file = recording.get("file")
    if not isinstance(recorded_file, str) or not recorded_file:
        return (
            RecordingMetadataStatus.INVALID,
            None,
            "Recording metadata file name must be a string.",
        )
    try:
        started_at = _parse_started_at(payload)
    except ValueError as error:
        return RecordingMetadataStatus.INVALID, None, str(error)
    if recorded_file != audio_name:
        return (
            RecordingMetadataStatus.MISMATCHED,
            None,
            f"Recording metadata names {recorded_file!r}, expected {audio_name!r}.",
        )
    return RecordingMetadataStatus.VALID, started_at, None


def _inspect_metadata(
    path: Path,
    root: Path,
    *,
    exists: bool,
    audio_exists: bool,
    audio_name: str,
) -> _MetadataInspection:
    if not exists:
        return _MetadataInspection(
            status=RecordingMetadataStatus.MISSING,
            issue="Recording metadata sidecar is missing.",
        )

    target, target_issue = _safe_target(path, root)
    if target is None:
        size_bytes, modified_ns = _link_statistics(path)
        status = (
            RecordingMetadataStatus.ORPHANED
            if not audio_exists
            else RecordingMetadataStatus.UNREADABLE
        )
        return _MetadataInspection(
            status=status,
            size_bytes=size_bytes,
            modified_ns=modified_ns,
            issue=_join_issues(
                "Metadata sidecar has no adjacent WAV recording."
                if not audio_exists
                else None,
                target_issue,
            ),
        )

    try:
        statistics = target.stat()
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        try:
            statistics = target.stat()
        except OSError:
            size_bytes = 0
            modified_ns = 0
        else:
            size_bytes = statistics.st_size
            modified_ns = statistics.st_mtime_ns
        status = (
            RecordingMetadataStatus.ORPHANED
            if not audio_exists
            else RecordingMetadataStatus.UNREADABLE
        )
        return _MetadataInspection(
            status=status,
            size_bytes=size_bytes,
            modified_ns=modified_ns,
            issue=_join_issues(
                "Metadata sidecar has no adjacent WAV recording."
                if not audio_exists
                else None,
                f"Could not read recording metadata: {error}",
            ),
        )

    base_status: RecordingMetadataStatus
    started_at: datetime | None
    issue: str | None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        base_status = RecordingMetadataStatus.INVALID
        started_at = None
        issue = f"Recording metadata JSON is invalid: {error.msg}."
    else:
        base_status, started_at, issue = _validate_metadata_payload(
            payload,
            audio_name=audio_name,
        )

    if not audio_exists:
        return _MetadataInspection(
            status=RecordingMetadataStatus.ORPHANED,
            started_at=(
                started_at
                if base_status is RecordingMetadataStatus.VALID
                else None
            ),
            size_bytes=statistics.st_size,
            modified_ns=statistics.st_mtime_ns,
            issue=_join_issues(
                "Metadata sidecar has no adjacent WAV recording.",
                issue,
            ),
        )

    return _MetadataInspection(
        status=base_status,
        started_at=started_at,
        size_bytes=statistics.st_size,
        modified_ns=statistics.st_mtime_ns,
        issue=issue,
    )


def scan_recording_inventory(root: str | Path) -> RecordingInventory:
    """Recursively inspect managed recording artifacts without mutating them."""

    inventory_root = Path(root).expanduser().resolve()
    if not inventory_root.is_dir():
        raise NotADirectoryError(
            f"Recording inventory root is not a directory: {inventory_root}"
        )

    artifacts, scan_issues = _discover_artifacts(inventory_root)
    units: dict[Path, dict[str, Path]] = {}
    for artifact in artifacts:
        relative = artifact.relative_to(inventory_root)
        if relative.name.casefold().endswith(".wav.json"):
            audio_relative = relative.with_name(relative.name[:-5])
            units.setdefault(audio_relative, {})["metadata"] = artifact
        else:
            units.setdefault(relative, {})["audio"] = artifact

    ordered_units = sorted(
        units.items(),
        key=lambda item: (
            item[0].as_posix().casefold(),
            item[0].as_posix(),
        ),
    )
    entries: list[RecordingInventoryEntry] = []
    for audio_relative, artifacts_for_unit in ordered_units:
        audio_path = artifacts_for_unit.get("audio", inventory_root / audio_relative)
        expected_metadata_path = recording_metadata_path(inventory_root / audio_relative)
        metadata_path = artifacts_for_unit.get("metadata", expected_metadata_path)
        audio_exists = "audio" in artifacts_for_unit
        metadata_exists = "metadata" in artifacts_for_unit

        audio = _inspect_audio(audio_path, inventory_root, exists=audio_exists)
        metadata = _inspect_metadata(
            metadata_path,
            inventory_root,
            exists=metadata_exists,
            audio_exists=audio_exists,
            audio_name=audio_path.name,
        )
        recorded_at = (
            metadata.started_at
            if metadata.status
            in {
                RecordingMetadataStatus.VALID,
                RecordingMetadataStatus.ORPHANED,
            }
            and metadata.started_at is not None
            else audio.recorded_at
        )
        entries.append(
            RecordingInventoryEntry(
                root=inventory_root,
                audio_path=audio_path,
                metadata_path=metadata_path,
                audio_status=audio.status,
                metadata_status=metadata.status,
                recorded_at=recorded_at,
                duration_seconds=audio.duration_seconds,
                frames=audio.frames,
                audio_size_bytes=audio.size_bytes,
                metadata_size_bytes=metadata.size_bytes,
                modified_ns=max(audio.modified_ns, metadata.modified_ns),
                issue=_join_issues(audio.issue, metadata.issue),
            )
        )

    frozen_entries = tuple(entries)
    summary = RecordingInventorySummary.from_entries(
        frozen_entries,
        scan_issues=len(scan_issues),
    )
    return RecordingInventory(
        root=inventory_root,
        entries=frozen_entries,
        summary=summary,
        issues=scan_issues,
    )
