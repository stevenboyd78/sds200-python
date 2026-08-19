from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from sds200 import DaemonSocketLocation, cli, web_dashboard
from sds200.web_server import (
    WEB_DASHBOARD_CONTAINER_EXPOSURE_HOST,
    WEB_DASHBOARD_DEFAULT_PORT,
    WEB_DASHBOARD_HOME_ASSISTANT_INGRESS_HOST,
)


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


class FakeDaemonRecordingFileClient:
    instances: list[FakeDaemonRecordingFileClient] = []

    def __init__(
        self,
        location: DaemonSocketLocation,
        *,
        timeout: float,
        max_content_bytes: int,
    ) -> None:
        self.location = location
        self.timeout = timeout
        self.max_content_bytes = max_content_bytes
        self.instances.append(self)


class FakeDaemonPcmuClient:
    instances: list[FakeDaemonPcmuClient] = []

    def __init__(
        self,
        location: DaemonSocketLocation,
        *,
        timeout: float,
        max_endpoint_bytes: int,
        max_frame_bytes: int,
    ) -> None:
        self.location = location
        self.timeout = timeout
        self.max_endpoint_bytes = max_endpoint_bytes
        self.max_frame_bytes = max_frame_bytes
        self.instances.append(self)


def test_web_parser_uses_loopback_defaults() -> None:
    args = cli.build_parser().parse_args(["web"])

    assert args.action == "web"
    assert args.home_assistant_ingress is False
    assert args.container_exposure is False
    assert args.daemon_socket_path is None
    assert args.daemon_event_socket_path is None
    assert args.daemon_pcmu_socket_path is None
    assert args.daemon_recording_file_socket_path is None
    assert args.daemon_timeout == 5.0
    assert args.daemon_max_response_bytes is None
    assert args.daemon_max_event_bytes is None
    assert args.daemon_pcmu_max_endpoint_bytes is None
    assert args.daemon_pcmu_max_frame_bytes is None
    assert args.daemon_recording_file_max_content_bytes is None
    assert args.listen_address is None
    assert args.listen_port == WEB_DASHBOARD_DEFAULT_PORT
    assert args.access_log is True


def test_web_parser_accepts_home_assistant_ingress() -> None:
    args = cli.build_parser().parse_args(
        ["web", "--home-assistant-ingress"]
    )

    assert args.home_assistant_ingress is True
    assert args.listen_address is None


def test_web_parser_accepts_container_exposure() -> None:
    args = cli.build_parser().parse_args(["web", "--container-exposure"])

    assert args.container_exposure is True
    assert args.home_assistant_ingress is False
    assert args.listen_address is None


def test_web_parser_accepts_explicit_local_options() -> None:
    args = cli.build_parser().parse_args(
        [
            "web",
            "--daemon-socket-path",
            "/tmp/sdsctl/daemon.sock",
            "--daemon-event-socket-path",
            "/tmp/sdsctl/events.sock",
            "--daemon-pcmu-socket-path",
            "/tmp/sdsctl/pcmu.sock",
            "--daemon-recording-file-socket-path",
            "/tmp/sdsctl/recordings.sock",
            "--daemon-timeout",
            "2.5",
            "--daemon-max-response-bytes",
            "8192",
            "--daemon-max-event-bytes",
            "4096",
            "--daemon-pcmu-max-endpoint-bytes",
            "2048",
            "--daemon-pcmu-max-frame-bytes",
            "65536",
            "--daemon-recording-file-max-content-bytes",
            "1048576",
            "--listen-address",
            "::1",
            "--listen-port",
            "8123",
            "--no-access-log",
        ]
    )

    assert args.daemon_socket_path == Path("/tmp/sdsctl/daemon.sock")
    assert args.daemon_event_socket_path == Path("/tmp/sdsctl/events.sock")
    assert args.daemon_pcmu_socket_path == Path("/tmp/sdsctl/pcmu.sock")
    assert args.daemon_recording_file_socket_path == Path(
        "/tmp/sdsctl/recordings.sock"
    )
    assert args.daemon_timeout == 2.5
    assert args.daemon_max_response_bytes == 8192
    assert args.daemon_max_event_bytes == 4096
    assert args.daemon_pcmu_max_endpoint_bytes == 2048
    assert args.daemon_pcmu_max_frame_bytes == 65536
    assert args.daemon_recording_file_max_content_bytes == 1048576
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


@pytest.mark.parametrize(
    ("frame_bytes", "message"),
    [
        (81, "must be at least 82"),
        (131073, "must not exceed the browser stream limit of 131072"),
    ],
)
def test_web_cli_rejects_pcmu_frame_limits_outside_browser_contract(
    frame_bytes: int,
    message: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = cli.main(
        [
            "web",
            "--daemon-pcmu-max-frame-bytes",
            str(frame_bytes),
        ],
        environ={},
    )

    assert result == 2
    assert message in capsys.readouterr().err


def test_web_cli_builds_daemon_clients_and_runs_server(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    FakeDaemonApiClient.instances.clear()
    FakeDaemonEventClient.instances.clear()
    FakeDaemonPcmuClient.instances.clear()
    FakeDaemonRecordingFileClient.instances.clear()
    captured_api_factories: list[Callable[[], object]] = []
    captured_event_factories: list[Callable[[], object]] = []
    captured_pcmu_factories: list[Callable[[], object]] = []
    captured_recording_file_factories: list[Callable[[], object]] = []
    app = object()
    captured_ingress_modes: list[bool] = []
    server_calls: list[tuple[object, str, int, bool, bool]] = []

    def fake_create_app(
        api_client_factory: Callable[[], object],
        event_client_factory: Callable[[], object],
        pcmu_client_factory: Callable[[], object],
        recording_file_client_factory: Callable[[], object],
        *,
        home_assistant_ingress: bool = False,
    ) -> object:
        captured_api_factories.append(api_client_factory)
        captured_event_factories.append(event_client_factory)
        captured_pcmu_factories.append(pcmu_client_factory)
        captured_recording_file_factories.append(
            recording_file_client_factory
        )
        captured_ingress_modes.append(home_assistant_ingress)
        return app

    def fake_run_server(
        selected_app: object,
        *,
        host: str,
        port: int,
        access_log: bool,
        home_assistant_ingress: bool = False,
        container_exposure: bool = False,
    ) -> int:
        assert container_exposure is False
        server_calls.append(
            (
                selected_app,
                host,
                port,
                access_log,
                home_assistant_ingress,
            )
        )
        return 0

    monkeypatch.setattr(cli, "DaemonApiClient", FakeDaemonApiClient)
    monkeypatch.setattr(cli, "DaemonEventClient", FakeDaemonEventClient)
    monkeypatch.setattr(cli, "DaemonPcmuClient", FakeDaemonPcmuClient)
    monkeypatch.setattr(
        cli,
        "DaemonRecordingFileClient",
        FakeDaemonRecordingFileClient,
    )
    monkeypatch.setattr(
        web_dashboard,
        "create_web_dashboard_app",
        fake_create_app,
    )
    monkeypatch.setattr(cli, "run_web_dashboard_server", fake_run_server)

    socket_path = tmp_path / "daemon.sock"
    event_socket_path = tmp_path / "events.sock"
    pcmu_socket_path = tmp_path / "pcmu.sock"
    recording_file_socket_path = tmp_path / "recordings.sock"
    result = cli.main(
        [
            "web",
            "--daemon-socket-path",
            str(socket_path),
            "--daemon-event-socket-path",
            str(event_socket_path),
            "--daemon-pcmu-socket-path",
            str(pcmu_socket_path),
            "--daemon-recording-file-socket-path",
            str(recording_file_socket_path),
            "--daemon-timeout",
            "2.5",
            "--daemon-max-response-bytes",
            "8192",
            "--daemon-max-event-bytes",
            "4096",
            "--daemon-pcmu-max-endpoint-bytes",
            "2048",
            "--daemon-pcmu-max-frame-bytes",
            "65536",
            "--daemon-recording-file-max-content-bytes",
            "1048576",
            "--listen-address",
            "localhost",
            "--listen-port",
            "8123",
            "--no-access-log",
        ],
        environ={},
    )

    assert result == 0
    assert server_calls == [(app, "127.0.0.1", 8123, False, False)]
    assert captured_ingress_modes == [False]
    assert len(captured_api_factories) == 1
    assert len(captured_event_factories) == 1
    assert len(captured_pcmu_factories) == 1
    assert len(captured_recording_file_factories) == 1

    daemon_client = captured_api_factories[0]()
    event_client = captured_event_factories[0]()
    pcmu_client = captured_pcmu_factories[0]()
    recording_file_client = captured_recording_file_factories[0]()

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

    assert isinstance(pcmu_client, FakeDaemonPcmuClient)
    assert pcmu_client.location.path == pcmu_socket_path
    assert pcmu_client.location.source.value == "explicit"
    assert pcmu_client.timeout == 2.5
    assert pcmu_client.max_endpoint_bytes == 2048
    assert pcmu_client.max_frame_bytes == 65536

    assert isinstance(
        recording_file_client,
        FakeDaemonRecordingFileClient,
    )
    assert recording_file_client.location.path == recording_file_socket_path
    assert recording_file_client.location.source.value == "explicit"
    assert recording_file_client.timeout == 2.5
    assert recording_file_client.max_content_bytes == 1048576


def test_web_cli_home_assistant_ingress_binds_wildcard_and_enables_guard(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app = object()
    create_calls: list[bool] = []
    server_calls: list[tuple[object, str, int, bool, bool]] = []

    def fake_create_app(
        api_client_factory: Callable[[], object],
        event_client_factory: Callable[[], object],
        pcmu_client_factory: Callable[[], object],
        recording_file_client_factory: Callable[[], object],
        *,
        home_assistant_ingress: bool = False,
    ) -> object:
        del (
            api_client_factory,
            event_client_factory,
            pcmu_client_factory,
            recording_file_client_factory,
        )
        create_calls.append(home_assistant_ingress)
        return app

    def fake_run_server(
        selected_app: object,
        *,
        host: str,
        port: int,
        access_log: bool,
        home_assistant_ingress: bool = False,
        container_exposure: bool = False,
    ) -> int:
        assert container_exposure is False
        server_calls.append(
            (
                selected_app,
                host,
                port,
                access_log,
                home_assistant_ingress,
            )
        )
        return 0

    monkeypatch.setattr(
        web_dashboard,
        "create_web_dashboard_app",
        fake_create_app,
    )
    monkeypatch.setattr(cli, "run_web_dashboard_server", fake_run_server)

    result = cli.main(
        [
            "web",
            "--home-assistant-ingress",
            "--daemon-socket-path",
            str(tmp_path / "daemon.sock"),
            "--daemon-event-socket-path",
            str(tmp_path / "events.sock"),
            "--daemon-pcmu-socket-path",
            str(tmp_path / "pcmu.sock"),
            "--daemon-recording-file-socket-path",
            str(tmp_path / "recordings.sock"),
            "--listen-port",
            "8099",
        ],
        environ={},
    )

    assert result == 0
    assert create_calls == [True]
    assert server_calls == [
        (
            app,
            WEB_DASHBOARD_HOME_ASSISTANT_INGRESS_HOST,
            8099,
            True,
            True,
        )
    ]


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "::1"],
)
def test_web_cli_home_assistant_ingress_rejects_listen_address(
    address: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = cli.main(
        [
            "web",
            "--home-assistant-ingress",
            "--listen-address",
            address,
        ],
        environ={},
    )

    assert result == 2
    assert (
        "--listen-address cannot be used with --home-assistant-ingress"
        in capsys.readouterr().err
    )


def test_web_cli_container_exposure_uses_wildcard_without_ingress(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    create_calls: list[bool] = []
    server_calls: list[tuple[str, bool, bool]] = []

    def fake_create_app(
        *args: object,
        home_assistant_ingress: bool = False,
    ) -> object:
        del args
        create_calls.append(home_assistant_ingress)
        return object()

    def fake_run_server(
        app: object,
        *,
        host: str,
        port: int,
        access_log: bool,
        home_assistant_ingress: bool = False,
        container_exposure: bool = False,
    ) -> int:
        del app, port, access_log
        server_calls.append((host, home_assistant_ingress, container_exposure))
        return 0

    monkeypatch.setattr(
        web_dashboard,
        "create_web_dashboard_app",
        fake_create_app,
    )
    monkeypatch.setattr(cli, "run_web_dashboard_server", fake_run_server)
    result = cli.main(
        [
            "web",
            "--container-exposure",
            "--daemon-socket-path",
            str(tmp_path / "daemon.sock"),
            "--daemon-event-socket-path",
            str(tmp_path / "events.sock"),
            "--daemon-pcmu-socket-path",
            str(tmp_path / "pcmu.sock"),
            "--daemon-recording-file-socket-path",
            str(tmp_path / "recordings.sock"),
        ],
        environ={},
    )

    assert result == 0
    assert create_calls == [False]
    assert server_calls == [(WEB_DASHBOARD_CONTAINER_EXPOSURE_HOST, False, True)]


@pytest.mark.parametrize(
    "arguments,message",
    [
        (
            ["--container-exposure", "--listen-address", "127.0.0.1"],
            "--listen-address cannot be used with --container-exposure",
        ),
        (
            ["--container-exposure", "--home-assistant-ingress"],
            "--container-exposure cannot be used with --home-assistant-ingress",
        ),
    ],
)
def test_web_cli_rejects_conflicting_container_exposure_options(
    arguments: list[str],
    message: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["web", *arguments], environ={}) == 2
    assert message in capsys.readouterr().err

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
