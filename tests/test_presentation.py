from dataclasses import FrozenInstanceError

import pytest

from sds200.presentation import (
    ActivityStatus,
    AvailabilityStatus,
    ConnectionStatus,
    HoldStatus,
    PresentationSeverity,
    ScannerPresentation,
    SignalLevel,
    classify_connection,
    classify_signal,
    present_radio_state,
)
from sds200.state import RadioStateSnapshot


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, SignalLevel.UNKNOWN),
        (-1, SignalLevel.NONE),
        (0, SignalLevel.NONE),
        (1, SignalLevel.WEAK),
        (2, SignalLevel.FAIR),
        (3, SignalLevel.GOOD),
        (4, SignalLevel.STRONG),
        (5, SignalLevel.STRONG),
    ],
)
def test_signal_levels_cover_scanner_scale(
    raw: int | None,
    expected: SignalLevel,
) -> None:
    assert classify_signal(raw) is expected


def test_connection_classification_prioritizes_disconnection() -> None:
    assert classify_connection(False, degraded=True) is ConnectionStatus.DISCONNECTED
    assert classify_connection(True, degraded=True) is ConnectionStatus.DEGRADED
    assert classify_connection(True) is ConnectionStatus.CONNECTED
    assert classify_connection(None) is ConnectionStatus.UNKNOWN


def test_present_radio_state_classifies_active_reception() -> None:
    presentation = present_radio_state(
        RadioStateSnapshot(
            mode="Trunk Scan",
            screen="trunk_scan",
            service_type=" Interop ",
            signal=5,
            mute="Unmute",
            recording="On",
        ),
        connected=True,
    )

    assert presentation == ScannerPresentation(
        connection=ConnectionStatus.CONNECTED,
        activity=ActivityStatus.RECEIVING,
        signal=SignalLevel.STRONG,
        hold=HoldStatus.NONE,
        availability=AvailabilityStatus.AVAILABLE,
        severity=PresentationSeverity.NORMAL,
        service_type="Interop",
        muted=False,
        recording=True,
        raw_signal=5,
    )


def test_hold_and_stale_states_are_independent() -> None:
    presentation = present_radio_state(
        RadioStateSnapshot(
            mode="Channel Hold",
            screen="channel_hold",
            signal=0,
            mute="Mute",
            recording="Off",
        ),
        connected=True,
        stale=True,
    )

    assert presentation.activity is ActivityStatus.HOLDING
    assert presentation.hold is HoldStatus.ACTIVE
    assert presentation.availability is AvailabilityStatus.STALE
    assert presentation.severity is PresentationSeverity.WARNING
    assert presentation.muted is True
    assert presentation.recording is False


def test_disconnected_state_is_unavailable_error() -> None:
    presentation = present_radio_state(
        RadioStateSnapshot(mode="Trunk Scan", signal=4, mute="Unmute"),
        connected=False,
    )

    assert presentation.connection is ConnectionStatus.DISCONNECTED
    assert presentation.activity is ActivityStatus.UNKNOWN
    assert presentation.availability is AvailabilityStatus.UNAVAILABLE
    assert presentation.severity is PresentationSeverity.ERROR


def test_empty_snapshot_remains_semantically_unknown() -> None:
    presentation = present_radio_state(RadioStateSnapshot())

    assert presentation.connection is ConnectionStatus.UNKNOWN
    assert presentation.activity is ActivityStatus.UNKNOWN
    assert presentation.signal is SignalLevel.UNKNOWN
    assert presentation.hold is HoldStatus.UNKNOWN
    assert presentation.availability is AvailabilityStatus.UNKNOWN
    assert presentation.severity is PresentationSeverity.INFO


def test_presentation_is_immutable_and_serializes_without_renderer_types() -> None:
    presentation = present_radio_state(
        RadioStateSnapshot(mode="Trunk Scan", signal=2, mute="Mute"),
        connected=True,
    )

    with pytest.raises(FrozenInstanceError):
        presentation.raw_signal = 5  # type: ignore[misc]

    assert presentation.as_dict() == {
        "connection": "connected",
        "activity": "scanning",
        "signal": "fair",
        "hold": "none",
        "availability": "available",
        "severity": "normal",
        "service_type": None,
        "muted": True,
        "recording": None,
        "raw_signal": 2,
    }
