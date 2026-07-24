import pytest

from sds200.commands import SetSquelch, SetVolume, StartScannerInfoPush
from sds200.exceptions import ProtocolError
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
