from __future__ import annotations

import threading
from pathlib import Path

from sds200.radio import SDSScanner
from sds200.state import RadioStateSnapshot
from sds200.tui_controls import (
    ControlRequest,
    ControlWorker,
    HoldSelection,
    channel_navigation,
    hold_selection,
    scanner_index_available,
)

FIXTURE = Path(__file__).parent / "fixtures" / "replay" / "sds100-tui-controls.jsonl"


def test_channel_navigation_uses_documented_channel_indexes() -> None:
    assert channel_navigation(
        RadioStateSnapshot(channel_kind="TGID", channel_index=400)
    ) == ("TGID", 400)
    assert channel_navigation(
        RadioStateSnapshot(channel_kind="ConvFrequency", channel_index=500)
    ) == ("CFREQ", 500)
    assert channel_navigation(
        RadioStateSnapshot(channel_kind="SrchFrequency", channel_index=600)
    ) is None
    assert channel_navigation(RadioStateSnapshot(channel_kind="TGID")) is None


def test_hold_selection_uses_documented_scope_indexes() -> None:
    snapshot = RadioStateSnapshot(
        system_index=100,
        department_index=200,
        site_index=300,
        channel_kind="TGID",
        channel_index=400,
    )

    assert hold_selection(snapshot, "system") == HoldSelection("system", "SYS", 100)
    assert hold_selection(snapshot, "department") == HoldSelection(
        "department", "DEPT", 200, 100
    )
    assert hold_selection(snapshot, "site") == HoldSelection("site", "SITE", 300)
    assert hold_selection(snapshot, "channel") == HoldSelection(
        "channel", "TGID", 400
    )
    assert hold_selection(RadioStateSnapshot(), "system") is None
    assert hold_selection(RadioStateSnapshot(), "department") is None
    assert hold_selection(RadioStateSnapshot(), "site") is None
    assert hold_selection(RadioStateSnapshot(), "channel") is None


def test_selection_helpers_reject_unavailable_scanner_indexes() -> None:
    unavailable = (1 << 32) - 1

    assert scanner_index_available(0) is True
    assert scanner_index_available(unavailable - 1) is True
    assert scanner_index_available(None) is False
    assert scanner_index_available(-1) is False
    assert scanner_index_available(unavailable) is False
    assert scanner_index_available(unavailable + 1) is False

    assert hold_selection(
        RadioStateSnapshot(system_index=unavailable),
        "system",
    ) is None
    assert hold_selection(
        RadioStateSnapshot(system_index=100, department_index=unavailable),
        "department",
    ) is None
    assert hold_selection(
        RadioStateSnapshot(system_index=unavailable, department_index=200),
        "department",
    ) is None
    assert hold_selection(
        RadioStateSnapshot(site_index=unavailable),
        "site",
    ) is None
    unavailable_channel = RadioStateSnapshot(
        channel_kind="TGID",
        channel_index=unavailable,
    )
    assert hold_selection(unavailable_channel, "channel") is None
    assert channel_navigation(unavailable_channel) is None


def test_control_worker_serializes_commands_and_reports_results() -> None:
    first_started = threading.Event()
    release_first = threading.Event()
    completed = threading.Event()
    calls: list[str] = []
    results: list[tuple[str, Exception | None]] = []

    def finish(request: ControlRequest, error: Exception | None) -> None:
        results.append((request.label, error))
        if len(results) == 2:
            completed.set()

    def first() -> None:
        calls.append("first-start")
        first_started.set()
        assert release_first.wait(1.0)
        calls.append("first-end")

    def second() -> None:
        calls.append("second")

    worker = ControlWorker(finish)
    worker.start()
    worker.submit(ControlRequest("First", first))
    worker.submit(ControlRequest("Second", second))

    assert first_started.wait(1.0)
    assert calls == ["first-start"]
    release_first.set()
    assert completed.wait(1.0)
    worker.stop()

    assert calls == ["first-start", "first-end", "second"]
    assert results == [("First", None), ("Second", None)]
    assert not worker.alive


def test_control_worker_reports_command_errors() -> None:
    completed = threading.Event()
    results: list[tuple[str, Exception | None]] = []

    def finish(request: ControlRequest, error: Exception | None) -> None:
        results.append((request.label, error))
        completed.set()

    def fail() -> None:
        raise RuntimeError("scanner rejected command")

    worker = ControlWorker(finish)
    worker.start()
    worker.submit(ControlRequest("Hold channel", fail))
    assert completed.wait(1.0)
    worker.stop()

    assert results[0][0] == "Hold channel"
    assert isinstance(results[0][1], RuntimeError)
    assert str(results[0][1]) == "scanner rejected command"


def test_replay_fixture_accepts_tui_control_sequence() -> None:
    with SDSScanner.replay(FIXTURE, expected_model="SDS100") as radio:
        assert radio.get_model() == "SDS100"
        assert radio.get_firmware() == "Version 1.26.01"
        initial = radio.get_scanner_info()
        assert initial.channel_index == 400
        assert initial.channel_kind == "TGID"

        with radio.scanner_info_push(100) as first:
            assert first.channel_index == 400
            radio.hold("TGID", 400)
            assert radio.state.snapshot.channel_hold == "On"
            radio.hold("SYS", 100)
            assert radio.state.snapshot.system_hold == "On"
            radio.hold("DEPT", 200, 100)
            assert radio.state.snapshot.department_hold == "On"
            radio.hold("SITE", 300)
            assert radio.state.snapshot.site_hold == "On"
            radio.next("TGID", 400)
            radio.previous("TGID", 400)
            radio.set_volume(11)
            radio.set_squelch(3)
