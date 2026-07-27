from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from .presentation import (
    ActivityStatus,
    AvailabilityStatus,
    ConnectionStatus,
    HoldStatus,
    PresentationSeverity,
    ScannerPresentation,
    SignalLevel,
)


class ThemeRole(StrEnum):
    """Stable semantic role that renderers may map to concrete styles."""

    TEXT_PRIMARY = "text.primary"
    TEXT_MUTED = "text.muted"
    CONNECTION_UNKNOWN = "connection.unknown"
    CONNECTION_CONNECTED = "connection.connected"
    CONNECTION_DEGRADED = "connection.degraded"
    CONNECTION_DISCONNECTED = "connection.disconnected"
    ACTIVITY_UNKNOWN = "activity.unknown"
    ACTIVITY_IDLE = "activity.idle"
    ACTIVITY_SCANNING = "activity.scanning"
    ACTIVITY_RECEIVING = "activity.receiving"
    ACTIVITY_HOLDING = "activity.holding"
    SIGNAL_UNKNOWN = "signal.unknown"
    SIGNAL_NONE = "signal.none"
    SIGNAL_WEAK = "signal.weak"
    SIGNAL_FAIR = "signal.fair"
    SIGNAL_GOOD = "signal.good"
    SIGNAL_STRONG = "signal.strong"
    HOLD_UNKNOWN = "hold.unknown"
    HOLD_NONE = "hold.none"
    HOLD_ACTIVE = "hold.active"
    AVAILABILITY_UNKNOWN = "availability.unknown"
    AVAILABILITY_AVAILABLE = "availability.available"
    AVAILABILITY_STALE = "availability.stale"
    AVAILABILITY_UNAVAILABLE = "availability.unavailable"
    SEVERITY_NORMAL = "severity.normal"
    SEVERITY_INFO = "severity.info"
    SEVERITY_WARNING = "severity.warning"
    SEVERITY_ERROR = "severity.error"
    STATE_MUTED = "state.muted"
    STATE_RECORDING = "state.recording"


@dataclass(frozen=True, slots=True)
class ThemeStyle:
    """Renderer-neutral style data for one semantic theme role."""

    foreground: str | None = None
    background: str | None = None
    bold: bool = False
    dim: bool = False
    underline: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "foreground",
            _normalize_style_value(self.foreground, "foreground"),
        )
        object.__setattr__(
            self,
            "background",
            _normalize_style_value(self.background, "background"),
        )

    def as_dict(self) -> dict[str, object]:
        """Return JSON-compatible style data without renderer-specific objects."""

        return {
            "foreground": self.foreground,
            "background": self.background,
            "bold": self.bold,
            "dim": self.dim,
            "underline": self.underline,
        }


def _normalize_style_value(value: str | None, attribute: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{attribute} must not be blank")
    return normalized


@dataclass(frozen=True, slots=True)
class ThemePalette:
    """Complete immutable mapping from semantic roles to generic styles."""

    name: str
    styles: Mapping[ThemeRole, ThemeStyle]

    def __post_init__(self) -> None:
        name = self.name.strip()
        if not name:
            raise ValueError("theme palette name must not be blank")

        normalized: dict[ThemeRole, ThemeStyle] = {}
        for role, style in self.styles.items():
            if not isinstance(style, ThemeStyle):
                raise TypeError(f"style for {role!s} must be a ThemeStyle")
            normalized[ThemeRole(role)] = style

        missing = tuple(role.value for role in ThemeRole if role not in normalized)
        if missing:
            raise ValueError(f"theme palette is missing roles: {', '.join(missing)}")

        object.__setattr__(self, "name", name)
        object.__setattr__(self, "styles", MappingProxyType(normalized))

    def resolve(self, role: ThemeRole | str) -> ThemeStyle:
        """Resolve one semantic role into its generic style."""

        return self.styles[ThemeRole(role)]

    def with_overrides(
        self,
        name: str,
        overrides: Mapping[ThemeRole, ThemeStyle],
    ) -> ThemePalette:
        """Create a complete derived palette with selected role overrides."""

        styles = dict(self.styles)
        styles.update(overrides)
        return ThemePalette(name=name, styles=styles)

    def as_dict(self) -> dict[str, object]:
        """Return a deterministic renderer-neutral palette document."""

        return {
            "name": self.name,
            "styles": {
                role.value: self.styles[role].as_dict() for role in ThemeRole
            },
        }


@dataclass(frozen=True, slots=True)
class PresentationThemeRoles:
    """Semantic theme roles selected for one scanner presentation."""

    connection: ThemeRole
    activity: ThemeRole
    signal: ThemeRole
    hold: ThemeRole
    availability: ThemeRole
    severity: ThemeRole
    muted: ThemeRole | None
    recording: ThemeRole | None

    def as_dict(self) -> dict[str, str | None]:
        """Return stable role names for JSON, Rich, or Textual adapters."""

        return {
            "connection": self.connection.value,
            "activity": self.activity.value,
            "signal": self.signal.value,
            "hold": self.hold.value,
            "availability": self.availability.value,
            "severity": self.severity.value,
            "muted": self.muted.value if self.muted is not None else None,
            "recording": (
                self.recording.value if self.recording is not None else None
            ),
        }


_CONNECTION_ROLES = MappingProxyType(
    {
        ConnectionStatus.UNKNOWN: ThemeRole.CONNECTION_UNKNOWN,
        ConnectionStatus.CONNECTED: ThemeRole.CONNECTION_CONNECTED,
        ConnectionStatus.DEGRADED: ThemeRole.CONNECTION_DEGRADED,
        ConnectionStatus.DISCONNECTED: ThemeRole.CONNECTION_DISCONNECTED,
    }
)
_ACTIVITY_ROLES = MappingProxyType(
    {
        ActivityStatus.UNKNOWN: ThemeRole.ACTIVITY_UNKNOWN,
        ActivityStatus.IDLE: ThemeRole.ACTIVITY_IDLE,
        ActivityStatus.SCANNING: ThemeRole.ACTIVITY_SCANNING,
        ActivityStatus.RECEIVING: ThemeRole.ACTIVITY_RECEIVING,
        ActivityStatus.HOLDING: ThemeRole.ACTIVITY_HOLDING,
    }
)
_SIGNAL_ROLES = MappingProxyType(
    {
        SignalLevel.UNKNOWN: ThemeRole.SIGNAL_UNKNOWN,
        SignalLevel.NONE: ThemeRole.SIGNAL_NONE,
        SignalLevel.WEAK: ThemeRole.SIGNAL_WEAK,
        SignalLevel.FAIR: ThemeRole.SIGNAL_FAIR,
        SignalLevel.GOOD: ThemeRole.SIGNAL_GOOD,
        SignalLevel.STRONG: ThemeRole.SIGNAL_STRONG,
    }
)
_HOLD_ROLES = MappingProxyType(
    {
        HoldStatus.UNKNOWN: ThemeRole.HOLD_UNKNOWN,
        HoldStatus.NONE: ThemeRole.HOLD_NONE,
        HoldStatus.ACTIVE: ThemeRole.HOLD_ACTIVE,
    }
)
_AVAILABILITY_ROLES = MappingProxyType(
    {
        AvailabilityStatus.UNKNOWN: ThemeRole.AVAILABILITY_UNKNOWN,
        AvailabilityStatus.AVAILABLE: ThemeRole.AVAILABILITY_AVAILABLE,
        AvailabilityStatus.STALE: ThemeRole.AVAILABILITY_STALE,
        AvailabilityStatus.UNAVAILABLE: ThemeRole.AVAILABILITY_UNAVAILABLE,
    }
)
_SEVERITY_ROLES = MappingProxyType(
    {
        PresentationSeverity.NORMAL: ThemeRole.SEVERITY_NORMAL,
        PresentationSeverity.INFO: ThemeRole.SEVERITY_INFO,
        PresentationSeverity.WARNING: ThemeRole.SEVERITY_WARNING,
        PresentationSeverity.ERROR: ThemeRole.SEVERITY_ERROR,
    }
)


def theme_roles_for(presentation: ScannerPresentation) -> PresentationThemeRoles:
    """Select semantic theme roles without choosing a renderer or palette."""

    return PresentationThemeRoles(
        connection=_CONNECTION_ROLES[presentation.connection],
        activity=_ACTIVITY_ROLES[presentation.activity],
        signal=_SIGNAL_ROLES[presentation.signal],
        hold=_HOLD_ROLES[presentation.hold],
        availability=_AVAILABILITY_ROLES[presentation.availability],
        severity=_SEVERITY_ROLES[presentation.severity],
        muted=ThemeRole.STATE_MUTED if presentation.muted is True else None,
        recording=(
            ThemeRole.STATE_RECORDING if presentation.recording is True else None
        ),
    )


def _style(
    foreground: str | None = None,
    *,
    bold: bool = False,
    dim: bool = False,
    underline: bool = False,
) -> ThemeStyle:
    return ThemeStyle(
        foreground=foreground,
        bold=bold,
        dim=dim,
        underline=underline,
    )


DEFAULT_DARK_THEME = ThemePalette(
    name="default-dark",
    styles={
        ThemeRole.TEXT_PRIMARY: _style("#f5f5f5"),
        ThemeRole.TEXT_MUTED: _style("#a3a3a3", dim=True),
        ThemeRole.CONNECTION_UNKNOWN: _style("#a3a3a3", dim=True),
        ThemeRole.CONNECTION_CONNECTED: _style("#5fd75f", bold=True),
        ThemeRole.CONNECTION_DEGRADED: _style("#ffaf00", bold=True),
        ThemeRole.CONNECTION_DISCONNECTED: _style("#ff5f5f", bold=True),
        ThemeRole.ACTIVITY_UNKNOWN: _style("#a3a3a3", dim=True),
        ThemeRole.ACTIVITY_IDLE: _style("#f5f5f5"),
        ThemeRole.ACTIVITY_SCANNING: _style("#5fafff"),
        ThemeRole.ACTIVITY_RECEIVING: _style("#5fd7d7", bold=True),
        ThemeRole.ACTIVITY_HOLDING: _style("#d787ff", bold=True),
        ThemeRole.SIGNAL_UNKNOWN: _style("#a3a3a3", dim=True),
        ThemeRole.SIGNAL_NONE: _style("#737373", dim=True),
        ThemeRole.SIGNAL_WEAK: _style("#ff5f5f"),
        ThemeRole.SIGNAL_FAIR: _style("#ffaf00"),
        ThemeRole.SIGNAL_GOOD: _style("#afd75f"),
        ThemeRole.SIGNAL_STRONG: _style("#5fd75f", bold=True),
        ThemeRole.HOLD_UNKNOWN: _style("#a3a3a3", dim=True),
        ThemeRole.HOLD_NONE: _style("#f5f5f5"),
        ThemeRole.HOLD_ACTIVE: _style("#d787ff", bold=True),
        ThemeRole.AVAILABILITY_UNKNOWN: _style("#a3a3a3", dim=True),
        ThemeRole.AVAILABILITY_AVAILABLE: _style("#f5f5f5"),
        ThemeRole.AVAILABILITY_STALE: _style("#ffaf00"),
        ThemeRole.AVAILABILITY_UNAVAILABLE: _style("#ff5f5f"),
        ThemeRole.SEVERITY_NORMAL: _style("#f5f5f5"),
        ThemeRole.SEVERITY_INFO: _style("#5fafff"),
        ThemeRole.SEVERITY_WARNING: _style("#ffaf00", bold=True),
        ThemeRole.SEVERITY_ERROR: _style("#ff5f5f", bold=True),
        ThemeRole.STATE_MUTED: _style("#a3a3a3", dim=True),
        ThemeRole.STATE_RECORDING: _style("#ff5f5f", bold=True),
    },
)

DEFAULT_LIGHT_THEME = ThemePalette(
    name="default-light",
    styles={
        ThemeRole.TEXT_PRIMARY: _style("#1f2937"),
        ThemeRole.TEXT_MUTED: _style("#6b7280", dim=True),
        ThemeRole.CONNECTION_UNKNOWN: _style("#6b7280", dim=True),
        ThemeRole.CONNECTION_CONNECTED: _style("#15803d", bold=True),
        ThemeRole.CONNECTION_DEGRADED: _style("#b45309", bold=True),
        ThemeRole.CONNECTION_DISCONNECTED: _style("#b91c1c", bold=True),
        ThemeRole.ACTIVITY_UNKNOWN: _style("#6b7280", dim=True),
        ThemeRole.ACTIVITY_IDLE: _style("#1f2937"),
        ThemeRole.ACTIVITY_SCANNING: _style("#1d4ed8"),
        ThemeRole.ACTIVITY_RECEIVING: _style("#0f766e", bold=True),
        ThemeRole.ACTIVITY_HOLDING: _style("#7e22ce", bold=True),
        ThemeRole.SIGNAL_UNKNOWN: _style("#6b7280", dim=True),
        ThemeRole.SIGNAL_NONE: _style("#9ca3af", dim=True),
        ThemeRole.SIGNAL_WEAK: _style("#b91c1c"),
        ThemeRole.SIGNAL_FAIR: _style("#b45309"),
        ThemeRole.SIGNAL_GOOD: _style("#4d7c0f"),
        ThemeRole.SIGNAL_STRONG: _style("#15803d", bold=True),
        ThemeRole.HOLD_UNKNOWN: _style("#6b7280", dim=True),
        ThemeRole.HOLD_NONE: _style("#1f2937"),
        ThemeRole.HOLD_ACTIVE: _style("#7e22ce", bold=True),
        ThemeRole.AVAILABILITY_UNKNOWN: _style("#6b7280", dim=True),
        ThemeRole.AVAILABILITY_AVAILABLE: _style("#1f2937"),
        ThemeRole.AVAILABILITY_STALE: _style("#b45309"),
        ThemeRole.AVAILABILITY_UNAVAILABLE: _style("#b91c1c"),
        ThemeRole.SEVERITY_NORMAL: _style("#1f2937"),
        ThemeRole.SEVERITY_INFO: _style("#1d4ed8"),
        ThemeRole.SEVERITY_WARNING: _style("#b45309", bold=True),
        ThemeRole.SEVERITY_ERROR: _style("#b91c1c", bold=True),
        ThemeRole.STATE_MUTED: _style("#6b7280", dim=True),
        ThemeRole.STATE_RECORDING: _style("#b91c1c", bold=True),
    },
)
