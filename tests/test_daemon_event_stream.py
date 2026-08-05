from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from sds200 import (
    AudioFanoutSnapshot,
    DaemonEventKind,
    DaemonEventStream,
    DaemonEventSubscriptionClosed,
    RadioStateSnapshot,
    ScannerInfo,
    StateChange,
)


class CallbackSource:
    def __init__(self) -> None:
        self.callbacks: list[Callable[[object], None]] = []

    def subscribe(
        self,
        callback: Callable[[object], None],
    ) -> Callable[[], None]:
        self.callbacks.append(callback)

        def unsubscribe() -> None:
            if callback in self.callbacks:
                self.callbacks.remove(callback)

        return unsubscribe

    def emit(self, value: object) -> None:
        for callback in tuple(self.callbacks):
            callback(value)


class FakeRadioState:
    def __init__(self) -> None:
        self.snapshot = RadioStateSnapshot()


class FakeScanner:
    def __init__(self) -> None:
        self.endpoint = "udp://192.0.2.25:50536"
        self.state = FakeRadioState()
        self.connections = CallbackSource()
        self.psi = CallbackSource()
        self.state_changes = CallbackSource()

    def on_connection(
        self,
        callback: Callable[[bool], None],
    ) -> Callable[[], None]:
        return self.connections.subscribe(callback)  # type: ignore[arg-type]

    def on_psi(
        self,
        callback: Callable[[ScannerInfo], None],
    ) -> Callable[[], None]:
        return self.psi.subscribe(callback)  # type: ignore[arg-type]

    def on_state_change(
        self,
        callback: Callable[[StateChange], None],
    ) -> Callable[[], None]:
        return self.state_changes.subscribe(callback)  # type: ignore[arg-type]


class FakeAudio:
    def __init__(self) -> None:
        self.states = CallbackSource()

    def on_state(
        self,
        callback: Callable[[AudioFanoutSnapshot], None],
    ) -> Callable[[], None]:
        return self.states.subscribe(callback)  # type: ignore[arg-type]


class FakeRouter:
    def __init__(self) -> None:
        self.transitions = CallbackSource()

    def on_transition(
        self,
        callback: Callable[[object], None],
    ) -> Callable[[], None]:
        return self.transitions.subscribe(callback)


@dataclass(frozen=True)
class FakeRecord:
    observed_at: datetime
    payload: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        return dict(self.payload)


class FakeRuntime:
    def __init__(self) -> None:
        self.scanner = FakeScanner()
        self.audio = FakeAudio()
        self.router = FakeRouter()
        self.transitions = CallbackSource()
        self.snapshot_payload: dict[str, object] = {
            "state": "idle",
            "scanner_endpoint": self.scanner.endpoint,
        }

    def snapshot(self) -> FakeRuntime:
        return self

    def as_dict(self) -> dict[str, object]:
        return dict(self.snapshot_payload)

    def on_transition(
        self,
        callback: Callable[[object], None],
    ) -> Callable[[], None]:
        return self.transitions.subscribe(callback)


def test_stream_starts_each_subscription_with_runtime_snapshot() -> None:
    runtime = FakeRuntime()
    stream = DaemonEventStream(runtime)

    first = stream.subscribe()
    first_snapshot = first.get(timeout=0)

    runtime.snapshot_payload["state"] = "running"
    second = stream.subscribe()
    second_snapshot = second.get(timeout=0)

    assert first_snapshot.sequence == 0
    assert first_snapshot.kind == DaemonEventKind.SNAPSHOT
    assert first_snapshot.payload == {
        "state": "idle",
        "scanner_endpoint": runtime.scanner.endpoint,
    }
    assert second_snapshot.sequence == 0
    assert second_snapshot.payload == {
        "state": "running",
        "scanner_endpoint": runtime.scanner.endpoint,
    }


def test_stream_maps_all_sources_into_one_ordered_event_sequence() -> None:
    initial = datetime(2026, 8, 4, 23, 30, tzinfo=UTC)
    generated = iter(
        initial + timedelta(seconds=offset)
        for offset in range(4)
    )
    runtime = FakeRuntime()
    stream = DaemonEventStream(runtime, now=lambda: next(generated))
    subscription = stream.subscribe()
    subscription.get(timeout=0)

    runtime_transition_at = initial + timedelta(minutes=1)
    runtime.transitions.emit(
        FakeRecord(
            runtime_transition_at,
            {"state": "starting"},
        )
    )

    runtime.scanner.connections.emit(True)

    previous = RadioStateSnapshot()
    current = RadioStateSnapshot(
        mode="Trunk Scan",
        channel="Example Channel",
        signal=5,
    )
    runtime.scanner.state.snapshot = current
    runtime.scanner.state_changes.emit(
        StateChange(
            previous=previous,
            current=current,
            fields=frozenset({"mode", "channel", "signal"}),
        )
    )

    psi_at = initial + timedelta(minutes=2)
    runtime.scanner.psi.emit(
        ScannerInfo(
            command="PSI",
            mode="Trunk Scan",
            screen="trunk_scan",
            nodes={},
            raw_xml="<ScannerInfo />",
            received_at=psi_at,
        )
    )

    runtime.audio.states.emit(
        AudioFanoutSnapshot(
            endpoint="rtsp://192.0.2.25/au:scanner.au",
            running=True,
            packets=3,
            samples=480,
            sinks=(),
        )
    )

    destination_at = initial + timedelta(minutes=3)
    runtime.router.transitions.emit(
        FakeRecord(
            destination_at,
            {
                "state": "active",
                "health": "healthy",
                "snapshot": {"subscriber_id": "playback:1"},
            },
        )
    )

    events = [subscription.get(timeout=0) for _ in range(6)]

    assert [event.sequence for event in events] == list(range(1, 7))
    assert [event.kind for event in events] == [
        DaemonEventKind.DAEMON_TRANSITION,
        DaemonEventKind.SCANNER_CONNECTION,
        DaemonEventKind.RADIO_STATE,
        DaemonEventKind.PSI_STATE,
        DaemonEventKind.AUDIO_STATE,
        DaemonEventKind.DESTINATION_HEALTH,
    ]
    assert [event.observed_at for event in events] == [
        runtime_transition_at,
        initial + timedelta(seconds=1),
        initial + timedelta(seconds=2),
        psi_at,
        initial + timedelta(seconds=3),
        destination_at,
    ]

    assert events[0].payload == {"state": "starting"}
    assert events[1].payload == {
        "endpoint": runtime.scanner.endpoint,
        "connected": True,
    }
    assert events[2].payload == {
        "fields": ("channel", "mode", "signal"),
        "previous": {
            "mode": None,
            "screen": None,
            "screen_kind": None,
            "system": None,
            "department": None,
            "site": None,
            "system_index": None,
            "system_hold": None,
            "department_index": None,
            "department_hold": None,
            "site_index": None,
            "site_hold": None,
            "channel": None,
            "channel_index": None,
            "channel_number": None,
            "channel_kind": None,
            "channel_hold": None,
            "frequency": None,
            "modulation": None,
            "sub_audio_detected": None,
            "tone_out_tone_a": None,
            "tone_out_tone_b": None,
            "weather_mode": None,
            "weather_same": None,
            "service_type": None,
            "talkgroup_id": None,
            "unit_id": None,
            "volume": None,
            "squelch": None,
            "signal": None,
            "rssi": None,
            "p25_status": None,
            "mute": None,
            "recording": None,
        },
        "current": {
            "mode": "Trunk Scan",
            "screen": None,
            "screen_kind": None,
            "system": None,
            "department": None,
            "site": None,
            "system_index": None,
            "system_hold": None,
            "department_index": None,
            "department_hold": None,
            "site_index": None,
            "site_hold": None,
            "channel": "Example Channel",
            "channel_index": None,
            "channel_number": None,
            "channel_kind": None,
            "channel_hold": None,
            "frequency": None,
            "modulation": None,
            "sub_audio_detected": None,
            "tone_out_tone_a": None,
            "tone_out_tone_b": None,
            "weather_mode": None,
            "weather_same": None,
            "service_type": None,
            "talkgroup_id": None,
            "unit_id": None,
            "volume": None,
            "squelch": None,
            "signal": 5,
            "rssi": None,
            "p25_status": None,
            "mute": None,
            "recording": None,
        },
    }
    assert events[3].payload == {
        "command": "PSI",
        "received_at": psi_at.isoformat(),
        "state": events[2].payload["current"],
    }
    assert events[4].payload == {
        "endpoint": "rtsp://192.0.2.25/au:scanner.au",
        "running": True,
        "packets": 3,
        "samples": 480,
        "sinks": (),
    }
    assert events[5].payload == {
        "state": "active",
        "health": "healthy",
        "snapshot": {"subscriber_id": "playback:1"},
    }


def test_stream_close_unsubscribes_sources_and_closes_subscriptions() -> None:
    runtime = FakeRuntime()
    stream = DaemonEventStream(runtime)
    subscription = stream.subscribe()
    subscription.get(timeout=0)

    assert len(runtime.transitions.callbacks) == 1
    assert len(runtime.scanner.connections.callbacks) == 1
    assert len(runtime.scanner.psi.callbacks) == 1
    assert len(runtime.scanner.state_changes.callbacks) == 1
    assert len(runtime.audio.states.callbacks) == 1
    assert len(runtime.router.transitions.callbacks) == 1

    stream.close()
    stream.close()

    assert runtime.transitions.callbacks == []
    assert runtime.scanner.connections.callbacks == []
    assert runtime.scanner.psi.callbacks == []
    assert runtime.scanner.state_changes.callbacks == []
    assert runtime.audio.states.callbacks == []
    assert runtime.router.transitions.callbacks == []
    assert stream.closed
    assert stream.subscriber_count == 0

    with pytest.raises(DaemonEventSubscriptionClosed):
        subscription.get(timeout=0)

    runtime.scanner.connections.emit(True)
    assert stream.sequence == 0
