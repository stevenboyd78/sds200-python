from __future__ import annotations

import logging
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import RLock

from .logging_config import (
    LOG_DATE_FORMAT,
    LOG_FORMAT,
    LOGGER_NAME,
    PackageStderrHandler,
)

DEFAULT_TUI_LOG_HISTORY_LIMIT = 200
TUI_LOG_VISIBLE_LINES = 6


@dataclass(frozen=True, slots=True)
class TuiLogSnapshot:
    """Immutable view of the bounded TUI log history."""

    version: int
    lines: tuple[str, ...]


class TuiLogBuffer:
    """Thread-safe bounded text buffer for package log records."""

    def __init__(self, limit: int = DEFAULT_TUI_LOG_HISTORY_LIMIT) -> None:
        if limit <= 0:
            raise ValueError("TUI log history limit must be greater than zero")
        self._limit = limit
        self._lines: deque[str] = deque(maxlen=limit)
        self._lock = RLock()
        self._version = 0

    @property
    def limit(self) -> int:
        """Return the maximum retained line count."""

        return self._limit

    def append(self, message: str) -> None:
        """Append one formatted record, retaining individual traceback lines."""

        lines = tuple(message.rstrip("\r\n").splitlines())
        if not lines:
            return
        with self._lock:
            self._lines.extend(lines)
            self._version += 1

    def snapshot(self) -> TuiLogSnapshot:
        """Return an immutable snapshot without exposing the internal deque."""

        with self._lock:
            return TuiLogSnapshot(
                version=self._version,
                lines=tuple(self._lines),
            )


class TuiLogHandler(logging.Handler):
    """Logging handler that formats records into a ``TuiLogBuffer``."""

    def __init__(self, buffer: TuiLogBuffer) -> None:
        super().__init__()
        self._buffer = buffer

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._buffer.append(self.format(record))
        except Exception:
            self.handleError(record)


@contextmanager
def capture_package_logs(buffer: TuiLogBuffer) -> Iterator[None]:
    """Replace package stderr logging while preserving every other handler."""

    package_logger = logging.getLogger(LOGGER_NAME)
    original_handlers = tuple(package_logger.handlers)
    stderr_handlers = tuple(
        handler
        for handler in original_handlers
        if isinstance(handler, PackageStderrHandler)
    )
    formatter = next(
        (
            handler.formatter
            for handler in stderr_handlers
            if handler.formatter is not None
        ),
        logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT),
    )
    level = min(
        (handler.level for handler in stderr_handlers),
        default=package_logger.level,
    )
    capture_handler = TuiLogHandler(buffer)
    capture_handler.setLevel(level)
    capture_handler.setFormatter(formatter)

    replacement_handlers: list[logging.Handler] = []
    capture_added = False
    for handler in original_handlers:
        if isinstance(handler, PackageStderrHandler):
            if not capture_added:
                replacement_handlers.append(capture_handler)
                capture_added = True
            continue
        replacement_handlers.append(handler)
    if not capture_added:
        replacement_handlers.insert(0, capture_handler)

    for handler in original_handlers:
        package_logger.removeHandler(handler)
    for handler in replacement_handlers:
        package_logger.addHandler(handler)

    try:
        yield
    finally:
        for handler in tuple(package_logger.handlers):
            package_logger.removeHandler(handler)
        for handler in original_handlers:
            package_logger.addHandler(handler)
        capture_handler.close()
