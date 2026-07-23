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

    @property
    def system(self) -> str | None:
        node = self.node("System")
        return node.get("Name") if node else None

    @property
    def department(self) -> str | None:
        node = self.node("Department")
        return node.get("Name") if node else None

    @property
    def channel(self) -> str | None:
        for tag in ("ConvFrequency", "TGID", "SrchFrequency", "CcHitsChannel"):
            node = self.node(tag)
            if node:
                return node.get("Name")
        return None

    @property
    def frequency(self) -> str | None:
        for tag in ("ConvFrequency", "SiteFrequency", "SrchFrequency"):
            node = self.node(tag)
            if node:
                return node.get("Freq")
        return None

    @property
    def modulation(self) -> str | None:
        for tag in ("ConvFrequency", "TGID", "SrchFrequency"):
            node = self.node(tag)
            if node:
                return node.get("Mod")
        return None

    @property
    def signal(self) -> int | None:
        node = self.node("Property")
        if not node:
            return None
        value = node.get("Sig")
        try:
            return int(value) if value is not None else None
        except ValueError:
            return None
