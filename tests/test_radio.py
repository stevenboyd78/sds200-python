import threading
import time

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
