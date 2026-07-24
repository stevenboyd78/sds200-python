from datetime import UTC, datetime
from io import StringIO

from sds200.monitor import TerminalMonitor, format_snapshot
from sds200.state import RadioStateSnapshot


def test_format_snapshot_includes_live_fields() -> None:
    snapshot = RadioStateSnapshot(
        mode="Trunk Scan",
        system="Utah Communications Authority (P25)",
        channel="Patch 65132",
        frequency="769.431250MHz",
        signal=5,
        volume=10,
        squelch=2,
    )

    rendered = format_snapshot(
        snapshot,
        "serial:///dev/ttyACM0",
        observed_at=datetime(2026, 7, 23, 12, 0, tzinfo=UTC),
    )

    assert "SDS-series Live Monitor" in rendered
    assert "Patch 65132" in rendered
    assert "769.431250MHz" in rendered
    assert "█████ (5)" in rendered


def test_terminal_monitor_can_append_without_ansi_clear() -> None:
    stream = StringIO()
    monitor = TerminalMonitor(stream=stream, clear=False)

    monitor.render(RadioStateSnapshot(channel="Dispatch"), "fake://scanner")

    assert not stream.getvalue().startswith("\x1b")
    assert "Dispatch" in stream.getvalue()
