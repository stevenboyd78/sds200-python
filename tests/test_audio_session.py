from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sds200.audio import AudioChunk, AudioChunkHandler, AudioStream
from sds200.audio_recording import PCMU_SAMPLE_RATE, PcmuWavRecorder
from sds200.audio_session import AudioRecordingSession, AudioSessionStatus
from sds200.exceptions import ScannerConnectionError
from sds200.network_audio import NetworkAudioStatistics

from .fakes import BlockingStartAudioTransport, FakeAudioTransport


class FakeClock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


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


class FailingStartAudioTransport(FakeAudioTransport):
    def start(self, handler: AudioChunkHandler) -> None:
        del handler
        raise ScannerConnectionError("audio start failed")


class FailingStopAudioTransport(FakeAudioTransport):
    def stop(self) -> None:
        super().stop()
        raise ScannerConnectionError("audio stop failed")


def test_audio_recording_session_tracks_lifecycle_and_reliability(
    tmp_path: Path,
) -> None:
    transport = StatisticalFakeAudioTransport()
    recorder = PcmuWavRecorder(tmp_path / "audio.wav")
    clock = FakeClock(10.0)
    observed_at = datetime(2026, 7, 28, tzinfo=UTC)
    session = AudioRecordingSession(
        AudioStream(transport),
        recorder,
        clock=clock,
        now=lambda: observed_at,
    )
    statuses: list[AudioSessionStatus] = []
    session.on_state(lambda snapshot: statuses.append(snapshot.status))

    session.start()
    transport.feed(AudioChunk(bytes((0xFF, 0x80, 0x00, 0x7F))))
    clock.value = 12.5

    recording = session.snapshot()
    assert recording.status is AudioSessionStatus.RECORDING
    assert recording.active
    assert recording.elapsed_seconds == 2.5
    assert recording.packets == 1
    assert recording.samples == 4
    assert recording.audio_duration_seconds == 4 / PCMU_SAMPLE_RATE
    assert recording.reliability.packets_lost == 2
    assert recording.reliability.ssrc_mismatch_packets == 7
    assert recording.reliability.callback_errors == 10

    session.stop()

    stopped = session.snapshot()
    assert stopped.status is AudioSessionStatus.STOPPED
    assert not stopped.active
    assert stopped.elapsed_seconds == 2.5
    assert stopped.started_at == observed_at
    assert stopped.stopped_at == observed_at
    assert not recorder.open
    assert statuses == [
        AudioSessionStatus.STARTING,
        AudioSessionStatus.RECORDING,
        AudioSessionStatus.STOPPING,
        AudioSessionStatus.STOPPED,
    ]


def test_audio_recording_session_cleans_up_after_start_failure(
    tmp_path: Path,
) -> None:
    transport = FailingStartAudioTransport()
    recorder = PcmuWavRecorder(tmp_path / "failed.wav")
    session = AudioRecordingSession(AudioStream(transport), recorder)

    with pytest.raises(ScannerConnectionError, match="audio start failed"):
        session.start()

    snapshot = session.snapshot()
    assert snapshot.status is AudioSessionStatus.FAILED
    assert snapshot.error == "audio start failed"
    assert not transport.running
    assert not recorder.open
    session.stop()


def test_audio_recording_session_closes_recorder_after_stop_failure(
    tmp_path: Path,
) -> None:
    transport = FailingStopAudioTransport()
    recorder = PcmuWavRecorder(tmp_path / "failed-stop.wav")
    session = AudioRecordingSession(AudioStream(transport), recorder)
    session.start()

    with pytest.raises(ScannerConnectionError, match="audio stop failed"):
        session.stop()

    snapshot = session.snapshot()
    assert snapshot.status is AudioSessionStatus.FAILED
    assert snapshot.error == "audio stop failed"
    assert not transport.running
    assert not recorder.open


def test_audio_recording_session_serializes_stop_during_start(
    tmp_path: Path,
) -> None:
    transport = BlockingStartAudioTransport()
    recorder = PcmuWavRecorder(tmp_path / "concurrent-stop.wav")
    session = AudioRecordingSession(AudioStream(transport), recorder)
    start_errors: list[Exception] = []
    stop_errors: list[Exception] = []

    def start_session() -> None:
        try:
            session.start()
        except Exception as error:
            start_errors.append(error)

    def stop_session() -> None:
        try:
            session.stop()
        except Exception as error:
            stop_errors.append(error)

    start_thread = threading.Thread(target=start_session)
    stop_thread = threading.Thread(target=stop_session)
    start_thread.start()
    assert transport.start_entered.wait(1.0)

    stop_thread.start()
    assert stop_thread.is_alive()
    transport.release_start.set()

    start_thread.join(timeout=2.0)
    stop_thread.join(timeout=2.0)

    assert not start_thread.is_alive()
    assert not stop_thread.is_alive()
    assert start_errors == []
    assert stop_errors == []
    assert session.status is AudioSessionStatus.STOPPED
    assert transport.start_calls == 1
    assert transport.stop_calls == 1
    assert not transport.running
    assert not recorder.open
