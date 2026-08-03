from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .broadcastify import BroadcastifyConfig
from .exceptions import ProfileError
from .reliability import ReconnectPolicy
from .remote_audio import EnvironmentSecret

REMOTE_AUDIO_PROFILE_VERSION = 1

RemoteAudioProfileKind = Literal["broadcastify"]


def default_remote_audio_profile_path() -> Path:
    """Return the legacy user path used until layered configuration is added."""

    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config_home) if config_home else Path.home() / ".config"
    return base / "sds200" / "remote-audio-profiles.toml"


@dataclass(frozen=True, slots=True)
class BroadcastifyDestinationProfile:
    """Saved Broadcastify settings containing only a secret reference."""

    name: str
    server: str
    mount: str
    environment_variable: str
    port: int = 80
    stream_name: str = "SDS200 scanner feed"
    genre: str = "Scanner"
    public: bool = True
    ffmpeg_executable: str = "ffmpeg"
    connect_timeout: float = 10.0
    socket_timeout: float = 10.0
    encoder_stop_timeout: float = 2.0
    buffer_seconds: float = 5.0
    stop_timeout: float = 5.0
    reconnect_initial_delay: float = 1.0
    reconnect_multiplier: float = 2.0
    reconnect_max_delay: float = 30.0
    reconnect_max_attempts: int | None = None

    def __post_init__(self) -> None:
        try:
            self.to_broadcastify_config()
        except (TypeError, ValueError) as exc:
            raise ProfileError(
                f"Broadcastify destination profile {self.name!r} is invalid: {exc}"
            ) from exc

    @property
    def kind(self) -> RemoteAudioProfileKind:
        return "broadcastify"

    @property
    def reconnect_policy(self) -> ReconnectPolicy:
        return ReconnectPolicy(
            initial_delay=self.reconnect_initial_delay,
            multiplier=self.reconnect_multiplier,
            max_delay=self.reconnect_max_delay,
            max_attempts=self.reconnect_max_attempts,
        )

    def to_broadcastify_config(self) -> BroadcastifyConfig:
        """Build the existing validated adapter configuration."""

        return BroadcastifyConfig(
            name=self.name,
            server=self.server,
            mount=self.mount,
            password=EnvironmentSecret(self.environment_variable),
            port=self.port,
            stream_name=self.stream_name,
            genre=self.genre,
            public=self.public,
            ffmpeg_executable=self.ffmpeg_executable,
            connect_timeout=self.connect_timeout,
            socket_timeout=self.socket_timeout,
            encoder_stop_timeout=self.encoder_stop_timeout,
            buffer_seconds=self.buffer_seconds,
            stop_timeout=self.stop_timeout,
            reconnect_policy=self.reconnect_policy,
        )


class RemoteAudioProfileStore:
    """Persist remote-audio destination profiles in a dedicated TOML document."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = (
            Path(path) if path is not None else default_remote_audio_profile_path()
        )

    def list(self) -> tuple[BroadcastifyDestinationProfile, ...]:
        profiles = self._load()
        return tuple(profiles[name] for name in sorted(profiles))

    def get(self, name: str) -> BroadcastifyDestinationProfile:
        try:
            return self._load()[name]
        except KeyError as exc:
            raise ProfileError(
                f"Remote audio destination profile {name!r} does not exist."
            ) from exc

    def put(self, profile: BroadcastifyDestinationProfile) -> None:
        if not isinstance(profile, BroadcastifyDestinationProfile):
            raise TypeError(
                "Remote audio profile store requires a "
                "BroadcastifyDestinationProfile."
            )
        profiles = self._load()
        profiles[profile.name] = profile
        self._save(profiles)

    def remove(self, name: str) -> None:
        profiles = self._load()
        if name not in profiles:
            raise ProfileError(
                f"Remote audio destination profile {name!r} does not exist."
            )
        del profiles[name]
        self._save(profiles)

    def _load(self) -> dict[str, BroadcastifyDestinationProfile]:
        if not self.path.exists():
            return {}

        try:
            document = tomllib.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ProfileError(
                f"Could not read remote audio profile file {self.path}: {exc}"
            ) from exc

        unexpected_top_level = sorted(
            key for key in document if key not in {"version", "destinations"}
        )
        if unexpected_top_level:
            fields = ", ".join(repr(field) for field in unexpected_top_level)
            raise ProfileError(
                f"Remote audio profile document has unsupported top-level "
                f"field(s): {fields}."
            )

        version = document.get("version")
        if (
            isinstance(version, bool)
            or not isinstance(version, int)
            or version != REMOTE_AUDIO_PROFILE_VERSION
        ):
            raise ProfileError(
                "Remote audio profile document version must be "
                f"{REMOTE_AUDIO_PROFILE_VERSION}; found {version!r}."
            )

        raw_destinations = document.get("destinations", {})
        if not isinstance(raw_destinations, Mapping):
            raise ProfileError(
                "Remote audio profile document must contain a "
                "[destinations] table."
            )

        profiles: dict[str, BroadcastifyDestinationProfile] = {}
        for name, raw in raw_destinations.items():
            if not isinstance(name, str) or not isinstance(raw, Mapping):
                raise ProfileError(
                    "Each remote audio destination must be a named TOML table."
                )
            profiles[name] = self._parse_profile(name, raw)
        return profiles

    @staticmethod
    def _parse_profile(
        name: str,
        raw: Mapping[object, object],
    ) -> BroadcastifyDestinationProfile:
        allowed_fields = {
            "kind",
            "server",
            "mount",
            "environment_variable",
            "port",
            "stream_name",
            "genre",
            "public",
            "ffmpeg_executable",
            "connect_timeout",
            "socket_timeout",
            "encoder_stop_timeout",
            "buffer_seconds",
            "stop_timeout",
            "reconnect_initial_delay",
            "reconnect_multiplier",
            "reconnect_max_delay",
            "reconnect_max_attempts",
        }
        unexpected_fields = sorted(
            str(field) for field in raw if field not in allowed_fields
        )
        if unexpected_fields:
            fields = ", ".join(repr(field) for field in unexpected_fields)
            raise ProfileError(
                f"Remote audio destination profile {name!r} has unsupported "
                f"field(s): {fields}."
            )

        kind = raw.get("kind")
        if kind != "broadcastify":
            raise ProfileError(
                f"Remote audio destination profile {name!r} has unsupported "
                f"kind {kind!r}."
            )

        max_attempts_value = raw.get("reconnect_max_attempts")
        if max_attempts_value is not None and (
            isinstance(max_attempts_value, bool)
            or not isinstance(max_attempts_value, int)
        ):
            raise ProfileError(
                f"Remote audio destination profile {name!r} has invalid "
                "reconnect_max_attempts value."
            )

        return BroadcastifyDestinationProfile(
            name=name,
            server=_string_field(name, raw, "server"),
            mount=_string_field(name, raw, "mount"),
            environment_variable=_string_field(name, raw, "environment_variable"),
            port=_int_field(name, raw, "port", 80),
            stream_name=_string_field(
                name,
                raw,
                "stream_name",
                default="SDS200 scanner feed",
            ),
            genre=_string_field(name, raw, "genre", default="Scanner"),
            public=_bool_field(name, raw, "public", True),
            ffmpeg_executable=_string_field(
                name,
                raw,
                "ffmpeg_executable",
                default="ffmpeg",
            ),
            connect_timeout=_float_field(name, raw, "connect_timeout", 10.0),
            socket_timeout=_float_field(name, raw, "socket_timeout", 10.0),
            encoder_stop_timeout=_float_field(
                name,
                raw,
                "encoder_stop_timeout",
                2.0,
            ),
            buffer_seconds=_float_field(name, raw, "buffer_seconds", 5.0),
            stop_timeout=_float_field(name, raw, "stop_timeout", 5.0),
            reconnect_initial_delay=_float_field(
                name,
                raw,
                "reconnect_initial_delay",
                1.0,
            ),
            reconnect_multiplier=_float_field(
                name,
                raw,
                "reconnect_multiplier",
                2.0,
            ),
            reconnect_max_delay=_float_field(
                name,
                raw,
                "reconnect_max_delay",
                30.0,
            ),
            reconnect_max_attempts=max_attempts_value,
        )

    def _save(
        self,
        profiles: Mapping[str, BroadcastifyDestinationProfile],
    ) -> None:
        lines = [f"version = {REMOTE_AUDIO_PROFILE_VERSION}", ""]
        for name in sorted(profiles):
            profile = profiles[name]
            lines.append(f"[destinations.{json.dumps(name)}]")
            lines.append('kind = "broadcastify"')
            lines.append(f"server = {json.dumps(profile.server)}")
            lines.append(f"mount = {json.dumps(profile.mount)}")
            lines.append(
                "environment_variable = "
                f"{json.dumps(profile.environment_variable)}"
            )
            lines.append(f"port = {profile.port}")
            lines.append(f"stream_name = {json.dumps(profile.stream_name)}")
            lines.append(f"genre = {json.dumps(profile.genre)}")
            lines.append(f"public = {str(profile.public).lower()}")
            lines.append(
                f"ffmpeg_executable = {json.dumps(profile.ffmpeg_executable)}"
            )
            lines.append(f"connect_timeout = {profile.connect_timeout}")
            lines.append(f"socket_timeout = {profile.socket_timeout}")
            lines.append(
                f"encoder_stop_timeout = {profile.encoder_stop_timeout}"
            )
            lines.append(f"buffer_seconds = {profile.buffer_seconds}")
            lines.append(f"stop_timeout = {profile.stop_timeout}")
            lines.append(
                f"reconnect_initial_delay = {profile.reconnect_initial_delay}"
            )
            lines.append(
                f"reconnect_multiplier = {profile.reconnect_multiplier}"
            )
            lines.append(
                f"reconnect_max_delay = {profile.reconnect_max_delay}"
            )
            if profile.reconnect_max_attempts is not None:
                lines.append(
                    "reconnect_max_attempts = "
                    f"{profile.reconnect_max_attempts}"
                )
            lines.append("")

        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text("\n".join(lines), encoding="utf-8")
            temporary.replace(self.path)
        except OSError as exc:
            with suppress(OSError):
                temporary.unlink()
            raise ProfileError(
                f"Could not write remote audio profile file {self.path}: {exc}"
            ) from exc


_MISSING = object()


def _string_field(
    profile_name: str,
    raw: Mapping[object, object],
    field: str,
    *,
    default: object = _MISSING,
) -> str:
    value = raw.get(field, default)
    if not isinstance(value, str):
        raise ProfileError(
            f"Remote audio destination profile {profile_name!r} requires "
            f"a string {field} value."
        )
    return value


def _int_field(
    profile_name: str,
    raw: Mapping[object, object],
    field: str,
    default: int,
) -> int:
    value = raw.get(field, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProfileError(
            f"Remote audio destination profile {profile_name!r} has invalid "
            f"{field} value."
        )
    return value


def _float_field(
    profile_name: str,
    raw: Mapping[object, object],
    field: str,
    default: float,
) -> float:
    value = raw.get(field, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProfileError(
            f"Remote audio destination profile {profile_name!r} has invalid "
            f"{field} value."
        )
    return float(value)


def _bool_field(
    profile_name: str,
    raw: Mapping[object, object],
    field: str,
    default: bool,
) -> bool:
    value = raw.get(field, default)
    if not isinstance(value, bool):
        raise ProfileError(
            f"Remote audio destination profile {profile_name!r} has invalid "
            f"{field} value."
        )
    return value
