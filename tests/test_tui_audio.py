from __future__ import annotations

import asyncio
import json
import struct
import threading
import wave
from collections.abc import Callable
from pathlib import Path

import pytest
from rich.text import Text
from textual.widgets import Static

from sds200.audio import AudioChunk, AudioStream
from sds200.audio_recording import PcmuWavRecorder
from sds200.audio_session import AudioRecordingSession, AudioSessionStatus
from sds200.network_audio import NetworkAudioStatistics
from sds200.recording_metadata import recording_metadata_path
from sds200.state import RadioStateSnapshot
from sds200.tui import ScannerIdentity, ScannerTuiApp
from sds200.tui_audio import RecordingPathPolicy, TuiAudioSession
from sds200.xml_protocol import ScannerInfoParser

from .fakes import BlockingStartAudioTransport, FakeAudioTransport


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
    "</ScannerInfo>"
)


def _app(
    session: AudioRecordingSession | TuiAudioSession,
) -> ScannerTuiApp:
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
    session: AudioRecordingSession | TuiAudioSession,
    status: AudioSessionStatus,
) -> None:
    for _ in range(200):
        if session.status is status:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"Expected audio status {status.value}, received {session.status.value}")


def test_tui_audio_binding_records_updates_and_stops(tmp_path: Path) -> None:
    async def exercise() -> None:
        output = tmp_path / "tui-audio.wav"
        transport = StatisticalFakeAudioTransport()
        recorder = PcmuWavRecorder(output)
        session = AudioRecordingSession(AudioStream(transport), recorder)
        app = _app(session)

        bindings = {(binding.key, binding.action) for binding in ScannerTuiApp.BINDINGS}
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


def test_tui_managed_recording_uses_live_state_for_metadata(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        output = tmp_path / "managed.wav"
        transport = FakeAudioTransport()
        session = TuiAudioSession(
            AudioStream(transport),
            RecordingPathPolicy(output=output),
            metadata=True,
            scanner="SDS200",
        )
        app = _app(session)

        async with app.run_test(size=(100, 46)) as pilot:
            for _ in range(200):
                if session.open and not app._audio_pending:
                    break
                await asyncio.sleep(0.01)
            assert session.open
            assert not app._audio_pending

            await pilot.press("r")
            await _wait_for_status(session, AudioSessionStatus.RECORDING)
            app.update_snapshot(
                RadioStateSnapshot(
                    system="County",
                    department="Fire",
                    site="North",
                    channel="Tac 1",
                    frequency="154.2800",
                ),
                connected=True,
            )
            await pilot.press("r")
            await _wait_for_status(session, AudioSessionStatus.STOPPED)
            await pilot.pause()
            assert "Recording and metadata completed" in _plain(app.query_one("#audio", Static))

        payload = json.loads(recording_metadata_path(output).read_text(encoding="utf-8"))
        assert payload["boundaries"]["started"]["state"]["channel"] == ("Example Dispatch")
        assert payload["boundaries"]["stopped"]["state"]["channel"] == "Tac 1"

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
        assert not app._poll_timers

    asyncio.run(exercise())


def test_tui_audio_poll_skips_rendering_after_shutdown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = FakeAudioTransport()
    recorder = PcmuWavRecorder(tmp_path / "shutdown-poll.wav")
    session = AudioRecordingSession(AudioStream(transport), recorder)
    app = _app(session)
    app._audio_snapshot = None
    app._shutdown_started.set()

    def fail_refresh() -> None:
        raise AssertionError("shutdown polling attempted to refresh removed widgets")

    monkeypatch.setattr(app, "_refresh_view", fail_refresh)
    app._poll_audio_state()


def test_tui_rejects_repeated_record_requests_while_starting(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        transport = BlockingStartAudioTransport()
        recorder = PcmuWavRecorder(tmp_path / "queued-start.wav")
        session = AudioRecordingSession(AudioStream(transport), recorder)
        app = _app(session)

        async with app.run_test(size=(100, 46)) as pilot:
            await pilot.press("r")
            assert await asyncio.to_thread(transport.start_entered.wait, 1.0)

            await pilot.press("r")
            await pilot.pause()
            audio = _plain(app.query_one("#audio", Static))
            assert "Audio: STARTING" in audio
            assert "Audio operation already queued" in audio
            assert transport.start_calls == 1

            transport.release_start.set()
            await _wait_for_status(session, AudioSessionStatus.RECORDING)
            await pilot.press("r")
            await _wait_for_status(session, AudioSessionStatus.STOPPED)

        assert transport.start_calls == 1
        assert transport.stop_calls == 1
        assert not recorder.open
        assert not app.audio_thread_alive

    asyncio.run(exercise())


def test_tui_shutdown_coordinates_with_audio_start_in_progress(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        transport = BlockingStartAudioTransport()
        recorder = PcmuWavRecorder(tmp_path / "shutdown-during-start.wav")
        session = AudioRecordingSession(AudioStream(transport), recorder)
        app = _app(session)
        shutdown_entered = threading.Event()
        release_errors: list[Exception] = []
        original_stop_audio = app.stop_audio
        original_call_from_thread = app.call_from_thread
        dispatch_after_shutdown = threading.Event()

        def stop_audio() -> None:
            shutdown_entered.set()
            original_stop_audio()

        def call_from_thread(
            callback: Callable[..., object],
            *args: object,
            **kwargs: object,
        ) -> object:
            if shutdown_entered.is_set():
                dispatch_after_shutdown.set()
            return original_call_from_thread(callback, *args, **kwargs)

        def release_start() -> None:
            try:
                if not shutdown_entered.wait(1.0):
                    raise TimeoutError("TUI shutdown did not begin")
                transport.release_start.set()
            except Exception as error:
                release_errors.append(error)

        release_thread = threading.Thread(target=release_start)
        release_thread.start()
        app.stop_audio = stop_audio
        app.call_from_thread = call_from_thread

        async with app.run_test(size=(100, 46)) as pilot:
            await pilot.press("r")
            assert await asyncio.to_thread(transport.start_entered.wait, 1.0)

        release_thread.join(timeout=1.0)
        assert not release_thread.is_alive()
        assert release_errors == []
        assert not dispatch_after_shutdown.is_set()
        assert session.status is AudioSessionStatus.STOPPED
        assert transport.start_calls == 1
        assert transport.stop_calls == 1
        assert not recorder.open
        assert not app.audio_thread_alive

    asyncio.run(exercise())
