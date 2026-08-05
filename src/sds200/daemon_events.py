from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite
from types import MappingProxyType
from typing import cast

DAEMON_EVENT_PROTOCOL = "sdsctl.daemon.events"
DAEMON_EVENT_VERSION = 1
DAEMON_EVENT_SUPPORTED_VERSIONS = (DAEMON_EVENT_VERSION,)


class DaemonEventKind(StrEnum):
    """Stable event kinds published by the local daemon event stream."""

    SNAPSHOT = "stream.snapshot"
    DAEMON_TRANSITION = "daemon.transition"
    SCANNER_CONNECTION = "scanner.connection"
    PSI_STATE = "scanner.psi"
    RADIO_STATE = "radio.state"
    AUDIO_STATE = "audio.state"
    DESTINATION_HEALTH = "destination.health"


@dataclass(frozen=True, slots=True)
class DaemonEvent:
    """One immutable JSON-compatible event-stream envelope."""

    sequence: int
    observed_at: datetime
    kind: str
    payload: Mapping[str, object] = field(default_factory=dict)
    protocol: str = DAEMON_EVENT_PROTOCOL
    version: int = DAEMON_EVENT_VERSION

    def __post_init__(self) -> None:
        if type(self.sequence) is not int:
            raise TypeError("Daemon event sequence must be an integer.")
        if self.sequence < 0:
            raise ValueError("Daemon event sequence must not be negative.")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("Daemon event timestamps must be timezone-aware.")
        if not isinstance(self.kind, str):
            raise TypeError("Daemon event kind must be a string.")
        if not self.kind or self.kind.strip() != self.kind:
            raise ValueError("Daemon event kind must not be empty or padded.")
        if any(ord(character) < 0x20 for character in self.kind):
            raise ValueError(
                "Daemon event kind must not contain control characters."
            )
        if self.protocol != DAEMON_EVENT_PROTOCOL:
            raise ValueError(
                f"Unsupported daemon event protocol: {self.protocol!r}."
            )
        if type(self.version) is not int:
            raise TypeError("Daemon event version must be an integer.")
        if self.version not in DAEMON_EVENT_SUPPORTED_VERSIONS:
            raise ValueError(
                "Unsupported daemon event version: "
                f"{self.version}; "
                f"supported={list(DAEMON_EVENT_SUPPORTED_VERSIONS)!r}."
            )
        if not isinstance(self.payload, Mapping):
            raise TypeError("Daemon event payload must be a mapping.")

        object.__setattr__(self, "kind", str(self.kind))
        object.__setattr__(
            self,
            "payload",
            _freeze_mapping(cast(Mapping[object, object], self.payload)),
        )

    @classmethod
    def create(
        cls,
        sequence: int,
        kind: str,
        payload: Mapping[str, object],
        *,
        observed_at: datetime | None = None,
    ) -> DaemonEvent:
        return cls(
            sequence=sequence,
            observed_at=observed_at or datetime.now(UTC),
            kind=kind,
            payload=payload,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "protocol": self.protocol,
            "version": self.version,
            "sequence": self.sequence,
            "observed_at": self.observed_at.isoformat(),
            "kind": self.kind,
            "payload": {
                key: _thaw_json(value)
                for key, value in self.payload.items()
            },
        }

    def to_json_line(self) -> bytes:
        return (
            json.dumps(
                self.as_dict(),
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")


def _freeze_mapping(
    value: Mapping[object, object],
) -> Mapping[str, object]:
    frozen: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError("Daemon event payload field names must be strings.")
        frozen[key] = _freeze_json(item)
    return MappingProxyType(frozen)


def _freeze_json(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError(
                "Daemon event payload numbers must be finite."
            )
        return value
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    raise TypeError(
        "Daemon event payload values must be JSON-compatible; "
        f"received {type(value).__name__}."
    )


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _thaw_json(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value
