from __future__ import annotations

import argparse
import importlib
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, cast

from .device import discover_scanners

SUPPORTED_SHELLS = ("bash", "zsh")

KNOWN_COMMANDS: Mapping[str, str] = {
    "GSI": "Get structured scanner information",
    "MDL": "Get scanner model",
    "PSI,0": "Stop scanner information updates",
    "PSI,500": "Start scanner information updates every 500 ms",
    "SQL": "Get squelch level",
    "STS": "Get scanner display status",
    "VER": "Get firmware version",
    "VOL": "Get volume level",
}


class _ArgcompleteModule(Protocol):
    def autocomplete(self, parser: argparse.ArgumentParser) -> object: ...

    def shellcode(
        self,
        executables: list[str],
        *,
        shell: str,
    ) -> str: ...


def _argcomplete() -> _ArgcompleteModule:
    module = importlib.import_module("argcomplete")
    return cast(_ArgcompleteModule, module)


def enable_tab_completion(parser: argparse.ArgumentParser) -> None:
    """Enable argcomplete when invoked by an activated shell hook."""
    _argcomplete().autocomplete(parser)


def completion_script(shell: str) -> str:
    """Return shell code that registers completion for the ``sds200`` command."""
    if shell not in SUPPORTED_SHELLS:
        supported = ", ".join(SUPPORTED_SHELLS)
        raise ValueError(f"Unsupported shell {shell!r}; choose one of: {supported}")
    return _argcomplete().shellcode(["sds200"], shell=shell)


def command_completer(prefix: str, **_: object) -> dict[str, str]:
    """Suggest known commands while still allowing arbitrary raw commands."""
    normalized = prefix.upper()
    return {
        command: description
        for command, description in KNOWN_COMMANDS.items()
        if command.startswith(normalized)
    }


def port_completer(prefix: str, **_: object) -> dict[str, str]:
    """Suggest stable by-id paths and their current tty targets."""
    suggestions: dict[str, str] = {}
    for device in discover_scanners():
        stable_path = str(device.path)
        resolved_path = str(device.resolved_path)
        if stable_path.startswith(prefix):
            suggestions[stable_path] = f"SDS200 → {resolved_path}"
        if resolved_path.startswith(prefix):
            suggestions[resolved_path] = f"SDS200 via {Path(stable_path).name}"
    return suggestions
