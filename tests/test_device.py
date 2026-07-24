from pathlib import Path

import pytest

from sds200.device import ScannerDevice, choose_scanner, discover_scanners


def test_choose_explicit_scanner() -> None:
    assert choose_scanner("/dev/ttyACM0") == Path("/dev/ttyACM0")


def test_choose_first_discovered_scanner() -> None:
    device = ScannerDevice(
        path=Path("/dev/serial/by-id/test"),
        resolved_path=Path("/dev/ttyACM0"),
        name="test",
    )
    assert choose_scanner(candidates=[device]) == device.path


def test_discover_supported_usb_models(tmp_path: Path) -> None:
    tty100 = tmp_path / "ttyACM0"
    tty150 = tmp_path / "ttyACM1"
    tty200 = tmp_path / "ttyACM2"
    for target in (tty100, tty150, tty200):
        target.touch()

    (tmp_path / "usb-UNIDEN_SDS100_Serial_Port-if00").symlink_to(tty100)
    (tmp_path / "usb-UNIDEN_SDS150_Serial_Port-if00").symlink_to(tty150)
    (tmp_path / "usb-UNIDEN_SDS200_Serial_Port-if00").symlink_to(tty200)

    devices = discover_scanners(tmp_path, "*UNIDEN*SDS*")

    assert [device.model for device in devices] == ["SDS100", "SDS150", "SDS200"]
    assert [device.model for device in discover_scanners(tmp_path, model="SDS150")] == [
        "SDS150"
    ]


def test_discovery_rejects_unknown_model_when_directory_is_missing(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unsupported SDS-series scanner model"):
        discover_scanners(tmp_path / "missing", model="unknown")
