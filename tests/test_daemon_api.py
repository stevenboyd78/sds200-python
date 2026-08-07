from __future__ import annotations

import json
from types import MappingProxyType

import pytest

from sds200.daemon_api import (
    DAEMON_API_CONTROL_OPERATIONS,
    DAEMON_API_MAX_CONTROL_TIMEOUT,
    DAEMON_API_MAX_REQUEST_ID_LENGTH,
    DAEMON_API_PROTOCOL,
    DAEMON_API_READ_ONLY_OPERATIONS,
    DAEMON_API_RECORDING_OPERATIONS,
    DAEMON_API_SUPPORTED_VERSIONS,
    DAEMON_API_VERSION,
    DaemonApiError,
    DaemonApiErrorCode,
    DaemonApiOperation,
    DaemonApiRequest,
    DaemonApiResponse,
    DaemonReadOnlyApi,
)


class FakeSnapshot:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def as_dict(self) -> dict[str, object]:
        return dict(self.payload)


class FakeRuntime:
    def __init__(
        self,
        payload: dict[str, object],
        *,
        error: Exception | None = None,
    ) -> None:
        self.payload = payload
        self.error = error
        self.snapshot_calls = 0

    def snapshot(self) -> FakeSnapshot:
        self.snapshot_calls += 1
        if self.error is not None:
            raise self.error
        return FakeSnapshot(self.payload)


@pytest.fixture
def snapshot_payload() -> dict[str, object]:
    return {
        "state": "running",
        "scanner_endpoint": "udp://192.0.2.25:50536",
        "scanner_model": "SDS200",
        "scanner_firmware": "Version 1.26.01",
        "scanner_connected": True,
        "psi_interval_ms": 500,
        "psi_active": True,
        "radio_state": {
            "system": "Metro",
            "department": "Dispatch",
            "channel": "Primary",
        },
        "audio": {
            "endpoint": "rtsp://192.0.2.25/au:scanner.au",
            "running": True,
        },
        "router": {
            "name": "daemon-pcm",
            "running": True,
            "subscribers": [],
        },
    }


def request_payload(
    operation: str,
    *,
    request_id: str = "request-1",
    protocol: str = DAEMON_API_PROTOCOL,
    version: int = DAEMON_API_VERSION,
    params: object = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "protocol": protocol,
        "version": version,
        "request_id": request_id,
        "operation": operation,
    }
    if params is not None:
        payload["params"] = params
    return payload


def test_request_is_strict_and_copies_parameters() -> None:
    params = {"example": 1}
    request = DaemonApiRequest.from_payload(
        request_payload("ping", params=params)
    )
    params["example"] = 2

    assert request.as_dict() == {
        "protocol": DAEMON_API_PROTOCOL,
        "version": DAEMON_API_VERSION,
        "request_id": "request-1",
        "operation": "ping",
        "params": {"example": 1},
    }
    assert isinstance(request.params, MappingProxyType)

    with pytest.raises(TypeError):
        request.params["other"] = 2  # type: ignore[index]


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        request_payload("ping") | {"unexpected": True},
        request_payload("ping") | {"version": True},
        request_payload("ping") | {"request_id": ""},
        request_payload("ping") | {"request_id": "bad\nidentifier"},
        request_payload("ping") | {"operation": ""},
        request_payload("ping") | {"params": []},
    ],
)
def test_invalid_request_envelopes_return_structured_errors(
    payload: object,
    snapshot_payload: dict[str, object],
) -> None:
    response = DaemonReadOnlyApi(
        FakeRuntime(snapshot_payload)
    ).handle_payload(payload)

    assert response.error is not None
    assert response.error.code is DaemonApiErrorCode.INVALID_REQUEST
    assert response.as_dict()["ok"] is False


def test_request_identifier_length_is_bounded(
    snapshot_payload: dict[str, object],
) -> None:
    response = DaemonReadOnlyApi(FakeRuntime(snapshot_payload)).handle_payload(
        request_payload(
            "ping",
            request_id="x" * (DAEMON_API_MAX_REQUEST_ID_LENGTH + 1),
        )
    )

    assert response.request_id is None
    assert response.error is not None
    assert response.error.code is DaemonApiErrorCode.INVALID_REQUEST


def test_hello_negotiates_version_and_lists_capabilities(
    snapshot_payload: dict[str, object],
) -> None:
    runtime = FakeRuntime(snapshot_payload)
    response = DaemonReadOnlyApi(runtime).handle_payload(
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
    assert runtime.snapshot_calls == 0


def test_capabilities_and_ping_do_not_read_runtime_snapshot(
    snapshot_payload: dict[str, object],
) -> None:
    runtime = FakeRuntime(snapshot_payload)
    api = DaemonReadOnlyApi(runtime)

    capabilities = api.handle_payload(
        request_payload(DaemonApiOperation.CAPABILITIES.value)
    )
    ping = api.handle_payload(
        request_payload(
            DaemonApiOperation.PING.value,
            request_id="request-2",
        )
    )

    assert capabilities.result is not None
    assert capabilities.result["read_only"] is False
    assert capabilities.result["read_only_operations"] == [
        operation.value
        for operation in DAEMON_API_READ_ONLY_OPERATIONS
        if operation not in DAEMON_API_RECORDING_OPERATIONS
    ]
    assert ping.result == {"pong": True}
    assert runtime.snapshot_calls == 0


def test_unsupported_protocol_and_version_are_correlated(
    snapshot_payload: dict[str, object],
) -> None:
    api = DaemonReadOnlyApi(FakeRuntime(snapshot_payload))

    protocol = api.handle_payload(
        request_payload("ping", protocol="other.protocol")
    )
    version = api.handle_payload(
        request_payload("ping", version=DAEMON_API_VERSION + 1)
    )

    assert protocol.request_id == "request-1"
    assert protocol.error is not None
    assert protocol.error.code is DaemonApiErrorCode.UNSUPPORTED_PROTOCOL
    assert version.request_id == "request-1"
    assert version.error is not None
    assert version.error.code is DaemonApiErrorCode.UNSUPPORTED_VERSION


def test_unknown_operation_and_parameters_are_rejected(
    snapshot_payload: dict[str, object],
) -> None:
    api = DaemonReadOnlyApi(FakeRuntime(snapshot_payload))

    unknown = api.handle_payload(request_payload("scanner.delete"))
    parameters = api.handle_payload(
        request_payload("ping", params={"unexpected": True})
    )

    assert unknown.error is not None
    assert unknown.error.code is DaemonApiErrorCode.UNKNOWN_OPERATION
    assert parameters.error is not None
    assert parameters.error.code is DaemonApiErrorCode.INVALID_PARAMETERS


def test_runtime_snapshot_returns_authoritative_serializable_payload(
    snapshot_payload: dict[str, object],
) -> None:
    runtime = FakeRuntime(snapshot_payload)
    response = DaemonReadOnlyApi(runtime).handle_payload(
        request_payload(DaemonApiOperation.RUNTIME_SNAPSHOT.value)
    )

    assert response.result == snapshot_payload
    assert runtime.snapshot_calls == 1
    assert json.loads(response.to_json_line())["result"]["state"] == "running"


def test_scanner_state_returns_only_scanner_and_psi_fields(
    snapshot_payload: dict[str, object],
) -> None:
    response = DaemonReadOnlyApi(
        FakeRuntime(snapshot_payload)
    ).handle_payload(
        request_payload(DaemonApiOperation.SCANNER_STATE.value)
    )

    assert response.result == {
        "scanner_endpoint": "udp://192.0.2.25:50536",
        "scanner_model": "SDS200",
        "scanner_firmware": "Version 1.26.01",
        "scanner_connected": True,
        "psi_interval_ms": 500,
        "psi_active": True,
        "radio_state": snapshot_payload["radio_state"],
    }


def test_audio_health_returns_audio_and_router_fields(
    snapshot_payload: dict[str, object],
) -> None:
    response = DaemonReadOnlyApi(
        FakeRuntime(snapshot_payload)
    ).handle_payload(
        request_payload(DaemonApiOperation.AUDIO_HEALTH.value)
    )

    assert response.result == {
        "audio": snapshot_payload["audio"],
        "router": snapshot_payload["router"],
    }


def test_runtime_failure_is_redacted(
    snapshot_payload: dict[str, object],
) -> None:
    response = DaemonReadOnlyApi(
        FakeRuntime(
            snapshot_payload,
            error=RuntimeError("secret runtime detail"),
        )
    ).handle_payload(
        request_payload(DaemonApiOperation.RUNTIME_SNAPSHOT.value)
    )

    assert response.error is not None
    assert response.error.code is DaemonApiErrorCode.INTERNAL_ERROR
    assert "secret" not in response.error.message
    assert "secret" not in response.to_json_line().decode("utf-8")


@pytest.mark.parametrize(
    "data",
    [
        b"not-json\n",
        b"\xff\n",
    ],
)
def test_invalid_json_lines_return_uncorrelated_errors(
    data: bytes,
    snapshot_payload: dict[str, object],
) -> None:
    encoded = DaemonReadOnlyApi(
        FakeRuntime(snapshot_payload)
    ).handle_json_line(data)
    payload = json.loads(encoded)

    assert encoded.endswith(b"\n")
    assert payload["request_id"] is None
    assert payload["ok"] is False
    assert payload["error"]["code"] == "invalid_request"


def test_response_requires_exactly_one_result_or_error() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        DaemonApiResponse(request_id="request-1")

    with pytest.raises(ValueError, match="exactly one"):
        DaemonApiResponse(
            request_id="request-1",
            result={},
            error=DaemonApiError(
                DaemonApiErrorCode.INTERNAL_ERROR,
                "Request failed.",
            ),
        )
