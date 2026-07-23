from __future__ import annotations

from .exceptions import ProtocolError
from .models import (
    DisplayLine,
    FirmwareResponse,
    ModelResponse,
    Packet,
    StatusResponse,
    ValueResponse,
)


class PacketParser:
    """Parse one CR-delimited SDS-series response."""

    def parse_packet(self, raw: str) -> Packet:
        cleaned = raw.rstrip("\r\n")
        if not cleaned:
            raise ProtocolError("Cannot parse an empty packet.")

        fields = tuple(cleaned.split(","))
        command = fields[0].strip().upper()
        if not command:
            raise ProtocolError(f"Packet has no command: {raw!r}")
        return Packet(command=command, fields=fields[1:], raw=cleaned)

    def parse_typed(
        self,
        packet: Packet,
    ) -> Packet | ModelResponse | FirmwareResponse | ValueResponse | StatusResponse:
        if packet.command == "MDL":
            return ModelResponse(model=self._required(packet, 0), packet=packet)
        if packet.command == "VER":
            return FirmwareResponse(version=self._required(packet, 0), packet=packet)
        if packet.command in {"VOL", "SQL"} and packet.fields:
            return ValueResponse(
                command=packet.command,
                value=self._integer(packet, 0),
                packet=packet,
            )
        if packet.command == "STS":
            return self._parse_status(packet)
        return packet

    @staticmethod
    def _required(packet: Packet, index: int) -> str:
        try:
            value = packet.fields[index]
        except IndexError as exc:
            raise ProtocolError(
                f"{packet.command} response is missing field {index}: {packet.raw!r}"
            ) from exc
        return value

    def _integer(self, packet: Packet, index: int) -> int:
        value = self._required(packet, index)
        try:
            return int(value)
        except ValueError as exc:
            raise ProtocolError(
                f"{packet.command} expected an integer, received {value!r}"
            ) from exc

    def _parse_status(self, packet: Packet) -> StatusResponse:
        display_form = self._required(packet, 0)
        payload = packet.fields[1:]

        # The protocol appends nine reserved fields after a variable number of
        # (line text, line mode) pairs.
        reserved_count = min(9, len(payload))
        line_fields = payload[:-reserved_count] if reserved_count else payload
        reserved = payload[-reserved_count:] if reserved_count else ()

        lines: list[DisplayLine] = []
        for index in range(0, len(line_fields) - 1, 2):
            lines.append(
                DisplayLine(
                    text=line_fields[index].replace("\t", ",").rstrip(),
                    mode=line_fields[index + 1],
                )
            )

        return StatusResponse(
            display_form=display_form,
            lines=tuple(lines),
            reserved=tuple(reserved),
            packet=packet,
        )
