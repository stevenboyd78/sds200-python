from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from sds200 import __version__, cli
from sds200.radio import SDSScanner
from sds200.replay import CaptureEvent, write_capture

from .fakes import FakeTransport


def _feed_gsi(transport: FakeTransport, xml: str) -> None:
    transport.feed_line("GSI,<XML>,")
    for line in xml.splitlines():
        transport.feed_line(line)


@pytest.mark.parametrize("flag", ["-V", "--version"])
def test_version_flags_report_installed_version(
    flag: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main([flag])

    assert exc_info.value.code == 0
    assert capsys.readouterr().out == f"sdsctl {__version__}\n"


def test_global_logging_options_parse_before_subcommand(tmp_path: Path) -> None:
    path = tmp_path / "sdsctl.log"
    args = cli.build_parser().parse_args(
        [
            "--log-level",
            "debug",
            "--log-file",
            str(path),
            "info",
        ]
    )

    assert args.log_level == "DEBUG"
    assert args.log_file == path


def test_tui_psi_recovery_options_parse() -> None:
    args = cli.build_parser().parse_args(
        [
            "--host",
            "192.168.0.251",
            "tui",
            "--no-psi-auto-recover",
            "--psi-recover-after",
            "20",
            "--psi-recovery-cooldown",
            "90",
        ]
    )

    assert not args.psi_auto_recover
    assert args.psi_recover_after == 20.0
    assert args.psi_recovery_cooldown == 90.0


def test_tui_audio_metadata_option_parses(tmp_path: Path) -> None:
    args = cli.build_parser().parse_args(
        [
            "--host",
            "192.168.0.251",
            "tui",
            "--audio-directory",
            str(tmp_path),
            "--audio-metadata",
        ]
    )

    assert args.audio_directory == tmp_path
    assert args.audio_metadata


def test_tui_audio_organization_option_parses(tmp_path: Path) -> None:
    args = cli.build_parser().parse_args(
        [
            "--host",
            "192.168.0.251",
            "tui",
            "--audio-directory",
            str(tmp_path),
            "--audio-organize-by",
            "scanner,date,system,channel",
        ]
    )

    assert args.audio_organize_by.components == (
        "scanner",
        "date",
        "system",
        "channel",
    )


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


def test_replay_cli_runs_info_without_hardware(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = Path(__file__).parent / "fixtures" / "replay" / "sds100-info.jsonl"

    assert cli.main(["--replay", str(fixture), "--model", "SDS100", "info"]) == 0

    output = capsys.readouterr().out
    assert "Model:    SDS100" in output
    assert "Firmware: Version 1.26.01" in output
    assert "Volume:   10" in output
    assert "Squelch:  2" in output


def test_capabilities_cli_reports_validation_status(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = Path(__file__).parent / "fixtures" / "replay" / "sds100-info.jsonl"

    assert cli.main(["--replay", str(fixture), "capabilities"]) == 0

    output = capsys.readouterr().out
    assert "Model:              SDS100" in output
    assert "Validation:         hardware-validated" in output
    assert "Navigation control: yes" in output
    assert "Battery level:      optional" in output


def test_navigation_cli_uses_typed_command(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = tmp_path / "navigation.jsonl"
    write_capture(
        fixture,
        (
            CaptureEvent(direction="tx", data="MDL"),
            CaptureEvent(direction="rx", data="MDL,SDS100"),
            CaptureEvent(direction="tx", data="HLD,SYS,42,"),
            CaptureEvent(direction="rx", data="HLD,OK"),
        ),
    )

    assert cli.main(["--replay", str(fixture), "hold", "sys", "42"]) == 0
    assert capsys.readouterr().out == "OK\n"


def test_redact_requires_capture(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = Path(__file__).parent / "fixtures" / "replay" / "sds100-info.jsonl"

    assert cli.main(["--replay", str(fixture), "--redact", "secret", "info"]) == 2
    assert "--redact requires --capture" in capsys.readouterr().err


def test_replay_speed_requires_replay(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["--replay-speed", "1", "info"]) == 2
    assert "--replay-speed requires --replay" in capsys.readouterr().err
