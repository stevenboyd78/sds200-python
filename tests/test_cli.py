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


def test_daemon_psi_recovery_options_parse() -> None:
    args = cli.build_parser().parse_args(
        [
            "--host",
            "192.168.0.251",
            "daemon",
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


def test_standard_parser_preserves_existing_managed_defaults() -> None:
    args = cli.build_parser().parse_args(["info"])

    assert args.max_xml_retries == 2
    assert args.reconnect_attempts == 0
    assert args.reconnect_initial_delay == 1.0
    assert args.reconnect_multiplier == 2.0
    assert args.reconnect_max_delay == 30.0
    assert args.health_history_limit == 100
    assert args.color == "auto"
    assert args.theme == "dark"
    assert args.verbose == 0
    assert args.log_level is None
    assert args.log_file is None


def test_runtime_parser_preserves_absent_managed_values() -> None:
    from sds200 import APPLICATION_CONFIGURATION_FIELDS

    args = cli.build_parser(
        suppress_configuration_defaults=True
    ).parse_args(["info"])

    assert all(
        not hasattr(args, field)
        for field in APPLICATION_CONFIGURATION_FIELDS
    )
    assert not hasattr(args, "verbose")


def test_cli_configuration_applies_files_environment_and_explicit_options(
    tmp_path: Path,
) -> None:
    from sds200 import resolve_configuration_paths

    paths = resolve_configuration_paths(
        environ={},
        home=tmp_path / "home",
        system_config_dir=tmp_path / "etc" / "sdsctl",
    )
    paths.system_config_dir.mkdir(parents=True)
    paths.user_config_dir.mkdir(parents=True)
    paths.system_config_file.write_text(
        "version = 1\n\n"
        "[application]\n"
        "max_xml_retries = 5\n"
        'theme = "dark"\n',
        encoding="utf-8",
    )
    paths.user_config_file.write_text(
        "version = 1\n\n"
        "[application]\n"
        "health_history_limit = 250\n",
        encoding="utf-8",
    )

    args = cli.build_parser(
        suppress_configuration_defaults=True
    ).parse_args(
        [
            "-vv",
            "--color=auto",
            "--reconnect-attempts",
            "0",
            "info",
        ]
    )

    resolved = cli._apply_cli_configuration(
        args,
        paths=paths,
        environ={
            "SDSCTL_THEME": "light",
            "SDSCTL_COLOR": "never",
            "SDSCTL_LOG_LEVEL": "ERROR",
        },
    )

    assert args.max_xml_retries == 5
    assert args.health_history_limit == 250
    assert args.theme == "light"
    assert args.color == "auto"
    assert args.reconnect_attempts == 0
    assert args.log_level == "DEBUG"
    assert args.verbose == 2
    assert resolved.source_for("max_xml_retries") == "system"
    assert resolved.source_for("health_history_limit") == "user"
    assert resolved.source_for("theme") == "environment"
    assert resolved.source_for("color") == "command-line"
    assert resolved.source_for("reconnect_attempts") == "command-line"
    assert resolved.source_for("log_level") == "command-line"


def test_explicit_log_level_overrides_verbose_for_configuration(
    tmp_path: Path,
) -> None:
    from sds200 import resolve_configuration_paths

    paths = resolve_configuration_paths(
        environ={},
        home=tmp_path / "home",
        system_config_dir=tmp_path / "etc" / "sdsctl",
    )
    args = cli.build_parser(
        suppress_configuration_defaults=True
    ).parse_args(["-vv", "--log-level", "error", "info"])

    resolved = cli._apply_cli_configuration(
        args,
        paths=paths,
        environ={"SDSCTL_LOG_LEVEL": "INFO"},
    )

    assert args.verbose == 2
    assert args.log_level == "ERROR"
    assert resolved.source_for("log_level") == "command-line"


def test_main_uses_layered_logging_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from sds200 import resolve_configuration_paths

    paths = resolve_configuration_paths(
        environ={},
        home=tmp_path / "home",
        system_config_dir=tmp_path / "etc" / "sdsctl",
    )
    paths.user_config_dir.mkdir(parents=True)
    paths.user_config_file.write_text(
        "version = 1\n\n"
        "[application]\n"
        'log_level = "WARNING"\n',
        encoding="utf-8",
    )
    observed: list[tuple[int, str | None, Path | None]] = []

    def configure(
        verbose: int = 0,
        *,
        level_name: str | None = None,
        log_file: Path | None = None,
    ) -> int:
        observed.append((verbose, level_name, log_file))
        return 20

    monkeypatch.setattr(cli, "configure_logging", configure)
    monkeypatch.setattr(cli, "completion_script", lambda shell: f"{shell} completion")

    result = cli.main(
        ["-v", "completion", "bash"],
        configuration_paths=paths,
        environ={"SDSCTL_LOG_LEVEL": "ERROR"},
    )

    assert result == 0
    assert observed == [(1, "INFO", None)]
    assert capsys.readouterr().out == "bash completion\n"


def test_main_reports_configuration_error_before_logging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from sds200 import resolve_configuration_paths

    paths = resolve_configuration_paths(
        environ={},
        home=tmp_path / "home",
        system_config_dir=tmp_path / "etc" / "sdsctl",
    )
    paths.user_config_dir.mkdir(parents=True)
    paths.user_config_file.write_text(
        "version = 1\n\n"
        "[application]\n"
        "health_history_limit = 0\n",
        encoding="utf-8",
    )
    logging_calls: list[object] = []
    monkeypatch.setattr(
        cli,
        "configure_logging",
        lambda *args, **kwargs: logging_calls.append((args, kwargs)),
    )

    result = cli.main(
        ["completion", "bash"],
        configuration_paths=paths,
        environ={},
    )

    captured = capsys.readouterr()
    assert result == 2
    assert logging_calls == []
    assert "Invalid user configuration" in captured.err
    assert str(paths.user_config_file) in captured.err


def test_runtime_parser_preserves_explicit_no_color_alias() -> None:
    args = cli.build_parser(
        suppress_configuration_defaults=True
    ).parse_args(["--no-color", "info"])

    assert args.color == "never"
