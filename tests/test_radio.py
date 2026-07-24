import threading
import time

from sds200.exceptions import (
    UnsupportedScannerFeatureError,
    UnsupportedScannerModelError,
)
from sds200.fallback import FallbackTransport
from sds200.models import RadioEvent
from sds200.profiles import ConnectionProfile
from sds200.radio import SDS200

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
