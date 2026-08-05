from __future__ import annotations

import struct
import wave
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sds200.audio import AudioChunk, AudioChunkHandler, AudioStream
from sds200.audio_recording import PCM_SAMPLE_WIDTH, PCMU_SAMPLE_RATE, PcmuWavRecorder
from sds200.audio_sinks import (
    AudioFanoutSession,
    AudioFanoutSnapshot,
    BufferedPlaybackSink,
    LocalPlaybackAdapter,
    PcmSinkRouter,
    PcmSinkStatistics,
    PcmSubscriberTransition,
    PcmWavSink,
    SoundDevicePlaybackSink,
    inspect_audio_backend,
)
from sds200.exceptions import AudioOutputError


class FakeAudioTransport:
    def __init__(self) -> None:
        self._handler: AudioChunkHandler | None = None
        self._running = False

    @property
    def endpoint(self) -> str:
        return "fake://audio"

    @property
    def running(self) -> bool:
        return self._running

    def start(self, handler: AudioChunkHandler) -> None:
        self._handler = handler
        self._running = True

    def stop(self) -> None:
        self._running = False
        self._handler = None

    def feed(self, chunk: AudioChunk) -> None:
        assert self._handler is not None
        self._handler(chunk)


class CollectingSink:
    def __init__(self, name: str) -> None:
        self._name = name
        self._running = False
        self.received: list[bytes] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def running(self) -> bool:
        return self._running

    @property
    def statistics(self) -> PcmSinkStatistics:
        total = sum(map(len, self.received))
        return PcmSinkStatistics(bytes_submitted=total, bytes_written=total)

    def start(self) -> None:
        self._running = True

    def submit_pcm(self, data: bytes) -> None:
        assert self._running
        self.received.append(data)

    def stop(self) -> None:
        self._running = False


class FakeRawOutputStream:
    def __init__(self, callback: Callable[[object, int, object, object], None]) -> None:
        self.callback = callback
        self.started = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def close(self) -> None:
        self.closed = True


class FakePlaybackAdapter:
    def __init__(self) -> None:
        self._running = False
        self.reader: Callable[[int], bytes] | None = None
        self.status_reporter: Callable[[bool], None] | None = None
        self.interrupt_calls = 0
        self.close_calls = 0

    @property
    def name(self) -> str:
        return "fake-playback"

    @property
    def running(self) -> bool:
        return self._running

    def start(
        self,
        pcm_reader: Callable[[int], bytes],
        status_reporter: Callable[[bool], None],
    ) -> None:
        self.reader = pcm_reader
        self.status_reporter = status_reporter
        self._running = True

    def interrupt(self) -> None:
        self.interrupt_calls += 1
        self._running = False

    def close(self) -> None:
        self.close_calls += 1
        self._running = False


class FakeInputOutputPair:
    def __getitem__(self, index: int) -> int:
        return (0, 2)[index]


class FakeSoundDeviceDefaults:
    device = FakeInputOutputPair()


class FakeSoundDeviceModule:
    def __init__(self) -> None:
        self.default = FakeSoundDeviceDefaults()
        self.stream: FakeRawOutputStream | None = None
        self.arguments: dict[str, object] = {}

    def RawOutputStream(self, **kwargs: object) -> FakeRawOutputStream:
        self.arguments = kwargs
        callback = kwargs["callback"]
        assert callable(callback)
        self.stream = FakeRawOutputStream(callback)
        return self.stream

    def get_portaudio_version(self) -> tuple[int, str]:
        return (1246720, "PortAudio V19.7.0")

    def query_hostapis(self) -> tuple[dict[str, object], ...]:
        return (
            {
                "name": "ALSA",
                "default_input_device": 0,
                "default_output_device": 2,
            },
            {
                "name": "JACK Audio Connection Kit",
                "default_input_device": -1,
                "default_output_device": -1,
            },
        )

    def query_devices(self) -> tuple[dict[str, object], ...]:
        return (
            {
                "name": "Input only",
                "index": 0,
                "hostapi": 0,
                "max_input_channels": 1,
                "max_output_channels": 0,
                "default_samplerate": 48000.0,
            },
            {
                "name": "HDMI",
                "index": 2,
                "hostapi": 0,
                "max_input_channels": 0,
                "max_output_channels": 2,
                "default_samplerate": 48000.0,
            },
            {
                "name": "USB Audio",
                "index": 4,
                "hostapi": 1,
                "max_input_channels": 0,
                "max_output_channels": 2,
                "default_samplerate": 44100.0,
            },
        )


def test_audio_fanout_decodes_once_for_multiple_sinks() -> None:
    transport = FakeAudioTransport()
    first = CollectingSink("first")
    second = CollectingSink("second")
    session = AudioFanoutSession(AudioStream(transport), (first, second))

    with session:
        transport.feed(AudioChunk(bytes((0xFF, 0x80, 0x00, 0x7F))))

    expected = struct.pack("<4h", 0, 32124, -32124, 0)
    assert first.received == [expected]
    assert second.received == [expected]
    snapshot = session.snapshot()
    assert snapshot.packets == 1
    assert snapshot.samples == 4
    assert snapshot.audio_duration_seconds == 4 / PCMU_SAMPLE_RATE
    assert not snapshot.running


def test_audio_fanout_emits_state_after_start_and_stop() -> None:
    transport = FakeAudioTransport()
    sink = CollectingSink("collector")
    session = AudioFanoutSession(AudioStream(transport), (sink,))
    observed: list[AudioFanoutSnapshot] = []

    unsubscribe = session.on_state(observed.append)

    session.start()
    transport.feed(AudioChunk(b"\xff"))
    session.stop()
    unsubscribe()

    assert [snapshot.running for snapshot in observed] == [True, False]
    assert observed[0].packets == 0
    assert observed[1].packets == 1
    assert observed[1].samples == 1


def test_audio_fanout_unsubscribe_and_repeated_stop_emit_nothing_more() -> None:
    transport = FakeAudioTransport()
    sink = CollectingSink("collector")
    session = AudioFanoutSession(AudioStream(transport), (sink,))
    observed: list[AudioFanoutSnapshot] = []

    unsubscribe = session.on_state(observed.append)
    session.start()
    unsubscribe()

    session.stop()
    session.stop()

    assert [snapshot.running for snapshot in observed] == [True]


def test_audio_fanout_state_listener_failures_are_isolated() -> None:
    transport = FakeAudioTransport()
    sink = CollectingSink("collector")
    session = AudioFanoutSession(AudioStream(transport), (sink,))
    observed: list[AudioFanoutSnapshot] = []

    def fail_listener(snapshot: AudioFanoutSnapshot) -> None:
        del snapshot
        raise RuntimeError("listener failed")

    session.on_state(fail_listener)
    session.on_state(observed.append)

    session.start()
    session.stop()

    assert [snapshot.running for snapshot in observed] == [True, False]


def test_audio_fanout_emits_stopped_state_after_start_failure() -> None:
    class FailingStartSink(CollectingSink):
        def start(self) -> None:
            raise RuntimeError("audio sink start failed")

    transport = FakeAudioTransport()
    sink = FailingStartSink("failing")
    session = AudioFanoutSession(AudioStream(transport), (sink,))
    observed: list[AudioFanoutSnapshot] = []
    session.on_state(observed.append)

    with pytest.raises(RuntimeError, match="audio sink start failed"):
        session.start()

    assert len(observed) == 1
    assert not observed[0].running
    assert observed[0].packets == 0
    assert observed[0].samples == 0


def test_buffered_playback_sink_uses_renderer_neutral_adapter() -> None:
    adapter = FakePlaybackAdapter()
    sink = BufferedPlaybackSink(
        name="playback:test",
        buffer_ms=1,
        adapter_factory=lambda: adapter,
    )

    assert isinstance(adapter, LocalPlaybackAdapter)
    sink.start()
    assert sink.running
    assert adapter.reader is not None
    assert adapter.status_reporter is not None

    pcm = bytes(range(32))
    sink.submit_pcm(pcm)
    assert adapter.reader(16) == pcm[-16:]
    adapter.status_reporter(True)

    statistics = sink.statistics
    assert statistics.bytes_submitted == 32
    assert statistics.bytes_written == 16
    assert statistics.bytes_dropped == 16
    assert statistics.overflows == 1
    assert statistics.callback_statuses == 1

    sink.set_muted(True)
    assert adapter.reader(16) == bytes(16)
    assert sink.statistics.underflows == 0

    sink.stop()
    assert adapter.interrupt_calls == 1
    assert adapter.close_calls == 1
    assert not sink.running


def test_buffered_playback_sink_rejects_invalid_adapter_factory() -> None:
    sink = BufferedPlaybackSink(
        name="playback:test",
        adapter_factory=lambda: object(),  # type: ignore[arg-type]
    )

    with pytest.raises(TypeError, match="LocalPlaybackAdapter-compatible"):
        sink.start()


def test_sounddevice_playback_uses_nonblocking_bounded_buffer() -> None:
    module = FakeSoundDeviceModule()
    sink = SoundDevicePlaybackSink(
        buffer_ms=1,
        module_loader=lambda name: module,
    )
    sink.start()
    assert module.stream is not None
    assert module.arguments["samplerate"] == PCMU_SAMPLE_RATE
    assert module.arguments["channels"] == 1
    assert module.arguments["dtype"] == "int16"

    pcm = bytes(range(32))
    sink.submit_pcm(pcm)
    output = bytearray(16)
    module.stream.callback(output, 8, object(), object())

    assert output == pcm[-16:]
    statistics = sink.statistics
    assert statistics.bytes_submitted == 32
    assert statistics.bytes_written == 16
    assert statistics.bytes_dropped == 16
    assert statistics.overflows == 1
    assert statistics.callback_statuses == 1

    underflow = bytearray(16)
    module.stream.callback(underflow, 8, object(), False)
    assert underflow == bytes(16)
    assert sink.statistics.underflows == 1

    sink.set_muted(True)
    sink.submit_pcm(bytes(range(16)))
    muted = bytearray(16)
    module.stream.callback(muted, 8, object(), object())
    assert muted == bytes(16)
    statistics = sink.statistics
    assert statistics.bytes_submitted == 32
    assert statistics.queued_bytes == 0
    assert statistics.underflows == 1
    assert statistics.callback_statuses == 2
    assert sink.muted

    sink.set_muted(False)
    assert not sink.muted
    sink.interrupt()
    assert not sink.running
    sink.stop()
    assert module.stream.closed
    assert not sink.running


def test_sounddevice_playback_reports_missing_optional_dependency() -> None:
    def missing(name: str) -> object:
        raise ModuleNotFoundError(name=name)

    sink = SoundDevicePlaybackSink(module_loader=missing)
    with pytest.raises(AudioOutputError, match=r"sds200\[playback\]"):
        sink.start()


def test_sounddevice_playback_reports_missing_portaudio_runtime() -> None:
    def missing_portaudio(name: str) -> object:
        del name
        raise OSError("PortAudio library not found")

    sink = SoundDevicePlaybackSink(module_loader=missing_portaudio)
    with pytest.raises(AudioOutputError, match=r"sudo apt install libportaudio2"):
        sink.start()


def test_audio_backend_inspection_reports_output_devices() -> None:
    module = FakeSoundDeviceModule()

    backend = inspect_audio_backend(module_loader=lambda name: module)

    assert backend.backend == "PortAudio"
    assert backend.version == "PortAudio V19.7.0"
    assert backend.default_output_device == 2
    assert [host_api.name for host_api in backend.host_apis] == [
        "ALSA",
        "JACK Audio Connection Kit",
    ]
    assert [device.index for device in backend.output_devices] == [2, 4]
    assert backend.output_devices[0].default
    assert backend.output_devices[0].host_api_name == "ALSA"
    assert not backend.output_devices[1].default
    assert backend.output_devices[1].host_api_name == "JACK Audio Connection Kit"


def test_pcm_wav_sink_drains_buffer_before_close(tmp_path: Path) -> None:
    output = tmp_path / "fanout.wav"
    recorder = PcmuWavRecorder(output)
    sink = PcmWavSink(recorder)
    sink.start()
    sink.submit_pcm(struct.pack("<4h", 0, 1, -1, 2))
    sink.stop()

    statistics = sink.statistics
    assert statistics.bytes_submitted == 4 * PCM_SAMPLE_WIDTH
    assert statistics.bytes_written == 4 * PCM_SAMPLE_WIDTH
    assert statistics.bytes_dropped == 0
    with wave.open(str(output), "rb") as recording:
        assert recording.getframerate() == PCMU_SAMPLE_RATE
        assert recording.getnframes() == 4
        assert struct.unpack("<4h", recording.readframes(4)) == (0, 1, -1, 2)



class HealthTestSink:
    def __init__(
        self,
        name: str,
        *,
        fail_start: bool = False,
        partial_start: bool = False,
        fail_submit: bool = False,
        fail_stop: bool = False,
    ) -> None:
        self._name = name
        self._running = False
        self.fail_start = fail_start
        self.partial_start = partial_start
        self.fail_submit = fail_submit
        self.fail_stop = fail_stop
        self.received: list[bytes] = []
        self.stop_calls = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def running(self) -> bool:
        return self._running

    @property
    def statistics(self) -> PcmSinkStatistics:
        total = sum(map(len, self.received))
        return PcmSinkStatistics(
            bytes_submitted=total,
            bytes_written=total,
        )

    def start(self) -> None:
        if self.fail_start:
            if self.partial_start:
                self._running = True
            raise RuntimeError("secret startup detail")
        self._running = True

    def submit_pcm(self, data: bytes) -> None:
        if self.fail_submit:
            raise RuntimeError("secret submission detail")
        self.received.append(data)

    def stop(self) -> None:
        self.stop_calls += 1
        self._running = False
        if self.fail_stop:
            raise RuntimeError("secret shutdown detail")


def test_pcm_router_startup_failure_does_not_abort_other_subscribers() -> None:
    router = PcmSinkRouter()
    failing = HealthTestSink(
        "failing",
        fail_start=True,
        partial_start=True,
    )
    healthy = HealthTestSink("healthy")

    router.attach(failing)
    router.attach(healthy)
    router.start()

    assert router.running
    assert healthy.running
    assert not failing.running
    assert failing.stop_calls == 1

    failing_snapshot = router.subscriber_snapshot(failing)
    healthy_snapshot = router.subscriber_snapshot(healthy)
    assert failing_snapshot is not None
    assert healthy_snapshot is not None
    assert failing_snapshot.state == "failed"
    assert failing_snapshot.health == "failed"
    assert not failing_snapshot.attached
    assert failing_snapshot.start_attempts == 1
    assert failing_snapshot.start_failures == 1
    assert failing_snapshot.failures == 1
    assert failing_snapshot.last_error == "RuntimeError"
    assert "secret" not in failing_snapshot.as_dict()["last_error"]
    assert healthy_snapshot.state == "active"
    assert healthy_snapshot.health == "healthy"

    router.stop()
    assert healthy.stop_calls == 1


def test_pcm_router_tracks_submit_health_and_isolates_listeners() -> None:
    initial = datetime(2026, 8, 3, 22, 30, tzinfo=UTC)
    current = initial

    def now() -> datetime:
        nonlocal current
        value = current
        current += timedelta(seconds=1)
        return value

    router = PcmSinkRouter(now=now)
    failing = HealthTestSink("failing", fail_submit=True)
    healthy = HealthTestSink("healthy")
    observed: list[PcmSubscriberTransition] = []

    def fail_listener(transition: PcmSubscriberTransition) -> None:
        del transition
        raise RuntimeError("listener failed")

    router.on_transition(fail_listener)
    router.on_transition(observed.append)
    router.attach(failing)
    router.attach(healthy)
    router.start()

    pcm = b"\x01\x00"
    router.submit_pcm(pcm)

    assert healthy.received == [pcm]
    failing_snapshot = router.subscriber_snapshot(failing)
    healthy_snapshot = router.subscriber_snapshot(healthy)
    assert failing_snapshot is not None
    assert healthy_snapshot is not None
    assert failing_snapshot.state == "failed"
    assert failing_snapshot.submit_failures == 1
    assert failing_snapshot.submissions == 1
    assert failing_snapshot.successful_submissions == 0
    assert failing_snapshot.last_failure_at is not None
    assert failing_snapshot.last_error == "RuntimeError"
    assert healthy_snapshot.state == "active"
    assert healthy_snapshot.submissions == 1
    assert healthy_snapshot.successful_submissions == 1
    assert healthy_snapshot.submit_failures == 0

    sequences = [transition.sequence for transition in observed]
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences))
    assert observed[-1].state == "failed"
    assert observed[-1].health == "failed"
    assert observed[-1].snapshot.name == "failing"

    payload = router.snapshot().as_dict()
    assert payload["running"] is True
    assert payload["transition_sequence"] == sequences[-1]
    assert len(payload["subscribers"]) == 2

    router.stop()

    stopped_snapshot = router.subscriber_snapshot(failing)
    assert stopped_snapshot is not None
    assert stopped_snapshot.state == "detached"
    assert stopped_snapshot.last_error == "RuntimeError"
    assert stopped_snapshot.submit_failures == 1


def test_pcm_router_shutdown_failure_isolated_and_recorded() -> None:
    router = PcmSinkRouter()
    failing = HealthTestSink("failing", fail_stop=True)
    healthy = HealthTestSink("healthy")

    router.attach(failing)
    router.attach(healthy)
    router.start()
    router.stop()

    assert not router.running
    assert healthy.stop_calls == 1
    assert failing.stop_calls == 1

    failing_snapshot = router.subscriber_snapshot(failing)
    healthy_snapshot = router.subscriber_snapshot(healthy)
    assert failing_snapshot is not None
    assert healthy_snapshot is not None
    assert failing_snapshot.state == "failed"
    assert failing_snapshot.health == "failed"
    assert not failing_snapshot.attached
    assert failing_snapshot.stop_failures == 1
    assert failing_snapshot.failures == 1
    assert failing_snapshot.last_error == "RuntimeError"
    assert healthy_snapshot.state == "detached"
    assert healthy_snapshot.health == "inactive"
    assert not healthy_snapshot.attached


def test_pcm_router_dynamic_start_failure_reaches_requesting_caller() -> None:
    router = PcmSinkRouter()
    healthy = HealthTestSink("healthy")
    failing = HealthTestSink(
        "failing",
        fail_start=True,
        partial_start=True,
    )

    router.attach(healthy)
    router.start()

    with pytest.raises(RuntimeError, match="secret startup detail"):
        router.attach(failing)

    assert router.running
    assert healthy.running
    router.submit_pcm(b"\x01\x00")
    assert healthy.received == [b"\x01\x00"]

    snapshot = router.subscriber_snapshot(failing)
    assert snapshot is not None
    assert snapshot.state == "failed"
    assert not snapshot.attached
    assert not failing.running
    assert failing.stop_calls == 1

    router.stop()
