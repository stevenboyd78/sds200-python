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
    DaemonControlBusyError,
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


class FakeRuntime:
    def snapshot(self) -> FakeSnapshot:
        return FakeSnapshot()


class FakeControlResult:
    def __init__(self, sequence: int, operation: str) -> None:
        self.sequence = sequence
        self.operation = operation

    def as_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "operation": self.operation,
            "started_at": "2026-08-05T11:00:00+00:00",
            "completed_at": "2026-08-05T11:00:01+00:00",
            "snapshot": FakeSnapshot().as_dict(),
        }


class FakeControlRuntime(FakeRuntime):
    def __init__(self) -> None:
        self.calls: list[
            tuple[str, tuple[object, ...], dict[str, object]]
        ] = []
        self.error: Exception | None = None
        self.sequence = 0

    def hold(
        self,
        target: str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        timeout: float = 2.0,
    ) -> FakeControlResult:
        return self._control(
            "scanner.hold",
            target,
            first,
            second,
            timeout=timeout,
        )

    def hold_state(
        self,
        scope: str,
        held: bool,
        *,
        timeout: float = 4.0,
    ) -> FakeControlResult:
        return self._control(
            "scanner.hold_state",
            scope,
            held,
            timeout=timeout,
        )

    def next(
        self,
        target: str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        count: int = 1,
        timeout: float = 2.0,
    ) -> FakeControlResult:
        return self._control(
            "scanner.next",
            target,
            first,
            second,
            count=count,
            timeout=timeout,
        )

    def previous(
        self,
        target: str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        count: int = 1,
        timeout: float = 2.0,
    ) -> FakeControlResult:
        return self._control(
            "scanner.previous",
            target,
            first,
            second,
            count=count,
            timeout=timeout,
        )

    def reconnect(
        self,
        *,
        timeout: float = 2.0,
    ) -> FakeControlResult:
        return self._control(
            "scanner.reconnect",
            timeout=timeout,
        )

    def _control(
        self,
        operation: str,
        *arguments: object,
        **keywords: object,
    ) -> FakeControlResult:
        self.calls.append((operation, arguments, keywords))
        if self.error is not None:
            raise self.error
        self.sequence += 1
        return FakeControlResult(self.sequence, operation)


def make_server(tmp_path: Path) -> tuple[DaemonApiServer, Path]:
    path = tmp_path / "daemon.sock"
    server = DaemonApiServer(
        DaemonSocketListener(
            DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
        ),
        DaemonReadOnlyApi(FakeRuntime()),
    )
    return server, path


def make_control_server(
    tmp_path: Path,
) -> tuple[DaemonApiServer, Path, FakeControlRuntime]:
    path = tmp_path / "daemon-control.sock"
    runtime = FakeControlRuntime()
    server = DaemonApiServer(
        DaemonSocketListener(
            DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
        ),
        DaemonReadOnlyApi(runtime),
    )
    return server, path, runtime


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


def test_client_hello_cache_isolated_from_caller_mutation(
    tmp_path: Path,
) -> None:
    server, path = make_server(tmp_path)
    location = DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)

    with server, DaemonApiClient(location) as client:
        first = client.hello()
        first_operations = first["operations"]
        first_control_operations = first["control_operations"]
        assert isinstance(first_operations, list)
        assert isinstance(first_control_operations, list)

        first_operations.clear()
        first_control_operations.clear()
        first["read_only"] = True

        second = client.hello()
        second_operations = second["operations"]
        second_control_operations = second["control_operations"]
        assert isinstance(second_operations, list)
        assert isinstance(second_control_operations, list)
        assert second["read_only"] is False
        assert DaemonApiOperation.HELLO.value in second_operations
        assert (
            DaemonApiOperation.SCANNER_HOLD.value
            in second_control_operations
        )

    server_snapshot = server.snapshot()
    assert server_snapshot.accepted_clients == 1
    assert server_snapshot.requests == 1
    assert server_snapshot.responses == 1


def test_client_executes_typed_controls_on_one_negotiated_socket(
    tmp_path: Path,
) -> None:
    server, path, runtime = make_control_server(tmp_path)
    location = DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)

    with server, DaemonApiClient(location) as client:
        results = (
            client.hold("sys", 42, timeout=1.5),
            client.hold_state(" Site ", False, timeout=3.5),
            client.next("dept", 7, 42, count=2, timeout=1.5),
            client.previous("tgid", 99, count=3, timeout=1.5),
            client.reconnect(timeout=1.5),
        )

        assert client.connected is True

    assert [result["sequence"] for result in results] == [1, 2, 3, 4, 5]
    assert [result["operation"] for result in results] == [
        "scanner.hold",
        "scanner.hold_state",
        "scanner.next",
        "scanner.previous",
        "scanner.reconnect",
    ]
    assert runtime.calls == [
        (
            "scanner.hold",
            ("SYS", 42, None),
            {"timeout": 1.5},
        ),
        (
            "scanner.hold_state",
            ("site", False),
            {"timeout": 3.5},
        ),
        (
            "scanner.next",
            ("DEPT", 7, 42),
            {"count": 2, "timeout": 1.5},
        ),
        (
            "scanner.previous",
            ("TGID", 99, None),
            {"count": 3, "timeout": 1.5},
        ),
        (
            "scanner.reconnect",
            (),
            {"timeout": 1.5},
        ),
    ]
    server_snapshot = server.snapshot()
    assert server_snapshot.accepted_clients == 1
    assert server_snapshot.requests == 6
    assert server_snapshot.responses == 6


@pytest.mark.parametrize(
    ("method", "arguments", "keywords", "error_type"),
    [
        ("hold", ("INVALID",), {}, ValueError),
        ("hold", ("SYS", "1,2"), {}, ValueError),
        ("hold_state", ("favorites", True), {}, ValueError),
        ("hold_state", ("system", 1), {}, TypeError),
        ("hold_state", ("system", True), {"timeout": 4.1}, ValueError),
        ("next", ("TGID",), {"count": 0}, ValueError),
        ("next", ("TGID",), {"count": True}, TypeError),
        ("reconnect", (), {"timeout": 2.1}, ValueError),
        ("reconnect", (), {"timeout": float("inf")}, ValueError),
    ],
)
def test_client_rejects_invalid_control_parameters_before_request(
    tmp_path: Path,
    method: str,
    arguments: tuple[object, ...],
    keywords: dict[str, object],
    error_type: type[Exception],
) -> None:
    server, path, runtime = make_control_server(tmp_path)
    client = DaemonApiClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    with server, client, pytest.raises(error_type):
        getattr(client, method)(*arguments, **keywords)

    assert runtime.calls == []


def test_client_preserves_structured_control_error_and_connection(
    tmp_path: Path,
) -> None:
    server, path, runtime = make_control_server(tmp_path)
    runtime.error = DaemonControlBusyError("synthetic busy control")
    client = DaemonApiClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    with server, client:
        with pytest.raises(DaemonRequestError) as captured:
            client.hold("SYS", 42)

        assert captured.value.code == "control_busy"
        assert captured.value.request_id == "sdsctl-2"
        assert client.connected is True

        runtime.error = None
        assert client.request(DaemonApiOperation.PING) == {"pong": True}

    server_snapshot = server.snapshot()
    assert server_snapshot.accepted_clients == 1
    assert server_snapshot.requests == 3
    assert server_snapshot.responses == 3


def test_client_rejects_malformed_control_completion(
    tmp_path: Path,
) -> None:
    class MalformedControlResult:
        @staticmethod
        def as_dict() -> dict[str, object]:
            return {
                "sequence": 1,
                "operation": DaemonApiOperation.SCANNER_HOLD.value,
                "started_at": "2026-08-05T11:00:00+00:00",
                "completed_at": "2026-08-05T11:00:01+00:00",
                "snapshot": {"state": "running"},
            }

    class MalformedControlRuntime(FakeControlRuntime):
        def hold(
            self,
            target: str,
            first: str | int | None = None,
            second: str | int | None = None,
            *,
            timeout: float = 2.0,
        ) -> MalformedControlResult:
            del target, first, second, timeout
            return MalformedControlResult()

    path = tmp_path / "malformed-control.sock"
    server = DaemonApiServer(
        DaemonSocketListener(
            DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
        ),
        DaemonReadOnlyApi(MalformedControlRuntime()),
    )
    client = DaemonApiClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    with server, pytest.raises(
        DaemonProtocolError,
        match="runtime snapshot omitted required fields",
    ):
        client.hold("SYS", 42)

    assert client.connected is False
    server_snapshot = server.snapshot()
    assert server_snapshot.requests == 2
    assert server_snapshot.responses == 2


def test_client_rejects_controls_from_legacy_read_only_daemon(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-read-only.sock"
    legacy_hello = {
        "protocol": DAEMON_API_PROTOCOL,
        "supported_versions": [DAEMON_API_VERSION],
        "operations": [
            DaemonApiOperation.HELLO.value,
            DaemonApiOperation.RUNTIME_SNAPSHOT.value,
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
    with pytest.raises(DaemonProtocolError, match="read-only"):
        client.hold("SYS", 42)

    client.close()
    thread.join(timeout=1.0)


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


def test_client_requires_hold_state_timeout_when_operation_is_advertised(
    tmp_path: Path,
) -> None:
    path = tmp_path / "hold-state-hello.sock"
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
                    "operations": [
                        DaemonApiOperation.HELLO.value,
                        DaemonApiOperation.SCANNER_HOLD_STATE.value,
                    ],
                    "read_only": False,
                    "read_only_operations": [
                        DaemonApiOperation.HELLO.value,
                    ],
                    "control_operations": [
                        DaemonApiOperation.SCANNER_HOLD_STATE.value,
                    ],
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

    with pytest.raises(DaemonProtocolError, match="hold-state timeout"):
        client.hello()

    thread.join(timeout=1.0)
    assert client.connected is False


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


@pytest.mark.parametrize(
    "identity",
    [
        {},
        {"scanner_model": None, "scanner_firmware": None},
    ],
)
def test_client_accepts_optional_runtime_identity(
    tmp_path: Path,
    identity: dict[str, object],
) -> None:
    path = tmp_path / "optional-identity.sock"
    snapshot = FakeSnapshot().as_dict()
    snapshot.pop("scanner_model")
    snapshot.pop("scanner_firmware")
    snapshot.update(identity)
    response = (
        json.dumps(
            {
                "protocol": DAEMON_API_PROTOCOL,
                "version": DAEMON_API_VERSION,
                "request_id": "sdsctl-1",
                "ok": True,
                "result": snapshot,
            }
        )
        + "\n"
    ).encode("utf-8")
    thread = start_scripted_server(path, response)
    client = DaemonApiClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    assert client.runtime_snapshot() == snapshot

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
def test_client_rejects_malformed_runtime_identity(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    path = tmp_path / f"malformed-{field}.sock"
    snapshot = FakeSnapshot().as_dict()
    snapshot[field] = value
    response = (
        json.dumps(
            {
                "protocol": DAEMON_API_PROTOCOL,
                "version": DAEMON_API_VERSION,
                "request_id": "sdsctl-1",
                "ok": True,
                "result": snapshot,
            }
        )
        + "\n"
    ).encode("utf-8")
    thread = start_scripted_server(path, response)
    client = DaemonApiClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    with pytest.raises(DaemonProtocolError, match=field):
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
