from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from sds200 import cli
from sds200.models import ScannerInfo
from sds200.radio import SDSScanner
from sds200.theme import DEFAULT_LIGHT_THEME
from sds200.tui_audio import TuiAudioSession
from sds200.xml_protocol import ScannerInfoParser

from .fakes import FakeAudioTransport

FIXTURE = Path(__file__).parent / "fixtures" / "replay" / "sds100-tui.jsonl"
XML = """<?xml version="1.0" encoding="utf-8"?>
<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">
<Property VOL="10" SQL="2" Sig="5" Rssi="-86" />
</ScannerInfo>"""


class FakeTuiRadio:
    endpoint = "udp://192.0.2.25:50536"
    connected = True

    def get_model(self) -> str:
        return "SDS200"

    def get_firmware(self) -> str:
        return "Version 1.26.01"

    def get_scanner_info(self) -> ScannerInfo:
        return ScannerInfoParser().parse("GSI", XML)


def test_tui_cli_uses_replay_radio_and_selected_theme(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run_tui(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr("sds200.tui.run_tui", fake_run_tui)

    assert (
        cli.main(
            [
                "--replay",
                str(FIXTURE),
                "--theme",
                "light",
                "tui",
                "--interval",
                "250",
                "--stale-after",
                "1.5",
            ]
        )
        == 0
    )

    assert captured["endpoint"] == f"replay://{FIXTURE.resolve()}"
    assert captured["model"] == "SDS100"
    assert captured["firmware"] == "Version 1.26.01"
    assert captured["connected"] is True
    assert captured["palette"] is DEFAULT_LIGHT_THEME
    assert captured["interval_ms"] == 250
    assert captured["stale_after"] == 1.5
    radio = captured["radio"]
    assert isinstance(radio, SDSScanner)
    assert radio.endpoint == f"replay://{FIXTURE.resolve()}"
    info = captured["info"]
    assert isinstance(info, ScannerInfo)
    assert info.system == "Example P25 System"
    assert info.channel == "Example Dispatch"


@pytest.mark.parametrize(
    ("extra", "autostart"),
    [([], False), (["--audio-device", "3"], False), (["--audio-playback"], True)],
)
def test_host_tui_always_builds_manual_playback_session(
    monkeypatch: pytest.MonkeyPatch,
    extra: list[str],
    autostart: bool,
) -> None:
    captured: dict[str, object] = {}
    radio = FakeTuiRadio()

    @contextmanager
    def selected_radio(args: object) -> Iterator[FakeTuiRadio]:
        del args
        yield radio

    def fake_run_tui(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(cli, "selected_radio", selected_radio)
    monkeypatch.setattr(cli, "NetworkAudioTransport", lambda *args, **kwargs: FakeAudioTransport())
    monkeypatch.setattr("sds200.tui.run_tui", fake_run_tui)

    assert cli.main(["--host", "192.0.2.25", "tui", *extra]) == 0

    session = captured["audio_session"]
    assert isinstance(session, TuiAudioSession)
    assert session.playback_available
    assert session.live_playback_enabled is autostart
