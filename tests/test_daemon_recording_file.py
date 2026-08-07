from __future__ import annotations

import os
import wave
from pathlib import Path
from typing import Any, cast

import pytest

import sds200.daemon_recording as daemon_recording
from sds200.daemon_recording import (
    DaemonRecordingFileNotFoundError,
    DaemonRecordingFileNotPlayableError,
    DaemonRecordingFileUnavailableError,
    DaemonRecordingIdentifierError,
    DaemonRecordingManager,
    DaemonRecordingOperationError,
)


def write_wav(
    path: Path,
    *,
    sample_rate: int = 8000,
    frames: bytes = b"\x00\x00\x01\x00",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(frames)


def manager(root: Path) -> DaemonRecordingManager:
    return DaemonRecordingManager(cast(Any, object()), root)


def test_open_recording_returns_inventory_relative_playable_file(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "nested" / "dispatch.wav"
    write_wav(audio)
    expected = audio.read_bytes()

    with manager(tmp_path).open_recording(
        "nested/dispatch.wav"
    ) as opened:
        assert opened.identifier == "nested/dispatch.wav"
        assert opened.size_bytes == len(expected)
        assert opened.stream.read() == expected

    assert opened.stream.closed


@pytest.mark.parametrize(
    "identifier",
    [
        "",
        " dispatch.wav",
        "dispatch.wav ",
        "/dispatch.wav",
        "../dispatch.wav",
        "nested/../dispatch.wav",
        "nested//dispatch.wav",
        "./dispatch.wav",
        "dispatch.mp3",
        "bad\x00name.wav",
    ],
)
def test_open_recording_rejects_non_inventory_identifiers(
    tmp_path: Path,
    identifier: str,
) -> None:
    with pytest.raises(DaemonRecordingIdentifierError):
        manager(tmp_path).open_recording(identifier)


def test_open_recording_requires_existing_inventory_entry(
    tmp_path: Path,
) -> None:
    with pytest.raises(DaemonRecordingFileNotFoundError):
        manager(tmp_path).open_recording("missing.wav")


def test_open_recording_rejects_incompatible_audio(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "wrong-rate.wav"
    write_wav(audio, sample_rate=16000)

    with pytest.raises(DaemonRecordingFileNotPlayableError):
        manager(tmp_path).open_recording("wrong-rate.wav")


def test_open_recording_rejects_symlink_outside_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.wav"
    write_wav(outside)
    link = root / "linked.wav"
    try:
        link.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")

    with pytest.raises(DaemonRecordingFileNotPlayableError):
        manager(root).open_recording("linked.wav")


def test_open_recording_rejects_active_recording(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "active.wav"
    write_wav(audio)
    selected = manager(tmp_path)
    selected._recording_path = audio
    selected._sink = cast(Any, object())

    with pytest.raises(DaemonRecordingFileUnavailableError):
        selected.open_recording("active.wav")


def test_open_recording_safe_open_blocks_symlink_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    audio = root / "dispatch.wav"
    outside = tmp_path / "outside.wav"
    write_wav(audio)
    write_wav(outside, frames=b"\x55\x00\x66\x00")

    original_scan = daemon_recording.scan_recording_inventory

    def swap_after_scan(path: str | Path):
        inventory = original_scan(path)
        audio.unlink()
        try:
            audio.symlink_to(outside)
        except OSError as error:
            pytest.skip(f"symlinks unavailable: {error}")
        return inventory

    monkeypatch.setattr(
        daemon_recording,
        "scan_recording_inventory",
        swap_after_scan,
    )

    with pytest.raises(DaemonRecordingOperationError):
        manager(root).open_recording("dispatch.wav")

    assert outside.read_bytes()
    assert os.path.islink(audio)
