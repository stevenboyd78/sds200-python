from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

import pytest

from sds200 import completion
from sds200.cli import build_parser, selected_radio
from sds200.device import ScannerDevice
from sds200.network import UdpTransport


def test_completion_subcommand_parses() -> None:
    args = build_parser().parse_args(["completion", "bash"])
    assert args.action == "completion"
    assert args.shell == "bash"


def test_monitor_subcommand_parses() -> None:
    args = build_parser().parse_args(["monitor", "--interval", "250", "--no-clear"])
    assert args.action == "monitor"
    assert args.interval == 250
    assert args.no_clear is True


def test_command_completer_filters_prefix() -> None:
    assert completion.command_completer("V") == {
        "VER": "Get firmware version",
        "VOL": "Get volume level",
    }


def test_port_completer_suggests_stable_and_resolved_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = ScannerDevice(
        path=Path("/dev/serial/by-id/usb-UNIDEN_SDS200-if00"),
        resolved_path=Path("/dev/ttyACM0"),
        name="usb-UNIDEN_SDS200-if00",
    )
    monkeypatch.setattr(completion, "discover_scanners", lambda: [device])

    stable = completion.port_completer("/dev/serial")
    resolved = completion.port_completer("/dev/tty")

    assert "/dev/serial/by-id/usb-UNIDEN_SDS200-if00" in stable
    assert "/dev/ttyACM0" in resolved


def test_enable_tab_completion_uses_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[argparse.ArgumentParser] = []
    fake = SimpleNamespace(autocomplete=lambda parser: seen.append(parser))
    monkeypatch.setattr(completion, "_argcomplete", lambda: fake)
    parser = build_parser()

    completion.enable_tab_completion(parser)

    assert seen == [parser]


def test_completion_script_uses_requested_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], str]] = []

    def shellcode(executables: list[str], *, shell: str) -> str:
        calls.append((executables, shell))
        return f"completion for {shell}"

    monkeypatch.setattr(
        completion,
        "_argcomplete",
        lambda: SimpleNamespace(shellcode=shellcode),
    )

    assert completion.completion_script("zsh") == "completion for zsh"
    assert calls == [(["sds200"], "zsh")]


def test_completion_script_rejects_unknown_shell() -> None:
    with pytest.raises(ValueError, match="Unsupported shell"):
        completion.completion_script("fish")


def test_network_options_parse() -> None:
    args = build_parser().parse_args(
        [
            "--host",
            "192.0.2.25",
            "--udp-port",
            "50536",
            "--bind-address",
            "127.0.0.1",
            "--bind-port",
            "42000",
            "monitor",
        ]
    )
    assert args.host == "192.0.2.25"
    assert args.udp_port == 50536
    assert args.bind_address == "127.0.0.1"
    assert args.bind_port == 42000


def test_serial_port_and_network_host_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["--port", "/dev/ttyACM0", "--host", "192.0.2.25", "info"]
        )


def test_selected_radio_creates_udp_transport() -> None:
    args = build_parser().parse_args(["--host", "192.0.2.25", "info"])
    radio = selected_radio(args)
    assert isinstance(radio.transport, UdpTransport)
    assert radio.endpoint == "udp://192.0.2.25:50536"


def test_udp_options_require_host() -> None:
    args = build_parser().parse_args(["--bind-port", "42000", "info"])
    with pytest.raises(ValueError, match="require --host"):
        selected_radio(args)


def test_profile_option_loads_saved_network_connection(tmp_path: Path) -> None:
    from sds200.profiles import ConnectionProfile, ProfileStore

    config = tmp_path / "profiles.toml"
    store = ProfileStore(config)
    store.put(ConnectionProfile.network("home", "192.0.2.25"))
    args = build_parser().parse_args(
        ["--config", str(config), "--profile", "home", "info"]
    )

    radio = selected_radio(args)

    assert isinstance(radio.transport, UdpTransport)
    assert radio.endpoint == "udp://192.0.2.25:50536"


def test_discovery_options_parse() -> None:
    args = build_parser().parse_args(
        ["discover", "--network", "192.0.2.0/24", "--network-only"]
    )
    assert args.network == ["192.0.2.0/24"]
    assert args.network_only is True


def test_profile_add_options_parse() -> None:
    args = build_parser().parse_args(
        ["profile", "add", "home", "--host", "192.0.2.25"]
    )
    assert args.profile_action == "add"
    assert args.profile_host == "192.0.2.25"


def test_profile_completer_reads_configured_store(
    tmp_path: Path,
) -> None:
    from sds200.profiles import ConnectionProfile, ProfileStore

    config = tmp_path / "profiles.toml"
    ProfileStore(config).put(ConnectionProfile.network("home", "192.0.2.25"))
    parsed_args = SimpleNamespace(config=config)

    assert completion.profile_completer("ho", parsed_args=parsed_args) == {
        "home": "network scanner connection"
    }


def test_discovery_workers_option() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["discover", "--network", "192.168.0.0/24", "--workers", "8"]
    )
    assert args.workers == 8


def test_health_watch_options_parse() -> None:
    args = build_parser().parse_args(["health", "--watch", "5", "--json"])
    assert args.watch == 5.0
    assert args.json is True


def test_profile_discover_options_parse() -> None:
    args = build_parser().parse_args(
        [
            "profile",
            "discover",
            "home",
            "--network",
            "192.0.2.0/24",
            "--prefer",
            "network",
        ]
    )
    assert args.profile_action == "discover"
    assert args.profile_preference == "network"


def test_profile_preference_override_parses() -> None:
    args = build_parser().parse_args(
        ["--profile", "home", "--prefer", "network", "info"]
    )
    assert args.connection_preference == "network"
