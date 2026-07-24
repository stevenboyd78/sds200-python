from pathlib import Path

import pytest

from sds200.device import ScannerDevice
from sds200.discovery import NetworkScanner
from sds200.exceptions import ProfileError
from sds200.profiles import (
    ConnectionProfile,
    ProfileStore,
    profile_from_discovery,
    repair_profile,
)


def test_profile_store_round_trip(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path / "profiles.toml")
    store.put(ConnectionProfile.network("home", "192.168.0.251"))
    store.put(ConnectionProfile.serial("desk", "/dev/ttyACM0"))

    assert [profile.name for profile in store.list()] == ["desk", "home"]
    assert store.get("home").host == "192.168.0.251"
    assert store.get("desk").port == "/dev/ttyACM0"

    store.remove("desk")
    assert [profile.name for profile in store.list()] == ["home"]


def test_profile_store_rejects_missing_profile(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path / "profiles.toml")
    with pytest.raises(ProfileError, match="does not exist"):
        store.get("missing")


def test_network_profile_validates_ports() -> None:
    with pytest.raises(ProfileError, match="UDP port"):
        ConnectionProfile.network("bad", "scanner.local", udp_port=70000)


def test_fallback_profile_round_trip(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path / "profiles.toml")
    store.put(
        ConnectionProfile.fallback(
            "home",
            port="/dev/serial/by-id/scanner",
            host="192.0.2.25",
            preference="network",
        )
    )

    loaded = store.get("home")

    assert loaded.kind == "fallback"
    assert loaded.preference == "network"
    assert loaded.port == "/dev/serial/by-id/scanner"
    assert loaded.host == "192.0.2.25"


def test_profile_from_discovery_creates_fallback_profile() -> None:
    serial = ScannerDevice(
        path=Path("/dev/serial/by-id/scanner"),
        resolved_path=Path("/dev/ttyACM0"),
        name="scanner",
    )
    network = NetworkScanner(
        host="192.0.2.25",
        port=50536,
        model="SDS200",
        latency_ms=2.5,
    )

    profile = profile_from_discovery(
        "home",
        [serial],
        [network],
        preference="network",
    )

    assert profile.kind == "fallback"
    assert profile.preference == "network"


def test_repair_profile_updates_stale_fallback_endpoints() -> None:
    profile = ConnectionProfile.fallback(
        "home",
        port="/dev/serial/by-id/old-scanner",
        host="192.0.2.10",
        preference="network",
    )
    serial = ScannerDevice(
        path=Path("/dev/serial/by-id/new-scanner"),
        resolved_path=Path("/dev/ttyACM0"),
        name="new-scanner",
    )
    network = NetworkScanner(
        host="192.0.2.25",
        port=50536,
        model="SDS200",
        latency_ms=2.0,
    )

    result = repair_profile(profile, [serial], [network])

    assert result.changed
    assert result.repaired.port == "/dev/serial/by-id/new-scanner"
    assert result.repaired.host == "192.0.2.25"
    assert result.repaired.preference == "network"
    assert set(result.changes) == {"port", "host"}


def test_repair_profile_preserves_known_endpoint_when_other_is_not_found() -> None:
    profile = ConnectionProfile.fallback(
        "home",
        port="/dev/serial/by-id/scanner",
        host="192.0.2.10",
        preference="serial",
    )
    network = NetworkScanner(
        host="192.0.2.25",
        port=50536,
        model="SDS200",
        latency_ms=2.0,
    )

    result = repair_profile(profile, [], [network])

    assert result.repaired.port == profile.port
    assert result.repaired.host == "192.0.2.25"
    assert result.repaired.preference == "serial"


def test_repair_profile_rejects_ambiguous_serial_results() -> None:
    profile = ConnectionProfile.serial("desk", "/dev/serial/by-id/missing")
    devices = [
        ScannerDevice(
            path=Path(f"/dev/serial/by-id/scanner-{index}"),
            resolved_path=Path(f"/dev/ttyACM{index}"),
            name=f"scanner-{index}",
        )
        for index in range(2)
    ]

    with pytest.raises(ProfileError, match="unambiguous USB scanner"):
        repair_profile(profile, devices, [])


def test_serial_profile_model_round_trip_and_normalization(tmp_path: Path) -> None:
    path = tmp_path / "profiles.toml"
    path.write_text(
        'version = 3\n\n[profiles."handheld"]\nkind = "serial"\n'
        'model = "SDS150GBT"\nport = "/dev/serial/by-id/sds150"\n',
        encoding="utf-8",
    )
    store = ProfileStore(path)

    loaded = store.get("handheld")
    store.put(loaded)

    assert loaded.model == "SDS150"
    assert 'model = "SDS150"' in path.read_text(encoding="utf-8")


def test_profile_store_loads_legacy_serial_profile_without_model(tmp_path: Path) -> None:
    path = tmp_path / "profiles.toml"
    path.write_text(
        'version = 2\n\n[profiles."legacy"]\nkind = "serial"\n'
        'port = "/dev/ttyACM0"\n',
        encoding="utf-8",
    )

    loaded = ProfileStore(path).get("legacy")

    assert loaded.model is None
    assert loaded.port == "/dev/ttyACM0"


def test_profile_store_rejects_handheld_network_profile(tmp_path: Path) -> None:
    path = tmp_path / "profiles.toml"
    path.write_text(
        'version = 3\n\n[profiles."bad"]\nkind = "network"\n'
        'model = "SDS150"\nhost = "192.0.2.25"\n',
        encoding="utf-8",
    )

    with pytest.raises(ProfileError, match="must use the SDS200"):
        ProfileStore(path).get("bad")


def test_profile_from_discovery_rejects_mixed_scanner_models() -> None:
    serial = ScannerDevice(
        path=Path("/dev/serial/by-id/sds150"),
        resolved_path=Path("/dev/ttyACM0"),
        name="sds150",
        model="SDS150",
    )
    network = NetworkScanner(
        host="192.0.2.25",
        port=50536,
        model="SDS200",
        latency_ms=2.5,
    )

    with pytest.raises(ProfileError, match="different scanner models"):
        profile_from_discovery("mixed", [serial], [network])


def test_repair_serial_profile_learns_discovered_model() -> None:
    profile = ConnectionProfile.serial("desk", "/dev/serial/by-id/old")
    device = ScannerDevice(
        path=Path("/dev/serial/by-id/sds100"),
        resolved_path=Path("/dev/ttyACM0"),
        name="sds100",
        model="SDS100",
    )

    result = repair_profile(profile, [device], [])

    assert result.repaired.model == "SDS100"
    assert result.changes["model"] == "unknown -> SDS100"
