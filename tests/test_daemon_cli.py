from __future__ import annotations

import signal

import pytest

from sds200 import cli
from sds200.audio import AudioChunkHandler
from sds200.daemon_process import DaemonProcessResult
from sds200.profiles import ConnectionProfile


class FakeAudioTransport:
    def __init__(self) -> None:
        self._running = False

    @property
    def endpoint(self) -> str:
        return "rtsp://192.0.2.25/au:scanner.au"

    @property
    def running(self) -> bool:
        return self._running

    def start(self, handler: AudioChunkHandler) -> None:
        del handler
        self._running = True

    def stop(self) -> None:
        self._running = False


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
        ]
    )

    assert args.interval == 750
    assert args.psi_timeout == 4.0
    assert args.rtsp_port == 8554
    assert args.rtsp_timeout == 6.0
    assert args.rtp_bind_address == "192.0.2.10"
    assert args.rtp_bind_port == 40000
    assert args.keepalive_interval == 20.0


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
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scanner = object()
    selected: list[tuple[object, object]] = []
    transport_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
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
        return FakeAudioTransport()

    class FakeProcess:
        def __init__(self, runtime: object) -> None:
            self.runtime = runtime
            processes.append(self)

        def run(self) -> DaemonProcessResult:
            return DaemonProcessResult(last_signal=int(signal.SIGTERM))

    monkeypatch.setattr(cli, "selected_radio", select_radio)
    monkeypatch.setattr(cli, "NetworkAudioTransport", transport_factory)
    monkeypatch.setattr(cli, "DaemonProcess", FakeProcess)

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
        ]
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
    assert runtime.scanner is scanner
    assert runtime.psi_interval_ms == 750
    assert runtime.psi_timeout == 4.0
    assert runtime.audio.sinks == (runtime.router,)
    assert runtime.router.name == "daemon-pcm"
    assert capsys.readouterr().out == ""


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
        def __init__(self, runtime: object) -> None:
            del runtime

        def run(self) -> DaemonProcessResult:
            raise OSError("process startup failed")

    monkeypatch.setattr(cli, "DaemonProcess", FailingProcess)

    assert cli.main(["--host", "192.0.2.25", "daemon"]) == 2
    assert "process startup failed" in capsys.readouterr().err
