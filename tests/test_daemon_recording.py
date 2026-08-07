from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import sds200.daemon_recording as daemon_recording
from sds200.audio import AudioChunk, AudioStream
from sds200.audio_recording import PcmuWavRecorder
from sds200.audio_session import AudioSessionStatus
from sds200.audio_sinks import PcmSink, PcmSinkRouter, PcmSinkStatistics
from sds200.daemon_recording import (
    DaemonRecordingBusyError,
    DaemonRecordingManager,
    DaemonRecordingOperationError,
    DaemonRecordingUnavailableError,
)
from sds200.network_audio import NetworkAudioStatistics
from sds200.recording_metadata import recording_metadata_path
from sds200.recording_organization import RecordingOrganizationPolicy
from sds200.state import RadioStateSnapshot


class FakeClock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class FakeWallClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class StatisticalFakeTransport:
    def __init__(self) -> None:
        self.endpoint = "rtsp://192.0.2.25/AU:scanner.au"
        self.running = True
        self.statistics = NetworkAudioStatistics(
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

    def start(self, handler: Callable[[AudioChunk], None]) -> None:
        del handler
        self.running = True

    def stop(self) -> None:
        self.running = False


@dataclass
class FakeRadioState:
    snapshot: RadioStateSnapshot


class FakeScanner:
    def __init__(self) -> None:
        self.state = FakeRadioState(
            RadioStateSnapshot(
                system="County",
                department="Fire",
                site="North",
                channel="Dispatch",
                frequency="154.1900",
            )
        )


class FakeAudio:
    def __init__(self) -> None:
        self.stream = AudioStream(StatisticalFakeTransport())


class FlakyFinalizationSink:
    """Test sink that fails its first explicit finalization attempt."""

    def __init__(
        self,
        recorder: PcmuWavRecorder,
        *,
        buffer_seconds: float = 5.0,
    ) -> None:
        del buffer_seconds
        self.recorder = recorder
        self._running = False
        self._statistics = PcmSinkStatistics()
        self.stop_attempts = 0

    @property
    def name(self) -> str:
        return "flaky-finalization"

    @property
    def running(self) -> bool:
        return self._running

    @property
    def statistics(self) -> PcmSinkStatistics:
        return self._statistics

    def start(self) -> None:
        self.recorder.start()
        self._running = True

    def submit_pcm(self, data: bytes) -> None:
        self.recorder.write_pcm(data)
        self._statistics = PcmSinkStatistics(
            bytes_submitted=self._statistics.bytes_submitted + len(data),
            bytes_written=self._statistics.bytes_written + len(data),
        )

    def stop(self) -> None:
        self.stop_attempts += 1
        if self.stop_attempts == 1:
            raise OSError("sensitive finalization failure")
        self.recorder.close()
        self._running = False


class FakeRuntime:
    def __init__(self) -> None:
        self.running = True
        self.scanner = FakeScanner()
        self.audio = FakeAudio()
        self.router = PcmSinkRouter(name="test-daemon-recording")
        self.router.start()
        self.attach_calls = 0
        self.detach_calls = 0
        self.attach_error: BaseException | None = None

    def attach_sink(self, sink: PcmSink) -> None:
        self.attach_calls += 1
        if self.attach_error is not None:
            raise self.attach_error
        self.router.attach(sink)

    def detach_sink(self, sink: PcmSink, *, stop: bool = True) -> None:
        self.detach_calls += 1
        self.router.detach(sink, stop=stop)

    def close(self) -> None:
        self.router.stop()


def test_daemon_recording_records_and_finalizes_metadata(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime()
    clock = FakeClock()
    wall_clock = FakeWallClock(datetime(2026, 8, 7, 19, 30, tzinfo=UTC))
    manager = DaemonRecordingManager(
        runtime,
        tmp_path,
        scanner="SDS200",
        clock=clock,
        now=wall_clock,
    )
    transitions: list[AudioSessionStatus] = []
    unsubscribe = manager.on_state(
        lambda snapshot: transitions.append(snapshot.status)
    )

    try:
        started = manager.start_recording()
        assert started.status is AudioSessionStatus.RECORDING
        assert started.recording_path is not None

        runtime.router.submit_pcm(b"\x00\x00" * 4)
        runtime.scanner.state.snapshot = RadioStateSnapshot(
            system="County",
            department="Fire",
            site="North",
            channel="Tac 1",
            frequency="154.2800",
        )
        clock.value += 2.5
        wall_clock.value += timedelta(seconds=3)

        stopped = manager.stop_recording()

        assert stopped.status is AudioSessionStatus.STOPPED
        assert not stopped.active
        assert stopped.elapsed_seconds == 2.5
        assert stopped.packets == 1
        assert stopped.samples == 4
        assert stopped.audio_duration_seconds == pytest.approx(4 / 8000)
        assert stopped.completed_recordings == 1
        assert stopped.recording_path is not None
        assert stopped.recording_path.exists()
        assert stopped.metadata_path == recording_metadata_path(
            stopped.recording_path
        )
        assert stopped.metadata_path.exists()
        assert stopped.sink_statistics.bytes_written == 8
        assert stopped.sink_statistics.bytes_dropped == 0
        assert stopped.reliability.as_dict() == {
            "packets_lost": 2,
            "duplicate_packets": 3,
            "late_packets": 4,
            "malformed_packets": 5,
            "unexpected_source_packets": 6,
            "ssrc_mismatch_packets": 7,
            "timestamp_discontinuities": 8,
            "receive_errors": 9,
            "callback_errors": 10,
        }

        payload = json.loads(
            stopped.metadata_path.read_text(encoding="utf-8")
        )
        assert payload["source"] == {
            "endpoint": "rtsp://192.0.2.25/AU:scanner.au",
            "scanner": "SDS200",
        }
        assert (
            payload["boundaries"]["started"]["state"]["channel"]
            == "Dispatch"
        )
        assert (
            payload["boundaries"]["stopped"]["state"]["channel"]
            == "Tac 1"
        )
        assert payload["statistics"]["packets"] == 1
        assert payload["statistics"]["samples"] == 4

        assert transitions == [
            AudioSessionStatus.STARTING,
            AudioSessionStatus.RECORDING,
            AudioSessionStatus.STOPPING,
            AudioSessionStatus.STOPPED,
        ]
        assert runtime.attach_calls == 1
        assert runtime.detach_calls == 1
        assert runtime.audio.stream.running
    finally:
        unsubscribe()
        manager.close()
        runtime.close()


def test_daemon_recording_repeats_with_collision_safe_paths(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime()
    wall_clock = FakeWallClock(datetime(2026, 8, 7, 19, 30, tzinfo=UTC))
    manager = DaemonRecordingManager(
        runtime,
        tmp_path,
        now=wall_clock,
    )

    try:
        first = manager.start_recording()
        runtime.router.submit_pcm(b"\x00\x00")
        first_stopped = manager.stop_recording()

        second = manager.start_recording()
        runtime.router.submit_pcm(b"\x00\x00")
        second_stopped = manager.stop_recording()

        assert first.recording_path is not None
        assert second.recording_path is not None
        assert first_stopped.recording_path is not None
        assert second_stopped.recording_path is not None
        assert first.recording_path.name == "sds200-20260807-193000.wav"
        assert second.recording_path.name == (
            "sds200-20260807-193000-2.wav"
        )
        assert second_stopped.completed_recordings == 2
        assert len(tuple(tmp_path.glob("*.wav"))) == 2
        assert len(tuple(tmp_path.glob("*.wav.json"))) == 2
    finally:
        manager.close()
        runtime.close()


def test_daemon_recording_organizes_from_start_boundary(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime()
    wall_clock = FakeWallClock(datetime(2026, 8, 7, 19, 30, tzinfo=UTC))
    manager = DaemonRecordingManager(
        runtime,
        tmp_path,
        scanner="SDS/200",
        organization=RecordingOrganizationPolicy.from_csv(
            "scanner,date,system,channel"
        ),
        now=wall_clock,
    )

    try:
        started = manager.start_recording()
        assert started.recording_path == (
            tmp_path
            / "SDS-200"
            / "2026-08-07"
            / "County"
            / "Dispatch"
            / "sds200-20260807-193000.wav"
        )

        runtime.scanner.state.snapshot = RadioStateSnapshot(
            system="Changed",
            channel="Tac 2",
        )
        runtime.router.submit_pcm(b"\x00\x00")
        stopped = manager.stop_recording()

        assert stopped.recording_path == started.recording_path
        assert stopped.metadata_path == recording_metadata_path(
            started.recording_path
        )
    finally:
        manager.close()
        runtime.close()


def test_daemon_recording_rejects_busy_and_unavailable_starts(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime()
    manager = DaemonRecordingManager(runtime, tmp_path)

    try:
        manager.start_recording()
        with pytest.raises(DaemonRecordingBusyError):
            manager.start_recording()
        manager.stop_recording()

        runtime.running = False
        with pytest.raises(DaemonRecordingUnavailableError):
            manager.start_recording()
    finally:
        manager.close()
        runtime.close()


def test_daemon_recording_close_finalizes_active_recording(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime()
    manager = DaemonRecordingManager(runtime, tmp_path)

    try:
        started = manager.start_recording()
        assert started.recording_path is not None
        runtime.router.submit_pcm(b"\x00\x00" * 3)

        manager.close()

        stopped = manager.snapshot()
        assert stopped.closed
        assert stopped.status is AudioSessionStatus.STOPPED
        assert stopped.samples == 3
        assert stopped.recording_path is not None
        assert stopped.recording_path.exists()
        assert stopped.metadata_path is not None
        assert stopped.metadata_path.exists()

        with pytest.raises(DaemonRecordingUnavailableError):
            manager.start_recording()
    finally:
        runtime.close()


def test_daemon_recording_finalization_failure_is_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = FakeRuntime()
    monkeypatch.setattr(
        daemon_recording,
        "PcmWavSink",
        FlakyFinalizationSink,
    )
    manager = DaemonRecordingManager(runtime, tmp_path)

    try:
        started = manager.start_recording()
        assert started.recording_path is not None
        runtime.router.submit_pcm(b"\x00\x00" * 2)

        with pytest.raises(
            DaemonRecordingOperationError,
            match="Could not finalize daemon recording",
        ):
            manager.stop_recording()

        failed = manager.snapshot()
        assert failed.status is AudioSessionStatus.FAILED
        assert failed.error == "OSError"
        assert failed.completed_recordings == 0
        assert failed.recording_path == started.recording_path
        assert failed.metadata_path is None

        with pytest.raises(DaemonRecordingBusyError):
            manager.start_recording()

        recovered = manager.stop_recording()
        assert recovered.status is AudioSessionStatus.STOPPED
        assert recovered.error is None
        assert recovered.samples == 2
        assert recovered.completed_recordings == 1
        assert recovered.metadata_path is not None
        assert recovered.metadata_path.exists()
    finally:
        manager.close()
        runtime.close()


def test_daemon_recording_start_failure_is_redacted_and_recoverable(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime()
    runtime.attach_error = OSError(
        f"sensitive failure under {tmp_path}"
    )
    manager = DaemonRecordingManager(runtime, tmp_path)

    try:
        with pytest.raises(
            DaemonRecordingOperationError,
            match="Could not start daemon recording",
        ):
            manager.start_recording()

        failed = manager.snapshot()
        assert failed.status is AudioSessionStatus.FAILED
        assert failed.error == "OSError"
        assert str(tmp_path) not in str(failed.as_dict()["error"])
        assert failed.completed_recordings == 0

        runtime.attach_error = None
        started = manager.start_recording()
        assert started.status is AudioSessionStatus.RECORDING
        runtime.router.submit_pcm(b"\x00\x00")
        stopped = manager.stop_recording()
        assert stopped.status is AudioSessionStatus.STOPPED
        assert stopped.completed_recordings == 1
    finally:
        manager.close()
        runtime.close()

def test_daemon_recording_inventory_is_bounded_newest_first_and_excludes_active(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime()
    wall_clock = FakeWallClock(datetime(2026, 8, 7, 19, 30, tzinfo=UTC))
    manager = DaemonRecordingManager(
        runtime,
        tmp_path / "recordings",
        now=wall_clock,
    )

    try:
        empty = manager.list_recordings(limit=2)
        assert empty.entries == ()
        assert empty.total_entries == 0
        assert empty.summary.managed_units == 0

        for _ in range(2):
            manager.start_recording()
            runtime.router.submit_pcm(b"\x00\x00")
            manager.stop_recording()
            wall_clock.value += timedelta(seconds=1)

        active = manager.start_recording()
        assert active.recording_path is not None
        runtime.router.submit_pcm(b"\x00\x00")

        while_active = manager.list_recordings(limit=1)
        assert while_active.total_entries == 2
        assert len(while_active.entries) == 1
        assert while_active.summary.managed_units == 2
        assert (
            while_active.entries[0].relative_audio_path.name
            == "sds200-20260807-193001.wav"
        )
        assert all(
            entry.audio_path != active.recording_path
            for entry in while_active.entries
        )

        manager.stop_recording()

        completed = manager.list_recordings(limit=2)
        assert completed.limit == 2
        assert completed.total_entries == 3
        assert completed.summary.managed_units == 3
        assert len(completed.entries) == 2
        assert [
            entry.relative_audio_path.name
            for entry in completed.entries
        ] == [
            "sds200-20260807-193002.wav",
            "sds200-20260807-193001.wav",
        ]
        payload = completed.as_dict()
        assert payload["total_entries"] == 3
        assert "root" not in payload
    finally:
        manager.close()
        runtime.close()


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_daemon_recording_inventory_rejects_invalid_limit(
    tmp_path: Path,
    limit: object,
) -> None:
    runtime = FakeRuntime()
    manager = DaemonRecordingManager(runtime, tmp_path)

    try:
        with pytest.raises((TypeError, ValueError)):
            manager.list_recordings(limit=limit)  # type: ignore[arg-type]
    finally:
        manager.close()
        runtime.close()
