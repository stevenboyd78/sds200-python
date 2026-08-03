from __future__ import annotations

import struct
import wave
from collections.abc import Callable
from pathlib import Path

import pytest

from sds200.audio import AudioChunk, AudioChunkHandler, AudioStream
from sds200.audio_recording import PCM_SAMPLE_WIDTH, PCMU_SAMPLE_RATE, PcmuWavRecorder
from sds200.audio_sinks import (
    AudioFanoutSession,
    PcmSinkStatistics,
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
