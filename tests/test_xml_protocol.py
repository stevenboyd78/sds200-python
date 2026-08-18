import pytest

from sds200.exceptions import ProtocolError
from sds200.xml_protocol import (
    AnalysisParser,
    GltParser,
    MsiParser,
    ScannerInfoParser,
    XmlResponseAssembler,
)

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


AST_XML = """<AST FutureRoot="keep-root">
<CurrentActivity Frequency="155.012500" TGID="1001" FutureAttr="keep-first" />
<Container><FutureRecord Value="nested" /></Container>
<CurrentActivity Frequency="155.112500" TGID="1002" />
</AST>"""


def test_xml_assembler_assembles_ast_with_its_root() -> None:
    assembler = XmlResponseAssembler()
    assert assembler.feed("AST,<XML>,") is None
    result = None
    for line in AST_XML.splitlines():
        result = assembler.feed(line)
    assert result == ("AST", AST_XML)


def test_analysis_parser_preserves_all_descendants_in_source_order() -> None:
    response = AnalysisParser().parse("AST", AST_XML)
    assert response.command == "AST"
    assert dict(response.root_attributes) == {"FutureRoot": "keep-root"}
    assert [record.tag for record in response.records] == [
        "CurrentActivity",
        "Container",
        "FutureRecord",
        "CurrentActivity",
    ]
    assert [record.attributes["TGID"] for record in response.records_by_tag(
        "CurrentActivity"
    )] == ["1001", "1002"]
    assert response.records[0].attributes["FutureAttr"] == "keep-first"
    assert response.records[2].attributes["Value"] == "nested"
    assert response.raw_xml == AST_XML
    with pytest.raises(TypeError):
        response.root_attributes["new"] = "value"  # type: ignore[index]


def test_analysis_parser_rejects_malformed_xml_without_payload_text() -> None:
    payload = "<AST><Secret Value='do-not-echo'></AST>"
    with pytest.raises(ProtocolError, match="^Invalid AST XML response$") as caught:
        AnalysisParser().parse("AST", payload)
    assert "do-not-echo" not in str(caught.value)


def test_analysis_parser_rejects_wrong_root() -> None:
    with pytest.raises(ProtocolError, match="^Expected AST root"):
        AnalysisParser().parse("AST", "<ScannerInfo />")

MSI_XML = """<MSI FutureRoot="keep-root">
<SyntheticRecord SyntheticId="first" FutureAttr="keep-first" />
<Container><FutureRecord Value="nested" FutureNested="keep-nested" /></Container>
<SyntheticRecord SyntheticId="second" />
</MSI>"""


def test_default_xml_assembler_assembles_msi() -> None:
    assembler = XmlResponseAssembler()

    assert assembler.recognizes_header("MSI,<XML>,") is True
    assert assembler.feed("MSI,<XML>,") is None

    result = None
    for line in MSI_XML.splitlines():
        result = assembler.feed(line)

    assert result == ("MSI", MSI_XML)


def test_msi_parser_preserves_all_descendants_in_source_order() -> None:
    response = MsiParser().parse("MSI", MSI_XML)

    assert response.command == "MSI"
    assert dict(response.root_attributes) == {"FutureRoot": "keep-root"}
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
    assert response.records[0].attributes["FutureAttr"] == "keep-first"
    assert response.records[2].attributes == {
        "Value": "nested",
        "FutureNested": "keep-nested",
    }
    assert response.raw_xml == MSI_XML

    with pytest.raises(TypeError):
        response.root_attributes["new"] = "value"  # type: ignore[index]
    with pytest.raises(TypeError):
        response.records[0].attributes["new"] = "value"  # type: ignore[index]


MSI_DOCUMENTED_MENU_XML = """<MSI Name="Menu title" Index="menu-index"
 MenuType="TypeSelect" Value="current-value" Selected="selected-raw"
 FutureRoot="keep-root">
<MenuItem Name="Item A" Index="item-a" Value="value-a" FutureItem="keep-item" />
<MenuItem Name="Item B" Index="item-b" Value="value-b" />
<MenuInput MaxLength="64" EnableKeys="ABC123" AddedInformation="info"
 FutureInput="keep-input" />
<MenuLocation MaxLength="123" EnableKeys="0123456789.-" IsLatitude="1"
 FutureLocation="keep-location" />
<MenuErrorMsg Text="error text" ScanButton="0" FutureError="keep-error" />
<FutureMenuNode FutureValue="keep-future" />
</MSI>"""


def test_msi_documented_menu_projection_preserves_exact_strings_and_unknowns() -> None:
    response = MsiParser().parse("MSI", MSI_DOCUMENTED_MENU_XML)
    projection = response.menu_projection

    assert projection.name == "Menu title"
    assert projection.index == "menu-index"
    assert projection.menu_type == "TypeSelect"
    assert projection.value == "current-value"
    assert projection.selected == "selected-raw"
    assert projection.root_attributes["FutureRoot"] == "keep-root"

    assert [item.name for item in projection.menu_items] == ["Item A", "Item B"]
    assert [item.index for item in projection.menu_items] == ["item-a", "item-b"]
    assert [item.value for item in projection.menu_items] == ["value-a", "value-b"]
    assert projection.menu_items[0].attributes["FutureItem"] == "keep-item"

    assert projection.menu_inputs[0].max_length == "64"
    assert projection.menu_inputs[0].enable_keys == "ABC123"
    assert projection.menu_inputs[0].added_information == "info"
    assert projection.menu_inputs[0].attributes["FutureInput"] == "keep-input"

    assert projection.menu_locations[0].max_length == "123"
    assert projection.menu_locations[0].enable_keys == "0123456789.-"
    assert projection.menu_locations[0].is_latitude == "1"
    assert projection.menu_locations[0].attributes["FutureLocation"] == (
        "keep-location"
    )

    assert projection.error_messages[0].text == "error text"
    assert projection.error_messages[0].scan_button == "0"
    assert projection.error_messages[0].attributes["FutureError"] == "keep-error"

    assert projection.records == response.records
    assert response.records_by_tag("FutureMenuNode")[0].attributes["FutureValue"] == (
        "keep-future"
    )
    assert response.raw_xml == MSI_DOCUMENTED_MENU_XML

    with pytest.raises(TypeError):
        projection.root_attributes["new"] = "value"  # type: ignore[index]
    with pytest.raises(TypeError):
        projection.menu_items[0].attributes["new"] = "value"  # type: ignore[index]


def test_msi_documented_projection_does_not_reject_partial_or_future_values() -> None:
    xml = (
        '<MSI MenuType="FutureMenuType" Selected="future-selected">'
        '<MenuItem FutureItem="keep" />'
        '<MenuInput MaxLength="future-length" />'
        '<MenuLocation IsLatitude="future-coordinate-kind" />'
        '<MenuErrorMsg ScanButton="future-button" />'
        '<FutureNode Future="keep" />'
        "</MSI>"
    )

    response = MsiParser().parse("MSI", xml)
    projection = response.menu_projection

    assert projection.name is None
    assert projection.index is None
    assert projection.menu_type == "FutureMenuType"
    assert projection.value is None
    assert projection.selected == "future-selected"

    assert projection.menu_items[0].name is None
    assert projection.menu_items[0].index is None
    assert projection.menu_items[0].value is None
    assert projection.menu_items[0].attributes["FutureItem"] == "keep"

    assert projection.menu_inputs[0].max_length == "future-length"
    assert projection.menu_inputs[0].enable_keys is None
    assert projection.menu_inputs[0].added_information is None

    assert projection.menu_locations[0].is_latitude == "future-coordinate-kind"
    assert projection.error_messages[0].scan_button == "future-button"
    assert response.records_by_tag("FutureNode")[0].attributes["Future"] == "keep"


def test_msi_parser_rejects_malformed_xml_without_payload_text() -> None:
    payload = "<MSI><SyntheticSecret Value='do-not-echo'></MSI>"

    with pytest.raises(ProtocolError, match="^Invalid MSI XML response$") as caught:
        MsiParser().parse("MSI", payload)

    assert "do-not-echo" not in str(caught.value)


def test_msi_parser_rejects_wrong_root() -> None:
    with pytest.raises(ProtocolError, match="^Expected MSI root"):
        MsiParser().parse("MSI", "<ScannerInfo />")
