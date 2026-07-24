import pytest

from sds200.scanner import (
    capabilities_for_model,
    infer_model_from_device_name,
    normalize_model_name,
)


@pytest.mark.parametrize(
    ("reported", "expected"),
    [
        ("SDS100", "SDS100"),
        ("sds200", "SDS200"),
        ("SDS150GBT", "SDS150"),
        ("UB391Z", "SDS150"),
    ],
)
def test_normalize_model_name(reported: str, expected: str) -> None:
    assert normalize_model_name(reported) == expected


def test_model_capabilities_include_handheld_limits() -> None:
    sds100 = capabilities_for_model("SDS100")
    sds150 = capabilities_for_model("SDS150GBT")
    sds200 = capabilities_for_model("SDS200")

    assert sds100.maximum_volume == 15
    assert sds100.maximum_squelch == 15
    assert sds100.charge_status is True
    assert sds150.charge_status is True
    assert sds200.maximum_volume == 29
    assert sds200.maximum_squelch == 19
    assert sds200.network_control is True


def test_infer_model_from_linux_by_id_name() -> None:
    assert (
        infer_model_from_device_name(
            "usb-UNIDEN_AMERICA_CORP._SDS150_Serial_Port-if00"
        )
        == "SDS150"
    )
