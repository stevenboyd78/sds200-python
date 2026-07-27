from __future__ import annotations

import sys
from typing import TextIO

from rich.console import Console
from rich.style import Style
from rich.text import Text

from .models import ScannerInfo
from .presentation import ScannerPresentation, present_radio_state
from .state import RadioStateSnapshot
from .theme import (
    DEFAULT_DARK_THEME,
    ThemePalette,
    ThemeRole,
    ThemeStyle,
    theme_roles_for,
)


class RichCliRenderer:
    """Render human CLI output from semantic presentation and theme data."""

    def __init__(
        self,
        *,
        palette: ThemePalette = DEFAULT_DARK_THEME,
        console: Console | None = None,
        file: TextIO | None = None,
    ) -> None:
        if console is not None and file is not None:
            raise ValueError("console and file are mutually exclusive")
        self._palette = palette
        self._console = console or Console(
            file=file or sys.stdout,
            highlight=False,
            markup=False,
        )

    @property
    def palette(self) -> ThemePalette:
        return self._palette

    def style_for(self, role: ThemeRole | str) -> Style:
        """Resolve one semantic role into a Rich style."""

        return rich_style(self._palette.resolve(role))

    def print_scanner_info(
        self,
        info: ScannerInfo,
        *,
        connected: bool | None = True,
    ) -> None:
        """Print scanner information with semantic terminal styling."""

        presentation = _presentation_for_info(info, connected=connected)
        roles = theme_roles_for(presentation)
        primary = ThemeRole.TEXT_PRIMARY
        muted = ThemeRole.TEXT_MUTED

        rows: tuple[tuple[str, object, ThemeRole], ...] = (
            ("Mode", info.mode, roles.activity),
            ("Screen", info.screen, roles.activity),
            ("System", info.system, primary),
            ("Department", info.department, primary),
            ("Site", info.site, primary),
            ("Channel", info.channel, primary),
            ("Frequency", info.frequency, primary),
            ("Modulation", info.modulation, primary),
            ("Service", info.service_type, primary),
            ("Signal", info.signal, roles.signal),
            ("RSSI", _number_or_dash(info.rssi), primary),
            ("Battery", _number_or_dash(info.battery), primary),
            (
                "Recording",
                info.recording or "-",
                roles.recording or muted,
            ),
            ("Mute", info.mute or "-", roles.muted or primary),
        )

        for label, value, role in rows:
            line = Text()
            line.append(f"{label + ':':12s}", style=self.style_for(muted))
            line.append(str(value), style=self.style_for(role))
            self._console.print(line, soft_wrap=True)


def rich_style(style: ThemeStyle) -> Style:
    """Convert one renderer-neutral theme style into a Rich style."""

    return Style(
        color=style.foreground,
        bgcolor=style.background,
        bold=style.bold,
        dim=style.dim,
        underline=style.underline,
    )


def _presentation_for_info(
    info: ScannerInfo,
    *,
    connected: bool | None,
) -> ScannerPresentation:
    snapshot = RadioStateSnapshot(
        mode=info.mode,
        screen=info.screen,
        system=info.system,
        department=info.department,
        site=info.site,
        channel=info.channel,
        frequency=info.frequency,
        modulation=info.modulation,
        service_type=info.service_type,
        talkgroup_id=info.talkgroup_id,
        unit_id=info.unit_id,
        volume=info.volume,
        squelch=info.squelch,
        signal=info.signal,
        rssi=info.rssi,
        p25_status=info.p25_status,
        mute=info.mute,
        recording=info.recording,
    )
    return present_radio_state(snapshot, connected=connected)


def _number_or_dash(value: float | None) -> str:
    return f"{value:g}" if value is not None else "-"
