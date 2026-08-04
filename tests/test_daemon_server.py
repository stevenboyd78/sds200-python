from __future__ import annotations

import json
import socket
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from sds200 import (
    DAEMON_API_PROTOCOL,
    DAEMON_API_VERSION,
    DaemonApiServer,
    DaemonIpcError,
    DaemonReadOnlyApi,
    DaemonSocketListener,
    DaemonSocketLocation,
    DaemonSocketSource,
)


class FakeSnapshot:
    def as_dict(self) -> dict[str, object]:
        return {
            "state": "running",
            "scanner_endpoint": "udp://192.0.2.25:50536",
            "scanner_connected": True,
            "psi_interval_ms": 500,
            "psi_active": True,
            "radio_state": {},
            "audio": {},
            "router": {},
        }


class FakeRuntime:
    def snapshot(self) -> FakeSnapshot:
        return FakeSnapshot()


class OversizedResponseApi:
    def handle_json_line(self, data: bytes | str) -> bytes:
        del data
        return b"x" * 300


def request(operation: str, request_id: str) -> bytes:
    return (
        json.dumps(
            {
                "protocol": DAEMON_API_PROTOCOL,
                "version": DAEMON_API_VERSION,
                "request_id": request_id,
                "operation": operation,
            }
        )
        + "\n"
    ).encode("utf-8")


def make_server(
    tmp_path: Path,
    *,
    api: object | None = None,
    **kwargs: object,
) -> tuple[DaemonApiServer, Path]:
    path = tmp_path / "daemon.sock"
    listener = DaemonSocketListener(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )
    selected_api = api or DaemonReadOnlyApi(FakeRuntime())
    server = DaemonApiServer(
        listener,
        selected_api,  # type: ignore[arg-type]
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
    predicate: Callable[[], bool],
    *,
    timeout: float = 1.0,
) -> None:
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
        ("max_request_bytes", True, TypeError),
        ("max_request_bytes", 0, ValueError),
        ("max_response_bytes", 255, ValueError),
        ("client_timeout", True, TypeError),
        ("client_timeout", 0, ValueError),
        ("accept_poll_interval", 0, ValueError),
        ("shutdown_timeout", float("inf"), ValueError),
    ],
)
def test_server_rejects_invalid_limits(
    tmp_path: Path,
    keyword: str,
    value: object,
    error_type: type[Exception],
) -> None:
    with pytest.raises(error_type):
        make_server(tmp_path, **{keyword: value})


def test_server_handles_ping_over_real_unix_socket(tmp_path: Path) -> None:
    server, path = make_server(tmp_path)

    with server:
        client = connect(path)
        try:
            client.sendall(request("ping", "ping-1"))
            payload = json.loads(read_line(client))

            assert payload["ok"] is True
            assert payload["request_id"] == "ping-1"
            assert payload["result"] == {"pong": True}
        finally:
            client.close()

    snapshot = server.snapshot()
    assert snapshot.active is False
    assert snapshot.accepted_clients == 1
    assert snapshot.requests == 1
    assert snapshot.responses == 1
    assert path.exists() is False


def test_connection_can_process_multiple_ordered_requests(
    tmp_path: Path,
) -> None:
    server, path = make_server(tmp_path)
    server.start()
    client = connect(path)

    try:
        client.sendall(
            request("ping", "ping-1")
            + request("daemon.capabilities", "capabilities-1")
        )
        first = json.loads(read_line(client))
        second = json.loads(read_line(client))

        assert first["request_id"] == "ping-1"
        assert second["request_id"] == "capabilities-1"
        assert second["result"]["read_only"] is True
    finally:
        client.close()
        server.stop()

    assert server.snapshot().requests == 2
    assert server.snapshot().responses == 2


def test_malformed_request_does_not_close_connection(tmp_path: Path) -> None:
    server, path = make_server(tmp_path)
    server.start()
    client = connect(path)

    try:
        client.sendall(b"not-json\n")
        malformed = json.loads(read_line(client))
        client.sendall(request("ping", "ping-2"))
        valid = json.loads(read_line(client))

        assert malformed["ok"] is False
        assert malformed["error"]["code"] == "invalid_request"
        assert valid["ok"] is True
        assert valid["request_id"] == "ping-2"
    finally:
        client.close()
        server.stop()


def test_oversized_request_returns_error_and_closes_client(
    tmp_path: Path,
) -> None:
    server, path = make_server(
        tmp_path,
        max_request_bytes=32,
    )
    server.start()
    client = connect(path)

    try:
        client.sendall(b"x" * 33)
        payload = json.loads(read_line(client))

        assert payload["ok"] is False
        assert payload["error"]["code"] == "request_too_large"
        assert client.recv(1) == b""
    finally:
        client.close()
        server.stop()

    assert server.snapshot().oversized_requests == 1


def test_maximum_client_count_rejects_excess_connections(
    tmp_path: Path,
) -> None:
    server, path = make_server(
        tmp_path,
        max_clients=1,
        client_timeout=1.0,
    )
    server.start()
    first = connect(path)

    try:
        wait_until(lambda: server.connected_clients == 1)
        second = connect(path)
        try:
            wait_until(lambda: server.snapshot().rejected_clients == 1)
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


def test_idle_client_is_closed_after_timeout(tmp_path: Path) -> None:
    server, path = make_server(
        tmp_path,
        client_timeout=0.05,
    )
    server.start()
    client = connect(path)

    try:
        wait_until(lambda: server.connected_clients == 1)
        wait_until(lambda: server.connected_clients == 0)
        assert client.recv(1) == b""
    finally:
        client.close()
        server.stop()


def test_stop_closes_clients_and_removes_socket(tmp_path: Path) -> None:
    server, path = make_server(tmp_path, client_timeout=5.0)
    server.start()
    client = connect(path)
    wait_until(lambda: server.connected_clients == 1)

    server.stop()

    try:
        assert client.recv(1) == b""
    finally:
        client.close()

    assert server.active is False
    assert path.exists() is False


def test_start_and_stop_are_idempotent_but_restart_is_rejected(
    tmp_path: Path,
) -> None:
    server, path = make_server(tmp_path)

    server.start()
    server.start()
    assert server.active is True

    server.stop()
    server.stop()
    assert path.exists() is False

    with pytest.raises(RuntimeError, match="cannot be restarted"):
        server.start()


def test_listener_start_failure_is_propagated_and_server_stays_inactive(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing" / "daemon.sock"
    listener = DaemonSocketListener(
        DaemonSocketLocation(
            missing,
            DaemonSocketSource.EXPLICIT,
        )
    )
    server = DaemonApiServer(listener, DaemonReadOnlyApi(FakeRuntime()))

    with pytest.raises(DaemonIpcError, match="does not exist"):
        server.start()

    assert server.active is False
    assert missing.exists() is False


def test_oversized_api_response_is_replaced_with_structured_error(
    tmp_path: Path,
) -> None:
    server, path = make_server(
        tmp_path,
        api=OversizedResponseApi(),
        max_response_bytes=256,
    )
    server.start()
    client = connect(path)

    try:
        client.sendall(request("ping", "ping-1"))
        payload = json.loads(read_line(client))

        assert payload["ok"] is False
        assert payload["error"]["code"] == "internal_error"
        assert "configured size limit" in payload["error"]["message"]
    finally:
        client.close()
        server.stop()

    assert server.snapshot().oversized_responses == 1


def test_server_snapshot_is_json_compatible(tmp_path: Path) -> None:
    server, _ = make_server(tmp_path)

    payload = server.snapshot().as_dict()

    assert json.loads(json.dumps(payload)) == payload
    assert payload["active"] is False
    assert payload["connected_clients"] == 0
    assert payload["last_error"] is None
