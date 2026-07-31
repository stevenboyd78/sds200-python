from __future__ import annotations

import json
import threading
import time
import wave
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sds200.audio import AudioChunk, AudioChunkHandler, AudioStream
from sds200.audio_session import AudioSessionStatus
from sds200.audio_sinks import PcmSinkStatistics
from sds200.recording_metadata import recording_metadata_path
from sds200.state import RadioStateSnapshot
from sds200.tui_audio import (
    PcmSinkRouter,
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


class BlockingSink:
    def __init__(self) -> None:
        self._running = False
        self.submitting = threading.Event()
        self.release = threading.Event()
        self.stopped_during_submit = False

    @property
    def name(self) -> str:
        return "blocking:test"

    @property
    def running(self) -> bool:
        return self._running

    @property
    def statistics(self) -> PcmSinkStatistics:
        return PcmSinkStatistics()

    def start(self) -> None:
        self._running = True

    def submit_pcm(self, data: bytes) -> None:
        del data
        self.submitting.set()
        self.release.wait(timeout=1.0)

    def stop(self) -> None:
        self.stopped_during_submit = self.submitting.is_set() and not self.release.is_set()
        self._running = False


def _wait_for_saved_stop(session: TuiAudioSession) -> None:
    for _ in range(200):
        if session.saved_playback_status is SavedPlaybackStatus.STOPPED:
            return
        time.sleep(0.01)
    raise AssertionError("Saved recording did not finish playing")


def test_pcm_router_waits_for_in_flight_submission_before_stopping_sink() -> None:
    router = PcmSinkRouter()
    sink = BlockingSink()
    router.attach(sink)
    router.start()

    submit_thread = threading.Thread(target=router.submit_pcm, args=(bytes((0, 0)),))
    submit_thread.start()
    assert sink.submitting.wait(timeout=1.0)

    detach_thread = threading.Thread(target=router.detach, args=(sink,))
    detach_thread.start()
    time.sleep(0.02)
    assert detach_thread.is_alive()
    assert sink.running

    sink.release.set()
    submit_thread.join(timeout=1.0)
    detach_thread.join(timeout=1.0)

    assert not submit_thread.is_alive()
    assert not detach_thread.is_alive()
    assert not sink.stopped_during_submit
    assert not sink.running
    router.stop()


def test_recording_path_failure_transitions_session_to_failed(tmp_path: Path) -> None:
    invalid_directory = tmp_path / "not-a-directory"
    invalid_directory.write_text("occupied", encoding="utf-8")
    transport = CountingAudioTransport()
    session = TuiAudioSession(
        AudioStream(transport),
        RecordingPathPolicy(directory=invalid_directory),
    )

    session.open_audio()
    with pytest.raises(OSError):
        session.start()

    snapshot = session.snapshot()
    assert snapshot.status is AudioSessionStatus.FAILED
    assert not snapshot.active
    assert snapshot.error is not None
    session.close()


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
    assert not playback.running
    assert not session.live_playback_active

    session.start_live_playback()
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
    assert list(tmp_path.glob("*.json")) == []
    for entry in session.recordings:
        with wave.open(str(entry.path), "rb") as recording:
            assert recording.getnchannels() == 1
            assert recording.getsampwidth() == 2
            assert recording.getframerate() == 8000
            assert recording.getnframes() == 2

    session.close()
    assert transport.stop_calls == 1
    assert not playback.running


def test_tui_audio_writes_opt_in_metadata_with_boundary_state(
    tmp_path: Path,
) -> None:
    transport = CountingAudioTransport()
    observed_at = datetime(2026, 7, 30, 23, 45, tzinfo=UTC)
    session = TuiAudioSession(
        AudioStream(transport),
        RecordingPathPolicy(directory=tmp_path),
        metadata=True,
        scanner="SDS200",
        now=lambda: observed_at,
    )
    session.update_radio_state(
        RadioStateSnapshot(
            system="County",
            department="Fire",
            site="North",
            channel="Dispatch",
            frequency="154.1900",
        )
    )

    session.open_audio()
    session.start()
    transport.feed(bytes((0xFF, 0x80)))
    session.update_radio_state(
        RadioStateSnapshot(
            system="County",
            department="Fire",
            site="North",
            channel="Tac 1",
            frequency="154.2800",
            talkgroup_id="1201",
        )
    )
    session.stop()

    recording = session.recordings[0].path
    sidecar = recording_metadata_path(recording)
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert session.last_metadata_path == sidecar
    assert payload["source"] == {
        "endpoint": "audio://scanner",
        "scanner": "SDS200",
    }
    assert payload["boundaries"]["started"]["state"]["channel"] == "Dispatch"
    assert payload["boundaries"]["stopped"]["state"] == {
        "system": "County",
        "department": "Fire",
        "site": "North",
        "channel": "Tac 1",
        "frequency": "154.2800",
        "talkgroup_id": "1201",
    }
    assert payload["statistics"]["samples"] == 2
    assert session.completed_recordings == 1
    session.close()


def test_metadata_sidecar_collision_allocates_a_new_recording_name(
    tmp_path: Path,
) -> None:
    observed_at = datetime(2026, 7, 30, 23, 45, tzinfo=UTC)
    first = tmp_path / "sds200-20260730-234500.wav"
    recording_metadata_path(first).write_text("{}\n", encoding="utf-8")
    transport = CountingAudioTransport()
    session = TuiAudioSession(
        AudioStream(transport),
        RecordingPathPolicy(directory=tmp_path),
        metadata=True,
        scanner="SDS200",
        now=lambda: observed_at,
    )

    session.open_audio()
    session.start()
    transport.feed(bytes((0xFF,)))
    session.stop()

    assert session.recordings[0].path.name == "sds200-20260730-234500-2.wav"
    assert session.last_metadata_path == (
        tmp_path / "sds200-20260730-234500-2.wav.json"
    )
    session.close()


def test_live_playback_toggle_keeps_prepared_sink_running(tmp_path: Path) -> None:
    transport = CountingAudioTransport()
    playback = CollectingPlaybackSink()
    session = TuiAudioSession(
        AudioStream(transport),
        RecordingPathPolicy(directory=tmp_path),
        playback_sink=playback,
    )

    session.open_audio()
    session.start_live_playback()
    assert playback.running
    assert session.live_playback_active

    session.toggle_live_playback()
    assert playback.running
    assert not session.live_playback_active
    assert not session.live_playback_enabled
    assert playback.stop_calls == 0

    session.start_live_playback()
    assert playback.running
    assert session.live_playback_active
    assert playback.stop_calls == 0

    session.close()
    assert not playback.running
    assert playback.stop_calls == 1


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
