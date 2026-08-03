from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sds200 import (
    RecordingAudioStatus,
    RecordingInventory,
    RecordingInventoryEntry,
    RecordingInventorySummary,
    RecordingMetadataStatus,
    RecordingRetentionDisposition,
    RecordingRetentionPolicy,
    RecordingRetentionReason,
    plan_recording_retention,
)

NOW = datetime(2026, 8, 3, 7, 0, tzinfo=UTC)


def inventory_entry(
    root: Path,
    name: str,
    *,
    recorded_at: datetime | None,
    size_bytes: int = 100,
    audio_status: RecordingAudioStatus = RecordingAudioStatus.COMPATIBLE,
    metadata_status: RecordingMetadataStatus = RecordingMetadataStatus.VALID,
) -> RecordingInventoryEntry:
    audio = root / name
    return RecordingInventoryEntry(
        root=root,
        audio_path=audio,
        metadata_path=audio.with_name(f"{audio.name}.json"),
        audio_status=audio_status,
        metadata_status=metadata_status,
        recorded_at=recorded_at,
        duration_seconds=1.0 if audio_status is not RecordingAudioStatus.MISSING else None,
        frames=8000 if audio_status is not RecordingAudioStatus.MISSING else None,
        audio_size_bytes=size_bytes,
        metadata_size_bytes=0,
        modified_ns=1,
    )


def inventory(
    root: Path,
    *entries: RecordingInventoryEntry,
) -> RecordingInventory:
    frozen = tuple(entries)
    return RecordingInventory(
        root=root,
        entries=frozen,
        summary=RecordingInventorySummary.from_entries(frozen),
    )


def decision_map(plan: object) -> dict[str, object]:
    decisions = plan.decisions
    return {
        decision.entry.audio_path.name: decision
        for decision in decisions
    }


def test_retention_policy_requires_a_limit() -> None:
    with pytest.raises(ValueError, match="at least one limit"):
        RecordingRetentionPolicy()


@pytest.mark.parametrize(
    ("arguments", "message"),
    (
        ({"maximum_age": timedelta()}, "maximum age must be positive"),
        ({"maximum_age": timedelta(seconds=-1)}, "maximum age must be positive"),
        ({"maximum_units": -1}, "maximum_units cannot be negative"),
        ({"maximum_total_bytes": -1}, "maximum_total_bytes cannot be negative"),
        ({"maximum_units": True}, "maximum_units must be an integer"),
    ),
)
def test_retention_policy_rejects_invalid_limits(
    arguments: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        RecordingRetentionPolicy(**arguments)  # type: ignore[arg-type]


def test_retention_policy_accepts_zero_unit_and_byte_limits() -> None:
    policy = RecordingRetentionPolicy(maximum_units=0, maximum_total_bytes=0)

    assert policy.maximum_units == 0
    assert policy.maximum_total_bytes == 0


def test_age_limit_selects_only_entries_older_than_cutoff(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    old = inventory_entry(root, "old.wav", recorded_at=NOW - timedelta(days=2))
    boundary = inventory_entry(
        root,
        "boundary.wav",
        recorded_at=NOW - timedelta(days=1),
    )
    recent = inventory_entry(root, "recent.wav", recorded_at=NOW)
    plan = plan_recording_retention(
        inventory(root, old, boundary, recent),
        RecordingRetentionPolicy(maximum_age=timedelta(days=1)),
        now=NOW,
    )

    decisions = decision_map(plan)
    assert decisions["old.wav"].disposition is RecordingRetentionDisposition.SELECT
    assert decisions["old.wav"].reasons == (RecordingRetentionReason.AGE_LIMIT,)
    assert decisions["boundary.wav"].disposition is (
        RecordingRetentionDisposition.RETAIN
    )
    assert decisions["recent.wav"].disposition is RecordingRetentionDisposition.RETAIN
    assert plan.summary.age_limit_satisfied


def test_age_limit_requires_aware_planning_time(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    item = inventory_entry(root, "one.wav", recorded_at=NOW)

    with pytest.raises(ValueError, match="planning time is required"):
        plan_recording_retention(
            inventory(root, item),
            RecordingRetentionPolicy(maximum_age=timedelta(days=1)),
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        plan_recording_retention(
            inventory(root, item),
            RecordingRetentionPolicy(maximum_age=timedelta(days=1)),
            now=datetime(2026, 8, 3, 7, 0),
        )


def test_unit_limit_selects_oldest_entries(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    entries = tuple(
        inventory_entry(
            root,
            f"{name}.wav",
            recorded_at=NOW - timedelta(hours=hours),
        )
        for name, hours in (("oldest", 3), ("middle", 2), ("newest", 1))
    )
    plan = plan_recording_retention(
        inventory(root, *entries),
        RecordingRetentionPolicy(maximum_units=1),
    )

    assert tuple(decision.entry.audio_path.name for decision in plan.selected) == (
        "oldest.wav",
        "middle.wav",
    )
    assert plan.summary.projected_units == 1
    assert plan.summary.unit_limit_satisfied


def test_byte_limit_selects_oldest_until_projected_bytes_fit(
    tmp_path: Path,
) -> None:
    root = tmp_path.resolve()
    entries = (
        inventory_entry(
            root,
            "oldest.wav",
            recorded_at=NOW - timedelta(hours=3),
            size_bytes=40,
        ),
        inventory_entry(
            root,
            "middle.wav",
            recorded_at=NOW - timedelta(hours=2),
            size_bytes=70,
        ),
        inventory_entry(
            root,
            "newest.wav",
            recorded_at=NOW - timedelta(hours=1),
            size_bytes=90,
        ),
    )
    plan = plan_recording_retention(
        inventory(root, *entries),
        RecordingRetentionPolicy(maximum_total_bytes=100),
    )

    assert tuple(decision.entry.audio_path.name for decision in plan.selected) == (
        "oldest.wav",
        "middle.wav",
    )
    assert plan.summary.selected_bytes == 110
    assert plan.summary.projected_bytes == 90
    assert plan.summary.byte_limit_satisfied


def test_combined_limits_explain_multiple_selection_reasons(
    tmp_path: Path,
) -> None:
    root = tmp_path.resolve()
    old = inventory_entry(
        root,
        "old.wav",
        recorded_at=NOW - timedelta(days=3),
    )
    recent_a = inventory_entry(root, "recent-a.wav", recorded_at=NOW)
    recent_b = inventory_entry(root, "recent-b.wav", recorded_at=NOW)
    plan = plan_recording_retention(
        inventory(root, old, recent_a, recent_b),
        RecordingRetentionPolicy(
            maximum_age=timedelta(days=2),
            maximum_units=2,
            maximum_total_bytes=250,
        ),
        now=NOW,
    )

    decision = decision_map(plan)["old.wav"]
    assert decision.disposition is RecordingRetentionDisposition.SELECT
    assert decision.reasons == (
        RecordingRetentionReason.AGE_LIMIT,
        RecordingRetentionReason.UNIT_LIMIT,
        RecordingRetentionReason.BYTE_LIMIT,
    )
    assert plan.summary.all_limits_satisfied


def test_missing_metadata_remains_selectable(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    item = inventory_entry(
        root,
        "plain.wav",
        recorded_at=NOW - timedelta(days=2),
        metadata_status=RecordingMetadataStatus.MISSING,
    )
    plan = plan_recording_retention(
        inventory(root, item),
        RecordingRetentionPolicy(maximum_age=timedelta(days=1)),
        now=NOW,
    )

    assert plan.selected[0].entry is item
    assert plan.selected[0].reasons == (RecordingRetentionReason.AGE_LIMIT,)


@pytest.mark.parametrize(
    ("audio_status", "metadata_status", "expected_reason"),
    (
        (
            RecordingAudioStatus.UNREADABLE,
            RecordingMetadataStatus.VALID,
            RecordingRetentionReason.UNREADABLE_AUDIO,
        ),
        (
            RecordingAudioStatus.MISSING,
            RecordingMetadataStatus.ORPHANED,
            RecordingRetentionReason.MISSING_AUDIO,
        ),
        (
            RecordingAudioStatus.COMPATIBLE,
            RecordingMetadataStatus.INVALID,
            RecordingRetentionReason.UNSAFE_METADATA,
        ),
    ),
)
def test_unsafe_units_are_protected(
    tmp_path: Path,
    audio_status: RecordingAudioStatus,
    metadata_status: RecordingMetadataStatus,
    expected_reason: RecordingRetentionReason,
) -> None:
    root = tmp_path.resolve()
    item = inventory_entry(
        root,
        "unsafe.wav",
        recorded_at=NOW - timedelta(days=10),
        audio_status=audio_status,
        metadata_status=metadata_status,
    )
    plan = plan_recording_retention(
        inventory(root, item),
        RecordingRetentionPolicy(
            maximum_age=timedelta(days=1),
            maximum_units=0,
            maximum_total_bytes=0,
        ),
        now=NOW,
    )

    assert plan.protected[0].entry is item
    assert expected_reason in plan.protected[0].reasons
    assert plan.summary.protected_units == 1


def test_unknown_timestamp_is_protected_and_age_limit_unsatisfied(
    tmp_path: Path,
) -> None:
    root = tmp_path.resolve()
    item = inventory_entry(root, "unknown.wav", recorded_at=None)
    plan = plan_recording_retention(
        inventory(root, item),
        RecordingRetentionPolicy(maximum_age=timedelta(days=1)),
        now=NOW,
    )

    assert plan.protected[0].reasons == (
        RecordingRetentionReason.UNKNOWN_TIMESTAMP,
    )
    assert plan.summary.age_unknown_units == 1
    assert not plan.summary.age_limit_satisfied
    assert not plan.summary.all_limits_satisfied


def test_protected_units_can_leave_unit_and_byte_limits_unsatisfied(
    tmp_path: Path,
) -> None:
    root = tmp_path.resolve()
    protected = inventory_entry(
        root,
        "protected.wav",
        recorded_at=NOW,
        size_bytes=200,
        metadata_status=RecordingMetadataStatus.INVALID,
    )
    selectable = inventory_entry(
        root,
        "selectable.wav",
        recorded_at=NOW - timedelta(days=1),
        size_bytes=100,
    )
    plan = plan_recording_retention(
        inventory(root, protected, selectable),
        RecordingRetentionPolicy(maximum_units=0, maximum_total_bytes=0),
    )

    assert plan.selected[0].entry is selectable
    assert plan.protected[0].entry is protected
    assert plan.summary.projected_units == 1
    assert plan.summary.projected_bytes == 200
    assert not plan.summary.unit_limit_satisfied
    assert not plan.summary.byte_limit_satisfied
    assert not plan.summary.all_limits_satisfied


def test_equal_timestamps_use_relative_path_tie_breakers(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    entries = (
        inventory_entry(root, "z.wav", recorded_at=NOW),
        inventory_entry(root, "A.wav", recorded_at=NOW),
        inventory_entry(root, "a.wav", recorded_at=NOW),
    )
    plan = plan_recording_retention(
        inventory(root, *entries),
        RecordingRetentionPolicy(maximum_units=1),
    )

    assert tuple(decision.entry.audio_path.name for decision in plan.selected) == (
        "A.wav",
        "a.wav",
    )


def test_planning_preserves_inventory_and_files(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    audio = root / "recording.wav"
    audio.write_bytes(b"audio")
    item = inventory_entry(
        root,
        audio.name,
        recorded_at=NOW - timedelta(days=2),
        size_bytes=audio.stat().st_size,
    )
    source = inventory(root, item)
    before_inventory = source.as_dict()
    before_bytes = audio.read_bytes()

    plan_recording_retention(
        source,
        RecordingRetentionPolicy(maximum_age=timedelta(days=1)),
        now=NOW,
    )

    assert source.as_dict() == before_inventory
    assert audio.read_bytes() == before_bytes


def test_retention_plan_serialization_is_stable(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    old = inventory_entry(root, "old.wav", recorded_at=NOW - timedelta(days=2))
    recent = inventory_entry(root, "recent.wav", recorded_at=NOW)
    policy = RecordingRetentionPolicy(
        maximum_age=timedelta(days=1),
        maximum_units=1,
    )
    plan = plan_recording_retention(
        inventory(root, old, recent),
        policy,
        now=NOW,
    )

    assert plan.as_dict() == {
        "root": str(root),
        "planned_at": NOW.isoformat(),
        "policy": {
            "maximum_age_seconds": 86400.0,
            "maximum_units": 1,
            "maximum_total_bytes": None,
        },
        "summary": plan.summary.as_dict(),
        "decisions": [decision.as_dict() for decision in plan.decisions],
    }
    assert plan.as_dict() == plan.as_dict()
