from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sds200 import RecordingIdentity, safe_recording_component
from sds200.audio_session import AudioReliabilitySnapshot
from sds200.recording_metadata import (
    RecordingMetadata,
    RecordingSource,
    RecordingState,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("  County / Fire: Dispatch  ", "County-Fire-Dispatch"),
        ("Café Dépôt", "Café-Dépôt"),
        ("one___two---three", "one-two-three"),
        ("../../", "unknown"),
        ("\x00\x01", "unknown"),
        ("CON", "recording-CON"),
        ("com1", "recording-com1"),
    ],
)
def test_safe_recording_component_is_portable(
    value: str,
    expected: str,
) -> None:
    assert safe_recording_component(value) == expected


def test_safe_recording_component_supports_fallback_and_length() -> None:
    assert safe_recording_component(None, fallback="No Signal") == "No-Signal"
    assert safe_recording_component("Dispatch Channel", max_length=8) == "Dispatch"

    with pytest.raises(ValueError, match="positive"):
        safe_recording_component("Dispatch", max_length=0)

    with pytest.raises(ValueError, match="fallback"):
        safe_recording_component(None, fallback="///")


def test_recording_identity_prefers_started_state_and_fills_missing_values(
    tmp_path: Path,
) -> None:
    started_at = datetime(2026, 8, 3, 5, 30, tzinfo=UTC)
    metadata = RecordingMetadata(
        recording_path=tmp_path / "original.wav",
        source=RecordingSource(
            endpoint="  rtsp://192.168.0.251/audio  ",
            scanner="  SDS/200  ",
        ),
        started_at=started_at,
        stopped_at=started_at + timedelta(seconds=4),
        elapsed_seconds=4.0,
        packets=100,
        samples=32_000,
        audio_duration_seconds=4.0,
        reliability=AudioReliabilitySnapshot(),
        started_state=RecordingState(
            mode="Scan",
            system="County / Public Safety",
            channel="Dispatch: 1",
            frequency="154.1900",
        ),
        stopped_state=RecordingState(
            mode="Hold",
            system="Different System",
            department="Fire & EMS",
            site="North",
            channel="Tac 1",
            talkgroup_id="1201",
        ),
    )

    identity = RecordingIdentity.from_metadata(metadata)

    assert identity.endpoint == "rtsp://192.168.0.251/audio"
    assert identity.scanner == "SDS/200"
    assert identity.mode == "Scan"
    assert identity.system == "County / Public Safety"
    assert identity.department == "Fire & EMS"
    assert identity.site == "North"
    assert identity.channel == "Dispatch: 1"
    assert identity.frequency == "154.1900"
    assert identity.talkgroup_id == "1201"

    assert identity.components["date"] == "2026-08-03"
    assert identity.components["timestamp"] == "20260803T053000Z"

    components = identity.filename_components()
    assert components["scanner"] == "SDS-200"
    assert components["endpoint"] == "rtsp-192-168-0-251-audio"
    assert components["system"] == "County-Public-Safety"
    assert components["department"] == "Fire-EMS"
    assert components["channel"] == "Dispatch-1"
    assert components["unit_id"] == "unknown"


def test_recording_identity_does_not_depend_on_recording_path(tmp_path: Path) -> None:
    observed_at = datetime(2026, 8, 3, 5, 30, tzinfo=UTC)
    common = {
        "source": RecordingSource(endpoint="scanner"),
        "started_at": observed_at,
        "stopped_at": observed_at + timedelta(seconds=1),
        "elapsed_seconds": 1.0,
        "packets": 25,
        "samples": 8_000,
        "audio_duration_seconds": 1.0,
        "reliability": AudioReliabilitySnapshot(),
        "started_state": RecordingState(channel="Dispatch"),
    }

    first = RecordingMetadata(
        recording_path=tmp_path / "first.wav",
        **common,
    )
    second = RecordingMetadata(
        recording_path=tmp_path / "moved.wav",
        **common,
    )

    assert (
        RecordingIdentity.from_metadata(first).components
        == RecordingIdentity.from_metadata(second).components
    )
