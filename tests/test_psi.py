from __future__ import annotations

import threading
import time

from sds200.radio import SDS200

from .fakes import FakeTransport

XML_LINES = (
    '<?xml version="1.0" encoding="utf-8"?>',
    '<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">',
    '<System Name="Utah Communications Authority (P25)" />',
    '<Department Name="Harris Dynamic Patch - Northern Utah" />',
    '<Site Name="Utah County Simulcast" Mod="NFM" />',
    '<TGID Name="Patch 65132" TGID="TGID:65132" SvcType="Interop" />',
    '<SiteFrequency Freq=" 769.431250MHz" />',
    '<Property VOL="10" SQL="2" Sig="5" P25Status="P25" />',
    '</ScannerInfo>',
)


def feed_psi(transport: FakeTransport) -> None:
    transport.feed_line("PSI,<XML>,")
    for line in XML_LINES:
        transport.feed_line(line)


def wait_for_write(transport: FakeTransport, value: str) -> None:
    deadline = time.monotonic() + 1.0
    while value not in transport.writes and time.monotonic() < deadline:
        time.sleep(0.005)
    assert value in transport.writes


def test_psi_start_updates_state_and_stop_sends_zero() -> None:
    transport = FakeTransport()
    radio = SDS200.from_transport(transport)

    with radio:
        result: list[object] = []
        thread = threading.Thread(
            target=lambda: result.append(
                radio.start_scanner_info_push(250, timeout=1.0)
            )
        )
        thread.start()
        wait_for_write(transport, "PSI,250")
        feed_psi(transport)
        thread.join(timeout=1.0)

        assert not thread.is_alive()
        assert result
        assert radio.psi_active
        assert radio.state.snapshot.channel == "Patch 65132"

        radio.stop_scanner_info_push()
        assert not radio.psi_active
        assert transport.writes[-1] == "PSI,0"


def test_psi_ack_is_followed_by_first_xml_update() -> None:
    transport = FakeTransport()
    radio = SDS200.from_transport(transport)

    with radio:
        result: list[object] = []
        thread = threading.Thread(
            target=lambda: result.append(
                radio.start_scanner_info_push(500, timeout=1.0)
            )
        )
        thread.start()
        wait_for_write(transport, "PSI,500")

        transport.feed_line("PSI,OK")
        feed_psi(transport)
        thread.join(timeout=1.0)

        assert not thread.is_alive()
        assert len(result) == 1
        assert radio.state.snapshot.channel == "Patch 65132"


def test_active_psi_is_restarted_after_transport_reconnect() -> None:
    transport = FakeTransport()
    radio = SDS200.from_transport(transport)

    with radio:
        thread = threading.Thread(
            target=lambda: radio.start_scanner_info_push(500, timeout=1.0)
        )
        thread.start()
        wait_for_write(transport, "PSI,500")
        feed_psi(transport)
        thread.join(timeout=1.0)

        transport.set_connected(False)
        transport.set_connected(True)

        assert transport.writes.count("PSI,500") == 2
