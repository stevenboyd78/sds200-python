from __future__ import annotations

from pathlib import Path

import pytest

from sds200 import (
    DaemonApiClient,
    DaemonApiServer,
    DaemonRequestError,
    DaemonSocketListener,
    DaemonSocketLocation,
    DaemonSocketSource,
)
from sds200.daemon_api import DaemonReadOnlyApi
from sds200.daemon_recording import DaemonRecordingBusyError


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
            "started_at": "2026-08-07T20:00:00+00:00",
            "stopped_at": None,
            "state_changed_at": "2026-08-07T20:00:00+00:00",
            "transition_sequence": 1,
            "last_failure_at": None,
            "last_error": None,
        }


class FakeRuntime:
    def snapshot(self) -> FakeSnapshot:
        return FakeSnapshot()


class FakePayload:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def as_dict(self) -> dict[str, object]:
        return dict(self.payload)


def recording_payload(status: str) -> dict[str, object]:
    return {
        "status": status,
        "active": status == "recording",
        "recording": "sds200-20260807-200000.wav" if status != "idle" else None,
        "metadata": None,
        "started_at": "2026-08-07T20:00:00+00:00" if status != "idle" else None,
        "stopped_at": None,
        "elapsed_seconds": 0.0,
        "packets": 0,
        "samples": 0,
        "audio_duration_seconds": 0.0,
        "reliability": {},
        "sink": {},
        "completed_recordings": 0,
        "closed": False,
        "error": None,
    }


class FakeRecordingManager:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.error: Exception | None = None

    def _result(self, call: str, payload: dict[str, object]) -> FakePayload:
        self.calls.append(call)
        if self.error is not None:
            raise self.error
        return FakePayload(payload)

    def snapshot(self) -> FakePayload:
        return self._result("status", recording_payload("idle"))

    def start_recording(self) -> FakePayload:
        return self._result("start", recording_payload("recording"))

    def stop_recording(self) -> FakePayload:
        return self._result("stop", recording_payload("stopped"))

    def list_recordings(self) -> FakePayload:
        return self._result(
            "list",
            {
                "limit": 50,
                "total_entries": 1,
                "summary": {"managed_units": 1},
                "issues": [],
                "entries": [{"audio": "sds200-20260807-200000.wav"}],
            },
        )


def make_server(
    tmp_path: Path,
) -> tuple[DaemonApiServer, Path, FakeRecordingManager]:
    path = tmp_path / "daemon-recording.sock"
    manager = FakeRecordingManager()
    server = DaemonApiServer(
        DaemonSocketListener(
            DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
        ),
        DaemonReadOnlyApi(
            FakeRuntime(),
            recording_manager=manager,
        ),
    )
    return server, path, manager


def test_client_recording_wrappers_reuse_negotiated_socket(tmp_path: Path) -> None:
    server, path, manager = make_server(tmp_path)
    client = DaemonApiClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    with server, client:
        status = client.recording_status()
        started = client.recording_start()
        stopped = client.recording_stop()
        inventory = client.recordings_list()

    assert status["status"] == "idle"
    assert started["status"] == "recording"
    assert stopped["status"] == "stopped"
    assert inventory["total_entries"] == 1
    assert manager.calls == ["status", "start", "stop", "list"]

    snapshot = server.snapshot()
    assert snapshot.accepted_clients == 1
    assert snapshot.requests == 5
    assert snapshot.responses == 5


def test_client_preserves_recording_error_and_connection(tmp_path: Path) -> None:
    server, path, manager = make_server(tmp_path)
    manager.error = DaemonRecordingBusyError("secret busy detail")
    client = DaemonApiClient(
        DaemonSocketLocation(path, DaemonSocketSource.EXPLICIT)
    )

    with server, client:
        with pytest.raises(DaemonRequestError) as captured:
            client.recording_start()

        assert captured.value.code == "recording_busy"
        assert client.connected is True

        manager.error = None
        assert client.recording_status()["status"] == "idle"
