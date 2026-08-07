from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from sds200 import DaemonSocketLocation, cli, web_dashboard
from sds200.web_server import WEB_DASHBOARD_DEFAULT_HOST, WEB_DASHBOARD_DEFAULT_PORT


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

    def hello(self) -> Mapping[str, object]:
        return {}

    def runtime_snapshot(self) -> Mapping[str, object]:
        return {}


class FakeDaemonEventClient:
    instances: list[FakeDaemonEventClient] = []

    def __init__(
        self,
        location: DaemonSocketLocation,
        *,
        timeout: float,
        max_event_bytes: int,
    ) -> None:
        self.location = location
        self.timeout = timeout
        self.max_event_bytes = max_event_bytes
        self.instances.append(self)


def test_web_parser_uses_loopback_defaults() -> None:
    args = cli.build_parser().parse_args(["web"])

    assert args.action == "web"
    assert args.daemon_socket_path is None
    assert args.daemon_event_socket_path is None
    assert args.daemon_timeout == 5.0
    assert args.daemon_max_response_bytes is None
    assert args.daemon_max_event_bytes is None
    assert args.listen_address == WEB_DASHBOARD_DEFAULT_HOST
    assert args.listen_port == WEB_DASHBOARD_DEFAULT_PORT
    assert args.access_log is True


def test_web_parser_accepts_explicit_local_options() -> None:
    args = cli.build_parser().parse_args(
        [
            "web",
            "--daemon-socket-path",
            "/tmp/sdsctl/daemon.sock",
            "--daemon-event-socket-path",
            "/tmp/sdsctl/events.sock",
            "--daemon-timeout",
            "2.5",
            "--daemon-max-response-bytes",
            "8192",
            "--daemon-max-event-bytes",
            "4096",
            "--listen-address",
            "::1",
            "--listen-port",
            "8123",
            "--no-access-log",
        ]
    )

    assert args.daemon_socket_path == Path("/tmp/sdsctl/daemon.sock")
    assert args.daemon_event_socket_path == Path("/tmp/sdsctl/events.sock")
    assert args.daemon_timeout == 2.5
    assert args.daemon_max_response_bytes == 8192
    assert args.daemon_max_event_bytes == 4096
    assert args.listen_address == "::1"
    assert args.listen_port == 8123
    assert args.access_log is False


@pytest.mark.parametrize(
    "address",
    ["0.0.0.0", "::", "192.168.0.25", "scanner.local"],
)
def test_web_parser_rejects_non_loopback_address(address: str) -> None:
    with pytest.raises(SystemExit) as error:
        cli.build_parser().parse_args(["web", "--listen-address", address])

    assert error.value.code == 2


def test_web_cli_builds_daemon_clients_and_runs_server(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    FakeDaemonApiClient.instances.clear()
    FakeDaemonEventClient.instances.clear()
    captured_api_factories: list[Callable[[], object]] = []
    captured_event_factories: list[Callable[[], object]] = []
    app = object()
    server_calls: list[tuple[object, str, int, bool]] = []

    def fake_create_app(
        api_client_factory: Callable[[], object],
        event_client_factory: Callable[[], object],
    ) -> object:
        captured_api_factories.append(api_client_factory)
        captured_event_factories.append(event_client_factory)
        return app

    def fake_run_server(
        selected_app: object,
        *,
        host: str,
        port: int,
        access_log: bool,
    ) -> int:
        server_calls.append((selected_app, host, port, access_log))
        return 0

    monkeypatch.setattr(cli, "DaemonApiClient", FakeDaemonApiClient)
    monkeypatch.setattr(cli, "DaemonEventClient", FakeDaemonEventClient)
    monkeypatch.setattr(
        web_dashboard,
        "create_web_dashboard_app",
        fake_create_app,
    )
    monkeypatch.setattr(cli, "run_web_dashboard_server", fake_run_server)

    socket_path = tmp_path / "daemon.sock"
    event_socket_path = tmp_path / "events.sock"
    result = cli.main(
        [
            "web",
            "--daemon-socket-path",
            str(socket_path),
            "--daemon-event-socket-path",
            str(event_socket_path),
            "--daemon-timeout",
            "2.5",
            "--daemon-max-response-bytes",
            "8192",
            "--daemon-max-event-bytes",
            "4096",
            "--listen-address",
            "localhost",
            "--listen-port",
            "8123",
            "--no-access-log",
        ],
        environ={},
    )

    assert result == 0
    assert server_calls == [(app, "127.0.0.1", 8123, False)]
    assert len(captured_api_factories) == 1
    assert len(captured_event_factories) == 1

    daemon_client = captured_api_factories[0]()
    event_client = captured_event_factories[0]()

    assert isinstance(daemon_client, FakeDaemonApiClient)
    assert daemon_client.location.path == socket_path
    assert daemon_client.location.source.value == "explicit"
    assert daemon_client.timeout == 2.5
    assert daemon_client.max_response_bytes == 8192

    assert isinstance(event_client, FakeDaemonEventClient)
    assert event_client.location.path == event_socket_path
    assert event_client.location.source.value == "explicit"
    assert event_client.timeout == 2.5
    assert event_client.max_event_bytes == 4096


def test_web_cli_rejects_scanner_connection_options(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = cli.main(
        ["--host", "192.168.0.251", "web"],
        environ={},
    )

    assert result == 2
    assert (
        "Scanner connection selectors are not used with daemon-client."
        in capsys.readouterr().err
    )
