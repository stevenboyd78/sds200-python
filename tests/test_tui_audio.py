
from __future__ import annotations

import asyncio
import struct
import wave
from pathlib import Path

from rich.text import Text
from textual.widgets import Static

from sds200.audio import AudioChunk, AudioStream
from sds200.audio_recording import PcmuWavRecorder
from sds200.audio_session import AudioRecordingSession, AudioSessionStatus
from sds200.network_audio import NetworkAudioStatistics
from sds200.tui import ScannerIdentity, ScannerTuiApp
from sds200.xml_protocol import ScannerInfoParser

from .fakes import FakeAudioTransport


class StatisticalFakeAudioTransport(FakeAudioTransport):
    @property
    def statistics(self) -> NetworkAudioStatistics:
        return NetworkAudioStatistics(
            packets_lost=2,
            duplicate_packets=3,
            late_packets=4,
            malformed_packets=5,
            unexpected_source_packets=6,
            ssrc_mismatch_packets=7,
            timestamp_discontinuities=8,
            receive_errors=9,
            callback_errors=10,
        )


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


def _app(session: AudioRecordingSession) -> ScannerTuiApp:
    return ScannerTuiApp(
        ScannerIdentity(
            endpoint="udp://192.0.2.25:50536",
            model="SDS200",
            firmware="Version 1.26.01",
        ),
        ScannerInfoParser().parse("GSI", XML),
        audio_session=session,
    )


def _plain(widget: Static) -> str:
    content = widget.content
    assert isinstance(content, (str, Text))
    return content if isinstance(content, str) else content.plain


async def _wait_for_status(
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


def test_tui_audio_binding_records_updates_and_stops(tmp_path: Path) -> None:
    async def exercise() -> None:
        output = tmp_path / "tui-audio.wav"
        transport = StatisticalFakeAudioTransport()
        recorder = PcmuWavRecorder(output)
        session = AudioRecordingSession(AudioStream(transport), recorder)
        app = _app(session)

        bindings = {
            (binding.key, binding.action) for binding in ScannerTuiApp.BINDINGS
        }
        assert ("r", "toggle_audio_recording") in bindings

        async with app.run_test(size=(100, 46)) as pilot:
            assert app.audio_thread_alive
            await pilot.press("r")
            await _wait_for_status(session, AudioSessionStatus.RECORDING)

            transport.feed(AudioChunk(bytes((0xFF, 0x80, 0x00, 0x7F))))
            await pilot.pause()
            app._poll_audio_state()
            audio = _plain(app.query_one("#audio", Static))
            assert "Audio: RECORDING" in audio
            assert "Packets / samples: 1 / 4" in audio
            assert f"Output: {output}" in audio
            assert "RTP loss / duplicate: 2 / 3" in audio
            assert "RTP late / malformed: 4 / 5" in audio
            assert "Source / SSRC: 6 / 7" in audio
            assert "Receive / callback: 9 / 10" in audio
            assert "Timestamp gaps: 8" in audio

            await pilot.press("r")
            await _wait_for_status(session, AudioSessionStatus.STOPPED)
            await pilot.pause()
            audio = _plain(app.query_one("#audio", Static))
            assert "Audio: STOPPED" in audio
            assert "Recording completed" in audio
            assert not recorder.open

        assert not app.audio_thread_alive
        with wave.open(str(output), "rb") as recording:
            assert recording.getnchannels() == 1
            assert recording.getsampwidth() == 2
            assert recording.getframerate() == 8000
            assert recording.getnframes() == 4
            assert struct.unpack("<4h", recording.readframes(4)) == (
                0,
                32124,
                -32124,
                0,
            )

    asyncio.run(exercise())


def test_tui_shutdown_finalizes_active_audio_recording(tmp_path: Path) -> None:
    async def exercise() -> None:
        transport = FakeAudioTransport()
        recorder = PcmuWavRecorder(tmp_path / "shutdown.wav")
        session = AudioRecordingSession(AudioStream(transport), recorder)
        app = _app(session)

        async with app.run_test(size=(100, 46)) as pilot:
            await pilot.press("r")
            await _wait_for_status(session, AudioSessionStatus.RECORDING)
            assert recorder.open

        assert session.status is AudioSessionStatus.STOPPED
        assert not recorder.open
        assert not app.audio_thread_alive

    asyncio.run(exercise())
