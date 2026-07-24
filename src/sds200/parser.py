from __future__ import annotations

import re

from .exceptions import ProtocolError
from .models import (
    ChargeStatus,
    DisplayLine,
    FirmwareResponse,
    ModelResponse,
    Packet,
    StatusResponse,
    ValueResponse,
)
from .scanner import normalize_model_name

_CHARGE_STATUS_NAMES = {
    0: "not_charging",
    1: "initializing",
    2: "temperature_error",
    3: "power_error",
    4: "full",
    5: "recharging",
    6: "charging",
}
_CHARGE_PATTERN = re.compile(
    r"^GCS,CST=(?P<status>\d+),"
    r"VOLT=(?P<voltage>-?\d+)mV:(?P<capacity>\d+)%,"
    r"CURR=(?P<current>[+-]?\d+)mA,"
    r"TEMP=\s*(?P<temperature>[+-]?\d+(?:\.\d+)?)C$",
    re.IGNORECASE,
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
    ) -> (
        Packet
        | ChargeStatus
        | ModelResponse
        | FirmwareResponse
        | ValueResponse
        | StatusResponse
    ):
        if packet.command == "MDL":
            reported_model = self._required(packet, 0).strip()
            model = normalize_model_name(reported_model) or reported_model
            return ModelResponse(
                model=model,
                reported_model=reported_model,
                packet=packet,
            )
        if packet.command == "VER":
            return FirmwareResponse(version=self._required(packet, 0), packet=packet)
        if packet.command == "GCS":
            return self._parse_charge_status(packet)
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

    @staticmethod
    def _parse_charge_status(packet: Packet) -> ChargeStatus:
        match = _CHARGE_PATTERN.fullmatch(packet.raw.strip())
        if match is None:
            raise ProtocolError(f"Invalid GCS response: {packet.raw!r}")
        status_code = int(match.group("status"))
        return ChargeStatus(
            status_code=status_code,
            status=_CHARGE_STATUS_NAMES.get(status_code, "unknown"),
            voltage_mv=int(match.group("voltage")),
            capacity_percent=int(match.group("capacity")),
            current_ma=int(match.group("current")),
            temperature_c=float(match.group("temperature")),
            packet=packet,
        )

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
