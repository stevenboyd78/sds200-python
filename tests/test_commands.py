import pytest

from sds200.commands import SetSquelch, SetVolume


def test_set_volume_wire() -> None:
    assert SetVolume(12).wire == "VOL,12"


def test_set_squelch_wire() -> None:
    assert SetSquelch(5).wire == "SQL,5"


@pytest.mark.parametrize("value", [-1, 30])
def test_volume_validation(value: int) -> None:
    with pytest.raises(ValueError):
        SetVolume(value)
