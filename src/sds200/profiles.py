from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from .device import ScannerDevice
from .discovery import NetworkScanner
from .exceptions import ProfileError
from .network import DEFAULT_UDP_PORT
from .scanner import ScannerModel, normalize_model_name

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
    model: ScannerModel | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ProfileError("Profile name must not be empty.")
        if self.preference not in TRANSPORT_PREFERENCES:
            raise ProfileError(f"Unsupported transport preference: {self.preference!r}")
        if self.model is not None:
            normalized_model = normalize_model_name(self.model)
            if normalized_model is None:
                raise ProfileError(f"Unsupported scanner model: {self.model!r}")
            object.__setattr__(self, "model", normalized_model)
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
        if self.kind in {"network", "fallback"} and self.model not in {None, "SDS200"}:
            raise ProfileError("Only the SDS200 supports native UDP network control.")
        if not 1 <= self.udp_port <= 65535:
            raise ProfileError("Profile UDP port must be between 1 and 65535.")
        if not 0 <= self.bind_port <= 65535:
            raise ProfileError("Profile bind port must be between 0 and 65535.")

    @classmethod
    def serial(
        cls,
        name: str,
        port: str | Path,
        *,
        model: ScannerModel | None = None,
    ) -> ConnectionProfile:
        return cls(name=name, kind="serial", port=str(port), model=model)

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
            model="SDS200",
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
            model="SDS200",
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
        if serial_device.model not in {None, network_scanner.model}:
            raise ProfileError(
                f"USB {serial_device.model} and network {network_scanner.model} "
                "results refer to different scanner models; use --model, "
                "--usb-only, or --network-only."
            )
        return ConnectionProfile.fallback(
            name,
            port=serial_device.path,
            host=network_scanner.host,
            udp_port=network_scanner.port,
            preference=preference,
        )
    if serial_device is not None:
        return ConnectionProfile.serial(
            name,
            serial_device.path,
            model=serial_device.model,
        )
    if network_scanner is not None:
        return ConnectionProfile.network(
            name,
            network_scanner.host,
            udp_port=network_scanner.port,
        )
    raise ProfileError("No supported SDS-series scanner was discovered for the profile.")


@dataclass(frozen=True, slots=True)
class ProfileRepairResult:
    original: ConnectionProfile
    repaired: ConnectionProfile
    changes: Mapping[str, str]

    @property
    def changed(self) -> bool:
        return self.original != self.repaired

    @classmethod
    def create(
        cls,
        original: ConnectionProfile,
        repaired: ConnectionProfile,
        changes: Mapping[str, str],
    ) -> ProfileRepairResult:
        return cls(
            original=original,
            repaired=repaired,
            changes=MappingProxyType(dict(changes)),
        )


def repair_profile(
    profile: ConnectionProfile,
    serial_devices: Sequence[ScannerDevice],
    network_scanners: Sequence[NetworkScanner],
) -> ProfileRepairResult:
    """Repair stale profile endpoints using unambiguous discovery results."""

    serial_device = _select_serial_device(profile.port, serial_devices, profile.model)
    network_scanner = _select_network_scanner(profile.host, network_scanners)
    changes: dict[str, str] = {}

    if profile.kind == "serial":
        if serial_device is None:
            raise ProfileError(
                f"Could not find an unambiguous USB scanner for profile {profile.name!r}."
            )
        repaired = ConnectionProfile.serial(
            profile.name,
            serial_device.path,
            model=serial_device.model or profile.model,
        )
        if repaired.port != profile.port:
            changes["port"] = f"{profile.port} -> {repaired.port}"
        if repaired.model != profile.model:
            changes["model"] = f"{profile.model or 'unknown'} -> {repaired.model}"
        return ProfileRepairResult.create(profile, repaired, changes)

    if profile.kind == "network":
        if network_scanner is None:
            raise ProfileError(
                f"Could not find an unambiguous network scanner for profile {profile.name!r}."
            )
        repaired = ConnectionProfile.network(
            profile.name,
            network_scanner.host,
            udp_port=network_scanner.port,
            bind_address=profile.bind_address,
            bind_port=profile.bind_port,
        )
        if repaired.host != profile.host:
            changes["host"] = f"{profile.host} -> {repaired.host}"
        if repaired.udp_port != profile.udp_port:
            changes["udp_port"] = f"{profile.udp_port} -> {repaired.udp_port}"
        return ProfileRepairResult.create(profile, repaired, changes)

    if serial_device is None and network_scanner is None:
        raise ProfileError(
            f"Could not discover either endpoint for fallback profile {profile.name!r}."
        )

    repaired_port = str(serial_device.path) if serial_device is not None else profile.port
    repaired_host = network_scanner.host if network_scanner is not None else profile.host
    repaired_udp_port = (
        network_scanner.port if network_scanner is not None else profile.udp_port
    )
    assert repaired_port is not None
    assert repaired_host is not None
    repaired = ConnectionProfile.fallback(
        profile.name,
        port=repaired_port,
        host=repaired_host,
        udp_port=repaired_udp_port,
        bind_address=profile.bind_address,
        bind_port=profile.bind_port,
        preference=profile.preference,
    )
    if repaired.port != profile.port:
        changes["port"] = f"{profile.port} -> {repaired.port}"
    if repaired.host != profile.host:
        changes["host"] = f"{profile.host} -> {repaired.host}"
    if repaired.udp_port != profile.udp_port:
        changes["udp_port"] = f"{profile.udp_port} -> {repaired.udp_port}"
    return ProfileRepairResult.create(profile, repaired, changes)


def _select_serial_device(
    current_port: str | None,
    devices: Sequence[ScannerDevice],
    model: ScannerModel | None = None,
) -> ScannerDevice | None:
    matching = tuple(
        device for device in devices if model is None or device.model in {None, model}
    )
    if current_port is not None:
        current = Path(current_port)
        for device in matching:
            if device.path == current or device.resolved_path == current:
                return device
    return matching[0] if len(matching) == 1 else None


def _select_network_scanner(
    current_host: str | None,
    scanners: Sequence[NetworkScanner],
) -> NetworkScanner | None:
    if current_host is not None:
        for scanner in scanners:
            if scanner.host == current_host:
                return scanner
    return scanners[0] if len(scanners) == 1 else None


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
        raw_model = raw.get("model")
        model = ProfileStore._parse_model(name, raw_model)

        if kind == "serial":
            port = raw.get("port")
            if not isinstance(port, str):
                raise ProfileError(f"Serial profile {name!r} requires a string port.")
            return ConnectionProfile.serial(name, port, model=model)

        if kind == "network":
            if model not in {None, "SDS200"}:
                raise ProfileError(
                    f"Network profile {name!r} must use the SDS200 model."
                )
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
            if model not in {None, "SDS200"}:
                raise ProfileError(
                    f"Fallback profile {name!r} must use the SDS200 model."
                )
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
    def _parse_model(name: str, value: object) -> ScannerModel | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ProfileError(f"Profile {name!r} has an invalid scanner model.")
        model = normalize_model_name(value)
        if model is None:
            raise ProfileError(
                f"Profile {name!r} has unsupported scanner model {value!r}."
            )
        return model

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
        lines = ["version = 3", ""]
        for name in sorted(profiles):
            profile = profiles[name]
            lines.append(f"[profiles.{json.dumps(name)}]")
            lines.append(f"kind = {json.dumps(profile.kind)}")
            if profile.model is not None:
                lines.append(f"model = {json.dumps(profile.model)}")
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
