from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .exceptions import ScannerNotFoundError

DEFAULT_BY_ID_DIRECTORY = Path("/dev/serial/by-id")
DEFAULT_SDS200_PATTERN = "*UNIDEN*SDS200*"


@dataclass(frozen=True, slots=True)
class ScannerDevice:
    path: Path
    resolved_path: Path
    name: str

    def __str__(self) -> str:
        return str(self.path)


def discover_scanners(
    directory: Path = DEFAULT_BY_ID_DIRECTORY,
    pattern: str = DEFAULT_SDS200_PATTERN,
) -> list[ScannerDevice]:
    if not directory.exists():
        return []

    devices: list[ScannerDevice] = []
    for path in sorted(directory.glob(pattern)):
        try:
            resolved = path.resolve(strict=True)
        except FileNotFoundError:
            continue
        devices.append(ScannerDevice(path=path, resolved_path=resolved, name=path.name))
    return devices


def choose_scanner(
    explicit_port: str | Path | None = None,
    *,
    candidates: Iterable[ScannerDevice] | None = None,
) -> Path:
    if explicit_port is not None:
        return Path(explicit_port)

    found = list(candidates) if candidates is not None else discover_scanners()
    if not found:
        raise ScannerNotFoundError(
            "No SDS200 scanner was found. Supply a port explicitly or verify "
            "/dev/serial/by-id contains a UNIDEN SDS200 device."
        )
    return found[0].path
