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
)
from sds200.theme import (
    DEFAULT_DARK_THEME,
    DEFAULT_LIGHT_THEME,
    PresentationThemeRoles,
    ThemePalette,
    ThemeRole,
    ThemeStyle,
    theme_roles_for,
)


def _presentation(
    *,
    muted: bool | None = None,
    recording: bool | None = None,
) -> ScannerPresentation:
    return ScannerPresentation(
        connection=ConnectionStatus.CONNECTED,
        activity=ActivityStatus.RECEIVING,
        signal=SignalLevel.STRONG,
        hold=HoldStatus.NONE,
        availability=AvailabilityStatus.AVAILABLE,
        severity=PresentationSeverity.NORMAL,
        service_type="Interop",
        muted=muted,
        recording=recording,
        raw_signal=5,
    )


def test_default_palettes_cover_every_theme_role() -> None:
    expected = set(ThemeRole)

    assert set(DEFAULT_DARK_THEME.styles) == expected
    assert set(DEFAULT_LIGHT_THEME.styles) == expected


def test_theme_palette_requires_a_complete_role_mapping() -> None:
    with pytest.raises(ValueError, match="missing roles"):
        ThemePalette(name="partial", styles={})


def test_theme_palette_rejects_blank_names() -> None:
    with pytest.raises(ValueError, match="name must not be blank"):
        ThemePalette(name=" ", styles=DEFAULT_DARK_THEME.styles)


def test_theme_style_rejects_blank_color_values() -> None:
    with pytest.raises(ValueError, match="foreground must not be blank"):
        ThemeStyle(foreground=" ")


def test_palette_and_styles_are_immutable() -> None:
    with pytest.raises(TypeError):
        DEFAULT_DARK_THEME.styles[
            ThemeRole.TEXT_PRIMARY
        ] = ThemeStyle()  # type: ignore[index]

    with pytest.raises(FrozenInstanceError):
        DEFAULT_DARK_THEME.name = "changed"  # type: ignore[misc]


def test_palette_resolves_enum_and_string_roles() -> None:
    expected = DEFAULT_DARK_THEME.styles[ThemeRole.SIGNAL_STRONG]

    assert DEFAULT_DARK_THEME.resolve(ThemeRole.SIGNAL_STRONG) is expected
    assert DEFAULT_DARK_THEME.resolve("signal.strong") is expected


def test_palette_overrides_preserve_completeness() -> None:
    replacement = ThemeStyle(foreground="#ffffff", underline=True)
    derived = DEFAULT_DARK_THEME.with_overrides(
        "high-contrast",
        {ThemeRole.TEXT_PRIMARY: replacement},
    )

    assert derived.name == "high-contrast"
    assert derived.resolve(ThemeRole.TEXT_PRIMARY) is replacement
    assert derived.resolve(ThemeRole.SIGNAL_STRONG) == DEFAULT_DARK_THEME.resolve(
        ThemeRole.SIGNAL_STRONG
    )


def test_theme_roles_cover_a_complete_scanner_presentation() -> None:
    roles = theme_roles_for(_presentation(muted=True, recording=True))

    assert roles == PresentationThemeRoles(
        connection=ThemeRole.CONNECTION_CONNECTED,
        activity=ThemeRole.ACTIVITY_RECEIVING,
        signal=ThemeRole.SIGNAL_STRONG,
        hold=ThemeRole.HOLD_NONE,
        availability=ThemeRole.AVAILABILITY_AVAILABLE,
        severity=ThemeRole.SEVERITY_NORMAL,
        muted=ThemeRole.STATE_MUTED,
        recording=ThemeRole.STATE_RECORDING,
    )


def test_inactive_boolean_states_do_not_emit_optional_roles() -> None:
    roles = theme_roles_for(_presentation(muted=False, recording=False))

    assert roles.muted is None
    assert roles.recording is None


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (ConnectionStatus.UNKNOWN, ThemeRole.CONNECTION_UNKNOWN),
        (ConnectionStatus.CONNECTED, ThemeRole.CONNECTION_CONNECTED),
        (ConnectionStatus.DEGRADED, ThemeRole.CONNECTION_DEGRADED),
        (ConnectionStatus.DISCONNECTED, ThemeRole.CONNECTION_DISCONNECTED),
    ],
)
def test_connection_roles_are_stable(
    status: ConnectionStatus,
    expected: ThemeRole,
) -> None:
    presentation = _presentation()
    presentation = ScannerPresentation(
        connection=status,
        activity=presentation.activity,
        signal=presentation.signal,
        hold=presentation.hold,
        availability=presentation.availability,
        severity=presentation.severity,
        service_type=presentation.service_type,
        muted=presentation.muted,
        recording=presentation.recording,
        raw_signal=presentation.raw_signal,
    )

    assert theme_roles_for(presentation).connection is expected


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        (SignalLevel.UNKNOWN, ThemeRole.SIGNAL_UNKNOWN),
        (SignalLevel.NONE, ThemeRole.SIGNAL_NONE),
        (SignalLevel.WEAK, ThemeRole.SIGNAL_WEAK),
        (SignalLevel.FAIR, ThemeRole.SIGNAL_FAIR),
        (SignalLevel.GOOD, ThemeRole.SIGNAL_GOOD),
        (SignalLevel.STRONG, ThemeRole.SIGNAL_STRONG),
    ],
)
def test_signal_roles_are_stable(level: SignalLevel, expected: ThemeRole) -> None:
    presentation = _presentation()
    presentation = ScannerPresentation(
        connection=presentation.connection,
        activity=presentation.activity,
        signal=level,
        hold=presentation.hold,
        availability=presentation.availability,
        severity=presentation.severity,
        service_type=presentation.service_type,
        muted=presentation.muted,
        recording=presentation.recording,
        raw_signal=presentation.raw_signal,
    )

    assert theme_roles_for(presentation).signal is expected


def test_role_and_palette_serialization_remain_renderer_neutral() -> None:
    roles = theme_roles_for(_presentation(muted=True))
    style = DEFAULT_DARK_THEME.resolve(roles.connection)

    assert roles.as_dict() == {
        "connection": "connection.connected",
        "activity": "activity.receiving",
        "signal": "signal.strong",
        "hold": "hold.none",
        "availability": "availability.available",
        "severity": "severity.normal",
        "muted": "state.muted",
        "recording": None,
    }
    assert style.as_dict() == {
        "foreground": "#5fd75f",
        "background": None,
        "bold": True,
        "dim": False,
        "underline": False,
    }


def test_light_and_dark_palettes_share_roles_but_not_all_colors() -> None:
    assert DEFAULT_DARK_THEME.as_dict()["styles"].keys() == (
        DEFAULT_LIGHT_THEME.as_dict()["styles"].keys()
    )
    assert DEFAULT_DARK_THEME.resolve(ThemeRole.TEXT_PRIMARY) != (
        DEFAULT_LIGHT_THEME.resolve(ThemeRole.TEXT_PRIMARY)
    )
