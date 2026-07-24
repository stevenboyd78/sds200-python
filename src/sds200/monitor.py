from __future__ import annotations

import sys
from datetime import UTC, datetime
from threading import RLock
from typing import TextIO

from .state import RadioStateSnapshot


def _display(value: object | None) -> str:
    return "—" if value is None or value == "" else str(value)


def _signal_bar(signal: int | None, width: int = 5) -> str:
    level = 0 if signal is None else max(0, min(signal, width))
    return "█" * level + "░" * (width - level)


def format_snapshot(
    snapshot: RadioStateSnapshot,
    endpoint: str,
    *,
    observed_at: datetime | None = None,
) -> str:
    now = observed_at or datetime.now(UTC)
    timestamp = now.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    signal = f"{_signal_bar(snapshot.signal)} ({_display(snapshot.signal)})"

    return "\n".join(
        (
            "SDS-series Live Monitor",
            "=" * 64,
            f"Endpoint    : {endpoint}",
            f"Updated     : {timestamp}",
            f"Mode        : {_display(snapshot.mode)}",
            f"System      : {_display(snapshot.system)}",
            f"Department  : {_display(snapshot.department)}",
            f"Site        : {_display(snapshot.site)}",
            f"Channel     : {_display(snapshot.channel)}",
            f"Frequency   : {_display(snapshot.frequency)}",
            f"Modulation  : {_display(snapshot.modulation)}",
            f"Service     : {_display(snapshot.service_type)}",
            f"Talkgroup   : {_display(snapshot.talkgroup_id)}",
            f"Unit ID     : {_display(snapshot.unit_id)}",
            f"Signal      : {signal}",
            f"RSSI        : {_display(snapshot.rssi)}",
            f"Volume      : {_display(snapshot.volume)}",
            f"Squelch     : {_display(snapshot.squelch)}",
            f"Mute        : {_display(snapshot.mute)}",
            f"Recording   : {_display(snapshot.recording)}",
            "",
            "Press Ctrl-C to stop.",
        )
    )


class TerminalMonitor:
    def __init__(self, *, stream: TextIO | None = None, clear: bool = True) -> None:
        self.stream: TextIO = stream or sys.stdout
        self.clear = clear
        self._lock = RLock()

    def render(self, snapshot: RadioStateSnapshot, endpoint: str) -> None:
        output = format_snapshot(snapshot, endpoint)
        prefix = "\x1b[2J\x1b[H" if self.clear else ""
        with self._lock:
            self.stream.write(prefix + output + "\n")
            self.stream.flush()
