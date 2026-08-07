from __future__ import annotations

import signal
from collections.abc import Callable
from pathlib import Path

import pytest

from sds200 import DaemonSocketSource, cli, resolve_configuration_paths
from sds200.audio import AudioChunkHandler
from sds200.daemon_process import DaemonProcessResult
from sds200.pcmu import PcmuPacketHandler
from sds200.pcmu_protocol import PCMU_STREAM_HEADER_BYTES
from sds200.profiles import ConnectionProfile


class FakeAudioTransport:
    def __init__(self) -> None:
        self._running = False
        self.packet_handlers: list[PcmuPacketHandler] = []

    @property
    def endpoint(self) -> str:
        return "rtsp://192.0.2.25/au:scanner.au"

    @property
    def running(self) -> bool:
        return self._running

    def on_packet(
        self,
        callback: PcmuPacketHandler,
    ) -> Callable[[], None]:
        self.packet_handlers.append(callback)

        def unsubscribe() -> None:
            if callback in self.packet_handlers:
                self.packet_handlers.remove(callback)

        return unsubscribe

    def start(self, handler: AudioChunkHandler) -> None:
        del handler
        self._running = True

    def stop(self) -> None:
        self._running = False


class FakeDaemonDestinationCoordinator:
    def __init__(
        self,
        runtime: object,
        *,
        factory: object,
        initial_configuration: object,
    ) -> None:
        self.runtime = runtime
        self.factory = factory
        self.initial_configuration = initial_configuration
        self.start_calls = 0
        self.stop_calls = 0

    def start(self) -> object:
        self.start_calls += 1
        return object()

    def stop(self) -> None:
        self.stop_calls += 1

    def close(self) -> None:
        self.stop()


class FakeDaemonEventStream:
    def __init__(
        self,
        runtime: object,
        *,
        recording_manager: object,
        queue_capacity: int,
        max_subscribers: int,
        max_event_bytes: int,
    ) -> None:
        self.runtime = runtime
        self.recording_manager = recording_manager
        self.queue_capacity = queue_capacity
        self.max_subscribers = max_subscribers
        self.max_event_bytes = max_event_bytes
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class StubProfileStore:
    def __init__(self, profile: ConnectionProfile) -> None:
        self.profile = profile

    def get(self, name: str) -> ConnectionProfile:
        assert name == self.profile.name
        return self.profile


def test_daemon_parser_accepts_process_and_audio_options() -> None:
    args = cli.build_parser().parse_args(
        [
            "--host",
            "192.0.2.25",
            "daemon",
            "--interval",
            "750",
            "--psi-timeout",
            "4",
            "--rtsp-port",
            "8554",
            "--rtsp-timeout",
            "6",
            "--rtp-bind-address",
            "192.0.2.10",
            "--rtp-bind-port",
            "40000",
            "--keepalive-interval",
            "20",
            "--destination-config",
            "/tmp/sdsctl-destinations.toml",
            "--recording-directory",
            "/tmp/sdsctl-recordings",
            "--recording-file-socket-path",
            "/tmp/sdsctl-recording-files-test.sock",
            "--recording-file-max-clients",
            "9",
            "--recording-file-max-identifier-bytes",
            "3072",
            "--recording-file-client-timeout",
            "12",
            "--recording-file-shutdown-timeout",
            "7",
            "--socket-path",
            "/tmp/sdsctl-test.sock",
            "--api-max-clients",
            "4",
            "--api-max-request-bytes",
            "8192",
            "--api-max-response-bytes",
            "16384",
            "--api-client-timeout",
            "7",
            "--api-shutdown-timeout",
            "3",
            "--event-socket-path",
            "/tmp/sdsctl-events-test.sock",
            "--event-queue-capacity",
            "96",
            "--event-max-clients",
            "5",
            "--event-max-bytes",
            "32768",
            "--event-send-timeout",
            "8",
            "--event-shutdown-timeout",
            "4",
            "--pcmu-socket-path",
            "/tmp/sdsctl-pcmu-test.sock",
            "--pcmu-queue-capacity",
            "128",
            "--pcmu-max-clients",
            "7",
            "--pcmu-max-payload-bytes",
            "2048",
            "--pcmu-max-endpoint-bytes",
            "1024",
            "--pcmu-max-frame-bytes",
            "8192",
            "--pcmu-send-timeout",
            "9",
            "--pcmu-shutdown-timeout",
            "6",
        ]
    )

    assert args.interval == 750
    assert args.psi_timeout == 4.0
    assert args.rtsp_port == 8554
    assert args.rtsp_timeout == 6.0
    assert args.rtp_bind_address == "192.0.2.10"
    assert args.rtp_bind_port == 40000
    assert args.keepalive_interval == 20.0
    assert args.destination_config == Path(
        "/tmp/sdsctl-destinations.toml"
    )
    assert args.recording_directory == Path("/tmp/sdsctl-recordings")
    assert args.recording_file_socket_path == Path(
        "/tmp/sdsctl-recording-files-test.sock"
    )
    assert args.recording_file_max_clients == 9
    assert args.recording_file_max_identifier_bytes == 3072
    assert args.recording_file_client_timeout == 12.0
    assert args.recording_file_shutdown_timeout == 7.0
    assert args.socket_path == Path("/tmp/sdsctl-test.sock")
    assert args.api_max_clients == 4
    assert args.api_max_request_bytes == 8192
    assert args.api_max_response_bytes == 16384
    assert args.api_client_timeout == 7.0
    assert args.api_shutdown_timeout == 3.0
    assert args.event_socket_path == Path("/tmp/sdsctl-events-test.sock")
    assert args.event_queue_capacity == 96
    assert args.event_max_clients == 5
    assert args.event_max_bytes == 32768
    assert args.event_send_timeout == 8.0
    assert args.event_shutdown_timeout == 4.0
    assert args.pcmu_socket_path == Path("/tmp/sdsctl-pcmu-test.sock")
    assert args.pcmu_queue_capacity == 128
    assert args.pcmu_max_clients == 7
    assert args.pcmu_max_payload_bytes == 2048
    assert args.pcmu_max_endpoint_bytes == 1024
    assert args.pcmu_max_frame_bytes == 8192
    assert args.pcmu_send_timeout == 9.0
    assert args.pcmu_shutdown_timeout == 6.0


@pytest.mark.parametrize(
    "profile",
    [
        ConnectionProfile.network("scanner", "192.0.2.25"),
        ConnectionProfile.fallback(
            "scanner",
            port="/dev/ttyACM0",
            host="192.0.2.25",
        ),
    ],
)
def test_daemon_host_resolves_network_capable_profile(
    profile: ConnectionProfile,
) -> None:
    args = cli.build_parser().parse_args(["--profile", "scanner", "daemon"])

    assert (
        cli._daemon_host(
            args,
            profile_store=StubProfileStore(profile),
        )
        == "192.0.2.25"
    )


def test_daemon_host_rejects_serial_only_profile() -> None:
    args = cli.build_parser().parse_args(["--profile", "scanner", "daemon"])
    store = StubProfileStore(
        ConnectionProfile.serial(
            "scanner",
            "/dev/ttyACM0",
            model="SDS200",
        )
    )

    with pytest.raises(ValueError, match="network-capable"):
        cli._daemon_host(args, profile_store=store)


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["daemon"], "requires --host"),
        (["--port", "/dev/ttyACM0", "daemon"], "requires --host"),
        (["--replay", "capture.jsonl", "daemon"], "does not support replay"),
        (
            ["--host", "192.0.2.25", "--model", "SDS100", "daemon"],
            "only available on the SDS200",
        ),
    ],
)
def test_daemon_host_rejects_unsupported_connection_modes(
    arguments: list[str],
    message: str,
) -> None:
    args = cli.build_parser().parse_args(arguments)

    with pytest.raises(ValueError, match=message):
        cli._daemon_host(args)


def test_daemon_cli_constructs_one_runtime_and_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scanner = object()
    selected: list[tuple[object, object]] = []
    transport_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    transports: list[FakeAudioTransport] = []
    processes: list[object] = []

    def select_radio(
        args: object,
        *,
        profile_store: object = None,
    ) -> object:
        selected.append((args, profile_store))
        return scanner

    def transport_factory(
        *args: object,
        **kwargs: object,
    ) -> FakeAudioTransport:
        transport_calls.append((args, kwargs))
        transport = FakeAudioTransport()
        transports.append(transport)
        return transport

    class FakeProcess:
        def __init__(
            self,
            runtime: object,
            *,
            destination_coordinator: object,
            destination_reloader: object,
            recording_manager: object,
            recording_file_server: object,
            api_server: object,
            event_server: object,
            pcmu_server: object,
        ) -> None:
            self.runtime = runtime
            self.destination_coordinator = destination_coordinator
            self.destination_reloader = destination_reloader
            self.recording_manager = recording_manager
            self.recording_file_server = recording_file_server
            self.api_server = api_server
            self.event_server = event_server
            self.pcmu_server = pcmu_server
            processes.append(self)

        def run(self) -> DaemonProcessResult:
            return DaemonProcessResult(last_signal=int(signal.SIGTERM))

    monkeypatch.setattr(cli, "selected_radio", select_radio)
    monkeypatch.setattr(cli, "NetworkAudioTransport", transport_factory)
    monkeypatch.setattr(
        cli,
        "DaemonDestinationCoordinator",
        FakeDaemonDestinationCoordinator,
    )
    monkeypatch.setattr(cli, "DaemonEventStream", FakeDaemonEventStream)
    monkeypatch.setattr(cli, "DaemonProcess", FakeProcess)

    paths = resolve_configuration_paths(
        environ={},
        home=tmp_path / "home",
        system_config_dir=tmp_path / "etc" / "sdsctl",
    )

    result = cli.main(
        [
            "--host",
            "192.0.2.25",
            "daemon",
            "--interval",
            "750",
            "--psi-timeout",
            "4",
            "--rtsp-port",
            "8554",
            "--rtsp-timeout",
            "6",
            "--rtp-bind-address",
            "192.0.2.10",
            "--rtp-bind-port",
            "40000",
            "--keepalive-interval",
            "20",
        ],
        configuration_paths=paths,
        environ={},
    )

    assert result == 0
    assert len(selected) == 1
    assert selected[0][1] is None
    assert transport_calls == [
        (
            ("192.0.2.25",),
            {
                "rtsp_port": 8554,
                "local_host": "192.0.2.10",
                "local_port": 40000,
                "rtsp_timeout": 6.0,
                "keepalive_interval": 20.0,
            },
        )
    ]
    assert len(processes) == 1

    process = processes[0]
    runtime = process.runtime
    destination_coordinator = process.destination_coordinator
    assert isinstance(
        destination_coordinator,
        FakeDaemonDestinationCoordinator,
    )
    assert destination_coordinator.runtime is runtime
    assert (
        destination_coordinator.initial_configuration.destinations
        == ()
    )
    assert destination_coordinator.factory.remote_profile_store.path == (
        paths.legacy_remote_audio_profiles_file
    )

    destination_reloader = process.destination_reloader
    assert isinstance(
        destination_reloader,
        cli.DaemonDestinationReloader,
    )
    assert destination_reloader.coordinator is destination_coordinator
    assert destination_reloader.path == (
        paths.daemon_destination_config_file
    )

    recording_manager = process.recording_manager
    assert isinstance(recording_manager, cli.DaemonRecordingManager)
    assert recording_manager.runtime is runtime
    assert recording_manager.directory == paths.daemon_recording_dir

    recording_file_server = process.recording_file_server
    assert isinstance(
        recording_file_server,
        cli.DaemonRecordingFileServer,
    )
    assert recording_file_server.recording_manager is recording_manager
    assert recording_file_server.max_clients == 8
    assert recording_file_server.max_identifier_bytes == 4096
    assert recording_file_server.client_timeout == 5.0
    assert recording_file_server.shutdown_timeout == 2.0
    assert recording_file_server.listener.location.source is (
        DaemonSocketSource.USER_STATE
    )
    assert recording_file_server.listener.location.path == (
        paths.user_state_dir / "recordings.sock"
    )

    assert runtime.scanner is scanner
    assert runtime.psi_interval_ms == 750
    assert runtime.psi_timeout == 4.0
    assert runtime.audio.sinks == (runtime.router,)
    assert runtime.router.name == "daemon-pcm"

    api_server = process.api_server
    assert isinstance(api_server, cli.DaemonApiServer)
    assert isinstance(api_server.api, cli.DaemonReadOnlyApi)
    assert api_server.api.runtime is runtime
    assert api_server.api.recording_manager is recording_manager
    assert api_server.max_clients == 8
    assert api_server.max_request_bytes == 64 * 1024
    assert api_server.max_response_bytes == 1024 * 1024
    assert api_server.client_timeout == 5.0
    assert api_server.shutdown_timeout == 3.0
    assert api_server.listener.location.source is DaemonSocketSource.USER_STATE
    assert api_server.listener.location.path == (
        paths.user_state_dir / "daemon.sock"
    )

    event_server = process.event_server
    assert isinstance(event_server, cli.DaemonEventServer)
    assert isinstance(event_server.stream, cli.DaemonEventStream)
    assert event_server.stream.runtime is runtime
    assert event_server.stream.recording_manager is recording_manager
    assert event_server.stream.queue_capacity == 64
    assert event_server.stream.max_subscribers == 8
    assert event_server.stream.max_event_bytes == 1024 * 1024
    assert event_server.max_clients == 8
    assert event_server.max_event_bytes == 1024 * 1024
    assert event_server.send_timeout == 5.0
    assert event_server.shutdown_timeout == 2.0
    assert event_server.listener.location.source is DaemonSocketSource.USER_STATE
    assert event_server.listener.location.path == (
        paths.user_state_dir / "events.sock"
    )

    pcmu_server = process.pcmu_server
    assert isinstance(pcmu_server, cli.DaemonPcmuServer)
    assert isinstance(pcmu_server.stream, cli.PcmuStream)
    assert pcmu_server.stream.source is transports[0]
    assert pcmu_server.stream.queue_capacity == 64
    assert pcmu_server.stream.max_subscribers == 8
    assert pcmu_server.stream.max_payload_bytes == 65535
    assert pcmu_server.max_clients == 8
    assert pcmu_server.max_endpoint_bytes == 4096
    assert pcmu_server.max_frame_bytes == 128 * 1024
    assert pcmu_server.send_timeout == 5.0
    assert pcmu_server.shutdown_timeout == 2.0
    assert pcmu_server.listener.location.source is (
        DaemonSocketSource.USER_STATE
    )
    assert pcmu_server.listener.location.path == (
        paths.user_state_dir / "pcmu.sock"
    )
    assert len(transports[0].packet_handlers) == 1
    assert capsys.readouterr().out == ""


def test_daemon_cli_loads_explicit_destination_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = tmp_path / "daemon-destinations.toml"
    manifest.write_text(
        "version = 1\n"
        "[destinations.speakers]\n"
        'kind = "playback"\n'
        'backend = "sounddevice"\n',
        encoding="utf-8",
    )
    observed: list[FakeDaemonDestinationCoordinator] = []
    observed_reload_paths: list[Path] = []

    monkeypatch.setattr(
        cli,
        "selected_radio",
        lambda args, **kwargs: object(),
    )
    monkeypatch.setattr(
        cli,
        "NetworkAudioTransport",
        lambda *args, **kwargs: FakeAudioTransport(),
    )
    monkeypatch.setattr(
        cli,
        "DaemonEventStream",
        FakeDaemonEventStream,
    )

    def coordinator_factory(
        runtime: object,
        *,
        factory: object,
        initial_configuration: object,
    ) -> FakeDaemonDestinationCoordinator:
        coordinator = FakeDaemonDestinationCoordinator(
            runtime,
            factory=factory,
            initial_configuration=initial_configuration,
        )
        observed.append(coordinator)
        return coordinator

    class FakeProcess:
        def __init__(
            self,
            runtime: object,
            *,
            destination_coordinator: object,
            destination_reloader: object,
            recording_manager: object,
            recording_file_server: object,
            api_server: object,
            event_server: object,
            pcmu_server: object,
        ) -> None:
            del (
                runtime,
                destination_coordinator,
                api_server,
                event_server,
                pcmu_server,
            )
            observed_reload_paths.append(
                destination_reloader.path  # type: ignore[attr-defined]
            )

        def run(self) -> DaemonProcessResult:
            return DaemonProcessResult(
                last_signal=int(signal.SIGTERM)
            )

    monkeypatch.setattr(
        cli,
        "DaemonDestinationCoordinator",
        coordinator_factory,
    )
    monkeypatch.setattr(cli, "DaemonProcess", FakeProcess)

    result = cli.main(
        [
            "--host",
            "192.0.2.25",
            "daemon",
            "--destination-config",
            str(manifest),
        ],
        environ={},
    )

    assert result == 0
    assert len(observed) == 1
    destination = (
        observed[0]
        .initial_configuration
        .destination("speakers")
    )
    assert destination.kind == "playback"
    assert destination.backend == "sounddevice"
    assert observed_reload_paths == [manifest]


def test_daemon_cli_rejects_invalid_destination_manifest_before_scanner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest = tmp_path / "daemon-destinations.toml"
    manifest.write_text(
        "version = 2\n",
        encoding="utf-8",
    )
    selections = 0

    def select_radio(*args: object, **kwargs: object) -> object:
        nonlocal selections
        del args, kwargs
        selections += 1
        return object()

    monkeypatch.setattr(cli, "selected_radio", select_radio)

    result = cli.main(
        [
            "--host",
            "192.0.2.25",
            "daemon",
            "--destination-config",
            str(manifest),
        ],
        environ={},
    )

    assert result == 2
    assert selections == 0
    assert "version must be 1" in capsys.readouterr().err


def test_daemon_cli_reports_profile_validation_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class Store:
        def __init__(self, path: object) -> None:
            del path

        def get(self, name: str) -> ConnectionProfile:
            return ConnectionProfile.serial(
                name,
                "/dev/ttyACM0",
                model="SDS200",
            )

    monkeypatch.setattr(cli, "ProfileStore", Store)

    assert cli.main(["--profile", "scanner", "daemon"]) == 2
    assert "network-capable" in capsys.readouterr().err


def test_daemon_cli_reports_process_os_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "selected_radio", lambda args, **kwargs: object())
    monkeypatch.setattr(
        cli,
        "NetworkAudioTransport",
        lambda *args, **kwargs: FakeAudioTransport(),
    )

    class FailingProcess:
        def __init__(
            self,
            runtime: object,
            *,
            destination_coordinator: object,
            destination_reloader: object,
            recording_manager: object,
            recording_file_server: object,
            api_server: object,
            event_server: object,
            pcmu_server: object,
        ) -> None:
            del (
                runtime,
                destination_coordinator,
                destination_reloader,
                api_server,
                event_server,
                pcmu_server,
            )

        def run(self) -> DaemonProcessResult:
            raise OSError("process startup failed")

    monkeypatch.setattr(
        cli,
        "DaemonDestinationCoordinator",
        FakeDaemonDestinationCoordinator,
    )
    monkeypatch.setattr(cli, "DaemonEventStream", FakeDaemonEventStream)
    monkeypatch.setattr(cli, "DaemonProcess", FailingProcess)

    assert cli.main(["--host", "192.0.2.25", "daemon"]) == 2
    assert "process startup failed" in capsys.readouterr().err


def test_daemon_cli_explicit_socket_path_overrides_runtime_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    explicit = tmp_path / "explicit" / "daemon.sock"
    explicit_events = tmp_path / "explicit" / "events.sock"
    explicit_pcmu = tmp_path / "explicit" / "pcmu.sock"
    explicit_recording_files = tmp_path / "explicit" / "recordings.sock"
    explicit_recordings = tmp_path / "recordings"
    explicit.parent.mkdir()
    observed: list[tuple[object, object, object, object, object]] = []

    monkeypatch.setattr(
        cli,
        "selected_radio",
        lambda args, **kwargs: object(),
    )
    monkeypatch.setattr(
        cli,
        "NetworkAudioTransport",
        lambda *args, **kwargs: FakeAudioTransport(),
    )

    class FakeProcess:
        def __init__(
            self,
            runtime: object,
            *,
            destination_coordinator: object,
            destination_reloader: object,
            recording_manager: object,
            recording_file_server: object,
            api_server: object,
            event_server: object,
            pcmu_server: object,
        ) -> None:
            del runtime, destination_coordinator, destination_reloader
            observed.append(
                (
                    recording_manager,
                    recording_file_server,
                    api_server,
                    event_server,
                    pcmu_server,
                )
            )

        def run(self) -> DaemonProcessResult:
            return DaemonProcessResult(last_signal=int(signal.SIGTERM))

    monkeypatch.setattr(
        cli,
        "DaemonDestinationCoordinator",
        FakeDaemonDestinationCoordinator,
    )
    monkeypatch.setattr(cli, "DaemonEventStream", FakeDaemonEventStream)
    monkeypatch.setattr(cli, "DaemonProcess", FakeProcess)

    result = cli.main(
        [
            "--host",
            "192.0.2.25",
            "daemon",
            "--socket-path",
            str(explicit),
            "--recording-directory",
            str(explicit_recordings),
            "--recording-file-socket-path",
            str(explicit_recording_files),
            "--recording-file-max-clients",
            "4",
            "--recording-file-max-identifier-bytes",
            "1536",
            "--recording-file-client-timeout",
            "13",
            "--recording-file-shutdown-timeout",
            "8",
            "--api-max-clients",
            "3",
            "--api-max-request-bytes",
            "4096",
            "--api-max-response-bytes",
            "8192",
            "--api-client-timeout",
            "9",
            "--api-shutdown-timeout",
            "4",
            "--event-socket-path",
            str(explicit_events),
            "--event-queue-capacity",
            "12",
            "--event-max-clients",
            "6",
            "--event-max-bytes",
            "65536",
            "--event-send-timeout",
            "10",
            "--event-shutdown-timeout",
            "5",
            "--pcmu-socket-path",
            str(explicit_pcmu),
            "--pcmu-queue-capacity",
            "20",
            "--pcmu-max-clients",
            "7",
            "--pcmu-max-payload-bytes",
            "4096",
            "--pcmu-max-endpoint-bytes",
            "2048",
            "--pcmu-max-frame-bytes",
            "16384",
            "--pcmu-send-timeout",
            "11",
            "--pcmu-shutdown-timeout",
            "6",
        ],
        environ={
            "XDG_RUNTIME_DIR": str(tmp_path / "runtime"),
        },
    )

    assert result == 0
    assert len(observed) == 1
    (
        recording_manager,
        recording_file_server,
        api_server,
        event_server,
        pcmu_server,
    ) = observed[0]
    assert isinstance(recording_manager, cli.DaemonRecordingManager)
    assert recording_manager.directory == explicit_recordings

    assert isinstance(
        recording_file_server,
        cli.DaemonRecordingFileServer,
    )
    assert recording_file_server.recording_manager is recording_manager
    assert recording_file_server.listener.location.source is (
        DaemonSocketSource.EXPLICIT
    )
    assert (
        recording_file_server.listener.location.path
        == explicit_recording_files
    )
    assert recording_file_server.max_clients == 4
    assert recording_file_server.max_identifier_bytes == 1536
    assert recording_file_server.client_timeout == 13.0
    assert recording_file_server.shutdown_timeout == 8.0

    assert isinstance(api_server, cli.DaemonApiServer)
    assert api_server.listener.location.source is DaemonSocketSource.EXPLICIT
    assert api_server.listener.location.path == explicit
    assert api_server.max_clients == 3
    assert api_server.max_request_bytes == 4096
    assert api_server.max_response_bytes == 8192
    assert api_server.client_timeout == 9.0
    assert api_server.shutdown_timeout == 4.0

    assert isinstance(event_server, cli.DaemonEventServer)
    assert isinstance(event_server.stream, cli.DaemonEventStream)
    assert event_server.stream.queue_capacity == 12
    assert event_server.stream.max_subscribers == 6
    assert event_server.stream.max_event_bytes == 65536
    assert event_server.listener.location.source is DaemonSocketSource.EXPLICIT
    assert event_server.listener.location.path == explicit_events
    assert event_server.max_clients == 6
    assert event_server.max_event_bytes == 65536
    assert event_server.send_timeout == 10.0
    assert event_server.shutdown_timeout == 5.0

    assert isinstance(pcmu_server, cli.DaemonPcmuServer)
    assert isinstance(pcmu_server.stream, cli.PcmuStream)
    assert pcmu_server.stream.queue_capacity == 20
    assert pcmu_server.stream.max_subscribers == 7
    assert pcmu_server.stream.max_payload_bytes == 4096
    assert pcmu_server.listener.location.source is (
        DaemonSocketSource.EXPLICIT
    )
    assert pcmu_server.listener.location.path == explicit_pcmu
    assert pcmu_server.max_clients == 7
    assert pcmu_server.max_endpoint_bytes == 2048
    assert pcmu_server.max_frame_bytes == 16384
    assert pcmu_server.send_timeout == 11.0
    assert pcmu_server.shutdown_timeout == 6.0


def test_daemon_cli_reports_relative_socket_path(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli,
        "selected_radio",
        lambda args, **kwargs: object(),
    )
    monkeypatch.setattr(
        cli,
        "NetworkAudioTransport",
        lambda *args, **kwargs: FakeAudioTransport(),
    )

    result = cli.main(
        [
            "--host",
            "192.0.2.25",
            "daemon",
            "--socket-path",
            "relative/daemon.sock",
        ],
        environ={},
    )

    assert result == 2
    assert "must be absolute" in capsys.readouterr().err


def test_daemon_cli_reports_relative_event_socket_path(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli,
        "selected_radio",
        lambda args, **kwargs: object(),
    )
    monkeypatch.setattr(
        cli,
        "NetworkAudioTransport",
        lambda *args, **kwargs: FakeAudioTransport(),
    )

    result = cli.main(
        [
            "--host",
            "192.0.2.25",
            "daemon",
            "--event-socket-path",
            "relative/events.sock",
        ],
        environ={},
    )

    assert result == 2
    assert "must be absolute" in capsys.readouterr().err


def test_daemon_cli_reports_relative_pcmu_socket_path(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli,
        "selected_radio",
        lambda args, **kwargs: object(),
    )
    monkeypatch.setattr(
        cli,
        "NetworkAudioTransport",
        lambda *args, **kwargs: FakeAudioTransport(),
    )

    result = cli.main(
        [
            "--host",
            "192.0.2.25",
            "daemon",
            "--pcmu-socket-path",
            "relative/pcmu.sock",
        ],
        environ={},
    )

    assert result == 2
    assert "must be absolute" in capsys.readouterr().err


def test_daemon_cli_closes_pcmu_stream_after_server_validation_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    transports: list[FakeAudioTransport] = []
    event_streams: list[FakeDaemonEventStream] = []

    def transport_factory(
        *args: object,
        **kwargs: object,
    ) -> FakeAudioTransport:
        del args, kwargs
        transport = FakeAudioTransport()
        transports.append(transport)
        return transport

    monkeypatch.setattr(
        cli,
        "selected_radio",
        lambda args, **kwargs: object(),
    )
    monkeypatch.setattr(
        cli,
        "NetworkAudioTransport",
        transport_factory,
    )

    def event_stream_factory(
        runtime: object,
        *,
        recording_manager: object,
        queue_capacity: int,
        max_subscribers: int,
        max_event_bytes: int,
    ) -> FakeDaemonEventStream:
        stream = FakeDaemonEventStream(
            runtime,
            recording_manager=recording_manager,
            queue_capacity=queue_capacity,
            max_subscribers=max_subscribers,
            max_event_bytes=max_event_bytes,
        )
        event_streams.append(stream)
        return stream

    monkeypatch.setattr(
        cli,
        "DaemonEventStream",
        event_stream_factory,
    )

    result = cli.main(
        [
            "--host",
            "192.0.2.25",
            "daemon",
            "--pcmu-max-frame-bytes",
            str(PCMU_STREAM_HEADER_BYTES - 1),
        ],
        environ={},
    )

    assert result == 2
    assert len(transports) == 1
    assert transports[0].packet_handlers == []
    assert len(event_streams) == 1
    assert event_streams[0].close_calls == 1
    assert "must be at least" in capsys.readouterr().err
