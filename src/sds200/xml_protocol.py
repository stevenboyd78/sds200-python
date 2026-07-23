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

    @staticmethod
    def _header_command(line: str) -> str | None:
        upper = line.upper()
        if upper.startswith("GSI,<XML>"):
            return "GSI"
        if upper.startswith("PSI,<XML>"):
            return "PSI"
        return None

    def feed(self, line: str) -> tuple[str, str] | None:
        header_command = self._header_command(line)
        if header_command is not None:
            # A new XML header is also a resynchronization point if an earlier
            # document was truncated by a disconnect or dropped packet.
            self._command = header_command
            self._lines.clear()
            return None

        if self._command is None:
            return None

        self._lines.append(line)
        if "</ScannerInfo>" not in line:
            return None

        command = self._command
        xml = "\n".join(self._lines)
        self.reset()
        return command, xml

    def reset(self) -> None:
        self._command = None
        self._lines.clear()

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
