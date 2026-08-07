from __future__ import annotations

import errno
import json
import os
import socket as socket_module
import threading
from collections.abc import Iterable, Iterator, Mapping
from contextlib import suppress
from datetime import datetime
from math import isfinite
from typing import cast

from .daemon_events import (
    DAEMON_EVENT_DEFAULT_MAX_BYTES,
    DaemonEvent,
    DaemonEventKind,
)
from .daemon_ipc import DaemonSocketLocation
from .exceptions import (
    DaemonDisconnectedError,
    DaemonProtocolError,
    DaemonUnavailableError,
)

DAEMON_EVENT_CLIENT_DEFAULT_TIMEOUT = 5.0


class DaemonEventClient:
    """Receive and validate one local daemon JSON Lines event stream."""

    def __init__(
        self,
        location: DaemonSocketLocation,
        *,
        timeout: float = DAEMON_EVENT_CLIENT_DEFAULT_TIMEOUT,
        max_event_bytes: int = DAEMON_EVENT_DEFAULT_MAX_BYTES,
    ) -> None:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise TypeError("Daemon event connect timeout must be a number.")
        normalized_timeout = float(timeout)
        if not isfinite(normalized_timeout) or normalized_timeout <= 0:
            raise ValueError(
                "Daemon event connect timeout must be finite and greater than zero."
            )
        if type(max_event_bytes) is not int:
            raise TypeError("Maximum daemon event size must be an integer.")
        if max_event_bytes <= 0:
            raise ValueError(
                "Maximum daemon event size must be greater than zero."
            )

        self.location = location
        self.timeout = normalized_timeout
        self.max_event_bytes = max_event_bytes
        self._lifecycle_lock = threading.RLock()
        self._receive_lock = threading.Lock()
        self._socket: socket_module.socket | None = None
        self._buffer = bytearray()
        self._last_sequence: int | None = None

    @property
    def connected(self) -> bool:
        with self._lifecycle_lock:
            return self._socket is not None

    @property
    def last_sequence(self) -> int | None:
        with self._lifecycle_lock:
            return self._last_sequence

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
                client.settimeout(None)
            except OSError as error:
                _close_socket(client)
                self._raise_connect_error(error)

            self._socket = client
            self._buffer.clear()
            self._last_sequence = None
            return client

    def close(self) -> None:
        with self._lifecycle_lock:
            client = self._socket
            self._socket = None
            self._buffer.clear()
            self._last_sequence = None
        if client is not None:
            _close_socket(client)

    def __enter__(self) -> DaemonEventClient:
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

    def receive(self) -> DaemonEvent:
        """Receive one validated event and enforce stream checkpoint ordering."""

        with self._receive_lock:
            client = self.connect()
            try:
                frame = self._read_line(client)
                event = _decode_event(frame)
                self._validate_order(event)
            except (DaemonDisconnectedError, DaemonProtocolError):
                self._discard_socket(client)
                raise
            return event

    def watch(
        self,
        *,
        kinds: (
            str
            | DaemonEventKind
            | Iterable[str | DaemonEventKind]
            | None
        ) = None,
        count: int | None = None,
    ) -> Iterator[DaemonEvent]:
        """Yield validated events matching an optional bounded kind filter."""

        normalized_kinds = _normalize_event_kinds(kinds)
        if count is not None:
            if type(count) is not int:
                raise TypeError("Daemon event count must be an integer or None.")
            if count <= 0:
                raise ValueError(
                    "Daemon event count must be greater than zero."
                )

        emitted = 0
        while count is None or emitted < count:
            event = self.receive()
            if (
                normalized_kinds is not None
                and event.kind not in normalized_kinds
            ):
                continue
            emitted += 1
            yield event

    def _read_line(self, expected: socket_module.socket) -> bytes:
        while True:
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                size = newline + 1
                if size > self.max_event_bytes:
                    raise DaemonProtocolError(
                        "The daemon event exceeds the maximum accepted size "
                        f"of {self.max_event_bytes} bytes."
                    )
                frame = bytes(self._buffer[:newline])
                del self._buffer[:size]
                return frame

            if len(self._buffer) >= self.max_event_bytes:
                raise DaemonProtocolError(
                    "The daemon event exceeds the maximum accepted size "
                    f"of {self.max_event_bytes} bytes."
                )

            try:
                chunk = expected.recv(
                    min(65536, self.max_event_bytes - len(self._buffer))
                )
            except OSError as error:
                raise DaemonDisconnectedError(
                    "The daemon event stream disconnected while receiving data."
                ) from error

            if not chunk:
                if self._buffer:
                    raise DaemonProtocolError(
                        "The daemon event stream closed with an incomplete "
                        "JSON Lines event."
                    )
                raise DaemonDisconnectedError(
                    "The daemon event stream disconnected."
                )
            self._buffer.extend(chunk)

    def _validate_order(self, event: DaemonEvent) -> None:
        with self._lifecycle_lock:
            previous = self._last_sequence
            if previous is None:
                if event.kind != DaemonEventKind.SNAPSHOT:
                    raise DaemonProtocolError(
                        "The daemon event stream did not begin with an "
                        "authoritative stream.snapshot checkpoint."
                    )
                _validate_snapshot_payload(event.payload)
                self._last_sequence = event.sequence
                return

            if event.kind == DaemonEventKind.SNAPSHOT:
                raise DaemonProtocolError(
                    "The daemon event stream emitted an unexpected later "
                    "stream.snapshot checkpoint."
                )
            expected = previous + 1
            if event.sequence < expected:
                raise DaemonProtocolError(
                    "The daemon event sequence did not advance monotonically: "
                    f"previous={previous}, received={event.sequence}."
                )
            if event.sequence > expected:
                raise DaemonProtocolError(
                    "The daemon event stream contains a sequence gap: "
                    f"expected={expected}, received={event.sequence}. "
                    "Reconnect to obtain a new authoritative snapshot."
                )
            self._last_sequence = event.sequence

    def _discard_socket(self, expected: socket_module.socket) -> None:
        with self._lifecycle_lock:
            if self._socket is expected:
                self._socket = None
                self._buffer.clear()
                self._last_sequence = None
        _close_socket(expected)

    def _raise_connect_error(self, error: OSError) -> None:
        path = self.location.path
        if error.errno == errno.ENOENT:
            raise DaemonUnavailableError(
                f"Daemon event socket was not found: {path}"
            ) from error
        if error.errno == errno.ECONNREFUSED:
            raise DaemonUnavailableError(
                "Daemon event socket is present but not accepting "
                f"connections: {path}"
            ) from error
        if error.errno in {errno.EACCES, errno.EPERM}:
            raise DaemonUnavailableError(
                "Permission denied while connecting to daemon event "
                f"socket: {path}"
            ) from error
        if isinstance(error, TimeoutError):
            raise DaemonUnavailableError(
                f"Timed out connecting to daemon event socket: {path}"
            ) from error

        detail = error.strerror or error.__class__.__name__
        raise DaemonUnavailableError(
            f"Could not connect to daemon event socket {path}: {detail}"
        ) from error


def _decode_event(frame: bytes) -> DaemonEvent:
    try:
        text = frame.decode("utf-8")
        decoded: object = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DaemonProtocolError(
            "The daemon returned an invalid UTF-8 JSON event."
        ) from error

    if not isinstance(decoded, Mapping):
        raise DaemonProtocolError(
            "The daemon event envelope must be a JSON object."
        )
    if any(not isinstance(key, str) for key in decoded):
        raise DaemonProtocolError(
            "The daemon event envelope contains a non-string field name."
        )

    required = {
        "protocol",
        "version",
        "sequence",
        "observed_at",
        "kind",
        "payload",
    }
    missing = sorted(required - set(decoded))
    if missing:
        raise DaemonProtocolError(
            "The daemon event envelope omitted required fields: "
            f"{missing!r}."
        )

    observed_at = _aware_datetime(decoded["observed_at"])
    try:
        return DaemonEvent(
            protocol=cast(str, decoded["protocol"]),
            version=cast(int, decoded["version"]),
            sequence=cast(int, decoded["sequence"]),
            observed_at=observed_at,
            kind=cast(str, decoded["kind"]),
            payload=cast(Mapping[str, object], decoded["payload"]),
        )
    except (TypeError, ValueError) as error:
        raise DaemonProtocolError(
            f"Invalid daemon event envelope: {error}"
        ) from error


def _aware_datetime(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise DaemonProtocolError(
            "The daemon event observed_at field must be an ISO 8601 timestamp."
        )
    normalized = f"{value[:-1]}+00:00" if value.endswith(("Z", "z")) else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise DaemonProtocolError(
            "The daemon event observed_at field is not valid ISO 8601."
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DaemonProtocolError(
            "The daemon event observed_at field lacks a UTC offset."
        )
    return parsed


def _normalize_event_kinds(
    kinds: (
        str
        | DaemonEventKind
        | Iterable[str | DaemonEventKind]
        | None
    ),
) -> frozenset[str] | None:
    if kinds is None:
        return None

    candidates: Iterable[str | DaemonEventKind] = (
        (kinds,) if isinstance(kinds, str) else kinds
    )

    supported = {kind.value for kind in DaemonEventKind}
    normalized: set[str] = set()
    for kind in candidates:
        value = kind.value if isinstance(kind, DaemonEventKind) else kind
        if not isinstance(value, str) or value not in supported:
            choices = ", ".join(sorted(supported))
            raise ValueError(
                f"Daemon event kind must be one of: {choices}."
            )
        normalized.add(value)
    if not normalized:
        raise ValueError("Daemon event kind filter must not be empty.")
    return frozenset(normalized)


def _validate_snapshot_payload(payload: Mapping[str, object]) -> None:
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
    missing = sorted(required - set(payload))
    if missing:
        raise DaemonProtocolError(
            "The daemon event snapshot omitted required runtime fields: "
            f"{missing!r}."
        )

    _non_empty_string(payload["state"], "state")
    _non_empty_string(payload["scanner_endpoint"], "scanner_endpoint")
    for name in ("scanner_model", "scanner_firmware"):
        if name in payload:
            _optional_string(payload[name], name)
    _boolean(payload["scanner_connected"], "scanner_connected")
    _positive_integer(payload["psi_interval_ms"], "psi_interval_ms")
    _boolean(payload["psi_active"], "psi_active")
    for name in ("radio_state", "audio", "router"):
        value = payload[name]
        if not isinstance(value, Mapping) or any(
            not isinstance(key, str) for key in value
        ):
            raise DaemonProtocolError(
                "The daemon event snapshot field "
                f"{name!r} must be a JSON object."
            )

    if "recording" in payload:
        recording = payload["recording"]
        if not isinstance(recording, Mapping) or any(
            not isinstance(key, str) for key in recording
        ):
            raise DaemonProtocolError(
                "The daemon event snapshot field 'recording' "
                "must be a JSON object."
            )

    _optional_string(payload["started_at"], "started_at")
    _optional_string(payload["stopped_at"], "stopped_at")
    _non_empty_string(payload["state_changed_at"], "state_changed_at")
    transition_sequence = payload["transition_sequence"]
    if type(transition_sequence) is not int or transition_sequence < 0:
        raise DaemonProtocolError(
            "The daemon event snapshot field 'transition_sequence' "
            "must be a non-negative integer."
        )
    _optional_string(payload["last_failure_at"], "last_failure_at")
    _optional_string(payload["last_error"], "last_error")


def _non_empty_string(value: object, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise DaemonProtocolError(
            f"The daemon event snapshot field {name!r} must be a string."
        )


def _optional_string(value: object, name: str) -> None:
    if value is not None and (not isinstance(value, str) or not value):
        raise DaemonProtocolError(
            "The daemon event snapshot field "
            f"{name!r} must be a string or null."
        )


def _boolean(value: object, name: str) -> None:
    if not isinstance(value, bool):
        raise DaemonProtocolError(
            f"The daemon event snapshot field {name!r} must be a boolean."
        )


def _positive_integer(value: object, name: str) -> None:
    if type(value) is not int or value <= 0:
        raise DaemonProtocolError(
            "The daemon event snapshot field "
            f"{name!r} must be a positive integer."
        )


def _close_socket(client: socket_module.socket) -> None:
    with suppress(OSError):
        client.shutdown(socket_module.SHUT_RDWR)
    with suppress(OSError):
        client.close()
