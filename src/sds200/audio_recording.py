from __future__ import annotations

import threading
import wave
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, Self, cast

from .audio import AudioChunk

PCMU_SAMPLE_RATE = 8000
PCM_CHANNELS = 1
PCM_SAMPLE_WIDTH = 2
_MULAW_BIAS = 0x84


def decode_mulaw_sample(value: int) -> int:
    """Decode one G.711 mu-law byte into a signed 16-bit PCM sample."""
    if not 0 <= value <= 0xFF:
        raise ValueError("mu-law sample must be between 0 and 255")

    encoded = (~value) & 0xFF
    sign = encoded & 0x80
    exponent = (encoded >> 4) & 0x07
    mantissa = encoded & 0x0F
    magnitude = ((mantissa << 3) + _MULAW_BIAS) << exponent
    sample = magnitude - _MULAW_BIAS
    return -sample if sign else sample


def decode_mulaw(data: bytes) -> bytes:
    """Decode G.711 mu-law bytes into little-endian signed 16-bit PCM."""
    pcm = bytearray(len(data) * PCM_SAMPLE_WIDTH)
    offset = 0
    for value in data:
        sample = decode_mulaw_sample(value)
        pcm[offset : offset + PCM_SAMPLE_WIDTH] = sample.to_bytes(
            PCM_SAMPLE_WIDTH,
            byteorder="little",
            signed=True,
        )
        offset += PCM_SAMPLE_WIDTH
    return bytes(pcm)


class PcmuWavRecorder:
    """Stream raw PCMU chunks into an 8 kHz mono signed 16-bit PCM WAV file."""

    def __init__(self, path: Path, *, overwrite: bool = False) -> None:
        self.path = path
        self.overwrite = overwrite
        self._stream: BinaryIO | None = None
        self._writer: wave.Wave_write | None = None
        self._packets = 0
        self._samples = 0
        self._lock = threading.RLock()

    @property
    def packets(self) -> int:
        with self._lock:
            return self._packets

    @property
    def samples(self) -> int:
        with self._lock:
            return self._samples

    @property
    def duration_seconds(self) -> float:
        return self.samples / PCMU_SAMPLE_RATE

    @property
    def open(self) -> bool:
        with self._lock:
            return self._writer is not None

    def start(self) -> None:
        with self._lock:
            if self._writer is not None:
                return
            mode = "wb" if self.overwrite else "xb"
            stream = cast(BinaryIO, self.path.open(mode))
            try:
                writer = wave.open(stream, "wb")  # noqa: SIM115
                writer.setnchannels(PCM_CHANNELS)
                writer.setsampwidth(PCM_SAMPLE_WIDTH)
                writer.setframerate(PCMU_SAMPLE_RATE)
            except Exception:
                stream.close()
                raise
            self._stream = stream
            self._writer = writer

    def write_chunk(self, chunk: AudioChunk) -> None:
        self.write_pcmu(chunk.data)

    def write_pcmu(self, data: bytes) -> None:
        if not data:
            return
        pcm = decode_mulaw(data)
        with self._lock:
            writer = self._writer
            if writer is None:
                raise RuntimeError("WAV recorder is not open")
            writer.writeframesraw(pcm)
            self._packets += 1
            self._samples += len(data)

    def close(self) -> None:
        with self._lock:
            writer, self._writer = self._writer, None
            stream, self._stream = self._stream, None
            try:
                if writer is not None:
                    writer.close()
            finally:
                if stream is not None:
                    stream.close()

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        self.close()
