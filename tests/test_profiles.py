from pathlib import Path

import pytest

from sds200.device import ScannerDevice
from sds200.discovery import NetworkScanner
from sds200.exceptions import ProfileError
from sds200.profiles import (
    ConnectionProfile,
    ProfileStore,
    profile_from_discovery,
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
