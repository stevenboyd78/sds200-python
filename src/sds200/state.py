from __future__ import annotations

from dataclasses import dataclass
from threading import RLock

from .models import ScannerInfo


@dataclass(frozen=True, slots=True)
class RadioStateSnapshot:
    mode: str | None = None
    screen: str | None = None
    system: str | None = None
    department: str | None = None
    channel: str | None = None
    frequency: str | None = None
    modulation: str | None = None
    signal: int | None = None


class RadioState:
    def __init__(self) -> None:
        self._lock = RLock()
        self._snapshot = RadioStateSnapshot()

    @property
    def snapshot(self) -> RadioStateSnapshot:
        with self._lock:
            return self._snapshot

    def update(self, info: ScannerInfo) -> tuple[RadioStateSnapshot, set[str]]:
        new = RadioStateSnapshot(
            mode=info.mode,
            screen=info.screen,
            system=info.system,
            department=info.department,
            channel=info.channel,
            frequency=info.frequency,
            modulation=info.modulation,
            signal=info.signal,
        )
        with self._lock:
            old = self._snapshot
            changed = {
                name
                for name in RadioStateSnapshot.__dataclass_fields__
                if getattr(old, name) != getattr(new, name)
            }
            self._snapshot = new
        return new, changed
