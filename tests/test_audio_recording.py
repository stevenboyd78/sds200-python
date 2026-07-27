from __future__ import annotations

import struct
import wave
from pathlib import Path

import pytest

from sds200.audio import AudioChunk
from sds200.audio_recording import (
    PCM_CHANNELS,
    PCM_SAMPLE_WIDTH,
    PCMU_SAMPLE_RATE,
    PcmuWavRecorder,
    decode_mulaw,
    decode_mulaw_sample,
)


def test_decode_mulaw_known_samples() -> None:
    encoded = bytes((0xFF, 0x7F, 0xFE, 0x7E, 0xD5, 0x55, 0x80, 0x00))

    assert struct.unpack("<8h", decode_mulaw(encoded)) == (
        0,
        0,
        8,
        -8,
        716,
        -716,
        32124,
        -32124,
    )


def test_decode_mulaw_sample_rejects_out_of_range_value() -> None:
    with pytest.raises(ValueError, match="between 0 and 255"):
        decode_mulaw_sample(256)


def test_pcmu_wav_recorder_streams_valid_pcm_wave(tmp_path: Path) -> None:
    output = tmp_path / "audio.wav"
    recorder = PcmuWavRecorder(output)

    with recorder:
        recorder.write_chunk(AudioChunk(bytes((0xFF, 0x80))))
        recorder.write_pcmu(bytes((0x00, 0x7F)))

    assert recorder.packets == 2
    assert recorder.samples == 4
    assert recorder.duration_seconds == 4 / PCMU_SAMPLE_RATE
    assert not recorder.open

    with wave.open(str(output), "rb") as recording:
        assert recording.getnchannels() == PCM_CHANNELS
        assert recording.getsampwidth() == PCM_SAMPLE_WIDTH
        assert recording.getframerate() == PCMU_SAMPLE_RATE
        assert recording.getnframes() == 4
        assert struct.unpack("<4h", recording.readframes(4)) == (
            0,
            32124,
            -32124,
            0,
        )


def test_pcmu_wav_recorder_refuses_to_overwrite_by_default(tmp_path: Path) -> None:
    output = tmp_path / "existing.wav"
    output.write_bytes(b"keep")

    with pytest.raises(FileExistsError):
        PcmuWavRecorder(output).start()

    assert output.read_bytes() == b"keep"


def test_pcmu_wav_recorder_can_overwrite_explicitly(tmp_path: Path) -> None:
    output = tmp_path / "existing.wav"
    output.write_bytes(b"replace")

    with PcmuWavRecorder(output, overwrite=True):
        pass

    with wave.open(str(output), "rb") as recording:
        assert recording.getnframes() == 0
