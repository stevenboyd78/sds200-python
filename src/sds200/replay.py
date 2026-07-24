from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Literal, cast

from .exceptions import CaptureFormatError, ReplayMismatchError, ScannerConnectionError
from .transport import (
    ConnectionHandler,
    ControlTransport,
    DiagnosticControlTransport,
    DiagnosticHandler,
    LineHandler,
    StatisticalControlTransport,
)

_CAPTURE_SCHEMA = "sds200.capture"
_CAPTURE_VERSION = 1
CaptureDirection = Literal["tx", "rx", "connection"]


@dataclass(frozen=True, slots=True)
class CaptureEvent:
    """One transport-level event stored in a JSON Lines capture."""

    direction: CaptureDirection
    delay_ms: float = 0.0
    data: str | None = None
    connected: bool | None = None

    def __post_init__(self) -> None:
        if self.direction not in {"tx", "rx", "connection"}:
            raise ValueError(f"Invalid capture event direction: {self.direction!r}.")
        if self.delay_ms < 0:
            raise ValueError("Capture event delay cannot be negative.")
        if self.direction in {"tx", "rx"}:
            if self.data is None:
                raise ValueError(f"{self.direction} capture event requires data.")
            if self.connected is not None:
                raise ValueError(f"{self.direction} capture event cannot set connected.")
        elif self.connected is None:
            raise ValueError("Connection capture event requires connected state.")

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "direction": self.direction,
            "delay_ms": round(self.delay_ms, 3),
        }
        if self.data is not None:
            payload["data"] = self.data
        if self.connected is not None:
            payload["connected"] = self.connected
        return payload

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object], *, line_number: int) -> CaptureEvent:
        direction = payload.get("direction")
        if direction not in {"tx", "rx", "connection"}:
            raise CaptureFormatError(
                f"Capture line {line_number} has invalid direction {direction!r}."
            )
        delay = payload.get("delay_ms", 0.0)
        if not isinstance(delay, (int, float)) or isinstance(delay, bool):
            raise CaptureFormatError(
                f"Capture line {line_number} has invalid delay_ms {delay!r}."
            )
        data = payload.get("data")
        connected = payload.get("connected")
        if data is not None and not isinstance(data, str):
            raise CaptureFormatError(
                f"Capture line {line_number} has non-string data {data!r}."
            )
        if connected is not None and not isinstance(connected, bool):
            raise CaptureFormatError(
                f"Capture line {line_number} has invalid connected state {connected!r}."
            )
        try:
            return cls(
                direction=direction,
                delay_ms=float(delay),
                data=data,
                connected=connected,
            )
        except ValueError as exc:
            raise CaptureFormatError(f"Capture line {line_number}: {exc}") from exc


@dataclass(frozen=True, slots=True)
class CaptureSession:
    """Validated capture metadata and events."""

    endpoint: str
    events: tuple[CaptureEvent, ...]
    created_at: str | None = None


class SessionCaptureWriter:
    """Thread-safe JSON Lines recorder for replayable scanner traffic."""

    def __init__(
        self,
        path: str | Path,
        *,
        endpoint: str,
        redactions: Sequence[str] = (),
    ) -> None:
        self.path = Path(path)
        self.endpoint = endpoint
        self._redactions = tuple(value for value in redactions if value)
        self._lock = threading.RLock()
        self._last_event_at = time.monotonic()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        header = {
            "schema": _CAPTURE_SCHEMA,
            "version": _CAPTURE_VERSION,
            "created_at": datetime.now(UTC).isoformat(),
            "endpoint": self._redact(endpoint),
        }
        self.path.write_text(json.dumps(header, sort_keys=True) + "\n", encoding="utf-8")

    def record_tx(self, command: str) -> None:
        self._record(CaptureEvent(direction="tx", data=self._redact(command)))

    def record_rx(self, line: str) -> None:
        self._record(CaptureEvent(direction="rx", data=self._redact(line)))

    def record_connection(self, connected: bool) -> None:
        self._record(CaptureEvent(direction="connection", connected=connected))

    def _record(self, event: CaptureEvent) -> None:
        with self._lock:
            now = time.monotonic()
            delayed = CaptureEvent(
                direction=event.direction,
                delay_ms=(now - self._last_event_at) * 1000,
                data=event.data,
                connected=event.connected,
            )
            self._last_event_at = now
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(delayed.as_dict(), sort_keys=True) + "\n")

    def _redact(self, value: str) -> str:
        result = value
        for index, sensitive in enumerate(self._redactions, start=1):
            result = result.replace(sensitive, f"<redacted:{index}>")
        return result


class RecordingTransport:
    """Control-transport wrapper that records replayable JSON Lines sessions."""

    def __init__(
        self,
        transport: ControlTransport,
        path: str | Path,
        *,
        redactions: Sequence[str] = (),
    ) -> None:
        self.transport = transport
        self.capture = SessionCaptureWriter(
            path,
            endpoint=transport.endpoint,
            redactions=redactions,
        )
        self._connection_handler: ConnectionHandler | None = None
        self._last_connected: bool | None = None

    @property
    def endpoint(self) -> str:
        return self.transport.endpoint

    @property
    def connected(self) -> bool:
        return self.transport.connected

    @property
    def statistics(self) -> Mapping[str, object]:
        if isinstance(self.transport, StatisticalControlTransport):
            return self.transport.statistics
        empty: dict[str, object] = {}
        return MappingProxyType(empty)

    def set_diagnostic_handler(self, handler: DiagnosticHandler | None) -> None:
        if isinstance(self.transport, DiagnosticControlTransport):
            self.transport.set_diagnostic_handler(handler)

    def start(
        self,
        handler: LineHandler,
        connection_handler: ConnectionHandler | None = None,
    ) -> None:
        def receive(line: str) -> None:
            self.capture.record_rx(line)
            handler(line)

        self._connection_handler = connection_handler

        def connection_changed(connected: bool) -> None:
            self._record_connection(connected)

        self.transport.start(receive, connection_changed)
        if self.transport.connected:
            self._record_connection(True)

    def stop(self) -> None:
        was_connected = self.transport.connected
        self.transport.stop()
        if was_connected and not self.transport.connected:
            self._record_connection(False)

    def write_command(self, command: str) -> None:
        self.capture.record_tx(command)
        self.transport.write_command(command)

    def _record_connection(self, connected: bool) -> None:
        if self._last_connected == connected:
            return
        self._last_connected = connected
        self.capture.record_connection(connected)
        if self._connection_handler is not None:
            self._connection_handler(connected)


class ReplayTransport:
    """Deterministic control transport backed by a JSON Lines capture."""

    def __init__(
        self,
        session: CaptureSession,
        *,
        source: str | Path | None = None,
        speed: float = 0.0,
        strict: bool = True,
    ) -> None:
        if speed < 0:
            raise ValueError("Replay speed cannot be negative.")
        self.session = session
        self.source = Path(source) if source is not None else None
        self.speed = speed
        self.strict = strict
        self._connected = False
        self._index = 0
        self._line_handler: LineHandler | None = None
        self._connection_handler: ConnectionHandler | None = None
        self._lock = threading.RLock()
        self._commands_sent = 0
        self._lines_received = 0
        self._connection_events = 0

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        *,
        speed: float = 0.0,
        strict: bool = True,
    ) -> ReplayTransport:
        return cls(load_capture(path), source=path, speed=speed, strict=strict)

    @property
    def endpoint(self) -> str:
        if self.source is not None:
            return f"replay://{self.source}"
        return f"replay://{self.session.endpoint}"

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def statistics(self) -> Mapping[str, object]:
        return MappingProxyType(
            {
                "commands_sent": self._commands_sent,
                "lines_received": self._lines_received,
                "connection_events": self._connection_events,
                "events_consumed": self._index,
                "events_total": len(self.session.events),
            }
        )

    def start(
        self,
        handler: LineHandler,
        connection_handler: ConnectionHandler | None = None,
    ) -> None:
        with self._lock:
            if self._connected:
                return
            self._line_handler = handler
            self._connection_handler = connection_handler
            self._set_connected(True)
            self._drain_non_tx_events()

    def stop(self) -> None:
        with self._lock:
            if not self._connected:
                return
            self._set_connected(False)

    def write_command(self, command: str) -> None:
        with self._lock:
            if not self.connected:
                raise ScannerConnectionError("Replay transport is disconnected.")
            self._drain_non_tx_events()
            if not self.connected:
                raise ScannerConnectionError("Replay transport disconnected before command.")
            if self._index >= len(self.session.events):
                raise ReplayMismatchError(
                    f"Replay exhausted before command {command!r} was sent."
                )
            expected = self.session.events[self._index]
            assert expected.direction == "tx"
            assert expected.data is not None
            if self.strict and expected.data != command:
                raise ReplayMismatchError(
                    f"Replay expected command {expected.data!r}, received {command!r}."
                )
            self._delay(expected)
            self._index += 1
            self._commands_sent += 1
            self._drain_non_tx_events()

    def _drain_non_tx_events(self) -> None:
        while self._index < len(self.session.events):
            event = self.session.events[self._index]
            if event.direction == "tx":
                return
            self._delay(event)
            self._index += 1
            if event.direction == "rx":
                assert event.data is not None
                if self._line_handler is None:
                    raise ScannerConnectionError("Replay transport has not been started.")
                self._lines_received += 1
                self._line_handler(event.data)
            else:
                assert event.connected is not None
                self._set_connected(event.connected)

    def _set_connected(self, connected: bool) -> None:
        if self._connected == connected:
            return
        self._connected = connected
        self._connection_events += 1
        if self._connection_handler is not None:
            self._connection_handler(connected)

    def _delay(self, event: CaptureEvent) -> None:
        if self.speed > 0 and event.delay_ms > 0:
            time.sleep(event.delay_ms / 1000 / self.speed)


def load_capture(path: str | Path) -> CaptureSession:
    """Load and validate one JSON Lines capture file."""

    capture_path = Path(path)
    try:
        raw_lines = capture_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise CaptureFormatError(f"Could not read capture {capture_path}: {exc}") from exc
    if not raw_lines:
        raise CaptureFormatError(f"Capture {capture_path} is empty.")

    header = _json_object(raw_lines[0], line_number=1)
    if header.get("schema") != _CAPTURE_SCHEMA:
        raise CaptureFormatError(
            f"Capture {capture_path} has unsupported schema {header.get('schema')!r}."
        )
    version = header.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version != _CAPTURE_VERSION:
        raise CaptureFormatError(
            f"Capture {capture_path} has unsupported version {version!r}."
        )
    endpoint = header.get("endpoint")
    if not isinstance(endpoint, str) or not endpoint:
        raise CaptureFormatError(f"Capture {capture_path} has no valid endpoint.")
    created_at = header.get("created_at")
    if created_at is not None and not isinstance(created_at, str):
        raise CaptureFormatError(f"Capture {capture_path} has invalid created_at metadata.")

    events = tuple(
        CaptureEvent.from_mapping(_json_object(line, line_number=index), line_number=index)
        for index, line in enumerate(raw_lines[1:], start=2)
        if line.strip()
    )
    return CaptureSession(endpoint=endpoint, events=events, created_at=created_at)


def write_capture(
    path: str | Path,
    events: Iterable[CaptureEvent],
    *,
    endpoint: str = "fixture://scanner",
) -> None:
    """Write deterministic capture fixtures without wall-clock timing."""

    capture_path = Path(path)
    capture_path.parent.mkdir(parents=True, exist_ok=True)
    header = {
        "schema": _CAPTURE_SCHEMA,
        "version": _CAPTURE_VERSION,
        "endpoint": endpoint,
    }
    lines = [json.dumps(header, sort_keys=True)]
    lines.extend(json.dumps(event.as_dict(), sort_keys=True) for event in events)
    capture_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _json_object(raw: str, *, line_number: int) -> dict[str, object]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CaptureFormatError(
            f"Capture line {line_number} is not valid JSON: {exc.msg}."
        ) from exc
    if not isinstance(payload, dict):
        raise CaptureFormatError(f"Capture line {line_number} must be a JSON object.")
    return cast(dict[str, object], payload)
