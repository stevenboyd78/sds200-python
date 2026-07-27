from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from threading import Event, Thread, current_thread
from time import monotonic
from typing import ClassVar, Protocol

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import VerticalScroll
from textual.widgets import Footer, Header, Static

from .models import ScannerInfo
from .presentation import ScannerPresentation, present_radio_state
from .rich_cli import rich_style
from .state import RadioStateSnapshot, snapshot_from_scanner_info
from .theme import (
    DEFAULT_DARK_THEME,
    DEFAULT_LIGHT_THEME,
    PresentationThemeRoles,
    ThemePalette,
    ThemeRole,
    theme_roles_for,
)
from .transport import TransportDiagnostic

Unsubscribe = Callable[[], None]
Clock = Callable[[], float]


class ScannerTuiRadio(Protocol):
    """Radio operations required by the live Textual adapter."""

    @property
    def connected(self) -> bool: ...

    def on_state(
        self,
        callback: Callable[[RadioStateSnapshot], None],
    ) -> Unsubscribe: ...

    def on_connection(self, callback: Callable[[bool], None]) -> Unsubscribe: ...

    def on_diagnostic(
        self,
        callback: Callable[[TransportDiagnostic], None],
    ) -> Unsubscribe: ...

    def scanner_info_push(
        self,
        interval_ms: int = 500,
        *,
        timeout: float = 3.0,
    ) -> AbstractContextManager[ScannerInfo]: ...


@dataclass(frozen=True, slots=True)
class ScannerIdentity:
    """Stable scanner identity displayed by the Textual shell."""

    endpoint: str
    model: str
    firmware: str


class ScannerTuiApp(App[None]):
    """Full-screen Textual interface for live SDS scanner state."""

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
        min-height: 6;
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
        radio: ScannerTuiRadio | None = None,
        interval_ms: int = 500,
        stale_after: float = 3.0,
        connected: bool | None = True,
        palette: ThemePalette = DEFAULT_DARK_THEME,
        clock: Clock = monotonic,
    ) -> None:
        if interval_ms <= 0:
            raise ValueError("PSI interval must be greater than zero")
        if stale_after <= 0:
            raise ValueError("Stale-state threshold must be greater than zero")

        super().__init__()
        self._identity = identity
        self._snapshot = snapshot_from_scanner_info(info)
        self._radio = radio
        self._interval_ms = interval_ms
        self._stale_after = stale_after
        self._connected = connected
        self._palette = palette
        self._clock = clock
        self._last_state_at = clock()
        self._degraded = False
        self._stale = False
        self._stream_mode = "INITIAL SNAPSHOT"
        self._status_message = "Initial scanner information loaded"
        self._unsubscribers: list[Unsubscribe] = []
        self._psi_stop = Event()
        self._psi_thread: Thread | None = None
        self.title = f"{identity.model} Scanner"
        self.sub_title = identity.endpoint

    @property
    def palette(self) -> ThemePalette:
        """Return the active renderer-neutral palette."""

        return self._palette

    @property
    def stale(self) -> bool:
        """Return whether the most recent live state exceeded the age threshold."""

        return self._stale

    @property
    def live_thread_alive(self) -> bool:
        """Return whether the PSI lifecycle thread is currently running."""

        return self._psi_thread is not None and self._psi_thread.is_alive()

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
        if self._radio is not None:
            check_interval = min(max(self._stale_after / 4, 0.1), 1.0)
            self.set_interval(check_interval, self.check_stale)
            self._start_live_updates()

    def on_unmount(self) -> None:
        self.stop_live_updates()

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
        snapshot: RadioStateSnapshot,
        *,
        connected: bool | None,
        degraded: bool = False,
    ) -> None:
        """Replace the displayed state from the Textual event-loop thread."""

        self._snapshot = snapshot
        self._connected = connected
        self._degraded = degraded
        self._stale = False
        self._last_state_at = self._clock()
        self._stream_mode = "LIVE PSI"
        self._status_message = "Live PSI update received"
        self._refresh_view()

    def check_stale(self) -> None:
        """Update freshness after comparing the last PSI update with the threshold."""

        if self._radio is None or self._connected is not True:
            return
        age = max(0.0, self._clock() - self._last_state_at)
        stale = age >= self._stale_after
        if stale == self._stale:
            return
        self._stale = stale
        if stale:
            self._status_message = f"No PSI update for {age:.1f} seconds"
        self._refresh_view()

    def stop_live_updates(self) -> None:
        """Stop PSI streaming and remove every radio callback subscription."""

        self._psi_stop.set()
        for unsubscribe in tuple(self._unsubscribers):
            unsubscribe()
        self._unsubscribers.clear()

        thread = self._psi_thread
        if thread is not None and thread is not current_thread():
            thread.join(timeout=2.0)
        if thread is not None and not thread.is_alive():
            self._psi_thread = None

    def _start_live_updates(self) -> None:
        assert self._radio is not None
        if self.live_thread_alive:
            return
        self._connected = self._radio.connected
        self._stream_mode = "STARTING PSI"
        self._status_message = "Starting live scanner-information updates"
        self._unsubscribers.extend(
            (
                self._radio.on_state(self._on_radio_state),
                self._radio.on_connection(self._on_radio_connection),
                self._radio.on_diagnostic(self._on_radio_diagnostic),
            )
        )
        self._psi_stop.clear()
        self._psi_thread = Thread(
            target=self._run_psi_stream,
            name="sds200-tui-psi",
            daemon=True,
        )
        self._psi_thread.start()
        self._refresh_view()

    def _run_psi_stream(self) -> None:
        assert self._radio is not None
        try:
            with self._radio.scanner_info_push(self._interval_ms) as first:
                self._dispatch_from_radio(self._apply_scanner_info, first)
                self._psi_stop.wait()
        except Exception as exc:
            if not self._psi_stop.is_set():
                self._dispatch_from_radio(self._apply_stream_error, str(exc))

    def _on_radio_state(self, snapshot: RadioStateSnapshot) -> None:
        self._dispatch_from_radio(self._apply_radio_state, snapshot)

    def _on_radio_connection(self, connected: bool) -> None:
        self._dispatch_from_radio(self._apply_connection, connected)

    def _on_radio_diagnostic(self, diagnostic: TransportDiagnostic) -> None:
        self._dispatch_from_radio(self._apply_diagnostic, diagnostic)

    def _dispatch_from_radio(
        self,
        callback: Callable[..., None],
        *args: object,
    ) -> None:
        try:
            self.call_from_thread(callback, *args)
        except RuntimeError:
            # The app may have completed between a radio callback and dispatch.
            return

    def _apply_scanner_info(self, info: ScannerInfo) -> None:
        self._apply_radio_state(snapshot_from_scanner_info(info))

    def _apply_radio_state(self, snapshot: RadioStateSnapshot) -> None:
        self.update_snapshot(snapshot, connected=True)

    def _apply_connection(self, connected: bool) -> None:
        self._connected = connected
        self._degraded = False
        self._stale = False
        self._last_state_at = self._clock()
        if connected:
            self._stream_mode = "WAITING FOR PSI"
            self._status_message = "Transport connected; waiting for scanner state"
        else:
            self._stream_mode = "RECONNECTING"
            self._status_message = "Transport disconnected; waiting to reconnect"
        self._refresh_view()

    def _apply_diagnostic(self, diagnostic: TransportDiagnostic) -> None:
        kind = diagnostic.kind.casefold()
        recovered = kind.endswith(("succeeded", "recovered"))
        self._degraded = False if recovered else self._connected is True
        self._stream_mode = _state_label(diagnostic.kind)
        self._status_message = diagnostic.message
        self._refresh_view()

    def _apply_stream_error(self, message: str) -> None:
        self._degraded = self._connected is True
        self._stream_mode = "PSI ERROR"
        self._status_message = message
        self._refresh_view()

    def _refresh_view(self) -> None:
        presentation = present_radio_state(
            self._snapshot,
            connected=self._connected,
            degraded=self._degraded,
            stale=self._stale,
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
                ("System", _display(self._snapshot.system), ThemeRole.TEXT_PRIMARY),
                ("Department", _display(self._snapshot.department), ThemeRole.TEXT_PRIMARY),
                ("Site", _display(self._snapshot.site), ThemeRole.TEXT_PRIMARY),
            )
        )
        self.query_one("#channel", Static).update(
            self._panel(
                ("Channel", _display(self._snapshot.channel), ThemeRole.TEXT_PRIMARY),
                ("Frequency", _display(self._snapshot.frequency), ThemeRole.TEXT_PRIMARY),
                ("Modulation", _display(self._snapshot.modulation), ThemeRole.TEXT_PRIMARY),
                ("Service", _display(self._snapshot.service_type), ThemeRole.TEXT_PRIMARY),
            )
        )
        self.query_one("#state", Static).update(
            self._state_panel(presentation, roles)
        )
        stream_mode = "STALE" if self._stale else self._stream_mode
        self.query_one("#status", Static).update(
            self._panel(
                (
                    "Availability",
                    _state_label(presentation.availability.value),
                    roles.availability,
                ),
                ("Severity", _state_label(presentation.severity.value), roles.severity),
                ("Stream", stream_mode, ThemeRole.TEXT_PRIMARY),
                ("Detail", self._status_message, ThemeRole.TEXT_PRIMARY),
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
    radio: ScannerTuiRadio,
    interval_ms: int,
    stale_after: float,
    connected: bool | None,
    palette: ThemePalette,
) -> None:
    """Launch the live Textual scanner interface and block until it exits."""

    app = ScannerTuiApp(
        ScannerIdentity(endpoint=endpoint, model=model, firmware=firmware),
        info,
        radio=radio,
        interval_ms=interval_ms,
        stale_after=stale_after,
        connected=connected,
        palette=palette,
    )
    try:
        app.run()
    finally:
        app.stop_live_updates()


def _display(value: object | None) -> str:
    return "-" if value is None or str(value).strip() == "" else str(value)


def _state_label(value: str) -> str:
    return value.replace("_", " ").upper()


def _boolean_state(value: bool | None, true_text: str, false_text: str) -> str:
    if value is None:
        return "UNKNOWN"
    return true_text if value else false_text
