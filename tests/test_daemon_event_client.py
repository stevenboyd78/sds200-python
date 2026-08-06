from __future__ import annotations

import json
import socket
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sds200 import (
    DAEMON_EVENT_PROTOCOL,
    DAEMON_EVENT_VERSION,
    DaemonDisconnectedError,
    DaemonEvent,
    DaemonEventClient,
    DaemonEventKind,
    DaemonEventPublisher,
    DaemonEventServer,
    DaemonEventSubscription,
    DaemonProtocolError,
    DaemonSocketListener,
    DaemonSocketLocation,
    DaemonSocketSource,
    DaemonUnavailableError,
)

SNAPSHOT = {
    "state": "running",
    "scanner_endpoint": "udp://192.0.2.25:50536",
    "scanner_model": "SDS200",
    "scanner_firmware": "Version 1.26.01",
    "scanner_connected": True,
    "psi_interval_ms": 500,
    "psi_active": True,
    "radio_state": {},
    "audio": {"running": True},
    "router": {"running": True},
    "started_at": "2026-08-05T11:00:00+00:00",
    "stopped_at": None,
    "state_changed_at": "2026-08-05T11:00:00+00:00",
    "transition_sequence": 2,
    "last_failure_at": None,
    "last_error": None,
}


class FakeEventStream:
    def __init__(self) -> None:
        self.publisher = DaemonEventPublisher(lambda: SNAPSHOT)

    def subscribe(self) -> DaemonEventSubscription:
        return self.publisher.subscribe()

    def publish(
        self,
        kind: DaemonEventKind,
        payload: Mapping[str, object],
    ) -> DaemonEvent:
        return self.publisher.publish(kind, payload)

    def close(self) -> None:
        self.publisher.close()


def make_server(
    tmp_path: Path,
) -> tuple[DaemonEventServer, Path, FakeEventStream]:
    path = tmp_path / "events.sock"
    stream = FakeEventStream()
    server = DaemonEventServer(
        DaemonSocketListener(
            DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
        ),
        stream,
    )
    return server, path, stream


def event_line(
    sequence: int,
    kind: str,
    payload: Mapping[str, object],
    *,
    protocol: str = DAEMON_EVENT_PROTOCOL,
    version: int = DAEMON_EVENT_VERSION,
    observed_at: str = "2026-08-05T11:00:00+00:00",
) -> bytes:
    return (
        json.dumps(
            {
                "protocol": protocol,
                "version": version,
                "sequence": sequence,
                "observed_at": observed_at,
                "kind": kind,
                "payload": dict(payload),
            }
        )
        + "\n"
    ).encode("utf-8")


def start_scripted_server(
    path: Path,
    payload: bytes | None,
) -> threading.Thread:
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen(1)

    def serve() -> None:
        try:
            client, _ = listener.accept()
            with client:
                if payload is not None:
                    client.sendall(payload)
        finally:
            listener.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return thread


@pytest.mark.parametrize(
    ("keyword", "value", "error_type"),
    [
        ("timeout", True, TypeError),
        ("timeout", 0, ValueError),
        ("timeout", float("inf"), ValueError),
        ("max_event_bytes", True, TypeError),
        ("max_event_bytes", 0, ValueError),
    ],
)
def test_event_client_rejects_invalid_limits(
    tmp_path: Path,
    keyword: str,
    value: object,
    error_type: type[Exception],
) -> None:
    location = DaemonSocketLocation(
        tmp_path / "events.sock",
        DaemonSocketSource.EXPLICIT,
    )

    with pytest.raises(error_type):
        DaemonEventClient(location, **{keyword: value})  # type: ignore[arg-type]


def test_event_client_receives_snapshot_then_ordered_event(
    tmp_path: Path,
) -> None:
    server, path, stream = make_server(tmp_path)
    location = DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)

    with server, DaemonEventClient(location) as client:
        snapshot = client.receive()
        published = stream.publish(
            DaemonEventKind.SCANNER_CONNECTION,
            {
                "endpoint": "udp://192.0.2.25:50536",
                "connected": True,
            },
        )
        event = client.receive()

        assert client.connected is True
        assert client.last_sequence == event.sequence

    assert snapshot.kind == DaemonEventKind.SNAPSHOT
    assert snapshot.payload == SNAPSHOT
    assert event.as_dict() == published.as_dict()
    assert client.connected is False


def test_event_client_filters_kinds_and_bounds_matching_count(
    tmp_path: Path,
) -> None:
    path = tmp_path / "filtered.sock"
    payload = b"".join(
        (
            event_line(7, DaemonEventKind.SNAPSHOT, SNAPSHOT),
            event_line(
                8,
                DaemonEventKind.SCANNER_CONNECTION,
                {"connected": True},
            ),
            event_line(
                9,
                DaemonEventKind.RADIO_STATE,
                {"fields": ["channel"]},
            ),
        )
    )
    thread = start_scripted_server(path, payload)
    client = DaemonEventClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    events = list(
        client.watch(
            kinds=[DaemonEventKind.RADIO_STATE],
            count=1,
        )
    )

    client.close()
    thread.join(timeout=1.0)
    assert [event.sequence for event in events] == [9]
    assert [event.kind for event in events] == [
        DaemonEventKind.RADIO_STATE
    ]


@pytest.mark.parametrize(
    "kind_filter",
    [
        DaemonEventKind.RADIO_STATE,
        DaemonEventKind.RADIO_STATE.value,
    ],
)
def test_event_client_accepts_one_kind_without_a_container(
    tmp_path: Path,
    kind_filter: str | DaemonEventKind,
) -> None:
    path = tmp_path / "single-kind.sock"
    payload = b"".join(
        (
            event_line(7, DaemonEventKind.SNAPSHOT, SNAPSHOT),
            event_line(
                8,
                DaemonEventKind.RADIO_STATE,
                {"fields": ["channel"]},
            ),
        )
    )
    thread = start_scripted_server(path, payload)
    client = DaemonEventClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    events = list(client.watch(kinds=kind_filter, count=1))

    client.close()
    thread.join(timeout=1.0)
    assert [event.sequence for event in events] == [8]
    assert [event.kind for event in events] == [
        DaemonEventKind.RADIO_STATE
    ]


def test_event_client_reports_missing_socket(tmp_path: Path) -> None:
    path = tmp_path / "missing.sock"
    client = DaemonEventClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    with pytest.raises(DaemonUnavailableError, match="event socket was not found"):
        client.receive()

    assert client.connected is False


def test_event_client_reports_refused_stale_socket(tmp_path: Path) -> None:
    path = tmp_path / "stale.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.close()
    client = DaemonEventClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    with pytest.raises(DaemonUnavailableError, match="not accepting"):
        client.receive()

    assert client.connected is False


def test_event_client_reports_disconnect_before_first_event(
    tmp_path: Path,
) -> None:
    path = tmp_path / "disconnect.sock"
    thread = start_scripted_server(path, None)
    client = DaemonEventClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    with pytest.raises(DaemonDisconnectedError, match="disconnected"):
        client.receive()

    thread.join(timeout=1.0)
    assert client.connected is False


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json\n",
        b"\xff\n",
        json.dumps([]).encode("utf-8") + b"\n",
    ],
)
def test_event_client_rejects_malformed_event(
    tmp_path: Path,
    payload: bytes,
) -> None:
    path = tmp_path / "malformed.sock"
    thread = start_scripted_server(path, payload)
    client = DaemonEventClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    with pytest.raises(DaemonProtocolError):
        client.receive()

    thread.join(timeout=1.0)
    assert client.connected is False


def test_event_client_rejects_incomplete_json_line(
    tmp_path: Path,
) -> None:
    path = tmp_path / "incomplete.sock"
    payload = event_line(
        0,
        DaemonEventKind.SNAPSHOT,
        SNAPSHOT,
    ).removesuffix(b"\n")
    thread = start_scripted_server(path, payload)
    client = DaemonEventClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    with pytest.raises(DaemonProtocolError, match="incomplete"):
        client.receive()

    thread.join(timeout=1.0)
    assert client.connected is False


def test_event_client_rejects_incompatible_protocol(tmp_path: Path) -> None:
    path = tmp_path / "protocol.sock"
    thread = start_scripted_server(
        path,
        event_line(
            0,
            DaemonEventKind.SNAPSHOT,
            SNAPSHOT,
            protocol="other.events",
        ),
    )
    client = DaemonEventClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    with pytest.raises(DaemonProtocolError, match="protocol"):
        client.receive()

    thread.join(timeout=1.0)
    assert client.connected is False


def test_event_client_rejects_incompatible_version(tmp_path: Path) -> None:
    path = tmp_path / "version.sock"
    thread = start_scripted_server(
        path,
        event_line(
            0,
            DaemonEventKind.SNAPSHOT,
            SNAPSHOT,
            version=DAEMON_EVENT_VERSION + 1,
        ),
    )
    client = DaemonEventClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    with pytest.raises(DaemonProtocolError, match="version"):
        client.receive()

    thread.join(timeout=1.0)
    assert client.connected is False


def test_event_client_requires_initial_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "initial.sock"
    thread = start_scripted_server(
        path,
        event_line(
            1,
            DaemonEventKind.SCANNER_CONNECTION,
            {"connected": True},
        ),
    )
    client = DaemonEventClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    with pytest.raises(DaemonProtocolError, match="did not begin"):
        client.receive()

    thread.join(timeout=1.0)
    assert client.connected is False


def test_event_client_validates_snapshot_payload(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.sock"
    thread = start_scripted_server(
        path,
        event_line(
            0,
            DaemonEventKind.SNAPSHOT,
            {"state": "running"},
        ),
    )
    client = DaemonEventClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    with pytest.raises(DaemonProtocolError, match="runtime fields"):
        client.receive()

    thread.join(timeout=1.0)
    assert client.connected is False


@pytest.mark.parametrize(
    "identity",
    [
        {},
        {"scanner_model": None, "scanner_firmware": None},
    ],
)
def test_event_client_accepts_optional_snapshot_identity(
    tmp_path: Path,
    identity: dict[str, object],
) -> None:
    path = tmp_path / "optional-identity.sock"
    snapshot = dict(SNAPSHOT)
    snapshot.pop("scanner_model")
    snapshot.pop("scanner_firmware")
    snapshot.update(identity)
    thread = start_scripted_server(
        path,
        event_line(0, DaemonEventKind.SNAPSHOT, snapshot),
    )
    client = DaemonEventClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    event = client.receive()

    assert event.payload == snapshot
    client.close()
    thread.join(timeout=1.0)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("scanner_model", ""),
        ("scanner_model", 200),
        ("scanner_firmware", ""),
        ("scanner_firmware", False),
    ],
)
def test_event_client_rejects_malformed_snapshot_identity(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    path = tmp_path / f"malformed-{field}.sock"
    snapshot = dict(SNAPSHOT)
    snapshot[field] = value
    thread = start_scripted_server(
        path,
        event_line(0, DaemonEventKind.SNAPSHOT, snapshot),
    )
    client = DaemonEventClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    with pytest.raises(DaemonProtocolError, match=field):
        client.receive()

    thread.join(timeout=1.0)
    assert client.connected is False


def test_event_client_rejects_sequence_gap(tmp_path: Path) -> None:
    path = tmp_path / "gap.sock"
    thread = start_scripted_server(
        path,
        b"".join(
            (
                event_line(4, DaemonEventKind.SNAPSHOT, SNAPSHOT),
                event_line(
                    6,
                    DaemonEventKind.RADIO_STATE,
                    {"fields": ["channel"]},
                ),
            )
        ),
    )
    client = DaemonEventClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    assert client.receive().sequence == 4
    with pytest.raises(DaemonProtocolError, match="sequence gap"):
        client.receive()

    thread.join(timeout=1.0)
    assert client.connected is False


@pytest.mark.parametrize(
    ("later_event", "expected"),
    [
        (
            event_line(
                4,
                DaemonEventKind.RADIO_STATE,
                {"fields": ["channel"]},
            ),
            "did not advance monotonically",
        ),
        (
            event_line(
                5,
                DaemonEventKind.SNAPSHOT,
                SNAPSHOT,
            ),
            "unexpected later",
        ),
    ],
)
def test_event_client_rejects_invalid_later_checkpoint_order(
    tmp_path: Path,
    later_event: bytes,
    expected: str,
) -> None:
    path = tmp_path / "invalid-order.sock"
    thread = start_scripted_server(
        path,
        b"".join(
            (
                event_line(4, DaemonEventKind.SNAPSHOT, SNAPSHOT),
                later_event,
            )
        ),
    )
    client = DaemonEventClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    assert client.receive().sequence == 4
    with pytest.raises(DaemonProtocolError, match=expected):
        client.receive()

    thread.join(timeout=1.0)
    assert client.connected is False


def test_event_client_rejects_oversized_event(tmp_path: Path) -> None:
    path = tmp_path / "oversized.sock"
    thread = start_scripted_server(
        path,
        event_line(0, DaemonEventKind.SNAPSHOT, SNAPSHOT),
    )
    client = DaemonEventClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT),
        max_event_bytes=64,
    )

    with pytest.raises(DaemonProtocolError, match="maximum accepted size"):
        client.receive()

    thread.join(timeout=1.0)
    assert client.connected is False


def test_event_client_accepts_z_timestamp(tmp_path: Path) -> None:
    path = tmp_path / "timestamp.sock"
    thread = start_scripted_server(
        path,
        event_line(
            0,
            DaemonEventKind.SNAPSHOT,
            SNAPSHOT,
            observed_at="2026-08-05T11:00:00Z",
        ),
    )
    client = DaemonEventClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    event = client.receive()

    client.close()
    thread.join(timeout=1.0)
    assert event.observed_at == datetime(2026, 8, 5, 11, tzinfo=UTC)
