from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

import pytest

from sds200 import completion
from sds200.cli import build_parser
from sds200.device import ScannerDevice


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
