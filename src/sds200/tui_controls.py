from __future__ import annotations

import queue
from collections.abc import Callable
from dataclasses import dataclass
from threading import Event, Thread, current_thread
from typing import Literal

from .commands import NavigationTarget
from .state import RadioStateSnapshot

ControlOperation = Callable[[], None]
ControlSuccess = Callable[[], None]
HoldScope = Literal["system", "department", "site", "channel"]


@dataclass(frozen=True, slots=True)
class HoldSelection:
    """One documented HLD target resolved from the current PSI/GSI state."""

    scope: HoldScope
    target: NavigationTarget
    first: int
    second: int | None = None


@dataclass(frozen=True, slots=True)
class ControlRequest:
    """One scanner command executed by the serialized TUI control worker."""

    label: str
    operation: ControlOperation
    on_success: ControlSuccess | None = None


ControlCompletion = Callable[[ControlRequest, Exception | None], None]


class ControlWorker:
    """Execute scanner controls sequentially away from the Textual event loop."""

    def __init__(self, completed: ControlCompletion) -> None:
        self._completed = completed
        self._queue: queue.Queue[ControlRequest | None] = queue.Queue()
        self._stop = Event()
        self._thread: Thread | None = None

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.alive:
            return
        self._stop.clear()
        self._thread = Thread(
            target=self._run,
            name="sds200-tui-controls",
            daemon=True,
        )
        self._thread.start()

    def submit(self, request: ControlRequest) -> None:
        if not self.alive:
            raise RuntimeError("TUI control worker is not running")
        self._queue.put(request)

    def stop(self) -> None:
        thread = self._thread
        if thread is None:
            return
        self._stop.set()
        self._queue.put(None)
        if thread is not current_thread():
            thread.join(timeout=3.0)
        if not thread.is_alive():
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            request = self._queue.get()
            try:
                if request is None:
                    return
                error: Exception | None = None
                try:
                    request.operation()
                except Exception as exc:  # scanner failures become UI status
                    error = exc
                self._completed(request, error)
            finally:
                self._queue.task_done()


def hold_selection(
    snapshot: RadioStateSnapshot,
    scope: HoldScope,
) -> HoldSelection | None:
    """Resolve a system, department, site, or channel hold from PSI indexes."""

    if scope == "system":
        if snapshot.system_index is None:
            return None
        return HoldSelection(scope, "SYS", snapshot.system_index)
    if scope == "department":
        if snapshot.department_index is None or snapshot.system_index is None:
            return None
        return HoldSelection(
            scope,
            "DEPT",
            snapshot.department_index,
            snapshot.system_index,
        )
    if scope == "site":
        if snapshot.site_index is None:
            return None
        return HoldSelection(scope, "SITE", snapshot.site_index)
    if snapshot.channel_index is None:
        return None
    if snapshot.channel_kind == "TGID":
        target: NavigationTarget = "TGID"
    elif snapshot.channel_kind == "ConvFrequency":
        target = "CFREQ"
    else:
        return None
    return HoldSelection(scope, target, snapshot.channel_index)


def channel_navigation(
    snapshot: RadioStateSnapshot,
) -> tuple[NavigationTarget, int] | None:
    """Return a documented channel navigation target when PSI provides an index."""

    if snapshot.channel_index is None:
        return None
    if snapshot.channel_kind == "TGID":
        return "TGID", snapshot.channel_index
    if snapshot.channel_kind == "ConvFrequency":
        return "CFREQ", snapshot.channel_index
    return None
