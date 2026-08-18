import threading
import time

import pytest

from sds200.analysis_subscriptions import AnalysisSubscriptionClosed
from sds200.exceptions import (
    CommandRejectedError,
    CommandTimeoutError,
    ProtocolError,
    ScannerRecordingControlError,
    UnsupportedScannerFeatureError,
    UnsupportedScannerModelError,
)
from sds200.fallback import FallbackTransport, TransportCandidate
from sds200.models import (
    AnalysisMode,
    AnalysisResponse,
    FavoritesQuickKeyState,
    MsiResponse,
    RadioEvent,
    ScannerInfo,
    ScannerRecordingStatus,
)
from sds200.profiles import ConnectionProfile
from sds200.radio import SDS200
from sds200.transport import TransportDiagnostic

from .fakes import FakeSerial, FakeTransport


def test_command_is_cr_terminated_and_matches_response() -> None:
    fake = FakeSerial()
    radio = SDS200("/dev/fake", reconnect=False, serial_factory=lambda **kwargs: fake)

    with radio:
        def respond() -> None:
            while not fake.writes:
                time.sleep(0.005)
            fake.feed(b"MDL,SDS200\r")

        thread = threading.Thread(target=respond)
        thread.start()
        assert radio.get_model(timeout=1.0) == "SDS200"
        thread.join()

    assert fake.writes == [b"MDL\r"]


def test_serial_msi_retrieval_is_lossless_and_state_neutral() -> None:
    fake = FakeSerial()
    radio = SDS200("/dev/fake", reconnect=False, serial_factory=lambda **kwargs: fake)
    initial_state = radio.state.snapshot

    with radio:
        def respond() -> None:
            while fake.writes != [b"MSI\r"]:
                time.sleep(0.005)
            fake.feed(
                b'MSI,<XML>,\r'
                b'<MSI FutureRoot="keep-root">\r'
                b'<SyntheticRecord SyntheticId="first" FutureAttr="keep-first" />\r'
                b'<Container><FutureRecord Value="nested" '
                b'FutureNested="keep-nested" /></Container>\r'
                b'<SyntheticRecord SyntheticId="second" />\r'
                b'</MSI>\r'
            )

        thread = threading.Thread(target=respond)
        thread.start()
        response = radio.get_msi(timeout=1.0)
        thread.join(timeout=1.0)

    assert isinstance(response, MsiResponse)
    assert response.command == "MSI"
    assert response.root_attributes["FutureRoot"] == "keep-root"
    assert [record.tag for record in response.records] == [
        "SyntheticRecord",
        "Container",
        "FutureRecord",
        "SyntheticRecord",
    ]
    assert [
        record.attributes["SyntheticId"]
        for record in response.records_by_tag("SyntheticRecord")
    ] == ["first", "second"]
    assert response.records_by_tag("FutureRecord")[0].attributes == {
        "Value": "nested",
        "FutureNested": "keep-nested",
    }
    assert radio.state.snapshot == initial_state
    assert fake.writes == [b"MSI\r"]


def test_msi_retrieval_fails_closed_before_direct_udp_write() -> None:
    transport = FakeTransport("udp://scanner")
    radio = SDS200.from_transport(transport)

    with pytest.raises(
        UnsupportedScannerFeatureError,
        match="MSI retrieval is unavailable on UDP and fallback control transports",
    ):
        radio.get_msi(timeout=1.0)

    assert transport.writes == []


def test_msi_retrieval_fails_closed_through_recording_udp_wrapper(tmp_path) -> None:
    transport = FakeTransport("udp://scanner")
    radio = SDS200.from_transport(
        transport,
        capture_path=tmp_path / "capture.jsonl",
    )

    with pytest.raises(
        UnsupportedScannerFeatureError,
        match="MSI retrieval is unavailable on UDP and fallback control transports",
    ):
        radio.get_msi(timeout=1.0)

    assert transport.writes == []


def test_msi_retrieval_fails_closed_before_fallback_write() -> None:
    preferred = FakeTransport("fake://serial")
    backup = FakeTransport("udp://scanner")
    fallback = FallbackTransport(
        (
            TransportCandidate("serial", preferred.endpoint, lambda: preferred),
            TransportCandidate("network", backup.endpoint, lambda: backup),
        )
    )
    radio = SDS200.from_transport(fallback)

    with pytest.raises(
        UnsupportedScannerFeatureError,
        match="MSI retrieval is unavailable on UDP and fallback control transports",
    ):
        radio.get_msi(timeout=1.0)

    assert preferred.writes == []
    assert backup.writes == []


def test_favorites_quick_keys_high_level_read_is_typed_and_exact() -> None:
    transport = FakeTransport()
    radio = SDS200.from_transport(transport)
    fields = tuple(str(index % 3) for index in range(100))

    with radio:
        def respond() -> None:
            while transport.writes != ["FQK"]:
                time.sleep(0.005)
            transport.feed_line("FQK," + ",".join(fields))

        thread = threading.Thread(target=respond)
        thread.start()
        result = radio.get_favorites_quick_keys(timeout=1.0)
        thread.join(timeout=1.0)

    assert transport.writes == ["FQK"]
    assert result.states[:3] == (
        FavoritesQuickKeyState.NONEXISTENT,
        FavoritesQuickKeyState.DISABLED,
        FavoritesQuickKeyState.ENABLED,
    )
    assert isinstance(result.states, tuple)
    assert result.packet.raw == "FQK," + ",".join(fields)


def test_favorites_quick_keys_high_level_write_is_exact() -> None:
    transport = FakeTransport()
    radio = SDS200.from_transport(transport)
    states = tuple(index % 3 for index in range(100))
    wire = "FQK," + ",".join(str(state) for state in states)

    with radio:
        def respond() -> None:
            while transport.writes != [wire]:
                time.sleep(0.005)
            transport.feed_line("FQK,OK")

        thread = threading.Thread(target=respond)
        thread.start()
        radio.set_favorites_quick_keys(states, timeout=1.0)
        thread.join(timeout=1.0)

    assert transport.writes == [wire]


def test_favorites_quick_keys_rejection_is_correlated_to_fqk() -> None:
    transport = FakeTransport()
    radio = SDS200.from_transport(transport)

    with radio:
        def respond() -> None:
            while not transport.writes:
                time.sleep(0.005)
            transport.feed_line("OTHER,NG")
            transport.feed_line("FQK,NG")

        thread = threading.Thread(target=respond)
        thread.start()
        with pytest.raises(CommandRejectedError, match="rejected FQK"):
            radio.set_favorites_quick_keys([1] * 100, timeout=1.0)
        thread.join(timeout=1.0)


def test_scanner_recording_status_high_level_read_is_typed_and_exact() -> None:
    transport = FakeTransport()
    radio = SDS200.from_transport(transport)

    with radio:
        def respond() -> None:
            while transport.writes != ["URC"]:
                time.sleep(0.005)
            transport.feed_line("URC,1")

        thread = threading.Thread(target=respond)
        thread.start()
        response = radio.get_scanner_recording_status(timeout=1.0)
        thread.join(timeout=1.0)

    assert transport.writes == ["URC"]
    assert response.status is ScannerRecordingStatus.RECORDING
    assert response.packet.raw == "URC,1"


@pytest.mark.parametrize(
    ("status", "wire"),
    [
        (ScannerRecordingStatus.RECORDING, "URC,1"),
        (ScannerRecordingStatus.STOPPED, "URC,0"),
    ],
)
def test_scanner_recording_status_high_level_write_is_exact(
    status: ScannerRecordingStatus, wire: str
) -> None:
    transport = FakeTransport()
    radio = SDS200.from_transport(transport)

    with radio:
        def respond() -> None:
            while transport.writes != [wire]:
                time.sleep(0.005)
            transport.feed_line("URC,OK")

        thread = threading.Thread(target=respond)
        thread.start()
        radio.set_scanner_recording_status(status, timeout=1.0)
        thread.join(timeout=1.0)

    assert transport.writes == [wire]


def test_scanner_recording_error_is_correlated_and_preserved() -> None:
    transport = FakeTransport()
    radio = SDS200.from_transport(transport)

    with radio:
        def respond() -> None:
            while not transport.writes:
                time.sleep(0.005)
            transport.feed_line("OTHER,ERR,0002")
            transport.feed_line("URC,ERR,0002")

        thread = threading.Thread(target=respond)
        thread.start()
        with pytest.raises(ScannerRecordingControlError) as caught:
            radio.set_scanner_recording_status(1, timeout=1.0)
        thread.join(timeout=1.0)

    assert transport.writes == ["URC,1"]
    assert caught.value.code == "0002"
    assert caught.value.reason == "LOW BATTERY"


def test_set_volume_range() -> None:
    radio = SDS200("/dev/fake", reconnect=False, serial_factory=lambda **kwargs: FakeSerial())

    try:
        radio.set_volume(30)
    except ValueError as exc:
        assert "0 and 29" in str(exc)
    else:
        raise AssertionError("Expected ValueError")


def test_health_check_returns_round_trip_metadata() -> None:
    transport = FakeTransport()
    radio = SDS200.from_transport(transport)

    with radio:
        def respond() -> None:
            while transport.writes != ["MDL"]:
                time.sleep(0.005)
            transport.feed_line("MDL,SDS200")
            while transport.writes != ["MDL", "VER"]:
                time.sleep(0.005)
            transport.feed_line("VER,Version 1.26.01")

        thread = threading.Thread(target=respond)
        thread.start()
        health = radio.health_check(timeout=1.0)
        thread.join(timeout=1.0)

    assert health.endpoint == "fake://scanner"
    assert health.model == "SDS200"
    assert health.firmware == "Version 1.26.01"
    assert health.latency_ms >= 0


def test_health_snapshot_tracks_connection_and_response_times() -> None:
    transport = FakeTransport()
    radio = SDS200.from_transport(transport)

    with radio:
        transport.feed_line("MDL,SDS200")
        snapshot = radio.health_snapshot()

    assert snapshot.connection_events >= 1
    assert snapshot.last_connected_at is not None
    assert snapshot.last_response_at is not None
    assert snapshot.model == "SDS200"


def test_fallback_profile_builds_preferred_transport_order() -> None:
    profile = ConnectionProfile.fallback(
        "home",
        port="/dev/fake",
        host="192.0.2.25",
        preference="network",
    )
    radio = SDS200.from_profile(profile, preference="serial")

    assert isinstance(radio.transport, FallbackTransport)
    assert radio.transport.candidates[0].name == "serial"


def test_radio_emits_structured_connection_events() -> None:
    transport = FakeTransport()
    radio = SDS200.from_transport(transport)
    events: list[RadioEvent] = []
    radio.on_event(events.append)

    radio.connect()
    transport.set_connected(False)
    radio.close()

    assert [event.kind for event in events[:2]] == [
        "connection.connected",
        "connection.disconnected",
    ]
    assert events[0].data["connected"] is True
    assert events[1].data["connected"] is False


def test_health_history_records_checks() -> None:
    transport = FakeTransport()
    radio = SDS200.from_transport(transport, health_history_limit=2)
    radio.connect()

    transport.feed_line("MDL,SDS200")
    transport.feed_line("VER,1.26.01")
    radio.health_snapshot()
    radio.health_snapshot(error="temporary")
    radio.health_snapshot()

    summary = radio.health_summary()
    radio.close()

    assert summary.samples == 2
    assert summary.degraded_samples == 1
    assert summary.healthy_samples == 1


def test_sds150_model_is_normalized_and_charge_status_is_parsed() -> None:
    transport = FakeTransport()
    radio = SDS200.from_transport(transport, expected_model="SDS150")

    with radio:
        def respond() -> None:
            while transport.writes != ["MDL"]:
                time.sleep(0.005)
            transport.feed_line("MDL,SDS150GBT")
            while transport.writes != ["MDL", "GCS"]:
                time.sleep(0.005)
            transport.feed_line(
                "GCS,CST=4,VOLT=4184mV:100%,CURR=0000mA,TEMP= 27.65C"
            )

        thread = threading.Thread(target=respond)
        thread.start()
        status = radio.get_charge_status(timeout=1.0)
        thread.join(timeout=1.0)

    assert radio.model == "SDS150"
    assert status.status == "full"
    assert status.capacity_percent == 100


def test_handheld_volume_limit_is_model_aware() -> None:
    transport = FakeTransport()
    radio = SDS200.from_transport(transport, expected_model="SDS100")

    with radio:
        def respond() -> None:
            while transport.writes != ["MDL"]:
                time.sleep(0.005)
            transport.feed_line("MDL,SDS100")

        thread = threading.Thread(target=respond)
        thread.start()
        try:
            radio.set_volume(16, timeout=1.0)
        except ValueError as exc:
            assert "between 0 and 15" in str(exc)
        else:
            raise AssertionError("Expected the SDS100 volume limit to reject 16")
        thread.join(timeout=1.0)

    assert transport.writes == ["MDL"]


def test_expected_model_mismatch_is_rejected() -> None:
    transport = FakeTransport()
    radio = SDS200.from_transport(transport, expected_model="SDS100")

    with radio:
        def respond() -> None:
            while transport.writes != ["MDL"]:
                time.sleep(0.005)
            transport.feed_line("MDL,SDS200")

        thread = threading.Thread(target=respond)
        thread.start()
        try:
            radio.get_model(timeout=1.0)
        except UnsupportedScannerModelError as exc:
            assert "Expected SDS100" in str(exc)
        else:
            raise AssertionError("Expected a scanner-model mismatch")
        thread.join(timeout=1.0)


def test_auto_rejects_unknown_model_before_discovery() -> None:
    try:
        SDS200.auto(model="not-a-scanner")
    except ValueError as exc:
        assert "Unsupported SDS-series scanner model" in str(exc)
    else:
        raise AssertionError("Expected an unsupported scanner model error")


def test_sds200_rejects_charge_status_before_gcs_is_sent() -> None:
    transport = FakeTransport()
    radio = SDS200.from_transport(transport, expected_model="SDS200")

    with radio:
        def respond() -> None:
            while transport.writes != ["MDL"]:
                time.sleep(0.005)
            transport.feed_line("MDL,SDS200")

        thread = threading.Thread(target=respond)
        thread.start()
        try:
            radio.get_charge_status(timeout=1.0)
        except UnsupportedScannerFeatureError as exc:
            assert "SDS200" in str(exc)
        else:
            raise AssertionError("Expected SDS200 charge-status rejection")
        thread.join(timeout=1.0)

    assert transport.writes == ["MDL"]


def test_sds100_battery_level_uses_gsi_without_sending_gcs() -> None:
    transport = FakeTransport()
    radio = SDS200.from_transport(transport, expected_model="SDS100")
    xml = """<?xml version="1.0" encoding="utf-8"?>
<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">
<Property VOL="10" SQL="2" Sig="5" Rssi="-86" />
</ScannerInfo>"""

    with radio:
        def respond() -> None:
            while transport.writes != ["MDL"]:
                time.sleep(0.005)
            transport.feed_line("MDL,SDS100")
            while transport.writes != ["MDL", "GSI"]:
                time.sleep(0.005)
            transport.feed_line("GSI,<XML>,")
            for line in xml.splitlines():
                transport.feed_line(line)

        thread = threading.Thread(target=respond)
        thread.start()
        assert radio.get_battery_level(timeout=1.0) is None
        thread.join(timeout=1.0)

    assert transport.writes == ["MDL", "GSI"]


def test_sds100_rejects_charge_status_before_gcs_is_sent() -> None:
    transport = FakeTransport()
    radio = SDS200.from_transport(transport, expected_model="SDS100")

    with radio:
        def respond() -> None:
            while transport.writes != ["MDL"]:
                time.sleep(0.005)
            transport.feed_line("MDL,SDS100")

        thread = threading.Thread(target=respond)
        thread.start()
        try:
            radio.get_charge_status(timeout=1.0)
        except UnsupportedScannerFeatureError as exc:
            assert "SDS100" in str(exc)
        else:
            raise AssertionError("Expected SDS100 charge-status rejection")
        thread.join(timeout=1.0)

    assert transport.writes == ["MDL"]


@pytest.mark.parametrize("rejection", ["ERR", "NG"])
def test_generic_rejection_fails_the_pending_command_immediately(rejection: str) -> None:
    transport = FakeTransport()
    radio = SDS200.from_transport(transport)

    with radio:
        def respond() -> None:
            while transport.writes != ["GCS"]:
                time.sleep(0.005)
            transport.feed_line(rejection)

        thread = threading.Thread(target=respond)
        thread.start()
        started = time.monotonic()
        try:
            radio.command("GCS", timeout=1.0)
        except CommandRejectedError as exc:
            assert str(exc) == f"Scanner rejected GCS command: {rejection}"
        else:
            raise AssertionError("Expected scanner command rejection")
        elapsed = time.monotonic() - started
        thread.join(timeout=1.0)

    assert elapsed < 0.5


def test_typed_navigation_uses_model_check_and_acknowledgement() -> None:
    transport = FakeTransport()
    radio = SDS200.from_transport(transport, expected_model="SDS100")

    with radio:
        def respond() -> None:
            while transport.writes != ["MDL"]:
                time.sleep(0.005)
            transport.feed_line("MDL,SDS100")
            while transport.writes != ["MDL", "HLD,SYS,42,"]:
                time.sleep(0.005)
            transport.feed_line("HLD,OK")
            while transport.writes != ["MDL", "HLD,SYS,42,", "NXT,DEPT,7,42,2"]:
                time.sleep(0.005)
            transport.feed_line("NXT,OK")
            while transport.writes != [
                "MDL",
                "HLD,SYS,42,",
                "NXT,DEPT,7,42,2",
                "PRV,TGID,99,,1",
            ]:
                time.sleep(0.005)
            transport.feed_line("PRV,OK")

        thread = threading.Thread(target=respond, daemon=True)
        thread.start()
        radio.hold("SYS", 42, timeout=1.0)
        radio.next("DEPT", 7, 42, count=2, timeout=1.0)
        radio.previous("TGID", 99, timeout=1.0)
        thread.join(timeout=1.0)

    assert not thread.is_alive()


def test_sds200_press_hold_key_uses_typed_key_command() -> None:
    transport = FakeTransport()
    radio = SDS200.from_transport(transport, expected_model="SDS200")

    with radio:
        def respond() -> None:
            while transport.writes != ["MDL"]:
                time.sleep(0.005)
            transport.feed_line("MDL,SDS200")
            while transport.writes != ["MDL", "KEY,C,P"]:
                time.sleep(0.005)
            transport.feed_line("KEY,OK")

        thread = threading.Thread(target=respond, daemon=True)
        thread.start()
        radio.press_hold_key("C", timeout=1.0)
        thread.join(timeout=1.0)

    assert not thread.is_alive()
    assert transport.writes == ["MDL", "KEY,C,P"]


def test_sds150_rejects_unverified_hold_key_control_before_key_command() -> None:
    transport = FakeTransport()
    radio = SDS200.from_transport(transport, expected_model="SDS150")

    with radio:
        def respond() -> None:
            while transport.writes != ["MDL"]:
                time.sleep(0.005)
            transport.feed_line("MDL,SDS150GBT")

        thread = threading.Thread(target=respond, daemon=True)
        thread.start()
        with pytest.raises(
            UnsupportedScannerFeatureError,
            match="hold-related key control",
        ):
            radio.press_hold_key("C", timeout=1.0)
        thread.join(timeout=1.0)

    assert not thread.is_alive()
    assert transport.writes == ["MDL"]


def test_preferred_recovery_restarts_active_psi_stream() -> None:
    transport = FakeTransport()
    radio = SDS200.from_transport(transport, expected_model="SDS200")

    with radio:
        radio._psi_interval_ms = 500
        radio._transport_diagnostic(
            TransportDiagnostic(
                kind="preferred_recovery_succeeded",
                endpoint=transport.endpoint,
                message="Recovered preferred transport",
            )
        )
        radio._psi_interval_ms = None

    assert transport.writes == ["PSI,500"]

def test_on_psi_emits_parsed_frame_after_state_update_and_unsubscribes() -> None:
    transport = FakeTransport()
    radio = SDS200.from_transport(transport, expected_model="SDS200")
    observed: list[ScannerInfo] = []
    state_channels: list[str | None] = []

    def capture(info: ScannerInfo) -> None:
        observed.append(info)
        state_channels.append(radio.state.snapshot.channel)

    unsubscribe = radio.on_psi(capture)
    xml = """<?xml version="1.0" encoding="utf-8"?>
<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">
<System Name="Example System" Index="100" />
<TGID Name="Example Channel" TGID="TGID:1234" />
<Property VOL="10" SQL="2" Sig="5" />
</ScannerInfo>"""

    with radio:
        for line_number in range(2):
            transport.feed_line("PSI,<XML>,")
            for line in xml.splitlines():
                transport.feed_line(line)
            if line_number == 0:
                unsubscribe()

    assert len(observed) == 1
    assert observed[0].command == "PSI"
    assert observed[0].channel == "Example Channel"
    assert state_channels == ["Example Channel"]


def test_malformed_glt_emits_protocol_error_without_a_response() -> None:
    transport = FakeTransport()
    radio = SDS200.from_transport(transport)
    errors: list[ProtocolError] = []
    radio.events.subscribe("protocol_error", errors.append)

    with radio:
        transport.feed_line("GLT,<XML>,")
        transport.feed_line("<GLT><FL></GLT>")

    assert len(errors) == 1
    assert str(errors[0]) == "Invalid GLT XML response"


def test_analysis_starts_correlate_first_ast_and_later_ast_is_published() -> None:
    transport = FakeTransport()
    radio = SDS200.from_transport(transport)
    observed: list[AnalysisResponse] = []
    radio.events.subscribe("ast", observed.append)
    initial_state = radio.state.snapshot

    def feed_ast(tag: str, identifier: str) -> None:
        transport.feed_line("AST,<XML>,")
        transport.feed_line('<AST FutureRoot="keep">')
        transport.feed_line(f'<{tag} SyntheticId="{identifier}" />')
        transport.feed_line("</AST>")

    with radio:
        def respond_current() -> None:
            while transport.writes != ["AST,CURRENT_ACTIVITY,7"]:
                time.sleep(0.005)
            feed_ast("CurrentActivity", "first")

        thread = threading.Thread(target=respond_current, daemon=True)
        thread.start()
        current = radio.start_current_activity_analysis(7, timeout=1.0)
        thread.join(timeout=1.0)

        feed_ast("CurrentActivity", "subsequent")

        def respond_apr() -> None:
            while transport.writes[-1:] != ["APR,CURRENT_ACTIVITY"]:
                time.sleep(0.005)
            transport.feed_line("APR,OK")

        thread = threading.Thread(target=respond_apr, daemon=True)
        thread.start()
        radio.pause_resume_analysis(AnalysisMode.CURRENT_ACTIVITY, timeout=1.0)
        thread.join(timeout=1.0)

        def respond_lcn() -> None:
            while transport.writes[-1:] != ["AST,LCN_MONITOR,9"]:
                time.sleep(0.005)
            feed_ast("LcnMonitor", "lcn-first")

        thread = threading.Thread(target=respond_lcn, daemon=True)
        thread.start()
        lcn = radio.start_lcn_monitor_analysis(9, timeout=1.0)
        thread.join(timeout=1.0)

    assert current.records[0].attributes["SyntheticId"] == "first"
    assert lcn.records[0].attributes["SyntheticId"] == "lcn-first"
    assert [item.records[0].attributes["SyntheticId"] for item in observed] == [
        "first",
        "subsequent",
        "lcn-first",
    ]
    assert radio.state.snapshot == initial_state
    assert transport.writes == [
        "AST,CURRENT_ACTIVITY,7",
        "APR,CURRENT_ACTIVITY",
        "AST,LCN_MONITOR,9",
    ]


def test_radio_owns_bounded_analysis_publication_without_state_or_wire_side_effects() -> None:
    transport = FakeTransport()
    radio = SDS200.from_transport(transport)
    subscription = radio.subscribe_analysis()
    observed: list[AnalysisResponse] = []
    radio.events.subscribe("ast", observed.append)
    initial_state = radio.state.snapshot

    def feed_ast(identifier: str) -> None:
        transport.feed_line("AST,<XML>,")
        transport.feed_line(
            f'<AST><CurrentActivity SyntheticId="{identifier}" /></AST>'
        )

    radio.connect()

    def respond() -> None:
        while transport.writes != ["AST,CURRENT_ACTIVITY,7"]:
            time.sleep(0.005)
        feed_ast("first")

    thread = threading.Thread(target=respond)
    thread.start()
    first = radio.start_current_activity_analysis(7, timeout=1.0)
    thread.join(timeout=1.0)
    feed_ast("later")

    deliveries = [subscription.get(0), subscription.get(0)]
    assert deliveries[0].response is first
    assert [delivery.sequence for delivery in deliveries] == [1, 2]
    assert [item.records[0].attributes["SyntheticId"] for item in observed] == [
        "first",
        "later",
    ]
    assert radio.analysis_snapshot().responses_published == 2
    assert radio.analysis_snapshot().last_sequence == 2
    assert radio.state.snapshot == initial_state

    subscription.close()
    assert transport.writes == ["AST,CURRENT_ACTIVITY,7"]

    blocked = radio.subscribe_analysis()
    failures: list[type[BaseException]] = []

    def receive() -> None:
        try:
            blocked.get()
        except BaseException as exc:
            failures.append(type(exc))

    reader = threading.Thread(target=receive)
    reader.start()
    radio.close()
    reader.join(timeout=1.0)

    assert failures == [AnalysisSubscriptionClosed]
    assert transport.writes == ["AST,CURRENT_ACTIVITY,7"]


def test_identical_psi_frames_refresh_state_observers() -> None:
    transport = FakeTransport()
    radio = SDS200.from_transport(transport, expected_model="SDS200")
    states: list[object] = []
    changes: list[object] = []
    radio.on_state(states.append)
    radio.on_state_change(changes.append)
    xml = """<?xml version="1.0" encoding="utf-8"?>
<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">
<System Name="Example System" Index="100" />
<Property VOL="10" SQL="2" Sig="0" />
</ScannerInfo>"""

    with radio:
        for _ in range(2):
            transport.feed_line("PSI,<XML>,")
            for line in xml.splitlines():
                transport.feed_line(line)

    assert len(states) == 2
    assert len(changes) == 1


def test_manual_reconnect_preserves_active_psi_interval() -> None:
    transport = FakeTransport()
    radio = SDS200.from_transport(transport, expected_model="SDS200")
    xml = """<?xml version="1.0" encoding="utf-8"?>
<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">
<Property VOL="10" SQL="2" Sig="0" />
</ScannerInfo>"""

    radio.connect()
    radio._psi_interval_ms = 500

    def respond() -> None:
        while transport.writes != ["PSI,500"]:
            time.sleep(0.005)
        transport.feed_line("PSI,<XML>,")
        for line in xml.splitlines():
            transport.feed_line(line)

    thread = threading.Thread(target=respond, daemon=True)
    thread.start()
    radio.reconnect()
    thread.join(timeout=1.0)

    assert not thread.is_alive()
    assert radio.connected
    assert radio.psi_interval_ms == 500
    radio._psi_interval_ms = None
    radio.close()


def test_failed_reconnect_preserves_active_psi_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = FakeTransport()
    radio = SDS200.from_transport(transport, expected_model="SDS200")
    xml = """<?xml version="1.0" encoding="utf-8"?>
<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">
<Property VOL="10" SQL="2" Sig="0" />
</ScannerInfo>"""

    radio.connect()
    radio._psi_interval_ms = 500
    start_scanner_info_push = radio.start_scanner_info_push

    def start_with_short_timeout(
        interval_ms: int = 500,
        *,
        timeout: float = 3.0,
    ) -> object:
        del timeout
        return start_scanner_info_push(interval_ms, timeout=0.05)

    monkeypatch.setattr(radio, "start_scanner_info_push", start_with_short_timeout)

    with pytest.raises(CommandTimeoutError):
        radio.reconnect()

    assert radio.psi_interval_ms == 500
    assert transport.writes == ["PSI,500", "PSI,0"]
    monkeypatch.setattr(radio, "start_scanner_info_push", start_scanner_info_push)

    def respond() -> None:
        while transport.writes.count("PSI,500") < 2:
            time.sleep(0.005)
        transport.feed_line("PSI,<XML>,")
        for line in xml.splitlines():
            transport.feed_line(line)

    thread = threading.Thread(target=respond, daemon=True)
    thread.start()
    radio.reconnect()


def test_only_direct_udp_transport_advertises_bounded_reconnect() -> None:
    direct = SDS200.network("192.0.2.25")
    injected = SDS200.from_transport(
        FakeTransport(),
        expected_model="SDS200",
    )

    assert direct.supports_bounded_reconnect is True
    assert injected.supports_bounded_reconnect is False


@pytest.mark.parametrize("timeout", [True, 0, float("inf")])
def test_reconnect_rejects_invalid_timeout(timeout: object) -> None:
    radio = SDS200.from_transport(
        FakeTransport(),
        expected_model="SDS200",
    )

    with pytest.raises((TypeError, ValueError)):
        radio.reconnect(timeout=timeout)  # type: ignore[arg-type]


def test_reconnect_deadline_includes_transport_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = FakeTransport()
    radio = SDS200.from_transport(
        transport,
        expected_model="SDS200",
    )
    radio.connect()
    original_stop = transport.stop

    def slow_stop() -> None:
        time.sleep(0.02)
        original_stop()

    monkeypatch.setattr(transport, "stop", slow_stop)

    with pytest.raises(CommandTimeoutError, match="stopping"):
        radio.reconnect(timeout=0.001)

    radio.close()


def test_reconnect_deadline_includes_active_psi_renewal() -> None:
    transport = FakeTransport("udp://scanner")
    radio = SDS200.from_transport(
        transport,
        expected_model="SDS200",
    )
    radio.connect()
    radio._psi_interval_ms = 500
    radio._psi_renewal_interval = 0.001
    radio._psi_renewal_defer = 0.001
    radio._psi_renewal_timeout = 0.2
    radio._start_psi_renewal()

    deadline = time.monotonic() + 1.0
    while transport.writes != ["PSI,500"] and time.monotonic() < deadline:
        time.sleep(0.001)

    assert transport.writes == ["PSI,500"]
    renewal_thread = radio._psi_renewal_thread
    assert renewal_thread is not None
    assert renewal_thread.is_alive()

    started = time.monotonic()
    with pytest.raises(CommandTimeoutError, match="scanner command activity"):
        radio.reconnect(timeout=0.01)
    elapsed = time.monotonic() - started

    assert elapsed < 0.1
    assert radio.connected
    assert radio.psi_active
    assert radio._psi_renewal_thread is renewal_thread
    assert renewal_thread.is_alive()

    transport.feed_line("PSI,<XML>,")
    transport.feed_line('<?xml version="1.0" encoding="utf-8"?>')
    transport.feed_line('<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">')
    transport.feed_line('<Property VOL="10" SQL="2" Sig="0" />')
    transport.feed_line("</ScannerInfo>")

    radio.stop_scanner_info_push()
    assert not renewal_thread.is_alive()
    radio.close()


def test_command_timeout_includes_wait_for_command_transaction() -> None:
    transport = FakeTransport()
    radio = SDS200.from_transport(
        transport,
        expected_model="SDS200",
    )
    radio.connect()
    first_errors: list[BaseException] = []

    def hold_command_transaction() -> None:
        try:
            radio.command("GSI", timeout=0.2)
        except BaseException as error:
            first_errors.append(error)

    first = threading.Thread(target=hold_command_transaction, daemon=True)
    first.start()

    deadline = time.monotonic() + 1.0
    while transport.writes != ["GSI"] and time.monotonic() < deadline:
        time.sleep(0.001)

    assert transport.writes == ["GSI"]

    started = time.monotonic()
    with pytest.raises(CommandTimeoutError, match="MDL response"):
        radio.command("MDL", timeout=0.01)
    elapsed = time.monotonic() - started

    assert elapsed < 0.1
    assert transport.writes == ["GSI"]

    first.join(timeout=1.0)
    assert not first.is_alive()
    assert len(first_errors) == 1
    assert isinstance(first_errors[0], CommandTimeoutError)

    radio.close()


def test_network_psi_push_renews_before_observed_hardware_expiry() -> None:
    transport = FakeTransport("udp://scanner")
    radio = SDS200.from_transport(transport, expected_model="SDS200")
    radio._psi_renewal_interval = 0.02
    radio._psi_renewal_defer = 0.005
    radio._psi_renewal_timeout = 0.1
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">\n'
        '<Property VOL="10" SQL="2" Sig="0" />\n'
        "</ScannerInfo>"
    )

    radio.connect()

    def respond_to_psi_commands() -> None:
        responded = 0
        while responded < 2:
            if transport.writes.count("PSI,500") <= responded:
                time.sleep(0.001)
                continue
            transport.feed_line("PSI,<XML>,")
            for line in xml.splitlines():
                transport.feed_line(line)
            responded += 1

    responder = threading.Thread(target=respond_to_psi_commands, daemon=True)
    responder.start()
    radio.start_scanner_info_push(timeout=1.0)

    deadline = time.monotonic() + 1.0
    while transport.writes.count("PSI,500") < 2 and time.monotonic() < deadline:
        time.sleep(0.001)
    responder.join(timeout=1.0)

    assert not responder.is_alive()
    assert transport.writes.count("PSI,500") >= 2
    renewal_thread = radio._psi_renewal_thread
    assert renewal_thread is not None
    assert renewal_thread.is_alive()

    radio.stop_scanner_info_push()
    writes_after_stop = list(transport.writes)
    time.sleep(0.05)

    assert transport.writes == writes_after_stop
    assert transport.writes[-1] == "PSI,0"
    assert radio._psi_renewal_thread is None
    assert not renewal_thread.is_alive()
    radio.close()


def test_psi_renewal_defers_while_response_command_is_pending() -> None:
    transport = FakeTransport("udp://scanner")
    radio = SDS200.from_transport(transport, expected_model="SDS200")
    radio.connect()
    radio._psi_interval_ms = 500
    radio._psi_renewal_timeout = 0.1
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">\n'
        '<Property VOL="10" SQL="2" Sig="0" />\n'
        "</ScannerInfo>"
    )
    errors: list[BaseException] = []

    def request_scanner_info() -> None:
        try:
            radio.get_scanner_info(timeout=1.0)
        except BaseException as error:
            errors.append(error)

    request = threading.Thread(target=request_scanner_info, daemon=True)
    request.start()

    deadline = time.monotonic() + 1.0
    while transport.writes != ["GSI"] and time.monotonic() < deadline:
        time.sleep(0.001)

    assert transport.writes == ["GSI"]
    assert radio._renew_psi_if_idle() is False
    assert transport.writes == ["GSI"]

    transport.feed_line("GSI,<XML>,")
    for line in xml.splitlines():
        transport.feed_line(line)
    request.join(timeout=1.0)

    assert not request.is_alive()
    assert not errors

    def respond_to_renewal() -> None:
        while transport.writes != ["GSI", "PSI,500"]:
            time.sleep(0.001)
        transport.feed_line("PSI,<XML>,")
        for line in xml.splitlines():
            transport.feed_line(line)

    responder = threading.Thread(target=respond_to_renewal, daemon=True)
    responder.start()
    assert radio._renew_psi_if_idle() is True
    responder.join(timeout=1.0)

    assert not responder.is_alive()
    assert transport.writes == ["GSI", "PSI,500"]

    radio._psi_interval_ms = None
    radio.close()


def test_psi_renewal_discards_frame_seen_before_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = FakeTransport("udp://scanner")
    radio = SDS200.from_transport(transport, expected_model="SDS200")
    radio.connect()
    radio._psi_interval_ms = 500
    radio._psi_renewal_timeout = 0.5
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">\n'
        '<Property VOL="10" SQL="2" Sig="0" />\n'
        "</ScannerInfo>"
    )
    original_subscribe = radio.events.subscribe
    injected_stale_frame = False

    def subscribe_with_stale_frame(
        event: str,
        callback: object,
    ) -> object:
        nonlocal injected_stale_frame
        unsubscribe = original_subscribe(event, callback)
        if event == "psi" and not injected_stale_frame:
            injected_stale_frame = True
            transport.feed_line("PSI,<XML>,")
            for line in xml.splitlines():
                transport.feed_line(line)
        return unsubscribe

    monkeypatch.setattr(radio.events, "subscribe", subscribe_with_stale_frame)

    renewal_results: list[bool] = []
    ack_sent = threading.Event()
    release_post_ack_frame = threading.Event()

    def respond_to_renewal() -> None:
        deadline = time.monotonic() + 1.0
        while transport.writes != ["PSI,500"] and time.monotonic() < deadline:
            time.sleep(0.001)
        assert transport.writes == ["PSI,500"]
        transport.feed_line("PSI,OK")
        ack_sent.set()
        assert release_post_ack_frame.wait(timeout=1.0)
        transport.feed_line("PSI,<XML>,")
        for line in xml.splitlines():
            transport.feed_line(line)

    responder = threading.Thread(target=respond_to_renewal, daemon=True)
    responder.start()
    renewal = threading.Thread(
        target=lambda: renewal_results.append(radio._renew_psi_if_idle()),
        daemon=True,
    )
    renewal.start()

    assert ack_sent.wait(timeout=1.0)
    time.sleep(0.02)

    assert injected_stale_frame
    assert renewal.is_alive()

    release_post_ack_frame.set()
    renewal.join(timeout=1.0)
    responder.join(timeout=1.0)

    assert not renewal.is_alive()
    assert not responder.is_alive()
    assert renewal_results == [True]
    assert transport.writes == ["PSI,500"]

    radio._psi_interval_ms = None
    radio.close()


def test_psi_renewal_keeps_transaction_lock_until_frame_after_ack() -> None:
    transport = FakeTransport("udp://scanner")
    radio = SDS200.from_transport(transport, expected_model="SDS200")
    radio.connect()
    radio._psi_interval_ms = 500
    radio._psi_renewal_timeout = 0.5
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">\n'
        '<Property VOL="10" SQL="2" Sig="0" />\n'
        "</ScannerInfo>"
    )
    renewal_results: list[bool] = []
    command_errors: list[BaseException] = []

    renewal = threading.Thread(
        target=lambda: renewal_results.append(radio._renew_psi_if_idle()),
        daemon=True,
    )
    renewal.start()

    deadline = time.monotonic() + 1.0
    while transport.writes != ["PSI,500"] and time.monotonic() < deadline:
        time.sleep(0.001)

    assert transport.writes == ["PSI,500"]
    transport.feed_line("PSI,OK")

    def request_scanner_info() -> None:
        try:
            radio.get_scanner_info(timeout=0.5)
        except BaseException as error:
            command_errors.append(error)

    request = threading.Thread(target=request_scanner_info, daemon=True)
    request.start()
    time.sleep(0.02)

    assert transport.writes == ["PSI,500"]
    assert renewal.is_alive()
    assert request.is_alive()

    transport.feed_line("PSI,<XML>,")
    for line in xml.splitlines():
        transport.feed_line(line)

    deadline = time.monotonic() + 1.0
    while transport.writes != ["PSI,500", "GSI"] and time.monotonic() < deadline:
        time.sleep(0.001)

    assert transport.writes == ["PSI,500", "GSI"]
    transport.feed_line("GSI,<XML>,")
    for line in xml.splitlines():
        transport.feed_line(line)

    renewal.join(timeout=1.0)
    request.join(timeout=1.0)

    assert not renewal.is_alive()
    assert not request.is_alive()
    assert renewal_results == [True]
    assert not command_errors

    radio._psi_interval_ms = None
    radio.close()


def test_psi_start_keeps_transaction_lock_until_frame_after_ack() -> None:
    transport = FakeTransport("udp://scanner")
    radio = SDS200.from_transport(transport, expected_model="SDS200")
    radio.connect()
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">\n'
        '<Property VOL="10" SQL="2" Sig="0" />\n'
        "</ScannerInfo>"
    )
    start_results: list[ScannerInfo] = []
    start_errors: list[BaseException] = []
    command_errors: list[BaseException] = []

    def start_push() -> None:
        try:
            start_results.append(radio.start_scanner_info_push(timeout=0.5))
        except BaseException as error:
            start_errors.append(error)

    starter = threading.Thread(target=start_push, daemon=True)
    starter.start()

    deadline = time.monotonic() + 1.0
    while transport.writes != ["PSI,500"] and time.monotonic() < deadline:
        time.sleep(0.001)

    assert transport.writes == ["PSI,500"]
    transport.feed_line("PSI,OK")

    def request_scanner_info() -> None:
        try:
            radio.get_scanner_info(timeout=0.5)
        except BaseException as error:
            command_errors.append(error)

    request = threading.Thread(target=request_scanner_info, daemon=True)
    request.start()
    time.sleep(0.02)

    assert transport.writes == ["PSI,500"]
    assert starter.is_alive()
    assert request.is_alive()

    transport.feed_line("PSI,<XML>,")
    for line in xml.splitlines():
        transport.feed_line(line)

    deadline = time.monotonic() + 1.0
    while transport.writes != ["PSI,500", "GSI"] and time.monotonic() < deadline:
        time.sleep(0.001)

    assert transport.writes == ["PSI,500", "GSI"]
    transport.feed_line("GSI,<XML>,")
    for line in xml.splitlines():
        transport.feed_line(line)

    starter.join(timeout=1.0)
    request.join(timeout=1.0)

    assert not starter.is_alive()
    assert not request.is_alive()
    assert len(start_results) == 1
    assert not start_errors
    assert not command_errors

    radio.stop_scanner_info_push()
    radio.close()


def test_stop_waits_for_inflight_psi_start_before_stopping_renewal() -> None:
    transport = FakeTransport("udp://scanner")
    radio = SDS200.from_transport(transport, expected_model="SDS200")
    radio.connect()
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">\n'
        '<Property VOL="10" SQL="2" Sig="0" />\n'
        "</ScannerInfo>"
    )
    start_results: list[ScannerInfo] = []
    start_errors: list[BaseException] = []
    stop_errors: list[BaseException] = []

    def start_push() -> None:
        try:
            start_results.append(radio.start_scanner_info_push(timeout=0.5))
        except BaseException as error:
            start_errors.append(error)

    def stop_push() -> None:
        try:
            radio.stop_scanner_info_push()
        except BaseException as error:
            stop_errors.append(error)

    starter = threading.Thread(target=start_push, daemon=True)
    starter.start()

    deadline = time.monotonic() + 1.0
    while transport.writes != ["PSI,500"] and time.monotonic() < deadline:
        time.sleep(0.001)

    assert transport.writes == ["PSI,500"]
    assert radio.psi_active

    stopper = threading.Thread(target=stop_push, daemon=True)
    stopper.start()
    time.sleep(0.02)

    assert starter.is_alive()
    assert stopper.is_alive()
    assert transport.writes == ["PSI,500"]

    transport.feed_line("PSI,<XML>,")
    for line in xml.splitlines():
        transport.feed_line(line)

    starter.join(timeout=1.0)
    stopper.join(timeout=1.0)

    assert not starter.is_alive()
    assert not stopper.is_alive()
    assert len(start_results) == 1
    assert not start_errors
    assert not stop_errors
    assert not radio.psi_active
    assert radio._psi_renewal_thread is None
    assert transport.writes == ["PSI,500", "PSI,0"]

    radio.close()


def test_close_stops_psi_renewal_after_transport_disconnect() -> None:
    transport = FakeTransport("udp://scanner")
    radio = SDS200.from_transport(transport, expected_model="SDS200")
    radio.connect()
    radio._psi_interval_ms = 500
    radio._psi_renewal_interval = 10.0
    radio._start_psi_renewal()

    renewal_thread = radio._psi_renewal_thread
    assert renewal_thread is not None
    assert renewal_thread.is_alive()

    transport.set_connected(False)
    assert not radio.connected
    assert radio.psi_active

    radio.close()

    assert radio._psi_renewal_thread is None
    assert not renewal_thread.is_alive()
    assert not radio.psi_active
    assert transport.writes == []


def test_recorded_udp_transport_keeps_psi_renewal_support(tmp_path) -> None:
    transport = FakeTransport("udp://scanner")
    radio = SDS200.from_transport(
        transport,
        expected_model="SDS200",
        capture_path=tmp_path / "session.jsonl",
    )

    assert radio._psi_renewal_supported is True



def test_radio_owns_receive_only_bounded_waterfall_publication() -> None:
    from sds200.models import GwfResponse, PwfResponse
    from sds200.waterfall_subscriptions import WaterfallSubscriptionClosed

    transport = FakeTransport()
    radio = SDS200.from_transport(transport)
    subscription = radio.subscribe_waterfall()
    observed: list[object] = []
    radio.on_response(observed.append)
    initial_state = radio.state.snapshot

    radio.connect()
    transport.feed_line("PWF,17,,23,FUTURE")
    gwf_values = tuple(str(index) for index in range(240))
    transport.feed_line("GWF," + ",".join(gwf_values))

    deliveries = [subscription.get(0), subscription.get(0)]

    assert isinstance(deliveries[0].response, PwfResponse)
    assert deliveries[0].response.values == ("17", "", "23", "FUTURE")
    assert isinstance(deliveries[1].response, GwfResponse)
    assert deliveries[1].response.values == gwf_values
    assert [delivery.sequence for delivery in deliveries] == [1, 2]
    assert observed[-2:] == [
        deliveries[0].response,
        deliveries[1].response,
    ]
    assert radio.waterfall_snapshot().responses_published == 2
    assert radio.waterfall_snapshot().last_sequence == 2
    assert radio.state.snapshot == initial_state
    assert transport.writes == []

    subscription.close()
    assert transport.writes == []

    blocked = radio.subscribe_waterfall()
    failures: list[type[BaseException]] = []

    def receive() -> None:
        try:
            blocked.get()
        except BaseException as exc:
            failures.append(type(exc))

    thread = threading.Thread(target=receive)
    thread.start()
    radio.close()
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert failures == [WaterfallSubscriptionClosed]
    assert transport.writes == []
