from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable

from sds200.exceptions import ScannerConnectionError


class FakeSerial:
    def __init__(self, **_: object) -> None:
        self.is_open = True
        self.reads: queue.Queue[bytes] = queue.Queue()
        self.writes: list[bytes] = []

    def read(self, size: int = 1) -> bytes:
        del size
        try:
            return self.reads.get(timeout=0.05)
        except queue.Empty:
            return b""

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        return len(data)

    def close(self) -> None:
        self.is_open = False

    def feed(self, data: bytes) -> None:
        self.reads.put(data)


class CloseAwareSerial(FakeSerial):
    """Detect whether close() occurs while read() is in progress."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.read_started = threading.Event()
        self.closed_while_reading = False
        self._reading = False
        self._state_lock = threading.Lock()

    def read(self, size: int = 1) -> bytes:
        del size
        with self._state_lock:
            self._reading = True
            self.read_started.set()

        time.sleep(0.05)

        with self._state_lock:
            if not self.is_open:
                self.closed_while_reading = True
            self._reading = False
        return b""

    def close(self) -> None:
        with self._state_lock:
            if self._reading:
                self.closed_while_reading = True
            self.is_open = False


class FakeTransport:
    def __init__(self, endpoint: str = "fake://scanner") -> None:
        self._endpoint = endpoint
        self._connected = False
        self.writes: list[str] = []
        self._line_handler: Callable[[str], None] | None = None
        self._connection_handler: Callable[[bool], None] | None = None

    @property
    def endpoint(self) -> str:
        return self._endpoint

    @property
    def connected(self) -> bool:
        return self._connected

    def start(
        self,
        handler: Callable[[str], None],
        connection_handler: Callable[[bool], None] | None = None,
    ) -> None:
        self._line_handler = handler
        self._connection_handler = connection_handler
        self.set_connected(True)

    def stop(self) -> None:
        self.set_connected(False)

    def write_command(self, command: str) -> None:
        if not self.connected:
            raise ScannerConnectionError("Fake transport is disconnected.")
        self.writes.append(command)

    def feed_line(self, line: str) -> None:
        assert self._line_handler is not None
        self._line_handler(line)

    def set_connected(self, connected: bool) -> None:
        if self._connected == connected:
            return
        self._connected = connected
        if self._connection_handler is not None:
            self._connection_handler(connected)


class FakeDatagramSocket:
    def __init__(self) -> None:
        self.timeout: float | None = None
        self.bound: tuple[str, int] | None = None
        self.remote: tuple[str, int] | None = None
        self.sent: list[bytes] = []
        self.incoming: queue.Queue[bytes | OSError] = queue.Queue()
        self.closed = False

    def settimeout(self, value: float | None) -> None:
        self.timeout = value

    def bind(self, address: tuple[str, int]) -> None:
        self.bound = address

    def connect(self, address: tuple[str, int]) -> None:
        self.remote = address

    def send(self, data: bytes) -> int:
        if self.closed:
            raise OSError("socket is closed")
        self.sent.append(data)
        return len(data)

    def recv(self, size: int) -> bytes:
        del size
        try:
            value = self.incoming.get(timeout=self.timeout or 0.05)
        except queue.Empty as exc:
            raise TimeoutError from exc
        if isinstance(value, OSError):
            raise value
        return value

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.incoming.put(OSError("socket is closed"))

    def feed(self, data: bytes) -> None:
        self.incoming.put(data)


class FakeDatagramSocketFactory:
    def __init__(self, socket: FakeDatagramSocket | None = None) -> None:
        self.socket = socket or FakeDatagramSocket()
        self.calls: list[tuple[int, int]] = []

    def __call__(self, family: int, socket_type: int) -> FakeDatagramSocket:
        self.calls.append((family, socket_type))
        return self.socket


class DatagramSocketSequenceFactory:
    def __init__(self, sockets: list[FakeDatagramSocket]) -> None:
        self.sockets = list(sockets)
        self.calls: list[tuple[int, int]] = []

    def __call__(self, family: int, socket_type: int) -> FakeDatagramSocket:
        self.calls.append((family, socket_type))
        if not self.sockets:
            raise OSError("no fake sockets remain")
        return self.sockets.pop(0)
