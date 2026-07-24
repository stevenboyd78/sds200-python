from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType


def _empty_mapping() -> Mapping[str, object]:
    return MappingProxyType({})


@dataclass(frozen=True, slots=True)
class Packet:
    command: str
    fields: tuple[str, ...]
    raw: str
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class ModelResponse:
    model: str
    packet: Packet
    reported_model: str | None = None


@dataclass(frozen=True, slots=True)
class FirmwareResponse:
    version: str
    packet: Packet


@dataclass(frozen=True, slots=True)
class ChargeStatus:
    status_code: int
    status: str
    voltage_mv: int
    capacity_percent: int
    current_ma: int
    temperature_c: float
    packet: Packet

    @property
    def charging(self) -> bool:
        return self.status_code in {5, 6}


@dataclass(frozen=True, slots=True)
class ValueResponse:
    command: str
    value: int
    packet: Packet


@dataclass(frozen=True, slots=True)
class DisplayLine:
    text: str
    mode: str


@dataclass(frozen=True, slots=True)
class StatusResponse:
    display_form: str
    lines: tuple[DisplayLine, ...]
    reserved: tuple[str, ...]
    packet: Packet


@dataclass(frozen=True, slots=True)
class ScannerNode:
    tag: str
    attributes: Mapping[str, str]

    @classmethod
    def create(cls, tag: str, attributes: dict[str, str]) -> ScannerNode:
        return cls(tag=tag, attributes=MappingProxyType(dict(attributes)))

    def get(self, name: str, default: str | None = None) -> str | None:
        return self.attributes.get(name, default)


@dataclass(frozen=True, slots=True)
class ScannerInfo:
    command: str
    mode: str | None
    screen: str | None
    nodes: Mapping[str, ScannerNode]
    raw_xml: str
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def node(self, tag: str) -> ScannerNode | None:
        return self.nodes.get(tag)

    def _attribute(self, tags: tuple[str, ...], name: str) -> str | None:
        for tag in tags:
            node = self.node(tag)
            if node is None:
                continue
            value = node.get(name)
            if value is not None:
                return value.strip()
        return None

    def _property(self, name: str) -> str | None:
        return self._attribute(("Property",), name)

    @staticmethod
    def _integer(value: str | None) -> int | None:
        try:
            return int(value) if value is not None else None
        except ValueError:
            return None

    @staticmethod
    def _floating(value: str | None) -> float | None:
        try:
            return float(value) if value is not None else None
        except ValueError:
            return None

    @property
    def system(self) -> str | None:
        return self._attribute(("System",), "Name")

    @property
    def department(self) -> str | None:
        return self._attribute(("Department",), "Name")

    @property
    def site(self) -> str | None:
        return self._attribute(("Site",), "Name")

    @property
    def channel(self) -> str | None:
        return self._attribute(
            (
                "ConvFrequency",
                "TGID",
                "SrchFrequency",
                "CcHitsChannel",
                "ToneOutChannel",
                "WxChannel",
            ),
            "Name",
        )

    @property
    def frequency(self) -> str | None:
        return self._attribute(
            (
                "ConvFrequency",
                "SiteFrequency",
                "SrchFrequency",
                "CcHitsChannel",
                "ToneOutChannel",
                "WxChannel",
            ),
            "Freq",
        )

    @property
    def modulation(self) -> str | None:
        value = self._attribute(
            (
                "ConvFrequency",
                "Site",
                "SrchFrequency",
                "CcHitsChannel",
                "ToneOutChannel",
                "WxChannel",
            ),
            "Mod",
        )
        if value is not None:
            return value
        status = self.p25_status
        return status if status not in (None, "None", "Data") else None

    @property
    def service_type(self) -> str | None:
        return self._attribute(("ConvFrequency", "TGID"), "SvcType")

    @property
    def talkgroup_id(self) -> str | None:
        return self._attribute(("TGID", "ConvFrequency", "SrchFrequency"), "TGID")

    @property
    def unit_id(self) -> str | None:
        return self._attribute(("TGID", "ConvFrequency", "SrchFrequency"), "U_Id")

    @property
    def volume(self) -> int | None:
        return self._integer(self._property("VOL"))

    @property
    def squelch(self) -> int | None:
        return self._integer(self._property("SQL"))

    @property
    def signal(self) -> int | None:
        return self._integer(self._property("Sig"))

    @property
    def rssi(self) -> float | None:
        return self._floating(self._property("Rssi"))

    @property
    def p25_status(self) -> str | None:
        return self._property("P25Status")

    @property
    def mute(self) -> str | None:
        return self._property("Mute")

    @property
    def recording(self) -> str | None:
        return self._property("Rec")


@dataclass(frozen=True, slots=True)
class RadioEvent:
    kind: str
    message: str
    endpoint: str | None = None
    observed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    data: Mapping[str, object] = field(default_factory=_empty_mapping)

    @classmethod
    def create(
        cls,
        kind: str,
        message: str,
        *,
        endpoint: str | None = None,
        observed_at: datetime | None = None,
        data: Mapping[str, object] | None = None,
    ) -> RadioEvent:
        return cls(
            kind=kind,
            message=message,
            endpoint=endpoint,
            observed_at=observed_at or datetime.now(UTC),
            data=MappingProxyType(dict(data or {})),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "message": self.message,
            "endpoint": self.endpoint,
            "observed_at": self.observed_at.isoformat(),
            "data": dict(self.data),
        }


@dataclass(frozen=True, slots=True)
class HealthSummary:
    samples: int
    healthy_samples: int
    degraded_samples: int
    unhealthy_samples: int
    disconnected_samples: int
    error_rate: float
    average_latency_ms: float | None
    maximum_latency_ms: float | None
    first_checked_at: datetime | None
    last_checked_at: datetime | None
    connection_events_delta: int
    reconnects: int
    failovers: int
    recent_errors: tuple[str, ...]

    @classmethod
    def empty(cls) -> HealthSummary:
        return cls(
            samples=0,
            healthy_samples=0,
            degraded_samples=0,
            unhealthy_samples=0,
            disconnected_samples=0,
            error_rate=0.0,
            average_latency_ms=None,
            maximum_latency_ms=None,
            first_checked_at=None,
            last_checked_at=None,
            connection_events_delta=0,
            reconnects=0,
            failovers=0,
            recent_errors=(),
        )

    @classmethod
    def create(
        cls,
        *,
        samples: int,
        healthy_samples: int,
        degraded_samples: int,
        unhealthy_samples: int,
        disconnected_samples: int,
        error_rate: float,
        average_latency_ms: float | None,
        maximum_latency_ms: float | None,
        first_checked_at: datetime | None,
        last_checked_at: datetime | None,
        connection_events_delta: int,
        reconnects: int,
        failovers: int,
        recent_errors: tuple[str, ...],
    ) -> HealthSummary:
        return cls(
            samples=samples,
            healthy_samples=healthy_samples,
            degraded_samples=degraded_samples,
            unhealthy_samples=unhealthy_samples,
            disconnected_samples=disconnected_samples,
            error_rate=error_rate,
            average_latency_ms=average_latency_ms,
            maximum_latency_ms=maximum_latency_ms,
            first_checked_at=first_checked_at,
            last_checked_at=last_checked_at,
            connection_events_delta=connection_events_delta,
            reconnects=reconnects,
            failovers=failovers,
            recent_errors=recent_errors,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "samples": self.samples,
            "healthy_samples": self.healthy_samples,
            "degraded_samples": self.degraded_samples,
            "unhealthy_samples": self.unhealthy_samples,
            "disconnected_samples": self.disconnected_samples,
            "error_rate": self.error_rate,
            "average_latency_ms": self.average_latency_ms,
            "maximum_latency_ms": self.maximum_latency_ms,
            "first_checked_at": (
                self.first_checked_at.isoformat()
                if self.first_checked_at is not None
                else None
            ),
            "last_checked_at": (
                self.last_checked_at.isoformat()
                if self.last_checked_at is not None
                else None
            ),
            "connection_events_delta": self.connection_events_delta,
            "reconnects": self.reconnects,
            "failovers": self.failovers,
            "recent_errors": list(self.recent_errors),
        }


@dataclass(frozen=True, slots=True)
class RadioHealth:
    status: str
    endpoint: str
    connected: bool
    model: str | None
    firmware: str | None
    latency_ms: float | None
    checked_at: datetime
    connection_events: int
    last_connected_at: datetime | None
    last_disconnected_at: datetime | None
    last_response_at: datetime | None
    last_state_at: datetime | None
    psi_active: bool
    psi_interval_ms: int | None
    error: str | None
    statistics: Mapping[str, object]

    @classmethod
    def create(
        cls,
        *,
        endpoint: str,
        connected: bool,
        model: str | None,
        firmware: str | None,
        latency_ms: float | None,
        status: str | None = None,
        connection_events: int = 0,
        last_connected_at: datetime | None = None,
        last_disconnected_at: datetime | None = None,
        last_response_at: datetime | None = None,
        last_state_at: datetime | None = None,
        psi_active: bool = False,
        psi_interval_ms: int | None = None,
        error: str | None = None,
        statistics: Mapping[str, object] | None = None,
        checked_at: datetime | None = None,
    ) -> RadioHealth:
        resolved_status = status or ("healthy" if connected and error is None else "degraded")
        return cls(
            status=resolved_status,
            endpoint=endpoint,
            connected=connected,
            model=model,
            firmware=firmware,
            latency_ms=latency_ms,
            checked_at=checked_at or datetime.now(UTC),
            connection_events=connection_events,
            last_connected_at=last_connected_at,
            last_disconnected_at=last_disconnected_at,
            last_response_at=last_response_at,
            last_state_at=last_state_at,
            psi_active=psi_active,
            psi_interval_ms=psi_interval_ms,
            error=error,
            statistics=MappingProxyType(dict(statistics or {})),
        )

    def as_dict(self) -> dict[str, object]:
        def timestamp(value: datetime | None) -> str | None:
            return value.isoformat() if value is not None else None

        return {
            "status": self.status,
            "endpoint": self.endpoint,
            "connected": self.connected,
            "model": self.model,
            "firmware": self.firmware,
            "latency_ms": self.latency_ms,
            "checked_at": self.checked_at.isoformat(),
            "connection_events": self.connection_events,
            "last_connected_at": timestamp(self.last_connected_at),
            "last_disconnected_at": timestamp(self.last_disconnected_at),
            "last_response_at": timestamp(self.last_response_at),
            "last_state_at": timestamp(self.last_state_at),
            "psi_active": self.psi_active,
            "psi_interval_ms": self.psi_interval_ms,
            "error": self.error,
            "statistics": dict(self.statistics),
        }
