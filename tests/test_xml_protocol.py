from sds200.xml_protocol import ScannerInfoParser, XmlResponseAssembler

XML = """<?xml version="1.0" encoding="utf-8"?>
<ScannerInfo Mode="Trunk Scan Hold" V_Screen="trunk_scan">
<MonitorList Name="Full Database" />
<System Name="Calcasieu" />
<Department Name="Parish Fire &amp; Medical" />
<ConvFrequency Name="DeQuincy Fire Department" Freq="154.4150MHz" Mod="NFM" />
<Property VOL="0" SQL="9" Sig="4" />
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
