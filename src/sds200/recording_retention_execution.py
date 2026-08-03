from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .recording_inventory import (
    RecordingAudioStatus,
    RecordingInventoryEntry,
    RecordingMetadataStatus,
    scan_recording_inventory,
)
from .recording_metadata import recording_metadata_path
from .recording_retention import (
    RecordingRetentionDecision,
    RecordingRetentionDisposition,
    RecordingRetentionPlan,
)


class RecordingRetentionConfirmationError(ValueError):
    """Raised when an execution request does not confirm the exact plan."""


class RecordingRetentionExecutionStatus(StrEnum):
    """Outcome for one selected managed recording unit."""

    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"


class RecordingRetentionExecutionReason(StrEnum):
    """Deterministic explanation for one execution outcome."""

    COMPLETED = "completed"
    ROOT_UNAVAILABLE = "root_unavailable"
    INVALID_MANAGED_PATH = "invalid_managed_path"
    PATH_OUTSIDE_ROOT = "path_outside_root"
    SYMLINK = "symlink"
    UNEXPECTED_FILE_TYPE = "unexpected_file_type"
    STALE_PLAN = "stale_plan"
    DELETE_FAILED = "delete_failed"


@dataclass(frozen=True, slots=True)
class RecordingRetentionExecutionEntry:
    """Immutable execution outcome for one selected retention decision."""

    decision: RecordingRetentionDecision
    status: RecordingRetentionExecutionStatus
    reason: RecordingRetentionExecutionReason
    audio_deleted: bool = False
    metadata_deleted: bool = False
    deleted_bytes: int = 0
    message: str | None = None

    def __post_init__(self) -> None:
        if self.decision.disposition is not RecordingRetentionDisposition.SELECT:
            raise ValueError(
                "Recording retention execution entries require select decisions."
            )
        if self.deleted_bytes < 0:
            raise ValueError("Recording retention deleted bytes cannot be negative.")
        if self.status is RecordingRetentionExecutionStatus.COMPLETED:
            if self.reason is not RecordingRetentionExecutionReason.COMPLETED:
                raise ValueError(
                    "Completed retention execution requires the completed reason."
                )
            if not self.audio_deleted:
                raise ValueError(
                    "Completed retention execution must delete the WAV recording."
                )
        elif self.reason is RecordingRetentionExecutionReason.COMPLETED:
            raise ValueError(
                "Incomplete retention execution cannot use the completed reason."
            )
        if (
            self.status is RecordingRetentionExecutionStatus.SKIPPED
            and (self.audio_deleted or self.metadata_deleted or self.deleted_bytes)
        ):
            raise ValueError("Skipped retention execution cannot report deletion.")

    @property
    def entry(self) -> RecordingInventoryEntry:
        return self.decision.entry

    def as_dict(self) -> dict[str, object]:
        return {
            "audio": str(self.entry.relative_audio_path),
            "status": self.status.value,
            "reason": self.reason.value,
            "selection_reasons": [
                reason.value for reason in self.decision.reasons
            ],
            "audio_deleted": self.audio_deleted,
            "metadata_deleted": self.metadata_deleted,
            "deleted_bytes": self.deleted_bytes,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class RecordingRetentionExecutionSummary:
    """Aggregate immutable results for one confirmed execution request."""

    selected_units: int
    selected_bytes: int
    attempted_units: int
    completed_units: int
    skipped_units: int
    failed_units: int
    audio_files_deleted: int
    metadata_files_deleted: int
    deleted_bytes: int
    all_completed: bool

    def as_dict(self) -> dict[str, int | bool]:
        return {
            "selected_units": self.selected_units,
            "selected_bytes": self.selected_bytes,
            "attempted_units": self.attempted_units,
            "completed_units": self.completed_units,
            "skipped_units": self.skipped_units,
            "failed_units": self.failed_units,
            "audio_files_deleted": self.audio_files_deleted,
            "metadata_files_deleted": self.metadata_files_deleted,
            "deleted_bytes": self.deleted_bytes,
            "all_completed": self.all_completed,
        }


@dataclass(frozen=True, slots=True)
class RecordingRetentionExecutionResult:
    """Complete deterministic report for one confirmed retention execution."""

    confirmation_token: str
    entries: tuple[RecordingRetentionExecutionEntry, ...]
    summary: RecordingRetentionExecutionSummary

    @property
    def completed(self) -> tuple[RecordingRetentionExecutionEntry, ...]:
        return tuple(
            entry
            for entry in self.entries
            if entry.status is RecordingRetentionExecutionStatus.COMPLETED
        )

    @property
    def skipped(self) -> tuple[RecordingRetentionExecutionEntry, ...]:
        return tuple(
            entry
            for entry in self.entries
            if entry.status is RecordingRetentionExecutionStatus.SKIPPED
        )

    @property
    def failed(self) -> tuple[RecordingRetentionExecutionEntry, ...]:
        return tuple(
            entry
            for entry in self.entries
            if entry.status is RecordingRetentionExecutionStatus.FAILED
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "confirmation_token": self.confirmation_token,
            "summary": self.summary.as_dict(),
            "entries": [entry.as_dict() for entry in self.entries],
        }


@dataclass(frozen=True, slots=True)
class _ValidationProblem:
    reason: RecordingRetentionExecutionReason
    message: str


@dataclass(frozen=True, slots=True)
class _ValidatedUnit:
    decision: RecordingRetentionDecision
    audio_stat: os.stat_result
    metadata_stat: os.stat_result | None


def recording_retention_confirmation_token(
    plan: RecordingRetentionPlan,
) -> str:
    """Return the exact confirmation token bound to a retention plan."""

    payload = json.dumps(
        plan.as_dict(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    return f"delete:{digest}"


def _root_problem(root: Path) -> _ValidationProblem | None:
    if not root.is_absolute():
        return _ValidationProblem(
            RecordingRetentionExecutionReason.INVALID_MANAGED_PATH,
            "Recording inventory root must be absolute.",
        )
    try:
        root_stat = root.lstat()
    except OSError as error:
        return _ValidationProblem(
            RecordingRetentionExecutionReason.ROOT_UNAVAILABLE,
            f"Could not inspect recording inventory root: {error}",
        )
    if stat.S_ISLNK(root_stat.st_mode):
        return _ValidationProblem(
            RecordingRetentionExecutionReason.SYMLINK,
            "Recording inventory root must not be a symlink.",
        )
    if not stat.S_ISDIR(root_stat.st_mode):
        return _ValidationProblem(
            RecordingRetentionExecutionReason.ROOT_UNAVAILABLE,
            "Recording inventory root is not a directory.",
        )
    try:
        resolved = root.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        return _ValidationProblem(
            RecordingRetentionExecutionReason.ROOT_UNAVAILABLE,
            f"Could not resolve recording inventory root: {error}",
        )
    if resolved != root:
        return _ValidationProblem(
            RecordingRetentionExecutionReason.STALE_PLAN,
            "Recording inventory root changed after planning.",
        )
    return None


def _relative_path(
    path: Path,
    root: Path,
) -> tuple[Path | None, _ValidationProblem | None]:
    if not path.is_absolute():
        return None, _ValidationProblem(
            RecordingRetentionExecutionReason.INVALID_MANAGED_PATH,
            f"Managed artifact path is not absolute: {path}",
        )
    try:
        relative = path.relative_to(root)
    except ValueError:
        return None, _ValidationProblem(
            RecordingRetentionExecutionReason.PATH_OUTSIDE_ROOT,
            f"Managed artifact is outside the inventory root: {path}",
        )
    if (
        not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
        or root / relative != path
    ):
        return None, _ValidationProblem(
            RecordingRetentionExecutionReason.PATH_OUTSIDE_ROOT,
            f"Managed artifact path escapes the inventory root: {path}",
        )
    return relative, None


def _parent_problem(path: Path, root: Path) -> _ValidationProblem | None:
    relative, problem = _relative_path(path, root)
    if problem is not None:
        return problem
    assert relative is not None

    current = root
    for component in relative.parts[:-1]:
        current /= component
        try:
            current_stat = current.lstat()
        except OSError as error:
            return _ValidationProblem(
                RecordingRetentionExecutionReason.STALE_PLAN,
                f"Managed artifact parent changed after planning: {error}",
            )
        if stat.S_ISLNK(current_stat.st_mode):
            return _ValidationProblem(
                RecordingRetentionExecutionReason.SYMLINK,
                f"Managed artifact parent is a symlink: {current}",
            )
        if not stat.S_ISDIR(current_stat.st_mode):
            return _ValidationProblem(
                RecordingRetentionExecutionReason.UNEXPECTED_FILE_TYPE,
                f"Managed artifact parent is not a directory: {current}",
            )
    return None


def _structure_problem(
    decision: RecordingRetentionDecision,
    plan: RecordingRetentionPlan,
) -> _ValidationProblem | None:
    entry = decision.entry
    root = plan.inventory.root
    if decision.disposition is not RecordingRetentionDisposition.SELECT:
        return _ValidationProblem(
            RecordingRetentionExecutionReason.INVALID_MANAGED_PATH,
            "Executor received a decision that was not selected.",
        )
    if entry not in plan.inventory.entries:
        return _ValidationProblem(
            RecordingRetentionExecutionReason.INVALID_MANAGED_PATH,
            "Selected entry is not part of the confirmed inventory.",
        )
    if entry.root != root:
        return _ValidationProblem(
            RecordingRetentionExecutionReason.INVALID_MANAGED_PATH,
            "Selected entry uses a different inventory root.",
        )
    if entry.audio_status not in {
        RecordingAudioStatus.COMPATIBLE,
        RecordingAudioStatus.INCOMPATIBLE,
    }:
        return _ValidationProblem(
            RecordingRetentionExecutionReason.STALE_PLAN,
            "Selected entry no longer represents eligible audio.",
        )
    if entry.metadata_status not in {
        RecordingMetadataStatus.VALID,
        RecordingMetadataStatus.MISSING,
    }:
        return _ValidationProblem(
            RecordingRetentionExecutionReason.STALE_PLAN,
            "Selected entry no longer represents eligible metadata.",
        )
    if entry.recorded_at is None:
        return _ValidationProblem(
            RecordingRetentionExecutionReason.STALE_PLAN,
            "Selected entry has no reliable recording timestamp.",
        )

    _, problem = _relative_path(entry.audio_path, root)
    if problem is not None:
        return problem
    if entry.audio_path.suffix.casefold() != ".wav":
        return _ValidationProblem(
            RecordingRetentionExecutionReason.INVALID_MANAGED_PATH,
            f"Selected audio path is not a WAV recording: {entry.audio_path}",
        )
    expected_metadata = recording_metadata_path(entry.audio_path)
    if entry.metadata_path != expected_metadata:
        return _ValidationProblem(
            RecordingRetentionExecutionReason.INVALID_MANAGED_PATH,
            "Selected metadata path is not adjacent to its WAV recording.",
        )
    _, problem = _relative_path(entry.metadata_path, root)
    if problem is not None:
        return problem

    problem = _parent_problem(entry.audio_path, root)
    if problem is not None:
        return problem
    return _parent_problem(entry.metadata_path, root)


def _artifact_stat(
    path: Path,
    *,
    expected_size: int,
    missing_allowed: bool = False,
) -> tuple[os.stat_result | None, _ValidationProblem | None]:
    try:
        observed = path.lstat()
    except FileNotFoundError:
        if missing_allowed:
            return None, None
        return None, _ValidationProblem(
            RecordingRetentionExecutionReason.STALE_PLAN,
            f"Managed artifact is missing: {path}",
        )
    except OSError as error:
        return None, _ValidationProblem(
            RecordingRetentionExecutionReason.STALE_PLAN,
            f"Could not inspect managed artifact: {path}: {error}",
        )

    if missing_allowed:
        reason = (
            RecordingRetentionExecutionReason.SYMLINK
            if stat.S_ISLNK(observed.st_mode)
            else RecordingRetentionExecutionReason.STALE_PLAN
        )
        return None, _ValidationProblem(
            reason,
            f"Unexpected metadata appeared after planning: {path}",
        )
    if stat.S_ISLNK(observed.st_mode):
        return None, _ValidationProblem(
            RecordingRetentionExecutionReason.SYMLINK,
            f"Managed artifact is a symlink: {path}",
        )
    if not stat.S_ISREG(observed.st_mode):
        return None, _ValidationProblem(
            RecordingRetentionExecutionReason.UNEXPECTED_FILE_TYPE,
            f"Managed artifact is not a regular file: {path}",
        )
    if observed.st_size != expected_size:
        return None, _ValidationProblem(
            RecordingRetentionExecutionReason.STALE_PLAN,
            f"Managed artifact size changed after planning: {path}",
        )
    return observed, None


def _entry_signature(entry: RecordingInventoryEntry) -> tuple[object, ...]:
    return (
        entry.relative_audio_path,
        entry.metadata_path.relative_to(entry.root),
        entry.audio_status,
        entry.metadata_status,
        entry.recorded_at,
        entry.duration_seconds,
        entry.frames,
        entry.audio_size_bytes,
        entry.metadata_size_bytes,
        entry.modified_ns,
    )


def _validate_unit(
    decision: RecordingRetentionDecision,
    plan: RecordingRetentionPlan,
    fresh_entries: dict[Path, RecordingInventoryEntry],
) -> tuple[_ValidatedUnit | None, _ValidationProblem | None]:
    problem = _structure_problem(decision, plan)
    if problem is not None:
        return None, problem

    entry = decision.entry
    audio_stat, problem = _artifact_stat(
        entry.audio_path,
        expected_size=entry.audio_size_bytes,
    )
    if problem is not None:
        return None, problem
    assert audio_stat is not None

    if entry.metadata_status is RecordingMetadataStatus.VALID:
        metadata_stat, problem = _artifact_stat(
            entry.metadata_path,
            expected_size=entry.metadata_size_bytes,
        )
    else:
        metadata_stat, problem = _artifact_stat(
            entry.metadata_path,
            expected_size=0,
            missing_allowed=True,
        )
    if problem is not None:
        return None, problem

    observed_modified_ns = max(
        audio_stat.st_mtime_ns,
        metadata_stat.st_mtime_ns if metadata_stat is not None else 0,
    )
    if observed_modified_ns != entry.modified_ns:
        return None, _ValidationProblem(
            RecordingRetentionExecutionReason.STALE_PLAN,
            f"Managed unit modification time changed: {entry.relative_audio_path}",
        )

    fresh = fresh_entries.get(entry.relative_audio_path)
    if fresh is None or _entry_signature(fresh) != _entry_signature(entry):
        return None, _ValidationProblem(
            RecordingRetentionExecutionReason.STALE_PLAN,
            f"Managed unit changed after planning: {entry.relative_audio_path}",
        )

    return (
        _ValidatedUnit(
            decision=decision,
            audio_stat=audio_stat,
            metadata_stat=metadata_stat,
        ),
        None,
    )


def _stat_fingerprint(observed: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        observed.st_dev,
        observed.st_ino,
        observed.st_mode,
        observed.st_size,
        observed.st_mtime_ns,
    )


def _current_problem(
    path: Path,
    expected: os.stat_result,
) -> _ValidationProblem | None:
    try:
        observed = path.lstat()
    except OSError as error:
        return _ValidationProblem(
            RecordingRetentionExecutionReason.STALE_PLAN,
            f"Managed artifact changed immediately before deletion: {path}: {error}",
        )
    if stat.S_ISLNK(observed.st_mode):
        return _ValidationProblem(
            RecordingRetentionExecutionReason.SYMLINK,
            f"Managed artifact became a symlink: {path}",
        )
    if not stat.S_ISREG(observed.st_mode):
        return _ValidationProblem(
            RecordingRetentionExecutionReason.UNEXPECTED_FILE_TYPE,
            f"Managed artifact changed file type: {path}",
        )
    if _stat_fingerprint(observed) != _stat_fingerprint(expected):
        return _ValidationProblem(
            RecordingRetentionExecutionReason.STALE_PLAN,
            f"Managed artifact changed immediately before deletion: {path}",
        )
    return None


def _outcome(
    decision: RecordingRetentionDecision,
    *,
    status: RecordingRetentionExecutionStatus,
    reason: RecordingRetentionExecutionReason,
    audio_deleted: bool = False,
    metadata_deleted: bool = False,
    deleted_bytes: int = 0,
    message: str | None = None,
) -> RecordingRetentionExecutionEntry:
    return RecordingRetentionExecutionEntry(
        decision=decision,
        status=status,
        reason=reason,
        audio_deleted=audio_deleted,
        metadata_deleted=metadata_deleted,
        deleted_bytes=deleted_bytes,
        message=message,
    )


def _execute_unit(validated: _ValidatedUnit) -> RecordingRetentionExecutionEntry:
    decision = validated.decision
    entry = decision.entry
    metadata_deleted = False
    deleted_bytes = 0

    if validated.metadata_stat is not None:
        problem = _current_problem(entry.metadata_path, validated.metadata_stat)
        if problem is not None:
            return _outcome(
                decision,
                status=RecordingRetentionExecutionStatus.SKIPPED,
                reason=problem.reason,
                message=problem.message,
            )
        try:
            entry.metadata_path.unlink()
        except OSError as error:
            return _outcome(
                decision,
                status=RecordingRetentionExecutionStatus.FAILED,
                reason=RecordingRetentionExecutionReason.DELETE_FAILED,
                message=f"Could not delete recording metadata: {error}",
            )
        metadata_deleted = True
        deleted_bytes += entry.metadata_size_bytes
    else:
        _, problem = _artifact_stat(
            entry.metadata_path,
            expected_size=0,
            missing_allowed=True,
        )
        if problem is not None:
            return _outcome(
                decision,
                status=RecordingRetentionExecutionStatus.SKIPPED,
                reason=problem.reason,
                message=problem.message,
            )

    problem = _current_problem(entry.audio_path, validated.audio_stat)
    if problem is not None:
        return _outcome(
            decision,
            status=(
                RecordingRetentionExecutionStatus.FAILED
                if metadata_deleted
                else RecordingRetentionExecutionStatus.SKIPPED
            ),
            reason=problem.reason,
            metadata_deleted=metadata_deleted,
            deleted_bytes=deleted_bytes,
            message=problem.message,
        )

    if metadata_deleted and os.path.lexists(entry.metadata_path):
        return _outcome(
            decision,
            status=RecordingRetentionExecutionStatus.FAILED,
            reason=RecordingRetentionExecutionReason.STALE_PLAN,
            metadata_deleted=True,
            deleted_bytes=deleted_bytes,
            message=(
                "Recording metadata reappeared before WAV deletion: "
                f"{entry.metadata_path}"
            ),
        )

    try:
        entry.audio_path.unlink()
    except OSError as error:
        return _outcome(
            decision,
            status=RecordingRetentionExecutionStatus.FAILED,
            reason=RecordingRetentionExecutionReason.DELETE_FAILED,
            metadata_deleted=metadata_deleted,
            deleted_bytes=deleted_bytes,
            message=f"Could not delete WAV recording: {error}",
        )

    deleted_bytes += entry.audio_size_bytes
    return _outcome(
        decision,
        status=RecordingRetentionExecutionStatus.COMPLETED,
        reason=RecordingRetentionExecutionReason.COMPLETED,
        audio_deleted=True,
        metadata_deleted=metadata_deleted,
        deleted_bytes=deleted_bytes,
    )


def _summary(
    plan: RecordingRetentionPlan,
    entries: tuple[RecordingRetentionExecutionEntry, ...],
) -> RecordingRetentionExecutionSummary:
    selected = plan.selected
    completed_units = sum(
        entry.status is RecordingRetentionExecutionStatus.COMPLETED
        for entry in entries
    )
    skipped_units = sum(
        entry.status is RecordingRetentionExecutionStatus.SKIPPED
        for entry in entries
    )
    failed_units = sum(
        entry.status is RecordingRetentionExecutionStatus.FAILED
        for entry in entries
    )
    return RecordingRetentionExecutionSummary(
        selected_units=len(selected),
        selected_bytes=sum(decision.total_size_bytes for decision in selected),
        attempted_units=len(entries),
        completed_units=completed_units,
        skipped_units=skipped_units,
        failed_units=failed_units,
        audio_files_deleted=sum(entry.audio_deleted for entry in entries),
        metadata_files_deleted=sum(entry.metadata_deleted for entry in entries),
        deleted_bytes=sum(entry.deleted_bytes for entry in entries),
        all_completed=(
            len(entries) == len(selected)
            and completed_units == len(selected)
            and skipped_units == 0
            and failed_units == 0
        ),
    )


def _result(
    plan: RecordingRetentionPlan,
    token: str,
    entries: tuple[RecordingRetentionExecutionEntry, ...],
) -> RecordingRetentionExecutionResult:
    return RecordingRetentionExecutionResult(
        confirmation_token=token,
        entries=entries,
        summary=_summary(plan, entries),
    )


def execute_recording_retention(
    plan: RecordingRetentionPlan,
    *,
    confirmation: str,
) -> RecordingRetentionExecutionResult:
    """Execute only the exact selected units in one explicitly confirmed plan."""

    expected = recording_retention_confirmation_token(plan)
    if not hmac.compare_digest(confirmation, expected):
        raise RecordingRetentionConfirmationError(
            "Recording retention confirmation does not match the exact plan."
        )

    selected = plan.selected
    root = plan.inventory.root
    problem = _root_problem(root)
    if problem is not None:
        entries = tuple(
            _outcome(
                decision,
                status=RecordingRetentionExecutionStatus.SKIPPED,
                reason=problem.reason,
                message=problem.message,
            )
            for decision in selected
        )
        return _result(plan, expected, entries)

    try:
        fresh_inventory = scan_recording_inventory(root)
    except (OSError, RuntimeError, ValueError) as error:
        entries = tuple(
            _outcome(
                decision,
                status=RecordingRetentionExecutionStatus.SKIPPED,
                reason=RecordingRetentionExecutionReason.ROOT_UNAVAILABLE,
                message=f"Could not re-scan recording inventory: {error}",
            )
            for decision in selected
        )
        return _result(plan, expected, entries)

    fresh_entries = {
        entry.relative_audio_path: entry for entry in fresh_inventory.entries
    }
    artifact_counts: dict[Path, int] = {}
    for decision in selected:
        entry = decision.entry
        for artifact in (entry.audio_path, entry.metadata_path):
            artifact_counts[artifact] = artifact_counts.get(artifact, 0) + 1

    outcomes: list[RecordingRetentionExecutionEntry] = []
    for decision in selected:
        entry = decision.entry
        if any(
            artifact_counts[artifact] > 1
            for artifact in (entry.audio_path, entry.metadata_path)
        ):
            outcomes.append(
                _outcome(
                    decision,
                    status=RecordingRetentionExecutionStatus.SKIPPED,
                    reason=RecordingRetentionExecutionReason.INVALID_MANAGED_PATH,
                    message="Confirmed plan contains overlapping managed artifacts.",
                )
            )
            continue

        validated, problem = _validate_unit(decision, plan, fresh_entries)
        if problem is not None:
            outcomes.append(
                _outcome(
                    decision,
                    status=RecordingRetentionExecutionStatus.SKIPPED,
                    reason=problem.reason,
                    message=problem.message,
                )
            )
            continue
        assert validated is not None
        outcomes.append(_execute_unit(validated))

    return _result(plan, expected, tuple(outcomes))
