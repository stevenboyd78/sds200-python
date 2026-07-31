#!/usr/bin/env python3
"""Generate deterministic documentation screenshots from the real Textual TUI."""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
import wave
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from sds200.audio import AudioChunk, AudioChunkHandler, AudioStream
from sds200.audio_session import AudioSessionStatus
from sds200.audio_sinks import PcmSinkStatistics
from sds200.state import snapshot_from_scanner_info
from sds200.tui import ScannerIdentity, ScannerTuiApp
from sds200.tui_audio import RecordingPathPolicy, TuiAudioSession
from sds200.tui_logging import TuiLogBuffer
from sds200.xml_protocol import ScannerInfoParser

SAMPLE_RATE = 8_000
SAMPLE_WIDTH = 2
CHANNELS = 1

FIXED_NOW = datetime(2026, 7, 30, 23, 15, tzinfo=UTC)
_TERMINAL_NAMESPACE_PATTERN = re.compile(r"terminal-\d+")
_CLOCK_TEXT_PATTERN = re.compile(
    r"(<text\b[^>]*>)(\d{2}:\d{2}:\d{2})(</text>)"
)

DEMO_XML = """<?xml version="1.0" encoding="utf-8"?>
<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">
<System Name="Demo County P25" />
<Department Name="Public Safety" />
<Site Name="Central Simulcast" Mod="NFM" />
<TGID Name="Fire Dispatch 1" TGID="TGID:1201" SvcType="Dispatch" />
<SiteFrequency Freq="769.456250MHz" />
<Property VOL="8" SQL="2" Sig="5" Rssi="-72" Rec="Off" Mute="Unmute" />
</ScannerInfo>
"""


class DemoAudioTransport:
    """In-memory audio transport used only by the screenshot generator."""

    def __init__(self) -> None:
        self._running = False
        self._handler: AudioChunkHandler | None = None

    @property
    def endpoint(self) -> str:
        return "demo-audio://fictional-sds200"

    @property
    def running(self) -> bool:
        return self._running

    def start(self, handler: AudioChunkHandler) -> None:
        self._handler = handler
        self._running = True

    def stop(self) -> None:
        self._running = False

    def feed(self, chunk: AudioChunk) -> None:
        if self._handler is None:
            raise RuntimeError("Demo audio transport has not started")
        self._handler(chunk)


class DemoPlaybackSink:
    """No-device playback sink that reports realistic lifecycle state."""

    def __init__(self) -> None:
        self._running = False
        self._muted = True
        self._bytes_submitted = 0

    @property
    def name(self) -> str:
        return "playback:demo-device"

    @property
    def running(self) -> bool:
        return self._running

    @property
    def statistics(self) -> PcmSinkStatistics:
        return PcmSinkStatistics(
            bytes_submitted=self._bytes_submitted,
            bytes_written=self._bytes_submitted,
        )

    def start(self) -> None:
        self._running = True

    def submit_pcm(self, data: bytes) -> None:
        if self._running and not self._muted:
            self._bytes_submitted += len(data)

    def stop(self) -> None:
        self._running = False

    def set_muted(self, muted: bool) -> None:
        self._muted = muted


@contextmanager
def working_directory(path: Path) -> Iterator[None]:
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def normalize_svg(svg: str, *, namespace: str) -> str:
    """Normalize nondeterministic Rich and Textual screenshot fields."""

    normalized, namespace_replacements = _TERMINAL_NAMESPACE_PATTERN.subn(
        f"terminal-{namespace}",
        svg,
    )
    if namespace_replacements == 0:
        raise RuntimeError("Textual screenshot did not contain a terminal namespace")

    fixed_clock = FIXED_NOW.strftime("%H:%M:%S")
    normalized, clock_replacements = _CLOCK_TEXT_PATTERN.subn(
        lambda match: f"{match.group(1)}{fixed_clock}{match.group(3)}",
        normalized,
    )
    if clock_replacements != 1:
        raise RuntimeError(
            "Expected exactly one Textual header clock, "
            f"found {clock_replacements}"
        )

    normalized = "\n".join(
        line.rstrip() for line in normalized.splitlines()
    )
    return f"{normalized}\n"


def write_demo_wav(
    path: Path,
    *,
    duration_seconds: int,
    modified_at: datetime,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame_count = SAMPLE_RATE * duration_seconds

    with wave.open(str(path), "wb") as output:
        output.setnchannels(CHANNELS)
        output.setsampwidth(SAMPLE_WIDTH)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(b"\x00\x00" * frame_count)

    timestamp = modified_at.timestamp()
    os.utime(path, (timestamp, timestamp))


def create_demo_recordings(directory: Path) -> None:
    recordings = (
        (
            "demo-fire-dispatch-20260730-225900.wav",
            22,
            datetime(2026, 7, 30, 22, 59, tzinfo=UTC),
        ),
        (
            "demo-public-works-20260730-224400.wav",
            13,
            datetime(2026, 7, 30, 22, 44, tzinfo=UTC),
        ),
        (
            "demo-mutual-aid-20260730-221800.wav",
            8,
            datetime(2026, 7, 30, 22, 18, tzinfo=UTC),
        ),
    )

    for name, duration, modified_at in recordings:
        write_demo_wav(
            directory / name,
            duration_seconds=duration,
            modified_at=modified_at,
        )


def create_log_buffer() -> TuiLogBuffer:
    buffer = TuiLogBuffer()
    for line in (
        "2026-07-30 23:14:54 INFO sds200.radio: Connected to demonstration scanner",
        "2026-07-30 23:14:55 INFO sds200.psi: Live PSI stream started at 500 ms",
        "2026-07-30 23:14:56 INFO sds200.audio: Shared demo audio stream ready",
        "2026-07-30 23:14:57 INFO sds200.tui_audio: Live playback enabled",
        "2026-07-30 23:14:58 INFO sds200.tui_audio: Recording library contains 3 files",
        "2026-07-30 23:14:59 INFO sds200.tui: Demonstration data loaded",
    ):
        buffer.append(line)
    return buffer


async def wait_until(
    predicate: Callable[[], bool],
    description: str,
) -> None:
    for _ in range(400):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise RuntimeError(f"Timed out waiting for {description}")


async def capture(
    output: Path,
    *,
    size: tuple[int, int],
    show_library: bool = False,
    active_recording: bool = False,
) -> None:
    info = ScannerInfoParser().parse("GSI", DEMO_XML)
    transport = DemoAudioTransport()
    playback = DemoPlaybackSink()
    session_clock = [100.0]

    session = TuiAudioSession(
        AudioStream(transport),
        RecordingPathPolicy(
            directory=Path("demo-recordings"),
            template="demo-{timestamp}.wav",
        ),
        live_playback=True,
        playback_sink=playback,
        history_limit=10,
        scanner="Fictional SDS200",
        clock=lambda: session_clock[0],
        now=lambda: FIXED_NOW,
    )

    app = ScannerTuiApp(
        ScannerIdentity(
            endpoint="demo://fictional-sds200",
            model="SDS200",
            firmware="Version 1.26.01 (demo)",
        ),
        info,
        audio_session=session,
        log_buffer=create_log_buffer(),
        connected=True,
        stale_after=3_600.0,
        psi_auto_recover=False,
        clock=lambda: 100.0,
        now=lambda: FIXED_NOW,
    )

    async with app.run_test(size=size) as pilot:
        await wait_until(
            lambda: session.open and not app._audio_pending,
            "demo audio stream startup",
        )

        # Use the same state-application path exercised by the TUI test suite.
        app._apply_radio_state(snapshot_from_scanner_info(info))

        await wait_until(
            lambda: session.live_playback_active and not app._audio_pending,
            "demo live playback activation",
        )

        if active_recording:
            await pilot.press("r")
            await wait_until(
                lambda: session.status is AudioSessionStatus.RECORDING,
                "demo recording startup",
            )

            for _ in range(300):
                transport.feed(AudioChunk(b"\xff" * 160))

            session_clock[0] = 106.0
            await pilot.pause()

        if show_library:
            await pilot.press("l")
            await pilot.pause()
            await pilot.press("down")
            await pilot.pause()

        await asyncio.sleep(0.35)

        await pilot.pause()

        svg = app.export_screenshot(
            title="sdsctl TUI — Demonstration data",
            simplify=True,
        )
        output.write_text(
            normalize_svg(svg, namespace=output.stem),
            encoding="utf-8",
        )


async def generate() -> None:
    repository = Path(__file__).resolve().parents[1]
    output_directory = repository / "docs" / "assets" / "screenshots"
    output_directory.mkdir(parents=True, exist_ok=True)

    captures = (
        ("tui-overview.svg", (120, 40), False, True),
        ("tui-recordings.svg", (100, 50), True, False),
        ("tui-compact.svg", (79, 24), False, False),
    )

    with tempfile.TemporaryDirectory(prefix="sdsctl-tui-demo-") as temporary:
        temporary_root = Path(temporary)

        for filename, size, show_library, active_recording in captures:
            work_directory = temporary_root / Path(filename).stem
            work_directory.mkdir()
            create_demo_recordings(work_directory / "demo-recordings")

            with working_directory(work_directory):
                output = output_directory / filename
                await capture(
                    output,
                    size=size,
                    show_library=show_library,
                    active_recording=active_recording,
                )
                print(f"Generated {output.relative_to(repository)}")


def main() -> None:
    asyncio.run(generate())


if __name__ == "__main__":
    main()
