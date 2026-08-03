from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from sds200 import RecordingIdentity, RecordingOrganizationPolicy
from sds200.state import RadioStateSnapshot


def identity() -> RecordingIdentity:
    return RecordingIdentity.from_start_boundary(
        started_at=datetime(2026, 8, 3, 5, 30, tzinfo=UTC),
        endpoint="rtsp://192.168.0.251/audio",
        scanner="SDS/200",
        state=RadioStateSnapshot(
            system="County / Public Safety",
            department="Fire & EMS",
            site="North",
            channel="Dispatch: 1",
        ),
    )


def test_empty_recording_organization_preserves_base_directory() -> None:
    policy = RecordingOrganizationPolicy()

    assert not policy.enabled
    assert policy.relative_directory(identity()) == Path()


def test_recording_organization_renders_ordered_safe_components() -> None:
    policy = RecordingOrganizationPolicy.from_csv(
        "scanner, date, system, department, site, channel"
    )

    assert policy.components == (
        "scanner",
        "date",
        "system",
        "department",
        "site",
        "channel",
    )
    assert policy.relative_directory(identity()) == Path(
        "SDS-200",
        "2026-08-03",
        "County-Public-Safety",
        "Fire-EMS",
        "North",
        "Dispatch-1",
    )


def test_recording_organization_uses_unknown_for_missing_values() -> None:
    policy = RecordingOrganizationPolicy(("scanner", "site", "channel"))
    missing = RecordingIdentity.from_start_boundary(
        started_at=datetime(2026, 8, 3, 5, 30, tzinfo=UTC),
        endpoint="scanner",
    )

    assert policy.relative_directory(missing) == Path(
        "unknown",
        "unknown",
        "unknown",
    )


@pytest.mark.parametrize(
    ("components", "message"),
    [
        (("scanner", ""), "must not be empty"),
        (("scanner", ".."), "Unsupported"),
        (("scanner/system",), "Unsupported"),
        (("scanner", "scanner"), "Duplicate"),
    ],
)
def test_recording_organization_rejects_unsafe_or_invalid_components(
    components: tuple[str, ...],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        RecordingOrganizationPolicy(components)
