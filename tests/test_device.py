from pathlib import Path

from sds200.device import ScannerDevice, choose_scanner


def test_choose_explicit_scanner() -> None:
    assert choose_scanner("/dev/ttyACM0") == Path("/dev/ttyACM0")


def test_choose_first_discovered_scanner() -> None:
    device = ScannerDevice(
        path=Path("/dev/serial/by-id/test"),
        resolved_path=Path("/dev/ttyACM0"),
        name="test",
    )
    assert choose_scanner(candidates=[device]) == device.path
