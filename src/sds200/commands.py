from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, TypeVar

from .exceptions import CommandRejectedError, ProtocolError
from .models import (
    ChargeStatus,
    FirmwareResponse,
    ModelResponse,
    Packet,
    ScannerInfo,
    StatusResponse,
    ValueResponse,
)

T = TypeVar("T", covariant=True)


class Command(Protocol[T]):
    @property
    def wire(self) -> str: ...

    @property
    def response_command(self) -> str: ...

    def parse_response(self, response: object) -> T: ...


@dataclass(frozen=True, slots=True)
class GetModel:
    @property
    def wire(self) -> str:
        return "MDL"

    @property
    def response_command(self) -> str:
        return "MDL"

    def parse_response(self, response: object) -> str:
        if not isinstance(response, ModelResponse):
            raise TypeError("MDL did not return ModelResponse")
        return response.model


@dataclass(frozen=True, slots=True)
class GetFirmware:
    @property
    def wire(self) -> str:
        return "VER"

    @property
    def response_command(self) -> str:
        return "VER"

    def parse_response(self, response: object) -> str:
        if not isinstance(response, FirmwareResponse):
            raise TypeError("VER did not return FirmwareResponse")
        return response.version


@dataclass(frozen=True, slots=True)
class GetChargeStatus:
    @property
    def wire(self) -> str:
        return "GCS"

    @property
    def response_command(self) -> str:
        return "GCS"

    def parse_response(self, response: object) -> ChargeStatus:
        if not isinstance(response, ChargeStatus):
            raise TypeError("GCS did not return ChargeStatus")
        return response


@dataclass(frozen=True, slots=True)
class GetVolume:
    @property
    def wire(self) -> str:
        return "VOL"

    @property
    def response_command(self) -> str:
        return "VOL"

    def parse_response(self, response: object) -> int:
        if not isinstance(response, ValueResponse):
            raise TypeError("VOL did not return ValueResponse")
        return response.value


@dataclass(frozen=True, slots=True)
class SetVolume:
    level: int
    maximum: int = 29

    def __post_init__(self) -> None:
        if self.maximum <= 0:
            raise ValueError("Maximum volume must be positive.")
        if not 0 <= self.level <= self.maximum:
            raise ValueError(
                f"SDS-series volume must be between 0 and {self.maximum}."
            )

    @property
    def wire(self) -> str:
        return f"VOL,{self.level}"

    @property
    def response_command(self) -> str:
        return "VOL"

    def parse_response(self, response: object) -> None:
        if not isinstance(response, (Packet, ValueResponse)):
            raise TypeError("VOL set returned an unexpected response")
        return None


@dataclass(frozen=True, slots=True)
class GetSquelch:
    @property
    def wire(self) -> str:
        return "SQL"

    @property
    def response_command(self) -> str:
        return "SQL"

    def parse_response(self, response: object) -> int:
        if not isinstance(response, ValueResponse):
            raise TypeError("SQL did not return ValueResponse")
        return response.value


@dataclass(frozen=True, slots=True)
class SetSquelch:
    level: int
    maximum: int = 19

    def __post_init__(self) -> None:
        if self.maximum <= 0:
            raise ValueError("Maximum squelch must be positive.")
        if not 0 <= self.level <= self.maximum:
            raise ValueError(
                f"SDS-series squelch must be between 0 and {self.maximum}."
            )

    @property
    def wire(self) -> str:
        return f"SQL,{self.level}"

    @property
    def response_command(self) -> str:
        return "SQL"

    def parse_response(self, response: object) -> None:
        if not isinstance(response, (Packet, ValueResponse)):
            raise TypeError("SQL set returned an unexpected response")
        return None


@dataclass(frozen=True, slots=True)
class GetStatus:
    @property
    def wire(self) -> str:
        return "STS"

    @property
    def response_command(self) -> str:
        return "STS"

    def parse_response(self, response: object) -> StatusResponse:
        if not isinstance(response, StatusResponse):
            raise TypeError("STS did not return StatusResponse")
        return response


@dataclass(frozen=True, slots=True)
class GetScannerInfo:
    @property
    def wire(self) -> str:
        return "GSI"

    @property
    def response_command(self) -> str:
        return "GSI"

    def parse_response(self, response: object) -> ScannerInfo:
        if not isinstance(response, ScannerInfo):
            raise TypeError("GSI did not return ScannerInfo")
        return response


@dataclass(frozen=True, slots=True)
class StartScannerInfoPush:
    interval_ms: int = 500

    def __post_init__(self) -> None:
        if self.interval_ms <= 0:
            raise ValueError("PSI interval must be positive.")

    @property
    def wire(self) -> str:
        return f"PSI,{self.interval_ms}"

    @property
    def response_command(self) -> str:
        return "PSI"

    def parse_response(self, response: object) -> ScannerInfo | None:
        if isinstance(response, ScannerInfo):
            return response
        if isinstance(response, Packet) and response.command == "PSI":
            status = response.fields[0].strip().upper() if response.fields else ""
            if status in {"NG", "ERR", "ERROR"}:
                raise ProtocolError(f"Scanner rejected PSI command: {response.raw}")
            return None
        raise ProtocolError(
            f"PSI returned an unexpected response: {type(response).__name__}"
        )


NavigationTarget = Literal[
    "SYS",
    "DEPT",
    "SITE",
    "CFREQ",
    "TGID",
    "STGID",
    "WX",
    "FTO",
    "CCHIT",
    "CS_FREQ",
    "QS_FREQ",
]
NAVIGATION_TARGETS: tuple[NavigationTarget, ...] = (
    "SYS",
    "DEPT",
    "SITE",
    "CFREQ",
    "TGID",
    "STGID",
    "WX",
    "FTO",
    "CCHIT",
    "CS_FREQ",
    "QS_FREQ",
)


def _navigation_target(value: str) -> NavigationTarget:
    normalized = value.strip().upper()
    if normalized not in NAVIGATION_TARGETS:
        choices = ", ".join(NAVIGATION_TARGETS)
        raise ValueError(f"Navigation target must be one of: {choices}.")
    return normalized


def _navigation_value(value: str | int | None) -> str:
    if value is None:
        return ""
    normalized = str(value).strip()
    if any(delimiter in normalized for delimiter in (",", "\r", "\n")):
        raise ValueError("Navigation values cannot contain commas or line breaks.")
    return normalized


def _parse_acknowledgement(response: object, command: str) -> None:
    if not isinstance(response, Packet) or response.command != command:
        raise ProtocolError(f"{command} returned an unexpected response.")
    status = response.fields[0].strip().upper() if response.fields else ""
    if status == "OK":
        return
    if status in {"NG", "ERR", "ERROR"}:
        raise CommandRejectedError(
            f"Scanner rejected {command} command: {response.raw}"
        )
    raise ProtocolError(f"{command} did not return OK: {response.raw}")


@dataclass(frozen=True, slots=True)
class HoldSelection:
    """Hold a documented scanner selection by protocol target and indexes."""

    target: str
    first: str | int | None = None
    second: str | int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "target", _navigation_target(self.target))

    @property
    def wire(self) -> str:
        return ",".join(
            ("HLD", self.target, _navigation_value(self.first), _navigation_value(self.second))
        )

    @property
    def response_command(self) -> str:
        return "HLD"

    def parse_response(self, response: object) -> None:
        _parse_acknowledgement(response, "HLD")


@dataclass(frozen=True, slots=True)
class NextSelection:
    """Move forward through a documented scanner selection list."""

    target: str
    first: str | int | None = None
    second: str | int | None = None
    count: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "target", _navigation_target(self.target))
        if not 1 <= self.count <= 8:
            raise ValueError("Navigation count must be between 1 and 8.")

    @property
    def wire(self) -> str:
        return ",".join(
            (
                "NXT",
                self.target,
                _navigation_value(self.first),
                _navigation_value(self.second),
                str(self.count),
            )
        )

    @property
    def response_command(self) -> str:
        return "NXT"

    def parse_response(self, response: object) -> None:
        _parse_acknowledgement(response, "NXT")


@dataclass(frozen=True, slots=True)
class PreviousSelection:
    """Move backward through a documented scanner selection list."""

    target: str
    first: str | int | None = None
    second: str | int | None = None
    count: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "target", _navigation_target(self.target))
        if not 1 <= self.count <= 8:
            raise ValueError("Navigation count must be between 1 and 8.")

    @property
    def wire(self) -> str:
        return ",".join(
            (
                "PRV",
                self.target,
                _navigation_value(self.first),
                _navigation_value(self.second),
                str(self.count),
            )
        )

    @property
    def response_command(self) -> str:
        return "PRV"

    def parse_response(self, response: object) -> None:
        _parse_acknowledgement(response, "PRV")
