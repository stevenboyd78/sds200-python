from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

import serial

from .exceptions import ScannerConnectionError

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
    expected_fragment: int | None = None
    received_fragment: int | None = None
    observed_at: datetime = field(default_factory=lambda: datetime.now(UTC))


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


class SerialTransport:
    def __init__(
        self,
        port: str | Path,
        *,
        baudrate: int = 115200,
        read_timeout: float = 0.2,
        reconnect: bool = True,
        reconnect_interval: float = 2.0,
        serial_factory: SerialFactory = default_serial_factory,
    ) -> None:
        self.port = str(port)
        self.baudrate = baudrate
        self.read_timeout = read_timeout
        self.reconnect = reconnect
        self.reconnect_interval = reconnect_interval
        self._serial_factory = serial_factory
        self._serial: SerialLike | None = None
        self._handler: LineHandler | None = None
        self._connection_handler: ConnectionHandler | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._write_lock = threading.Lock()

    @property
    def endpoint(self) -> str:
        return self.port

    @property
    def connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

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
                self._close()
                raise ScannerConnectionError(
                    f"Failed to write to scanner on {self.port}."
                ) from exc
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
                try:
                    self._open()
                except ScannerConnectionError:
                    logger.warning(
                        "Reconnect failed for %s; retrying in %.1f seconds",
                        self.port,
                        self.reconnect_interval,
                    )
                    self._stop.wait(self.reconnect_interval)
                    continue

            serial_port = self._serial
            assert serial_port is not None
            try:
                chunk = serial_port.read(512)
            except (OSError, TypeError, serial.SerialException):
                # TypeError is possible in pyserial when another thread closes
                # the underlying POSIX file descriptor during read(). It is
                # harmless during an intentional shutdown.
                if self._stop.is_set():
                    return
                logger.warning("Scanner disconnected from %s", self.port)
                self._close()
                buffer.clear()
                continue

            if self._stop.is_set():
                return
            if not chunk:
                continue
            buffer.extend(chunk)

            while b"\r" in buffer:
                raw_line, _, remainder = buffer.partition(b"\r")
                buffer = bytearray(remainder)
                line = raw_line.decode("utf-8", errors="replace")
                logger.debug("RX %s", line)
                if self._handler and line:
                    self._handler(line)
