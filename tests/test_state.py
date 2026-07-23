from sds200.state import RadioState
from sds200.xml_protocol import ScannerInfoParser

XML = """<?xml version="1.0" encoding="utf-8"?>
<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">
<System Name="Utah Communications Authority (P25)" />
<Department Name="Harris Dynamic Patch - Northern Utah" />
<Site Name="Utah County Simulcast" Mod="NFM" />
<TGID Name="Patch 65132" TGID="TGID:65132" SvcType="Interop" U_Id="UID:9190014" />
<SiteFrequency Freq=" 769.431250MHz" />
<Property VOL="10" SQL="2" Sig="5" Rssi="-42" P25Status="P25" Mute="Unmute" Rec="Off" />
</ScannerInfo>"""


def test_state_change_contains_rich_scanner_information() -> None:
    info = ScannerInfoParser().parse("PSI", XML)
    state = RadioState()

    change = state.update(info)

    assert change is not None
    assert change.current.site == "Utah County Simulcast"
    assert change.current.frequency == "769.431250MHz"
    assert change.current.modulation == "NFM"
    assert change.current.service_type == "Interop"
    assert change.current.volume == 10
    assert change.current.signal == 5
    assert change.changed("channel")


def test_identical_state_does_not_emit_a_change() -> None:
    info = ScannerInfoParser().parse("PSI", XML)
    state = RadioState()

    assert state.update(info) is not None
    assert state.update(info) is None
