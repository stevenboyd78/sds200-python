from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Mapping
from types import MappingProxyType

from .exceptions import ProtocolError
from .models import GltRecord, GltResponse, ScannerInfo, ScannerNode

XML_COMMAND_ROOTS: Mapping[str, str] = MappingProxyType(
    {"GSI": "ScannerInfo", "PSI": "ScannerInfo", "GLT": "GLT"}
)


class XmlResponseAssembler:
    """Collect supported CR-delimited XML responses into bounded documents."""

    def __init__(self, command_roots: Mapping[str, str] | None = None) -> None:
        configured = XML_COMMAND_ROOTS if command_roots is None else command_roots
        self._command_roots = MappingProxyType(
            {command.upper(): root for command, root in configured.items()}
        )
        self._command: str | None = None
        self._root: str | None = None
        self._lines: list[str] = []

    def _header_command(self, line: str) -> str | None:
        upper = line.upper()
        for command in self._command_roots:
            if upper.startswith(f"{command},<XML>"):
                return command
        return None

    def recognizes_header(self, line: str) -> bool:
        return self._header_command(line) is not None

    def feed(self, line: str) -> tuple[str, str] | None:
        header_command = self._header_command(line)
        if header_command is not None:
            # A new XML header is also a resynchronization point if an earlier
            # document was truncated by a disconnect or dropped packet.
            self._command = header_command
            self._root = self._command_roots[header_command]
            self._lines.clear()
            return None

        if self._command is None:
            return None

        self._lines.append(line)
        assert self._root is not None
        if f"</{self._root}>" not in line:
            return None

        command = self._command
        xml = "\n".join(self._lines)
        self.reset()
        return command, xml

    def reset(self) -> None:
        self._command = None
        self._root = None
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

        records = tuple(
            ScannerNode.create(element.tag, element.attrib)
            for element in root.iter()
            if element is not root
        )
        nodes = {record.tag: record for record in records}

        return ScannerInfo(
            command=command,
            mode=root.attrib.get("Mode"),
            screen=root.attrib.get("V_Screen"),
            nodes=MappingProxyType(nodes),
            raw_xml=xml,
            records=records,
        )


class GltParser:
    def parse(self, command: str, xml: str) -> GltResponse:
        try:
            root = ET.fromstring(xml)
        except ET.ParseError as exc:
            raise ProtocolError("Invalid GLT XML response") from exc

        if root.tag != "GLT":
            raise ProtocolError(f"Expected GLT root, received {root.tag!r}")

        return GltResponse.create(
            command=command,
            root_attributes=root.attrib,
            records=tuple(
                GltRecord.create(element.tag, element.attrib) for element in root
            ),
            raw_xml=xml,
        )
