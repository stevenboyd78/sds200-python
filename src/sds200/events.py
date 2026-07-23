from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from threading import RLock
from typing import Any

logger = logging.getLogger(__name__)
Callback = Callable[[Any], None]


class EventBus:
    def __init__(self) -> None:
        self._callbacks: dict[str, list[Callback]] = defaultdict(list)
        self._lock = RLock()

    def subscribe(self, event: str, callback: Callback) -> Callable[[], None]:
        with self._lock:
            self._callbacks[event].append(callback)

        def unsubscribe() -> None:
            with self._lock:
                callbacks = self._callbacks.get(event, [])
                if callback in callbacks:
                    callbacks.remove(callback)

        return unsubscribe

    def emit(self, event: str, value: Any) -> None:
        with self._lock:
            callbacks = tuple(self._callbacks.get(event, ()))
        for callback in callbacks:
            try:
                callback(value)
            except Exception:
                logger.exception("Unhandled exception in %s callback", event)
