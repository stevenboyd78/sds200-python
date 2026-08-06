from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, fields
from math import isfinite
from threading import Event, RLock, Thread, current_thread
from typing import Any, Protocol, Self

from .commands import NavigationTarget
from .daemon_events import DaemonEvent, DaemonEventKind
from .events import EventBus
from .exceptions import DaemonProtocolError, UnsupportedScannerFeatureError
from .state import RadioStateSnapshot, ScannerScreenKind
from .transport import TransportDiagnostic

Unsubscribe = Callable[[], None]


class _DaemonApiClient(Protocol):
    def close(self) -> None: ...

    def hold(
        self,
        target: str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        timeout: float = 2.0,
    ) -> Mapping[str, object]: ...

    def next(
        self,
        target: str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        count: int = 1,
        timeout: float = 2.0,
    ) -> Mapping[str, object]: ...

    def previous(
        self,
        target: str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        count: int = 1,
        timeout: float = 2.0,
    ) -> Mapping[str, object]: ...

    def reconnect(
        self,
        *,
        timeout: float = 2.0,
    ) -> Mapping[str, object]: ...


class _DaemonEventClient(Protocol):
    def close(self) -> None: ...

    def receive(self) -> DaemonEvent: ...


@dataclass(frozen=True, slots=True)
class DaemonTuiBootstrap:
    """Validated identity and initial state for one daemon-backed TUI."""

    endpoint: str
    model: str
    firmware: str
    connected: bool
    snapshot: RadioStateSnapshot


class DaemonTuiRadio:
    """Adapt daemon-owned scanner state and controls to the TUI radio contract."""

    def __init__(
        self,
        api_client: _DaemonApiClient,
        event_client: _DaemonEventClient,
        *,
        event_thread_join_timeout: float = 2.0,
    ) -> None:
        if isinstance(event_thread_join_timeout, bool) or not isinstance(
            event_thread_join_timeout,
            (int, float),
        ):
            raise TypeError(
                "Daemon TUI event-thread join timeout must be a number."
            )
        normalized_timeout = float(event_thread_join_timeout)
        if not isfinite(normalized_timeout) or normalized_timeout <= 0:
            raise ValueError(
                "Daemon TUI event-thread join timeout must be finite and "
                "greater than zero."
            )

        self.api_client = api_client
        self.event_client = event_client
        self.event_thread_join_timeout = normalized_timeout
        self._events = EventBus()
        self._lock = RLock()
        self._connected = False
        self._closed = False
        self._stream_active = False
        self._stream_stop = Event()
        self._event_thread: Thread | None = None

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connected

    @property
    def event_thread_alive(self) -> bool:
        with self._lock:
            thread = self._event_thread
        return thread is not None and thread.is_alive()

    def initialize(
        self,
        snapshot: Mapping[str, object],
    ) -> DaemonTuiBootstrap:
        """Apply one authoritative API snapshot before launching the TUI."""

        initial = daemon_tui_bootstrap(snapshot)
        self._set_connected(initial.connected)
        return initial

    def hold(
        self,
        target: NavigationTarget | str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        timeout: float = 2.0,
    ) -> None:
        result = self.api_client.hold(
            str(target),
            first,
            second,
            timeout=timeout,
        )
        self._apply_control_result(result)

    def next(
        self,
        target: NavigationTarget | str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        count: int = 1,
        timeout: float = 2.0,
    ) -> None:
        result = self.api_client.next(
            str(target),
            first,
            second,
            count=count,
            timeout=timeout,
        )
        self._apply_control_result(result)

    def previous(
        self,
        target: NavigationTarget | str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        count: int = 1,
        timeout: float = 2.0,
    ) -> None:
        result = self.api_client.previous(
            str(target),
            first,
            second,
            count=count,
            timeout=timeout,
        )
        self._apply_control_result(result)

    def reconnect(self, *, timeout: float = 2.0) -> None:
        result = self.api_client.reconnect(timeout=timeout)
        self._apply_control_result(result)

    def set_volume(self, level: int, *, timeout: float = 2.0) -> None:
        del level, timeout
        raise UnsupportedScannerFeatureError(
            "Volume control is unavailable in daemon-backed TUI mode."
        )

    def set_squelch(self, level: int, *, timeout: float = 2.0) -> None:
        del level, timeout
        raise UnsupportedScannerFeatureError(
            "Squelch control is unavailable in daemon-backed TUI mode."
        )

    def on_state(
        self,
        callback: Callable[[RadioStateSnapshot], None],
    ) -> Unsubscribe:
        return self._events.subscribe("state", callback)

    def on_connection(
        self,
        callback: Callable[[bool], None],
    ) -> Unsubscribe:
        return self._events.subscribe("connection", callback)

    def on_diagnostic(
        self,
        callback: Callable[[TransportDiagnostic], None],
    ) -> Unsubscribe:
        return self._events.subscribe("diagnostic", callback)

    @contextmanager
    def radio_state_push(
        self,
        interval_ms: int = 500,
        *,
        timeout: float = 3.0,
    ) -> Iterator[RadioStateSnapshot]:
        if type(interval_ms) is not int or interval_ms <= 0:
            raise ValueError(
                "Daemon TUI state interval must be a positive integer."
            )
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise TypeError("Daemon TUI state timeout must be a number.")
        normalized_timeout = float(timeout)
        if not isfinite(normalized_timeout) or normalized_timeout <= 0:
            raise ValueError(
                "Daemon TUI state timeout must be finite and greater than zero."
            )
        del normalized_timeout

        with self._lock:
            if self._closed:
                raise RuntimeError("Daemon TUI radio is closed.")
            if self._stream_active:
                raise RuntimeError(
                    "Daemon TUI state stream is already active."
                )
            self._stream_active = True

        self._stream_stop.clear()
        try:
            first_event = self.event_client.receive()
            first = self._initial_snapshot(first_event)
            thread = Thread(
                target=self._run_event_stream,
                name="sds200-tui-daemon-events",
                daemon=True,
            )
            with self._lock:
                self._event_thread = thread
            thread.start()
            yield first
        finally:
            self._stream_stop.set()
            self.event_client.close()
            self._join_event_thread()
            with self._lock:
                self._stream_active = False

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True

        self._stream_stop.set()
        self.event_client.close()
        self._join_event_thread()
        self.api_client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception_type, exception, traceback
        self.close()

    def _initial_snapshot(
        self,
        event: DaemonEvent,
    ) -> RadioStateSnapshot:
        if event.kind != DaemonEventKind.SNAPSHOT:
            raise DaemonProtocolError(
                "Daemon TUI event stream did not begin with an authoritative "
                "snapshot."
            )
        return self._apply_runtime_snapshot(event.payload)

    def _run_event_stream(self) -> None:
        try:
            while not self._stream_stop.is_set():
                event = self.event_client.receive()
                self._apply_event(event)
        except Exception as error:
            if self._stream_stop.is_set():
                return
            self._set_connected(False)
            self._events.emit(
                "diagnostic",
                TransportDiagnostic(
                    kind="daemon_event_disconnected",
                    message=str(error),
                ),
            )
        finally:
            self.event_client.close()

    def _apply_event(self, event: DaemonEvent) -> None:
        if event.kind == DaemonEventKind.SCANNER_CONNECTION:
            connected = event.payload.get("connected")
            if type(connected) is not bool:
                raise DaemonProtocolError(
                    "Daemon scanner.connection event omitted a boolean "
                    "connected field."
                )
            self._set_connected(connected)
            return

        if event.kind == DaemonEventKind.PSI_STATE:
            state = event.payload.get("state")
            if not isinstance(state, Mapping):
                raise DaemonProtocolError(
                    "Daemon scanner.psi event omitted a radio-state object."
                )
            self._events.emit("state", _radio_state_snapshot(state))

    def _apply_control_result(
        self,
        result: Mapping[str, object],
    ) -> None:
        snapshot = result.get("snapshot")
        if not isinstance(snapshot, Mapping):
            raise DaemonProtocolError(
                "Daemon TUI control result omitted an authoritative snapshot."
            )
        state = self._apply_runtime_snapshot(snapshot)
        self._events.emit("state", state)

    def _apply_runtime_snapshot(
        self,
        snapshot: Mapping[str, object],
    ) -> RadioStateSnapshot:
        return self.initialize(snapshot).snapshot

    def _set_connected(self, connected: bool) -> None:
        with self._lock:
            changed = connected != self._connected
            self._connected = connected
        if changed:
            self._events.emit("connection", connected)

    def _join_event_thread(self) -> None:
        with self._lock:
            thread = self._event_thread
        if thread is not None and thread is not current_thread():
            thread.join(timeout=self.event_thread_join_timeout)
        if thread is not None and not thread.is_alive():
            with self._lock:
                if self._event_thread is thread:
                    self._event_thread = None


_STRING_FIELDS = frozenset(
    {
        "mode",
        "screen",
        "system",
        "department",
        "site",
        "system_hold",
        "department_hold",
        "site_hold",
        "channel",
        "channel_kind",
        "channel_hold",
        "frequency",
        "modulation",
        "sub_audio_detected",
        "tone_out_tone_a",
        "tone_out_tone_b",
        "weather_mode",
        "weather_same",
        "service_type",
        "talkgroup_id",
        "unit_id",
        "p25_status",
        "mute",
        "recording",
    }
)
_INTEGER_FIELDS = frozenset(
    {
        "system_index",
        "department_index",
        "site_index",
        "channel_index",
        "channel_number",
        "volume",
        "squelch",
        "signal",
    }
)
_RADIO_STATE_FIELDS = frozenset(
    field.name for field in fields(RadioStateSnapshot)
)


def _radio_state_snapshot(
    payload: Mapping[str, object],
) -> RadioStateSnapshot:
    values: dict[str, Any] = {}

    for name in _RADIO_STATE_FIELDS:
        value = payload.get(name)
        if value is None:
            continue

        if name in _STRING_FIELDS:
            if not isinstance(value, str):
                raise DaemonProtocolError(
                    f"Daemon radio-state field {name!r} must be a string or null."
                )
            values[name] = value
            continue

        if name in _INTEGER_FIELDS:
            if type(value) is not int:
                raise DaemonProtocolError(
                    f"Daemon radio-state field {name!r} must be an integer or null."
                )
            values[name] = value
            continue

        if name == "rssi":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise DaemonProtocolError(
                    "Daemon radio-state field 'rssi' must be a number or null."
                )
            normalized = float(value)
            if not isfinite(normalized):
                raise DaemonProtocolError(
                    "Daemon radio-state field 'rssi' must be finite."
                )
            values[name] = normalized
            continue

        if name == "screen_kind":
            if not isinstance(value, str):
                raise DaemonProtocolError(
                    "Daemon radio-state field 'screen_kind' must be a "
                    "string or null."
                )
            try:
                values[name] = ScannerScreenKind(value)
            except ValueError as error:
                raise DaemonProtocolError(
                    "Daemon radio-state field 'screen_kind' is unsupported."
                ) from error

    return RadioStateSnapshot(**values)


def _daemon_identity_value(
    value: object,
    *,
    name: str,
    fallback: str,
) -> str:
    if value is None:
        return fallback
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
    ):
        raise DaemonProtocolError(
            f"Daemon runtime snapshot field {name!r} must be a non-empty "
            "string or null."
        )
    return value


def daemon_tui_bootstrap(
    payload: Mapping[str, object],
) -> DaemonTuiBootstrap:
    """Decode one authoritative daemon snapshot for TUI construction."""

    endpoint = payload.get("scanner_endpoint")
    if (
        not isinstance(endpoint, str)
        or not endpoint
        or endpoint.strip() != endpoint
    ):
        raise DaemonProtocolError(
            "Daemon runtime snapshot omitted a valid scanner_endpoint field."
        )

    connected = payload.get("scanner_connected")
    if type(connected) is not bool:
        raise DaemonProtocolError(
            "Daemon runtime snapshot omitted a boolean scanner_connected field."
        )

    state = payload.get("radio_state")
    if not isinstance(state, Mapping):
        raise DaemonProtocolError(
            "Daemon runtime snapshot omitted a radio_state object."
        )

    return DaemonTuiBootstrap(
        endpoint=endpoint,
        model=_daemon_identity_value(
            payload.get("scanner_model"),
            name="scanner_model",
            fallback="Unknown model",
        ),
        firmware=_daemon_identity_value(
            payload.get("scanner_firmware"),
            name="scanner_firmware",
            fallback="Unknown firmware",
        ),
        connected=connected,
        snapshot=_radio_state_snapshot(state),
    )
