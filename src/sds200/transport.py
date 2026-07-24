from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Protocol, runtime_checkable

import serial

from .exceptions import ScannerConnectionError
from .reliability import ReconnectCounter, ReconnectPolicy

logger = logging.getLogger(__name__)


class SerialLike(Protocol):
    is_open: bool

    def read(self, size: int = 1) -> bytes: ...
    def write(self, data: bytes) -> int | None: ...
    def close(self) -> None: ...


LineHandler = Callable[[str], None]
ConnectionHandler = Callable[[bool], None]


@dataclass(frozen=True, slots=True)
class TransportDiagnostic:
    kind: str
    message: str
    command: str | None = None
    endpoint: str | None = None
    expected_fragment: int | None = None
    received_fragment: int | None = None
    attempt: int | None = None
    delay_seconds: float | None = None
    previous_endpoint: str | None = None
    observed_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "message": self.message,
            "command": self.command,
            "endpoint": self.endpoint,
            "expected_fragment": self.expected_fragment,
            "received_fragment": self.received_fragment,
            "attempt": self.attempt,
            "delay_seconds": self.delay_seconds,
            "previous_endpoint": self.previous_endpoint,
            "observed_at": self.observed_at.isoformat(),
        }


DiagnosticHandler = Callable[[TransportDiagnostic], None]
SerialFactory = Callable[..., SerialLike]


@runtime_checkable
class ControlTransport(Protocol):
    """Transport contract shared by serial and future network connections."""

    @property
    def endpoint(self) -> str: ...

    @property
    def connected(self) -> bool: ...

    def start(
        self,
        handler: LineHandler,
        connection_handler: ConnectionHandler | None = None,
    ) -> None: ...

    def stop(self) -> None: ...

    def write_command(self, command: str) -> None: ...


@runtime_checkable
class DiagnosticControlTransport(Protocol):
    def set_diagnostic_handler(
        self,
        handler: DiagnosticHandler | None,
    ) -> None: ...


@runtime_checkable
class StatisticalControlTransport(Protocol):
    @property
    def statistics(self) -> Mapping[str, object]: ...


@dataclass(slots=True)
class _MutableSerialStatistics:
    commands_sent: int = 0
    bytes_received: int = 0
    lines_received: int = 0
    read_errors: int = 0
    write_errors: int = 0
    connection_opens: int = 0
    reconnects: int = 0
    reconnect_attempts: int = 0
    reconnect_failures: int = 0
    reconnect_exhausted: int = 0
    last_reconnect_at: datetime | None = None
    last_receive_at: datetime | None = None
    last_error: str | None = None

    def mapping(self) -> Mapping[str, object]:
        return MappingProxyType(
            {
                "commands_sent": self.commands_sent,
                "bytes_received": self.bytes_received,
                "lines_received": self.lines_received,
                "read_errors": self.read_errors,
                "write_errors": self.write_errors,
                "connection_opens": self.connection_opens,
                "reconnects": self.reconnects,
                "reconnect_attempts": self.reconnect_attempts,
                "reconnect_failures": self.reconnect_failures,
                "reconnect_exhausted": self.reconnect_exhausted,
                "last_reconnect_at": (
                    self.last_reconnect_at.isoformat()
                    if self.last_reconnect_at is not None
                    else None
                ),
                "last_receive_at": (
                    self.last_receive_at.isoformat()
                    if self.last_receive_at is not None
                    else None
                ),
                "last_error": self.last_error,
            }
        )


def default_serial_factory(
    *,
    port: str,
    baudrate: int,
    timeout: float | None,
    write_timeout: float | None,
) -> SerialLike:
    return serial.Serial(
        port=port,
        baudrate=baudrate,
        timeout=timeout,
        write_timeout=write_timeout,
    )


def _pop_serial_line(buffer: bytearray) -> bytes | None:
    carriage_return = buffer.find(b"\r")
    line_feed = buffer.find(b"\n")
    indexes = tuple(index for index in (carriage_return, line_feed) if index >= 0)
    if not indexes:
        return None
    delimiter = min(indexes)
    line = bytes(buffer[:delimiter])
    del buffer[: delimiter + 1]
    while buffer and buffer[0] in {10, 13}:
        del buffer[0]
    return line


class SerialTransport:
    def __init__(
        self,
        port: str | Path,
        *,
        baudrate: int = 115200,
        read_timeout: float = 0.2,
        reconnect: bool = True,
        reconnect_interval: float = 2.0,
        reconnect_policy: ReconnectPolicy | None = None,
        serial_factory: SerialFactory = default_serial_factory,
    ) -> None:
        self.port = str(port)
        self.baudrate = baudrate
        self.read_timeout = read_timeout
        if reconnect_interval <= 0:
            raise ValueError("Reconnect interval must be greater than zero.")
        self.reconnect = reconnect
        self.reconnect_interval = reconnect_interval
        self.reconnect_policy = reconnect_policy or ReconnectPolicy(
            initial_delay=reconnect_interval,
            multiplier=1.0,
            max_delay=reconnect_interval,
        )
        self._reconnect_counter = ReconnectCounter(self.reconnect_policy)
        self._serial_factory = serial_factory
        self._serial: SerialLike | None = None
        self._handler: LineHandler | None = None
        self._connection_handler: ConnectionHandler | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._write_lock = threading.Lock()
        self._statistics_lock = threading.RLock()
        self._statistics = _MutableSerialStatistics()
        self._diagnostic_handler: DiagnosticHandler | None = None

    @property
    def endpoint(self) -> str:
        return self.port

    @property
    def connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    @property
    def statistics(self) -> Mapping[str, object]:
        with self._statistics_lock:
            return self._statistics.mapping()

    def set_diagnostic_handler(
        self,
        handler: DiagnosticHandler | None,
    ) -> None:
        self._diagnostic_handler = handler

    def start(
        self,
        handler: LineHandler,
        connection_handler: ConnectionHandler | None = None,
    ) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._handler = handler
        self._connection_handler = connection_handler
        self._stop.clear()
        self._open()
        self._thread = threading.Thread(
            target=self._reader_loop,
            name="sds200-serial-reader",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread

        # A pyserial read uses the port's file descriptor. Closing the port
        # before the reader returns can change that descriptor to None while
        # serialposix.read() is still using it. Let the configured read timeout
        # wake the thread first, then close the port.
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(1.0, self.read_timeout * 4))
            if thread.is_alive():
                logger.warning(
                    "Reader thread did not stop before timeout; forcing %s closed",
                    self.port,
                )
                self._close()
                thread.join(timeout=1.0)

        self._close()
        self._thread = None

    def write_command(self, command: str) -> None:
        normalized = command.rstrip("\r\n")
        if not normalized:
            raise ValueError("Command must not be empty.")
        data = (normalized + "\r").encode("ascii")

        with self._write_lock:
            if not self.connected or self._serial is None:
                raise ScannerConnectionError(f"Scanner is not connected on {self.port}.")
            try:
                self._serial.write(data)
            except (OSError, serial.SerialException) as exc:
                with self._statistics_lock:
                    self._statistics.write_errors += 1
                    self._statistics.last_error = str(exc)
                self._emit_diagnostic(
                    TransportDiagnostic(
                        kind="serial_write_error",
                        endpoint=self.endpoint,
                        message=f"Serial write failed on {self.endpoint}: {exc}",
                    )
                )
                self._close()
                raise ScannerConnectionError(
                    f"Failed to write to scanner on {self.port}."
                ) from exc
        with self._statistics_lock:
            self._statistics.commands_sent += 1
        logger.debug("TX %s", normalized)

    def _open(self) -> None:
        try:
            self._serial = self._serial_factory(
                port=self.port,
                baudrate=self.baudrate,
                timeout=self.read_timeout,
                write_timeout=self.read_timeout,
            )
        except (OSError, serial.SerialException) as exc:
            self._serial = None
            raise ScannerConnectionError(
                f"Could not open scanner serial port {self.port}."
            ) from exc
        with self._statistics_lock:
            was_reconnect = self._statistics.connection_opens > 0
            self._statistics.connection_opens += 1
            if was_reconnect:
                self._statistics.reconnects += 1
                self._statistics.last_reconnect_at = datetime.now(UTC)
        if was_reconnect:
            self._emit_diagnostic(
                TransportDiagnostic(
                    kind="reconnected",
                    endpoint=self.endpoint,
                    message=f"Reconnected scanner serial transport on {self.endpoint}",
                )
            )
        self._reconnect_counter.reset()
        logger.info("Connected to scanner on %s", self.port)
        self._notify_connection(True)

    def _close(self) -> None:
        serial_port, self._serial = self._serial, None
        was_connected = serial_port is not None and serial_port.is_open
        if serial_port is not None:
            try:
                serial_port.close()
            except (OSError, serial.SerialException):
                logger.debug("Error while closing serial port", exc_info=True)
        if was_connected:
            self._notify_connection(False)

    def _emit_diagnostic(self, diagnostic: TransportDiagnostic) -> None:
        if self._diagnostic_handler is None:
            return
        try:
            self._diagnostic_handler(diagnostic)
        except Exception:
            logger.exception("Unhandled exception in serial diagnostic callback")

    def _notify_connection(self, connected: bool) -> None:
        if self._connection_handler is None:
            return
        try:
            self._connection_handler(connected)
        except Exception:
            logger.exception("Unhandled exception in connection callback")

    def _reader_loop(self) -> None:
        buffer = bytearray()

        while not self._stop.is_set():
            if not self.connected:
                if not self.reconnect:
                    return
                scheduled = self._reconnect_counter.next()
                if scheduled is None:
                    with self._statistics_lock:
                        self._statistics.reconnect_exhausted += 1
                    self._emit_diagnostic(
                        TransportDiagnostic(
                            kind="reconnect_exhausted",
                            endpoint=self.endpoint,
                            message=(
                                "Serial reconnect policy exhausted after "
                                f"{self._reconnect_counter.attempts} attempts"
                            ),
                            attempt=self._reconnect_counter.attempts,
                        )
                    )
                    return
                attempt, delay = scheduled
                with self._statistics_lock:
                    self._statistics.reconnect_attempts += 1
                self._emit_diagnostic(
                    TransportDiagnostic(
                        kind="reconnect_scheduled",
                        endpoint=self.endpoint,
                        message=(
                            f"Serial reconnect attempt {attempt} scheduled in "
                            f"{delay:.1f} seconds"
                        ),
                        attempt=attempt,
                        delay_seconds=delay,
                    )
                )
                if self._stop.wait(delay):
                    return
                try:
                    self._open()
                except ScannerConnectionError as exc:
                    with self._statistics_lock:
                        self._statistics.reconnect_failures += 1
                        self._statistics.last_error = str(exc)
                    self._emit_diagnostic(
                        TransportDiagnostic(
                            kind="reconnect_failed",
                            endpoint=self.endpoint,
                            message=f"Serial reconnect attempt {attempt} failed: {exc}",
                            attempt=attempt,
                        )
                    )
                    continue

            serial_port = self._serial
            assert serial_port is not None
            try:
                chunk = serial_port.read(512)
            except (OSError, TypeError, serial.SerialException) as exc:
                # TypeError is possible in pyserial when another thread closes
                # the underlying POSIX file descriptor during read(). It is
                # harmless during an intentional shutdown.
                if self._stop.is_set():
                    return
                with self._statistics_lock:
                    self._statistics.read_errors += 1
                    self._statistics.last_error = str(exc)
                self._emit_diagnostic(
                    TransportDiagnostic(
                        kind="serial_read_error",
                        endpoint=self.endpoint,
                        message=f"Serial read failed on {self.endpoint}: {exc}",
                    )
                )
                logger.warning("Scanner disconnected from %s", self.port)
                self._close()
                buffer.clear()
                continue

            if self._stop.is_set():
                return
            if not chunk:
                continue
            with self._statistics_lock:
                self._statistics.bytes_received += len(chunk)
                self._statistics.last_receive_at = datetime.now(UTC)
            buffer.extend(chunk)

            while True:
                raw_line = _pop_serial_line(buffer)
                if raw_line is None:
                    break
                line = raw_line.decode("utf-8", errors="replace")
                logger.debug("RX %s", line)
                with self._statistics_lock:
                    self._statistics.lines_received += 1
                if self._handler and line:
                    self._handler(line)
