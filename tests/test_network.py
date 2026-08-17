from __future__ import annotations

import socket
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable

import pytest

from sds200.network import UdpDatagramDecoder, UdpTransport
from sds200.radio import SDS200
from sds200.reliability import ReconnectPolicy
from sds200.transport import TransportDiagnostic
from sds200.xml_protocol import ScannerInfoParser, XmlResponseAssembler

from .fakes import (
    DatagramSocketSequenceFactory,
    FakeDatagramSocket,
    FakeDatagramSocketFactory,
)


def wait_until(predicate: Callable[[], bool], *, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert predicate()


def test_decoder_emits_normal_command_responses() -> None:
    decoder = UdpDatagramDecoder()
    assert decoder.feed(b"MDL,SDS200\r") == ("MDL,SDS200",)


def test_decoder_reassembles_numbered_xml_datagrams() -> None:
    decoder = UdpDatagramDecoder()
    first = (
        b'GSI,<XML>,\r<?xml version="1.0" encoding="utf-8"?>'
        b'<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">'
        b'<System Name="Utah Communications Authority (P25)" />'
        b'<Footer No="1" EOT="0" />'
        b'</ScannerInfo>'
    )
    second = (
        b'GSI,<XML>,\r<?xml version="1.0" encoding="utf-8"?>'
        b'<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">'
        b'<Department Name="Harris Dynamic Patch - Northern Utah" />'
        b'<TGID Name="Patch 65132" TGID="TGID:65132" SvcType="Interop" />'
        b'<Property VOL="10" SQL="2" Sig="5" />'
        b'<Footer No="2" EOT="1" />'
        b'</ScannerInfo>'
    )

    assert decoder.feed(first) == ()
    lines = decoder.feed(second)
    assert lines[0] == "GSI,<XML>,"

    assembler = XmlResponseAssembler()
    assembled = None
    for line in lines:
        assembled = assembler.feed(line)

    assert assembled is not None
    command, xml = assembled
    info = ScannerInfoParser().parse(command, xml)
    assert info.system == "Utah Communications Authority (P25)"
    assert info.department == "Harris Dynamic Patch - Northern Utah"
    assert info.channel == "Patch 65132"
    assert info.signal == 5


def test_decoder_discards_xml_after_sequence_gap() -> None:
    decoder = UdpDatagramDecoder()
    first = (
        b'GSI,<XML>,<ScannerInfo><System Name="One" />'
        b'<Footer No="1" EOT="0" /></ScannerInfo>'
    )
    third = (
        b'GSI,<XML>,<ScannerInfo><System Name="Three" />'
        b'<Footer No="3" EOT="1" /></ScannerInfo>'
    )

    assert decoder.feed(first) == ()
    assert decoder.feed(third) == ()


def test_udp_transport_sends_cr_terminated_command_and_receives_response() -> None:
    fake = FakeDatagramSocket()
    factory = FakeDatagramSocketFactory(fake)
    received: list[str] = []
    transport = UdpTransport(
        "192.0.2.25",
        socket_factory=factory,
        reconnect=False,
    )

    transport.start(received.append)
    try:
        transport.write_command("MDL")
        fake.feed(b"MDL,SDS200\r")
        wait_until(lambda: received == ["MDL,SDS200"])
    finally:
        transport.stop()

    assert factory.calls == [(socket.AF_INET, socket.SOCK_DGRAM)]
    assert fake.bound is None
    assert fake.remote == ("192.0.2.25", 50536)
    assert fake.sent == [b"MDL\r"]


def test_udp_transport_resolves_specific_address_for_explicit_local_port() -> None:
    fake = FakeDatagramSocket()
    resolver_calls: list[tuple[str, int]] = []

    def resolver(host: str, port: int) -> str:
        resolver_calls.append((host, port))
        return "192.0.2.10"

    transport = UdpTransport(
        "192.0.2.25",
        local_port=42000,
        reconnect=False,
        socket_factory=FakeDatagramSocketFactory(fake),
        local_address_resolver=resolver,
    )

    transport.start(lambda _line: None)
    transport.stop()

    assert resolver_calls == [("192.0.2.25", 50536)]
    assert fake.bound == ("192.0.2.10", 42000)


def test_udp_transport_rejects_wildcard_bind_address() -> None:
    with pytest.raises(ValueError, match="must not bind all network interfaces"):
        UdpTransport("192.0.2.25", local_host="0.0.0.0")


def test_radio_network_factory_uses_existing_command_api() -> None:
    fake = FakeDatagramSocket()
    factory = FakeDatagramSocketFactory(fake)
    radio = SDS200.network(
        "scanner.example.test",
        socket_factory=factory,
    )

    with radio:
        def respond() -> None:
            wait_until(lambda: fake.sent == [b"MDL\r"])
            fake.feed(b"MDL,SDS200\r")

        thread = threading.Thread(target=respond)
        thread.start()
        assert radio.get_model(timeout=1.0) == "SDS200"
        thread.join(timeout=1.0)

    assert radio.endpoint == "udp://scanner.example.test:50536"


def test_udp_transport_reopens_socket_after_local_failure() -> None:
    first = FakeDatagramSocket()
    second = FakeDatagramSocket()
    factory = DatagramSocketSequenceFactory([first, second])
    received: list[str] = []
    transport = UdpTransport(
        "192.0.2.25",
        reconnect_interval=0.01,
        socket_factory=factory,
    )

    transport.start(received.append)
    try:
        first.incoming.put(OSError("simulated socket failure"))
        wait_until(lambda: len(factory.calls) == 2)
        second.feed(b"VER,Version 1.26.01\r")
        wait_until(lambda: received == ["VER,Version 1.26.01"])
    finally:
        transport.stop()

    assert first.closed
    assert second.closed


def test_radio_network_parses_single_datagram_scanner_info() -> None:
    fake = FakeDatagramSocket()
    factory = FakeDatagramSocketFactory(fake)
    radio = SDS200.network("192.0.2.25", socket_factory=factory)
    xml = (
        b'GSI,<XML>,\r<?xml version="1.0" encoding="utf-8"?>\r'
        b'<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">\r'
        b'<System Name="Utah Communications Authority (P25)" />\r'
        b'<Department Name="Harris Dynamic Patch - Northern Utah" />\r'
        b'<TGID Name="Patch 65132" TGID="TGID:65132" />\r'
        b'<Property Sig="5" />\r'
        b'</ScannerInfo>\r'
    )

    with radio:
        def respond() -> None:
            wait_until(lambda: fake.sent == [b"GSI\r"])
            fake.feed(xml)

        thread = threading.Thread(target=respond)
        thread.start()
        info = radio.get_scanner_info(timeout=1.0)
        thread.join(timeout=1.0)

    assert info.system == "Utah Communications Authority (P25)"
    assert info.department == "Harris Dynamic Patch - Northern Utah"
    assert info.channel == "Patch 65132"
    assert info.signal == 5


def test_decoder_wraps_bare_gsi_xml_after_command() -> None:
    decoder = UdpDatagramDecoder()
    decoder.expect_command("GSI")
    bare_xml = (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">'
        b'<System Name="Utah Communications Authority (P25)" />'
        b'</ScannerInfo>'
    )

    lines = decoder.feed(bare_xml)

    assert lines[0] == "GSI,<XML>,"
    assembler = XmlResponseAssembler()
    assembled = None
    for line in lines:
        assembled = assembler.feed(line)
    assert assembled is not None
    command, xml = assembled
    info = ScannerInfoParser().parse(command, xml)
    assert info.system == "Utah Communications Authority (P25)"


def test_decoder_keeps_psi_for_repeated_bare_xml_updates() -> None:
    decoder = UdpDatagramDecoder()
    decoder.expect_command("PSI,500")
    first = b'<ScannerInfo Mode="Trunk Scan"><Property Sig="1" /></ScannerInfo>'
    second = b'<ScannerInfo Mode="Trunk Scan"><Property Sig="4" /></ScannerInfo>'

    assert decoder.feed(first)[0] == "PSI,<XML>,"
    assert decoder.feed(second)[0] == "PSI,<XML>,"


def test_decoder_wraps_bare_glt_xml_once_after_exact_command() -> None:
    decoder = UdpDatagramDecoder()
    decoder.expect_command("GLT,FL")
    bare_xml = (
        b'<GLT Version="future"><FL Index="0" Name="First" />'
        b'<FL Index="1" Name="Second" FutureAttr="preserve-me" /></GLT>'
    )

    lines = decoder.feed(bare_xml)

    assert lines == ("GLT,<XML>,", bare_xml.decode())
    assert decoder.feed(bare_xml) == (bare_xml.decode(),)


def test_decoder_malformed_bare_glt_does_not_complete_expectation() -> None:
    completed: list[str] = []
    decoder = UdpDatagramDecoder(completion_handler=completed.append)
    decoder.expect_command("GLT,FL")
    malformed = b'<GLT><FL Index="0"></GLT>'
    valid = b'<GLT><FL Index="0" /></GLT>'

    assert decoder.feed(malformed) == ("GLT,<XML>,", malformed.decode())
    assert completed == []

    assert decoder.feed(valid) == ("GLT,<XML>,", valid.decode())
    assert completed == ["GLT"]
    assert decoder.feed(valid) == (valid.decode(),)
    assert completed == ["GLT"]


def test_decoder_new_command_clears_stale_glt_expectation() -> None:
    decoder = UdpDatagramDecoder()
    decoder.expect_command("GLT,FL")
    decoder.expect_command("MDL")
    bare_xml = b'<GLT><FL Index="0" /></GLT>'

    assert decoder.feed(bare_xml) == (bare_xml.decode(),)


def test_decoder_correlates_bare_xml_by_expected_root() -> None:
    scanner_info = b"<ScannerInfo><Property Sig=\"4\" /></ScannerInfo>"
    glt = b"<GLT><FL Index=\"0\" /></GLT>"

    decoder = UdpDatagramDecoder()
    decoder.expect_command("GLT,FL")
    assert decoder.feed(scanner_info) == (scanner_info.decode(),)

    for command in ("GSI", "PSI,500"):
        decoder = UdpDatagramDecoder()
        decoder.expect_command(command)
        assert decoder.feed(glt) == (glt.decode(),)


def test_decoder_accepts_only_matching_explicit_glt_root() -> None:
    decoder = UdpDatagramDecoder()
    glt = '<GLT FutureRoot="keep"><FL Index="0" /></GLT>'

    assert decoder.feed(f"GLT,<XML>,{glt}".encode()) == ("GLT,<XML>,", glt)
    assert decoder.feed(b"GLT,<XML>,<ScannerInfo />") == ()


def test_decoder_reassembles_numbered_glt_datagrams_in_source_order() -> None:
    completed: list[str] = []
    decoder = UdpDatagramDecoder(completion_handler=completed.append)
    decoder.expect_command("GLT,FL")
    first = (
        b'GLT,<XML>,<GLT Version="future"><FL Index="0" Name="First" '
        b'FutureAttr="preserve-me" /><Footer No="1" EOT="0" /></GLT>'
    )
    second = (
        b'GLT,<XML>,<GLT Version="future"><FL Index="1" Name="Second" />'
        b'<Foot No="2" EOT="1" /></GLT>'
    )

    assert decoder.feed(first) == ()
    assert completed == []
    lines = decoder.feed(second)

    assert completed == ["GLT"]
    assert lines[0] == "GLT,<XML>,"
    root = ET.fromstring(lines[1])
    assert root.tag == "GLT"
    assert root.attrib == {"Version": "future"}
    assert [child.attrib["Name"] for child in root] == ["First", "Second"]
    assert root[0].attrib["FutureAttr"] == "preserve-me"


def test_radio_network_parses_bare_scanner_info() -> None:
    fake = FakeDatagramSocket()
    factory = FakeDatagramSocketFactory(fake)
    radio = SDS200.network("192.0.2.25", socket_factory=factory)
    xml = (
        b'<?xml version="1.0" encoding="utf-8"?>\r'
        b'<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">\r'
        b'<System Name="Utah Communications Authority (P25)" />\r'
        b'<Department Name="Harris Dynamic Patch - Northern Utah" />\r'
        b'<TGID Name="Patch 65132" TGID="TGID:65132" />\r'
        b'<Property Sig="5" />\r'
        b'</ScannerInfo>\r'
    )

    with radio:
        def respond() -> None:
            wait_until(lambda: fake.sent == [b"GSI\r"])
            fake.feed(xml)

        thread = threading.Thread(target=respond)
        thread.start()
        info = radio.get_scanner_info(timeout=1.0)
        thread.join(timeout=1.0)

    assert info.system == "Utah Communications Authority (P25)"
    assert info.department == "Harris Dynamic Patch - Northern Utah"
    assert info.channel == "Patch 65132"
    assert info.signal == 5


def test_udp_transport_retries_after_fragment_gap_and_tracks_statistics() -> None:
    fake = FakeDatagramSocket()
    transport = UdpTransport(
        "192.0.2.25",
        socket_factory=FakeDatagramSocketFactory(fake),
        reconnect=False,
        max_xml_retries=2,
    )
    diagnostics = []
    transport.set_diagnostic_handler(diagnostics.append)
    transport.start(lambda line: None)
    try:
        transport.write_command("GSI")
        fake.feed(
            b'GSI,<XML>,<ScannerInfo><System Name="One" />'
            b'<Footer No="1" EOT="0" /></ScannerInfo>'
        )
        fake.feed(
            b'GSI,<XML>,<ScannerInfo><System Name="Three" />'
            b'<Footer No="3" EOT="1" /></ScannerInfo>'
        )
        wait_until(lambda: fake.sent == [b"GSI\r", b"GSI\r"])
    finally:
        transport.stop()

    assert diagnostics[0].kind == "sequence_gap"
    assert transport.statistics["retries_sent"] == 1
    assert transport.statistics["xml_fragments_dropped"] == 1


def test_udp_transport_retries_glt_with_exact_original_wire_command() -> None:
    fake = FakeDatagramSocket()
    transport = UdpTransport(
        "192.0.2.25",
        socket_factory=FakeDatagramSocketFactory(fake),
        reconnect=False,
        max_xml_retries=2,
    )
    diagnostics: list[TransportDiagnostic] = []
    transport.set_diagnostic_handler(diagnostics.append)
    transport.start(lambda _line: None)
    try:
        transport.write_command("GLT,FL")
        fake.feed(
            b'GLT,<XML>,<GLT><FL Index="0" /><Footer No="1" EOT="0" /></GLT>'
        )
        fake.feed(
            b'GLT,<XML>,<GLT><FL Index="2" /><Footer No="3" EOT="1" /></GLT>'
        )
        wait_until(lambda: fake.sent == [b"GLT,FL\r", b"GLT,FL\r"])
    finally:
        transport.stop()

    assert diagnostics[0].kind == "sequence_gap"
    assert transport.statistics["commands_sent"] == 2
    assert transport.statistics["retries_sent"] == 1
    assert transport.statistics["xml_fragments_dropped"] == 1


def test_radio_network_parses_bare_glt_favorites() -> None:
    fake = FakeDatagramSocket()
    radio = SDS200.network(
        "scanner.example.test",
        socket_factory=FakeDatagramSocketFactory(fake),
    )
    xml = (
        b'<GLT Version="future"><FL Index="0" Name="Example Favorites A" '
        b'Monitor="On" Q_Key="1" N_Tag="None" FutureAttr="preserve-me" />'
        b'<FL Index="1" Name="Example Favorites B" Monitor="On" Q_Key="1" '
        b'N_Tag="None" /></GLT>'
    )

    with radio:
        def respond() -> None:
            wait_until(lambda: fake.sent == [b"GLT,FL\r"])
            fake.feed(xml)

        thread = threading.Thread(target=respond)
        thread.start()
        response = radio.get_glt_favorites(timeout=1.0)
        thread.join(timeout=1.0)

    favorites = response.records_by_tag("FL")
    assert [record.attributes["Name"] for record in favorites] == [
        "Example Favorites A",
        "Example Favorites B",
    ]
    assert favorites[0].attributes["FutureAttr"] == "preserve-me"
    assert fake.sent == [b"GLT,FL\r"]


def test_udp_transport_statistics_count_completed_xml() -> None:
    fake = FakeDatagramSocket()
    transport = UdpTransport(
        "192.0.2.25",
        socket_factory=FakeDatagramSocketFactory(fake),
        reconnect=False,
    )
    received: list[str] = []
    transport.start(received.append)
    try:
        transport.write_command("GSI")
        fake.feed(b'<ScannerInfo Mode="Trunk Scan"><Property Sig="4" /></ScannerInfo>')
        wait_until(lambda: transport.statistics["xml_documents_completed"] == 1)
    finally:
        transport.stop()

    assert transport.statistics["commands_sent"] == 1
    assert transport.statistics["datagrams_received"] == 1
    assert transport.statistics["bytes_received"] > 0


def test_udp_transport_does_not_complete_malformed_glt() -> None:
    fake = FakeDatagramSocket()
    transport = UdpTransport(
        "192.0.2.25",
        socket_factory=FakeDatagramSocketFactory(fake),
        reconnect=False,
    )
    received: list[str] = []
    malformed = b'<GLT><FL Index="0"></GLT>'
    valid = b'<GLT><FL Index="0" /></GLT>'
    transport.start(received.append)
    try:
        transport.write_command("GLT,FL")
        fake.feed(malformed)
        wait_until(lambda: len(received) == 2)
        assert received == ["GLT,<XML>,", malformed.decode()]
        assert transport.statistics["xml_documents_completed"] == 0

        fake.feed(valid)
        wait_until(lambda: transport.statistics["xml_documents_completed"] == 1)
    finally:
        transport.stop()

    assert received == [
        "GLT,<XML>,",
        malformed.decode(),
        "GLT,<XML>,",
        valid.decode(),
    ]
    assert transport.statistics["xml_documents_completed"] == 1


def test_udp_transport_reconnects_with_policy() -> None:
    first = FakeDatagramSocket()
    second = FakeDatagramSocket()
    factory = DatagramSocketSequenceFactory([first, second])
    diagnostics: list[TransportDiagnostic] = []
    transport = UdpTransport(
        "192.0.2.25",
        read_timeout=0.01,
        reconnect_policy=ReconnectPolicy(
            initial_delay=0.01,
            multiplier=2.0,
            max_delay=0.02,
            max_attempts=2,
        ),
        socket_factory=factory,
    )
    transport.set_diagnostic_handler(diagnostics.append)

    transport.start(lambda line: None)
    first.incoming.put(OSError("simulated disconnect"))
    deadline = time.monotonic() + 1.0
    while transport.statistics["socket_reopens"] != 1 and time.monotonic() < deadline:
        time.sleep(0.005)

    try:
        assert transport.connected
        assert transport.statistics["reconnect_attempts"] == 1
        assert [diagnostic.kind for diagnostic in diagnostics] == [
            "reconnect_scheduled",
            "reconnected",
        ]
    finally:
        transport.stop()
