
from __future__ import annotations

from pathlib import Path
from types import TracebackType
from typing import Self

import pytest

from sds200 import cli, tui
from sds200.models import ScannerInfo
from sds200.tui_audio import TuiAudioSession
from sds200.xml_protocol import ScannerInfoParser

from .fakes import FakeAudioTransport

XML = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">\n'
    '<Property VOL="10" SQL="2" Sig="5" Rssi="-86" />\n'
    '</ScannerInfo>'
)


class FakeTuiRadio:
    endpoint = "udp://192.0.2.25:50536"
    connected = True

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback

    def get_model(self) -> str:
        return "SDS200"

    def get_firmware(self) -> str:
        return "Version 1.26.01"

    def get_scanner_info(self) -> ScannerInfo:
        return ScannerInfoParser().parse("GSI", XML)


def test_tui_cli_builds_optional_audio_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output = tmp_path / "tui.wav"
    radio = FakeTuiRadio()
    audio_transport = FakeAudioTransport()
    captured: dict[str, object] = {}

    monkeypatch.setattr(cli, "selected_radio", lambda args: radio)
    monkeypatch.setattr(
        cli,
        "NetworkAudioTransport",
        lambda *args, **kwargs: audio_transport,
    )
    monkeypatch.setattr(tui, "run_tui", lambda **kwargs: captured.update(kwargs))

    result = cli.main(
        [
            "--host",
            "192.0.2.25",
            "tui",
            "--audio-output",
            str(output),
        ]
    )

    assert result == 0
    session = captured["audio_session"]
    assert isinstance(session, TuiAudioSession)
    assert session.path_policy.output == output
    assert not session.repeatable
    assert session.stream.transport is audio_transport


def test_tui_cli_builds_organized_audio_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    radio = FakeTuiRadio()
    audio_transport = FakeAudioTransport()
    captured: dict[str, object] = {}

    monkeypatch.setattr(cli, "selected_radio", lambda args: radio)
    monkeypatch.setattr(
        cli,
        "NetworkAudioTransport",
        lambda *args, **kwargs: audio_transport,
    )
    monkeypatch.setattr(tui, "run_tui", lambda **kwargs: captured.update(kwargs))

    result = cli.main(
        [
            "--host",
            "192.0.2.25",
            "tui",
            "--audio-directory",
            str(tmp_path),
            "--audio-organize-by",
            "scanner,date,channel",
        ]
    )

    assert result == 0
    session = captured["audio_session"]
    assert isinstance(session, TuiAudioSession)
    assert session.path_policy.organization.components == (
        "scanner",
        "date",
        "channel",
    )


def test_tui_audio_organization_requires_directory(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = cli.main(["tui", "--audio-organize-by", "scanner,date"])

    assert result == 2
    assert "--audio-organize-by requires --audio-directory" in capsys.readouterr().err


def test_tui_audio_requires_explicit_network_host(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = cli.main(
        ["tui", "--audio-output", str(tmp_path / "tui.wav")]
    )

    assert result == 2
    assert "requires an explicit SDS200 --host" in capsys.readouterr().err


def test_tui_audio_force_requires_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = cli.main(["tui", "--audio-force"])

    assert result == 2
    assert "--audio-force requires --audio-output" in capsys.readouterr().err
