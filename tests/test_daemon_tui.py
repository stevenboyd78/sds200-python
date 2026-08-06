from __future__ import annotations

import queue
import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime

import pytest

from sds200 import (
    DaemonEvent,
    DaemonEventKind,
    DaemonProtocolError,
    DaemonTuiRadio,
    RadioStateSnapshot,
    ScannerScreenKind,
    UnsupportedScannerFeatureError,
)
from sds200.transport import TransportDiagnostic


def runtime_snapshot(
    *,
    connected: bool = True,
    channel: str = "Initial Dispatch",
) -> dict[str, object]:
    return {
        "scanner_connected": connected,
        "radio_state": {
            "mode": "Trunk Scan",
            "screen": "trunk_scan",
            "screen_kind": "scanning",
            "system": "Example System",
            "channel": channel,
            "channel_index": 400,
            "signal": 4,
            "rssi": -82,
        },
    }


def event(
    sequence: int,
    kind: DaemonEventKind,
    payload: Mapping[str, object],
) -> DaemonEvent:
    return DaemonEvent(
        sequence=sequence,
        observed_at=datetime(2026, 8, 6, tzinfo=UTC),
        kind=kind,
        payload=payload,
    )


class FakeApiClient:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1

    def hold(
        self,
        target: str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        timeout: float = 2.0,
    ) -> Mapping[str, object]:
        self.calls.append(("hold", target, first, second, timeout))
        return self._result(channel="Held Dispatch")

    def next(
        self,
        target: str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        count: int = 1,
        timeout: float = 2.0,
    ) -> Mapping[str, object]:
        self.calls.append(
            ("next", target, first, second, count, timeout)
        )
        return self._result(channel="Next Dispatch")

    def previous(
        self,
        target: str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        count: int = 1,
        timeout: float = 2.0,
    ) -> Mapping[str, object]:
        self.calls.append(
            ("previous", target, first, second, count, timeout)
        )
        return self._result(channel="Previous Dispatch")

    def reconnect(
        self,
        *,
        timeout: float = 2.0,
    ) -> Mapping[str, object]:
        self.calls.append(("reconnect", timeout))
        return self._result(channel="Reconnected Dispatch")

    def _result(self, *, channel: str) -> Mapping[str, object]:
        return {
            "snapshot": runtime_snapshot(channel=channel),
        }


class FakeEventClient:
    def __init__(self, first: DaemonEvent) -> None:
        self._events: queue.Queue[DaemonEvent | object] = queue.Queue()
        self._events.put(first)
        self._closed = threading.Event()
        self.close_calls = 0
        self._sentinel = object()

    def receive(self) -> DaemonEvent:
        item = self._events.get(timeout=1.0)
        if item is self._sentinel:
            raise RuntimeError("event client closed")
        assert isinstance(item, DaemonEvent)
        return item

    def push(self, item: DaemonEvent) -> None:
        self._events.put(item)

    def close(self) -> None:
        self.close_calls += 1
        if not self._closed.is_set():
            self._closed.set()
            self._events.put(self._sentinel)


def wait_for(
    predicate: Callable[[], bool],
) -> None:
    for _ in range(200):
        if predicate():
            return
        threading.Event().wait(0.005)
    raise AssertionError("Timed out waiting for daemon TUI adapter state.")


def make_radio() -> tuple[DaemonTuiRadio, FakeApiClient, FakeEventClient]:
    api = FakeApiClient()
    events = FakeEventClient(
        event(
            0,
            DaemonEventKind.SNAPSHOT,
            runtime_snapshot(),
        )
    )
    return DaemonTuiRadio(api, events), api, events


def test_daemon_tui_radio_streams_authoritative_state_and_connection() -> None:
    radio, api, events = make_radio()
    states: list[RadioStateSnapshot] = []
    connections: list[bool] = []
    unsubscribe_state = radio.on_state(states.append)
    unsubscribe_connection = radio.on_connection(connections.append)

    with radio.radio_state_push(500) as first:
        assert first.channel == "Initial Dispatch"
        assert first.screen_kind is ScannerScreenKind.SCANNING
        assert first.rssi == -82.0
        assert radio.connected is True
        assert connections == [True]

        events.push(
            event(
                1,
                DaemonEventKind.PSI_STATE,
                {
                    "state": {
                        "screen_kind": "scanning",
                        "channel": "Updated Dispatch",
                        "signal": 5,
                        "rssi": -70.5,
                    }
                },
            )
        )
        wait_for(lambda: len(states) == 1)
        assert states[0].channel == "Updated Dispatch"
        assert states[0].signal == 5
        assert states[0].rssi == -70.5

        events.push(
            event(
                2,
                DaemonEventKind.SCANNER_CONNECTION,
                {"connected": False},
            )
        )
        wait_for(lambda: connections == [True, False])
        assert radio.connected is False

    unsubscribe_state()
    unsubscribe_connection()
    assert events.close_calls >= 1
    assert api.close_calls == 0
    assert not radio.event_thread_alive

    radio.close()
    assert api.close_calls == 1


def test_daemon_tui_radio_delegates_safe_controls_and_applies_snapshots() -> None:
    radio, api, _ = make_radio()
    states: list[RadioStateSnapshot] = []
    connections: list[bool] = []
    radio.on_state(states.append)
    radio.on_connection(connections.append)

    radio.hold("TGID", 400)
    radio.next("TGID", 400, count=2)
    radio.previous("TGID", 400)
    radio.reconnect()

    assert api.calls == [
        ("hold", "TGID", 400, None, 2.0),
        ("next", "TGID", 400, None, 2, 2.0),
        ("previous", "TGID", 400, None, 1, 2.0),
        ("reconnect", 2.0),
    ]
    assert [state.channel for state in states] == [
        "Held Dispatch",
        "Next Dispatch",
        "Previous Dispatch",
        "Reconnected Dispatch",
    ]
    assert connections == [True]


@pytest.mark.parametrize(
    ("operation", "message"),
    [
        ("set_volume", "Volume control"),
        ("set_squelch", "Squelch control"),
    ],
)
def test_daemon_tui_radio_rejects_unavailable_level_controls(
    operation: str,
    message: str,
) -> None:
    radio, _, _ = make_radio()

    with pytest.raises(UnsupportedScannerFeatureError, match=message):
        getattr(radio, operation)(5)


def test_daemon_tui_radio_reports_event_stream_failure_as_diagnostic() -> None:
    radio, _, events = make_radio()
    diagnostics: list[TransportDiagnostic] = []
    connections: list[bool] = []
    radio.on_diagnostic(diagnostics.append)
    radio.on_connection(connections.append)

    with radio.radio_state_push() as first:
        assert first.channel == "Initial Dispatch"
        events.close()
        wait_for(lambda: len(diagnostics) == 1)

        assert diagnostics[0].kind == "daemon_event_disconnected"
        assert "event client closed" in diagnostics[0].message
        assert connections == [True, False]
        assert radio.connected is False


@pytest.mark.parametrize(
    "state",
    [
        {"signal": True},
        {"rssi": "invalid"},
        {"screen_kind": "unsupported"},
    ],
)
def test_daemon_tui_radio_rejects_malformed_radio_state(
    state: Mapping[str, object],
) -> None:
    api = FakeApiClient()
    events = FakeEventClient(
        event(
            0,
            DaemonEventKind.SNAPSHOT,
            {
                "scanner_connected": True,
                "radio_state": state,
            },
        )
    )
    radio = DaemonTuiRadio(api, events)

    with pytest.raises(DaemonProtocolError), radio.radio_state_push():
        pass


def test_daemon_tui_radio_close_is_idempotent() -> None:
    radio, api, events = make_radio()

    radio.close()
    radio.close()

    assert api.close_calls == 1
    assert events.close_calls == 1
