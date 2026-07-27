from __future__ import annotations

import struct
import wave
from collections.abc import Callable
from pathlib import Path

import pytest

from sds200 import cli
from sds200.audio import AudioChunk, AudioChunkHandler


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


def _monotonic(values: list[float]) -> Callable[[], float]:
    iterator = iter(values)
    return iterator.__next__


def test_audio_cli_records_native_pcm_wave(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "scanner.wav"
    transport = FakeNetworkAudioTransport()
    _install_fake_transport(monkeypatch, transport)
    monkeypatch.setattr(cli, "sleep", lambda _seconds: None)
    monkeypatch.setattr(cli, "monotonic", _monotonic([10.0, 20.0, 21.5]))

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
        "Recorded 1.5 seconds",
        "Packets: 1",
        "Audio samples: 2",
        "Audio duration: 0.0 seconds",
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
