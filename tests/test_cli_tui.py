from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from sds200 import DaemonTuiRadio, cli
from sds200.models import ScannerInfo
from sds200.radio import SDSScanner
from sds200.state import RadioStateSnapshot
from sds200.theme import DEFAULT_LIGHT_THEME
from sds200.tui_audio import TuiAudioSession
from sds200.xml_protocol import ScannerInfoParser

from .fakes import FakeAudioTransport

FIXTURE = Path(__file__).parent / "fixtures" / "replay" / "sds100-tui.jsonl"
XML = """<?xml version="1.0" encoding="utf-8"?>
<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">
<Property VOL="10" SQL="2" Sig="5" Rssi="-86" />
</ScannerInfo>"""


class FakeTuiRadio:
    endpoint = "udp://192.0.2.25:50536"
    connected = True

    def get_model(self) -> str:
        return "SDS200"

    def get_firmware(self) -> str:
        return "Version 1.26.01"

    def get_scanner_info(self) -> ScannerInfo:
        return ScannerInfoParser().parse("GSI", XML)


def test_tui_cli_uses_replay_radio_and_selected_theme(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run_tui(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr("sds200.tui.run_tui", fake_run_tui)

    assert (
        cli.main(
            [
                "--replay",
                str(FIXTURE),
                "--theme",
                "light",
                "tui",
                "--interval",
                "250",
                "--stale-after",
                "1.5",
            ]
        )
        == 0
    )

    assert captured["endpoint"] == f"replay://{FIXTURE.resolve()}"
    assert captured["model"] == "SDS100"
    assert captured["firmware"] == "Version 1.26.01"
    assert captured["connected"] is True
    assert captured["palette"] is DEFAULT_LIGHT_THEME
    assert captured["interval_ms"] == 250
    assert captured["stale_after"] == 1.5
    radio = captured["radio"]
    assert isinstance(radio, SDSScanner)
    assert radio.endpoint == f"replay://{FIXTURE.resolve()}"
    snapshot = captured["snapshot"]
    assert isinstance(snapshot, RadioStateSnapshot)
    assert snapshot.system == "Example P25 System"
    assert snapshot.channel == "Example Dispatch"


@pytest.mark.parametrize(
    ("extra", "autostart"),
    [([], False), (["--audio-device", "3"], False), (["--audio-playback"], True)],
)
def test_host_tui_always_builds_manual_playback_session(
    monkeypatch: pytest.MonkeyPatch,
    extra: list[str],
    autostart: bool,
) -> None:
    captured: dict[str, object] = {}
    radio = FakeTuiRadio()

    @contextmanager
    def selected_radio(args: object) -> Iterator[FakeTuiRadio]:
        del args
        yield radio

    def fake_run_tui(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(cli, "selected_radio", selected_radio)
    monkeypatch.setattr(cli, "NetworkAudioTransport", lambda *args, **kwargs: FakeAudioTransport())
    monkeypatch.setattr("sds200.tui.run_tui", fake_run_tui)

    assert cli.main(["--host", "192.0.2.25", "tui", *extra]) == 0

    session = captured["audio_session"]
    assert isinstance(session, TuiAudioSession)
    assert session.playback_available
    assert session.live_playback_enabled is autostart


def test_tui_parser_accepts_explicit_daemon_client_options() -> None:
    args = cli.build_parser().parse_args(
        [
            "tui",
            "--daemon-client",
            "--daemon-socket-path",
            "/tmp/sdsctl-daemon.sock",
            "--daemon-event-socket-path",
            "/tmp/sdsctl-events.sock",
            "--daemon-timeout",
            "1.5",
            "--daemon-max-response-bytes",
            "8192",
            "--daemon-max-event-bytes",
            "4096",
        ]
    )

    assert args.daemon_client is True
    assert args.daemon_socket_path == Path("/tmp/sdsctl-daemon.sock")
    assert args.daemon_event_socket_path == Path("/tmp/sdsctl-events.sock")
    assert args.daemon_timeout == 1.5
    assert args.daemon_max_response_bytes == 8192
    assert args.daemon_max_event_bytes == 4096


def test_tui_cli_uses_daemon_without_opening_scanner_or_rtsp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeApiClient:
        instances: list[FakeApiClient] = []

        def __init__(
            self,
            location: object,
            *,
            timeout: float,
            max_response_bytes: int,
        ) -> None:
            self.location = location
            self.timeout = timeout
            self.max_response_bytes = max_response_bytes
            self.closed = False
            self.hello_calls = 0
            self.snapshot_calls = 0
            self.instances.append(self)

        def hello(self) -> dict[str, object]:
            self.hello_calls += 1
            return {"operations": ["runtime.snapshot"]}

        def runtime_snapshot(self) -> dict[str, object]:
            self.snapshot_calls += 1
            return {
                "scanner_endpoint": "udp://192.0.2.25:50536",
                "scanner_model": "SDS200",
                "scanner_firmware": "Version 1.26.01",
                "scanner_connected": True,
                "radio_state": {
                    "screen_kind": "scanning",
                    "system": "Metro",
                    "channel": "Primary",
                    "signal": 5,
                    "rssi": -74,
                },
            }

        def close(self) -> None:
            self.closed = True

    class FakeEventClient:
        instances: list[FakeEventClient] = []

        def __init__(
            self,
            location: object,
            *,
            timeout: float,
            max_event_bytes: int,
        ) -> None:
            self.location = location
            self.timeout = timeout
            self.max_event_bytes = max_event_bytes
            self.closed = False
            self.receive_calls = 0
            self.instances.append(self)

        def receive(self) -> object:
            self.receive_calls += 1
            pytest.fail("run_tui stub must not start the event stream")

        def close(self) -> None:
            self.closed = True

    def unexpected_selected_radio(
        *args: object,
        **kwargs: object,
    ) -> object:
        del args, kwargs
        pytest.fail("daemon-backed TUI must not open scanner hardware")

    def unexpected_audio_transport(
        *args: object,
        **kwargs: object,
    ) -> object:
        del args, kwargs
        pytest.fail("daemon-backed TUI must not open scanner RTSP audio")

    def fake_run_tui(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(cli, "DaemonApiClient", FakeApiClient)
    monkeypatch.setattr(cli, "DaemonEventClient", FakeEventClient)
    monkeypatch.setattr(cli, "selected_radio", unexpected_selected_radio)
    monkeypatch.setattr(
        cli,
        "NetworkAudioTransport",
        unexpected_audio_transport,
    )
    monkeypatch.setattr("sds200.tui.run_tui", fake_run_tui)

    assert (
        cli.main(
            [
                "tui",
                "--daemon-client",
                "--daemon-socket-path",
                "/tmp/sdsctl-daemon.sock",
                "--daemon-event-socket-path",
                "/tmp/sdsctl-events.sock",
                "--daemon-timeout",
                "1.5",
                "--daemon-max-response-bytes",
                "8192",
                "--daemon-max-event-bytes",
                "4096",
            ],
            environ={},
        )
        == 0
    )

    assert captured["endpoint"] == "udp://192.0.2.25:50536"
    assert captured["model"] == "SDS200"
    assert captured["firmware"] == "Version 1.26.01"
    assert captured["connected"] is True
    assert captured["audio_session"] is None
    assert isinstance(captured["radio"], DaemonTuiRadio)

    snapshot = captured["snapshot"]
    assert isinstance(snapshot, RadioStateSnapshot)
    assert snapshot.channel == "Primary"
    assert snapshot.rssi == -74.0

    api_client = FakeApiClient.instances[0]
    event_client = FakeEventClient.instances[0]
    assert api_client.location.path == Path("/tmp/sdsctl-daemon.sock")
    assert api_client.timeout == 1.5
    assert api_client.max_response_bytes == 8192
    assert api_client.hello_calls == 1
    assert api_client.snapshot_calls == 1
    assert api_client.closed is True

    assert event_client.location.path == Path("/tmp/sdsctl-events.sock")
    assert event_client.timeout == 1.5
    assert event_client.max_event_bytes == 4096
    assert event_client.receive_calls == 0
    assert event_client.closed is True


@pytest.mark.parametrize(
    "arguments",
    [
        ["--host", "192.0.2.25", "tui", "--daemon-client"],
        ["--port", "/dev/ttyACM0", "tui", "--daemon-client"],
        ["--replay", str(FIXTURE), "tui", "--daemon-client"],
    ],
)
def test_tui_daemon_client_rejects_scanner_selectors(
    arguments: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(arguments, environ={}) == 2
    assert "not used with the daemon-backed TUI" in capsys.readouterr().err


def test_tui_daemon_options_require_explicit_mode(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        cli.main(
            [
                "tui",
                "--daemon-socket-path",
                "/tmp/sdsctl-daemon.sock",
            ],
            environ={},
        )
        == 2
    )
    assert "require --daemon-client" in capsys.readouterr().err


def test_tui_daemon_client_rejects_direct_audio_options(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        cli.main(
            [
                "tui",
                "--daemon-client",
                "--audio-playback",
            ],
            environ={},
        )
        == 2
    )
    assert "Daemon-backed TUI audio is not enabled" in capsys.readouterr().err
