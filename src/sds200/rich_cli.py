from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from typing import Literal, TextIO

from rich.console import Console
from rich.style import Style
from rich.text import Text

from .models import ScannerInfo
from .presentation import present_scanner_info
from .theme import (
    DEFAULT_DARK_THEME,
    DEFAULT_LIGHT_THEME,
    ThemePalette,
    ThemeRole,
    ThemeStyle,
    theme_roles_for,
)

ColorMode = Literal["auto", "always", "never"]
ThemeName = Literal["dark", "light"]
COLOR_MODES: tuple[ColorMode, ...] = ("auto", "always", "never")
THEME_NAMES: tuple[ThemeName, ...] = ("dark", "light")


class RichCliRenderer:
    """Render human CLI output from semantic presentation and theme data."""

    def __init__(
        self,
        *,
        palette: ThemePalette = DEFAULT_DARK_THEME,
        color: str = "auto",
        environ: Mapping[str, str] | None = None,
        console: Console | None = None,
        file: TextIO | None = None,
    ) -> None:
        if console is not None and file is not None:
            raise ValueError("console and file are mutually exclusive")
        self._palette = palette
        self._color_mode = resolve_color_mode(color, environ=environ)
        if console is not None:
            self._console = console
        else:
            force_terminal: bool | None = None
            no_color = False
            if self._color_mode == "always":
                force_terminal = True
            elif self._color_mode == "never":
                force_terminal = False
                no_color = True
            self._console = Console(
                file=file or sys.stdout,
                force_terminal=force_terminal,
                no_color=no_color,
                highlight=False,
                markup=False,
            )

    @property
    def palette(self) -> ThemePalette:
        return self._palette

    @property
    def color_mode(self) -> ColorMode:
        return self._color_mode

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

        presentation = present_scanner_info(info, connected=connected)
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


def resolve_color_mode(
    requested: str = "auto",
    *,
    environ: Mapping[str, str] | None = None,
) -> ColorMode:
    """Resolve CLI and environment color policy into one stable mode."""

    normalized = requested.strip().casefold()
    if normalized not in COLOR_MODES:
        choices = ", ".join(COLOR_MODES)
        raise ValueError(f"color mode must be one of: {choices}")
    mode = normalized
    if mode != "auto":
        return mode

    environment = os.environ if environ is None else environ
    if "NO_COLOR" in environment:
        return "never"
    if "FORCE_COLOR" in environment:
        return "never" if environment["FORCE_COLOR"].strip() == "0" else "always"
    return "auto"


def palette_for_name(name: str) -> ThemePalette:
    """Resolve a stable CLI theme name into a renderer-neutral palette."""

    normalized = name.strip().casefold()
    if normalized == "dark":
        return DEFAULT_DARK_THEME
    if normalized == "light":
        return DEFAULT_LIGHT_THEME
    choices = ", ".join(THEME_NAMES)
    raise ValueError(f"theme must be one of: {choices}")


def rich_style(style: ThemeStyle) -> Style:
    """Convert one renderer-neutral theme style into a Rich style."""

    return Style(
        color=style.foreground,
        bgcolor=style.background,
        bold=style.bold,
        dim=style.dim,
        underline=style.underline,
    )


def _number_or_dash(value: float | None) -> str:
    return f"{value:g}" if value is not None else "-"
