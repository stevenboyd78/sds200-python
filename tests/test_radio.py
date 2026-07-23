import threading
import time

from sds200.radio import SDS200

from .fakes import FakeSerial


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
