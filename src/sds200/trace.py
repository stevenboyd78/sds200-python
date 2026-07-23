from __future__ import annotations

import logging
from pathlib import Path
from threading import RLock

logger = logging.getLogger("sds200.trace")


class TrafficTrace:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self._lock = RLock()

    def tx(self, value: str) -> None:
        self._write("TX", value)

    def rx(self, value: str) -> None:
        self._write("RX", value)

    def _write(self, direction: str, value: str) -> None:
        logger.debug("%s %s", direction, value)
        if self.path is None:
            return
        line = f"{direction}  {value}\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)
