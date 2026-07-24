from __future__ import annotations

import time

from sds200.reliability import ReconnectPolicy
from sds200.transport import SerialTransport, TransportDiagnostic

from .fakes import CloseAwareSerial, FakeSerial


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


def test_transport_reports_connection_changes() -> None:
    fake = CloseAwareSerial()
    changes: list[bool] = []
    transport = SerialTransport(
        "/dev/fake",
        reconnect=False,
        serial_factory=lambda **kwargs: fake,
    )

    transport.start(lambda line: None, changes.append)
    transport.stop()

    assert changes == [True, False]


def test_serial_transport_reconnects_with_policy() -> None:
    class DisconnectingSerial(FakeSerial):
        def read(self, size: int = 1) -> bytes:
            del size
            raise OSError("simulated disconnect")

    first = DisconnectingSerial()
    second = FakeSerial()
    serials = iter((first, second))
    diagnostics: list[TransportDiagnostic] = []
    transport = SerialTransport(
        "/dev/fake",
        read_timeout=0.01,
        reconnect_policy=ReconnectPolicy(
            initial_delay=0.01,
            multiplier=2.0,
            max_delay=0.02,
            max_attempts=2,
        ),
        serial_factory=lambda **kwargs: next(serials),
    )
    transport.set_diagnostic_handler(diagnostics.append)

    transport.start(lambda line: None)
    deadline = time.monotonic() + 1.0
    while transport.statistics["reconnects"] != 1 and time.monotonic() < deadline:
        time.sleep(0.005)

    try:
        assert transport.connected
        assert transport.statistics["reconnect_attempts"] == 1
        assert [diagnostic.kind for diagnostic in diagnostics] == [
            "serial_read_error",
            "reconnect_scheduled",
            "reconnected",
        ]
    finally:
        transport.stop()


def test_serial_transport_accepts_lf_and_crlf_responses() -> None:
    fake = FakeSerial()
    lines: list[str] = []
    transport = SerialTransport(
        "/dev/fake",
        reconnect=False,
        serial_factory=lambda **kwargs: fake,
    )

    transport.start(lines.append)
    fake.feed(b"MDL,SDS150GBT\nVER,1.00.00\r\n")
    deadline = time.monotonic() + 1.0
    while len(lines) < 2 and time.monotonic() < deadline:
        time.sleep(0.005)
    transport.stop()

    assert lines == ["MDL,SDS150GBT", "VER,1.00.00"]
