from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType


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


@dataclass(frozen=True, slots=True)
class FirmwareResponse:
    version: str
    packet: Packet


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
class RadioHealth:
    endpoint: str
    connected: bool
    model: str
    firmware: str
    latency_ms: float
    statistics: Mapping[str, object]

    @classmethod
    def create(
        cls,
        *,
        endpoint: str,
        connected: bool,
        model: str,
        firmware: str,
        latency_ms: float,
        statistics: Mapping[str, object] | None = None,
    ) -> RadioHealth:
        return cls(
            endpoint=endpoint,
            connected=connected,
            model=model,
            firmware=firmware,
            latency_ms=latency_ms,
            statistics=MappingProxyType(dict(statistics or {})),
        )
