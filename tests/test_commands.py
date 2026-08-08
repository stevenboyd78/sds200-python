import pytest

from sds200.commands import (
    HoldSelection,
    NextSelection,
    PressKey,
    PreviousSelection,
    SetSquelch,
    SetVolume,
    StartScannerInfoPush,
)
from sds200.exceptions import CommandRejectedError, ProtocolError
from sds200.models import Packet


def test_set_volume_wire() -> None:
    assert SetVolume(12).wire == "VOL,12"


def test_set_squelch_wire() -> None:
    assert SetSquelch(5).wire == "SQL,5"


@pytest.mark.parametrize("value", [-1, 30])
def test_volume_validation(value: int) -> None:
    with pytest.raises(ValueError):
        SetVolume(value)


def test_psi_command_wire_and_validation() -> None:
    assert StartScannerInfoPush(250).wire == "PSI,250"
    with pytest.raises(ValueError):
        StartScannerInfoPush(0)


def test_psi_command_accepts_acknowledgement() -> None:
    packet = Packet(command="PSI", fields=("OK",), raw="PSI,OK")
    assert StartScannerInfoPush().parse_response(packet) is None


def test_psi_command_rejects_negative_acknowledgement() -> None:
    packet = Packet(command="PSI", fields=("NG",), raw="PSI,NG")
    with pytest.raises(ProtocolError, match="rejected PSI"):
        StartScannerInfoPush().parse_response(packet)


def test_handheld_volume_and_squelch_limits() -> None:
    assert SetVolume(15, maximum=15).wire == "VOL,15"
    assert SetSquelch(15, maximum=15).wire == "SQL,15"
    with pytest.raises(ValueError, match="between 0 and 15"):
        SetVolume(16, maximum=15)
    with pytest.raises(ValueError, match="between 0 and 15"):
        SetSquelch(16, maximum=15)


def test_hold_related_key_press_wire() -> None:
    assert PressKey("A").wire == "KEY,A,P"
    assert PressKey("b").wire == "KEY,B,P"
    assert PressKey(" F ").wire == "KEY,F,P"


@pytest.mark.parametrize("value", ["", "M", "1", "A,P"])
def test_hold_related_key_press_rejects_other_keys(value: str) -> None:
    with pytest.raises(ValueError, match="Hold-related key code"):
        PressKey(value)


def test_hold_related_key_press_acknowledgement() -> None:
    command = PressKey("C")
    command.parse_response(Packet(command="KEY", fields=("OK",), raw="KEY,OK"))
    with pytest.raises(CommandRejectedError, match="rejected KEY"):
        command.parse_response(
            Packet(command="KEY", fields=("NG",), raw="KEY,NG")
        )


def test_navigation_command_wires() -> None:
    assert HoldSelection("SYS", 42).wire == "HLD,SYS,42,"
    assert NextSelection("DEPT", 7, 42, count=3).wire == "NXT,DEPT,7,42,3"
    assert PreviousSelection("TGID", 99, count=2).wire == "PRV,TGID,99,,2"


def test_navigation_commands_validate_target_and_count() -> None:
    with pytest.raises(ValueError, match="Navigation target"):
        NextSelection("INVALID")
    with pytest.raises(ValueError, match="between 1 and 8"):
        NextSelection("SYS", 1, count=9)
    with pytest.raises(ValueError, match="commas or line breaks"):
        _ = NextSelection("SYS", "1,2").wire


def test_navigation_acknowledgement() -> None:
    command = HoldSelection("SYS", 42)
    command.parse_response(Packet(command="HLD", fields=("OK",), raw="HLD,OK"))
    with pytest.raises(CommandRejectedError, match="rejected HLD"):
        command.parse_response(
            Packet(command="HLD", fields=("NG",), raw="HLD,NG")
        )
