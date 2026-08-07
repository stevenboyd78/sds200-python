from __future__ import annotations

from dataclasses import dataclass

import pytest

from sds200.daemon_api import (
    DAEMON_API_PROTOCOL,
    DAEMON_API_READ_ONLY_OPERATIONS,
    DAEMON_API_RECORDING_OPERATIONS,
    DAEMON_API_VERSION,
    DaemonApiErrorCode,
    DaemonApiOperation,
    DaemonReadOnlyApi,
)
from sds200.daemon_recording import (
    DaemonRecordingBusyError,
    DaemonRecordingOperationError,
    DaemonRecordingUnavailableError,
)


@dataclass
class FakePayload:
    payload: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        return dict(self.payload)


class FakeRuntime:
    def __init__(self) -> None:
        self.snapshot_calls = 0

    def snapshot(self) -> FakePayload:
        self.snapshot_calls += 1
        return FakePayload({"state": "running"})


def recording_payload(*, status: str = "idle") -> dict[str, object]:
    return {
        "status": status,
        "active": status in {"starting", "recording", "stopping"},
        "recording": None,
        "metadata": None,
        "started_at": None,
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


def inventory_payload() -> dict[str, object]:
    return {
        "limit": 50,
        "total_entries": 0,
        "summary": {"managed_units": 0},
        "issues": [],
        "entries": [],
    }


class FakeRecordingManager:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.error: Exception | None = None

    def _result(self, name: str, payload: dict[str, object]) -> FakePayload:
        self.calls.append(name)
        if self.error is not None:
            raise self.error
        return FakePayload(payload)

    def snapshot(self) -> FakePayload:
        return self._result("status", recording_payload())

    def start_recording(self) -> FakePayload:
        return self._result("start", recording_payload(status="recording"))

    def stop_recording(self) -> FakePayload:
        return self._result("stop", recording_payload(status="stopped"))

    def list_recordings(self) -> FakePayload:
        return self._result("list", inventory_payload())


def request(operation: DaemonApiOperation, *, params: object = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "protocol": DAEMON_API_PROTOCOL,
        "version": DAEMON_API_VERSION,
        "request_id": "recording-1",
        "operation": operation.value,
    }
    if params is not None:
        payload["params"] = params
    return payload


def test_recording_capabilities_are_advertised_only_with_manager() -> None:
    runtime = FakeRuntime()
    without = DaemonReadOnlyApi(runtime).handle_payload(
        request(DaemonApiOperation.HELLO)
    )
    manager = FakeRecordingManager()
    with_manager = DaemonReadOnlyApi(
        runtime,
        recording_manager=manager,
    ).handle_payload(request(DaemonApiOperation.HELLO))

    assert without.result is not None
    without_operations = without.result["operations"]
    assert isinstance(without_operations, list)
    assert not set(operation.value for operation in DAEMON_API_RECORDING_OPERATIONS) & set(
        without_operations
    )

    assert with_manager.result is not None
    assert with_manager.result["operations"] == [
        operation.value for operation in DaemonApiOperation
    ]
    assert with_manager.result["read_only_operations"] == [
        operation.value for operation in DAEMON_API_READ_ONLY_OPERATIONS
    ]


@pytest.mark.parametrize(
    ("operation", "expected_call", "expected_status"),
    [
        (DaemonApiOperation.RECORDING_STATUS, "status", "idle"),
        (DaemonApiOperation.RECORDING_START, "start", "recording"),
        (DaemonApiOperation.RECORDING_STOP, "stop", "stopped"),
    ],
)
def test_recording_snapshot_operations_dispatch_without_runtime_snapshot(
    operation: DaemonApiOperation,
    expected_call: str,
    expected_status: str,
) -> None:
    runtime = FakeRuntime()
    manager = FakeRecordingManager()
    response = DaemonReadOnlyApi(
        runtime,
        recording_manager=manager,
    ).handle_payload(request(operation))

    assert response.error is None
    assert response.result is not None
    assert response.result["status"] == expected_status
    assert manager.calls == [expected_call]
    assert runtime.snapshot_calls == 0


def test_recordings_list_dispatches_manager_inventory() -> None:
    runtime = FakeRuntime()
    manager = FakeRecordingManager()
    response = DaemonReadOnlyApi(
        runtime,
        recording_manager=manager,
    ).handle_payload(request(DaemonApiOperation.RECORDINGS_LIST))

    assert response.result == inventory_payload()
    assert manager.calls == ["list"]
    assert runtime.snapshot_calls == 0


@pytest.mark.parametrize("operation", DAEMON_API_RECORDING_OPERATIONS)
def test_recording_operations_reject_parameters(
    operation: DaemonApiOperation,
) -> None:
    manager = FakeRecordingManager()
    response = DaemonReadOnlyApi(
        FakeRuntime(),
        recording_manager=manager,
    ).handle_payload(request(operation, params={"path": "/tmp/secret.wav"}))

    assert response.error is not None
    assert response.error.code is DaemonApiErrorCode.INVALID_PARAMETERS
    assert manager.calls == []


def test_recording_operation_without_manager_is_unavailable() -> None:
    response = DaemonReadOnlyApi(FakeRuntime()).handle_payload(
        request(DaemonApiOperation.RECORDING_STATUS)
    )

    assert response.error is not None
    assert response.error.code is DaemonApiErrorCode.RECORDING_UNAVAILABLE


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            DaemonRecordingBusyError("secret busy detail"),
            DaemonApiErrorCode.RECORDING_BUSY,
        ),
        (
            DaemonRecordingUnavailableError("secret unavailable detail"),
            DaemonApiErrorCode.RECORDING_UNAVAILABLE,
        ),
        (
            DaemonRecordingOperationError("secret failure detail"),
            DaemonApiErrorCode.RECORDING_FAILED,
        ),
    ],
)
def test_recording_failures_are_classified_and_redacted(
    error: Exception,
    expected: DaemonApiErrorCode,
) -> None:
    manager = FakeRecordingManager()
    manager.error = error
    response = DaemonReadOnlyApi(
        FakeRuntime(),
        recording_manager=manager,
    ).handle_payload(request(DaemonApiOperation.RECORDING_START))

    assert response.request_id == "recording-1"
    assert response.error is not None
    assert response.error.code is expected
    encoded = response.to_json_line().decode("utf-8")
    assert "secret" not in encoded
