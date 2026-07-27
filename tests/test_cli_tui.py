from __future__ import annotations

from pathlib import Path

import pytest

from sds200 import cli
from sds200.models import ScannerInfo
from sds200.theme import DEFAULT_LIGHT_THEME

FIXTURE = Path(__file__).parent / "fixtures" / "replay" / "sds100-tui.jsonl"


def test_tui_cli_uses_replay_radio_and_selected_theme(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run_tui(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr("sds200.tui.run_tui", fake_run_tui)

    assert cli.main(["--replay", str(FIXTURE), "--theme", "light", "tui"]) == 0

    assert captured["endpoint"] == f"replay://{FIXTURE.resolve()}"
    assert captured["model"] == "SDS100"
    assert captured["firmware"] == "Version 1.26.01"
    assert captured["connected"] is True
    assert captured["palette"] is DEFAULT_LIGHT_THEME
    info = captured["info"]
    assert isinstance(info, ScannerInfo)
    assert info.system == "Example P25 System"
    assert info.channel == "Example Dispatch"
