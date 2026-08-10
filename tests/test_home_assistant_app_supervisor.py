from __future__ import annotations

import json
import signal
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Self

import pytest

from sds200.exceptions import SDS200Error
from sds200.home_assistant_app import (
    HOME_ASSISTANT_APP_MQTT_PASSWORD_VARIABLE,
    HOME_ASSISTANT_SUPERVISOR_TOKEN_VARIABLE,
    HomeAssistantAppOptions,
    HomeAssistantMqttService,
)
from sds200.home_assistant_app_runtime import HomeAssistantAppRuntimePaths
from sds200.home_assistant_app_supervisor import (
    HomeAssistantAppLaunchPlan,
    HomeAssistantAppSupervisor,
    migrate_home_assistant_app_recordings,
    prepare_home_assistant_app_launch_plan,
)


class FakeSignals:
    def __init__(self, *, stop_after_waits: int | None = None) -> None:
        self.stop_after_waits = stop_after_waits
        self.wait_calls = 0
        self.enter_calls = 0
        self.exit_calls = 0
        self._stop_requested = False
        self._last_signal: int | None = None

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested

    @property
    def last_signal(self) -> int | None:
        return self._last_signal

    def request_stop(self, signum: int = int(signal.SIGTERM)) -> None:
        self._last_signal = signum
        self._stop_requested = True

    def wait(self, timeout: float | None = None) -> bool:
        del timeout
        self.wait_calls += 1
        if (
            self.stop_after_waits is not None
            and self.wait_calls >= self.stop_after_waits
        ):
            self.request_stop()
            return True
        return False

    def __enter__(self) -> Self:
        self.enter_calls += 1
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception_type, exception, traceback
        self.exit_calls += 1


class FakeProcess:
    def __init__(
        self,
        name: str,
        events: list[str],
        *,
        returncode: int | None = None,
        timeout_on_wait: bool = False,
    ) -> None:
        self.name = name
        self.events = events
        self.returncode = returncode
        self.timeout_on_wait = timeout_on_wait
        self.terminate_calls = 0
        self.kill_calls = 0
        self.wait_calls: list[float | None] = []

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminate_calls += 1
        self.events.append(f"{self.name}:terminate")

    def kill(self) -> None:
        self.kill_calls += 1
        self.events.append(f"{self.name}:kill")
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls.append(timeout)
        self.events.append(f"{self.name}:wait")
        if self.timeout_on_wait and self.kill_calls == 0:
            raise subprocess.TimeoutExpired(self.name, timeout)
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


def runtime_paths(tmp_path: Path) -> HomeAssistantAppRuntimePaths:
    runtime = tmp_path / "run" / "sdsctl"
    return HomeAssistantAppRuntimePaths(
        runtime_directory=runtime,
        mqtt_configuration=runtime / "daemon-mqtt.toml",
        daemon_socket=runtime / "daemon.sock",
        event_socket=runtime / "events.sock",
        pcmu_socket=runtime / "pcmu.sock",
        recording_file_socket=runtime / "recordings.sock",
        recording_directory=tmp_path / "data" / "recordings",
    )


def launch_plan(tmp_path: Path) -> HomeAssistantAppLaunchPlan:
    paths = runtime_paths(tmp_path)
    return HomeAssistantAppLaunchPlan(
        options=HomeAssistantAppOptions(scanner_host="192.0.2.25"),
        mqtt_service=HomeAssistantMqttService(
            host="mqtt",
            port=1883,
            ssl=False,
            username="user",
            password="secret",
            protocol="3.1.1",
        ),
        paths=paths,
        daemon_command=("daemon-child",),
        web_command=("web-child",),
        daemon_environment={"DAEMON": "1"},
        web_environment={"WEB": "1"},
    )


def test_launch_plan_repr_does_not_expose_child_environments(
    tmp_path: Path,
) -> None:
    rendered = repr(launch_plan(tmp_path))

    assert "secret" not in rendered
    assert "daemon_environment" not in rendered
    assert "web_environment" not in rendered


def test_migrate_home_assistant_app_recordings_preserves_library(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "data" / "recordings"
    destination = tmp_path / "media" / "sdsctl" / "recordings"
    nested = legacy / "system" / "department"
    nested.mkdir(parents=True)

    recording = nested / "test.wav"
    metadata = nested / "test.wav.json"
    recording.write_bytes(b"RIFF-test-recording")
    metadata.write_text(
        '{"recording": "test.wav"}\n',
        encoding="utf-8",
    )

    migrated = migrate_home_assistant_app_recordings(
        destination,
        legacy_directory=legacy,
    )

    assert migrated == 2
    assert (
        destination / "system" / "department" / "test.wav"
    ).read_bytes() == b"RIFF-test-recording"
    assert (
        destination / "system" / "department" / "test.wav.json"
    ).read_text(encoding="utf-8") == '{"recording": "test.wav"}\n'
    assert not legacy.exists()

    assert (
        migrate_home_assistant_app_recordings(
            destination,
            legacy_directory=legacy,
        )
        == 0
    )


def test_migrate_home_assistant_app_recordings_rejects_conflicts_without_changes(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "data" / "recordings"
    destination = tmp_path / "media" / "sdsctl" / "recordings"
    legacy.mkdir(parents=True)
    destination.mkdir(parents=True)

    migratable = legacy / "a.wav"
    conflicting_source = legacy / "b.wav"
    conflicting_destination = destination / "b.wav"

    migratable.write_bytes(b"migratable")
    conflicting_source.write_bytes(b"legacy")
    conflicting_destination.write_bytes(b"existing")

    with pytest.raises(
        SDS200Error,
        match="conflicting destination file",
    ):
        migrate_home_assistant_app_recordings(
            destination,
            legacy_directory=legacy,
        )

    assert migratable.read_bytes() == b"migratable"
    assert conflicting_source.read_bytes() == b"legacy"
    assert conflicting_destination.read_bytes() == b"existing"
    assert not (destination / "a.wav").exists()


@pytest.mark.parametrize(
    "argument",
    [
        "daemon_ready_timeout",
        "daemon_ready_poll_interval",
        "daemon_probe_timeout",
        "web_stop_timeout",
        "daemon_stop_timeout",
        "force_stop_timeout",
        "supervisor_poll_interval",
    ],
)
@pytest.mark.parametrize("value", [0.0, float("nan"), float("inf")])
def test_supervisor_requires_finite_positive_timeouts(
    tmp_path: Path,
    argument: str,
    value: float,
) -> None:
    with pytest.raises(ValueError, match="finite and greater than zero"):
        HomeAssistantAppSupervisor(
            launch_plan(tmp_path),
            **{argument: value},
        )


def test_prepare_launch_plan_generates_config_and_separates_child_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    options_path = tmp_path / "options.json"
    options_path.write_text(
        json.dumps(
            {
                "scanner_host": "192.0.2.25",
                "mqtt_topic_prefix": "scanner/main",
            }
        ),
        encoding="utf-8",
    )
    paths = runtime_paths(tmp_path)
    paths.recording_directory.mkdir(parents=True)
    paths.recording_directory.chmod(0o711)

    service = HomeAssistantMqttService(
        host="mqtt",
        port=1883,
        ssl=False,
        username="user",
        password="secret",
        protocol="3.1.1",
    )
    calls: list[Mapping[str, str] | None] = []

    def fake_fetch(
        *,
        environ: Mapping[str, str] | None = None,
        **kwargs: object,
    ) -> HomeAssistantMqttService:
        del kwargs
        calls.append(environ)
        return service

    monkeypatch.setattr(
        "sds200.home_assistant_app_supervisor.fetch_home_assistant_mqtt_service",
        fake_fetch,
    )

    plan = prepare_home_assistant_app_launch_plan(
        options_path=options_path,
        paths=paths,
        environ={
            HOME_ASSISTANT_SUPERVISOR_TOKEN_VARIABLE: "supervisor-token",
            HOME_ASSISTANT_APP_MQTT_PASSWORD_VARIABLE: "stale-password",
            "PATH": "/usr/bin",
        },
    )

    assert calls == [
        {
            HOME_ASSISTANT_SUPERVISOR_TOKEN_VARIABLE: "supervisor-token",
            HOME_ASSISTANT_APP_MQTT_PASSWORD_VARIABLE: "stale-password",
            "PATH": "/usr/bin",
        }
    ]
    assert paths.runtime_directory.is_dir()
    assert paths.recording_directory.is_dir()
    assert paths.recording_directory.stat().st_mode & 0o777 == 0o711
    assert paths.mqtt_configuration.is_file()
    rendered = paths.mqtt_configuration.read_text(encoding="utf-8")
    assert "secret" not in rendered
    assert "commands_enabled = false" in rendered
    assert "[home_assistant]\nenabled = true" in rendered

    assert plan.daemon_environment["PATH"] == "/usr/bin"
    assert (
        plan.daemon_environment[HOME_ASSISTANT_APP_MQTT_PASSWORD_VARIABLE]
        == "secret"
    )
    assert HOME_ASSISTANT_SUPERVISOR_TOKEN_VARIABLE not in plan.daemon_environment

    assert plan.web_environment == {"PATH": "/usr/bin"}
    assert HOME_ASSISTANT_SUPERVISOR_TOKEN_VARIABLE not in plan.web_environment
    assert HOME_ASSISTANT_APP_MQTT_PASSWORD_VARIABLE not in plan.web_environment


def test_supervisor_starts_web_only_after_daemon_readiness(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    daemon = FakeProcess("daemon", events)
    web = FakeProcess("web", events)
    created: list[tuple[tuple[str, ...], dict[str, str]]] = []
    processes = iter((daemon, web))
    signals = FakeSignals(stop_after_waits=1)
    probes = 0

    def factory(
        command: Sequence[str],
        environment: Mapping[str, str],
    ) -> FakeProcess:
        created.append((tuple(command), dict(environment)))
        child = next(processes)
        events.append(f"start:{child.name}")
        return child

    def ready_probe(path: Path, timeout: float) -> bool:
        nonlocal probes
        probes += 1
        assert path == launch_plan(tmp_path).paths.daemon_socket
        assert timeout == 0.5
        events.append("daemon:ready")
        return True

    plan = launch_plan(tmp_path)
    supervisor = HomeAssistantAppSupervisor(
        plan,
        process_factory=factory,
        daemon_ready_probe=ready_probe,
        signals=signals,
    )

    assert supervisor.run() == 0

    assert probes == 1
    assert created == [
        (("daemon-child",), {"DAEMON": "1"}),
        (("web-child",), {"WEB": "1"}),
    ]
    assert events[:3] == [
        "start:daemon",
        "daemon:ready",
        "start:web",
    ]
    assert events[-4:] == [
        "web:terminate",
        "web:wait",
        "daemon:terminate",
        "daemon:wait",
    ]


def test_supervisor_does_not_start_web_when_stop_arrives_during_readiness(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    daemon = FakeProcess("daemon", events)
    signals = FakeSignals(stop_after_waits=1)
    created = 0

    def factory(
        command: Sequence[str],
        environment: Mapping[str, str],
    ) -> FakeProcess:
        nonlocal created
        del command, environment
        created += 1
        return daemon

    supervisor = HomeAssistantAppSupervisor(
        launch_plan(tmp_path),
        process_factory=factory,
        daemon_ready_probe=lambda path, timeout: False,
        signals=signals,
    )

    assert supervisor.run() == 0
    assert created == 1
    assert daemon.terminate_calls == 1


def test_supervisor_rejects_daemon_exit_before_readiness(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    daemon = FakeProcess("daemon", events, returncode=7)

    supervisor = HomeAssistantAppSupervisor(
        launch_plan(tmp_path),
        process_factory=lambda command, environment: daemon,
        daemon_ready_probe=lambda path, timeout: False,
        signals=FakeSignals(),
    )

    with pytest.raises(
        Exception,
        match="daemon exited before readiness with status 7",
    ):
        supervisor.run()

    assert daemon.terminate_calls == 0


@pytest.mark.parametrize(
    ("failed_child", "message"),
    [
        ("daemon", "daemon exited unexpectedly with status 3"),
        ("web", "web process exited unexpectedly with status 4"),
    ],
)
def test_supervisor_stops_sibling_when_child_exits(
    tmp_path: Path,
    failed_child: str,
    message: str,
) -> None:
    events: list[str] = []
    daemon = FakeProcess("daemon", events)
    web = FakeProcess(
        "web",
        events,
        returncode=4 if failed_child == "web" else None,
    )
    processes = iter((daemon, web))

    def factory(
        command: Sequence[str],
        environment: Mapping[str, str],
    ) -> FakeProcess:
        del command, environment
        child = next(processes)
        if child is web and failed_child == "daemon":
            daemon.returncode = 3
        return child

    supervisor = HomeAssistantAppSupervisor(
        launch_plan(tmp_path),
        process_factory=factory,
        daemon_ready_probe=lambda path, timeout: True,
        signals=FakeSignals(),
    )

    with pytest.raises(Exception, match=message):
        supervisor.run()

    if failed_child == "daemon":
        assert web.terminate_calls == 1
        assert daemon.terminate_calls == 0
    else:
        assert web.terminate_calls == 0
        assert daemon.terminate_calls == 1


def test_supervisor_forces_child_after_bounded_graceful_stop(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    daemon = FakeProcess(
        "daemon",
        events,
        timeout_on_wait=True,
    )
    web = FakeProcess("web", events)
    processes = iter((daemon, web))

    supervisor = HomeAssistantAppSupervisor(
        launch_plan(tmp_path),
        process_factory=lambda command, environment: next(processes),
        daemon_ready_probe=lambda path, timeout: True,
        signals=FakeSignals(stop_after_waits=1),
        web_stop_timeout=1.0,
        daemon_stop_timeout=2.0,
        force_stop_timeout=0.5,
    )

    assert supervisor.run() == 0
    assert web.kill_calls == 0
    assert web.wait_calls == [1.0]
    assert daemon.terminate_calls == 1
    assert daemon.kill_calls == 1
    assert daemon.wait_calls == [2.0, 0.5]
    assert events.index("web:terminate") < events.index("daemon:terminate")


def test_supervisor_readiness_timeout_is_bounded(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    daemon = FakeProcess("daemon", events)
    times = iter((0.0, 0.0, 1.1))

    supervisor = HomeAssistantAppSupervisor(
        launch_plan(tmp_path),
        process_factory=lambda command, environment: daemon,
        daemon_ready_probe=lambda path, timeout: False,
        signals=FakeSignals(),
        daemon_ready_timeout=1.0,
        daemon_ready_poll_interval=0.1,
        monotonic=lambda: next(times),
    )

    with pytest.raises(
        Exception,
        match="did not become ready before the 1-second deadline",
    ):
        supervisor.run()

    assert daemon.terminate_calls == 1


def test_supervisor_uses_distinct_default_child_stop_budgets(
    tmp_path: Path,
) -> None:
    supervisor = HomeAssistantAppSupervisor(
        launch_plan(tmp_path),
        process_factory=lambda command, environment: FakeProcess(
            "unused",
            [],
        ),
        daemon_ready_probe=lambda path, timeout: True,
        signals=FakeSignals(stop_after_waits=1),
    )

    assert supervisor.web_stop_timeout == 5.0
    assert supervisor.daemon_stop_timeout == 30.0
    assert supervisor.force_stop_timeout == 5.0
