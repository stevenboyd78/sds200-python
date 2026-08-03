from __future__ import annotations

import struct
import wave
from pathlib import Path

import pytest

from sds200 import cli
from sds200.audio import AudioChunk, AudioChunkHandler
from sds200.audio_sinks import (
    AudioBackendInfo,
    AudioHostApiInfo,
    AudioOutputDeviceInfo,
)
from sds200.network_audio import NetworkAudioStatistics


class FakeNetworkAudioTransport:
    def __init__(self) -> None:
        self._running = False
        self.started = False
        self.stopped = False

    @property
    def endpoint(self) -> str:
        return "rtsp://192.0.2.25/au:scanner.au"

    @property
    def running(self) -> bool:
        return self._running

    def start(self, handler: AudioChunkHandler) -> None:
        self.started = True
        self._running = True
        handler(AudioChunk(bytes((0xFF, 0x80))))

    @property
    def statistics(self) -> NetworkAudioStatistics:
        return NetworkAudioStatistics(
            packets_delivered=1,
            packets_lost=2,
            duplicate_packets=3,
            late_packets=4,
            malformed_packets=5,
            unexpected_source_packets=6,
            ssrc_mismatch_packets=7,
            timestamp_discontinuities=8,
        )

    def stop(self) -> None:
        self.stopped = True
        self._running = False


def _install_fake_transport(
    monkeypatch: pytest.MonkeyPatch,
    transport: FakeNetworkAudioTransport,
) -> None:
    def factory(*args: object, **kwargs: object) -> FakeNetworkAudioTransport:
        del args, kwargs
        return transport

    monkeypatch.setattr(cli, "NetworkAudioTransport", factory)


def test_audio_cli_records_native_pcm_wave(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "scanner.wav"
    transport = FakeNetworkAudioTransport()
    _install_fake_transport(monkeypatch, transport)
    monkeypatch.setattr(cli, "sleep", lambda _seconds: None)

    result = cli.main(
        [
            "--host",
            "192.0.2.25",
            "audio",
            "--output",
            str(output),
            "--duration",
            "1",
        ]
    )

    assert result == 0
    assert transport.started
    assert transport.stopped
    with wave.open(str(output), "rb") as recording:
        assert recording.getnchannels() == 1
        assert recording.getsampwidth() == 2
        assert recording.getframerate() == 8000
        assert recording.getnframes() == 2
        assert struct.unpack("<2h", recording.readframes(2)) == (0, 32124)

    assert capsys.readouterr().out.splitlines() == [
        "Streamed 0.0 seconds",
        "Packets: 1",
        "Audio samples: 2",
        "RTP lost: 2",
        "RTP duplicates: 3",
        "RTP late: 4",
        "RTP malformed: 5",
        "RTP unexpected source: 6",
        "RTP SSRC mismatches: 7",
        "Timestamp discontinuities: 8",
        f"Output: {output}",
    ]


def test_audio_cli_requires_explicit_host(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = cli.main(
        ["audio", "--output", str(tmp_path / "scanner.wav"), "--duration", "1"]
    )

    assert result == 2
    assert "audio requires an explicit SDS200 --host" in capsys.readouterr().err


def test_audio_cli_refuses_to_overwrite_existing_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "scanner.wav"
    output.write_bytes(b"keep")
    transport = FakeNetworkAudioTransport()
    _install_fake_transport(monkeypatch, transport)

    result = cli.main(
        [
            "--host",
            "192.0.2.25",
            "audio",
            "--output",
            str(output),
            "--duration",
            "1",
        ]
    )

    assert result == 2
    assert not transport.started
    assert output.read_bytes() == b"keep"
    assert "File exists" in capsys.readouterr().err


def test_audio_devices_cli_lists_backend_and_outputs(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    backend = AudioBackendInfo(
        backend="PortAudio",
        version="PortAudio V19.7.0",
        default_output_device=2,
        host_apis=(
            AudioHostApiInfo(
                index=0,
                name="ALSA",
                default_output_device=2,
            ),
        ),
        output_devices=(
            AudioOutputDeviceInfo(
                index=2,
                name="HDMI",
                host_api_index=0,
                host_api_name="ALSA",
                max_output_channels=2,
                default_samplerate=48000.0,
                default=True,
            ),
        ),
    )
    monkeypatch.setattr(cli, "inspect_audio_backend", lambda: backend)

    assert cli.main(["audio-devices"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "Backend: PortAudio",
        "Version: PortAudio V19.7.0",
        "Default output: 2: HDMI [ALSA]",
        "Host APIs:",
        "  0: ALSA (default output: 2)",
        "Output devices:",
        "  2: HDMI [ALSA] channels=2 default-rate=48000 Hz (default)",
    ]


def test_audio_parser_accepts_playback_without_output() -> None:
    args = cli.build_parser().parse_args(
        [
            "--host",
            "192.168.0.251",
            "audio",
            "--play",
            "--device",
            "USB Audio",
            "--buffer-ms",
            "400",
        ]
    )

    assert args.play
    assert args.output is None
    assert args.device == "USB Audio"
    assert args.buffer_ms == 400


def test_audio_parser_converts_numeric_device_index() -> None:
    args = cli.build_parser().parse_args(
        ["--host", "192.168.0.251", "audio", "--play", "--device", "2"]
    )

    assert args.device == 2


def test_audio_parser_accepts_simultaneous_playback_and_recording(
    tmp_path: Path,
) -> None:
    output = tmp_path / "scanner.wav"
    args = cli.build_parser().parse_args(
        [
            "--host",
            "192.168.0.251",
            "audio",
            "--play",
            "--output",
            str(output),
        ]
    )

    assert args.play
    assert args.output == output


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ([], "audio requires --play, --output, or both"),
        (["--force"], "--force requires --output"),
        (["--device", "USB Audio"], "--device requires --play"),
    ],
)
def test_audio_cli_rejects_invalid_destination_options(
    options: list[str],
    message: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["--host", "192.168.0.251", "audio", *options]) == 2
    assert message in capsys.readouterr().err
