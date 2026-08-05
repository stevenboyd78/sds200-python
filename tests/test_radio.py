import threading
import time

import pytest

from sds200.exceptions import (
    CommandRejectedError,
    CommandTimeoutError,
    UnsupportedScannerFeatureError,
    UnsupportedScannerModelError,
)
from sds200.fallback import FallbackTransport
from sds200.models import RadioEvent, ScannerInfo
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
