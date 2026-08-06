from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Event, RLock

from rich.text import Text
from textual.widgets import Static

from sds200.audio import AudioChunk, AudioStream
from sds200.audio_recording import PcmuWavRecorder
from sds200.audio_session import AudioRecordingSession, AudioSessionStatus
from sds200.models import ScannerInfo
from sds200.radio import SDSScanner
from sds200.state import RadioStateSnapshot, snapshot_from_scanner_info
from sds200.transport import TransportDiagnostic
from sds200.tui import ScannerIdentity, ScannerTuiApp
from sds200.xml_protocol import ScannerInfoParser

from .fakes import FakeAudioTransport

INITIAL_XML = """<?xml version="1.0" encoding="utf-8"?>
<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">
<System Name="Example P25 System" />
<Department Name="Example Department" />
<Site Name="Example Simulcast" Mod="NFM" />
<TGID Name="Initial Dispatch" SvcType="Dispatch" />
<SiteFrequency Freq="769.431250MHz" />
<Property Sig="2" Rssi="-96" Rec="Off" Mute="Unmute" />
</ScannerInfo>"""

UPDATED_XML = """<?xml version="1.0" encoding="utf-8"?>
<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">
<System Name="Example P25 System" />
<Department Name="Example Department" />
<Site Name="Example Simulcast" Mod="NFM" />
<TGID Name="Updated Dispatch" SvcType="Interop" />
<SiteFrequency Freq="769.681250MHz" />
<Property Sig="5" Rssi="-70" Rec="On" Mute="Unmute" />
</ScannerInfo>"""

FIXTURE = Path(__file__).parent / "fixtures" / "replay" / "sds100-tui-live.jsonl"
Unsubscribe = Callable[[], None]


class FakeLiveRadio:
    def __init__(self, initial: ScannerInfo) -> None:
        self.connected = True
        self.initial = initial
        self.interval_ms: int | None = None
        self.started = Event()
        self.stopped = Event()
        self.unsubscribe_count = 0
        self.reconnect_calls = 0
        self._lock = RLock()
        self._state_callbacks: list[Callable[[RadioStateSnapshot], None]] = []
        self._connection_callbacks: list[Callable[[bool], None]] = []
        self._diagnostic_callbacks: list[Callable[[TransportDiagnostic], None]] = []

    def reconnect(self) -> None:
        self.reconnect_calls += 1
        self.emit_connection(True)

    def on_state(self, callback: Callable[[RadioStateSnapshot], None]) -> Unsubscribe:
        return self._subscribe(self._state_callbacks, callback)

    def on_connection(self, callback: Callable[[bool], None]) -> Unsubscribe:
        return self._subscribe(self._connection_callbacks, callback)

    def on_diagnostic(
        self,
        callback: Callable[[TransportDiagnostic], None],
    ) -> Unsubscribe:
        return self._subscribe(self._diagnostic_callbacks, callback)

    @contextmanager
    def radio_state_push(
        self,
        interval_ms: int = 500,
        *,
        timeout: float = 3.0,
    ) -> Iterator[RadioStateSnapshot]:
        del timeout
        self.interval_ms = interval_ms
        self.started.set()
        try:
            yield snapshot_from_scanner_info(self.initial)
        finally:
            self.stopped.set()

    def emit_state(self, snapshot: RadioStateSnapshot) -> None:
        self._emit(self._state_callbacks, snapshot)

    def emit_connection(self, connected: bool) -> None:
        self.connected = connected
        self._emit(self._connection_callbacks, connected)

    def emit_diagnostic(self, diagnostic: TransportDiagnostic) -> None:
        self._emit(self._diagnostic_callbacks, diagnostic)

    def _subscribe(
        self,
        callbacks: list[Callable[..., None]],
        callback: Callable[..., None],
    ) -> Unsubscribe:
        with self._lock:
            callbacks.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in callbacks:
                    callbacks.remove(callback)
                    self.unsubscribe_count += 1

        return unsubscribe

    def _emit(self, callbacks: list[Callable[..., None]], value: object) -> None:
        with self._lock:
            current = tuple(callbacks)
        for callback in current:
            callback(value)


def _info(xml: str = INITIAL_XML, *, command: str = "GSI") -> ScannerInfo:
    return ScannerInfoParser().parse(command, xml)


def _app(
    radio: FakeLiveRadio,
    *,
    stale_after: float = 3.0,
    clock: Callable[[], float] | None = None,
    audio_session: AudioRecordingSession | None = None,
    psi_auto_recover: bool = True,
    psi_recover_after: float = 10.0,
    psi_recovery_cooldown: float = 60.0,
) -> ScannerTuiApp:
    kwargs: dict[str, object] = {}
    if clock is not None:
        kwargs["clock"] = clock
    return ScannerTuiApp(
        ScannerIdentity(
            endpoint="fake://scanner",
            model="SDS200",
            firmware="Version 1.26.01",
        ),
        snapshot_from_scanner_info(radio.initial),
        radio=radio,
        audio_session=audio_session,
        interval_ms=250,
        stale_after=stale_after,
        psi_auto_recover=psi_auto_recover,
        psi_recover_after=psi_recover_after,
        psi_recovery_cooldown=psi_recovery_cooldown,
        **kwargs,
    )


def _plain(widget: Static) -> str:
    content = widget.content
    assert isinstance(content, Text)
    return content.plain


async def _wait_for_audio_status(
    session: AudioRecordingSession,
    status: AudioSessionStatus,
) -> None:
    for _ in range(200):
        if session.status is status:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(
        f"Expected audio status {status.value}, received {session.status.value}"
    )


async def _wait_for_reconnects(radio: FakeLiveRadio, count: int) -> None:
    for _ in range(200):
        if radio.reconnect_calls == count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(
        f"Expected {count} reconnect calls, received {radio.reconnect_calls}"
    )


def test_live_state_callbacks_update_widgets_from_radio_threads() -> None:
    async def exercise() -> None:
        radio = FakeLiveRadio(_info())
        app = _app(radio)
        updated = snapshot_from_scanner_info(_info(UPDATED_XML, command="PSI"))

        async with app.run_test(size=(80, 34)) as pilot:
            assert await asyncio.to_thread(radio.started.wait, 1.0)
            await asyncio.to_thread(radio.emit_state, updated)
            await pilot.pause()

            assert "Updated Dispatch" in _plain(app.query_one("#channel", Static))
            assert "STRONG (5)" in _plain(app.query_one("#state", Static))
            assert "LIVE PSI" in _plain(app.query_one("#status", Static))
            assert radio.interval_ms == 250

        assert await asyncio.to_thread(radio.stopped.wait, 1.0)
        assert radio.unsubscribe_count == 3
        assert not app.live_thread_alive

    asyncio.run(exercise())


def test_connection_and_diagnostic_callbacks_show_recovery_state() -> None:
    async def exercise() -> None:
        radio = FakeLiveRadio(_info())
        app = _app(radio)

        async with app.run_test(size=(80, 34)) as pilot:
            assert await asyncio.to_thread(radio.started.wait, 1.0)
            await asyncio.to_thread(radio.emit_connection, False)
            await pilot.pause()
            assert "DISCONNECTED" in _plain(app.query_one("#connection", Static))
            assert "RECONNECTING" in _plain(app.query_one("#status", Static))

            await asyncio.to_thread(radio.emit_connection, True)
            await asyncio.to_thread(
                radio.emit_diagnostic,
                TransportDiagnostic(
                    kind="reconnect_attempt",
                    message="Reconnect attempt 2 in 1.0 seconds",
                    attempt=2,
                    delay_seconds=1.0,
                ),
            )
            await pilot.pause()
            assert "DEGRADED" in _plain(app.query_one("#connection", Static))
            status = _plain(app.query_one("#status", Static))
            assert "RECONNECT ATTEMPT" in status
            assert "Reconnect attempt 2" in status

    asyncio.run(exercise())


def test_stale_live_state_recovers_after_next_psi_update() -> None:
    async def exercise() -> None:
        now = [100.0]
        radio = FakeLiveRadio(_info())
        app = _app(radio, stale_after=2.0, clock=lambda: now[0])
        updated = snapshot_from_scanner_info(_info(UPDATED_XML, command="PSI"))

        async with app.run_test(size=(80, 34)) as pilot:
            assert await asyncio.to_thread(radio.started.wait, 1.0)
            await pilot.pause()
            now[0] = 102.5
            app.check_stale()
            assert app.stale
            status = _plain(app.query_one("#status", Static))
            assert "STALE" in status
            assert "No PSI update for 2.5 seconds" in status

            await asyncio.to_thread(radio.emit_state, updated)
            await pilot.pause()
            assert not app.stale
            assert "AVAILABLE" in _plain(app.query_one("#status", Static))

    asyncio.run(exercise())


def test_stale_live_state_automatically_reconnects_with_cooldown() -> None:
    async def exercise() -> None:
        now = [100.0]
        radio = FakeLiveRadio(_info())
        app = _app(
            radio,
            stale_after=2.0,
            clock=lambda: now[0],
            psi_recover_after=5.0,
            psi_recovery_cooldown=60.0,
        )
        updated = snapshot_from_scanner_info(_info(UPDATED_XML, command="PSI"))

        async with app.run_test(size=(80, 36)) as pilot:
            assert await asyncio.to_thread(radio.started.wait, 1.0)

            now[0] = 102.5
            app.check_stale()
            assert app.stale
            assert radio.reconnect_calls == 0

            now[0] = 105.1
            app.check_stale()
            await _wait_for_reconnects(radio, 1)
            await pilot.pause()
            status = _plain(app.query_one("#status", Static))
            assert "PSI recovery A/S/F: 1 / 0 / 0" in status

            now[0] = 116.0
            app.check_stale()
            assert radio.reconnect_calls == 1

            now[0] = 166.0
            app.check_stale()
            await _wait_for_reconnects(radio, 2)
            await pilot.pause()
            status = _plain(app.query_one("#status", Static))
            assert "PSI recovery A/S/F: 2 / 0 / 1" in status

            now[0] = 167.0
            await asyncio.to_thread(radio.emit_state, updated)
            await pilot.pause()
            status = _plain(app.query_one("#status", Static))
            assert "LIVE PSI" in status
            assert "PSI recovery A/S/F: 2 / 1 / 1" in status

    asyncio.run(exercise())


def test_automatic_psi_recovery_does_not_interrupt_audio_recording(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        now = [100.0]
        radio = FakeLiveRadio(_info())
        transport = FakeAudioTransport()
        recorder = PcmuWavRecorder(tmp_path / "automatic-recovery-audio.wav")
        session = AudioRecordingSession(AudioStream(transport), recorder)
        app = _app(
            radio,
            stale_after=2.0,
            clock=lambda: now[0],
            audio_session=session,
            psi_recover_after=5.0,
        )
        updated = snapshot_from_scanner_info(_info(UPDATED_XML, command="PSI"))

        async with app.run_test(size=(100, 46)) as pilot:
            assert await asyncio.to_thread(radio.started.wait, 1.0)
            await pilot.press("r")
            await _wait_for_audio_status(session, AudioSessionStatus.RECORDING)

            transport.feed(AudioChunk(bytes((0xFF, 0x80, 0x00, 0x7F))))
            assert session.snapshot().packets == 1

            now[0] = 105.1
            app.check_stale()
            await _wait_for_reconnects(radio, 1)
            await pilot.pause()

            assert session.status is AudioSessionStatus.RECORDING
            assert transport.running
            transport.feed(AudioChunk(bytes((0x7F, 0x00, 0x80, 0xFF))))
            assert session.snapshot().packets == 2

            now[0] = 106.0
            await asyncio.to_thread(radio.emit_state, updated)
            await pilot.pause()
            assert "PSI recovery A/S/F: 1 / 1 / 0" in _plain(
                app.query_one("#status", Static)
            )
            assert session.status is AudioSessionStatus.RECORDING

            await pilot.press("r")
            await _wait_for_audio_status(session, AudioSessionStatus.STOPPED)

        assert not recorder.open
        assert not app.audio_thread_alive

    asyncio.run(exercise())


def test_replay_fixture_streams_multiple_live_psi_states() -> None:
    states: list[RadioStateSnapshot] = []

    with SDSScanner.replay(FIXTURE, expected_model="SDS100") as radio:
        assert radio.get_model() == "SDS100"
        assert radio.get_firmware() == "Version 1.26.01"
        assert radio.get_scanner_info().channel == "Initial Dispatch"
        unsubscribe = radio.on_state(states.append)
        with radio.radio_state_push(100) as first:
            assert isinstance(first, RadioStateSnapshot)
            assert first.channel == "First Dispatch"
        unsubscribe()

        assert not radio.psi_active

    assert [state.channel for state in states] == ["First Dispatch", "Second Dispatch"]
    assert states[-1].frequency == "769.681250MHz"
    assert states[-1].recording == "On"


def test_scanner_reconnect_does_not_interrupt_active_audio_recording(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        radio = FakeLiveRadio(_info())
        transport = FakeAudioTransport()
        recorder = PcmuWavRecorder(tmp_path / "reconnect-audio.wav")
        session = AudioRecordingSession(AudioStream(transport), recorder)
        app = _app(radio, audio_session=session)

        async with app.run_test(size=(100, 46)) as pilot:
            assert await asyncio.to_thread(radio.started.wait, 1.0)
            await pilot.press("r")
            await _wait_for_audio_status(session, AudioSessionStatus.RECORDING)

            await asyncio.to_thread(radio.emit_connection, False)
            await pilot.pause()
            assert "DISCONNECTED" in _plain(app.query_one("#connection", Static))
            assert session.status is AudioSessionStatus.RECORDING
            assert transport.running
            assert session.snapshot().packets == 0

            transport.feed(AudioChunk(bytes((0xFF, 0x80, 0x00, 0x7F))))
            assert session.snapshot().packets == 1

            await pilot.press("c")
            for _ in range(200):
                if radio.reconnect_calls == 1:
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("Reconnect request did not complete")
            await pilot.pause()

            assert "CONNECTED" in _plain(app.query_one("#connection", Static))
            assert session.status is AudioSessionStatus.RECORDING
            assert transport.running

            await pilot.press("r")
            await _wait_for_audio_status(session, AudioSessionStatus.STOPPED)

        assert await asyncio.to_thread(radio.stopped.wait, 1.0)
        assert radio.unsubscribe_count == 3
        assert not app.live_thread_alive
        assert not app.control_thread_alive
        assert not app.audio_thread_alive
        assert not recorder.open

    asyncio.run(exercise())
