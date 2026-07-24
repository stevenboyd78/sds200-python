import pytest

from sds200.exceptions import ProtocolError
from sds200.models import (
    ChargeStatus,
    FirmwareResponse,
    ModelResponse,
    StatusResponse,
    ValueResponse,
)
from sds200.parser import PacketParser


def test_model_response() -> None:
    parser = PacketParser()
    parsed = parser.parse_typed(parser.parse_packet("MDL,SDS200"))
    assert isinstance(parsed, ModelResponse)
    assert parsed.model == "SDS200"


def test_firmware_response() -> None:
    parser = PacketParser()
    parsed = parser.parse_typed(parser.parse_packet("VER,Version 1.23.00"))
    assert isinstance(parsed, FirmwareResponse)
    assert parsed.version == "Version 1.23.00"


def test_value_response() -> None:
    parser = PacketParser()
    parsed = parser.parse_typed(parser.parse_packet("VOL,12"))
    assert isinstance(parsed, ValueResponse)
    assert parsed.value == 12


def test_status_preserves_display_lines() -> None:
    parser = PacketParser()
    raw = (
        "STS,00000,System Name,************************,"
        "Channel Name,________________________,0,1,0,0,,,,0,OFF,3"
    )
    parsed = parser.parse_typed(parser.parse_packet(raw))
    assert isinstance(parsed, StatusResponse)
    assert parsed.display_form == "00000"
    assert parsed.lines[0].text == "System Name"
    assert parsed.lines[1].text == "Channel Name"
    assert len(parsed.reserved) == 9


def test_sds150_reported_model_is_normalized() -> None:
    parser = PacketParser()
    parsed = parser.parse_typed(parser.parse_packet("MDL,SDS150GBT"))

    assert isinstance(parsed, ModelResponse)
    assert parsed.model == "SDS150"
    assert parsed.reported_model == "SDS150GBT"


def test_charge_status_response() -> None:
    parser = PacketParser()
    parsed = parser.parse_typed(
        parser.parse_packet(
            "GCS,CST=6,VOLT=4012mV:82%,CURR=0123mA,TEMP= 27.65C"
        )
    )

    assert isinstance(parsed, ChargeStatus)
    assert parsed.status == "charging"
    assert parsed.charging is True
    assert parsed.voltage_mv == 4012
    assert parsed.capacity_percent == 82
    assert parsed.current_ma == 123
    assert parsed.temperature_c == 27.65


def test_malformed_charge_status_is_rejected() -> None:
    parser = PacketParser()

    with pytest.raises(ProtocolError, match="Invalid GCS response"):
        parser.parse_typed(parser.parse_packet("GCS,CST=6,VOLT=bad"))
