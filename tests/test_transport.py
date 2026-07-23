from __future__ import annotations

from sds200.transport import SerialTransport

from .fakes import CloseAwareSerial


def test_stop_waits_for_active_read_before_closing() -> None:
    fake = CloseAwareSerial()
    transport = SerialTransport(
        "/dev/fake",
        reconnect=False,
        serial_factory=lambda **kwargs: fake,
    )

    transport.start(lambda line: None)
    assert fake.read_started.wait(timeout=1.0)

    transport.stop()

    assert not fake.closed_while_reading
    assert not fake.is_open
    assert not transport.connected
