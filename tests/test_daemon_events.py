from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast

import pytest

from sds200 import (
    DAEMON_EVENT_PROTOCOL,
    DAEMON_EVENT_SUPPORTED_VERSIONS,
    DAEMON_EVENT_VERSION,
    DaemonEvent,
    DaemonEventKind,
)


def make_event(
    *,
    sequence: int = 7,
    kind: str = DaemonEventKind.RADIO_STATE,
    payload: Mapping[str, object] | None = None,
) -> DaemonEvent:
    return DaemonEvent(
        sequence=sequence,
        observed_at=datetime(2026, 8, 4, 12, 30, tzinfo=UTC),
        kind=kind,
        payload={} if payload is None else payload,
    )


def test_event_protocol_contract_is_stable() -> None:
    assert DAEMON_EVENT_PROTOCOL == "sdsctl.daemon.events"
    assert DAEMON_EVENT_VERSION == 1
    assert DAEMON_EVENT_SUPPORTED_VERSIONS == (1,)
    assert [kind.value for kind in DaemonEventKind] == [
        "stream.snapshot",
        "daemon.transition",
        "scanner.connection",
        "scanner.psi",
        "radio.state",
        "audio.state",
        "recording.state",
        "destination.health",
    ]


def test_event_payload_is_deeply_copied_and_immutable() -> None:
    source: dict[str, object] = {
        "nested": {"value": 1},
        "items": ["first", {"healthy": True}],
    }

    event = make_event(payload=source)

    cast(dict[str, object], source["nested"])["value"] = 2
    cast(list[object], source["items"]).append("late")

    assert event.as_dict()["payload"] == {
        "nested": {"value": 1},
        "items": ["first", {"healthy": True}],
    }

    with pytest.raises(TypeError):
        cast(dict[str, object], event.payload)["new"] = "value"

    nested = cast(Mapping[str, object], event.payload["nested"])
    with pytest.raises(TypeError):
        cast(dict[str, object], nested)["value"] = 3

    assert isinstance(event.payload["items"], tuple)


def test_event_dictionary_and_json_line_are_json_compatible() -> None:
    event = make_event(
        payload={
            "connected": True,
            "count": 3,
            "ratio": 0.5,
            "optional": None,
            "fields": ("channel", "frequency"),
        }
    )

    payload = event.as_dict()

    assert payload == {
        "protocol": DAEMON_EVENT_PROTOCOL,
        "version": DAEMON_EVENT_VERSION,
        "sequence": 7,
        "observed_at": "2026-08-04T12:30:00+00:00",
        "kind": "radio.state",
        "payload": {
            "connected": True,
            "count": 3,
            "ratio": 0.5,
            "optional": None,
            "fields": ["channel", "frequency"],
        },
    }
    assert json.loads(json.dumps(payload)) == payload
    assert event.to_json_line().endswith(b"\n")
    assert json.loads(event.to_json_line()) == payload


def test_create_uses_an_aware_current_timestamp() -> None:
    event = DaemonEvent.create(
        1,
        DaemonEventKind.SNAPSHOT,
        {"state": "running"},
    )

    assert event.observed_at.tzinfo is not None
    assert event.observed_at.utcoffset() is not None


@pytest.mark.parametrize("sequence", [True, 1.5, "1"])
def test_event_rejects_non_integer_sequences(sequence: object) -> None:
    with pytest.raises(TypeError, match="sequence"):
        make_event(sequence=sequence)  # type: ignore[arg-type]


def test_event_rejects_negative_sequence() -> None:
    with pytest.raises(ValueError, match="negative"):
        make_event(sequence=-1)


@pytest.mark.parametrize("kind", ["", " padded", "padded ", "bad\nkind"])
def test_event_rejects_invalid_kinds(kind: str) -> None:
    with pytest.raises(ValueError, match="kind"):
        make_event(kind=kind)


def test_event_requires_an_aware_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        DaemonEvent(
            sequence=1,
            observed_at=datetime(2026, 8, 4, 12, 30),
            kind=DaemonEventKind.SNAPSHOT,
        )


def test_event_rejects_non_string_payload_keys() -> None:
    payload = cast(Mapping[str, object], {1: "invalid"})

    with pytest.raises(TypeError, match="field names"):
        make_event(payload=payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"value": object()},
        {"value": {1, 2}},
        {"value": float("inf")},
        {"value": float("nan")},
    ],
)
def test_event_rejects_non_json_payload_values(
    payload: Mapping[str, object],
) -> None:
    with pytest.raises((TypeError, ValueError), match="payload"):
        make_event(payload=payload)


def test_event_rejects_protocol_or_version_changes() -> None:
    with pytest.raises(ValueError, match="protocol"):
        DaemonEvent(
            sequence=1,
            observed_at=datetime.now(UTC),
            kind=DaemonEventKind.SNAPSHOT,
            protocol="other.protocol",
        )

    with pytest.raises(ValueError, match="version"):
        DaemonEvent(
            sequence=1,
            observed_at=datetime.now(UTC),
            kind=DaemonEventKind.SNAPSHOT,
            version=2,
        )
