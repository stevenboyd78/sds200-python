from __future__ import annotations

from dataclasses import dataclass, fields
from threading import RLock

from .models import ScannerInfo


@dataclass(frozen=True, slots=True)
class RadioStateSnapshot:
    mode: str | None = None
    screen: str | None = None
    system: str | None = None
    department: str | None = None
    site: str | None = None
    system_index: int | None = None
    system_hold: str | None = None
    department_index: int | None = None
    department_hold: str | None = None
    site_index: int | None = None
    site_hold: str | None = None
    channel: str | None = None
    channel_index: int | None = None
    channel_kind: str | None = None
    channel_hold: str | None = None
    frequency: str | None = None
    modulation: str | None = None
    service_type: str | None = None
    talkgroup_id: str | None = None
    unit_id: str | None = None
    volume: int | None = None
    squelch: int | None = None
    signal: int | None = None
    rssi: float | None = None
    p25_status: str | None = None
    mute: str | None = None
    recording: str | None = None


@dataclass(frozen=True, slots=True)
class StateChange:
    previous: RadioStateSnapshot
    current: RadioStateSnapshot
    fields: frozenset[str]

    def changed(self, field: str) -> bool:
        return field in self.fields


def snapshot_from_scanner_info(info: ScannerInfo) -> RadioStateSnapshot:
    """Convert parsed GSI or PSI XML into the shared immutable state snapshot."""

    return RadioStateSnapshot(
        mode=info.mode,
        screen=info.screen,
        system=info.system,
        department=info.department,
        site=info.site,
        system_index=info.system_index,
        system_hold=info.system_hold,
        department_index=info.department_index,
        department_hold=info.department_hold,
        site_index=info.site_index,
        site_hold=info.site_hold,
        channel=info.channel,
        channel_index=info.channel_index,
        channel_kind=info.channel_kind,
        channel_hold=info.channel_hold,
        frequency=info.frequency,
        modulation=info.modulation,
        service_type=info.service_type,
        talkgroup_id=info.talkgroup_id,
        unit_id=info.unit_id,
        volume=info.volume,
        squelch=info.squelch,
        signal=info.signal,
        rssi=info.rssi,
        p25_status=info.p25_status,
        mute=info.mute,
        recording=info.recording,
    )


class RadioState:
    def __init__(self) -> None:
        self._lock = RLock()
        self._snapshot = RadioStateSnapshot()

    @property
    def snapshot(self) -> RadioStateSnapshot:
        with self._lock:
            return self._snapshot

    def update(self, info: ScannerInfo) -> StateChange | None:
        current = snapshot_from_scanner_info(info)
        with self._lock:
            previous = self._snapshot
            changed = frozenset(
                field.name
                for field in fields(RadioStateSnapshot)
                if getattr(previous, field.name) != getattr(current, field.name)
            )
            self._snapshot = current

        if not changed:
            return None
        return StateChange(previous=previous, current=current, fields=changed)
