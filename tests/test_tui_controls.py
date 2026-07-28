from __future__ import annotations

import threading
from pathlib import Path

from sds200.radio import SDSScanner
from sds200.state import RadioStateSnapshot
from sds200.tui_controls import ControlRequest, ControlWorker, channel_navigation

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
            radio.next("TGID", 400)
            radio.previous("TGID", 400)
            radio.set_volume(11)
            radio.set_squelch(3)
