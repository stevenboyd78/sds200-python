from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager, suppress
from dataclasses import dataclass, replace
from datetime import datetime
from threading import Event, Thread, current_thread
from time import monotonic
from typing import ClassVar, Protocol

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import VerticalScroll
from textual.widgets import Footer, Header, Static

from .audio_session import (
    AudioRecordingSession,
    AudioSessionSnapshot,
    AudioSessionStatus,
)
from .commands import NavigationTarget
from .models import ScannerInfo
from .presentation import ScannerPresentation, present_radio_state
from .rich_cli import rich_style
from .scanner import capabilities_for_model
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
from .tui_controls import (
    ControlRequest,
    ControlWorker,
    HoldScope,
    channel_navigation,
    hold_selection,
)

Unsubscribe = Callable[[], None]
Clock = Callable[[], float]
WallClock = Callable[[], datetime]


def _local_now() -> datetime:
    return datetime.now().astimezone()


KEY_HELP_TEXT = """Keyboard controls
Q        Quit
T        Toggle dark/light theme
C        Reconnect scanner
R        Start / stop audio recording
H        Hold current channel
S / D    Hold current system / department
I        Hold current site
N / P    Next / previous channel
+ / -    Raise / lower volume
] / [    Raise / lower squelch
^p       Command Palette
?        Show or hide this help
"""


class ScannerTuiRadio(Protocol):
    """Radio operations required by the live Textual adapter."""

    @property
    def connected(self) -> bool: ...

    def reconnect(self) -> None: ...

    def hold(
        self,
        target: NavigationTarget | str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        timeout: float = 2.0,
    ) -> None: ...

    def next(
        self,
        target: NavigationTarget | str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        count: int = 1,
        timeout: float = 2.0,
    ) -> None: ...

    def previous(
        self,
        target: NavigationTarget | str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        count: int = 1,
        timeout: float = 2.0,
    ) -> None: ...

    def set_volume(self, level: int, *, timeout: float = 2.0) -> None: ...

    def set_squelch(self, level: int, *, timeout: float = 2.0) -> None: ...

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
        min-height: 9;
    }

    #audio {
        min-height: 10;
    }

    #keys {
        display: none;
        min-height: 9;
    }

    Screen.show-keys #keys {
        display: block;
    }

    Screen.-compact #body {
        padding: 0;
    }

    Screen.-compact .panel {
        min-height: 1;
        margin-bottom: 0;
        padding: 0 1;
        border: none;
    }

    Screen.-compact #status {
        min-height: 7;
    }

    Screen.-compact #audio {
        min-height: 1;
    }

    Screen.-short #identity {
        display: none;
    }

    Screen.-wide #body {
        layout: grid;
        grid-size: 2;
        grid-columns: 1fr 1fr;
        grid-gutter: 0 1;
    }

    Screen.-wide #keys {
        column-span: 2;
    }
    """
    HORIZONTAL_BREAKPOINTS: list[tuple[int, str]] | None = [
        (0, "-compact"),
        (80, "-standard"),
        (120, "-wide"),
    ]
    VERTICAL_BREAKPOINTS: list[tuple[int, str]] | None = [
        (0, "-short"),
        (32, "-tall"),
    ]
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "quit", "Quit"),
        Binding("t", "toggle_theme", "Theme"),
        Binding(
            "ctrl+p",
            "command_palette",
            "Command Palette",
            key_display="^p",
            priority=True,
        ),
        Binding("c", "reconnect", "Reconnect"),
        Binding("r", "toggle_audio_recording", "Record"),
        Binding(
            "question_mark",
            "toggle_key_help",
            "Keys",
            key_display="?",
        ),
        Binding("h", "hold_channel", "Hold channel", show=False),
        Binding("s", "hold_system", "Hold system", show=False),
        Binding("d", "hold_department", "Hold department", show=False),
        Binding("i", "hold_site", "Hold site", show=False),
        Binding("n", "next_channel", "Next", show=False),
        Binding("p", "previous_channel", "Previous", show=False),
        Binding("plus", "volume_up", "Vol +", key_display="+", show=False),
        Binding("minus", "volume_down", "Vol -", key_display="-", show=False),
        Binding(
            "right_square_bracket",
            "squelch_up",
            "SQL +",
            key_display="]",
            show=False,
        ),
        Binding(
            "left_square_bracket",
            "squelch_down",
            "SQL -",
            key_display="[",
            show=False,
        ),
    ]

    def __init__(
        self,
        identity: ScannerIdentity,
        info: ScannerInfo,
        *,
        radio: ScannerTuiRadio | None = None,
        audio_session: AudioRecordingSession | None = None,
        interval_ms: int = 500,
        stale_after: float = 3.0,
        connected: bool | None = True,
        palette: ThemePalette = DEFAULT_DARK_THEME,
        clock: Clock = monotonic,
        now: WallClock = _local_now,
    ) -> None:
        if interval_ms <= 0:
            raise ValueError("PSI interval must be greater than zero")
        if stale_after <= 0:
            raise ValueError("Stale-state threshold must be greater than zero")

        super().__init__()
        self._identity = identity
        self._snapshot = snapshot_from_scanner_info(info)
        self._capabilities = capabilities_for_model(identity.model)
        self._radio = radio
        self._audio_session = audio_session
        self._audio_snapshot = (
            audio_session.snapshot() if audio_session is not None else None
        )
        self._audio_message = (
            "Ready to record" if audio_session is not None else "Audio unavailable"
        )
        self._audio_pending = False
        self._audio_unsubscribe: Unsubscribe | None = None
        self._interval_ms = interval_ms
        self._stale_after = stale_after
        self._connected = connected
        self._palette = palette
        self._clock = clock
        self._now = now
        self._transition_values: dict[str, str] = {}
        self._transition_since: dict[str, datetime] = {}
        self._last_state_at = clock()
        self._degraded = False
        self._stale = False
        self._stream_mode = "INITIAL SNAPSHOT"
        self._status_message = "Initial scanner information loaded"
        self._control_message = "Ready"
        self._key_help_visible = False
        self._unsubscribers: list[Unsubscribe] = []
        self._psi_stop = Event()
        self._shutdown_started = Event()
        self._psi_thread: Thread | None = None
        self._control_worker = ControlWorker(self._on_control_completed)
        self._audio_worker = ControlWorker(
            self._on_audio_completed,
            thread_name="sds200-tui-audio",
        )
        self.title = f"{identity.model} Scanner"
        self.sub_title = f"{identity.endpoint} | {identity.firmware}"

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

    @property
    def control_thread_alive(self) -> bool:
        """Return whether the serialized scanner-control worker is running."""

        return self._control_worker.alive

    @property
    def audio_thread_alive(self) -> bool:
        """Return whether the dedicated audio lifecycle worker is running."""

        return self._audio_worker.alive

    @property
    def key_help_visible(self) -> bool:
        """Return whether the in-app keyboard reference is visible."""

        return self._key_help_visible

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with VerticalScroll(id="body"):
            yield Static(KEY_HELP_TEXT, id="keys", classes="panel", markup=False)
            yield Static(id="connection", classes="panel", markup=False)
            yield Static(id="identity", classes="panel", markup=False)
            yield Static(id="system", classes="panel", markup=False)
            yield Static(id="channel", classes="panel", markup=False)
            yield Static(id="state", classes="panel", markup=False)
            if self._audio_session is not None:
                yield Static(id="audio", classes="panel", markup=False)
            yield Static(id="status", classes="panel", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self._shutdown_started.clear()
        self._refresh_view()
        if self._radio is not None:
            self._control_worker.start()
            check_interval = min(max(self._stale_after / 4, 0.1), 1.0)
            self.set_interval(check_interval, self.check_stale)
            self._start_live_updates()
        if self._audio_session is not None:
            self._audio_worker.start()
            self._audio_unsubscribe = self._audio_session.on_state(
                self._on_audio_state
            )
            self.set_interval(0.25, self._poll_audio_state)

    def on_unmount(self) -> None:
        self._shutdown_started.set()
        self.stop_audio()
        self.stop_live_updates()
        self.stop_controls()

    def action_toggle_theme(self) -> None:
        """Toggle between the built-in semantic light and dark palettes."""

        self._palette = (
            DEFAULT_LIGHT_THEME
            if self._palette.name == DEFAULT_DARK_THEME.name
            else DEFAULT_DARK_THEME
        )
        self._refresh_view()

    def action_toggle_key_help(self) -> None:
        """Show or hide the full keyboard reference."""

        self._key_help_visible = not self._key_help_visible
        if self._key_help_visible:
            self.screen.add_class("show-keys")
            self._control_message = "Keyboard help shown"
        else:
            self.screen.remove_class("show-keys")
            self._control_message = "Keyboard help hidden"
        self._refresh_view()

    def action_reconnect(self) -> None:
        """Restart the scanner transport and resume the active PSI stream."""
        radio = self._radio
        if radio is None:
            self._control_unavailable("No live scanner connection")
            return
        if self._identity.endpoint.startswith("replay://"):
            self._control_unavailable("Replay sessions cannot reconnect")
            return
        self._submit_control(
            ControlRequest("Reconnect scanner", radio.reconnect),
            requires_connection=False,
        )

    def action_toggle_audio_recording(self) -> None:
        """Start or stop the configured one-shot network-audio recording."""
        session = self._audio_session
        if session is None:
            self._control_unavailable(
                "Start the TUI with --audio-output to enable recording"
            )
            return
        if self._audio_pending:
            self._audio_message = "Audio operation already queued"
            self._refresh_view()
            return

        snapshot = session.snapshot()
        if snapshot.status is AudioSessionStatus.IDLE:
            self._submit_audio(ControlRequest("Start recording", session.start))
            return
        if snapshot.active:
            self._submit_audio(ControlRequest("Stop recording", session.stop))
            return

        self._audio_message = (
            "Recording session completed; restart the TUI to record another file"
        )
        self._refresh_view()

    def action_hold_channel(self) -> None:
        """Hold the indexed channel reported by the current PSI snapshot."""

        self._queue_hold("channel")

    def action_hold_system(self) -> None:
        """Hold the indexed system reported by the current PSI snapshot."""

        self._queue_hold("system")

    def action_hold_department(self) -> None:
        """Hold the indexed department reported by the current PSI snapshot."""

        self._queue_hold("department")

    def action_hold_site(self) -> None:
        """Hold the indexed site reported by the current PSI snapshot."""

        self._queue_hold("site")

    def action_next_channel(self) -> None:
        """Move to the next indexed channel without blocking the UI thread."""

        radio = self._radio
        if radio is None:
            self._control_unavailable("No live scanner connection")
            return
        self._queue_navigation(
            "Next channel",
            lambda target, index: radio.next(target, index),
        )

    def action_previous_channel(self) -> None:
        """Move to the previous indexed channel without blocking the UI thread."""

        radio = self._radio
        if radio is None:
            self._control_unavailable("No live scanner connection")
            return
        self._queue_navigation(
            "Previous channel",
            lambda target, index: radio.previous(target, index),
        )

    def action_volume_up(self) -> None:
        self._queue_volume(1)

    def action_volume_down(self) -> None:
        self._queue_volume(-1)

    def action_squelch_up(self) -> None:
        self._queue_squelch(1)

    def action_squelch_down(self) -> None:
        self._queue_squelch(-1)

    def _queue_hold(self, scope: HoldScope) -> None:
        radio = self._radio
        if radio is None:
            self._control_unavailable("No live scanner connection")
            return
        if not self._capabilities.navigation_control:
            self._control_unavailable("Navigation is not supported by this scanner")
            return
        selection = hold_selection(self._snapshot, scope)
        if selection is None:
            self._control_unavailable(
                f"Current PSI state does not provide a {scope} hold index"
            )
            return
        label = f"Hold {scope}"
        self._submit_control(
            ControlRequest(
                label,
                lambda: radio.hold(
                    selection.target,
                    selection.first,
                    selection.second,
                ),
                lambda: self._set_hold_scope(scope),
            )
        )

    def _queue_navigation(
        self,
        label: str,
        operation: Callable[[NavigationTarget, int], None],
    ) -> None:
        if not self._capabilities.navigation_control:
            self._control_unavailable("Navigation is not supported by this scanner")
            return
        selection = channel_navigation(self._snapshot)
        if selection is None:
            self._control_unavailable(
                "Current PSI state does not provide a controllable channel index"
            )
            return
        target, index = selection
        self._submit_control(ControlRequest(label, lambda: operation(target, index)))

    def _queue_volume(self, adjustment: int) -> None:
        radio = self._radio
        current = self._snapshot.volume
        if radio is None or current is None:
            self._control_unavailable("Current volume is unavailable")
            return
        target = min(
            self._capabilities.maximum_volume,
            max(0, current + adjustment),
        )
        if target == current:
            self._control_unavailable(f"Volume is already {current}")
            return
        self._submit_control(
            ControlRequest(
                f"Volume {target}",
                lambda: radio.set_volume(target),
                lambda: self._set_snapshot_volume(target),
            )
        )

    def _queue_squelch(self, adjustment: int) -> None:
        radio = self._radio
        current = self._snapshot.squelch
        if radio is None or current is None:
            self._control_unavailable("Current squelch is unavailable")
            return
        target = min(
            self._capabilities.maximum_squelch,
            max(0, current + adjustment),
        )
        if target == current:
            self._control_unavailable(f"Squelch is already {current}")
            return
        self._submit_control(
            ControlRequest(
                f"Squelch {target}",
                lambda: radio.set_squelch(target),
                lambda: self._set_snapshot_squelch(target),
            )
        )

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

        if (
            self._radio is None
            or self._connected is not True
            or self._identity.endpoint.startswith("replay://")
        ):
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

    def stop_controls(self) -> None:
        """Stop the serialized command worker after its active request returns."""

        self._control_worker.stop()

    def stop_audio(self) -> None:
        """Stop audio recording, remove callbacks, and stop its worker."""
        session = self._audio_session
        unsubscribe, self._audio_unsubscribe = self._audio_unsubscribe, None
        if unsubscribe is not None:
            unsubscribe()
        self._audio_worker.stop()
        if session is None:
            return
        with suppress(Exception):
            session.stop()
        self._audio_snapshot = session.snapshot()

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

    def _on_audio_state(self, snapshot: AudioSessionSnapshot) -> None:
        self._dispatch_from_radio(self._apply_audio_state, snapshot)

    def _poll_audio_state(self) -> None:
        session = self._audio_session
        if session is None:
            return
        snapshot = session.snapshot()
        if snapshot == self._audio_snapshot:
            return
        self._audio_snapshot = snapshot
        self._refresh_view()

    def _dispatch_from_radio(
        self,
        callback: Callable[..., None],
        *args: object,
    ) -> None:
        if self._shutdown_started.is_set():
            return
        try:
            self.call_from_thread(callback, *args)
        except RuntimeError:
            # The app may have completed between a radio callback and dispatch.
            return

    def _submit_audio(self, request: ControlRequest) -> None:
        try:
            self._audio_worker.submit(request)
        except RuntimeError as exc:
            self._audio_message = f"Unavailable: {exc}"
            self._refresh_view()
            return
        self._audio_pending = True
        self._audio_message = f"Queued: {request.label}"
        self._refresh_view()

    def _submit_control(
        self,
        request: ControlRequest,
        *,
        requires_connection: bool = True,
    ) -> None:
        if requires_connection and self._connected is not True:
            self._control_unavailable("Controls are unavailable while disconnected")
            return
        try:
            self._control_worker.submit(request)
        except RuntimeError as exc:
            self._control_unavailable(str(exc))
            return
        self._control_message = f"Queued: {request.label}"
        self._refresh_view()

    def _control_unavailable(self, message: str) -> None:
        self._control_message = f"Unavailable: {message}"
        self._refresh_view()

    def _on_control_completed(
        self,
        request: ControlRequest,
        error: Exception | None,
    ) -> None:
        self._dispatch_from_radio(self._apply_control_result, request, error)

    def _apply_control_result(
        self,
        request: ControlRequest,
        error: Exception | None,
    ) -> None:
        if error is not None:
            self._control_message = f"Failed: {request.label}: {error}"
        else:
            if request.on_success is not None:
                request.on_success()
            self._control_message = f"Completed: {request.label}"
        self._refresh_view()

    def _on_audio_completed(
        self,
        request: ControlRequest,
        error: Exception | None,
    ) -> None:
        self._dispatch_from_radio(self._apply_audio_result, request, error)

    def _apply_audio_result(
        self,
        request: ControlRequest,
        error: Exception | None,
    ) -> None:
        self._audio_pending = False
        session = self._audio_session
        if session is not None:
            self._audio_snapshot = session.snapshot()
        if error is not None:
            self._audio_message = f"Failed: {request.label}: {error}"
        elif (
            self._audio_snapshot is not None
            and self._audio_snapshot.status is AudioSessionStatus.RECORDING
        ):
            self._audio_message = "Recording in progress"
        elif (
            self._audio_snapshot is not None
            and self._audio_snapshot.status is AudioSessionStatus.STOPPED
        ):
            self._audio_message = "Recording completed"
        else:
            self._audio_message = f"Completed: {request.label}"
        self._refresh_view()

    def _apply_audio_state(self, snapshot: AudioSessionSnapshot) -> None:
        self._audio_snapshot = snapshot
        if snapshot.error is not None:
            self._audio_message = f"Failed: {snapshot.error}"
        elif snapshot.status is AudioSessionStatus.RECORDING:
            self._audio_message = "Recording in progress"
        elif snapshot.status is AudioSessionStatus.STOPPING:
            self._audio_message = "Finalizing WAV recording"
        elif snapshot.status is AudioSessionStatus.STOPPED:
            self._audio_message = "Recording completed"
        self._refresh_view()

    def _set_snapshot_volume(self, value: int) -> None:
        self._snapshot = replace(self._snapshot, volume=value)

    def _set_snapshot_squelch(self, value: int) -> None:
        self._snapshot = replace(self._snapshot, squelch=value)

    def _set_hold_scope(self, scope: HoldScope) -> None:
        mode = self._snapshot.mode
        if mode is not None and "hold" not in mode.casefold():
            mode = f"{mode} Hold"
        if scope == "system":
            self._snapshot = replace(
                self._snapshot, mode=mode, system_hold="On"
            )
        elif scope == "department":
            self._snapshot = replace(
                self._snapshot, mode=mode, department_hold="On"
            )
        elif scope == "site":
            self._snapshot = replace(self._snapshot, mode=mode, site_hold="On")
        else:
            self._snapshot = replace(
                self._snapshot, mode=mode, channel_hold="On"
            )

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
            self._control_message = "Ready"
        else:
            self._stream_mode = "RECONNECTING"
            self._status_message = "Transport disconnected; waiting to reconnect"
            self._control_message = "Unavailable: scanner disconnected"
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
                (
                    "Connection",
                    self._transition_display(
                        "connection",
                        _state_label(presentation.connection.value),
                    ),
                    roles.connection,
                ),
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
                    self._transition_display(
                        "availability",
                        _state_label(presentation.availability.value),
                    ),
                    roles.availability,
                ),
                (
                    "Severity",
                    self._transition_display(
                        "severity",
                        _state_label(presentation.severity.value),
                    ),
                    roles.severity,
                ),
                ("Stream", stream_mode, ThemeRole.TEXT_PRIMARY),
                (
                    "Volume",
                    _level_display(
                        self._snapshot.volume,
                        self._capabilities.maximum_volume,
                    ),
                    ThemeRole.TEXT_PRIMARY,
                ),
                (
                    "Squelch",
                    _level_display(
                        self._snapshot.squelch,
                        self._capabilities.maximum_squelch,
                    ),
                    ThemeRole.TEXT_PRIMARY,
                ),
                ("Control", self._control_message, ThemeRole.TEXT_PRIMARY),
                ("Detail", self._status_message, ThemeRole.TEXT_PRIMARY),
            )
        )

        if self._audio_session is not None:
            self.query_one("#audio", Static).update(self._audio_panel())

    def _audio_panel(self) -> Text:
        snapshot = self._audio_snapshot
        assert snapshot is not None
        reliability = snapshot.reliability
        if snapshot.status is AudioSessionStatus.FAILED:
            status_role = ThemeRole.SEVERITY_ERROR
        elif snapshot.active:
            status_role = ThemeRole.STATE_RECORDING
        else:
            status_role = ThemeRole.TEXT_PRIMARY
        detail = snapshot.error or self._audio_message
        return self._panel(
            ("Audio", _state_label(snapshot.status.value), status_role),
            (
                "Elapsed",
                f"{snapshot.elapsed_seconds:.1f} seconds",
                ThemeRole.TEXT_PRIMARY,
            ),
            ("Output", str(snapshot.output_path), ThemeRole.TEXT_PRIMARY),
            (
                "Packets / samples",
                f"{snapshot.packets} / {snapshot.samples}",
                ThemeRole.TEXT_PRIMARY,
            ),
            (
                "Audio duration",
                f"{snapshot.audio_duration_seconds:.1f} seconds",
                ThemeRole.TEXT_PRIMARY,
            ),
            (
                "RTP loss / duplicate",
                f"{reliability.packets_lost} / {reliability.duplicate_packets}",
                ThemeRole.TEXT_PRIMARY,
            ),
            (
                "RTP late / malformed",
                f"{reliability.late_packets} / {reliability.malformed_packets}",
                ThemeRole.TEXT_PRIMARY,
            ),
            (
                "Source / SSRC",
                (
                    f"{reliability.unexpected_source_packets} / "
                    f"{reliability.ssrc_mismatch_packets}"
                ),
                ThemeRole.TEXT_PRIMARY,
            ),
            (
                "Receive / callback",
                f"{reliability.receive_errors} / {reliability.callback_errors}",
                ThemeRole.TEXT_PRIMARY,
            ),
            (
                "Timestamp gaps",
                str(reliability.timestamp_discontinuities),
                ThemeRole.TEXT_PRIMARY,
            ),
            ("Audio control", detail, ThemeRole.TEXT_PRIMARY),
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
            ("Hold", _hold_display(self._snapshot, presentation), roles.hold),
            ("Mute", _boolean_state(presentation.muted, "MUTED", "UNMUTED"), muted_role),
            (
                "Recording",
                _boolean_state(presentation.recording, "RECORDING", "OFF"),
                recording_role,
            ),
        )

    def _transition_display(self, key: str, value: str) -> str:
        if self._transition_values.get(key) != value:
            self._transition_values[key] = value
            self._transition_since[key] = self._now()
        since = self._transition_since[key]
        return f"{value} since {since:%H:%M:%S}"

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
    audio_session: AudioRecordingSession | None = None,
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
        audio_session=audio_session,
        interval_ms=interval_ms,
        stale_after=stale_after,
        connected=connected,
        palette=palette,
    )
    try:
        app.run()
    finally:
        app._shutdown_started.set()
        app.stop_audio()
        app.stop_live_updates()
        app.stop_controls()


def _display(value: object | None) -> str:
    return "-" if value is None or str(value).strip() == "" else str(value)


def _level_display(value: int | None, maximum: int) -> str:
    if value is None:
        return "-"
    return f"{value}/{maximum}"


def _state_label(value: str) -> str:
    return value.replace("_", " ").upper()


def _hold_display(
    snapshot: RadioStateSnapshot,
    presentation: ScannerPresentation,
) -> str:
    scopes = tuple(
        label
        for label, value in (
            ("SYSTEM", snapshot.system_hold),
            ("DEPARTMENT", snapshot.department_hold),
            ("SITE", snapshot.site_hold),
            ("CHANNEL", snapshot.channel_hold),
        )
        if value is not None and value.strip().casefold() == "on"
    )
    if scopes:
        return " + ".join(scopes)
    return _state_label(presentation.hold.value)


def _boolean_state(value: bool | None, true_text: str, false_text: str) -> str:
    if value is None:
        return "UNKNOWN"
    return true_text if value else false_text
