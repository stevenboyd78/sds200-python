from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Literal, TypeAlias, cast

from .configuration import (
    DAEMON_DESTINATION_CONFIG_FILENAME,
    ConfigurationPaths,
    resolve_configuration_paths,
)
from .exceptions import ConfigurationError

DAEMON_DESTINATION_CONFIG_VERSION = 1

DaemonPlaybackBackend: TypeAlias = Literal[
    "auto",
    "sounddevice",
    "pipewire",
    "pulseaudio",
    "alsa",
]
DaemonDestinationKind: TypeAlias = Literal[
    "playback",
    "recording",
    "remote-profile",
]
DaemonDestinationChangeAction: TypeAlias = Literal[
    "added",
    "removed",
    "replaced",
    "unchanged",
]

DAEMON_PLAYBACK_BACKENDS: tuple[DaemonPlaybackBackend, ...] = (
    "auto",
    "sounddevice",
    "pipewire",
    "pulseaudio",
    "alsa",
)


def _require_name(value: object, *, label: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string.")
    if not value or value.strip() != value:
        raise ValueError(f"{label} must not be empty or padded.")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{label} must not contain control characters.")
    return value


def _require_positive_integer(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer.")
    if value <= 0:
        raise ValueError(f"{label} must be greater than zero.")
    return value


def _require_number(
    value: object,
    *,
    label: str,
    minimum: float,
    inclusive: bool,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be a number.")
    normalized = float(value)
    if not isfinite(normalized):
        raise ValueError(f"{label} must be finite.")
    if inclusive:
        invalid = normalized < minimum
        description = f"at least {minimum:g}"
    else:
        invalid = normalized <= minimum
        description = f"greater than {minimum:g}"
    if invalid:
        raise ValueError(f"{label} must be {description}.")
    return normalized


def _require_device(value: object) -> str | int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError(
            "Daemon playback device must be a string, integer, or null."
        )
    if isinstance(value, int):
        if value < 0:
            raise ValueError(
                "Daemon playback device index must not be negative."
            )
        return value
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            raise ValueError(
                "Daemon playback device must not be empty."
            )
        return normalized
    raise TypeError(
        "Daemon playback device must be a string, integer, or null."
    )


@dataclass(frozen=True, slots=True)
class DaemonPlaybackDestination:
    """Saved daemon-owned local playback destination."""

    name: str
    backend: DaemonPlaybackBackend = "auto"
    device: str | int | None = None
    buffer_ms: int = 250

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "name",
            _require_name(
                self.name,
                label="Daemon playback destination name",
            ),
        )
        if not isinstance(self.backend, str):
            raise TypeError("Daemon playback backend must be a string.")
        backend = self.backend.strip().lower()
        if backend not in DAEMON_PLAYBACK_BACKENDS:
            choices = ", ".join(DAEMON_PLAYBACK_BACKENDS)
            raise ValueError(
                "Daemon playback backend must be one of: "
                f"{choices}."
            )
        object.__setattr__(self, "backend", backend)
        object.__setattr__(self, "device", _require_device(self.device))
        object.__setattr__(
            self,
            "buffer_ms",
            _require_positive_integer(
                self.buffer_ms,
                label="Daemon playback buffer",
            ),
        )

    @property
    def kind(self) -> Literal["playback"]:
        return "playback"

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind,
            "backend": self.backend,
            "device": self.device,
            "buffer_ms": self.buffer_ms,
        }


@dataclass(frozen=True, slots=True)
class DaemonRecordingDestination:
    """Saved daemon-owned continuous WAV recording destination."""

    name: str
    path: Path
    overwrite: bool = False
    buffer_seconds: float = 5.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "name",
            _require_name(
                self.name,
                label="Daemon recording destination name",
            ),
        )
        if not isinstance(self.path, (str, Path)):
            raise TypeError(
                "Daemon recording path must be a path."
            )
        if isinstance(self.path, str) and not self.path.strip():
            raise ValueError(
                "Daemon recording path must not be empty."
            )
        path = Path(self.path)
        if not path.is_absolute():
            raise ValueError(
                "Daemon recording path must be absolute."
            )
        object.__setattr__(self, "path", path)

        if not isinstance(self.overwrite, bool):
            raise TypeError(
                "Daemon recording overwrite must be a boolean."
            )
        object.__setattr__(
            self,
            "buffer_seconds",
            _require_number(
                self.buffer_seconds,
                label="Daemon recording buffer",
                minimum=0.0,
                inclusive=False,
            ),
        )

    @property
    def kind(self) -> Literal["recording"]:
        return "recording"

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind,
            "path": str(self.path),
            "overwrite": self.overwrite,
            "buffer_seconds": self.buffer_seconds,
        }


@dataclass(frozen=True, slots=True)
class DaemonRemoteProfileDestination:
    """Saved daemon destination referencing a remote-audio profile."""

    name: str
    profile: str
    publish_metadata: bool = True
    metadata_minimum_update_interval: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "name",
            _require_name(
                self.name,
                label="Daemon remote destination name",
            ),
        )
        object.__setattr__(
            self,
            "profile",
            _require_name(
                self.profile,
                label="Daemon remote profile name",
            ),
        )
        if not isinstance(self.publish_metadata, bool):
            raise TypeError(
                "Daemon remote metadata activation must be a boolean."
            )
        object.__setattr__(
            self,
            "metadata_minimum_update_interval",
            _require_number(
                self.metadata_minimum_update_interval,
                label="Daemon remote metadata minimum update interval",
                minimum=0.0,
                inclusive=True,
            ),
        )

    @property
    def kind(self) -> Literal["remote-profile"]:
        return "remote-profile"

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind,
            "profile": self.profile,
            "publish_metadata": self.publish_metadata,
            "metadata_minimum_update_interval": (
                self.metadata_minimum_update_interval
            ),
        }


DaemonDestination: TypeAlias = (
    DaemonPlaybackDestination
    | DaemonRecordingDestination
    | DaemonRemoteProfileDestination
)

_DESTINATION_TYPES = (
    DaemonPlaybackDestination,
    DaemonRecordingDestination,
    DaemonRemoteProfileDestination,
)


@dataclass(frozen=True, slots=True)
class DaemonDestinationConfiguration:
    """Immutable desired daemon destination set."""

    destinations: tuple[DaemonDestination, ...] = ()

    def __post_init__(self) -> None:
        copied = tuple(self.destinations)
        for destination in copied:
            if not isinstance(destination, _DESTINATION_TYPES):
                raise TypeError(
                    "Daemon destination configurations require typed "
                    "destination entries."
                )

        names = [destination.name for destination in copied]
        duplicates = sorted(
            name for name in set(names) if names.count(name) > 1
        )
        if duplicates:
            rendered = ", ".join(repr(name) for name in duplicates)
            raise ValueError(
                "Daemon destination names must be unique; duplicate(s): "
                f"{rendered}."
            )

        object.__setattr__(
            self,
            "destinations",
            tuple(
                sorted(
                    copied,
                    key=lambda destination: destination.name,
                )
            ),
        )

    def destination(self, name: str) -> DaemonDestination:
        normalized = _require_name(
            name,
            label="Daemon destination lookup name",
        )
        for destination in self.destinations:
            if destination.name == normalized:
                return destination
        raise KeyError(
            f"Daemon destination {normalized!r} does not exist."
        )

    def as_dict(self) -> dict[str, object]:
        serialized: dict[str, object] = {}
        for destination in self.destinations:
            payload = destination.as_dict()
            payload.pop("name")
            serialized[destination.name] = payload
        return {
            "version": DAEMON_DESTINATION_CONFIG_VERSION,
            "destinations": serialized,
        }


@dataclass(frozen=True, slots=True)
class DaemonDestinationChange:
    """One deterministic desired-configuration replacement decision."""

    name: str
    action: DaemonDestinationChangeAction
    before: DaemonDestination | None
    after: DaemonDestination | None

    def __post_init__(self) -> None:
        _require_name(
            self.name,
            label="Daemon destination change name",
        )
        expected: dict[
            DaemonDestinationChangeAction,
            tuple[bool, bool],
        ] = {
            "added": (False, True),
            "removed": (True, False),
            "replaced": (True, True),
            "unchanged": (True, True),
        }
        if self.action not in expected:
            raise ValueError(
                "Unsupported daemon destination change action."
            )

        before_required, after_required = expected[self.action]
        if (self.before is not None) != before_required:
            raise ValueError(
                "Daemon destination change has an invalid before value."
            )
        if (self.after is not None) != after_required:
            raise ValueError(
                "Daemon destination change has an invalid after value."
            )
        if self.before is not None and self.before.name != self.name:
            raise ValueError(
                "Daemon destination change before name does not match."
            )
        if self.after is not None and self.after.name != self.name:
            raise ValueError(
                "Daemon destination change after name does not match."
            )
        if (
            self.action == "unchanged"
            and self.before != self.after
        ):
            raise ValueError(
                "Unchanged daemon destination entries must be equal."
            )
        if (
            self.action == "replaced"
            and self.before == self.after
        ):
            raise ValueError(
                "Replaced daemon destination entries must differ."
            )

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "action": self.action,
            "before": (
                None
                if self.before is None
                else self.before.as_dict()
            ),
            "after": (
                None
                if self.after is None
                else self.after.as_dict()
            ),
        }


@dataclass(frozen=True, slots=True)
class DaemonDestinationReplacementPreview:
    """Immutable deterministic preview of one desired-set replacement."""

    changes: tuple[DaemonDestinationChange, ...]

    def __post_init__(self) -> None:
        copied = tuple(self.changes)
        names = tuple(change.name for change in copied)
        if names != tuple(sorted(names)):
            raise ValueError(
                "Daemon destination preview changes must be name-sorted."
            )
        if len(set(names)) != len(names):
            raise ValueError(
                "Daemon destination preview changes must be unique."
            )
        object.__setattr__(self, "changes", copied)

    @property
    def changed(self) -> bool:
        return any(
            change.action != "unchanged"
            for change in self.changes
        )

    def names_for(
        self,
        action: DaemonDestinationChangeAction,
    ) -> tuple[str, ...]:
        if action not in {
            "added",
            "removed",
            "replaced",
            "unchanged",
        }:
            raise ValueError(
                "Unsupported daemon destination change action."
            )
        return tuple(
            change.name
            for change in self.changes
            if change.action == action
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "changed": self.changed,
            "added": list(self.names_for("added")),
            "removed": list(self.names_for("removed")),
            "replaced": list(self.names_for("replaced")),
            "unchanged": list(self.names_for("unchanged")),
            "changes": [
                change.as_dict()
                for change in self.changes
            ],
        }


def default_daemon_destination_config_path(
    paths: ConfigurationPaths | None = None,
) -> Path:
    """Return the deterministic user destination-manifest path."""

    resolved = paths or resolve_configuration_paths()
    return resolved.user_config_dir / DAEMON_DESTINATION_CONFIG_FILENAME


def load_daemon_destination_configuration(
    path: str | Path | None = None,
    *,
    paths: ConfigurationPaths | None = None,
) -> DaemonDestinationConfiguration:
    """Load one strict versioned daemon destination manifest."""

    if path is not None and paths is not None:
        raise ValueError(
            "Specify a daemon destination path or configuration paths, "
            "not both."
        )

    config_path = (
        default_daemon_destination_config_path(paths)
        if path is None
        else Path(path)
    )
    if not config_path.exists():
        return DaemonDestinationConfiguration()

    try:
        document = tomllib.loads(
            config_path.read_text(encoding="utf-8")
        )
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigurationError(
            "Could not read daemon destination configuration "
            f"{config_path}: {error}"
        ) from error

    unexpected_top_level = sorted(
        str(field)
        for field in document
        if field not in {"version", "destinations"}
    )
    if unexpected_top_level:
        fields = ", ".join(
            repr(field)
            for field in unexpected_top_level
        )
        raise ConfigurationError(
            "Daemon destination configuration "
            f"{config_path} has unsupported top-level field(s): "
            f"{fields}."
        )

    version = document.get("version")
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version != DAEMON_DESTINATION_CONFIG_VERSION
    ):
        raise ConfigurationError(
            "Daemon destination configuration "
            f"{config_path} version must be "
            f"{DAEMON_DESTINATION_CONFIG_VERSION}."
        )

    raw_destinations = document.get("destinations", {})
    if not isinstance(raw_destinations, Mapping):
        raise ConfigurationError(
            "Daemon destination configuration "
            f"{config_path} must contain a [destinations] table."
        )

    if any(
        not isinstance(name, str)
        for name in raw_destinations
    ):
        raise ConfigurationError(
            "Every daemon destination must use a string table name."
        )

    destinations: list[DaemonDestination] = []
    for name in sorted(raw_destinations):
        raw = raw_destinations[name]
        if not isinstance(raw, Mapping):
            raise ConfigurationError(
                "Daemon destination "
                f"{name!r} in {config_path} must be a table."
            )
        destinations.append(
            _parse_destination(
                name,
                raw,
                path=config_path,
            )
        )

    try:
        return DaemonDestinationConfiguration(tuple(destinations))
    except (TypeError, ValueError) as error:
        raise ConfigurationError(
            "Invalid daemon destination configuration "
            f"{config_path}: {error}"
        ) from error


def preview_daemon_destination_replacement(
    current: DaemonDestinationConfiguration,
    replacement: DaemonDestinationConfiguration,
) -> DaemonDestinationReplacementPreview:
    """Return a stable add/remove/replace/unchanged preview."""

    if not isinstance(current, DaemonDestinationConfiguration):
        raise TypeError(
            "Current daemon destinations must be a "
            "DaemonDestinationConfiguration."
        )
    if not isinstance(replacement, DaemonDestinationConfiguration):
        raise TypeError(
            "Replacement daemon destinations must be a "
            "DaemonDestinationConfiguration."
        )

    before = {
        destination.name: destination
        for destination in current.destinations
    }
    after = {
        destination.name: destination
        for destination in replacement.destinations
    }

    changes: list[DaemonDestinationChange] = []
    for name in sorted(before.keys() | after.keys()):
        previous = before.get(name)
        desired = after.get(name)
        if previous is None:
            action: DaemonDestinationChangeAction = "added"
        elif desired is None:
            action = "removed"
        elif previous == desired:
            action = "unchanged"
        else:
            action = "replaced"

        changes.append(
            DaemonDestinationChange(
                name=name,
                action=action,
                before=previous,
                after=desired,
            )
        )

    return DaemonDestinationReplacementPreview(tuple(changes))


def _parse_destination(
    name: str,
    raw: Mapping[object, object],
    *,
    path: Path,
) -> DaemonDestination:
    kind = raw.get("kind")
    try:
        if kind == "playback":
            _reject_unexpected_fields(
                name,
                raw,
                {
                    "kind",
                    "backend",
                    "device",
                    "buffer_ms",
                },
                path=path,
            )
            return DaemonPlaybackDestination(
                name=name,
                backend=cast(
                    DaemonPlaybackBackend,
                    _string_field(
                        name,
                        raw,
                        "backend",
                        default="auto",
                        path=path,
                    ),
                ),
                device=cast(
                    str | int | None,
                    raw.get("device"),
                ),
                buffer_ms=_integer_field(
                    name,
                    raw,
                    "buffer_ms",
                    default=250,
                    path=path,
                ),
            )

        if kind == "recording":
            _reject_unexpected_fields(
                name,
                raw,
                {
                    "kind",
                    "path",
                    "overwrite",
                    "buffer_seconds",
                },
                path=path,
            )
            return DaemonRecordingDestination(
                name=name,
                path=Path(
                    _string_field(
                        name,
                        raw,
                        "path",
                        path=path,
                    )
                ),
                overwrite=_boolean_field(
                    name,
                    raw,
                    "overwrite",
                    default=False,
                    path=path,
                ),
                buffer_seconds=_number_field(
                    name,
                    raw,
                    "buffer_seconds",
                    default=5.0,
                    path=path,
                ),
            )

        if kind == "remote-profile":
            _reject_unexpected_fields(
                name,
                raw,
                {
                    "kind",
                    "profile",
                    "publish_metadata",
                    "metadata_minimum_update_interval",
                },
                path=path,
            )
            return DaemonRemoteProfileDestination(
                name=name,
                profile=_string_field(
                    name,
                    raw,
                    "profile",
                    path=path,
                ),
                publish_metadata=_boolean_field(
                    name,
                    raw,
                    "publish_metadata",
                    default=True,
                    path=path,
                ),
                metadata_minimum_update_interval=_number_field(
                    name,
                    raw,
                    "metadata_minimum_update_interval",
                    default=0.0,
                    path=path,
                ),
            )
    except (TypeError, ValueError) as error:
        raise ConfigurationError(
            f"Invalid daemon destination {name!r} in {path}: "
            f"{error}"
        ) from error

    raise ConfigurationError(
        f"Daemon destination {name!r} in {path} has an unsupported "
        "or missing kind."
    )


def _reject_unexpected_fields(
    name: str,
    raw: Mapping[object, object],
    allowed: set[str],
    *,
    path: Path,
) -> None:
    unexpected = sorted(
        str(field)
        for field in raw
        if field not in allowed
    )
    if not unexpected:
        return
    fields = ", ".join(repr(field) for field in unexpected)
    raise ConfigurationError(
        f"Daemon destination {name!r} in {path} has unsupported "
        f"field(s): {fields}."
    )


_MISSING = object()


def _string_field(
    name: str,
    raw: Mapping[object, object],
    field: str,
    *,
    path: Path,
    default: object = _MISSING,
) -> str:
    value = raw.get(field, default)
    if not isinstance(value, str):
        raise ConfigurationError(
            f"Daemon destination {name!r} in {path} requires "
            f"a string {field} value."
        )
    return value


def _integer_field(
    name: str,
    raw: Mapping[object, object],
    field: str,
    *,
    path: Path,
    default: int,
) -> int:
    value = raw.get(field, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigurationError(
            f"Daemon destination {name!r} in {path} requires "
            f"an integer {field} value."
        )
    return value


def _number_field(
    name: str,
    raw: Mapping[object, object],
    field: str,
    *,
    path: Path,
    default: float,
) -> float:
    value = raw.get(field, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError(
            f"Daemon destination {name!r} in {path} requires "
            f"a numeric {field} value."
        )
    return float(value)


def _boolean_field(
    name: str,
    raw: Mapping[object, object],
    field: str,
    *,
    path: Path,
    default: bool,
) -> bool:
    value = raw.get(field, default)
    if not isinstance(value, bool):
        raise ConfigurationError(
            f"Daemon destination {name!r} in {path} requires "
            f"a boolean {field} value."
        )
    return value
