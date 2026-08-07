from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from sds200.recording_identity import RecordingIdentity
from sds200.recording_metadata import recording_metadata_path
from sds200.recording_organization import RecordingOrganizationPolicy
from sds200.recording_paths import (
    DEFAULT_RECORDING_TEMPLATE,
    RecordingPathPolicy,
)
from sds200.state import RadioStateSnapshot


def test_recording_path_policy_uses_stable_default_template(
    tmp_path: Path,
) -> None:
    policy = RecordingPathPolicy(directory=tmp_path)

    assert policy.template == DEFAULT_RECORDING_TEMPLATE
    assert policy.repeatable
    assert policy.enabled
    assert policy.display_path == tmp_path


@pytest.mark.parametrize(
    ("template", "message"),
    (
        ("{channel}.wav", "include only"),
        ("static.wav", "include only"),
        ("nested/{timestamp}.wav", "file name"),
        ("{timestamp}.raw", r"\.wav"),
    ),
)
def test_recording_path_policy_rejects_unsafe_templates(
    tmp_path: Path,
    template: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        RecordingPathPolicy(directory=tmp_path, template=template)


def test_recording_path_policy_allocates_around_existing_sidecar(
    tmp_path: Path,
) -> None:
    observed_at = datetime(2026, 8, 7, 19, 30, tzinfo=UTC)
    policy = RecordingPathPolicy(directory=tmp_path)

    first = policy.next_path(
        observed_at,
        explicit_used=False,
        metadata=True,
    )
    recording_metadata_path(first).write_text("{}\n", encoding="utf-8")

    second = policy.next_path(
        observed_at,
        explicit_used=False,
        metadata=True,
    )

    assert first.name == "sds200-20260807-193000.wav"
    assert second.name == "sds200-20260807-193000-2.wav"


def test_recording_path_policy_organizes_from_start_identity(
    tmp_path: Path,
) -> None:
    observed_at = datetime(2026, 8, 7, 19, 30, tzinfo=UTC)
    identity = RecordingIdentity.from_start_boundary(
        started_at=observed_at,
        endpoint="192.0.2.25",
        scanner="SDS/200",
        state=RadioStateSnapshot(
            system="County / Public Safety",
            channel="Dispatch: 1",
        ),
    )
    policy = RecordingPathPolicy(
        directory=tmp_path,
        organization=RecordingOrganizationPolicy.from_csv(
            "scanner,date,system,channel"
        ),
    )

    path = policy.next_path(
        observed_at,
        explicit_used=False,
        metadata=True,
        identity=identity,
    )

    assert path == (
        tmp_path
        / "SDS-200"
        / "2026-08-07"
        / "County-Public-Safety"
        / "Dispatch-1"
        / "sds200-20260807-193000.wav"
    )


def test_recording_path_policy_discovers_nested_wav_files(
    tmp_path: Path,
) -> None:
    nested = tmp_path / "SDS200" / "2026-08-07"
    nested.mkdir(parents=True)
    recording = nested / "dispatch.wav"
    recording.touch()

    policy = RecordingPathPolicy(directory=tmp_path)

    assert policy.library_paths() == (recording,)
