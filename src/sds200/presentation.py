from __future__ import annotations

from dataclasses import dataclass, fields
from enum import StrEnum

from .models import ScannerInfo
from .state import RadioStateSnapshot, snapshot_from_scanner_info


class ConnectionStatus(StrEnum):
    """Semantic scanner connection state."""

    UNKNOWN = "unknown"
    CONNECTED = "connected"
    DEGRADED = "degraded"
    DISCONNECTED = "disconnected"


class ActivityStatus(StrEnum):
    """Semantic description of the scanner's current activity."""

    UNKNOWN = "unknown"
    IDLE = "idle"
    SCANNING = "scanning"
    RECEIVING = "receiving"
    HOLDING = "holding"


class SignalLevel(StrEnum):
    """Renderer-independent signal-strength band."""

    UNKNOWN = "unknown"
    NONE = "none"
    WEAK = "weak"
    FAIR = "fair"
    GOOD = "good"
    STRONG = "strong"


class HoldStatus(StrEnum):
    """Whether the scanner is semantically in a hold state."""

    UNKNOWN = "unknown"
    NONE = "none"
    ACTIVE = "active"


class AvailabilityStatus(StrEnum):
    """Freshness and availability of scanner state."""

    UNKNOWN = "unknown"
    AVAILABLE = "available"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


class PresentationSeverity(StrEnum):
    """Importance level for a semantic presentation state."""

    NORMAL = "normal"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ScannerPresentation:
    """Immutable semantic state shared by terminal and TUI renderers."""

    connection: ConnectionStatus
    activity: ActivityStatus
    signal: SignalLevel
    hold: HoldStatus
    availability: AvailabilityStatus
    severity: PresentationSeverity
    service_type: str | None
    muted: bool | None
    recording: bool | None
    raw_signal: int | None

    def as_dict(self) -> dict[str, object]:
        """Return a renderer-neutral, JSON-compatible representation."""

        return {
            "connection": self.connection.value,
            "activity": self.activity.value,
            "signal": self.signal.value,
            "hold": self.hold.value,
            "availability": self.availability.value,
            "severity": self.severity.value,
            "service_type": self.service_type,
            "muted": self.muted,
            "recording": self.recording,
            "raw_signal": self.raw_signal,
        }


_MUTE_TRUE = frozenset({"1", "true", "yes", "on", "mute", "muted"})
_MUTE_FALSE = frozenset({"0", "false", "no", "off", "unmute", "unmuted"})
_RECORDING_TRUE = frozenset({"1", "true", "yes", "on", "record", "recording"})
_RECORDING_FALSE = frozenset({"0", "false", "no", "off", "idle"})
_SCANNING_TERMS = ("scan", "search", "close call", "weather")


def classify_connection(
    connected: bool | None,
    *,
    degraded: bool = False,
) -> ConnectionStatus:
    """Classify connection health without assigning display styles."""

    if connected is False:
        return ConnectionStatus.DISCONNECTED
    if degraded:
        return ConnectionStatus.DEGRADED
    if connected is True:
        return ConnectionStatus.CONNECTED
    return ConnectionStatus.UNKNOWN


def classify_signal(signal: int | None) -> SignalLevel:
    """Map the SDS scanner signal scale into stable semantic bands."""

    if signal is None:
        return SignalLevel.UNKNOWN
    if signal <= 0:
        return SignalLevel.NONE
    if signal == 1:
        return SignalLevel.WEAK
    if signal == 2:
        return SignalLevel.FAIR
    if signal == 3:
        return SignalLevel.GOOD
    return SignalLevel.STRONG


def present_radio_state(
    snapshot: RadioStateSnapshot,
    *,
    connected: bool | None = None,
    degraded: bool = False,
    stale: bool = False,
) -> ScannerPresentation:
    """Derive renderer-independent meaning from one radio-state snapshot."""

    connection = classify_connection(connected, degraded=degraded)
    hold = _classify_hold(snapshot)
    muted = _parse_flag(snapshot.mute, _MUTE_TRUE, _MUTE_FALSE)
    recording = _parse_flag(
        snapshot.recording,
        _RECORDING_TRUE,
        _RECORDING_FALSE,
    )
    availability = _classify_availability(snapshot, connection, stale=stale)
    activity = _classify_activity(
        snapshot,
        connection=connection,
        hold=hold,
        muted=muted,
    )
    severity = _classify_severity(connection, availability)
    service_type = snapshot.service_type.strip() if snapshot.service_type else None

    return ScannerPresentation(
        connection=connection,
        activity=activity,
        signal=classify_signal(snapshot.signal),
        hold=hold,
        availability=availability,
        severity=severity,
        service_type=service_type or None,
        muted=muted,
        recording=recording,
        raw_signal=snapshot.signal,
    )


def present_scanner_info(
    info: ScannerInfo,
    *,
    connected: bool | None = None,
    degraded: bool = False,
    stale: bool = False,
) -> ScannerPresentation:
    """Derive renderer-independent meaning directly from scanner XML data."""

    return present_radio_state(
        snapshot_from_scanner_info(info),
        connected=connected,
        degraded=degraded,
        stale=stale,
    )


def _classify_hold(snapshot: RadioStateSnapshot) -> HoldStatus:
    values = tuple(value for value in (snapshot.mode, snapshot.screen) if value)
    if not values:
        return HoldStatus.UNKNOWN
    text = " ".join(values).casefold()
    return HoldStatus.ACTIVE if "hold" in text else HoldStatus.NONE


def _classify_activity(
    snapshot: RadioStateSnapshot,
    *,
    connection: ConnectionStatus,
    hold: HoldStatus,
    muted: bool | None,
) -> ActivityStatus:
    if connection is ConnectionStatus.DISCONNECTED:
        return ActivityStatus.UNKNOWN
    if muted is False and snapshot.signal is not None and snapshot.signal > 0:
        return ActivityStatus.RECEIVING
    if hold is HoldStatus.ACTIVE:
        return ActivityStatus.HOLDING

    text = " ".join(
        value for value in (snapshot.mode, snapshot.screen) if value
    ).casefold()
    if any(term in text for term in _SCANNING_TERMS):
        return ActivityStatus.SCANNING
    if _snapshot_has_data(snapshot):
        return ActivityStatus.IDLE
    return ActivityStatus.UNKNOWN


def _classify_availability(
    snapshot: RadioStateSnapshot,
    connection: ConnectionStatus,
    *,
    stale: bool,
) -> AvailabilityStatus:
    if connection is ConnectionStatus.DISCONNECTED:
        return AvailabilityStatus.UNAVAILABLE
    if stale:
        return AvailabilityStatus.STALE
    if _snapshot_has_data(snapshot):
        return AvailabilityStatus.AVAILABLE
    return AvailabilityStatus.UNKNOWN


def _classify_severity(
    connection: ConnectionStatus,
    availability: AvailabilityStatus,
) -> PresentationSeverity:
    if connection is ConnectionStatus.DISCONNECTED:
        return PresentationSeverity.ERROR
    if (
        connection is ConnectionStatus.DEGRADED
        or availability is AvailabilityStatus.STALE
    ):
        return PresentationSeverity.WARNING
    if (
        connection is ConnectionStatus.UNKNOWN
        or availability is AvailabilityStatus.UNKNOWN
    ):
        return PresentationSeverity.INFO
    return PresentationSeverity.NORMAL


def _snapshot_has_data(snapshot: RadioStateSnapshot) -> bool:
    return any(
        getattr(snapshot, field.name) is not None
        for field in fields(RadioStateSnapshot)
    )


def _parse_flag(
    value: str | None,
    true_values: frozenset[str],
    false_values: frozenset[str],
) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().casefold()
    if normalized in true_values:
        return True
    if normalized in false_values:
        return False
    return None
