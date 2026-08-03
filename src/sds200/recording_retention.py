from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from .recording_inventory import (
    RecordingAudioStatus,
    RecordingInventory,
    RecordingInventoryEntry,
    RecordingMetadataStatus,
)


class RecordingRetentionDisposition(StrEnum):
    """Non-destructive planning disposition for one managed recording unit."""

    RETAIN = "retain"
    SELECT = "select"
    PROTECT = "protect"


class RecordingRetentionReason(StrEnum):
    """Deterministic explanation for one retention-plan disposition."""

    WITHIN_POLICY = "within_policy"
    AGE_LIMIT = "age_limit"
    UNIT_LIMIT = "unit_limit"
    BYTE_LIMIT = "byte_limit"
    UNREADABLE_AUDIO = "unreadable_audio"
    MISSING_AUDIO = "missing_audio"
    UNSAFE_METADATA = "unsafe_metadata"
    UNKNOWN_TIMESTAMP = "unknown_timestamp"


@dataclass(frozen=True, slots=True)
class RecordingRetentionPolicy:
    """Renderer-neutral limits used to build a non-destructive retention plan."""

    maximum_age: timedelta | None = None
    maximum_units: int | None = None
    maximum_total_bytes: int | None = None

    def __post_init__(self) -> None:
        if (
            self.maximum_age is None
            and self.maximum_units is None
            and self.maximum_total_bytes is None
        ):
            raise ValueError("Recording retention policy requires at least one limit.")
        if self.maximum_age is not None and self.maximum_age <= timedelta():
            raise ValueError("Recording retention maximum age must be positive.")
        for name, value in (
            ("maximum_units", self.maximum_units),
            ("maximum_total_bytes", self.maximum_total_bytes),
        ):
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"Recording retention {name} must be an integer.")
            if value < 0:
                raise ValueError(f"Recording retention {name} cannot be negative.")

    def as_dict(self) -> dict[str, int | float | None]:
        return {
            "maximum_age_seconds": (
                self.maximum_age.total_seconds()
                if self.maximum_age is not None
                else None
            ),
            "maximum_units": self.maximum_units,
            "maximum_total_bytes": self.maximum_total_bytes,
        }


@dataclass(frozen=True, slots=True)
class RecordingRetentionDecision:
    """One explained, non-destructive decision for an inventory entry."""

    entry: RecordingInventoryEntry
    disposition: RecordingRetentionDisposition
    reasons: tuple[RecordingRetentionReason, ...]

    def __post_init__(self) -> None:
        if not self.reasons:
            raise ValueError("Recording retention decisions require at least one reason.")
        if len(set(self.reasons)) != len(self.reasons):
            raise ValueError("Recording retention decision reasons must be unique.")

        limit_reasons = {
            RecordingRetentionReason.AGE_LIMIT,
            RecordingRetentionReason.UNIT_LIMIT,
            RecordingRetentionReason.BYTE_LIMIT,
        }
        protection_reasons = {
            RecordingRetentionReason.UNREADABLE_AUDIO,
            RecordingRetentionReason.MISSING_AUDIO,
            RecordingRetentionReason.UNSAFE_METADATA,
            RecordingRetentionReason.UNKNOWN_TIMESTAMP,
        }
        reason_set = set(self.reasons)
        if self.disposition is RecordingRetentionDisposition.RETAIN:
            valid = reason_set == {RecordingRetentionReason.WITHIN_POLICY}
        elif self.disposition is RecordingRetentionDisposition.SELECT:
            valid = bool(reason_set) and reason_set <= limit_reasons
        else:
            valid = bool(reason_set) and reason_set <= protection_reasons
        if not valid:
            raise ValueError(
                "Recording retention reasons do not match the decision disposition."
            )

    @property
    def total_size_bytes(self) -> int:
        return self.entry.total_size_bytes

    def as_dict(self) -> dict[str, object]:
        return {
            "audio": str(self.entry.relative_audio_path),
            "disposition": self.disposition.value,
            "reasons": [reason.value for reason in self.reasons],
            "total_size_bytes": self.total_size_bytes,
            "recorded_at": (
                self.entry.recorded_at.astimezone(UTC).isoformat()
                if self.entry.recorded_at is not None
                else None
            ),
            "audio_status": self.entry.audio_status.value,
            "metadata_status": self.entry.metadata_status.value,
        }


@dataclass(frozen=True, slots=True)
class RecordingRetentionSummary:
    """Aggregate results and limit satisfaction for a retention plan."""

    managed_units: int
    managed_bytes: int
    selected_units: int
    selected_bytes: int
    retained_units: int
    retained_bytes: int
    protected_units: int
    protected_bytes: int
    projected_units: int
    projected_bytes: int
    age_violations_remaining: int
    age_unknown_units: int
    age_limit_satisfied: bool
    unit_limit_satisfied: bool
    byte_limit_satisfied: bool
    all_limits_satisfied: bool

    def as_dict(self) -> dict[str, int | bool]:
        return {
            "managed_units": self.managed_units,
            "managed_bytes": self.managed_bytes,
            "selected_units": self.selected_units,
            "selected_bytes": self.selected_bytes,
            "retained_units": self.retained_units,
            "retained_bytes": self.retained_bytes,
            "protected_units": self.protected_units,
            "protected_bytes": self.protected_bytes,
            "projected_units": self.projected_units,
            "projected_bytes": self.projected_bytes,
            "age_violations_remaining": self.age_violations_remaining,
            "age_unknown_units": self.age_unknown_units,
            "age_limit_satisfied": self.age_limit_satisfied,
            "unit_limit_satisfied": self.unit_limit_satisfied,
            "byte_limit_satisfied": self.byte_limit_satisfied,
            "all_limits_satisfied": self.all_limits_satisfied,
        }


@dataclass(frozen=True, slots=True)
class RecordingRetentionPlan:
    """Deterministic preview that never mutates the recording inventory."""

    inventory: RecordingInventory
    policy: RecordingRetentionPolicy
    now: datetime | None
    decisions: tuple[RecordingRetentionDecision, ...]
    summary: RecordingRetentionSummary

    @property
    def selected(self) -> tuple[RecordingRetentionDecision, ...]:
        return tuple(
            decision
            for decision in self.decisions
            if decision.disposition is RecordingRetentionDisposition.SELECT
        )

    @property
    def retained(self) -> tuple[RecordingRetentionDecision, ...]:
        return tuple(
            decision
            for decision in self.decisions
            if decision.disposition is RecordingRetentionDisposition.RETAIN
        )

    @property
    def protected(self) -> tuple[RecordingRetentionDecision, ...]:
        return tuple(
            decision
            for decision in self.decisions
            if decision.disposition is RecordingRetentionDisposition.PROTECT
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "root": str(self.inventory.root),
            "planned_at": self.now.isoformat() if self.now is not None else None,
            "policy": self.policy.as_dict(),
            "summary": self.summary.as_dict(),
            "decisions": [decision.as_dict() for decision in self.decisions],
        }


_PROTECTION_METADATA_STATUSES = {
    RecordingMetadataStatus.UNREADABLE,
    RecordingMetadataStatus.INVALID,
    RecordingMetadataStatus.MISMATCHED,
    RecordingMetadataStatus.ORPHANED,
}

_LIMIT_REASON_ORDER = (
    RecordingRetentionReason.AGE_LIMIT,
    RecordingRetentionReason.UNIT_LIMIT,
    RecordingRetentionReason.BYTE_LIMIT,
)

_PROTECTION_REASON_ORDER = (
    RecordingRetentionReason.UNREADABLE_AUDIO,
    RecordingRetentionReason.MISSING_AUDIO,
    RecordingRetentionReason.UNSAFE_METADATA,
    RecordingRetentionReason.UNKNOWN_TIMESTAMP,
)


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware.")


def _selection_key(entry: RecordingInventoryEntry) -> tuple[datetime, str, str]:
    recorded_at = entry.recorded_at
    assert recorded_at is not None
    relative = entry.relative_audio_path.as_posix()
    return recorded_at.astimezone(UTC), relative.casefold(), relative


def _protection_reasons(
    entry: RecordingInventoryEntry,
) -> tuple[RecordingRetentionReason, ...]:
    reasons: set[RecordingRetentionReason] = set()
    if entry.audio_status is RecordingAudioStatus.UNREADABLE:
        reasons.add(RecordingRetentionReason.UNREADABLE_AUDIO)
    elif entry.audio_status is RecordingAudioStatus.MISSING:
        reasons.add(RecordingRetentionReason.MISSING_AUDIO)

    if entry.metadata_status in _PROTECTION_METADATA_STATUSES:
        reasons.add(RecordingRetentionReason.UNSAFE_METADATA)
    if entry.recorded_at is None:
        reasons.add(RecordingRetentionReason.UNKNOWN_TIMESTAMP)

    return tuple(reason for reason in _PROTECTION_REASON_ORDER if reason in reasons)


def _prefix_for_bytes(
    entries: tuple[RecordingInventoryEntry, ...],
    required_bytes: int,
) -> tuple[RecordingInventoryEntry, ...]:
    if required_bytes <= 0:
        return ()
    selected: list[RecordingInventoryEntry] = []
    removed = 0
    for entry in entries:
        selected.append(entry)
        removed += entry.total_size_bytes
        if removed >= required_bytes:
            break
    return tuple(selected)


def plan_recording_retention(
    inventory: RecordingInventory,
    policy: RecordingRetentionPolicy,
    *,
    now: datetime | None = None,
) -> RecordingRetentionPlan:
    """Build an explained retention preview without changing the filesystem."""

    planned_at: datetime | None = None
    cutoff: datetime | None = None
    if now is not None:
        _require_aware(now, "Recording retention planning time")
        planned_at = now.astimezone(UTC)
    if policy.maximum_age is not None:
        if planned_at is None:
            raise ValueError(
                "Recording retention planning time is required for an age limit."
            )
        cutoff = planned_at - policy.maximum_age

    protected_reasons = {
        entry: protection
        for entry in inventory.entries
        if (protection := _protection_reasons(entry))
    }
    selectable = tuple(
        sorted(
            (
                entry
                for entry in inventory.entries
                if entry not in protected_reasons
            ),
            key=_selection_key,
        )
    )

    selected_reasons: dict[
        RecordingInventoryEntry, set[RecordingRetentionReason]
    ] = {}

    if cutoff is not None:
        for entry in selectable:
            recorded_at = entry.recorded_at
            assert recorded_at is not None
            if recorded_at.astimezone(UTC) < cutoff:
                selected_reasons.setdefault(entry, set()).add(
                    RecordingRetentionReason.AGE_LIMIT
                )

    managed_units = len(inventory.entries)
    managed_bytes = sum(entry.total_size_bytes for entry in inventory.entries)

    if policy.maximum_units is not None:
        required_units = max(0, managed_units - policy.maximum_units)
        for entry in selectable[:required_units]:
            selected_reasons.setdefault(entry, set()).add(
                RecordingRetentionReason.UNIT_LIMIT
            )

    if policy.maximum_total_bytes is not None:
        required_bytes = max(0, managed_bytes - policy.maximum_total_bytes)
        for entry in _prefix_for_bytes(selectable, required_bytes):
            selected_reasons.setdefault(entry, set()).add(
                RecordingRetentionReason.BYTE_LIMIT
            )

    decisions: list[RecordingRetentionDecision] = []
    for entry in inventory.entries:
        if entry in protected_reasons:
            decisions.append(
                RecordingRetentionDecision(
                    entry=entry,
                    disposition=RecordingRetentionDisposition.PROTECT,
                    reasons=protected_reasons[entry],
                )
            )
            continue

        selection_reasons = selected_reasons.get(entry)
        if selection_reasons:
            decisions.append(
                RecordingRetentionDecision(
                    entry=entry,
                    disposition=RecordingRetentionDisposition.SELECT,
                    reasons=tuple(
                        reason
                        for reason in _LIMIT_REASON_ORDER
                        if reason in selection_reasons
                    ),
                )
            )
        else:
            decisions.append(
                RecordingRetentionDecision(
                    entry=entry,
                    disposition=RecordingRetentionDisposition.RETAIN,
                    reasons=(RecordingRetentionReason.WITHIN_POLICY,),
                )
            )

    frozen_decisions = tuple(decisions)
    selected = tuple(
        decision
        for decision in frozen_decisions
        if decision.disposition is RecordingRetentionDisposition.SELECT
    )
    retained = tuple(
        decision
        for decision in frozen_decisions
        if decision.disposition is RecordingRetentionDisposition.RETAIN
    )
    protected = tuple(
        decision
        for decision in frozen_decisions
        if decision.disposition is RecordingRetentionDisposition.PROTECT
    )

    selected_bytes = sum(decision.total_size_bytes for decision in selected)
    retained_bytes = sum(decision.total_size_bytes for decision in retained)
    protected_bytes = sum(decision.total_size_bytes for decision in protected)
    projected_units = managed_units - len(selected)
    projected_bytes = managed_bytes - selected_bytes

    if cutoff is None:
        age_violations_remaining = 0
        age_unknown_units = 0
        age_limit_satisfied = True
    else:
        remaining = tuple((*retained, *protected))
        age_violations_remaining = sum(
            decision.entry.recorded_at is not None
            and decision.entry.recorded_at.astimezone(UTC) < cutoff
            for decision in remaining
        )
        age_unknown_units = sum(
            decision.entry.recorded_at is None for decision in remaining
        )
        age_limit_satisfied = (
            age_violations_remaining == 0 and age_unknown_units == 0
        )

    unit_limit_satisfied = (
        policy.maximum_units is None or projected_units <= policy.maximum_units
    )
    byte_limit_satisfied = (
        policy.maximum_total_bytes is None
        or projected_bytes <= policy.maximum_total_bytes
    )
    summary = RecordingRetentionSummary(
        managed_units=managed_units,
        managed_bytes=managed_bytes,
        selected_units=len(selected),
        selected_bytes=selected_bytes,
        retained_units=len(retained),
        retained_bytes=retained_bytes,
        protected_units=len(protected),
        protected_bytes=protected_bytes,
        projected_units=projected_units,
        projected_bytes=projected_bytes,
        age_violations_remaining=age_violations_remaining,
        age_unknown_units=age_unknown_units,
        age_limit_satisfied=age_limit_satisfied,
        unit_limit_satisfied=unit_limit_satisfied,
        byte_limit_satisfied=byte_limit_satisfied,
        all_limits_satisfied=(
            age_limit_satisfied
            and unit_limit_satisfied
            and byte_limit_satisfied
        ),
    )
    return RecordingRetentionPlan(
        inventory=inventory,
        policy=policy,
        now=planned_at,
        decisions=frozen_decisions,
        summary=summary,
    )
