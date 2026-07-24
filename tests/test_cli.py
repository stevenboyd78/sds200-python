from __future__ import annotations

import threading
import time

import pytest

from sds200 import cli
from sds200.radio import SDSScanner

from .fakes import FakeTransport


def _feed_gsi(transport: FakeTransport, xml: str) -> None:
    transport.feed_line("GSI,<XML>,")
    for line in xml.splitlines():
        transport.feed_line(line)


def test_sds100_battery_cli_uses_optional_gsi_property(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    transport = FakeTransport()
    radio = SDSScanner.from_transport(transport, expected_model="SDS100")
    monkeypatch.setattr(cli, "selected_radio", lambda args: radio)
    xml = """<?xml version="1.0" encoding="utf-8"?>
<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">
<Property VOL="10" SQL="2" Sig="5" Rssi="-86" />
</ScannerInfo>"""

    def respond() -> None:
        while transport.writes != ["MDL"]:
            time.sleep(0.005)
        transport.feed_line("MDL,SDS100")
        while transport.writes != ["MDL", "GSI"]:
            time.sleep(0.005)
        _feed_gsi(transport, xml)

    thread = threading.Thread(target=respond, daemon=True)
    thread.start()
    assert cli.main(["--model", "SDS100", "battery"]) == 0
    thread.join(timeout=1.0)

    assert transport.writes == ["MDL", "GSI"]
    assert capsys.readouterr().out.splitlines() == [
        "Model:   SDS100",
        "Battery: unavailable",
        "Source:  GSI Property",
    ]


def test_scanner_info_cli_prints_extended_property_fields(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    transport = FakeTransport()
    radio = SDSScanner.from_transport(transport, expected_model="SDS100")
    monkeypatch.setattr(cli, "selected_radio", lambda args: radio)
    xml = """<?xml version="1.0" encoding="utf-8"?>
<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">
<System Name="Utah Communications Authority (P25)" />
<Site Name="Utah County Simulcast" Mod="NFM" />
<Property VOL="10" SQL="2" Sig="5" Rssi="-86" Rec="Off" Mute="Mute" />
</ScannerInfo>"""

    def respond() -> None:
        while transport.writes != ["GSI"]:
            time.sleep(0.005)
        _feed_gsi(transport, xml)

    thread = threading.Thread(target=respond, daemon=True)
    thread.start()
    assert cli.main(["--model", "SDS100", "scanner-info"]) == 0
    thread.join(timeout=1.0)

    output = capsys.readouterr().out
    assert "RSSI:       -86" in output
    assert "Battery:    -" in output
    assert "Recording:  Off" in output
    assert "Mute:       Mute" in output
