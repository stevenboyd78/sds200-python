from __future__ import annotations

import asyncio
from pathlib import Path

from rich.text import Text
from textual.widgets import Static

from sds200.audio import AudioChunk, AudioStream
from sds200.audio_session import AudioSessionStatus
from sds200.audio_sinks import PcmSinkStatistics
from sds200.state import snapshot_from_scanner_info
from sds200.tui import ScannerIdentity, ScannerTuiApp
from sds200.tui_audio import (
    RecordingPathPolicy,
    SavedPlaybackStatus,
    TuiAudioSession,
)
from sds200.xml_protocol import ScannerInfoParser

from .fakes import FakeAudioTransport

XML = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">\n'
    '<System Name="Example P25 System" />\n'
    '<Department Name="Example Department" />\n'
    '<Site Name="Example Simulcast" Mod="NFM" />\n'
    '<TGID Name="Example Dispatch" TGID="TGID:65132" SvcType="Interop" />\n'
    '<SiteFrequency Freq="769.431250MHz" />\n'
    '<Property VOL="10" SQL="2" Sig="5" Rssi="-86" Rec="Off" Mute="Unmute" />\n'
    '</ScannerInfo>'
)


def _plain(widget: Static) -> str:
    content = widget.content
    assert isinstance(content, (str, Text))
    return content if isinstance(content, str) else content.plain


class CollectingPlaybackSink:
    def __init__(self) -> None:
        self._running = False
        self.start_calls = 0
        self.stop_calls = 0

    @property
    def name(self) -> str:
        return "playback:test"

    @property
    def running(self) -> bool:
        return self._running

    @property
    def statistics(self) -> PcmSinkStatistics:
        return PcmSinkStatistics()

    def start(self) -> None:
        self._running = True
        self.start_calls += 1

    def submit_pcm(self, data: bytes) -> None:
        del data
        assert self._running

    def stop(self) -> None:
        self._running = False
        self.stop_calls += 1


async def _wait_for_status(
    session: TuiAudioSession,
    status: AudioSessionStatus,
) -> None:
    for _ in range(200):
        if session.status is status:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(
        f"Expected audio status {status.value}, received {session.status.value}"
    )


def test_tui_defers_requested_playback_until_connected_live_psi() -> None:
    async def exercise() -> None:
        transport = FakeAudioTransport()
        playback = CollectingPlaybackSink()
        info = ScannerInfoParser().parse("GSI", XML)
        session = TuiAudioSession(
            AudioStream(transport),
            RecordingPathPolicy(),
            live_playback=True,
            playback_sink=playback,
        )
        app = ScannerTuiApp(
            ScannerIdentity(
                endpoint="udp://192.0.2.25:50536",
                model="SDS200",
                firmware="Version 1.26.01",
            ),
            snapshot_from_scanner_info(info),
            audio_session=session,
            connected=True,
        )

        async with app.run_test(size=(120, 40)):
            for _ in range(200):
                if session.open and not app._audio_pending:
                    break
                await asyncio.sleep(0.01)
            assert session.open
            assert session.live_playback_enabled
            assert not session.live_playback_active
            assert not playback.running

            app._apply_radio_state(snapshot_from_scanner_info(info))
            for _ in range(200):
                if session.live_playback_active and not app._audio_pending:
                    break
                await asyncio.sleep(0.01)

            assert session.live_playback_active
            assert playback.running
            panel = _plain(app.query_one("#audio", Static))
            assert "Live playback: ON" in panel
            assert "Playback device: ACTIVE" in panel

        assert playback.stop_calls == 1

    asyncio.run(exercise())


def test_tui_short_layout_summarizes_audio_and_status_and_restores_detail(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        transport = FakeAudioTransport()
        playback = CollectingPlaybackSink()
        info = ScannerInfoParser().parse("GSI", XML)
        session = TuiAudioSession(
            AudioStream(transport),
            RecordingPathPolicy(directory=tmp_path),
            live_playback=True,
            playback_sink=playback,
        )
        app = ScannerTuiApp(
            ScannerIdentity(
                endpoint="udp://192.0.2.25:50536",
                model="SDS200",
                firmware="Version 1.26.01",
            ),
            snapshot_from_scanner_info(info),
            audio_session=session,
            connected=True,
        )

        async with app.run_test(size=(90, 28)) as pilot:
            for _ in range(200):
                if session.open and not app._audio_pending:
                    break
                await asyncio.sleep(0.01)
            assert session.open

            app._apply_radio_state(snapshot_from_scanner_info(info))
            for _ in range(200):
                if session.live_playback_active and not app._audio_pending:
                    break
                await asyncio.sleep(0.01)
            await pilot.pause()

            audio_widget = app.query_one("#audio", Static)
            status_widget = app.query_one("#status", Static)
            body = app.query_one("#body")

            compact_audio = _plain(audio_widget)
            compact_status = _plain(status_widget)

            assert compact_audio.splitlines() == [
                "Live: ON | device ACTIVE",
                "Saved / recording: STOPPED | IDLE",
                "Session: 0.0s | 0 packets | 0 completed",
                "Audio: Live playback active | loss/dup 0/0",
            ]
            assert compact_status.splitlines() == [
                "Health: AVAILABLE / NORMAL",
                "PSI: LIVE PSI | recovery 0/0/0",
                "Levels: VOL 10/29 | SQL 2/19",
                "Status: Live PSI update received",
            ]
            assert status_widget.region.bottom <= body.region.bottom
            assert "Output:" not in compact_audio
            assert "Availability:" not in compact_status

            await pilot.resize_terminal(120, 40)
            await pilot.pause()

            detailed_audio = _plain(audio_widget)
            detailed_status = _plain(status_widget)

            assert app.screen.has_class("-wide")
            assert app.screen.has_class("-tall")
            assert "Playback device: ACTIVE" in detailed_audio
            assert "Output:" in detailed_audio
            assert "Playback underflow / dropped:" in detailed_audio
            assert "Availability: AVAILABLE since" in detailed_status
            assert "PSI recovery A/S/F: 0 / 0 / 0" in detailed_status
            assert "Detail: Live PSI update received" in detailed_status

        assert playback.stop_calls == 1

    asyncio.run(exercise())


def test_tui_manual_playback_toggle_keeps_device_prepared() -> None:
    async def exercise() -> None:
        transport = FakeAudioTransport()
        playback = CollectingPlaybackSink()
        session = TuiAudioSession(
            AudioStream(transport),
            RecordingPathPolicy(),
            playback_sink=playback,
        )
        app = ScannerTuiApp(
            ScannerIdentity(
                endpoint="udp://192.0.2.25:50536",
                model="SDS200",
                firmware="Version 1.26.01",
            ),
            snapshot_from_scanner_info(ScannerInfoParser().parse("GSI", XML)),
            audio_session=session,
        )

        async with app.run_test(size=(120, 40)) as pilot:
            for _ in range(200):
                if session.open and not app._audio_pending:
                    break
                await asyncio.sleep(0.01)

            await pilot.press("a")
            for _ in range(200):
                if session.live_playback_active and not app._audio_pending:
                    break
                await asyncio.sleep(0.01)
            assert playback.running
            assert session.live_playback_active

            await pilot.press("a")
            for _ in range(200):
                if not session.live_playback_active and not app._audio_pending:
                    break
                await asyncio.sleep(0.01)
            assert playback.running
            assert playback.stop_calls == 0
            panel = _plain(app.query_one("#audio", Static))
            assert "Live playback: OFF" in panel
            assert "Playback device: READY / MUTED" in panel

        assert playback.stop_calls == 1

    asyncio.run(exercise())


def test_tui_panel_reports_active_device_during_saved_playback() -> None:
    async def exercise() -> None:
        transport = FakeAudioTransport()
        playback = CollectingPlaybackSink()
        session = TuiAudioSession(
            AudioStream(transport),
            RecordingPathPolicy(),
            playback_sink=playback,
        )
        app = ScannerTuiApp(
            ScannerIdentity(
                endpoint="udp://192.0.2.25:50536",
                model="SDS200",
                firmware="Version 1.26.01",
            ),
            snapshot_from_scanner_info(ScannerInfoParser().parse("GSI", XML)),
            audio_session=session,
        )

        async with app.run_test(size=(120, 40)):
            for _ in range(200):
                if session.open and not app._audio_pending:
                    break
                await asyncio.sleep(0.01)
            assert session.open
            assert not app._audio_pending

            playback.start()
            with session._state_lock:
                session._saved_status = SavedPlaybackStatus.PLAYING
            app._refresh_view()

            panel = _plain(app.query_one("#audio", Static))
            assert "Saved playback: PLAYING" in panel
            assert "Playback device: ACTIVE" in panel

        assert playback.stop_calls == 1

    asyncio.run(exercise())


def test_tui_creates_consecutive_recordings_and_lists_the_library(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        transport = FakeAudioTransport()
        session = TuiAudioSession(
            AudioStream(transport),
            RecordingPathPolicy(directory=tmp_path),
        )
        app = ScannerTuiApp(
            ScannerIdentity(
                endpoint="udp://192.0.2.25:50536",
                model="SDS200",
                firmware="Version 1.26.01",
            ),
            snapshot_from_scanner_info(ScannerInfoParser().parse("GSI", XML)),
            audio_session=session,
        )

        async with app.run_test(size=(100, 50)) as pilot:
            for _ in range(200):
                if session.open and not app._audio_pending:
                    break
                await asyncio.sleep(0.01)
            assert session.open
            assert not app._audio_pending

            await pilot.press("r")
            await _wait_for_status(session, AudioSessionStatus.RECORDING)
            transport.feed(AudioChunk(bytes((0xFF, 0x80))))
            await pilot.press("r")
            await _wait_for_status(session, AudioSessionStatus.STOPPED)

            await pilot.press("r")
            await _wait_for_status(session, AudioSessionStatus.RECORDING)
            transport.feed(AudioChunk(bytes((0x00, 0x7F))))
            await pilot.press("r")
            await _wait_for_status(session, AudioSessionStatus.STOPPED)

            await pilot.press("l")
            await pilot.pause()
            panel = _plain(app.query_one("#audio", Static))
            assert "Completed this session: 2" in panel
            assert "Recordings: 2 newest first" in panel
            assert sum(entry.path.name in panel for entry in session.recordings) == 2

        assert not session.open
        assert not app.audio_thread_alive

    asyncio.run(exercise())
