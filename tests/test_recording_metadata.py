from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sds200.audio_session import (
    AudioReliabilitySnapshot,
    AudioSessionSnapshot,
    AudioSessionStatus,
)
from sds200.recording_metadata import (
    RECORDING_METADATA_SCHEMA,
    RECORDING_METADATA_VERSION,
    RecordingMetadata,
    RecordingSource,
    RecordingState,
    recording_metadata_path,
    write_recording_metadata,
)
from sds200.state import RadioStateSnapshot


def snapshot(
    path: Path,
    *,
    endpoint: str = "rtsp://scanner/audio",
    started_at: datetime | None,
    stopped_at: datetime | None,
    elapsed_seconds: float,
    packets: int,
    samples: int,
) -> AudioSessionSnapshot:
    status = (
        AudioSessionStatus.STOPPED
        if stopped_at is not None
        else AudioSessionStatus.RECORDING
    )
    return AudioSessionSnapshot(
        status=status,
        endpoint=endpoint,
        output_path=path,
        started_at=started_at,
        stopped_at=stopped_at,
        elapsed_seconds=elapsed_seconds,
        packets=packets,
        samples=samples,
        audio_duration_seconds=samples / 8000,
        reliability=AudioReliabilitySnapshot(packets_lost=2, callback_errors=1),
    )


def test_recording_metadata_serializes_versioned_boundary_state(tmp_path: Path) -> None:
    path = tmp_path / "dispatch.wav"
    started_at = datetime(2026, 7, 30, 13, 0, tzinfo=UTC)
    stopped_at = started_at + timedelta(seconds=5)
    started = snapshot(
        path,
        started_at=started_at,
        stopped_at=None,
        elapsed_seconds=0.0,
        packets=0,
        samples=0,
    )
    stopped = snapshot(
        path,
        started_at=started_at,
        stopped_at=stopped_at,
        elapsed_seconds=5.0,
        packets=125,
        samples=40_000,
    )

    metadata = RecordingMetadata.from_snapshots(
        started,
        stopped,
        scanner="SDS200",
        started_state=RadioStateSnapshot(
            system="County",
            department="Fire",
            site="North",
            channel="Dispatch",
            frequency="154.1900",
            service_type="Fire Dispatch",
        ),
        stopped_state=RadioStateSnapshot(
            system="County",
            department="Fire",
            site="North",
            channel="Tac 1",
            frequency="154.2800",
            talkgroup_id="1201",
        ),
    )

    payload = metadata.as_dict()
    assert payload["schema"] == RECORDING_METADATA_SCHEMA
    assert payload["version"] == RECORDING_METADATA_VERSION
    assert payload["recording"] == {
        "file": "dispatch.wav",
        "format": "wav",
        "sample_rate_hz": 8000,
        "channels": 1,
        "sample_width_bytes": 2,
    }
    assert payload["source"] == {
        "endpoint": "rtsp://scanner/audio",
        "scanner": "SDS200",
    }
    boundaries = payload["boundaries"]
    assert isinstance(boundaries, dict)
    assert boundaries["started"] == {
        "at": "2026-07-30T13:00:00+00:00",
        "state": {
            "system": "County",
            "department": "Fire",
            "site": "North",
            "channel": "Dispatch",
            "frequency": "154.1900",
            "service_type": "Fire Dispatch",
        },
    }
    assert boundaries["stopped"] == {
        "at": "2026-07-30T13:00:05+00:00",
        "state": {
            "system": "County",
            "department": "Fire",
            "site": "North",
            "channel": "Tac 1",
            "frequency": "154.2800",
            "talkgroup_id": "1201",
        },
    }
    assert json.loads(metadata.to_json()) == payload


def test_recording_metadata_rejects_invalid_boundaries(tmp_path: Path) -> None:
    aware = datetime(2026, 7, 30, 13, 0, tzinfo=UTC)
    kwargs = {
        "recording_path": tmp_path / "dispatch.wav",
        "source": RecordingSource(endpoint="scanner"),
        "elapsed_seconds": 0.0,
        "packets": 0,
        "samples": 0,
        "audio_duration_seconds": 0.0,
        "reliability": AudioReliabilitySnapshot(),
    }

    with pytest.raises(ValueError, match="timezone-aware"):
        RecordingMetadata(
            started_at=aware.replace(tzinfo=None),
            stopped_at=aware,
            **kwargs,
        )

    with pytest.raises(ValueError, match="cannot precede"):
        RecordingMetadata(
            started_at=aware,
            stopped_at=aware - timedelta(seconds=1),
            **kwargs,
        )


def test_recording_metadata_requires_matching_snapshots(tmp_path: Path) -> None:
    started_at = datetime(2026, 7, 30, 13, 0, tzinfo=UTC)
    started = snapshot(
        tmp_path / "one.wav",
        started_at=started_at,
        stopped_at=None,
        elapsed_seconds=0.0,
        packets=0,
        samples=0,
    )
    stopped = snapshot(
        tmp_path / "two.wav",
        started_at=started_at,
        stopped_at=started_at + timedelta(seconds=1),
        elapsed_seconds=1.0,
        packets=25,
        samples=8_000,
    )

    with pytest.raises(ValueError, match="same output path"):
        RecordingMetadata.from_snapshots(started, stopped)


def test_write_recording_metadata_is_atomic_and_collision_safe(
    tmp_path: Path,
) -> None:
    recording = tmp_path / "dispatch.wav"
    observed_at = datetime(2026, 7, 30, 13, 0, tzinfo=UTC)
    metadata = RecordingMetadata(
        recording_path=recording,
        source=RecordingSource(endpoint="scanner"),
        started_at=observed_at,
        stopped_at=observed_at + timedelta(seconds=1),
        elapsed_seconds=1.0,
        packets=25,
        samples=8_000,
        audio_duration_seconds=1.0,
        reliability=AudioReliabilitySnapshot(),
        started_state=RecordingState(channel="Dispatch"),
        stopped_state=RecordingState(channel="Dispatch"),
    )

    expected = tmp_path / "dispatch.wav.json"
    assert recording_metadata_path(recording) == expected
    assert write_recording_metadata(metadata) == expected
    assert expected.read_text(encoding="utf-8") == metadata.to_json()
    assert list(tmp_path.glob("*.tmp")) == []

    with pytest.raises(FileExistsError):
        write_recording_metadata(metadata)

    replacement = RecordingMetadata(
        recording_path=recording,
        source=RecordingSource(endpoint="scanner"),
        started_at=observed_at,
        stopped_at=observed_at + timedelta(seconds=2),
        elapsed_seconds=2.0,
        packets=50,
        samples=16_000,
        audio_duration_seconds=2.0,
        reliability=AudioReliabilitySnapshot(),
    )
    assert write_recording_metadata(replacement, overwrite=True) == expected
    assert json.loads(expected.read_text(encoding="utf-8"))["statistics"][
        "samples"
    ] == 16_000
