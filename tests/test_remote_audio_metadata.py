from dataclasses import FrozenInstanceError

import pytest

from sds200 import (
    DEFAULT_REMOTE_STREAM_TITLE_MAX_LENGTH,
    RemoteStreamMetadata,
    remote_stream_metadata_from_state,
)
from sds200.presentation import ActivityStatus, AvailabilityStatus
from sds200.state import RadioStateSnapshot


def test_remote_stream_metadata_formats_active_channel_title() -> None:
    metadata = remote_stream_metadata_from_state(
        RadioStateSnapshot(
            mode="Trunk Scan",
            system="County",
            department="Fire",
            site="North",
            channel="Dispatch",
            frequency="154.1900",
            service_type="Fire Dispatch",
            talkgroup_id="1201",
            unit_id="Engine 4",
            signal=4,
            mute="Unmute",
        ),
        connected=True,
    )

    assert metadata.activity is ActivityStatus.RECEIVING
    assert metadata.availability is AvailabilityStatus.AVAILABLE
    assert metadata.render_title() == "County | Fire | Dispatch"
    assert metadata.site == "North"
    assert metadata.frequency == "154.1900"
    assert metadata.service_type == "Fire Dispatch"
    assert metadata.talkgroup_id == "1201"
    assert metadata.unit_id == "Engine 4"


def test_remote_stream_metadata_uses_frequency_without_channel() -> None:
    metadata = remote_stream_metadata_from_state(
        RadioStateSnapshot(
            mode="Custom Search",
            frequency="460.1250",
            signal=3,
            mute="Unmute",
        ),
        connected=True,
    )

    assert metadata.render_title() == "460.1250"


def test_remote_stream_metadata_formats_scanning_state() -> None:
    metadata = remote_stream_metadata_from_state(
        RadioStateSnapshot(
            mode="Trunk Scan",
            signal=0,
            mute="Unmute",
        ),
        connected=True,
    )

    assert metadata.activity is ActivityStatus.SCANNING
    assert metadata.render_title() == "Scanning"


def test_remote_stream_metadata_formats_idle_state() -> None:
    metadata = remote_stream_metadata_from_state(
        RadioStateSnapshot(
            mode="Manual",
            signal=0,
            mute="Mute",
        ),
        connected=True,
    )

    assert metadata.activity is ActivityStatus.IDLE
    assert metadata.render_title() == "Scanner idle"


def test_remote_stream_metadata_formats_unavailable_state() -> None:
    metadata = remote_stream_metadata_from_state(
        RadioStateSnapshot(
            mode="Trunk Scan",
            channel="Dispatch",
        ),
        connected=False,
    )

    assert metadata.availability is AvailabilityStatus.UNAVAILABLE
    assert metadata.render_title() == "Scanner unavailable"


def test_remote_stream_metadata_formats_stale_state() -> None:
    metadata = remote_stream_metadata_from_state(
        RadioStateSnapshot(
            mode="Trunk Scan",
            channel="Dispatch",
        ),
        connected=True,
        stale=True,
    )

    assert metadata.availability is AvailabilityStatus.STALE
    assert metadata.render_title() == "Scanner state stale"


def test_remote_stream_metadata_normalizes_whitespace() -> None:
    metadata = remote_stream_metadata_from_state(
        RadioStateSnapshot(
            system="  County   Public Safety ",
            department=" Fire  and   EMS ",
            channel=" Dispatch   One ",
            service_type=" Fire   Dispatch ",
        )
    )

    assert metadata.system == "County Public Safety"
    assert metadata.department == "Fire and EMS"
    assert metadata.channel == "Dispatch One"
    assert metadata.service_type == "Fire Dispatch"
    assert metadata.render_title() == (
        "County Public Safety | Fire and EMS | Dispatch One"
    )


def test_remote_stream_metadata_rejects_control_characters() -> None:
    with pytest.raises(ValueError, match="channel contains control characters"):
        remote_stream_metadata_from_state(
            RadioStateSnapshot(channel="Dispatch\nInjected")
        )


def test_remote_stream_metadata_deduplicates_and_bounds_title() -> None:
    metadata = RemoteStreamMetadata(
        activity=ActivityStatus.RECEIVING,
        availability=AvailabilityStatus.AVAILABLE,
        system="County",
        department="county",
        channel="Dispatch",
    )

    assert metadata.render_title() == "County | Dispatch"
    assert metadata.render_title(max_length=8) == "County"
    assert DEFAULT_REMOTE_STREAM_TITLE_MAX_LENGTH == 160


@pytest.mark.parametrize("maximum", [0, -1, True, 1.5])
def test_remote_stream_metadata_rejects_invalid_title_lengths(
    maximum: object,
) -> None:
    metadata = RemoteStreamMetadata(
        activity=ActivityStatus.SCANNING,
        availability=AvailabilityStatus.AVAILABLE,
    )

    with pytest.raises(ValueError, match="maximum length must be positive"):
        metadata.render_title(max_length=maximum)  # type: ignore[arg-type]


def test_remote_stream_metadata_is_immutable_and_serializable() -> None:
    metadata = RemoteStreamMetadata(
        activity=ActivityStatus.RECEIVING,
        availability=AvailabilityStatus.AVAILABLE,
        system="County",
        channel="Dispatch",
    )

    with pytest.raises(FrozenInstanceError):
        metadata.channel = "Tac 1"  # type: ignore[misc]

    assert metadata.as_dict() == {
        "title": "County | Dispatch",
        "activity": "receiving",
        "availability": "available",
        "system": "County",
        "department": None,
        "site": None,
        "channel": "Dispatch",
        "frequency": None,
        "service_type": None,
        "talkgroup_id": None,
        "unit_id": None,
    }
