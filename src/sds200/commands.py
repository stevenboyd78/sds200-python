from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, TypeVar

from .exceptions import (
    CommandRejectedError,
    ProtocolError,
    ScannerRecordingControlError,
)
from .models import (
    AnalysisMode,
    AnalysisResponse,
    ChargeStatus,
    FavoritesQuickKeys,
    FavoritesQuickKeyState,
    FirmwareResponse,
    GltResponse,
    ModelResponse,
    MsiResponse,
    Packet,
    ScannerInfo,
    ScannerRecordingStatus,
    ScannerRecordingStatusResponse,
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
class GetGltFavorites:
    @property
    def wire(self) -> str:
        return "GLT,FL"

    @property
    def response_command(self) -> str:
        return "GLT"

    def parse_response(self, response: object) -> GltResponse:
        if not isinstance(response, GltResponse):
            raise TypeError("GLT did not return GltResponse")
        return response


@dataclass(frozen=True, slots=True)
class GetMsi:
    @property
    def wire(self) -> str:
        return "MSI"

    @property
    def response_command(self) -> str:
        return "MSI"

    def parse_response(self, response: object) -> MsiResponse:
        if not isinstance(response, MsiResponse):
            raise TypeError("MSI did not return MsiResponse")
        return response


IndexedMenuId = Literal[
    "SCAN_SYSTEM",
    "SCAN_DEPARTMENT",
    "SCAN_SITE",
    "SCAN_CHANNEL",
    "SRCH_RANGE",
    "FTO_CHANNEL",
]
INDEXED_MENU_IDS: tuple[IndexedMenuId, ...] = (
    "SCAN_SYSTEM",
    "SCAN_DEPARTMENT",
    "SCAN_SITE",
    "SCAN_CHANNEL",
    "SRCH_RANGE",
    "FTO_CHANNEL",
)

RfPowerPlotModulation = Literal["Auto", "AM", "NFM", "FM", "WFM", "FMB"]
RF_POWER_PLOT_MODULATIONS: tuple[RfPowerPlotModulation, ...] = (
    "Auto",
    "AM",
    "NFM",
    "FM",
    "WFM",
    "FMB",
)
RfPowerPlotSamplingRate = Literal[100, 200, 400, 800]
RF_POWER_PLOT_SAMPLING_RATES: tuple[RfPowerPlotSamplingRate, ...] = (
    100,
    200,
    400,
    800,
)


def _indexed_menu_id(value: str) -> IndexedMenuId:
    for menu_id in INDEXED_MENU_IDS:
        if value == menu_id:
            return menu_id
    choices = ", ".join(INDEXED_MENU_IDS)
    raise ValueError(f"Indexed MNU menu ID must be one of: {choices}.")


def _menu_index(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("MNU index must be a non-empty string.")
    if value != value.strip():
        raise ValueError("MNU index cannot have leading or trailing whitespace.")
    if any(delimiter in value for delimiter in (",", "\r", "\n")):
        raise ValueError("MNU index cannot contain commas or line breaks.")
    return value


@dataclass(frozen=True, slots=True)
class OpenIndexedMenu:
    # Open one specification-backed indexed menu using an opaque index token.
    menu_id: IndexedMenuId
    index: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "menu_id", _indexed_menu_id(self.menu_id))
        object.__setattr__(self, "index", _menu_index(self.index))

    @property
    def wire(self) -> str:
        return f"MNU,{self.menu_id},{self.index}"

    @property
    def response_command(self) -> str:
        return "MNU"

    def parse_response(self, response: object) -> None:
        if not isinstance(response, Packet) or response.command != "MNU":
            raise ProtocolError("MNU returned an unexpected response.")
        if response.fields != ("OK",):
            raise ProtocolError("MNU acknowledgement must be exactly MNU,OK.")


def _require_site_index(site_index: int) -> None:
    """Validate the host/API boundary, without claiming a protocol upper range."""
    if isinstance(site_index, bool) or not isinstance(site_index, int):
        raise ValueError("AST site index must be a non-negative integer.")
    if site_index < 0:
        raise ValueError("AST site index must be a non-negative integer.")


def _rf_power_plot_frequency(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("AST RF_POWER_PLOT frequency must be an integer.")
    if not 250000 <= value <= 13000000:
        raise ValueError(
            "AST RF_POWER_PLOT frequency must be between 250000 and 13000000."
        )
    return value


def _rf_power_plot_modulation(value: str) -> RfPowerPlotModulation:
    if not isinstance(value, str):
        raise ValueError(
            "AST RF_POWER_PLOT modulation must be one of: "
            + ", ".join(RF_POWER_PLOT_MODULATIONS)
            + "."
        )
    for modulation in RF_POWER_PLOT_MODULATIONS:
        if value == modulation:
            return modulation
    raise ValueError(
        "AST RF_POWER_PLOT modulation must be one of: "
        + ", ".join(RF_POWER_PLOT_MODULATIONS)
        + "."
    )


def _rf_power_plot_sampling_rate(value: int) -> RfPowerPlotSamplingRate:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            "AST RF_POWER_PLOT sampling rate must be one of: 100, 200, 400, 800."
        )
    for sampling_rate in RF_POWER_PLOT_SAMPLING_RATES:
        if value == sampling_rate:
            return sampling_rate
    raise ValueError(
        "AST RF_POWER_PLOT sampling rate must be one of: 100, 200, 400, 800."
    )


@dataclass(frozen=True, slots=True)
class StartRfPowerPlotAnalysis:
    """Start the documented RF Power Plot mode and require exact AST,OK."""

    frequency: int
    modulation: RfPowerPlotModulation
    sampling_rate: RfPowerPlotSamplingRate

    def __post_init__(self) -> None:
        object.__setattr__(self, "frequency", _rf_power_plot_frequency(self.frequency))
        object.__setattr__(
            self, "modulation", _rf_power_plot_modulation(self.modulation)
        )
        object.__setattr__(
            self, "sampling_rate", _rf_power_plot_sampling_rate(self.sampling_rate)
        )

    @property
    def wire(self) -> str:
        return (
            f"AST,RF_POWER_PLOT,{self.frequency},"
            f"{self.modulation},{self.sampling_rate}"
        )

    @property
    def response_command(self) -> str:
        return "AST"

    def parse_response(self, response: object) -> None:
        if not isinstance(response, Packet) or response.command != "AST":
            raise ProtocolError("AST RF_POWER_PLOT returned an unexpected response.")
        if response.fields != ("OK",):
            raise ProtocolError(
                "AST RF_POWER_PLOT acknowledgement must be exactly AST,OK."
            )


@dataclass(frozen=True, slots=True)
class StartSystemStatusAnalysis:
    """Start the documented System Status analysis mode and require exact AST,OK."""

    site_index: int

    def __post_init__(self) -> None:
        _require_site_index(self.site_index)

    @property
    def wire(self) -> str:
        return f"AST,SYSTEM_STATUS,{self.site_index}"

    @property
    def response_command(self) -> str:
        return "AST"

    def parse_response(self, response: object) -> None:
        if not isinstance(response, Packet) or response.command != "AST":
            raise ProtocolError("AST SYSTEM_STATUS returned an unexpected response.")
        if response.fields != ("OK",):
            raise ProtocolError(
                "AST SYSTEM_STATUS acknowledgement must be exactly AST,OK."
            )


@dataclass(frozen=True, slots=True)
class StartCurrentActivityAnalysis:
    site_index: int

    def __post_init__(self) -> None:
        _require_site_index(self.site_index)

    @property
    def wire(self) -> str:
        return f"AST,CURRENT_ACTIVITY,{self.site_index}"

    @property
    def response_command(self) -> str:
        return "AST"

    def parse_response(self, response: object) -> AnalysisResponse:
        if not isinstance(response, AnalysisResponse):
            raise TypeError("AST did not return AnalysisResponse")
        return response


@dataclass(frozen=True, slots=True)
class StartLcnMonitorAnalysis:
    site_index: int

    def __post_init__(self) -> None:
        _require_site_index(self.site_index)

    @property
    def wire(self) -> str:
        return f"AST,LCN_MONITOR,{self.site_index}"

    @property
    def response_command(self) -> str:
        return "AST"

    def parse_response(self, response: object) -> AnalysisResponse:
        if not isinstance(response, AnalysisResponse):
            raise TypeError("AST did not return AnalysisResponse")
        return response


@dataclass(frozen=True, slots=True)
class PauseResumeAnalysis:
    mode: AnalysisMode

    def __post_init__(self) -> None:
        if not isinstance(self.mode, AnalysisMode):
            raise ValueError("APR mode must be an AnalysisMode value.")

    @property
    def wire(self) -> str:
        return f"APR,{self.mode.value}"

    @property
    def response_command(self) -> str:
        return "APR"

    def parse_response(self, response: object) -> None:
        if not isinstance(response, Packet) or response.command != "APR":
            raise ProtocolError("APR returned an unexpected response.")
        if response.fields == ("OK",):
            return
        if response.fields in {("NG",), ("ERR",), ("ERROR",)}:
            _parse_acknowledgement(response, "APR")
        raise ProtocolError("APR acknowledgement must be exactly APR,OK.")


@dataclass(frozen=True, slots=True)
class GetFavoritesQuickKeys:
    @property
    def wire(self) -> str:
        return "FQK"

    @property
    def response_command(self) -> str:
        return "FQK"

    def parse_response(self, response: object) -> FavoritesQuickKeys:
        if not isinstance(response, Packet) or response.command != "FQK":
            raise ProtocolError("FQK read returned an unexpected response.")
        if len(response.fields) != 100:
            raise ProtocolError("FQK read must return exactly 100 status fields.")
        if any(field not in {"0", "1", "2"} for field in response.fields):
            raise ProtocolError("FQK read returned an invalid status field.")
        return FavoritesQuickKeys(
            states=tuple(FavoritesQuickKeyState(int(field)) for field in response.fields),
            packet=response,
        )


@dataclass(frozen=True, slots=True, init=False)
class SetFavoritesQuickKeys:
    """Set 100 FQK states; controller status 0 is scanner-ignored by specification."""

    states: tuple[FavoritesQuickKeyState, ...]

    def __init__(self, states: Sequence[int | FavoritesQuickKeyState]) -> None:
        if len(states) != 100:
            raise ValueError("FQK write requires exactly 100 states.")
        normalized: list[FavoritesQuickKeyState] = []
        for state in states:
            if isinstance(state, bool) or not isinstance(state, int):
                raise ValueError("FQK states must be integers 0, 1, or 2.")
            try:
                normalized.append(FavoritesQuickKeyState(state))
            except ValueError as exc:
                raise ValueError("FQK states must be integers 0, 1, or 2.") from exc
        object.__setattr__(self, "states", tuple(normalized))

    @property
    def wire(self) -> str:
        return "FQK," + ",".join(str(int(state)) for state in self.states)

    @property
    def response_command(self) -> str:
        return "FQK"

    def parse_response(self, response: object) -> None:
        if not isinstance(response, Packet) or response.command != "FQK":
            raise ProtocolError("FQK write returned an unexpected response.")
        if len(response.fields) != 1:
            raise ProtocolError("FQK write acknowledgement must contain exactly one field.")
        if response.fields == ("OK",):
            return
        _parse_acknowledgement(response, "FQK")
        raise ProtocolError("FQK write acknowledgement must be exactly FQK,OK.")


def _raise_scanner_recording_error(response: Packet) -> None:
    if not response.fields or response.fields[0] != "ERR":
        return
    if (
        len(response.fields) != 2
        or len(response.fields[1]) != 4
        or not response.fields[1].isascii()
        or not response.fields[1].isdigit()
    ):
        raise ProtocolError("URC returned a malformed operation error.")
    raise ScannerRecordingControlError(response.fields[1])


@dataclass(frozen=True, slots=True)
class GetScannerRecordingStatus:
    @property
    def wire(self) -> str:
        return "URC"

    @property
    def response_command(self) -> str:
        return "URC"

    def parse_response(self, response: object) -> ScannerRecordingStatusResponse:
        if not isinstance(response, Packet) or response.command != "URC":
            raise ProtocolError("URC read returned an unexpected response.")
        _raise_scanner_recording_error(response)
        if len(response.fields) != 1 or response.fields[0] not in {"0", "1"}:
            raise ProtocolError("URC read must return exactly one valid status field.")
        return ScannerRecordingStatusResponse(
            status=ScannerRecordingStatus(int(response.fields[0])),
            packet=response,
        )


@dataclass(frozen=True, slots=True, init=False)
class SetScannerRecordingStatus:
    status: ScannerRecordingStatus

    def __init__(self, status: int | ScannerRecordingStatus) -> None:
        if isinstance(status, bool) or not isinstance(status, int):
            raise ValueError("URC status must be integer 0 or 1.")
        try:
            normalized = ScannerRecordingStatus(status)
        except ValueError as exc:
            raise ValueError("URC status must be integer 0 or 1.") from exc
        object.__setattr__(self, "status", normalized)

    @property
    def wire(self) -> str:
        return f"URC,{int(self.status)}"

    @property
    def response_command(self) -> str:
        return "URC"

    def parse_response(self, response: object) -> None:
        if not isinstance(response, Packet) or response.command != "URC":
            raise ProtocolError("URC write returned an unexpected response.")
        _raise_scanner_recording_error(response)
        if response.fields == ("OK",):
            return
        if response.fields in {("NG",), ("ERROR",)}:
            _parse_acknowledgement(response, "URC")
        raise ProtocolError("URC write acknowledgement must be exactly URC,OK.")


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
                raise CommandRejectedError(
                    f"Scanner rejected PSI command: {response.raw}"
                )
            return None
        raise ProtocolError(
            f"PSI returned an unexpected response: {type(response).__name__}"
        )


HoldKeyCode = Literal["F", "A", "B", "C"]
HOLD_KEY_CODES: tuple[HoldKeyCode, ...] = ("F", "A", "B", "C")


def _hold_key_code(value: str) -> str:
    normalized = value.strip().upper()
    if normalized not in HOLD_KEY_CODES:
        choices = ", ".join(HOLD_KEY_CODES)
        raise ValueError(f"Hold-related key code must be one of: {choices}.")
    return normalized


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
class PressKey:
    """Press one allowlisted SDS hold-related front-panel key."""

    key_code: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "key_code", _hold_key_code(self.key_code))

    @property
    def wire(self) -> str:
        # Current SDS specifications require KEY_MODE but do not define its
        # values. Long-standing Uniden remote protocols define P as one press.
        return f"KEY,{self.key_code},P"

    @property
    def response_command(self) -> str:
        return "KEY"

    def parse_response(self, response: object) -> None:
        _parse_acknowledgement(response, "KEY")


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
