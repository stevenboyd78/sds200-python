from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

import pytest

from sds200 import (
    DAEMON_API_PROTOCOL,
    DAEMON_API_VERSION,
    DaemonApiClient,
    DaemonApiOperation,
    DaemonApiServer,
    DaemonDisconnectedError,
    DaemonProtocolError,
    DaemonReadOnlyApi,
    DaemonRequestError,
    DaemonSocketListener,
    DaemonSocketLocation,
    DaemonSocketSource,
    DaemonUnavailableError,
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
            "audio": {"running": True},
            "router": {"running": True},
            "started_at": "2026-08-05T11:00:00+00:00",
            "stopped_at": None,
            "state_changed_at": "2026-08-05T11:00:00+00:00",
            "transition_sequence": 2,
            "last_failure_at": None,
            "last_error": None,
        }


class FakeRuntime:
    def snapshot(self) -> FakeSnapshot:
        return FakeSnapshot()


def make_server(tmp_path: Path) -> tuple[DaemonApiServer, Path]:
    path = tmp_path / "daemon.sock"
    server = DaemonApiServer(
        DaemonSocketListener(
            DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
        ),
        DaemonReadOnlyApi(FakeRuntime()),
    )
    return server, path


def start_scripted_server(
    path: Path,
    response: bytes | None,
) -> threading.Thread:
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen(1)

    def serve() -> None:
        try:
            client, _ = listener.accept()
            with client:
                request = bytearray()
                while not request.endswith(b"\n"):
                    chunk = client.recv(4096)
                    if not chunk:
                        return
                    request.extend(chunk)
                if response is not None:
                    client.sendall(response)
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
        ("max_response_bytes", True, TypeError),
        ("max_response_bytes", 0, ValueError),
    ],
)
def test_client_rejects_invalid_limits(
    tmp_path: Path,
    keyword: str,
    value: object,
    error_type: type[Exception],
) -> None:
    location = DaemonSocketLocation(
        tmp_path / "daemon.sock",
        DaemonSocketSource.EXPLICIT,
    )

    with pytest.raises(error_type):
        DaemonApiClient(location, **{keyword: value})  # type: ignore[arg-type]


def test_client_negotiates_and_reuses_one_real_socket(tmp_path: Path) -> None:
    server, path = make_server(tmp_path)
    location = DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)

    with server, DaemonApiClient(location) as client:
        hello = client.hello()
        snapshot = client.runtime_snapshot()

        assert client.connected is True
        assert hello["protocol"] == DAEMON_API_PROTOCOL
        assert hello["selected_version"] == DAEMON_API_VERSION
        assert snapshot["state"] == "running"

    assert client.connected is False
    server_snapshot = server.snapshot()
    assert server_snapshot.accepted_clients == 1
    assert server_snapshot.requests == 2
    assert server_snapshot.responses == 2


def test_client_reports_missing_socket(tmp_path: Path) -> None:
    client = DaemonApiClient(
        DaemonSocketLocation(
            tmp_path / "missing.sock",
            DaemonSocketSource.EXPLICIT,
        )
    )

    with pytest.raises(DaemonUnavailableError, match="was not found"):
        client.request(DaemonApiOperation.PING)


def test_client_reports_refused_stale_socket(tmp_path: Path) -> None:
    path = tmp_path / "stale.sock"
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(path))
    stale.close()

    client = DaemonApiClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    with pytest.raises(DaemonUnavailableError, match="not accepting"):
        client.request(DaemonApiOperation.PING)


def test_client_reports_disconnect_before_response(tmp_path: Path) -> None:
    path = tmp_path / "disconnect.sock"
    thread = start_scripted_server(path, None)
    client = DaemonApiClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    with pytest.raises(DaemonDisconnectedError, match="disconnected"):
        client.request(DaemonApiOperation.PING)

    thread.join(timeout=1.0)
    assert thread.is_alive() is False
    assert client.connected is False


def test_client_rejects_malformed_response(tmp_path: Path) -> None:
    path = tmp_path / "malformed.sock"
    thread = start_scripted_server(path, b"not-json\n")
    client = DaemonApiClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    with pytest.raises(DaemonProtocolError, match="invalid UTF-8 JSON"):
        client.request(DaemonApiOperation.PING)

    thread.join(timeout=1.0)
    assert client.connected is False


def test_client_rejects_incompatible_protocol(tmp_path: Path) -> None:
    path = tmp_path / "protocol.sock"
    response = (
        json.dumps(
            {
                "protocol": "other.protocol",
                "version": DAEMON_API_VERSION,
                "request_id": "sdsctl-1",
                "ok": True,
                "result": {"pong": True},
            }
        )
        + "\n"
    ).encode("utf-8")
    thread = start_scripted_server(path, response)
    client = DaemonApiClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    with pytest.raises(DaemonProtocolError, match="Incompatible daemon protocol"):
        client.request(DaemonApiOperation.PING)

    thread.join(timeout=1.0)
    assert client.connected is False


def test_client_rejects_uncorrelated_response(tmp_path: Path) -> None:
    path = tmp_path / "correlation.sock"
    response = (
        json.dumps(
            {
                "protocol": DAEMON_API_PROTOCOL,
                "version": DAEMON_API_VERSION,
                "request_id": "different-request",
                "ok": True,
                "result": {"pong": True},
            }
        )
        + "\n"
    ).encode("utf-8")
    thread = start_scripted_server(path, response)
    client = DaemonApiClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    with pytest.raises(DaemonProtocolError, match="did not match"):
        client.request(DaemonApiOperation.PING)

    thread.join(timeout=1.0)
    assert client.connected is False


def test_client_raises_structured_daemon_request_error(tmp_path: Path) -> None:
    server, path = make_server(tmp_path)
    client = DaemonApiClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    observed: DaemonRequestError | None = None
    with server, client:
        try:
            client.request("scanner.delete", request_id="delete-1")
        except DaemonRequestError as error:
            observed = error

        assert observed is not None
        assert observed.code == "unknown_operation"
        assert observed.request_id == "delete-1"
        assert "scanner.delete" in observed.message
        assert client.connected is True
        assert client.request(DaemonApiOperation.PING) == {"pong": True}

    server_snapshot = server.snapshot()
    assert server_snapshot.accepted_clients == 1
    assert server_snapshot.requests == 2
    assert server_snapshot.responses == 2


def test_client_accepts_legacy_read_only_version_one_hello(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-hello.sock"
    legacy_hello = {
        "protocol": DAEMON_API_PROTOCOL,
        "supported_versions": [DAEMON_API_VERSION],
        "operations": [
            DaemonApiOperation.HELLO.value,
            DaemonApiOperation.CAPABILITIES.value,
            DaemonApiOperation.PING.value,
            DaemonApiOperation.RUNTIME_SNAPSHOT.value,
            DaemonApiOperation.SCANNER_STATE.value,
            DaemonApiOperation.AUDIO_HEALTH.value,
        ],
        "read_only": True,
        "selected_version": DAEMON_API_VERSION,
    }
    response = (
        json.dumps(
            {
                "protocol": DAEMON_API_PROTOCOL,
                "version": DAEMON_API_VERSION,
                "request_id": "sdsctl-1",
                "ok": True,
                "result": legacy_hello,
            }
        )
        + "\n"
    ).encode("utf-8")
    thread = start_scripted_server(path, response)
    client = DaemonApiClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    assert client.hello() == legacy_hello

    client.close()
    thread.join(timeout=1.0)
    assert client.connected is False


def test_client_rejects_malformed_hello_capabilities(tmp_path: Path) -> None:
    path = tmp_path / "hello.sock"
    response = (
        json.dumps(
            {
                "protocol": DAEMON_API_PROTOCOL,
                "version": DAEMON_API_VERSION,
                "request_id": "sdsctl-1",
                "ok": True,
                "result": {
                    "protocol": DAEMON_API_PROTOCOL,
                    "supported_versions": [DAEMON_API_VERSION],
                    "operations": [DaemonApiOperation.HELLO.value],
                    "read_only": False,
                    "read_only_operations": [DaemonApiOperation.HELLO.value],
                    "max_control_timeout": 2.0,
                    "selected_version": DAEMON_API_VERSION,
                },
            }
        )
        + "\n"
    ).encode("utf-8")
    thread = start_scripted_server(path, response)
    client = DaemonApiClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    with pytest.raises(DaemonProtocolError, match="control_operations"):
        client.hello()

    thread.join(timeout=1.0)
    assert client.connected is False


def test_client_rejects_malformed_runtime_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.sock"
    response = (
        json.dumps(
            {
                "protocol": DAEMON_API_PROTOCOL,
                "version": DAEMON_API_VERSION,
                "request_id": "sdsctl-1",
                "ok": True,
                "result": {"state": "running"},
            }
        )
        + "\n"
    ).encode("utf-8")
    thread = start_scripted_server(path, response)
    client = DaemonApiClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    with pytest.raises(DaemonProtocolError, match="omitted required fields"):
        client.runtime_snapshot()

    thread.join(timeout=1.0)
    assert client.connected is False


def test_client_rejects_multiple_responses_for_one_request(
    tmp_path: Path,
) -> None:
    path = tmp_path / "multiple.sock"
    response = (
        json.dumps(
            {
                "protocol": DAEMON_API_PROTOCOL,
                "version": DAEMON_API_VERSION,
                "request_id": "sdsctl-1",
                "ok": True,
                "result": {"pong": True},
            }
        )
        + "\n"
    ).encode("utf-8")
    thread = start_scripted_server(path, response + response)
    client = DaemonApiClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    with pytest.raises(DaemonProtocolError, match="more than one response"):
        client.request(DaemonApiOperation.PING)

    thread.join(timeout=1.0)
    assert client.connected is False


def test_client_bounds_response_size(tmp_path: Path) -> None:
    path = tmp_path / "oversized.sock"
    thread = start_scripted_server(path, b"x" * 33 + b"\n")
    client = DaemonApiClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT),
        max_response_bytes=32,
    )

    with pytest.raises(DaemonProtocolError, match="configured client limit"):
        client.request(DaemonApiOperation.PING)

    thread.join(timeout=1.0)
    assert client.connected is False
