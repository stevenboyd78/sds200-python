from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from sds200.daemon_api import (
    DAEMON_API_CONTROL_OPERATIONS,
    DAEMON_API_DEFAULT_CONTROL_TIMEOUT,
    DAEMON_API_MAX_CONTROL_TIMEOUT,
    DAEMON_API_PROTOCOL,
    DAEMON_API_READ_ONLY_OPERATIONS,
    DAEMON_API_RECORDING_OPERATIONS,
    DAEMON_API_SUPPORTED_VERSIONS,
    DAEMON_API_VERSION,
    DaemonApiErrorCode,
    DaemonApiOperation,
    DaemonReadOnlyApi,
)
from sds200.exceptions import (
    CommandRejectedError,
    CommandTimeoutError,
    DaemonControlBusyError,
    DaemonControlUnavailableError,
    ProtocolError,
    ScannerConnectionError,
    UnsupportedScannerFeatureError,
)


@dataclass(frozen=True)
class FakeControlResult:
    sequence: int
    operation: str

    def as_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "operation": self.operation,
            "started_at": "2026-08-05T09:00:00+00:00",
            "completed_at": "2026-08-05T09:00:01+00:00",
            "snapshot": {
                "state": "running",
                "scanner_connected": True,
            },
        }


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
        }


class FakeControlRuntime:
    def __init__(self) -> None:
        self.calls: list[
            tuple[str, tuple[object, ...], dict[str, object]]
        ] = []
        self.error: Exception | None = None
        self.sequence = 0

    def snapshot(self) -> FakeSnapshot:
        return FakeSnapshot()

    def hold(
        self,
        target: str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        timeout: float = DAEMON_API_DEFAULT_CONTROL_TIMEOUT,
    ) -> FakeControlResult:
        return self._control(
            "scanner.hold",
            target,
            first,
            second,
            timeout=timeout,
        )

    def next(
        self,
        target: str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        count: int = 1,
        timeout: float = DAEMON_API_DEFAULT_CONTROL_TIMEOUT,
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
        timeout: float = DAEMON_API_DEFAULT_CONTROL_TIMEOUT,
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
        timeout: float = DAEMON_API_DEFAULT_CONTROL_TIMEOUT,
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


def request_payload(
    operation: str,
    *,
    request_id: str = "control-1",
    params: object = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "protocol": DAEMON_API_PROTOCOL,
        "version": DAEMON_API_VERSION,
        "request_id": request_id,
        "operation": operation,
    }
    if params is not None:
        payload["params"] = params
    return payload


def test_capabilities_preserve_reads_and_advertise_controls() -> None:
    response = DaemonReadOnlyApi(FakeControlRuntime()).handle_payload(
        request_payload(DaemonApiOperation.HELLO.value)
    )

    assert response.error is None
    assert response.result == {
        "protocol": DAEMON_API_PROTOCOL,
        "supported_versions": list(DAEMON_API_SUPPORTED_VERSIONS),
        "operations": [
            operation.value
            for operation in DaemonApiOperation
            if operation not in DAEMON_API_RECORDING_OPERATIONS
        ],
        "read_only": False,
        "read_only_operations": [
            operation.value
            for operation in DAEMON_API_READ_ONLY_OPERATIONS
            if operation not in DAEMON_API_RECORDING_OPERATIONS
        ],
        "control_operations": [
            operation.value
            for operation in DAEMON_API_CONTROL_OPERATIONS
        ],
        "max_control_timeout": DAEMON_API_MAX_CONTROL_TIMEOUT,
        "selected_version": DAEMON_API_VERSION,
    }


def test_hold_uses_strict_parameters_and_authoritative_result() -> None:
    runtime = FakeControlRuntime()
    response = DaemonReadOnlyApi(runtime).handle_payload(
        request_payload(
            DaemonApiOperation.SCANNER_HOLD.value,
            params={
                "target": "sys",
                "first": 42,
                "second": None,
                "timeout": 1.5,
            },
        )
    )

    assert response.error is None
    assert response.result is not None
    assert response.result["sequence"] == 1
    assert response.result["operation"] == "scanner.hold"
    assert runtime.calls == [
        (
            "scanner.hold",
            ("SYS", 42, None),
            {"timeout": 1.5},
        )
    ]
    encoded = json.loads(response.to_json_line())
    assert encoded["result"]["snapshot"]["state"] == "running"


@pytest.mark.parametrize(
    ("operation", "params", "expected"),
    [
        (
            DaemonApiOperation.SCANNER_NEXT,
            {
                "target": "TGID",
                "first": 99,
                "count": 3,
                "timeout": 1.75,
            },
            (
                "scanner.next",
                ("TGID", 99, None),
                {"count": 3, "timeout": 1.75},
            ),
        ),
        (
            DaemonApiOperation.SCANNER_PREVIOUS,
            {
                "target": "DEPT",
                "first": 7,
                "second": 42,
                "count": 2,
                "timeout": 1.75,
            },
            (
                "scanner.previous",
                ("DEPT", 7, 42),
                {"count": 2, "timeout": 1.75},
            ),
        ),
    ],
)
def test_next_and_previous_dispatch_count_and_timeout(
    operation: DaemonApiOperation,
    params: dict[str, object],
    expected: tuple[str, tuple[object, ...], dict[str, object]],
) -> None:
    runtime = FakeControlRuntime()
    response = DaemonReadOnlyApi(runtime).handle_payload(
        request_payload(operation.value, params=params)
    )

    assert response.error is None
    assert runtime.calls == [expected]


def test_reconnect_accepts_only_a_bounded_timeout() -> None:
    runtime = FakeControlRuntime()
    api = DaemonReadOnlyApi(runtime)

    success = api.handle_payload(
        request_payload(
            DaemonApiOperation.SCANNER_RECONNECT.value,
            params={"timeout": 1.5},
        )
    )
    rejected = api.handle_payload(
        request_payload(
            DaemonApiOperation.SCANNER_RECONNECT.value,
            request_id="control-2",
            params={"unexpected": True},
        )
    )

    assert success.error is None
    assert runtime.calls == [
        ("scanner.reconnect", (), {"timeout": 1.5})
    ]
    assert rejected.error is not None
    assert rejected.error.code is DaemonApiErrorCode.INVALID_PARAMETERS


@pytest.mark.parametrize(
    ("operation", "params"),
    [
        (DaemonApiOperation.SCANNER_HOLD, {}),
        (DaemonApiOperation.SCANNER_HOLD, {"target": ""}),
        (DaemonApiOperation.SCANNER_HOLD, {"target": "INVALID"}),
        (DaemonApiOperation.SCANNER_HOLD, {"target": "SYS", "extra": 1}),
        (DaemonApiOperation.SCANNER_HOLD, {"target": "SYS", "first": True}),
        (DaemonApiOperation.SCANNER_HOLD, {"target": "SYS", "first": "1,2"}),
        (DaemonApiOperation.SCANNER_NEXT, {"target": "TGID", "count": True}),
        (DaemonApiOperation.SCANNER_NEXT, {"target": "TGID", "count": 0}),
        (DaemonApiOperation.SCANNER_NEXT, {"target": "TGID", "count": 9}),
        (DaemonApiOperation.SCANNER_NEXT, {"target": "TGID", "timeout": 0}),
        (
            DaemonApiOperation.SCANNER_NEXT,
            {
                "target": "TGID",
                "timeout": DAEMON_API_MAX_CONTROL_TIMEOUT + 0.1,
            },
        ),
        (
            DaemonApiOperation.SCANNER_NEXT,
            {"target": "TGID", "timeout": float("inf")},
        ),
        (
            DaemonApiOperation.SCANNER_RECONNECT,
            {"timeout": 0},
        ),
        (
            DaemonApiOperation.SCANNER_RECONNECT,
            {"unexpected": True},
        ),
    ],
)
def test_invalid_parameters_are_rejected_before_runtime(
    operation: DaemonApiOperation,
    params: dict[str, object],
) -> None:
    runtime = FakeControlRuntime()
    response = DaemonReadOnlyApi(runtime).handle_payload(
        request_payload(operation.value, params=params)
    )

    assert response.error is not None
    assert response.error.code is DaemonApiErrorCode.INVALID_PARAMETERS
    assert runtime.calls == []


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (
            DaemonControlBusyError("secret busy detail"),
            DaemonApiErrorCode.CONTROL_BUSY,
        ),
        (
            DaemonControlUnavailableError("secret unavailable detail"),
            DaemonApiErrorCode.CONTROL_UNAVAILABLE,
        ),
        (
            UnsupportedScannerFeatureError("secret unsupported detail"),
            DaemonApiErrorCode.UNSUPPORTED_OPERATION,
        ),
        (
            CommandTimeoutError("secret timeout detail"),
            DaemonApiErrorCode.CONTROL_TIMEOUT,
        ),
        (
            CommandRejectedError("secret rejection detail"),
            DaemonApiErrorCode.CONTROL_REJECTED,
        ),
        (
            ScannerConnectionError("secret connection detail"),
            DaemonApiErrorCode.CONTROL_FAILED,
        ),
        (
            ProtocolError("secret protocol detail"),
            DaemonApiErrorCode.CONTROL_FAILED,
        ),
    ],
)
def test_failures_are_correlated_classified_and_redacted(
    error: Exception,
    code: DaemonApiErrorCode,
) -> None:
    runtime = FakeControlRuntime()
    runtime.error = error

    response = DaemonReadOnlyApi(runtime).handle_payload(
        request_payload(
            DaemonApiOperation.SCANNER_HOLD.value,
            params={"target": "SYS", "first": 42},
        )
    )

    assert response.request_id == "control-1"
    assert response.error is not None
    assert response.error.code is code
    assert "secret" not in response.error.message
    assert "secret" not in response.to_json_line().decode("utf-8")


def test_unexpected_failure_remains_redacted_internal_error() -> None:
    runtime = FakeControlRuntime()
    runtime.error = RuntimeError("secret implementation detail")

    response = DaemonReadOnlyApi(runtime).handle_payload(
        request_payload(
            DaemonApiOperation.SCANNER_HOLD.value,
            params={"target": "SYS", "first": 42},
        )
    )

    assert response.error is not None
    assert response.error.code is DaemonApiErrorCode.INTERNAL_ERROR
    assert "secret" not in response.to_json_line().decode("utf-8")
