from __future__ import annotations

import json
import os
import wave
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sds200 import (
    RECORDING_METADATA_SCHEMA,
    RECORDING_METADATA_VERSION,
    cli,
    recording_metadata_path,
)

NOW = datetime(2026, 8, 3, 8, 0, tzinfo=UTC)


def write_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        output.writeframes(bytes((0, 0, 1, 0)))


def write_metadata(
    audio: Path,
    *,
    started_at: datetime = NOW,
) -> Path:
    sidecar = recording_metadata_path(audio)
    sidecar.write_text(
        json.dumps(
            {
                "schema": RECORDING_METADATA_SCHEMA,
                "version": RECORDING_METADATA_VERSION,
                "recording": {"file": audio.name},
                "boundaries": {"started": {"at": started_at.isoformat()}},
            }
        ),
        encoding="utf-8",
    )
    return sidecar


def confirmation_from_output(output: str) -> str:
    prefix = "Confirmation token:   "
    return next(
        line.removeprefix(prefix)
        for line in output.splitlines()
        if line.startswith(prefix)
    )


def test_recording_retention_options_parse(tmp_path: Path) -> None:
    args = cli.build_parser().parse_args(
        [
            "recordings",
            "retention",
            str(tmp_path),
            "--maximum-age-days",
            "30",
            "--maximum-units",
            "500",
            "--maximum-total-bytes",
            "1024",
            "--planned-at",
            "2026-08-03T08:00:00Z",
            "--json",
        ]
    )

    assert args.action == "recordings"
    assert args.recordings_action == "retention"
    assert args.root == tmp_path
    assert args.maximum_age_days == 30.0
    assert args.maximum_units == 500
    assert args.maximum_total_bytes == 1024
    assert args.planned_at == NOW
    assert args.json


def test_preview_does_not_delete_selected_recording(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    audio = tmp_path / "recording.wav"
    write_wav(audio)
    sidecar = write_metadata(audio)

    assert (
        cli.main(
            [
                "recordings",
                "retention",
                str(tmp_path),
                "--maximum-units",
                "0",
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
    assert "Recording retention preview" in output
    assert "Selected units:       1" in output
    assert confirmation_from_output(output).startswith("delete:")
    assert audio.exists()
    assert sidecar.exists()


def test_preview_json_is_stable_and_non_destructive(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    audio = tmp_path / "recording.wav"
    write_wav(audio)

    assert (
        cli.main(
            [
                "recordings",
                "retention",
                str(tmp_path),
                "--maximum-total-bytes",
                "0",
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "preview"
    assert payload["confirmation_token"].startswith("delete:")
    assert payload["plan"]["summary"]["selected_units"] == 1
    assert audio.exists()


def test_exact_preview_token_executes_the_same_non_age_plan(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    audio = tmp_path / "recording.wav"
    write_wav(audio)
    sidecar = write_metadata(audio)

    preview_args = [
        "recordings",
        "retention",
        str(tmp_path),
        "--maximum-units",
        "0",
    ]
    assert cli.main(preview_args) == 0
    confirmation = confirmation_from_output(capsys.readouterr().out)

    assert cli.main([*preview_args, "--execute", confirmation]) == 0

    output = capsys.readouterr().out
    assert "Recording retention execution" in output
    assert "Completed units:      1" in output
    assert not audio.exists()
    assert not sidecar.exists()


def test_same_size_mtime_change_invalidates_preview_token(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    audio = tmp_path / "recording.wav"
    write_wav(audio)
    sidecar = write_metadata(audio)

    audio_before_preview = audio.stat()
    sidecar_before_preview = sidecar.stat()
    sidecar_mtime_ns = max(
        sidecar_before_preview.st_mtime_ns,
        audio_before_preview.st_mtime_ns + 10_000_000_000,
    )
    os.utime(
        sidecar,
        ns=(sidecar_before_preview.st_atime_ns, sidecar_mtime_ns),
    )

    preview_args = [
        "recordings",
        "retention",
        str(tmp_path),
        "--maximum-units",
        "0",
    ]

    assert cli.main(preview_args) == 0
    confirmation = confirmation_from_output(capsys.readouterr().out)

    original = audio.stat()
    sidecar_snapshot = sidecar.stat()
    changed_audio_mtime_ns = original.st_mtime_ns + 1_000_000_000
    assert changed_audio_mtime_ns < sidecar_snapshot.st_mtime_ns

    os.utime(
        audio,
        ns=(original.st_atime_ns, changed_audio_mtime_ns),
    )

    changed = audio.stat()
    assert changed.st_size == original.st_size
    assert changed.st_mtime_ns != original.st_mtime_ns
    assert sidecar.stat().st_mtime_ns == sidecar_snapshot.st_mtime_ns

    assert cli.main([*preview_args, "--execute", confirmation]) == 2

    captured = capsys.readouterr()
    assert "confirmation does not match" in captured.err
    assert captured.out == ""
    assert audio.exists()
    assert sidecar.exists()


def test_age_execution_requires_fixed_planning_boundary(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    audio = tmp_path / "old.wav"
    write_wav(audio)
    write_metadata(audio, started_at=NOW - timedelta(days=2))

    assert (
        cli.main(
            [
                "recordings",
                "retention",
                str(tmp_path),
                "--maximum-age-days",
                "1",
                "--execute",
                "delete:wrong",
            ]
        )
        == 2
    )

    assert "--planned-at is required" in capsys.readouterr().err
    assert audio.exists()


def test_fixed_age_preview_token_executes_exact_plan(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    audio = tmp_path / "old.wav"
    write_wav(audio)
    write_metadata(audio, started_at=NOW - timedelta(days=2))
    args = [
        "recordings",
        "retention",
        str(tmp_path),
        "--maximum-age-days",
        "1",
        "--planned-at",
        NOW.isoformat(),
    ]

    assert cli.main(args) == 0
    confirmation = confirmation_from_output(capsys.readouterr().out)

    assert cli.main([*args, "--execute", confirmation]) == 0
    assert "Completed units:      1" in capsys.readouterr().out
    assert not audio.exists()


def test_wrong_confirmation_refuses_all_mutation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    audio = tmp_path / "recording.wav"
    write_wav(audio)
    sidecar = write_metadata(audio)

    assert (
        cli.main(
            [
                "recordings",
                "retention",
                str(tmp_path),
                "--maximum-units",
                "0",
                "--execute",
                "delete:wrong",
            ]
        )
        == 2
    )

    captured = capsys.readouterr()
    assert "confirmation does not match" in captured.err
    assert captured.out == ""
    assert audio.exists()
    assert sidecar.exists()


def test_protected_unit_returns_unsatisfied_status_without_deletion(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    audio = tmp_path / "protected.wav"
    write_wav(audio)
    sidecar = recording_metadata_path(audio)
    sidecar.write_text("{broken", encoding="utf-8")

    assert (
        cli.main(
            [
                "recordings",
                "retention",
                str(tmp_path),
                "--maximum-units",
                "0",
            ]
        )
        == 1
    )

    output = capsys.readouterr().out
    assert "Protected units:      1" in output
    assert "All limits satisfied: no" in output
    assert audio.exists()
    assert sidecar.exists()


def test_recordings_action_rejects_scanner_connection_selector(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        cli.main(
            [
                "--host",
                "192.0.2.25",
                "recordings",
                "retention",
                str(tmp_path),
                "--maximum-units",
                "0",
            ]
        )
        == 2
    )

    assert "Connection selectors are not used" in capsys.readouterr().err
