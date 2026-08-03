from __future__ import annotations

import json
import os
import wave
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sds200 import (
    RECORDING_METADATA_SCHEMA,
    RECORDING_METADATA_VERSION,
    RecordingRetentionConfirmationError,
    RecordingRetentionExecutionReason,
    RecordingRetentionExecutionStatus,
    RecordingRetentionPolicy,
    execute_recording_retention,
    plan_recording_retention,
    recording_metadata_path,
    recording_retention_confirmation_token,
    scan_recording_inventory,
)

NOW = datetime(2026, 8, 3, 8, 0, tzinfo=UTC)


def write_wav(path: Path, *, frames: bytes = bytes((0, 0, 1, 0))) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        output.writeframes(frames)


def write_metadata(
    audio: Path,
    *,
    started_at: datetime = NOW,
) -> Path:
    sidecar = recording_metadata_path(audio)
    sidecar.write_text(
        json.dumps(
            {
                "schema": RECORDING_METADATA_SCHEMA,
                "version": RECORDING_METADATA_VERSION,
                "recording": {"file": audio.name},
                "boundaries": {"started": {"at": started_at.isoformat()}},
            }
        ),
        encoding="utf-8",
    )
    return sidecar


def selected_plan(root: Path, *, with_metadata: bool = True):
    audio = root / "recording.wav"
    write_wav(audio)
    if with_metadata:
        write_metadata(audio, started_at=NOW - timedelta(days=2))
    inventory = scan_recording_inventory(root)
    plan = plan_recording_retention(
        inventory,
        RecordingRetentionPolicy(maximum_units=0),
    )
    return audio, plan


def execute(plan):
    return execute_recording_retention(
        plan,
        confirmation=recording_retention_confirmation_token(plan),
    )


def test_confirmation_token_is_stable_and_bound_to_exact_plan(
    tmp_path: Path,
) -> None:
    _, plan = selected_plan(tmp_path)

    first = recording_retention_confirmation_token(plan)
    second = recording_retention_confirmation_token(plan)
    changed = replace(
        plan,
        policy=RecordingRetentionPolicy(maximum_total_bytes=0),
    )

    assert first == second
    assert first.startswith("delete:")
    assert first != recording_retention_confirmation_token(changed)


def test_confirmation_mismatch_refuses_all_mutation(tmp_path: Path) -> None:
    audio, plan = selected_plan(tmp_path)
    sidecar = recording_metadata_path(audio)

    with pytest.raises(
        RecordingRetentionConfirmationError,
        match="does not match",
    ):
        execute_recording_retention(plan, confirmation="delete:wrong")

    assert audio.exists()
    assert sidecar.exists()


def test_execution_deletes_selected_wav_and_sidecar(tmp_path: Path) -> None:
    audio, plan = selected_plan(tmp_path)
    sidecar = recording_metadata_path(audio)
    expected_bytes = audio.stat().st_size + sidecar.stat().st_size

    result = execute(plan)

    assert not audio.exists()
    assert not sidecar.exists()
    assert len(result.completed) == 1
    entry = result.completed[0]
    assert entry.status is RecordingRetentionExecutionStatus.COMPLETED
    assert entry.reason is RecordingRetentionExecutionReason.COMPLETED
    assert entry.audio_deleted
    assert entry.metadata_deleted
    assert entry.deleted_bytes == expected_bytes
    assert result.summary.completed_units == 1
    assert result.summary.deleted_bytes == expected_bytes
    assert result.summary.all_completed


def test_execution_deletes_audio_when_sidecar_was_missing(tmp_path: Path) -> None:
    audio, plan = selected_plan(tmp_path, with_metadata=False)

    result = execute(plan)

    assert not audio.exists()
    assert result.completed[0].audio_deleted
    assert not result.completed[0].metadata_deleted


def test_execution_never_deletes_retained_units(tmp_path: Path) -> None:
    old = tmp_path / "old.wav"
    recent = tmp_path / "recent.wav"
    write_wav(old)
    write_wav(recent)
    write_metadata(old, started_at=NOW - timedelta(days=2))
    write_metadata(recent, started_at=NOW)
    inventory = scan_recording_inventory(tmp_path)
    plan = plan_recording_retention(
        inventory,
        RecordingRetentionPolicy(maximum_units=1),
    )

    result = execute(plan)

    assert not old.exists()
    assert not recording_metadata_path(old).exists()
    assert recent.exists()
    assert recording_metadata_path(recent).exists()
    assert result.summary.selected_units == 1
    assert result.summary.completed_units == 1


def test_execution_never_deletes_protected_units(tmp_path: Path) -> None:
    selected = tmp_path / "selected.wav"
    protected = tmp_path / "protected.wav"
    write_wav(selected)
    write_wav(protected)
    write_metadata(selected, started_at=NOW - timedelta(days=2))
    recording_metadata_path(protected).write_text("{broken", encoding="utf-8")
    inventory = scan_recording_inventory(tmp_path)
    plan = plan_recording_retention(
        inventory,
        RecordingRetentionPolicy(maximum_units=0),
    )

    result = execute(plan)

    assert not selected.exists()
    assert protected.exists()
    assert recording_metadata_path(protected).exists()
    assert len(plan.protected) == 1
    assert result.summary.selected_units == 1


def test_changed_audio_is_skipped_as_a_stale_plan(tmp_path: Path) -> None:
    audio, plan = selected_plan(tmp_path)
    audio.write_bytes(b"changed after planning")

    result = execute(plan)

    assert audio.exists()
    assert recording_metadata_path(audio).exists()
    assert result.skipped[0].reason is RecordingRetentionExecutionReason.STALE_PLAN
    assert not result.summary.all_completed


def test_new_metadata_for_missing_sidecar_is_skipped(tmp_path: Path) -> None:
    audio, plan = selected_plan(tmp_path, with_metadata=False)
    write_metadata(audio)

    result = execute(plan)

    assert audio.exists()
    assert recording_metadata_path(audio).exists()
    assert result.skipped[0].reason is RecordingRetentionExecutionReason.STALE_PLAN


def test_file_symlink_is_refused_even_when_target_matches_snapshot(
    tmp_path: Path,
) -> None:
    audio, plan = selected_plan(tmp_path, with_metadata=False)
    original_stat = audio.stat()
    target = tmp_path / "target.bin"
    target.write_bytes(audio.read_bytes())
    os.utime(
        target,
        ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
    )
    audio.unlink()
    try:
        audio.symlink_to(target)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")

    result = execute(plan)

    assert audio.is_symlink()
    assert target.exists()
    assert result.skipped[0].reason is RecordingRetentionExecutionReason.SYMLINK


def test_symlinked_parent_directory_is_refused(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    audio = nested / "recording.wav"
    write_wav(audio)
    inventory = scan_recording_inventory(tmp_path)
    plan = plan_recording_retention(
        inventory,
        RecordingRetentionPolicy(maximum_units=0),
    )
    actual = tmp_path / "actual"
    nested.rename(actual)
    try:
        nested.symlink_to(actual, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")

    result = execute(plan)

    assert (actual / "recording.wav").exists()
    assert result.skipped[0].reason is RecordingRetentionExecutionReason.SYMLINK


def test_unexpected_file_type_is_refused(tmp_path: Path) -> None:
    audio, plan = selected_plan(tmp_path, with_metadata=False)
    audio.unlink()
    audio.mkdir()

    result = execute(plan)

    assert audio.is_dir()
    assert result.skipped[0].reason is (
        RecordingRetentionExecutionReason.UNEXPECTED_FILE_TYPE
    )


def test_path_escape_in_forged_plan_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    _, plan = selected_plan(root, with_metadata=False)
    outside = tmp_path / "outside.wav"
    write_wav(outside)
    original = plan.selected[0]
    forged_audio = root / ".." / outside.name
    forged_entry = replace(
        original.entry,
        audio_path=forged_audio,
        metadata_path=recording_metadata_path(forged_audio),
    )
    forged_decision = replace(original, entry=forged_entry)
    forged_plan = replace(plan, decisions=(forged_decision,))

    result = execute(forged_plan)

    assert outside.exists()
    assert result.skipped[0].reason in {
        RecordingRetentionExecutionReason.INVALID_MANAGED_PATH,
        RecordingRetentionExecutionReason.PATH_OUTSIDE_ROOT,
    }


def test_sidecar_delete_failure_preserves_audio_and_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "a.wav"
    second = tmp_path / "b.wav"
    for path in (first, second):
        write_wav(path)
        write_metadata(path)
    plan = plan_recording_retention(
        scan_recording_inventory(tmp_path),
        RecordingRetentionPolicy(maximum_units=0),
    )
    blocked = recording_metadata_path(first)
    original_unlink = Path.unlink

    def guarded_unlink(path: Path, *args: object, **kwargs: object) -> None:
        if path == blocked:
            raise PermissionError("blocked sidecar")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", guarded_unlink)

    result = execute(plan)

    assert first.exists()
    assert blocked.exists()
    assert not second.exists()
    assert not recording_metadata_path(second).exists()
    assert result.failed[0].entry.audio_path == first
    assert result.failed[0].reason is (
        RecordingRetentionExecutionReason.DELETE_FAILED
    )
    assert result.summary.failed_units == 1
    assert result.summary.completed_units == 1


def test_audio_delete_failure_reports_partial_sidecar_deletion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio, plan = selected_plan(tmp_path)
    sidecar = recording_metadata_path(audio)
    sidecar_size = sidecar.stat().st_size
    original_unlink = Path.unlink

    def guarded_unlink(path: Path, *args: object, **kwargs: object) -> None:
        if path == audio:
            raise PermissionError("blocked audio")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", guarded_unlink)

    result = execute(plan)

    assert audio.exists()
    assert not sidecar.exists()
    failure = result.failed[0]
    assert not failure.audio_deleted
    assert failure.metadata_deleted
    assert failure.deleted_bytes == sidecar_size
    assert failure.reason is RecordingRetentionExecutionReason.DELETE_FAILED


def test_execution_result_serialization_is_stable(tmp_path: Path) -> None:
    _, plan = selected_plan(tmp_path)

    result = execute(plan)

    assert result.as_dict() == {
        "confirmation_token": recording_retention_confirmation_token(plan),
        "summary": result.summary.as_dict(),
        "entries": [entry.as_dict() for entry in result.entries],
    }
    assert result.as_dict() == result.as_dict()
