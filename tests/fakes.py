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
