from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import VerticalScroll
from textual.widgets import Footer, Header, Static

from .models import ScannerInfo
from .presentation import ScannerPresentation, present_scanner_info
from .rich_cli import rich_style
from .theme import (
    DEFAULT_DARK_THEME,
    DEFAULT_LIGHT_THEME,
    PresentationThemeRoles,
    ThemePalette,
    ThemeRole,
    theme_roles_for,
)


@dataclass(frozen=True, slots=True)
class ScannerIdentity:
    """Stable scanner identity displayed by the Textual shell."""

    endpoint: str
    model: str
    firmware: str


class ScannerTuiApp(App[None]):
    """Initial full-screen Textual shell for SDS scanner state."""

    TITLE = "SDS Scanner"
    CSS: ClassVar[str] = """
    Screen {
        background: #10151c;
        color: #f5f5f5;
    }

    Screen.light {
        background: #f3f4f6;
        color: #1f2937;
    }

    #body {
        height: 1fr;
        padding: 1;
    }

    .panel {
        height: auto;
        min-height: 3;
        margin-bottom: 1;
        padding: 0 1;
        background: #1b2430;
        border: round #5fafff;
    }

    Screen.light .panel {
        background: #ffffff;
        border: round #1d4ed8;
    }

    #status {
        min-height: 4;
    }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "quit", "Quit"),
        Binding("t", "toggle_theme", "Theme"),
    ]

    def __init__(
        self,
        identity: ScannerIdentity,
        info: ScannerInfo,
        *,
        connected: bool | None = True,
        palette: ThemePalette = DEFAULT_DARK_THEME,
    ) -> None:
        super().__init__()
        self._identity = identity
        self._info = info
        self._connected = connected
        self._palette = palette
        self.title = f"{identity.model} Scanner"
        self.sub_title = identity.endpoint

    @property
    def palette(self) -> ThemePalette:
        """Return the active renderer-neutral palette."""

        return self._palette

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with VerticalScroll(id="body"):
            yield Static(id="connection", classes="panel", markup=False)
            yield Static(id="identity", classes="panel", markup=False)
            yield Static(id="system", classes="panel", markup=False)
            yield Static(id="channel", classes="panel", markup=False)
            yield Static(id="state", classes="panel", markup=False)
            yield Static(id="status", classes="panel", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_view()

    def action_toggle_theme(self) -> None:
        """Toggle between the built-in semantic light and dark palettes."""

        self._palette = (
            DEFAULT_LIGHT_THEME
            if self._palette.name == DEFAULT_DARK_THEME.name
            else DEFAULT_DARK_THEME
        )
        self._refresh_view()

    def update_snapshot(
        self,
        info: ScannerInfo,
        *,
        connected: bool | None,
    ) -> None:
        """Replace the displayed scanner snapshot.

        Milestone 13.2 will call this from live scanner-state subscriptions.
        """

        self._info = info
        self._connected = connected
        self._refresh_view()

    def _refresh_view(self) -> None:
        presentation = present_scanner_info(
            self._info,
            connected=self._connected,
        )
        roles = theme_roles_for(presentation)
        self._apply_theme_class()

        self.query_one("#connection", Static).update(
            self._panel(
                ("Connection", _state_label(presentation.connection.value), roles.connection),
                ("Endpoint", self._identity.endpoint, ThemeRole.TEXT_PRIMARY),
            )
        )
        self.query_one("#identity", Static).update(
            self._panel(
                ("Model", self._identity.model, ThemeRole.TEXT_PRIMARY),
                ("Firmware", self._identity.firmware, ThemeRole.TEXT_PRIMARY),
            )
        )
        self.query_one("#system", Static).update(
            self._panel(
                ("System", _display(self._info.system), ThemeRole.TEXT_PRIMARY),
                ("Department", _display(self._info.department), ThemeRole.TEXT_PRIMARY),
                ("Site", _display(self._info.site), ThemeRole.TEXT_PRIMARY),
            )
        )
        self.query_one("#channel", Static).update(
            self._panel(
                ("Channel", _display(self._info.channel), ThemeRole.TEXT_PRIMARY),
                ("Frequency", _display(self._info.frequency), ThemeRole.TEXT_PRIMARY),
                ("Modulation", _display(self._info.modulation), ThemeRole.TEXT_PRIMARY),
                ("Service", _display(self._info.service_type), ThemeRole.TEXT_PRIMARY),
            )
        )
        self.query_one("#state", Static).update(
            self._state_panel(presentation, roles)
        )
        self.query_one("#status", Static).update(
            self._panel(
                (
                    "Availability",
                    _state_label(presentation.availability.value),
                    roles.availability,
                ),
                ("Severity", _state_label(presentation.severity.value), roles.severity),
                ("Mode", "INITIAL SNAPSHOT", ThemeRole.TEXT_PRIMARY),
            )
        )

    def _state_panel(
        self,
        presentation: ScannerPresentation,
        roles: PresentationThemeRoles,
    ) -> Text:
        muted_role = roles.muted or ThemeRole.TEXT_PRIMARY
        recording_role = roles.recording or ThemeRole.TEXT_PRIMARY
        signal = _state_label(presentation.signal.value)
        if presentation.raw_signal is not None:
            signal = f"{signal} ({presentation.raw_signal})"
        return self._panel(
            ("Activity", _state_label(presentation.activity.value), roles.activity),
            ("Signal", signal, roles.signal),
            ("Hold", _state_label(presentation.hold.value), roles.hold),
            ("Mute", _boolean_state(presentation.muted, "MUTED", "UNMUTED"), muted_role),
            (
                "Recording",
                _boolean_state(presentation.recording, "RECORDING", "OFF"),
                recording_role,
            ),
        )

    def _panel(self, *rows: tuple[str, str, ThemeRole]) -> Text:
        output = Text()
        label_style = rich_style(self._palette.resolve(ThemeRole.TEXT_MUTED))
        for index, (label, value, role) in enumerate(rows):
            if index:
                output.append("\n")
            output.append(f"{label}: ", style=label_style)
            output.append(value, style=rich_style(self._palette.resolve(role)))
        return output

    def _apply_theme_class(self) -> None:
        self.screen.remove_class("light")
        if self._palette.name == DEFAULT_LIGHT_THEME.name:
            self.screen.add_class("light")


def run_tui(
    *,
    endpoint: str,
    model: str,
    firmware: str,
    info: ScannerInfo,
    connected: bool | None,
    palette: ThemePalette,
) -> None:
    """Launch the Textual scanner shell and block until it exits."""

    ScannerTuiApp(
        ScannerIdentity(endpoint=endpoint, model=model, firmware=firmware),
        info,
        connected=connected,
        palette=palette,
    ).run()


def _display(value: object | None) -> str:
    return "-" if value is None or str(value).strip() == "" else str(value)


def _state_label(value: str) -> str:
    return value.replace("_", " ").upper()


def _boolean_state(value: bool | None, true_text: str, false_text: str) -> str:
    if value is None:
        return "UNKNOWN"
    return true_text if value else false_text
