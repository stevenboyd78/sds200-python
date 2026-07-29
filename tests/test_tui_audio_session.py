from __future__ import annotations

import time
import wave
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sds200.audio import AudioChunk, AudioChunkHandler, AudioStream
from sds200.audio_sinks import PcmSinkStatistics
from sds200.tui_audio import (
    RecordingPathPolicy,
    SavedPlaybackStatus,
    TuiAudioSession,
)


class CountingAudioTransport:
    def __init__(self) -> None:
        self._handler: AudioChunkHandler | None = None
        self._running = False
        self.start_calls = 0
        self.stop_calls = 0

    @property
    def endpoint(self) -> str:
        return "audio://scanner"

    @property
    def running(self) -> bool:
        return self._running

    def start(self, handler: AudioChunkHandler) -> None:
        self._handler = handler
        self._running = True
        self.start_calls += 1

    def stop(self) -> None:
        self._handler = None
        self._running = False
        self.stop_calls += 1

    def feed(self, data: bytes) -> None:
        assert self._handler is not None
        self._handler(AudioChunk(data))


class CollectingPlaybackSink:
    def __init__(self) -> None:
        self._running = False
        self.received: list[bytes] = []
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
        total = sum(map(len, self.received))
        return PcmSinkStatistics(bytes_submitted=total, bytes_written=total)

    def start(self) -> None:
        self._running = True
        self.start_calls += 1

    def submit_pcm(self, data: bytes) -> None:
        assert self._running
        self.received.append(data)

    def stop(self) -> None:
        self._running = False
        self.stop_calls += 1


def _wait_for_saved_stop(session: TuiAudioSession) -> None:
    for _ in range(200):
        if session.saved_playback_status is SavedPlaybackStatus.STOPPED:
            return
        time.sleep(0.01)
    raise AssertionError("Saved recording did not finish playing")


def test_recording_path_policy_rejects_unsafe_templates(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="include only"):
        RecordingPathPolicy(directory=tmp_path, template="{channel}.wav")
    with pytest.raises(ValueError, match="include only"):
        RecordingPathPolicy(directory=tmp_path, template="static.wav")
    with pytest.raises(ValueError, match="file name"):
        RecordingPathPolicy(directory=tmp_path, template="nested/{timestamp}.wav")
    with pytest.raises(ValueError, match=r"\.wav"):
        RecordingPathPolicy(directory=tmp_path, template="{timestamp}.raw")


def test_tui_audio_starts_live_playback_and_records_repeatedly(tmp_path: Path) -> None:
    transport = CountingAudioTransport()
    playback = CollectingPlaybackSink()

    def now() -> datetime:
        return datetime(2026, 7, 29, 2, 55, 1, tzinfo=UTC)

    session = TuiAudioSession(
        AudioStream(transport),
        RecordingPathPolicy(directory=tmp_path),
        live_playback=True,
        playback_sink=playback,
        now=now,
    )

    session.open_audio()
    assert transport.start_calls == 1
    assert playback.running
    assert session.live_playback_active

    session.start()
    transport.feed(bytes((0xFF, 0x80)))
    session.stop()
    session.start()
    transport.feed(bytes((0x00, 0x7F)))
    session.stop()

    assert session.completed_recordings == 2
    assert {entry.path.name for entry in session.recordings} == {
        "sds200-20260729-025501.wav",
        "sds200-20260729-025501-2.wav",
    }
    assert len(playback.received) == 2
    for entry in session.recordings:
        with wave.open(str(entry.path), "rb") as recording:
            assert recording.getnchannels() == 1
            assert recording.getsampwidth() == 2
            assert recording.getframerate() == 8000
            assert recording.getnframes() == 2

    session.close()
    assert transport.stop_calls == 1
    assert not playback.running


def test_explicit_output_remains_one_shot_and_protected(tmp_path: Path) -> None:
    output = tmp_path / "explicit.wav"
    transport = CountingAudioTransport()
    session = TuiAudioSession(
        AudioStream(transport),
        RecordingPathPolicy(output=output),
    )

    session.open_audio()
    session.start()
    transport.feed(bytes((0xFF,)))
    session.stop()

    with pytest.raises(RuntimeError, match="already been used"):
        session.start()
    session.close()


def test_saved_playback_temporarily_replaces_and_restores_live_audio(
    tmp_path: Path,
) -> None:
    transport = CountingAudioTransport()
    playback = CollectingPlaybackSink()
    session = TuiAudioSession(
        AudioStream(transport),
        RecordingPathPolicy(directory=tmp_path),
        live_playback=True,
        playback_sink=playback,
    )

    session.open_audio()
    session.start()
    transport.feed(bytes((0xFF, 0x80, 0x00, 0x7F)))
    session.stop()
    entry = session.recordings[0]

    session.play_recording(entry.path)
    _wait_for_saved_stop(session)

    assert session.live_playback_enabled
    assert session.live_playback_active
    assert session.saved_playback_path == entry.path
    assert session.saved_playback_error is None
    assert playback.start_calls >= 2
    session.close()
