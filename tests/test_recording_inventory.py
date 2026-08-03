from __future__ import annotations

import json
import os
import wave
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sds200 import (
    RECORDING_METADATA_SCHEMA,
    RECORDING_METADATA_VERSION,
    RecordingAudioStatus,
    RecordingMetadataStatus,
    scan_recording_inventory,
)
from sds200.recording_metadata import recording_metadata_path


def write_wav(
    path: Path,
    *,
    sample_rate: int = 8000,
    channels: int = 1,
    sample_width: int = 2,
    frames: bytes = bytes((0, 0, 1, 0)),
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(sample_width)
        output.setframerate(sample_rate)
        output.writeframes(frames)


def write_metadata(
    audio: Path,
    *,
    recorded_file: str | None = None,
    started_at: str = "2026-08-03T06:30:00+00:00",
) -> Path:
    sidecar = recording_metadata_path(audio)
    sidecar.write_text(
        json.dumps(
            {
                "schema": RECORDING_METADATA_SCHEMA,
                "version": RECORDING_METADATA_VERSION,
                "recording": {"file": recorded_file or audio.name},
                "boundaries": {"started": {"at": started_at}},
            }
        ),
        encoding="utf-8",
    )
    return sidecar


def test_recording_inventory_requires_directory(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(NotADirectoryError, match="not a directory"):
        scan_recording_inventory(missing)

    occupied = tmp_path / "occupied"
    occupied.write_text("file", encoding="utf-8")
    with pytest.raises(NotADirectoryError, match="not a directory"):
        scan_recording_inventory(occupied)


def test_recording_inventory_reports_compatible_audio_without_metadata(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "nested" / "dispatch.wav"
    write_wav(audio)

    inventory = scan_recording_inventory(tmp_path)

    assert inventory.root == tmp_path.resolve()
    assert inventory.issues == ()
    assert len(inventory.entries) == 1
    entry = inventory.entries[0]
    assert entry.relative_audio_path == Path("nested/dispatch.wav")
    assert entry.audio_status is RecordingAudioStatus.COMPATIBLE
    assert entry.metadata_status is RecordingMetadataStatus.MISSING
    assert entry.playable
    assert entry.requires_attention
    assert entry.frames == 2
    assert entry.duration_seconds == pytest.approx(2 / 8000)
    assert entry.audio_size_bytes == audio.stat().st_size
    assert entry.metadata_size_bytes == 0
    assert inventory.summary.compatible_recordings == 1
    assert inventory.summary.recordings_without_metadata == 1
    assert inventory.summary.attention_units == 1


def test_recording_inventory_validates_sidecar_and_uses_metadata_time(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "dispatch.wav"
    write_wav(audio)
    sidecar = write_metadata(audio)

    inventory = scan_recording_inventory(tmp_path)

    entry = inventory.entries[0]
    assert entry.metadata_status is RecordingMetadataStatus.VALID
    assert entry.recorded_at == datetime(2026, 8, 3, 6, 30, tzinfo=UTC)
    assert not entry.requires_attention
    assert entry.total_size_bytes == audio.stat().st_size + sidecar.stat().st_size
    assert inventory.summary.valid_sidecars == 1
    assert inventory.summary.total_bytes == entry.total_size_bytes
    assert inventory.as_dict()["summary"] == inventory.summary.as_dict()


def test_recording_inventory_reports_mismatched_and_invalid_sidecars(
    tmp_path: Path,
) -> None:
    mismatched = tmp_path / "a.wav"
    invalid = tmp_path / "b.wav"
    unreadable = tmp_path / "c.wav"
    for path in (mismatched, invalid, unreadable):
        write_wav(path)
    write_metadata(mismatched, recorded_file="other.wav")
    recording_metadata_path(invalid).write_text("{broken", encoding="utf-8")
    recording_metadata_path(unreadable).write_bytes(b"\xff\xfe")

    inventory = scan_recording_inventory(tmp_path)
    statuses = {
        entry.audio_path.name: entry.metadata_status for entry in inventory.entries
    }

    assert statuses == {
        "a.wav": RecordingMetadataStatus.MISMATCHED,
        "b.wav": RecordingMetadataStatus.INVALID,
        "c.wav": RecordingMetadataStatus.UNREADABLE,
    }
    assert inventory.summary.invalid_sidecars == 3
    assert inventory.summary.attention_units == 3


def test_recording_inventory_reports_incompatible_and_unreadable_audio(
    tmp_path: Path,
) -> None:
    incompatible = tmp_path / "incompatible.wav"
    unreadable = tmp_path / "unreadable.wav"
    write_wav(incompatible, sample_rate=16000)
    unreadable.write_bytes(b"not a wave file")

    inventory = scan_recording_inventory(tmp_path)
    entries = {entry.audio_path.name: entry for entry in inventory.entries}

    assert entries["incompatible.wav"].audio_status is (
        RecordingAudioStatus.INCOMPATIBLE
    )
    assert entries["incompatible.wav"].duration_seconds is not None
    assert entries["unreadable.wav"].audio_status is RecordingAudioStatus.UNREADABLE
    assert not entries["unreadable.wav"].playable
    assert inventory.summary.incompatible_recordings == 1
    assert inventory.summary.unreadable_recordings == 1


def test_recording_inventory_reports_orphaned_sidecar(tmp_path: Path) -> None:
    expected_audio = tmp_path / "orphan.wav"
    sidecar = write_metadata(expected_audio)

    inventory = scan_recording_inventory(tmp_path)

    entry = inventory.entries[0]
    assert entry.audio_path == expected_audio
    assert entry.metadata_path == sidecar
    assert entry.audio_status is RecordingAudioStatus.MISSING
    assert entry.metadata_status is RecordingMetadataStatus.ORPHANED
    assert entry.recorded_at == datetime(2026, 8, 3, 6, 30, tzinfo=UTC)
    assert inventory.summary.missing_recordings == 1
    assert inventory.summary.orphan_sidecars == 1


def test_recording_inventory_order_is_deterministic(tmp_path: Path) -> None:
    paths = (
        tmp_path / "z" / "two.wav",
        tmp_path / "A" / "three.wav",
        tmp_path / "a" / "one.wav",
    )
    for path in paths:
        write_wav(path)

    first = scan_recording_inventory(tmp_path)
    second = scan_recording_inventory(tmp_path)

    expected = (
        Path("a/one.wav"),
        Path("A/three.wav"),
        Path("z/two.wav"),
    )
    assert tuple(entry.relative_audio_path for entry in first.entries) == expected
    assert first.entries == second.entries
    assert first.summary == second.summary


def test_recording_inventory_does_not_follow_directory_symlinks(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    write_wav(outside / "outside.wav")
    try:
        (root / "linked").symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")

    inventory = scan_recording_inventory(root)

    assert inventory.entries == ()
    assert inventory.summary.managed_units == 0


def test_recording_inventory_rejects_file_symlink_outside_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside.wav"
    root.mkdir()
    write_wav(outside)
    link = root / "linked.wav"
    try:
        link.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")

    inventory = scan_recording_inventory(root)

    entry = inventory.entries[0]
    assert entry.audio_status is RecordingAudioStatus.UNREADABLE
    assert entry.issue is not None
    assert "outside inventory root" in entry.issue
    assert inventory.summary.unreadable_recordings == 1
    assert os.path.lexists(link)
