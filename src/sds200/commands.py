from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeVar

from .models import (
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

    def __post_init__(self) -> None:
        if not 0 <= self.level <= 29:
            raise ValueError("SDS200 volume must be between 0 and 29.")

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

    def __post_init__(self) -> None:
        if not 0 <= self.level <= 19:
            raise ValueError("SDS200 squelch must be between 0 and 19.")

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

    def parse_response(self, response: object) -> ScannerInfo:
        if not isinstance(response, ScannerInfo):
            raise TypeError("PSI did not return ScannerInfo")
        return response
