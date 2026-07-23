from pathlib import Path

import pytest

from sds200.exceptions import ProfileError
from sds200.profiles import ConnectionProfile, ProfileStore


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
