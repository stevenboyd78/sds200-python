from __future__ import annotations

import errno
import json
import os
import socket as socket_module
import threading
from collections.abc import Mapping
from contextlib import suppress
from copy import deepcopy
from datetime import datetime
from math import isfinite

from .commands import NAVIGATION_TARGETS
from .daemon_api import (
    DAEMON_API_DEFAULT_CONTROL_TIMEOUT,
    DAEMON_API_PROTOCOL,
    DAEMON_API_SUPPORTED_VERSIONS,
    DAEMON_API_VERSION,
    DaemonApiOperation,
    DaemonApiRequest,
)
from .daemon_ipc import DaemonSocketLocation
from .daemon_server import DAEMON_API_DEFAULT_MAX_RESPONSE_BYTES
from .exceptions import (
    DaemonDisconnectedError,
    DaemonProtocolError,
    DaemonRequestError,
    DaemonUnavailableError,
)

DAEMON_API_CLIENT_DEFAULT_TIMEOUT = 5.0
_DAEMON_API_CLIENT_RECV_BYTES = 4096


class DaemonApiClient:
    """Persistent negotiated client for the private local daemon API."""

    def __init__(
        self,
        location: DaemonSocketLocation,
        *,
        timeout: float = DAEMON_API_CLIENT_DEFAULT_TIMEOUT,
        max_response_bytes: int = DAEMON_API_DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        if not isinstance(location, DaemonSocketLocation):
            raise TypeError(
                "Daemon API client location must be a DaemonSocketLocation."
            )
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise TypeError("Daemon API client timeout must be a number.")
        normalized_timeout = float(timeout)
        if not isfinite(normalized_timeout) or normalized_timeout <= 0:
            raise ValueError(
                "Daemon API client timeout must be finite and greater than zero."
            )
        if isinstance(max_response_bytes, bool) or not isinstance(
            max_response_bytes,
            int,
        ):
            raise TypeError(
                "Maximum daemon API client response size must be an integer."
            )
        if max_response_bytes <= 0:
            raise ValueError(
                "Maximum daemon API client response size must be greater than zero."
            )

        self.location = location
        self.timeout = normalized_timeout
        self.max_response_bytes = max_response_bytes
        self._lifecycle_lock = threading.RLock()
        self._request_lock = threading.Lock()
        self._socket: socket_module.socket | None = None
        self._hello_result: dict[str, object] | None = None
        self._request_sequence = 0

    @property
    def connected(self) -> bool:
        with self._lifecycle_lock:
            return self._socket is not None

    def connect(self) -> socket_module.socket:
        with self._lifecycle_lock:
            if self._socket is not None:
                return self._socket

            client = socket_module.socket(
                socket_module.AF_UNIX,
                socket_module.SOCK_STREAM,
            )
            client.settimeout(self.timeout)
            try:
                client.connect(os.fspath(self.location.path))
            except OSError as error:
                client.close()
                self._raise_connect_error(error)

            self._socket = client
            return client

    def close(self) -> None:
        with self._lifecycle_lock:
            client = self._socket
            self._socket = None
            self._hello_result = None
        if client is not None:
            _close_socket(client)

    def __enter__(self) -> DaemonApiClient:
        self.connect()
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception_type, exception, traceback
        self.close()

    def hello(self) -> dict[str, object]:
        """Negotiate and validate the local daemon protocol contract."""

        with self._lifecycle_lock:
            cached = self._hello_result
        if cached is not None:
            return deepcopy(cached)

        result = self.request(DaemonApiOperation.HELLO)
        try:
            _validate_hello_result(result)
        except DaemonProtocolError:
            self.close()
            raise

        normalized_result = deepcopy(result)
        with self._lifecycle_lock:
            if self._socket is not None:
                self._hello_result = normalized_result
        return deepcopy(normalized_result)

    def runtime_snapshot(self) -> dict[str, object]:
        """Return one validated authoritative runtime snapshot."""

        result = self.request(DaemonApiOperation.RUNTIME_SNAPSHOT)
        try:
            _validate_runtime_snapshot(result)
        except DaemonProtocolError:
            self.close()
            raise
        return result

    def hold(
        self,
        target: str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        timeout: float = DAEMON_API_DEFAULT_CONTROL_TIMEOUT,
    ) -> dict[str, object]:
        """Complete one daemon-owned scanner hold operation."""

        normalized_target, normalized_first, normalized_second = (
            _navigation_parameters(target, first, second)
        )
        normalized_timeout = self._require_control_operation(
            DaemonApiOperation.SCANNER_HOLD,
            timeout,
        )
        params: dict[str, object] = {
            "target": normalized_target,
            "timeout": normalized_timeout,
        }
        if normalized_first is not None:
            params["first"] = normalized_first
        if normalized_second is not None:
            params["second"] = normalized_second
        return self._control(DaemonApiOperation.SCANNER_HOLD, params)

    def next(
        self,
        target: str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        count: int = 1,
        timeout: float = DAEMON_API_DEFAULT_CONTROL_TIMEOUT,
    ) -> dict[str, object]:
        """Move forward through one daemon-owned scanner selection list."""

        normalized_target, normalized_first, normalized_second = (
            _navigation_parameters(target, first, second)
        )
        normalized_count = _navigation_count(count)
        normalized_timeout = self._require_control_operation(
            DaemonApiOperation.SCANNER_NEXT,
            timeout,
        )
        params: dict[str, object] = {
            "target": normalized_target,
            "count": normalized_count,
            "timeout": normalized_timeout,
        }
        if normalized_first is not None:
            params["first"] = normalized_first
        if normalized_second is not None:
            params["second"] = normalized_second
        return self._control(DaemonApiOperation.SCANNER_NEXT, params)

    def previous(
        self,
        target: str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        count: int = 1,
        timeout: float = DAEMON_API_DEFAULT_CONTROL_TIMEOUT,
    ) -> dict[str, object]:
        """Move backward through one daemon-owned scanner selection list."""

        normalized_target, normalized_first, normalized_second = (
            _navigation_parameters(target, first, second)
        )
        normalized_count = _navigation_count(count)
        normalized_timeout = self._require_control_operation(
            DaemonApiOperation.SCANNER_PREVIOUS,
            timeout,
        )
        params: dict[str, object] = {
            "target": normalized_target,
            "count": normalized_count,
            "timeout": normalized_timeout,
        }
        if normalized_first is not None:
            params["first"] = normalized_first
        if normalized_second is not None:
            params["second"] = normalized_second
        return self._control(DaemonApiOperation.SCANNER_PREVIOUS, params)

    def reconnect(
        self,
        *,
        timeout: float = DAEMON_API_DEFAULT_CONTROL_TIMEOUT,
    ) -> dict[str, object]:
        """Complete one bounded daemon-owned scanner reconnect."""

        normalized_timeout = self._require_control_operation(
            DaemonApiOperation.SCANNER_RECONNECT,
            timeout,
        )
        return self._control(
            DaemonApiOperation.SCANNER_RECONNECT,
            {"timeout": normalized_timeout},
        )

    def _control(
        self,
        operation: DaemonApiOperation,
        params: Mapping[str, object],
    ) -> dict[str, object]:
        result = self.request(operation, params)
        try:
            _validate_control_result(result, expected_operation=operation)
        except DaemonProtocolError:
            self.close()
            raise
        return result

    def _require_operation(self, operation: DaemonApiOperation) -> None:
        hello = self.hello()
        operations = hello["operations"]
        assert isinstance(operations, list)
        if operation.value not in operations:
            raise DaemonProtocolError(
                f"The daemon does not advertise {operation.value} support."
            )

    def _require_control_operation(
        self,
        operation: DaemonApiOperation,
        timeout: float,
    ) -> float:
        hello = self.hello()

        if hello["read_only"] is True:
            raise DaemonProtocolError(
                "The connected daemon is read-only and does not support "
                f"{operation.value}."
            )

        control_operations = hello["control_operations"]
        assert isinstance(control_operations, list)
        if operation.value not in control_operations:
            raise DaemonProtocolError(
                f"The daemon does not advertise {operation.value} control support."
            )

        self._require_operation(operation)
        maximum = hello["max_control_timeout"]
        assert isinstance(maximum, (int, float))
        return _bounded_control_timeout(timeout, maximum=float(maximum))

    def request(
        self,
        operation: str | DaemonApiOperation,
        params: Mapping[str, object] | None = None,
        *,
        request_id: str | None = None,
    ) -> dict[str, object]:
        """Submit one correlated request and return its successful result."""

        operation_name = (
            operation.value
            if isinstance(operation, DaemonApiOperation)
            else operation
        )

        with self._request_lock:
            selected_request_id = request_id or self._next_request_id()
            request = DaemonApiRequest(
                request_id=selected_request_id,
                operation=operation_name,
                params=params or {},
            )
            encoded = (
                json.dumps(
                    request.as_dict(),
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8")

            client = self.connect()
            try:
                client.sendall(encoded)
            except OSError as error:
                self._invalidate(client)
                raise DaemonDisconnectedError(
                    "The daemon connection closed while sending a request."
                ) from error

            frame = self._read_response(client)
            try:
                return _decode_response(
                    frame,
                    expected_request_id=selected_request_id,
                )
            except DaemonProtocolError:
                self._invalidate(client)
                raise

    def _next_request_id(self) -> str:
        self._request_sequence += 1
        return f"sdsctl-{self._request_sequence}"

    def _read_response(self, client: socket_module.socket) -> bytes:
        buffer = bytearray()

        while True:
            try:
                chunk = client.recv(
                    min(
                        _DAEMON_API_CLIENT_RECV_BYTES,
                        self.max_response_bytes + 1,
                    )
                )
            except TimeoutError as error:
                self._invalidate(client)
                raise DaemonDisconnectedError(
                    "Timed out waiting for a daemon API response."
                ) from error
            except OSError as error:
                self._invalidate(client)
                raise DaemonDisconnectedError(
                    "The daemon connection closed while receiving a response."
                ) from error

            if not chunk:
                self._invalidate(client)
                raise DaemonDisconnectedError(
                    "The daemon disconnected before returning a complete response."
                )

            buffer.extend(chunk)
            newline = buffer.find(b"\n")
            if newline >= 0:
                frame = bytes(buffer[:newline])
                if len(frame) > self.max_response_bytes:
                    self._invalidate(client)
                    raise DaemonProtocolError(
                        "The daemon response exceeded the configured client limit."
                    )
                if newline + 1 != len(buffer):
                    self._invalidate(client)
                    raise DaemonProtocolError(
                        "The daemon returned more than one response for one request."
                    )
                return frame

            if len(buffer) > self.max_response_bytes:
                self._invalidate(client)
                raise DaemonProtocolError(
                    "The daemon response exceeded the configured client limit."
                )

    def _invalidate(self, expected: socket_module.socket) -> None:
        with self._lifecycle_lock:
            if self._socket is expected:
                self._socket = None
                self._hello_result = None
        _close_socket(expected)

    def _raise_connect_error(self, error: OSError) -> None:
        path = self.location.path
        if error.errno == errno.ENOENT:
            raise DaemonUnavailableError(
                f"Daemon socket was not found: {path}"
            ) from error
        if error.errno == errno.ECONNREFUSED:
            raise DaemonUnavailableError(
                f"Daemon socket is present but not accepting connections: {path}"
            ) from error
        if error.errno in {errno.EACCES, errno.EPERM}:
            raise DaemonUnavailableError(
                f"Permission denied while connecting to daemon socket: {path}"
            ) from error
        if isinstance(error, TimeoutError):
            raise DaemonUnavailableError(
                f"Timed out connecting to daemon socket: {path}"
            ) from error

        detail = error.strerror or error.__class__.__name__
        raise DaemonUnavailableError(
            f"Could not connect to daemon socket {path}: {detail}"
        ) from error


def _navigation_parameters(
    target: object,
    first: object,
    second: object,
) -> tuple[str, str | int | None, str | int | None]:
    if not isinstance(target, str) or not target.strip():
        raise ValueError("Daemon scanner control target must be a non-empty string.")
    normalized_target = target.strip().upper()
    if normalized_target not in NAVIGATION_TARGETS:
        choices = ", ".join(NAVIGATION_TARGETS)
        raise ValueError(
            f"Daemon scanner control target must be one of: {choices}."
        )

    return (
        normalized_target,
        _navigation_value(first, "first"),
        _navigation_value(second, "second"),
    )


def _navigation_value(
    value: object,
    name: str,
) -> str | int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise TypeError(
            f"Daemon navigation parameter {name!r} must be a string or integer."
        )

    normalized = str(value).strip()
    if any(delimiter in normalized for delimiter in (",", "\r", "\n")):
        raise ValueError(
            f"Daemon navigation parameter {name!r} cannot contain "
            "commas or line breaks."
        )
    return value


def _navigation_count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("Daemon navigation count must be an integer.")
    if not 1 <= value <= 8:
        raise ValueError("Daemon navigation count must be between 1 and 8.")
    return value


def _bounded_control_timeout(
    value: object,
    *,
    maximum: float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("Daemon control timeout must be a number.")
    normalized = float(value)
    if not isfinite(normalized) or normalized <= 0:
        raise ValueError(
            "Daemon control timeout must be finite and greater than zero."
        )
    if normalized > maximum:
        raise ValueError(
            "Daemon control timeout exceeds the advertised maximum "
            f"of {maximum} seconds."
        )
    return normalized


def _validate_control_result(
    result: Mapping[str, object],
    *,
    expected_operation: DaemonApiOperation,
) -> None:
    required = {
        "sequence",
        "operation",
        "started_at",
        "completed_at",
        "snapshot",
    }
    missing = sorted(required - set(result))
    if missing:
        raise DaemonProtocolError(
            "The daemon control result omitted required fields: "
            f"{missing!r}."
        )

    sequence = result["sequence"]
    if type(sequence) is not int or sequence <= 0:
        raise DaemonProtocolError(
            "The daemon control result sequence must be a positive integer."
        )

    operation = result["operation"]
    if operation != expected_operation.value:
        raise DaemonProtocolError(
            "The daemon control result operation did not match the request."
        )

    started_at = _aware_datetime(result["started_at"], "started_at")
    completed_at = _aware_datetime(result["completed_at"], "completed_at")
    if completed_at < started_at:
        raise DaemonProtocolError(
            "The daemon control completion precedes its start time."
        )

    snapshot = result["snapshot"]
    if not isinstance(snapshot, Mapping) or any(
        not isinstance(key, str) for key in snapshot
    ):
        raise DaemonProtocolError(
            "The daemon control result snapshot must be a JSON object."
        )
    _validate_runtime_snapshot(snapshot)


def _aware_datetime(value: object, name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise DaemonProtocolError(
            f"The daemon control result field {name!r} must be a timestamp."
        )

    normalized = f"{value[:-1]}+00:00" if value.endswith(("Z", "z")) else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise DaemonProtocolError(
            f"The daemon control result field {name!r} is not ISO 8601."
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DaemonProtocolError(
            f"The daemon control result field {name!r} lacks a UTC offset."
        )
    return parsed


def _validate_hello_result(result: Mapping[str, object]) -> None:
    protocol = result.get("protocol")
    selected_version = result.get("selected_version")
    supported_versions = _integer_list(result, "supported_versions")
    operations = _string_list(result, "operations")
    read_only = result.get("read_only")

    if protocol != DAEMON_API_PROTOCOL:
        raise DaemonProtocolError(
            "The daemon hello result advertised an incompatible protocol."
        )
    if type(selected_version) is not int:
        raise DaemonProtocolError(
            "The daemon hello result omitted a valid selected version."
        )
    if selected_version != DAEMON_API_VERSION:
        raise DaemonProtocolError(
            "The daemon selected unsupported protocol version "
            f"{selected_version}; client version={DAEMON_API_VERSION}."
        )
    if DAEMON_API_VERSION not in supported_versions:
        raise DaemonProtocolError(
            "The daemon does not advertise the client protocol version."
        )
    if not isinstance(read_only, bool):
        raise DaemonProtocolError(
            "The daemon hello result omitted a valid read_only capability."
        )

    if read_only:
        read_only_operations = (
            _string_list(result, "read_only_operations")
            if "read_only_operations" in result
            else operations
        )
        control_operations = (
            _string_list(result, "control_operations")
            if "control_operations" in result
            else ()
        )
        if control_operations:
            raise DaemonProtocolError(
                "A read-only daemon cannot advertise control operations."
            )
    else:
        read_only_operations = _string_list(result, "read_only_operations")
        control_operations = _string_list(result, "control_operations")

    if not read_only or "max_control_timeout" in result:
        max_control_timeout = result.get("max_control_timeout")
        if isinstance(max_control_timeout, bool) or not isinstance(
            max_control_timeout,
            (int, float),
        ):
            raise DaemonProtocolError(
                "The daemon hello result omitted a valid control timeout."
            )
        normalized_timeout = float(max_control_timeout)
        if not isfinite(normalized_timeout) or normalized_timeout <= 0:
            raise DaemonProtocolError(
                "The daemon hello result advertised an invalid control timeout."
            )

    operation_set = set(operations)
    read_only_set = set(read_only_operations)
    control_set = set(control_operations)
    if len(operation_set) != len(operations):
        raise DaemonProtocolError(
            "The daemon hello result contains duplicate operations."
        )
    if len(read_only_set) != len(read_only_operations):
        raise DaemonProtocolError(
            "The daemon hello result contains duplicate read-only operations."
        )
    if len(control_set) != len(control_operations):
        raise DaemonProtocolError(
            "The daemon hello result contains duplicate control operations."
        )
    if DaemonApiOperation.HELLO.value not in operation_set:
        raise DaemonProtocolError(
            "The daemon hello result does not advertise hello support."
        )
    if not read_only_set.issubset(operation_set):
        raise DaemonProtocolError(
            "The daemon hello result advertises unknown read-only operations."
        )
    if not control_set.issubset(operation_set):
        raise DaemonProtocolError(
            "The daemon hello result advertises unknown control operations."
        )
    if read_only_set & control_set:
        raise DaemonProtocolError(
            "The daemon hello result overlaps read-only and control operations."
        )


def _validate_runtime_snapshot(result: Mapping[str, object]) -> None:
    required = {
        "state",
        "scanner_endpoint",
        "scanner_connected",
        "psi_interval_ms",
        "psi_active",
        "radio_state",
        "audio",
        "router",
        "started_at",
        "stopped_at",
        "state_changed_at",
        "transition_sequence",
        "last_failure_at",
        "last_error",
    }
    missing = sorted(required - set(result))
    if missing:
        raise DaemonProtocolError(
            "The daemon runtime snapshot omitted required fields: "
            f"{missing!r}."
        )

    _non_empty_string(result["state"], "state")
    _non_empty_string(result["scanner_endpoint"], "scanner_endpoint")
    for name in ("scanner_model", "scanner_firmware"):
        if name in result:
            _optional_string(result[name], name)
    _boolean(result["scanner_connected"], "scanner_connected")
    _positive_integer(result["psi_interval_ms"], "psi_interval_ms")
    _boolean(result["psi_active"], "psi_active")
    for name in ("radio_state", "audio", "router"):
        _string_keyed_mapping(result[name], name)

    _optional_string(result["started_at"], "started_at")
    _optional_string(result["stopped_at"], "stopped_at")
    _non_empty_string(result["state_changed_at"], "state_changed_at")
    _non_negative_integer(result["transition_sequence"], "transition_sequence")
    _optional_string(result["last_failure_at"], "last_failure_at")
    _optional_string(result["last_error"], "last_error")


def _integer_list(
    payload: Mapping[str, object],
    name: str,
) -> tuple[int, ...]:
    value = payload.get(name)
    if not isinstance(value, list):
        raise DaemonProtocolError(
            f"The daemon hello result omitted a valid {name} list."
        )

    normalized: list[int] = []
    for item in value:
        if type(item) is not int:
            raise DaemonProtocolError(
                f"The daemon hello result omitted a valid {name} list."
            )
        normalized.append(item)
    return tuple(normalized)


def _string_list(
    payload: Mapping[str, object],
    name: str,
) -> tuple[str, ...]:
    value = payload.get(name)
    if not isinstance(value, list):
        raise DaemonProtocolError(
            f"The daemon hello result omitted a valid {name} list."
        )

    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise DaemonProtocolError(
                f"The daemon hello result omitted a valid {name} list."
            )
        normalized.append(item)
    return tuple(normalized)


def _non_empty_string(value: object, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise DaemonProtocolError(
            f"The daemon runtime snapshot field {name!r} must be a string."
        )


def _optional_string(value: object, name: str) -> None:
    if value is not None and (not isinstance(value, str) or not value):
        raise DaemonProtocolError(
            "The daemon runtime snapshot field "
            f"{name!r} must be a string or null."
        )


def _boolean(value: object, name: str) -> None:
    if not isinstance(value, bool):
        raise DaemonProtocolError(
            f"The daemon runtime snapshot field {name!r} must be a boolean."
        )


def _positive_integer(value: object, name: str) -> None:
    if type(value) is not int or value <= 0:
        raise DaemonProtocolError(
            "The daemon runtime snapshot field "
            f"{name!r} must be a positive integer."
        )


def _non_negative_integer(value: object, name: str) -> None:
    if type(value) is not int or value < 0:
        raise DaemonProtocolError(
            "The daemon runtime snapshot field "
            f"{name!r} must be a non-negative integer."
        )


def _string_keyed_mapping(value: object, name: str) -> None:
    if not isinstance(value, Mapping) or any(
        not isinstance(key, str) for key in value
    ):
        raise DaemonProtocolError(
            "The daemon runtime snapshot field "
            f"{name!r} must be a JSON object."
        )


def _decode_response(
    frame: bytes,
    *,
    expected_request_id: str,
) -> dict[str, object]:
    try:
        text = frame.decode("utf-8")
        payload: object = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DaemonProtocolError(
            "The daemon returned an invalid UTF-8 JSON response."
        ) from error

    if not isinstance(payload, Mapping):
        raise DaemonProtocolError("The daemon response must be a JSON object.")
    if any(not isinstance(key, str) for key in payload):
        raise DaemonProtocolError(
            "The daemon response contains a non-string field name."
        )

    protocol = payload.get("protocol")
    version = payload.get("version")
    request_id = payload.get("request_id")
    ok = payload.get("ok")

    if protocol != DAEMON_API_PROTOCOL:
        raise DaemonProtocolError(
            f"Incompatible daemon protocol: {protocol!r}."
        )
    if type(version) is not int or version not in DAEMON_API_SUPPORTED_VERSIONS:
        raise DaemonProtocolError(
            f"Incompatible daemon protocol version: {version!r}."
        )
    if request_id != expected_request_id:
        raise DaemonProtocolError(
            "The daemon response request identifier did not match the request."
        )
    if not isinstance(ok, bool):
        raise DaemonProtocolError(
            "The daemon response omitted a valid success indicator."
        )

    if ok:
        if "error" in payload or "result" not in payload:
            raise DaemonProtocolError(
                "A successful daemon response must contain only a result."
            )
        result = payload["result"]
        if not isinstance(result, Mapping):
            raise DaemonProtocolError(
                "The daemon response result must be a JSON object."
            )
        if any(not isinstance(key, str) for key in result):
            raise DaemonProtocolError(
                "The daemon response result contains a non-string field name."
            )
        return {key: value for key, value in result.items()}

    if "result" in payload or "error" not in payload:
        raise DaemonProtocolError(
            "A failed daemon response must contain only an error."
        )
    error_payload = payload["error"]
    if not isinstance(error_payload, Mapping):
        raise DaemonProtocolError(
            "The daemon response error must be a JSON object."
        )
    code = error_payload.get("code")
    message = error_payload.get("message")
    if not isinstance(code, str) or not code:
        raise DaemonProtocolError(
            "The daemon response error omitted a valid code."
        )
    if not isinstance(message, str) or not message:
        raise DaemonProtocolError(
            "The daemon response error omitted a valid message."
        )

    raise DaemonRequestError(
        code,
        message,
        request_id=expected_request_id,
    )


def _close_socket(client: socket_module.socket) -> None:
    with suppress(OSError):
        client.shutdown(socket_module.SHUT_RDWR)
    with suppress(OSError):
        client.close()
