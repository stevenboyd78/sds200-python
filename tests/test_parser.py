from sds200.models import FirmwareResponse, ModelResponse, StatusResponse, ValueResponse
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
