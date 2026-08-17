import pytest

from sds200.exceptions import ProtocolError
from sds200.xml_protocol import GltParser, ScannerInfoParser, XmlResponseAssembler

XML = """<?xml version="1.0" encoding="utf-8"?>
<ScannerInfo Mode="Trunk Scan Hold" V_Screen="trunk_scan">
<MonitorList Name="Full Database" />
<System Name="Calcasieu" />
<Department Name="Parish Fire &amp; Medical" />
<ConvFrequency Name="DeQuincy Fire Department" Freq="154.4150MHz" Mod="NFM" />
<Property VOL="0" SQL="9" Sig="4" Battery="2.7" Rssi="-88" Rec="Off" Mute="Mute" />
</ScannerInfo>"""


def test_xml_assembler() -> None:
    assembler = XmlResponseAssembler()
    assert assembler.feed("GSI,<XML>,") is None
    result = None
    for line in XML.splitlines():
        result = assembler.feed(line)
    assert result == ("GSI", XML)


def test_scanner_info_parser() -> None:
    info = ScannerInfoParser().parse("GSI", XML)
    assert info.mode == "Trunk Scan Hold"
    assert info.system == "Calcasieu"
    assert info.department == "Parish Fire & Medical"
    assert info.channel == "DeQuincy Fire Department"
    assert info.frequency == "154.4150MHz"
    assert info.modulation == "NFM"
    assert info.signal == 4
    assert info.battery == 2.7
    assert info.rssi == -88.0
    assert info.recording == "Off"
    assert info.mute == "Mute"
    assert info.raw_xml == XML


REPEATED_SCANNER_INFO_XML = """<ScannerInfo Mode="Synthetic" V_Screen="future">
<System Name="First synthetic system" FutureSystemAttr="keep-system" />
<FutureRecord Value="first" FutureAttr="keep-first">
  <NestedFutureRecord Value="nested" NestedAttr="keep-nested" />
</FutureRecord>
<Department Name="Synthetic department" />
<FutureRecord Value="second" FutureAttr="keep-second" />
<Property VOL="1" Sig="3" />
</ScannerInfo>"""


def test_scanner_info_parser_preserves_ordered_repeated_records() -> None:
    info = ScannerInfoParser().parse("PSI", REPEATED_SCANNER_INFO_XML)

    assert [record.tag for record in info.records] == [
        "System",
        "FutureRecord",
        "NestedFutureRecord",
        "Department",
        "FutureRecord",
        "Property",
    ]
    assert [
        dict(record.attributes) for record in info.records_by_tag("FutureRecord")
    ] == [
        {"Value": "first", "FutureAttr": "keep-first"},
        {"Value": "second", "FutureAttr": "keep-second"},
    ]
    assert dict(info.records[0].attributes) == {
        "Name": "First synthetic system",
        "FutureSystemAttr": "keep-system",
    }
    assert dict(info.records[2].attributes) == {
        "Value": "nested",
        "NestedAttr": "keep-nested",
    }
    assert info.node("FutureRecord") is info.records_by_tag("FutureRecord")[-1]
    assert info.node("FutureRecord").get("Value") == "second"
    assert info.system == "First synthetic system"
    assert info.department == "Synthetic department"
    assert info.signal == 3
    assert info.raw_xml == REPEATED_SCANNER_INFO_XML


def test_xml_assembler_resynchronizes_on_a_new_header() -> None:
    assembler = XmlResponseAssembler()
    assert assembler.feed("PSI,<XML>,") is None
    assert assembler.feed("<ScannerInfo>") is None
    assert assembler.feed("PSI,<XML>,") is None

    result = None
    for line in XML.splitlines():
        result = assembler.feed(line)

    assert result == ("PSI", XML)


GLT_XML = """<GLT Version="future">
<FL Index="0" Name="First" Monitor="On" Q_Key="1" N_Tag="None" FutureAttr="keep" />
<FL Index="1" Name="Second" Monitor="Off" Q_Key="2" N_Tag="Tag" />
<FutureRecord Value="unknown" FutureChildAttr="also-keep" />
</GLT>"""


def test_xml_assembler_assembles_glt_with_its_root() -> None:
    assembler = XmlResponseAssembler()
    assert assembler.feed("GLT,<XML>,") is None

    result = None
    for line in GLT_XML.splitlines():
        result = assembler.feed(line)

    assert result == ("GLT", GLT_XML)


@pytest.mark.parametrize(
    ("first_header", "first_root", "second_header", "expected_command", "xml"),
    [
        ("GSI,<XML>,", "<ScannerInfo>", "GLT,<XML>,", "GLT", GLT_XML),
        ("GLT,<XML>,", "<GLT>", "GSI,<XML>,", "GSI", XML),
    ],
)
def test_xml_assembler_resynchronizes_across_command_root_pairs(
    first_header: str,
    first_root: str,
    second_header: str,
    expected_command: str,
    xml: str,
) -> None:
    assembler = XmlResponseAssembler()
    assert assembler.feed(first_header) is None
    assert assembler.feed(first_root) is None
    assert assembler.feed(second_header) is None

    result = None
    for line in xml.splitlines():
        result = assembler.feed(line)

    assert result == (expected_command, xml)


def test_xml_assembler_does_not_accept_arbitrary_xml_headers() -> None:
    assembler = XmlResponseAssembler()

    assert assembler.feed("FUTURE,<XML>,") is None
    assert assembler.collecting is False
    assert assembler.recognizes_header("FUTURE,<XML>,") is False


def test_glt_parser_preserves_lossless_direct_records() -> None:
    response = GltParser().parse("GLT", GLT_XML)

    assert response.command == "GLT"
    assert dict(response.root_attributes) == {"Version": "future"}
    assert [record.tag for record in response.records] == [
        "FL",
        "FL",
        "FutureRecord",
    ]
    assert [record.attributes["Name"] for record in response.records_by_tag("FL")] == [
        "First",
        "Second",
    ]
    assert dict(response.records[0].attributes) == {
        "Index": "0",
        "Name": "First",
        "Monitor": "On",
        "Q_Key": "1",
        "N_Tag": "None",
        "FutureAttr": "keep",
    }
    assert dict(response.records[2].attributes) == {
        "Value": "unknown",
        "FutureChildAttr": "also-keep",
    }
    assert response.raw_xml == GLT_XML


def test_glt_parser_rejects_malformed_xml() -> None:
    with pytest.raises(ProtocolError, match="^Invalid GLT XML response$"):
        GltParser().parse("GLT", "<GLT><FL></GLT>")


def test_glt_parser_rejects_wrong_root() -> None:
    with pytest.raises(ProtocolError, match="Expected GLT root"):
        GltParser().parse("GLT", "<ScannerInfo />")
