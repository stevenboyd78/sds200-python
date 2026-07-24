from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ScannerModel = Literal["SDS100", "SDS150", "SDS200"]
SUPPORTED_SCANNER_MODELS: tuple[ScannerModel, ...] = ("SDS100", "SDS150", "SDS200")


@dataclass(frozen=True, slots=True)
class ScannerCapabilities:
    """Model-specific limits and transport capabilities."""

    model: ScannerModel
    serial_control: bool
    network_control: bool
    charge_status: bool
    maximum_volume: int
    maximum_squelch: int


_CAPABILITIES: dict[ScannerModel, ScannerCapabilities] = {
    "SDS100": ScannerCapabilities(
        model="SDS100",
        serial_control=True,
        network_control=False,
        charge_status=True,
        maximum_volume=15,
        maximum_squelch=15,
    ),
    "SDS150": ScannerCapabilities(
        model="SDS150",
        serial_control=True,
        network_control=False,
        charge_status=True,
        maximum_volume=15,
        maximum_squelch=15,
    ),
    "SDS200": ScannerCapabilities(
        model="SDS200",
        serial_control=True,
        network_control=True,
        charge_status=False,
        maximum_volume=29,
        maximum_squelch=19,
    ),
}

_MODEL_ALIASES: dict[str, ScannerModel] = {
    "SDS100": "SDS100",
    "UB383Z": "SDS100",
    "SDS150": "SDS150",
    "SDS150GBT": "SDS150",
    "UB391Z": "SDS150",
    "SDS200": "SDS200",
    "UB384Z": "SDS200",
}


def normalize_model_name(value: str) -> ScannerModel | None:
    """Normalize a scanner-reported or user-supplied model name.

    SDS150 firmware reports ``SDS150GBT`` through the MDL command. Public APIs
    expose the retail model name ``SDS150`` while the raw packet remains
    available through :class:`sds200.models.ModelResponse`.
    """

    normalized = "".join(character for character in value.upper() if character.isalnum())
    return _MODEL_ALIASES.get(normalized)


def capabilities_for_model(model: ScannerModel | str) -> ScannerCapabilities:
    normalized = normalize_model_name(model)
    if normalized is None:
        raise ValueError(f"Unsupported SDS-series scanner model: {model!r}")
    return _CAPABILITIES[normalized]


def infer_model_from_device_name(name: str) -> ScannerModel | None:
    normalized = "".join(character for character in name.upper() if character.isalnum())
    for alias in ("SDS150GBT", "SDS150", "SDS200", "SDS100"):
        if alias in normalized:
            return _MODEL_ALIASES[alias]
    return None
