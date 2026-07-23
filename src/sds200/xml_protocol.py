from __future__ import annotations

import xml.etree.ElementTree as ET
from types import MappingProxyType

from .exceptions import ProtocolError
from .models import ScannerInfo, ScannerNode


class XmlResponseAssembler:
    """Collect CR-delimited GSI/PSI XML responses into one document."""

    def __init__(self) -> None:
        self._command: str | None = None
        self._lines: list[str] = []

    def feed(self, line: str) -> tuple[str, str] | None:
        upper = line.upper()
        if self._command is None:
            if upper.startswith("GSI,<XML>"):
                self._command = "GSI"
                self._lines.clear()
                return None
            if upper.startswith("PSI,<XML>"):
                self._command = "PSI"
                self._lines.clear()
                return None
            return None

        self._lines.append(line)
        if "</ScannerInfo>" not in line:
            return None

        command = self._command
        assert command is not None
        xml = "\n".join(self._lines)
        self._command = None
        self._lines = []
        return command, xml

    @property
    def collecting(self) -> bool:
        return self._command is not None


class ScannerInfoParser:
    def parse(self, command: str, xml: str) -> ScannerInfo:
        try:
            root = ET.fromstring(xml)
        except ET.ParseError as exc:
            raise ProtocolError(f"Invalid {command} XML response: {exc}") from exc

        if root.tag != "ScannerInfo":
            raise ProtocolError(f"Expected ScannerInfo root, received {root.tag!r}")

        nodes: dict[str, ScannerNode] = {}
        for element in root.iter():
            if element is root:
                continue
            nodes[element.tag] = ScannerNode.create(element.tag, element.attrib)

        return ScannerInfo(
            command=command,
            mode=root.attrib.get("Mode"),
            screen=root.attrib.get("V_Screen"),
            nodes=MappingProxyType(nodes),
            raw_xml=xml,
        )
