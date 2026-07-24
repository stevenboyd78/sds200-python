from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .device import ScannerDevice
from .discovery import NetworkScanner
from .exceptions import ProfileError
from .network import DEFAULT_UDP_PORT

ProfileKind = Literal["serial", "network", "fallback"]
TransportPreference = Literal["serial", "network"]
TRANSPORT_PREFERENCES: tuple[TransportPreference, ...] = ("serial", "network")


def default_profile_path() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config_home) if config_home else Path.home() / ".config"
    return base / "sds200" / "profiles.toml"


@dataclass(frozen=True, slots=True)
class ConnectionProfile:
    name: str
    kind: ProfileKind
    port: str | None = None
    host: str | None = None
    udp_port: int = DEFAULT_UDP_PORT
    bind_address: str = ""
    bind_port: int = 0
    preference: TransportPreference = "serial"

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ProfileError("Profile name must not be empty.")
        if self.preference not in TRANSPORT_PREFERENCES:
            raise ProfileError(f"Unsupported transport preference: {self.preference!r}")
        if self.kind == "serial":
            if not self.port:
                raise ProfileError("A serial profile requires a port.")
            if self.host is not None:
                raise ProfileError("A serial profile cannot define a network host.")
        elif self.kind == "network":
            if not self.host:
                raise ProfileError("A network profile requires a host.")
            if self.port is not None:
                raise ProfileError("A network profile cannot define a serial port.")
        elif self.kind == "fallback":
            if not self.port or not self.host:
                raise ProfileError(
                    "A fallback profile requires both serial and network endpoints."
                )
        else:
            raise ProfileError(f"Unsupported profile kind: {self.kind!r}")
        if not 1 <= self.udp_port <= 65535:
            raise ProfileError("Profile UDP port must be between 1 and 65535.")
        if not 0 <= self.bind_port <= 65535:
            raise ProfileError("Profile bind port must be between 0 and 65535.")

    @classmethod
    def serial(cls, name: str, port: str | Path) -> ConnectionProfile:
        return cls(name=name, kind="serial", port=str(port))

    @classmethod
    def network(
        cls,
        name: str,
        host: str,
        *,
        udp_port: int = DEFAULT_UDP_PORT,
        bind_address: str = "",
        bind_port: int = 0,
    ) -> ConnectionProfile:
        return cls(
            name=name,
            kind="network",
            host=host,
            udp_port=udp_port,
            bind_address=bind_address,
            bind_port=bind_port,
            preference="network",
        )

    @classmethod
    def fallback(
        cls,
        name: str,
        *,
        port: str | Path,
        host: str,
        preference: TransportPreference = "serial",
        udp_port: int = DEFAULT_UDP_PORT,
        bind_address: str = "",
        bind_port: int = 0,
    ) -> ConnectionProfile:
        return cls(
            name=name,
            kind="fallback",
            port=str(port),
            host=host,
            udp_port=udp_port,
            bind_address=bind_address,
            bind_port=bind_port,
            preference=preference,
        )


def profile_from_discovery(
    name: str,
    serial_devices: Sequence[ScannerDevice],
    network_scanners: Sequence[NetworkScanner],
    *,
    preference: TransportPreference = "serial",
) -> ConnectionProfile:
    if preference not in TRANSPORT_PREFERENCES:
        raise ProfileError(f"Unsupported transport preference: {preference!r}")
    if len(serial_devices) > 1:
        raise ProfileError(
            "More than one USB scanner was discovered; create the profile with "
            "an explicit --port instead."
        )
    if len(network_scanners) > 1:
        raise ProfileError(
            "More than one network scanner was discovered; create the profile "
            "with an explicit --host instead."
        )

    serial_device = serial_devices[0] if serial_devices else None
    network_scanner = network_scanners[0] if network_scanners else None
    if serial_device is not None and network_scanner is not None:
        return ConnectionProfile.fallback(
            name,
            port=serial_device.path,
            host=network_scanner.host,
            udp_port=network_scanner.port,
            preference=preference,
        )
    if serial_device is not None:
        return ConnectionProfile.serial(name, serial_device.path)
    if network_scanner is not None:
        return ConnectionProfile.network(
            name,
            network_scanner.host,
            udp_port=network_scanner.port,
        )
    raise ProfileError("No SDS200 scanner was discovered for the profile.")


class ProfileStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_profile_path()

    def list(self) -> tuple[ConnectionProfile, ...]:
        profiles = self._load()
        return tuple(profiles[name] for name in sorted(profiles))

    def get(self, name: str) -> ConnectionProfile:
        try:
            return self._load()[name]
        except KeyError as exc:
            raise ProfileError(f"Connection profile {name!r} does not exist.") from exc

    def put(self, profile: ConnectionProfile) -> None:
        profiles = self._load()
        profiles[profile.name] = profile
        self._save(profiles)

    def remove(self, name: str) -> None:
        profiles = self._load()
        if name not in profiles:
            raise ProfileError(f"Connection profile {name!r} does not exist.")
        del profiles[name]
        self._save(profiles)

    def _load(self) -> dict[str, ConnectionProfile]:
        if not self.path.exists():
            return {}
        try:
            document = tomllib.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ProfileError(f"Could not read profile file {self.path}: {exc}") from exc

        raw_profiles = document.get("profiles", {})
        if not isinstance(raw_profiles, Mapping):
            raise ProfileError("The profiles document must contain a [profiles] table.")

        profiles: dict[str, ConnectionProfile] = {}
        for name, raw in raw_profiles.items():
            if not isinstance(name, str) or not isinstance(raw, Mapping):
                raise ProfileError("Each profile must be a named TOML table.")
            profiles[name] = self._parse_profile(name, raw)
        return profiles

    @staticmethod
    def _parse_profile(name: str, raw: Mapping[object, object]) -> ConnectionProfile:
        kind = raw.get("kind")
        if kind == "serial":
            port = raw.get("port")
            if not isinstance(port, str):
                raise ProfileError(f"Serial profile {name!r} requires a string port.")
            return ConnectionProfile.serial(name, port)

        if kind == "network":
            host, udp_port, bind_address, bind_port = ProfileStore._network_fields(
                name, raw, label="Network"
            )
            return ConnectionProfile.network(
                name,
                host,
                udp_port=udp_port,
                bind_address=bind_address,
                bind_port=bind_port,
            )

        if kind == "fallback":
            host, udp_port, bind_address, bind_port = ProfileStore._network_fields(
                name, raw, label="Fallback"
            )
            port = raw.get("port")
            if not isinstance(port, str):
                raise ProfileError(
                    f"Fallback profile {name!r} requires a string serial port."
                )
            raw_preference = raw.get("preference", "serial")
            if raw_preference == "serial":
                preference: TransportPreference = "serial"
            elif raw_preference == "network":
                preference = "network"
            else:
                raise ProfileError(
                    f"Fallback profile {name!r} has invalid preference "
                    f"{raw_preference!r}."
                )
            return ConnectionProfile.fallback(
                name,
                port=port,
                host=host,
                udp_port=udp_port,
                bind_address=bind_address,
                bind_port=bind_port,
                preference=preference,
            )

        raise ProfileError(f"Profile {name!r} has unsupported kind {kind!r}.")

    @staticmethod
    def _network_fields(
        name: str,
        raw: Mapping[object, object],
        *,
        label: str,
    ) -> tuple[str, int, str, int]:
        host = raw.get("host")
        if not isinstance(host, str):
            raise ProfileError(f"{label} profile {name!r} requires a host.")
        udp_port = raw.get("udp_port", DEFAULT_UDP_PORT)
        bind_address = raw.get("bind_address", "")
        bind_port = raw.get("bind_port", 0)
        if not isinstance(udp_port, int) or not isinstance(bind_port, int):
            raise ProfileError(f"{label} profile {name!r} has an invalid port.")
        if not isinstance(bind_address, str):
            raise ProfileError(
                f"{label} profile {name!r} has an invalid bind address."
            )
        return host, udp_port, bind_address, bind_port

    def _save(self, profiles: Mapping[str, ConnectionProfile]) -> None:
        lines = ["version = 2", ""]
        for name in sorted(profiles):
            profile = profiles[name]
            lines.append(f"[profiles.{json.dumps(name)}]")
            lines.append(f"kind = {json.dumps(profile.kind)}")
            if profile.kind == "serial":
                assert profile.port is not None
                lines.append(f"port = {json.dumps(profile.port)}")
            else:
                assert profile.host is not None
                if profile.kind == "fallback":
                    assert profile.port is not None
                    lines.append(f"port = {json.dumps(profile.port)}")
                    lines.append(f"preference = {json.dumps(profile.preference)}")
                lines.append(f"host = {json.dumps(profile.host)}")
                lines.append(f"udp_port = {profile.udp_port}")
                lines.append(f"bind_address = {json.dumps(profile.bind_address)}")
                lines.append(f"bind_port = {profile.bind_port}")
            lines.append("")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            temporary.write_text("\n".join(lines), encoding="utf-8")
            temporary.replace(self.path)
        except OSError as exc:
            raise ProfileError(f"Could not write profile file {self.path}: {exc}") from exc
