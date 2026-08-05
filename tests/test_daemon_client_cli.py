from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from sds200 import (
    DAEMON_API_PROTOCOL,
    DAEMON_API_VERSION,
    DaemonApiOperation,
    DaemonSocketLocation,
    DaemonUnavailableError,
    cli,
)

HELLO = {
    "protocol": DAEMON_API_PROTOCOL,
    "supported_versions": [DAEMON_API_VERSION],
    "operations": [
        DaemonApiOperation.HELLO.value,
        DaemonApiOperation.RUNTIME_SNAPSHOT.value,
    ],
    "read_only": False,
    "read_only_operations": [
        DaemonApiOperation.HELLO.value,
        DaemonApiOperation.RUNTIME_SNAPSHOT.value,
    ],
    "control_operations": [],
    "max_control_timeout": 2.0,
    "selected_version": DAEMON_API_VERSION,
}

SNAPSHOT = {
    "state": "running",
    "scanner_endpoint": "udp://192.0.2.25:50536",
    "scanner_connected": True,
    "psi_interval_ms": 500,
    "psi_active": True,
    "radio_state": {
        "system": "Metro",
        "department": "Dispatch",
        "channel": "Primary",
    },
    "audio": {"running": True},
    "router": {"running": True},
    "started_at": "2026-08-05T11:00:00+00:00",
    "stopped_at": None,
    "state_changed_at": "2026-08-05T11:00:00+00:00",
    "transition_sequence": 2,
    "last_failure_at": None,
    "last_error": None,
}


class FakeDaemonApiClient:
    instances: list[FakeDaemonApiClient] = []

    def __init__(
        self,
        location: DaemonSocketLocation,
        *,
        timeout: float,
        max_response_bytes: int,
    ) -> None:
        self.location = location
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.closed = False
        self.requests: list[tuple[object, Mapping[str, object] | None]] = []
        self.instances.append(self)

    def __enter__(self) -> FakeDaemonApiClient:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception_type, exception, traceback
        self.closed = True

    def hello(self) -> dict[str, object]:
        return dict(HELLO)

    def request(
        self,
        operation: object,
        params: Mapping[str, object] | None = None,
        *,
        request_id: str | None = None,
    ) -> dict[str, object]:
        del request_id
        self.requests.append((operation, params))
        return dict(SNAPSHOT)

    def runtime_snapshot(self) -> dict[str, object]:
        return self.request(DaemonApiOperation.RUNTIME_SNAPSHOT)


def test_daemon_client_parser_accepts_status_options() -> None:
    args = cli.build_parser().parse_args(
        [
            "daemon-client",
            "--socket-path",
            "/tmp/sdsctl-daemon.sock",
            "--timeout",
            "4",
            "--max-response-bytes",
            "8192",
            "status",
            "--json",
        ]
    )

    assert args.action == "daemon-client"
    assert args.daemon_client_action == "status"
    assert args.socket_path == Path("/tmp/sdsctl-daemon.sock")
    assert args.timeout == 4.0
    assert args.max_response_bytes == 8192
    assert args.json is True


def test_daemon_client_status_prints_human_summary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    FakeDaemonApiClient.instances.clear()
    monkeypatch.setattr(cli, "DaemonApiClient", FakeDaemonApiClient)

    result = cli.main(
        [
            "daemon-client",
            "--socket-path",
            "/tmp/sdsctl-daemon.sock",
            "status",
        ],
        environ={},
    )

    assert result == 0
    assert capsys.readouterr().out.splitlines() == [
        "Daemon socket:      /tmp/sdsctl-daemon.sock",
        "Socket source:      explicit",
        "Protocol:           sdsctl.daemon v1",
        "Runtime:            running",
        "Scanner connected:  yes",
        "Scanner endpoint:   udp://192.0.2.25:50536",
        "PSI active:         yes",
        "PSI interval:       500 ms",
        "Audio running:      yes",
        "Router running:     yes",
        "Last error:         -",
    ]
    assert len(FakeDaemonApiClient.instances) == 1
    client = FakeDaemonApiClient.instances[0]
    assert client.requests == [
        (DaemonApiOperation.RUNTIME_SNAPSHOT, None)
    ]
    assert client.closed is True


def test_daemon_client_status_prints_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    FakeDaemonApiClient.instances.clear()
    monkeypatch.setattr(cli, "DaemonApiClient", FakeDaemonApiClient)

    assert (
        cli.main(
            [
                "daemon-client",
                "--socket-path",
                "/tmp/sdsctl-daemon.sock",
                "status",
                "--json",
            ],
            environ={},
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["socket"] == {
        "path": "/tmp/sdsctl-daemon.sock",
        "source": "explicit",
    }
    assert payload["hello"] == HELLO
    assert payload["runtime"] == SNAPSHOT


def test_daemon_client_snapshot_prints_authoritative_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    FakeDaemonApiClient.instances.clear()
    monkeypatch.setattr(cli, "DaemonApiClient", FakeDaemonApiClient)

    assert (
        cli.main(
            [
                "daemon-client",
                "--socket-path",
                "/tmp/sdsctl-daemon.sock",
                "snapshot",
            ],
            environ={},
        )
        == 0
    )

    assert json.loads(capsys.readouterr().out) == SNAPSHOT


@pytest.mark.parametrize(
    "arguments",
    [
        ["--host", "192.0.2.25", "daemon-client", "status"],
        ["--port", "/dev/ttyACM0", "daemon-client", "status"],
        ["--profile", "scanner", "daemon-client", "status"],
        ["--capture", "capture.jsonl", "daemon-client", "status"],
        ["--trace", "trace.log", "daemon-client", "status"],
    ],
)
def test_daemon_client_rejects_scanner_connection_options(
    arguments: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "DaemonApiClient", FakeDaemonApiClient)

    assert cli.main(arguments, environ={}) == 2
    assert "not used with daemon-client" in capsys.readouterr().err


def test_daemon_client_uses_runtime_socket_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    FakeDaemonApiClient.instances.clear()
    monkeypatch.setattr(cli, "DaemonApiClient", FakeDaemonApiClient)

    assert (
        cli.main(
            ["daemon-client", "status"],
            environ={"XDG_RUNTIME_DIR": str(tmp_path)},
        )
        == 0
    )

    capsys.readouterr()
    client = FakeDaemonApiClient.instances[0]
    assert client.location.path == tmp_path / "sdsctl" / "daemon.sock"
    assert client.location.source.value == "xdg-runtime"


def test_daemon_client_reports_missing_snapshot_capability(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class IncompatibleClient(FakeDaemonApiClient):
        def hello(self) -> dict[str, object]:
            payload = dict(HELLO)
            payload["operations"] = [DaemonApiOperation.HELLO.value]
            return payload

    monkeypatch.setattr(cli, "DaemonApiClient", IncompatibleClient)

    assert (
        cli.main(
            [
                "daemon-client",
                "--socket-path",
                "/tmp/sdsctl-daemon.sock",
                "status",
            ],
            environ={},
        )
        == 2
    )
    assert (
        "does not advertise runtime.snapshot support"
        in capsys.readouterr().err
    )


def test_daemon_client_reports_unavailable_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class UnavailableClient(FakeDaemonApiClient):
        def __enter__(self) -> UnavailableClient:
            raise DaemonUnavailableError("Daemon socket was not found.")

    monkeypatch.setattr(cli, "DaemonApiClient", UnavailableClient)

    assert (
        cli.main(
            [
                "daemon-client",
                "--socket-path",
                "/tmp/missing.sock",
                "status",
            ],
            environ={},
        )
        == 2
    )
    assert "Daemon socket was not found" in capsys.readouterr().err
