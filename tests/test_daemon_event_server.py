from __future__ import annotations

import json
import logging
import socket
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sds200 import (
    DaemonEvent,
    DaemonEventKind,
    DaemonEventPublisher,
    DaemonEventServer,
    DaemonEventSubscription,
    DaemonSocketListener,
    DaemonSocketLocation,
    DaemonSocketSource,
)


class FakeEventStream:
    def __init__(self) -> None:
        self.snapshot_payload: dict[str, object] = {
            "state": "idle",
            "scanner_endpoint": "udp://192.0.2.25:50536",
        }
        self.close_calls = 0
        self._publisher = DaemonEventPublisher(
            lambda: self.snapshot_payload,
            queue_capacity=4,
            max_subscribers=8,
        )

    def subscribe(self) -> DaemonEventSubscription:
        return self._publisher.subscribe()

    def publish(
        self,
        kind: DaemonEventKind,
        payload: Mapping[str, object],
        *,
        observed_at: datetime | None = None,
    ) -> DaemonEvent:
        return self._publisher.publish(
            kind,
            payload,
            observed_at=observed_at,
        )

    def close(self) -> None:
        self.close_calls += 1
        self._publisher.close()


def make_server(
    tmp_path: Path,
    stream: FakeEventStream,
    **kwargs: object,
) -> tuple[DaemonEventServer, Path]:
    path = tmp_path / "events.sock"
    listener = DaemonSocketListener(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )
    server = DaemonEventServer(
        listener,
        stream,
        **kwargs,  # type: ignore[arg-type]
    )
    return server, path


def connect(path: Path) -> socket.socket:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(1.0)
    client.connect(str(path))
    return client


def read_line(client: socket.socket) -> bytes:
    payload = bytearray()
    while not payload.endswith(b"\n"):
        chunk = client.recv(1)
        if not chunk:
            break
        payload.extend(chunk)
    return bytes(payload)


def wait_until(
    predicate: object,
    *,
    timeout: float = 1.0,
) -> None:
    assert callable(predicate)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Condition did not become true before timeout")


@pytest.mark.parametrize(
    ("keyword", "value", "error_type"),
    [
        ("max_clients", True, TypeError),
        ("max_clients", 0, ValueError),
        ("send_timeout", True, TypeError),
        ("send_timeout", 0, ValueError),
        ("accept_poll_interval", 0, ValueError),
        ("shutdown_timeout", float("inf"), ValueError),
    ],
)
def test_event_server_rejects_invalid_limits(
    tmp_path: Path,
    keyword: str,
    value: object,
    error_type: type[Exception],
) -> None:
    stream = FakeEventStream()

    with pytest.raises(error_type):
        make_server(
            tmp_path,
            stream,
            **{keyword: value},
        )


def test_event_server_sends_snapshot_then_ordered_events(
    tmp_path: Path,
) -> None:
    stream = FakeEventStream()
    server, path = make_server(tmp_path, stream)
    server.start()
    client = connect(path)

    try:
        snapshot = json.loads(read_line(client))

        observed_at = datetime(2026, 8, 4, 23, 45, tzinfo=UTC)
        stream.publish(
            DaemonEventKind.SCANNER_CONNECTION,
            {
                "endpoint": "udp://192.0.2.25:50536",
                "connected": True,
            },
            observed_at=observed_at,
        )
        event = json.loads(read_line(client))

        assert snapshot["sequence"] == 0
        assert snapshot["kind"] == DaemonEventKind.SNAPSHOT
        assert snapshot["payload"] == stream.snapshot_payload

        assert event["sequence"] == 1
        assert event["kind"] == DaemonEventKind.SCANNER_CONNECTION
        assert event["observed_at"] == observed_at.isoformat()
        assert event["payload"] == {
            "endpoint": "udp://192.0.2.25:50536",
            "connected": True,
        }
    finally:
        client.close()
        server.stop()

    server_snapshot = server.snapshot()
    assert server_snapshot.active is False
    assert server_snapshot.accepted_clients == 1
    assert server_snapshot.events_sent == 2
    assert server_snapshot.last_error is None
    assert stream.close_calls == 1
    assert path.exists() is False


def test_event_server_rejects_excess_clients(
    tmp_path: Path,
) -> None:
    stream = FakeEventStream()
    server, path = make_server(
        tmp_path,
        stream,
        max_clients=1,
    )
    server.start()
    first = connect(path)

    try:
        json.loads(read_line(first))
        wait_until(lambda: server.connected_clients == 1)

        second = connect(path)
        try:
            wait_until(
                lambda: server.snapshot().rejected_clients == 1
            )
            assert second.recv(1) == b""
        finally:
            second.close()
    finally:
        first.close()
        server.stop()

    snapshot = server.snapshot()
    assert snapshot.max_clients == 1
    assert snapshot.accepted_clients == 1
    assert snapshot.rejected_clients == 1


def test_event_server_stop_closes_clients_and_owned_stream(
    tmp_path: Path,
) -> None:
    stream = FakeEventStream()
    server, path = make_server(
        tmp_path,
        stream,
        send_timeout=5.0,
    )
    server.start()
    server.start()
    client = connect(path)
    json.loads(read_line(client))
    wait_until(lambda: server.connected_clients == 1)

    server.stop()
    server.stop()

    try:
        assert client.recv(1) == b""
    finally:
        client.close()

    assert server.active is False
    assert server.connected_clients == 0
    assert stream.close_calls == 1
    assert path.exists() is False

    with pytest.raises(RuntimeError, match="cannot be restarted"):
        server.start()



def test_event_server_client_disconnect_is_not_an_operational_error(
    tmp_path: Path,
) -> None:
    stream = FakeEventStream()
    server, path = make_server(tmp_path, stream)
    server.start()
    client = connect(path)

    try:
        json.loads(read_line(client))
        wait_until(lambda: server.connected_clients == 1)

        client.shutdown(socket.SHUT_RDWR)
        client.close()
        stream.publish(
            DaemonEventKind.SCANNER_CONNECTION,
            {"connected": False},
        )

        wait_until(lambda: server.connected_clients == 0)
        assert server.snapshot().last_error is None
    finally:
        client.close()
        server.stop()


def test_event_server_startup_preserves_error_and_attempts_all_cleanup(
    caplog: pytest.LogCaptureFixture,
) -> None:
    order: list[str] = []
    startup_error = RuntimeError("secret startup failure")

    class FailingListener:
        def start(self) -> socket.socket:
            order.append("listener.start")
            raise startup_error

        def stop(self) -> None:
            order.append("listener.stop")
            raise OSError("secret listener cleanup failure")

    class FailingStream:
        def subscribe(self) -> DaemonEventSubscription:
            raise AssertionError("unreachable")

        def close(self) -> None:
            order.append("stream.close")
            raise ValueError("secret stream cleanup failure")

    server = DaemonEventServer(
        FailingListener(),  # type: ignore[arg-type]
        FailingStream(),
    )

    with (
        caplog.at_level(logging.ERROR, logger="sds200.daemon_event_server"),
        pytest.raises(RuntimeError) as raised,
    ):
        server.start()

    assert raised.value is startup_error
    assert order == [
        "listener.start",
        "listener.stop",
        "stream.close",
    ]
    assert "startup_error=RuntimeError" in caplog.text
    assert "listener:OSError" in caplog.text
    assert "stream:ValueError" in caplog.text
    assert "secret" not in caplog.text

def test_event_server_stop_before_start_is_terminal(
    tmp_path: Path,
) -> None:
    stream = FakeEventStream()
    server, path = make_server(tmp_path, stream)

    server.stop()
    server.stop()

    assert server.active is False
    assert stream.close_calls == 1
    assert path.exists() is False

    with pytest.raises(RuntimeError, match="cannot be restarted"):
        server.start()


def test_event_server_snapshot_is_json_compatible(
    tmp_path: Path,
) -> None:
    stream = FakeEventStream()
    server, _ = make_server(tmp_path, stream)

    payload = server.snapshot().as_dict()

    assert json.loads(json.dumps(payload)) == payload
    assert payload["active"] is False
    assert payload["connected_clients"] == 0
    assert payload["events_sent"] == 0
    assert payload["last_error"] is None
