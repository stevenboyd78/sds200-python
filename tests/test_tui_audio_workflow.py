from __future__ import annotations

import asyncio
from pathlib import Path

from rich.text import Text
from textual.widgets import Static

from sds200.audio import AudioChunk, AudioStream
from sds200.audio_session import AudioSessionStatus
from sds200.tui import ScannerIdentity, ScannerTuiApp
from sds200.tui_audio import RecordingPathPolicy, TuiAudioSession
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
            ScannerInfoParser().parse("GSI", XML),
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
