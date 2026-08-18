from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from sds200.exceptions import (
    CaptureFormatError,
    CommandRejectedError,
    ReplayMismatchError,
    ScannerConnectionError,
    ScannerRecordingControlError,
)
from sds200.models import AnalysisMode
from sds200.radio import SDSScanner
from sds200.replay import (
    CaptureEvent,
    RecordingTransport,
    ReplayTransport,
    load_capture,
    write_capture,
)

from .fakes import FakeTransport

FIXTURES = Path(__file__).parent / "fixtures" / "replay"
ADVANCED_PROTOCOL_FIXTURES = Path(__file__).parent / "fixtures" / "advanced_protocol"


def test_hardware_derived_sds100_info_capture_replays_typed_api() -> None:
    with SDSScanner.replay(
        FIXTURES / "sds100-info.jsonl",
        expected_model="SDS100",
    ) as radio:
        assert radio.get_model() == "SDS100"
        assert radio.get_firmware() == "Version 1.26.01"
        assert radio.get_volume() == 10
        assert radio.get_squelch() == 2


def test_replay_rejects_a_command_sequence_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "mismatch.jsonl"
    write_capture(
        path,
        (
            CaptureEvent(direction="tx", data="MDL"),
            CaptureEvent(direction="rx", data="MDL,SDS100"),
        ),
    )
    transport = ReplayTransport.from_file(path)
    transport.start(lambda line: None)

    with pytest.raises(ReplayMismatchError, match="expected command 'MDL'"):
        transport.write_command("VER")


def test_capture_loader_rejects_unknown_schema(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text('{"schema":"other","version":1,"endpoint":"x"}\n')

    with pytest.raises(CaptureFormatError, match="unsupported schema"):
        load_capture(path)


def test_recording_transport_creates_replayable_session(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    fake = FakeTransport(endpoint="fake://scanner")
    transport = RecordingTransport(fake, path)
    radio = SDSScanner.from_transport(transport, expected_model="SDS100")

    with radio:
        def respond() -> None:
            while fake.writes != ["MDL"]:
                time.sleep(0.005)
            fake.feed_line("MDL,SDS100")

        thread = threading.Thread(target=respond, daemon=True)
        thread.start()
        assert radio.get_model(timeout=1.0) == "SDS100"
        thread.join(timeout=1.0)

    capture = load_capture(path)
    assert capture.endpoint == "fake://scanner"
    assert [event.direction for event in capture.events] == [
        "connection",
        "tx",
        "rx",
        "connection",
    ]

    with SDSScanner.replay(path, expected_model="SDS100") as replayed:
        assert replayed.get_model(timeout=1.0) == "SDS100"


def test_recording_transport_applies_literal_redactions(tmp_path: Path) -> None:
    path = tmp_path / "redacted.jsonl"
    fake = FakeTransport(endpoint="udp://192.168.0.251:50536")
    transport = RecordingTransport(
        fake,
        path,
        redactions=("192.168.0.251", "Private System"),
    )
    received: list[str] = []
    transport.start(received.append)
    transport.write_command("QSH,192.168.0.251")
    fake.feed_line('GSI,<Property Name="Private System" />')
    transport.stop()

    text = path.read_text(encoding="utf-8")
    assert "192.168.0.251" not in text
    assert "Private System" not in text
    assert "<redacted:1>" in text
    assert "<redacted:2>" in text


def test_write_capture_produces_json_lines(tmp_path: Path) -> None:
    path = tmp_path / "fixture.jsonl"
    write_capture(
        path,
        [CaptureEvent(direction="tx", data="MDL")],
        endpoint="fixture://unit",
    )

    lines = path.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["schema"] == "sds200.capture"
    assert json.loads(lines[1]) == {
        "data": "MDL",
        "delay_ms": 0.0,
        "direction": "tx",
    }


def test_replay_connection_events_can_disconnect_before_a_command(tmp_path: Path) -> None:
    path = tmp_path / "disconnect.jsonl"
    write_capture(
        path,
        (
            CaptureEvent(direction="connection", connected=False),
            CaptureEvent(direction="tx", data="MDL"),
            CaptureEvent(direction="rx", data="MDL,SDS100"),
        ),
    )
    transport = ReplayTransport.from_file(path)
    transport.start(lambda line: None)

    with pytest.raises(ScannerConnectionError, match="disconnected"):
        transport.write_command("MDL")


def test_replay_preserves_multiline_gsi_parsing() -> None:
    with SDSScanner.replay(FIXTURES / "sds100-scanner-info.jsonl") as radio:
        info = radio.get_scanner_info(timeout=1.0)

    assert info.system == "Example P25 System"
    assert info.department == "Example Department"
    assert info.site == "Example Simulcast"
    assert info.frequency == "769.431250MHz"
    assert info.rssi == -86
    assert info.battery is None


def test_replay_executes_lossless_glt_favorites_retrieval() -> None:
    with SDSScanner.replay(
        ADVANCED_PROTOCOL_FIXTURES / "synthetic-glt-fl.jsonl"
    ) as radio:
        response = radio.get_glt_favorites(timeout=1.0)

    favorites = response.records_by_tag("FL")
    assert [record.attributes["Name"] for record in favorites] == [
        "Example Favorites A",
        "Example Favorites B",
    ]
    assert favorites[0].attributes["FutureAttr"] == "preserve-me"


def test_replay_executes_lossless_msi_retrieval() -> None:
    with SDSScanner.replay(
        ADVANCED_PROTOCOL_FIXTURES / "synthetic-msi-retrieval.jsonl"
    ) as radio:
        response = radio.get_msi(timeout=1.0)

    assert response.command == "MSI"
    assert response.root_attributes["FutureRoot"] == "keep-root"
    assert [
        record.attributes["SyntheticId"]
        for record in response.records_by_tag("SyntheticRecord")
    ] == ["first", "second"]
    assert response.records_by_tag("FutureRecord")[0].attributes["FutureNested"] == (
        "keep-nested"
    )


def test_replay_projects_documented_msi_menu_shapes_losslessly() -> None:
    with SDSScanner.replay(
        ADVANCED_PROTOCOL_FIXTURES / "synthetic-msi-menu-projection.jsonl"
    ) as radio:
        before = radio.state.snapshot
        select = radio.get_msi(timeout=1.0)
        menu_input = radio.get_msi(timeout=1.0)
        location = radio.get_msi(timeout=1.0)
        error = radio.get_msi(timeout=1.0)
        after = radio.state.snapshot

    select_projection = select.menu_projection
    assert select_projection.name == "Synthetic Select"
    assert select_projection.index == "select-menu"
    assert select_projection.menu_type == "TypeSelect"
    assert select_projection.value == "select-value"
    assert select_projection.selected == "selected-opaque"
    assert [item.name for item in select_projection.menu_items] == ["Alpha", "Beta"]
    assert [item.index for item in select_projection.menu_items] == ["item-a", "item-b"]
    assert select_projection.menu_items[0].attributes["FutureItem"] == "keep-item-a"
    assert select_projection.records == select.records
    assert select.records_by_tag("FutureMenuNode")[0].attributes["FutureValue"] == (
        "keep-future"
    )

    input_projection = menu_input.menu_projection
    assert input_projection.menu_type == "TypeInput"
    assert input_projection.menu_inputs[0].max_length == "64"
    assert input_projection.menu_inputs[0].enable_keys == "ABC123"
    assert input_projection.menu_inputs[0].added_information == "Synthetic information"
    assert input_projection.menu_inputs[0].attributes["FutureInput"] == "keep-input"

    location_projection = location.menu_projection
    assert location_projection.menu_type == "TypeLocation"
    assert location_projection.menu_locations[0].max_length == "99"
    assert location_projection.menu_locations[0].enable_keys == "0123456789.-"
    assert location_projection.menu_locations[0].is_latitude == "1"
    assert location_projection.menu_locations[0].attributes["FutureLocation"] == (
        "keep-location"
    )

    error_projection = error.menu_projection
    assert error_projection.menu_type == "TypeError"
    assert error_projection.error_messages[0].text == "Synthetic error"
    assert error_projection.error_messages[0].scan_button == "0"
    assert error_projection.error_messages[0].attributes["FutureError"] == "keep-error"

    assert after == before


def test_replay_executes_exact_indexed_mnu_controls() -> None:
    requests = (
        ("SCAN_SYSTEM", "000001"),
        ("SCAN_DEPARTMENT", "000002"),
        ("SCAN_SITE", "000003"),
        ("SCAN_CHANNEL", "000004"),
        ("SRCH_RANGE", "000005"),
        ("FTO_CHANNEL", "000006"),
    )

    with SDSScanner.replay(
        ADVANCED_PROTOCOL_FIXTURES / "synthetic-mnu-indexed.jsonl"
    ) as radio:
        before = radio.state.snapshot
        for menu_id, index in requests:
            radio.open_indexed_menu(menu_id, index, timeout=1.0)  # type: ignore[arg-type]
        after = radio.state.snapshot

    assert after == before


def test_replay_executes_exact_system_status_start_acknowledgement() -> None:
    with SDSScanner.replay(
        ADVANCED_PROTOCOL_FIXTURES / "synthetic-ast-system-status.jsonl"
    ) as radio:
        before = radio.state.snapshot
        result = radio.start_system_status_analysis(7, timeout=1.0)
        after = radio.state.snapshot

    assert result is None
    assert after == before


def test_replay_executes_current_activity_apr_lcn_monitor_apr() -> None:
    with SDSScanner.replay(
        ADVANCED_PROTOCOL_FIXTURES / "synthetic-ast-apr.jsonl"
    ) as radio:
        current = radio.start_current_activity_analysis(7, timeout=1.0)
        radio.pause_resume_analysis(AnalysisMode.CURRENT_ACTIVITY, timeout=1.0)
        lcn = radio.start_lcn_monitor_analysis(9, timeout=1.0)
        radio.pause_resume_analysis(AnalysisMode.LCN_MONITOR, timeout=1.0)

    assert [record.attributes["TGID"] for record in current.records_by_tag(
        "CurrentActivity"
    )] == ["1001", "1002"]
    assert current.root_attributes["FutureRoot"] == "preserve-root"
    assert [record.attributes["ReceiveStaus"] for record in lcn.records_by_tag(
        "LcnMonitor"
    )] == ["Active", "Idle"]
    assert lcn.records_by_tag("FutureRecord")[0].attributes["FutureValue"] == (
        "preserve-record"
    )


def test_replay_executes_exact_favorites_quick_key_read_and_write() -> None:
    read_states = tuple(index % 3 for index in range(100))
    write_states = tuple((index + 2) % 3 for index in range(100))

    with SDSScanner.replay(
        ADVANCED_PROTOCOL_FIXTURES / "synthetic-fqk.jsonl"
    ) as radio:
        response = radio.get_favorites_quick_keys(timeout=1.0)
        radio.set_favorites_quick_keys(write_states, timeout=1.0)

    assert len(response.states) == 100
    assert tuple(int(state) for state in response.states) == read_states
    assert response.packet.fields == tuple(str(state) for state in read_states)


def test_replay_executes_scanner_recording_control_and_errors() -> None:
    expected_errors = (
        ("0001", "FILE ACCESS"),
        ("0002", "LOW BATTERY"),
        ("0003", "SESSION OVER LIMIT"),
        ("0004", "RTC LOST"),
        ("9999", None),
    )

    with SDSScanner.replay(
        ADVANCED_PROTOCOL_FIXTURES / "synthetic-urc.jsonl"
    ) as radio:
        stopped = radio.get_scanner_recording_status(timeout=1.0)
        radio.set_scanner_recording_status(1, timeout=1.0)
        recording = radio.get_scanner_recording_status(timeout=1.0)
        radio.set_scanner_recording_status(0, timeout=1.0)

        observed_errors: list[tuple[str, str | None]] = []
        for code, reason in expected_errors:
            with pytest.raises(ScannerRecordingControlError) as caught:
                radio.set_scanner_recording_status(1, timeout=1.0)
            observed_errors.append((caught.value.code, caught.value.reason))
            assert (caught.value.code, caught.value.reason) == (code, reason)

    assert stopped.status.value == 0
    assert stopped.packet.raw == "URC,0"
    assert recording.status.value == 1
    assert recording.packet.raw == "URC,1"
    assert observed_errors == list(expected_errors)


def test_replay_preserves_generic_command_rejection(tmp_path: Path) -> None:
    path = tmp_path / "rejected.jsonl"
    write_capture(
        path,
        (
            CaptureEvent(direction="tx", data="GCS"),
            CaptureEvent(direction="rx", data="ERR"),
        ),
    )

    with (
        SDSScanner.replay(path) as radio,
        pytest.raises(CommandRejectedError, match="rejected GCS command: ERR"),
    ):
        radio.command("GCS", timeout=1.0)


def test_radio_capture_path_wraps_custom_transport(tmp_path: Path) -> None:
    path = tmp_path / "wrapped.jsonl"
    fake = FakeTransport()
    radio = SDSScanner.from_transport(fake, capture_path=path)

    assert isinstance(radio.transport, RecordingTransport)
    with radio:
        pass
    assert load_capture(path).events[0].direction == "connection"


def test_capture_event_rejects_invalid_runtime_direction() -> None:
    with pytest.raises(ValueError, match="Invalid capture event direction"):
        CaptureEvent(direction="invalid", data="MDL")  # type: ignore[arg-type]


def test_capture_loader_rejects_boolean_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "bad-version.jsonl"
    path.write_text(
        '{"schema":"sds200.capture","version":true,"endpoint":"x"}\n',
        encoding="utf-8",
    )

    with pytest.raises(CaptureFormatError, match="unsupported version"):
        load_capture(path)
