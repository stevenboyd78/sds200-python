from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from types import MappingProxyType
from typing import Protocol, cast

from .commands import NAVIGATION_TARGETS
from .daemon_recording import (
    DaemonRecordingBusyError,
    DaemonRecordingOperationError,
    DaemonRecordingUnavailableError,
)
from .exceptions import (
    CommandRejectedError,
    CommandTimeoutError,
    DaemonControlBusyError,
    DaemonControlUnavailableError,
    SDS200Error,
    UnsupportedScannerFeatureError,
    UnsupportedScannerModelError,
)

DAEMON_API_PROTOCOL = "sdsctl.daemon"
DAEMON_API_VERSION = 1
DAEMON_API_SUPPORTED_VERSIONS = (DAEMON_API_VERSION,)
DAEMON_API_MAX_REQUEST_ID_LENGTH = 128
DAEMON_API_DEFAULT_CONTROL_TIMEOUT = 2.0
DAEMON_API_MAX_CONTROL_TIMEOUT = 2.0
DAEMON_API_DEFAULT_HOLD_STATE_TIMEOUT = 4.0
DAEMON_API_MAX_HOLD_STATE_TIMEOUT = 4.0
_HOLD_STATE_SCOPES = ("system", "department", "site", "channel")


class DaemonApiOperation(StrEnum):
    """Versioned operations supported by the local daemon API."""

    HELLO = "hello"
    CAPABILITIES = "daemon.capabilities"
    PING = "ping"
    RUNTIME_SNAPSHOT = "runtime.snapshot"
    SCANNER_STATE = "scanner.state"
    AUDIO_HEALTH = "audio.health"
    RECORDING_STATUS = "recording.status"
    RECORDING_START = "recording.start"
    RECORDING_STOP = "recording.stop"
    RECORDINGS_LIST = "recordings.list"
    SCANNER_HOLD = "scanner.hold"
    SCANNER_HOLD_STATE = "scanner.hold_state"
    SCANNER_NEXT = "scanner.next"
    SCANNER_PREVIOUS = "scanner.previous"
    SCANNER_RECONNECT = "scanner.reconnect"


DAEMON_API_READ_ONLY_OPERATIONS = (
    DaemonApiOperation.HELLO,
    DaemonApiOperation.CAPABILITIES,
    DaemonApiOperation.PING,
    DaemonApiOperation.RUNTIME_SNAPSHOT,
    DaemonApiOperation.SCANNER_STATE,
    DaemonApiOperation.AUDIO_HEALTH,
    DaemonApiOperation.RECORDING_STATUS,
    DaemonApiOperation.RECORDINGS_LIST,
)
DAEMON_API_RECORDING_OPERATIONS = (
    DaemonApiOperation.RECORDING_STATUS,
    DaemonApiOperation.RECORDING_START,
    DaemonApiOperation.RECORDING_STOP,
    DaemonApiOperation.RECORDINGS_LIST,
)
DAEMON_API_CONTROL_OPERATIONS = (
    DaemonApiOperation.SCANNER_HOLD,
    DaemonApiOperation.SCANNER_HOLD_STATE,
    DaemonApiOperation.SCANNER_NEXT,
    DaemonApiOperation.SCANNER_PREVIOUS,
    DaemonApiOperation.SCANNER_RECONNECT,
)


class DaemonApiErrorCode(StrEnum):
    """Stable machine-readable daemon API error classifications."""

    INVALID_REQUEST = "invalid_request"
    UNSUPPORTED_PROTOCOL = "unsupported_protocol"
    UNSUPPORTED_VERSION = "unsupported_version"
    UNKNOWN_OPERATION = "unknown_operation"
    INVALID_PARAMETERS = "invalid_parameters"
    CONTROL_BUSY = "control_busy"
    CONTROL_UNAVAILABLE = "control_unavailable"
    UNSUPPORTED_OPERATION = "unsupported_operation"
    CONTROL_TIMEOUT = "control_timeout"
    CONTROL_REJECTED = "control_rejected"
    CONTROL_FAILED = "control_failed"
    RECORDING_BUSY = "recording_busy"
    RECORDING_UNAVAILABLE = "recording_unavailable"
    RECORDING_FAILED = "recording_failed"
    REQUEST_TOO_LARGE = "request_too_large"
    INTERNAL_ERROR = "internal_error"


class _SnapshotLike(Protocol):
    def as_dict(self) -> dict[str, object]: ...


class _RuntimeLike(Protocol):
    def snapshot(self) -> _SnapshotLike: ...


class _ControlResultLike(Protocol):
    def as_dict(self) -> dict[str, object]: ...


class _RecordingManagerLike(Protocol):
    def snapshot(self) -> _SnapshotLike: ...

    def start_recording(self) -> _SnapshotLike: ...

    def stop_recording(self) -> _SnapshotLike: ...

    def list_recordings(self) -> _SnapshotLike: ...


class _ControlRuntimeLike(_RuntimeLike, Protocol):
    def hold(
        self,
        target: str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        timeout: float = DAEMON_API_DEFAULT_CONTROL_TIMEOUT,
    ) -> _ControlResultLike: ...

    def hold_state(
        self,
        scope: str,
        held: bool,
        *,
        timeout: float = DAEMON_API_DEFAULT_HOLD_STATE_TIMEOUT,
    ) -> _ControlResultLike: ...

    def next(
        self,
        target: str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        count: int = 1,
        timeout: float = DAEMON_API_DEFAULT_CONTROL_TIMEOUT,
    ) -> _ControlResultLike: ...

    def previous(
        self,
        target: str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        count: int = 1,
        timeout: float = DAEMON_API_DEFAULT_CONTROL_TIMEOUT,
    ) -> _ControlResultLike: ...

    def reconnect(
        self,
        *,
        timeout: float = DAEMON_API_DEFAULT_CONTROL_TIMEOUT,
    ) -> _ControlResultLike: ...


class _RequestValidationError(ValueError):
    def __init__(
        self,
        code: DaemonApiErrorCode,
        message: str,
        *,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.request_id = request_id


@dataclass(frozen=True, slots=True)
class DaemonApiRequest:
    """One validated daemon API request envelope."""

    request_id: str
    operation: str
    params: Mapping[str, object] = field(default_factory=dict)
    protocol: str = DAEMON_API_PROTOCOL
    version: int = DAEMON_API_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.protocol, str) or not self.protocol:
            raise TypeError("Daemon API protocol must be a non-empty string.")
        if type(self.version) is not int:
            raise TypeError("Daemon API version must be an integer.")
        _validate_request_id(self.request_id)
        if not isinstance(self.operation, str) or not self.operation.strip():
            raise TypeError("Daemon API operation must be a non-empty string.")
        if not isinstance(self.params, Mapping):
            raise TypeError("Daemon API params must be a mapping.")
        if any(not isinstance(key, str) for key in self.params):
            raise TypeError("Daemon API parameter names must be strings.")

        object.__setattr__(
            self,
            "params",
            MappingProxyType(dict(self.params)),
        )

    @classmethod
    def from_payload(cls, payload: object) -> DaemonApiRequest:
        if not isinstance(payload, Mapping):
            raise _RequestValidationError(
                DaemonApiErrorCode.INVALID_REQUEST,
                "Request must be a JSON object.",
            )
        if any(not isinstance(key, str) for key in payload):
            raise _RequestValidationError(
                DaemonApiErrorCode.INVALID_REQUEST,
                "Request field names must be strings.",
            )

        request_id = _request_id_for_error(payload.get("request_id"))
        allowed = {
            "protocol",
            "version",
            "request_id",
            "operation",
            "params",
        }
        unexpected = sorted(set(payload) - allowed)
        missing = sorted(
            {
                "protocol",
                "version",
                "request_id",
                "operation",
            }
            - set(payload)
        )
        if missing or unexpected:
            raise _RequestValidationError(
                DaemonApiErrorCode.INVALID_REQUEST,
                (
                    "Request fields are invalid; "
                    f"missing={missing!r}, unexpected={unexpected!r}."
                ),
                request_id=request_id,
            )

        protocol = payload["protocol"]
        version = payload["version"]
        raw_request_id = payload["request_id"]
        operation = payload["operation"]
        params = payload.get("params", {})

        if not isinstance(protocol, str) or not protocol:
            raise _RequestValidationError(
                DaemonApiErrorCode.INVALID_REQUEST,
                "Request protocol must be a non-empty string.",
                request_id=request_id,
            )
        if type(version) is not int:
            raise _RequestValidationError(
                DaemonApiErrorCode.INVALID_REQUEST,
                "Request version must be an integer.",
                request_id=request_id,
            )
        try:
            _validate_request_id(raw_request_id)
        except (TypeError, ValueError) as error:
            raise _RequestValidationError(
                DaemonApiErrorCode.INVALID_REQUEST,
                str(error),
            ) from error
        if not isinstance(operation, str) or not operation.strip():
            raise _RequestValidationError(
                DaemonApiErrorCode.INVALID_REQUEST,
                "Request operation must be a non-empty string.",
                request_id=request_id,
            )
        if not isinstance(params, Mapping):
            raise _RequestValidationError(
                DaemonApiErrorCode.INVALID_REQUEST,
                "Request params must be a JSON object.",
                request_id=request_id,
            )
        if any(not isinstance(key, str) for key in params):
            raise _RequestValidationError(
                DaemonApiErrorCode.INVALID_REQUEST,
                "Request parameter names must be strings.",
                request_id=request_id,
            )

        return cls(
            protocol=protocol,
            version=version,
            request_id=raw_request_id,
            operation=operation,
            params=params,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "protocol": self.protocol,
            "version": self.version,
            "request_id": self.request_id,
            "operation": self.operation,
            "params": dict(self.params),
        }


@dataclass(frozen=True, slots=True)
class DaemonApiError:
    """One structured daemon API failure."""

    code: DaemonApiErrorCode
    message: str

    def __post_init__(self) -> None:
        if not isinstance(self.message, str) or not self.message:
            raise ValueError("Daemon API error message must not be empty.")

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code.value,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class DaemonApiResponse:
    """One immutable daemon API response envelope."""

    request_id: str | None
    result: Mapping[str, object] | None = None
    error: DaemonApiError | None = None
    protocol: str = DAEMON_API_PROTOCOL
    version: int = DAEMON_API_VERSION

    def __post_init__(self) -> None:
        if (self.result is None) == (self.error is None):
            raise ValueError(
                "Daemon API response must contain exactly one result or error."
            )
        if self.request_id is not None:
            _validate_request_id(self.request_id)
        if self.result is not None:
            if not isinstance(self.result, Mapping):
                raise TypeError("Daemon API response result must be a mapping.")
            if any(not isinstance(key, str) for key in self.result):
                raise TypeError("Daemon API result field names must be strings.")
            object.__setattr__(
                self,
                "result",
                MappingProxyType(dict(self.result)),
            )

    @classmethod
    def success(
        cls,
        request_id: str,
        result: Mapping[str, object],
    ) -> DaemonApiResponse:
        return cls(request_id=request_id, result=result)

    @classmethod
    def failure(
        cls,
        request_id: str | None,
        code: DaemonApiErrorCode,
        message: str,
    ) -> DaemonApiResponse:
        return cls(
            request_id=request_id,
            error=DaemonApiError(code, message),
        )

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "protocol": self.protocol,
            "version": self.version,
            "request_id": self.request_id,
            "ok": self.error is None,
        }
        if self.result is not None:
            payload["result"] = dict(self.result)
        else:
            assert self.error is not None
            payload["error"] = self.error.as_dict()
        return payload

    def to_json_line(self) -> bytes:
        return (
            json.dumps(
                self.as_dict(),
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")


class DaemonReadOnlyApi:
    """Dispatch versioned requests against one daemon ownership runtime."""

    def __init__(
        self,
        runtime: _RuntimeLike,
        *,
        recording_manager: _RecordingManagerLike | None = None,
        reconnect_available: bool = True,
    ) -> None:
        if type(reconnect_available) is not bool:
            raise TypeError("Daemon reconnect availability must be a boolean.")
        self.runtime = runtime
        self.recording_manager = recording_manager
        self.reconnect_available = reconnect_available

    def _control_operations(self) -> tuple[DaemonApiOperation, ...]:
        return tuple(
            operation
            for operation in DAEMON_API_CONTROL_OPERATIONS
            if (
                self.reconnect_available
                or operation is not DaemonApiOperation.SCANNER_RECONNECT
            )
        )

    @property
    def maximum_request_seconds(self) -> float:
        return max(
            DAEMON_API_MAX_CONTROL_TIMEOUT,
            DAEMON_API_MAX_HOLD_STATE_TIMEOUT,
        )

    def handle_payload(self, payload: object) -> DaemonApiResponse:
        return self._handle_payload(payload, control_only=False)

    def handle_control_payload(
        self,
        payload: object,
    ) -> DaemonApiResponse:
        return self._handle_payload(payload, control_only=True)

    def _handle_payload(
        self,
        payload: object,
        *,
        control_only: bool,
    ) -> DaemonApiResponse:
        try:
            request = DaemonApiRequest.from_payload(payload)
        except _RequestValidationError as error:
            return DaemonApiResponse.failure(
                error.request_id,
                error.code,
                str(error),
            )

        if request.protocol != DAEMON_API_PROTOCOL:
            return DaemonApiResponse.failure(
                request.request_id,
                DaemonApiErrorCode.UNSUPPORTED_PROTOCOL,
                f"Unsupported daemon API protocol: {request.protocol!r}.",
            )
        if request.version not in DAEMON_API_SUPPORTED_VERSIONS:
            return DaemonApiResponse.failure(
                request.request_id,
                DaemonApiErrorCode.UNSUPPORTED_VERSION,
                (
                    f"Unsupported daemon API version: {request.version}; "
                    f"supported={list(DAEMON_API_SUPPORTED_VERSIONS)!r}."
                ),
            )

        try:
            operation = DaemonApiOperation(request.operation)
        except ValueError:
            return DaemonApiResponse.failure(
                request.request_id,
                DaemonApiErrorCode.UNKNOWN_OPERATION,
                f"Unknown daemon API operation: {request.operation!r}.",
            )

        if control_only and operation not in DAEMON_API_CONTROL_OPERATIONS:
            return DaemonApiResponse.failure(
                request.request_id,
                DaemonApiErrorCode.UNKNOWN_OPERATION,
                "Operation is not available on the daemon scanner-control "
                f"interface: {request.operation!r}.",
            )

        control_operations = self._control_operations()
        if (
            operation in DAEMON_API_CONTROL_OPERATIONS
            and operation not in control_operations
        ):
            return DaemonApiResponse.failure(
                request.request_id,
                DaemonApiErrorCode.UNSUPPORTED_OPERATION,
                "The daemon does not advertise this scanner control operation.",
            )

        if operation in control_operations:
            try:
                _validate_control_params(operation, request.params)
            except _ControlParameterError as error:
                return DaemonApiResponse.failure(
                    request.request_id,
                    DaemonApiErrorCode.INVALID_PARAMETERS,
                    str(error),
                )
        elif request.params:
            return DaemonApiResponse.failure(
                request.request_id,
                DaemonApiErrorCode.INVALID_PARAMETERS,
                f"{operation.value} does not accept parameters.",
            )

        try:
            result = self._dispatch(operation, request.params)
        except (_ControlDispatchError, _RecordingDispatchError) as error:
            return DaemonApiResponse.failure(
                request.request_id,
                error.code,
                str(error),
            )
        except Exception:
            return DaemonApiResponse.failure(
                request.request_id,
                DaemonApiErrorCode.INTERNAL_ERROR,
                "The daemon could not complete the request.",
            )

        return DaemonApiResponse.success(request.request_id, result)

    def handle_json_line(self, data: bytes | str) -> bytes:
        try:
            text = data.decode("utf-8") if isinstance(data, bytes) else data
            payload = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError):
            response = DaemonApiResponse.failure(
                None,
                DaemonApiErrorCode.INVALID_REQUEST,
                "Request must contain one valid UTF-8 JSON value.",
            )
        else:
            response = self.handle_payload(payload)
        return response.to_json_line()

    def _dispatch(
        self,
        operation: DaemonApiOperation,
        params: Mapping[str, object],
    ) -> Mapping[str, object]:
        if operation is DaemonApiOperation.HELLO:
            return {
                **self._capabilities(),
                "selected_version": DAEMON_API_VERSION,
            }
        if operation is DaemonApiOperation.CAPABILITIES:
            return self._capabilities()
        if operation is DaemonApiOperation.PING:
            return {"pong": True}
        if operation in DAEMON_API_RECORDING_OPERATIONS:
            return self._dispatch_recording(operation)
        if operation in DAEMON_API_CONTROL_OPERATIONS:
            return self._dispatch_control(operation, params)

        snapshot = self.runtime.snapshot().as_dict()
        if operation is DaemonApiOperation.RUNTIME_SNAPSHOT:
            return snapshot
        if operation is DaemonApiOperation.SCANNER_STATE:
            return {
                "scanner_endpoint": snapshot["scanner_endpoint"],
                "scanner_model": snapshot["scanner_model"],
                "scanner_firmware": snapshot["scanner_firmware"],
                "scanner_connected": snapshot["scanner_connected"],
                "psi_interval_ms": snapshot["psi_interval_ms"],
                "psi_active": snapshot["psi_active"],
                "radio_state": snapshot["radio_state"],
            }
        if operation is DaemonApiOperation.AUDIO_HEALTH:
            return {
                "audio": snapshot["audio"],
                "router": snapshot["router"],
            }
        raise AssertionError(f"Unhandled daemon API operation: {operation!r}")

    def _dispatch_recording(
        self,
        operation: DaemonApiOperation,
    ) -> Mapping[str, object]:
        manager = self.recording_manager
        if manager is None:
            raise _RecordingDispatchError(
                DaemonApiErrorCode.RECORDING_UNAVAILABLE,
                "Daemon recording is unavailable.",
            )

        try:
            if operation is DaemonApiOperation.RECORDING_STATUS:
                return manager.snapshot().as_dict()
            if operation is DaemonApiOperation.RECORDING_START:
                return manager.start_recording().as_dict()
            if operation is DaemonApiOperation.RECORDING_STOP:
                return manager.stop_recording().as_dict()
            if operation is DaemonApiOperation.RECORDINGS_LIST:
                return manager.list_recordings().as_dict()
        except DaemonRecordingBusyError:
            raise _RecordingDispatchError(
                DaemonApiErrorCode.RECORDING_BUSY,
                "A daemon recording is already active or awaiting finalization.",
            ) from None
        except DaemonRecordingUnavailableError:
            raise _RecordingDispatchError(
                DaemonApiErrorCode.RECORDING_UNAVAILABLE,
                "Daemon recording is unavailable.",
            ) from None
        except DaemonRecordingOperationError:
            raise _RecordingDispatchError(
                DaemonApiErrorCode.RECORDING_FAILED,
                "The daemon recording operation could not be completed.",
            ) from None

        raise AssertionError(
            f"Unhandled daemon API recording operation: {operation!r}"
        )

    def _dispatch_control(
        self,
        operation: DaemonApiOperation,
        params: Mapping[str, object],
    ) -> Mapping[str, object]:
        runtime = cast(_ControlRuntimeLike, self.runtime)

        try:
            if operation is DaemonApiOperation.SCANNER_HOLD:
                return runtime.hold(
                    _control_target(params),
                    _navigation_value(params.get("first")),
                    _navigation_value(params.get("second")),
                    timeout=_control_timeout(params),
                ).as_dict()
            if operation is DaemonApiOperation.SCANNER_HOLD_STATE:
                return runtime.hold_state(
                    _hold_state_scope(params),
                    _hold_state_held(params),
                    timeout=_hold_state_timeout(params),
                ).as_dict()
            if operation is DaemonApiOperation.SCANNER_NEXT:
                return runtime.next(
                    _control_target(params),
                    _navigation_value(params.get("first")),
                    _navigation_value(params.get("second")),
                    count=_navigation_count(params),
                    timeout=_control_timeout(params),
                ).as_dict()
            if operation is DaemonApiOperation.SCANNER_PREVIOUS:
                return runtime.previous(
                    _control_target(params),
                    _navigation_value(params.get("first")),
                    _navigation_value(params.get("second")),
                    count=_navigation_count(params),
                    timeout=_control_timeout(params),
                ).as_dict()
            if operation is DaemonApiOperation.SCANNER_RECONNECT:
                return runtime.reconnect(
                    timeout=_control_timeout(params)
                ).as_dict()
        except DaemonControlBusyError:
            raise _ControlDispatchError(
                DaemonApiErrorCode.CONTROL_BUSY,
                "Another daemon scanner control is already in progress.",
            ) from None
        except DaemonControlUnavailableError:
            raise _ControlDispatchError(
                DaemonApiErrorCode.CONTROL_UNAVAILABLE,
                "The daemon scanner control is unavailable.",
            ) from None
        except (
            UnsupportedScannerFeatureError,
            UnsupportedScannerModelError,
        ):
            raise _ControlDispatchError(
                DaemonApiErrorCode.UNSUPPORTED_OPERATION,
                "The connected scanner does not support this operation.",
            ) from None
        except CommandTimeoutError:
            raise _ControlDispatchError(
                DaemonApiErrorCode.CONTROL_TIMEOUT,
                "The scanner control did not complete before its deadline.",
            ) from None
        except CommandRejectedError:
            raise _ControlDispatchError(
                DaemonApiErrorCode.CONTROL_REJECTED,
                "The scanner rejected the requested control.",
            ) from None
        except ValueError:
            raise _ControlDispatchError(
                DaemonApiErrorCode.INVALID_PARAMETERS,
                "The scanner control parameters are invalid.",
            ) from None
        except (OSError, SDS200Error):
            raise _ControlDispatchError(
                DaemonApiErrorCode.CONTROL_FAILED,
                "The scanner control could not be completed.",
            ) from None

        raise AssertionError(
            f"Unhandled daemon API control operation: {operation!r}"
        )

    def _capabilities(self) -> dict[str, object]:
        recording_available = self.recording_manager is not None
        control_operations = self._control_operations()
        operations = [
            operation
            for operation in DaemonApiOperation
            if (
                (
                    recording_available
                    or operation not in DAEMON_API_RECORDING_OPERATIONS
                )
                and (
                    operation not in DAEMON_API_CONTROL_OPERATIONS
                    or operation in control_operations
                )
            )
        ]
        return {
            "protocol": DAEMON_API_PROTOCOL,
            "supported_versions": list(DAEMON_API_SUPPORTED_VERSIONS),
            "operations": [operation.value for operation in operations],
            "read_only": False,
            "read_only_operations": [
                operation.value
                for operation in DAEMON_API_READ_ONLY_OPERATIONS
                if operation in operations
            ],
            "control_operations": [
                operation.value
                for operation in control_operations
            ],
            "max_control_timeout": DAEMON_API_MAX_CONTROL_TIMEOUT,
            "max_hold_state_timeout": DAEMON_API_MAX_HOLD_STATE_TIMEOUT,
        }


class _ControlParameterError(ValueError):
    pass


class _ControlDispatchError(RuntimeError):
    def __init__(
        self,
        code: DaemonApiErrorCode,
        message: str,
    ) -> None:
        super().__init__(message)
        self.code = code


class _RecordingDispatchError(RuntimeError):
    def __init__(
        self,
        code: DaemonApiErrorCode,
        message: str,
    ) -> None:
        super().__init__(message)
        self.code = code


def _validate_control_params(
    operation: DaemonApiOperation,
    params: Mapping[str, object],
) -> None:
    if operation is DaemonApiOperation.SCANNER_HOLD_STATE:
        unexpected = sorted(set(params) - {"scope", "held", "timeout"})
        if unexpected:
            raise _ControlParameterError(
                "scanner.hold_state received unexpected parameters: "
                f"{unexpected!r}."
            )
        _hold_state_scope(params)
        _hold_state_held(params)
        _hold_state_timeout(params)
        return

    if operation is DaemonApiOperation.SCANNER_RECONNECT:
        unexpected = sorted(set(params) - {"timeout"})
        if unexpected:
            raise _ControlParameterError(
                "scanner.reconnect received unexpected parameters: "
                f"{unexpected!r}."
            )
        _control_timeout(params)
        return

    allowed = {"target", "first", "second", "timeout"}
    if operation in {
        DaemonApiOperation.SCANNER_NEXT,
        DaemonApiOperation.SCANNER_PREVIOUS,
    }:
        allowed.add("count")

    unexpected = sorted(set(params) - allowed)
    if unexpected:
        raise _ControlParameterError(
            f"{operation.value} received unexpected parameters: {unexpected!r}."
        )

    _control_target(params)
    for name in ("first", "second"):
        _validate_navigation_value(name, params.get(name))

    _control_timeout(params)
    if "count" in allowed:
        _navigation_count(params)


def _hold_state_scope(params: Mapping[str, object]) -> str:
    value = params.get("scope")
    if not isinstance(value, str) or not value.strip():
        raise _ControlParameterError(
            "Scanner hold-state controls require a non-empty string scope."
        )
    normalized = value.strip().lower()
    if normalized not in _HOLD_STATE_SCOPES:
        choices = ", ".join(_HOLD_STATE_SCOPES)
        raise _ControlParameterError(
            f"Scanner hold-state scope must be one of: {choices}."
        )
    return normalized


def _hold_state_held(params: Mapping[str, object]) -> bool:
    value = params.get("held")
    if type(value) is not bool:
        raise _ControlParameterError(
            "Scanner hold-state held value must be a boolean."
        )
    return value


def _control_target(params: Mapping[str, object]) -> str:
    value = params.get("target")
    if not isinstance(value, str) or not value.strip():
        raise _ControlParameterError(
            "Scanner navigation controls require a non-empty string target."
        )
    normalized = value.strip().upper()
    if normalized not in NAVIGATION_TARGETS:
        choices = ", ".join(NAVIGATION_TARGETS)
        raise _ControlParameterError(
            f"Scanner navigation target must be one of: {choices}."
        )
    return normalized


def _validate_navigation_value(name: str, value: object) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise _ControlParameterError(
            f"Navigation parameter {name!r} must be a string, integer, or null."
        )
    normalized = str(value).strip()
    if any(delimiter in normalized for delimiter in (",", "\r", "\n")):
        raise _ControlParameterError(
            f"Navigation parameter {name!r} cannot contain commas or line breaks."
        )


def _navigation_value(value: object) -> str | int | None:
    assert value is None or (
        not isinstance(value, bool)
        and isinstance(value, (str, int))
    )
    return value


def _navigation_count(params: Mapping[str, object]) -> int:
    value = params.get("count", 1)
    if isinstance(value, bool) or not isinstance(value, int):
        raise _ControlParameterError("Navigation count must be an integer.")
    if not 1 <= value <= 8:
        raise _ControlParameterError(
            "Navigation count must be between 1 and 8."
        )
    return value


def _hold_state_timeout(params: Mapping[str, object]) -> float:
    value = params.get(
        "timeout",
        DAEMON_API_DEFAULT_HOLD_STATE_TIMEOUT,
    )
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _ControlParameterError(
            "Hold-state control timeout must be a number."
        )

    normalized = float(value)
    if (
        not isfinite(normalized)
        or normalized <= 0
        or normalized > DAEMON_API_MAX_HOLD_STATE_TIMEOUT
    ):
        raise _ControlParameterError(
            "Hold-state control timeout must be finite, greater than zero, "
            "and no more than "
            f"{DAEMON_API_MAX_HOLD_STATE_TIMEOUT:g} seconds."
        )
    return normalized


def _control_timeout(params: Mapping[str, object]) -> float:
    value = params.get("timeout", DAEMON_API_DEFAULT_CONTROL_TIMEOUT)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _ControlParameterError("Control timeout must be a number.")

    normalized = float(value)
    if (
        not isfinite(normalized)
        or normalized <= 0
        or normalized > DAEMON_API_MAX_CONTROL_TIMEOUT
    ):
        raise _ControlParameterError(
            "Control timeout must be finite, greater than zero, and no more "
            f"than {DAEMON_API_MAX_CONTROL_TIMEOUT:g} seconds."
        )
    return normalized


def _validate_request_id(value: object) -> None:
    if not isinstance(value, str):
        raise TypeError("Daemon API request identifier must be a string.")
    if not value:
        raise ValueError("Daemon API request identifier must not be empty.")
    if len(value) > DAEMON_API_MAX_REQUEST_ID_LENGTH:
        raise ValueError(
            "Daemon API request identifier exceeds "
            f"{DAEMON_API_MAX_REQUEST_ID_LENGTH} characters."
        )
    if any(ord(character) < 0x20 for character in value):
        raise ValueError(
            "Daemon API request identifier must not contain control characters."
        )


def _request_id_for_error(value: object) -> str | None:
    try:
        _validate_request_id(value)
    except (TypeError, ValueError):
        return None
    assert isinstance(value, str)
    return value
