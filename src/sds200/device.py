from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .exceptions import ScannerNotFoundError
from .scanner import ScannerModel, infer_model_from_device_name, normalize_model_name

DEFAULT_BY_ID_DIRECTORY = Path("/dev/serial/by-id")
DEFAULT_SDS_PATTERN = "*UNIDEN*SDS*"
DEFAULT_SDS200_PATTERN = "*UNIDEN*SDS200*"


@dataclass(frozen=True, slots=True)
class ScannerDevice:
    path: Path
    resolved_path: Path
    name: str
    model: ScannerModel | None = None

    def __str__(self) -> str:
        return str(self.path)


def discover_scanners(
    directory: Path = DEFAULT_BY_ID_DIRECTORY,
    pattern: str = DEFAULT_SDS_PATTERN,
    *,
    model: ScannerModel | str | None = None,
) -> list[ScannerDevice]:
    """Discover USB serial devices for supported SDS-series scanners."""

    requested_model = normalize_model_name(model) if model is not None else None
    if model is not None and requested_model is None:
        raise ValueError(f"Unsupported SDS-series scanner model: {model!r}")
    if not directory.exists():
        return []

    devices: list[ScannerDevice] = []
    for path in sorted(directory.glob(pattern)):
        inferred_model = infer_model_from_device_name(path.name)
        if requested_model is not None and inferred_model != requested_model:
            continue
        try:
            resolved = path.resolve(strict=True)
        except FileNotFoundError:
            continue
        devices.append(
            ScannerDevice(
                path=path,
                resolved_path=resolved,
                name=path.name,
                model=inferred_model,
            )
        )
    return devices


def choose_scanner(
    explicit_port: str | Path | None = None,
    *,
    candidates: Iterable[ScannerDevice] | None = None,
    model: ScannerModel | str | None = None,
) -> Path:
    if explicit_port is not None:
        return Path(explicit_port)

    requested_model = normalize_model_name(model) if model is not None else None
    if model is not None and requested_model is None:
        raise ValueError(f"Unsupported SDS-series scanner model: {model!r}")

    found = list(candidates) if candidates is not None else discover_scanners(model=model)
    if requested_model is not None:
        found = [device for device in found if device.model == requested_model]
    if not found:
        suffix = f" matching {requested_model}" if requested_model is not None else ""
        raise ScannerNotFoundError(
            f"No supported SDS-series scanner{suffix} was found. Supply a port "
            "explicitly or verify /dev/serial/by-id contains a Uniden SDS100, "
            "SDS150, or SDS200 serial device."
        )
    return found[0].path
