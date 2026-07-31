from pathlib import Path

import pytest

from sds200.models import ScannerInfo
from sds200.state import (
    RadioState,
    ScannerScreenKind,
    classify_scanner_screen,
    snapshot_from_scanner_info,
)
from sds200.xml_protocol import ScannerInfoParser

FIXTURES = Path(__file__).parent / "fixtures" / "scanner_info"


def _fixture(name: str, command: str = "PSI") -> ScannerInfo:
    xml = (FIXTURES / name).read_text(encoding="utf-8")
    return ScannerInfoParser().parse(command, xml)


@pytest.mark.parametrize(
    ("fixture_name", "expected"),
    [
        ("synthetic-scan.xml", ScannerScreenKind.SCANNING),
        ("synthetic-quick-search.xml", ScannerScreenKind.SEARCH),
        ("synthetic-close-call.xml", ScannerScreenKind.CLOSE_CALL),
        ("synthetic-weather.xml", ScannerScreenKind.WEATHER),
        ("synthetic-tone-out.xml", ScannerScreenKind.TONE_OUT),
        ("synthetic-unknown.xml", ScannerScreenKind.UNKNOWN),
    ],
)
def test_scanner_screen_fixture_classification(
    fixture_name: str,
    expected: ScannerScreenKind,
) -> None:
    info = _fixture(fixture_name)
    snapshot = snapshot_from_scanner_info(info)

    assert snapshot.mode == info.mode
    assert snapshot.screen == info.screen
    assert snapshot.screen_kind is expected


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("SrchFrequency", ScannerScreenKind.SEARCH),
        ("CcHitsChannel", ScannerScreenKind.CLOSE_CALL),
        ("WxChannel", ScannerScreenKind.WEATHER),
        ("ToneOutChannel", ScannerScreenKind.TONE_OUT),
    ],
)
def test_special_channel_node_is_classifier_fallback(
    tag: str,
    expected: ScannerScreenKind,
) -> None:
    xml = (
        '<ScannerInfo Mode="Unrecognized" V_Screen="future_screen">'
        f'<{tag} Name="Synthetic Channel" />'
        "</ScannerInfo>"
    )
    info = ScannerInfoParser().parse("PSI", xml)

    assert classify_scanner_screen(info) is expected


def test_explicit_screen_text_precedes_node_fallback() -> None:
    xml = """
<ScannerInfo Mode="Weather Scan" V_Screen="weather_scan">
<SrchFrequency Name="Synthetic Search Node" />
</ScannerInfo>
"""
    info = ScannerInfoParser().parse("PSI", xml)

    assert classify_scanner_screen(info) is ScannerScreenKind.WEATHER


def test_special_node_precedes_generic_search_text() -> None:
    xml = """
<ScannerInfo Mode="Search Hold" V_Screen="search">
<CcHitsChannel Name="Synthetic Close Call Hit" />
</ScannerInfo>
"""
    info = ScannerInfoParser().parse("PSI", xml)

    assert classify_scanner_screen(info) is ScannerScreenKind.CLOSE_CALL


def test_generic_search_text_without_special_node() -> None:
    xml = '''
<ScannerInfo Mode="Quick Search" V_Screen="search">
<Property VOL="8" SQL="2" Sig="0" Rssi="-120" Mute="Mute" Rec="Off" />
</ScannerInfo>
'''
    info = ScannerInfoParser().parse("PSI", xml)

    assert classify_scanner_screen(info) is ScannerScreenKind.SEARCH


def test_empty_snapshot_has_no_reported_screen_kind() -> None:
    assert RadioState().snapshot.screen_kind is None


@pytest.mark.parametrize(
    ("before_name", "after_name", "before_kind", "after_kind"),
    [
        (
            "synthetic-scan.xml",
            "synthetic-quick-search.xml",
            ScannerScreenKind.SCANNING,
            ScannerScreenKind.SEARCH,
        ),
        (
            "synthetic-quick-search.xml",
            "synthetic-close-call.xml",
            ScannerScreenKind.SEARCH,
            ScannerScreenKind.CLOSE_CALL,
        ),
        (
            "synthetic-close-call.xml",
            "synthetic-weather.xml",
            ScannerScreenKind.CLOSE_CALL,
            ScannerScreenKind.WEATHER,
        ),
        (
            "synthetic-weather.xml",
            "synthetic-tone-out.xml",
            ScannerScreenKind.WEATHER,
            ScannerScreenKind.TONE_OUT,
        ),
        (
            "synthetic-tone-out.xml",
            "synthetic-unknown.xml",
            ScannerScreenKind.TONE_OUT,
            ScannerScreenKind.UNKNOWN,
        ),
    ],
)
def test_gsi_to_psi_screen_transition(
    before_name: str,
    after_name: str,
    before_kind: ScannerScreenKind,
    after_kind: ScannerScreenKind,
) -> None:
    state = RadioState()

    initial = state.update(_fixture(before_name, "GSI"))
    change = state.update(_fixture(after_name, "PSI"))

    assert initial is not None
    assert initial.current.screen_kind is before_kind

    assert change is not None
    assert change.previous.screen_kind is before_kind
    assert change.current.screen_kind is after_kind
    assert change.changed("mode")
    assert change.changed("screen")
    assert change.changed("screen_kind")


@pytest.mark.parametrize(
    ("fixture_name", "expected"),
    [
        ("synthetic-quick-search.xml", "CTCSS 123.0Hz"),
        ("synthetic-close-call.xml", "NAC 293h"),
    ],
)
def test_special_screen_fixture_preserves_detected_sub_audio(
    fixture_name: str,
    expected: str,
) -> None:
    info = _fixture(fixture_name)
    snapshot = snapshot_from_scanner_info(info)

    assert info.sub_audio_detected == expected
    assert snapshot.sub_audio_detected == expected


def test_sub_audio_none_sentinel_is_normalized() -> None:
    xml = """
<ScannerInfo Mode="Quick Search" V_Screen="quick_search">
<SrchFrequency Freq="154.280000MHz" SAD="None" />
</ScannerInfo>
"""
    info = ScannerInfoParser().parse("PSI", xml)
    snapshot = snapshot_from_scanner_info(info)

    assert info.sub_audio_detected is None
    assert snapshot.sub_audio_detected is None


def test_search_to_close_call_transition_reports_sub_audio_change() -> None:
    state = RadioState()
    state.update(_fixture("synthetic-quick-search.xml", "GSI"))

    change = state.update(_fixture("synthetic-close-call.xml", "PSI"))

    assert change is not None
    assert change.previous.sub_audio_detected == "CTCSS 123.0Hz"
    assert change.current.sub_audio_detected == "NAC 293h"
    assert change.changed("sub_audio_detected")


@pytest.mark.parametrize(
    (
        "fixture_name",
        "channel_number",
        "weather_mode",
        "weather_same",
    ),
    [
        (
            "synthetic-weather.xml",
            7,
            "Monitor Weather",
            None,
        ),
        (
            "synthetic-weather-alert.xml",
            4,
            "Weather Alert",
            "Front Range Counties",
        ),
    ],
)
def test_weather_fixture_preserves_protocol_details(
    fixture_name: str,
    channel_number: int,
    weather_mode: str,
    weather_same: str | None,
) -> None:
    info = _fixture(fixture_name)
    snapshot = snapshot_from_scanner_info(info)

    assert info.channel_number == channel_number
    assert info.weather_mode == weather_mode
    assert info.weather_same == weather_same

    assert snapshot.channel_number == channel_number
    assert snapshot.weather_mode == weather_mode
    assert snapshot.weather_same == weather_same


def test_weather_scan_to_alert_transition_reports_weather_changes() -> None:
    state = RadioState()
    state.update(_fixture("synthetic-weather.xml", "GSI"))

    change = state.update(_fixture("synthetic-weather-alert.xml", "PSI"))

    assert change is not None
    assert change.previous.screen_kind is ScannerScreenKind.WEATHER
    assert change.current.screen_kind is ScannerScreenKind.WEATHER
    assert change.previous.weather_mode == "Monitor Weather"
    assert change.current.weather_mode == "Weather Alert"
    assert change.previous.weather_same is None
    assert change.current.weather_same == "Front Range Counties"
    assert change.changed("mode")
    assert change.changed("screen")
    assert change.changed("channel")
    assert change.changed("channel_number")
    assert change.changed("channel_hold")
    assert change.changed("weather_mode")
    assert change.changed("weather_same")


@pytest.mark.parametrize(
    ("reported", "expected"),
    [
        ("None", None),
        ("Alert Only", "Alert Only"),
    ],
)
def test_weather_same_normalization(
    reported: str,
    expected: str | None,
) -> None:
    xml = f"""
<ScannerInfo Mode="Weather Alert" V_Screen="weather_alert">
<WxMode Mode="Weather Alert" SAME="{reported}" />
<WxChannel Name="Synthetic Weather Channel" CH_No="1" />
</ScannerInfo>
"""
    info = ScannerInfoParser().parse("PSI", xml)
    snapshot = snapshot_from_scanner_info(info)

    assert info.weather_same == expected
    assert snapshot.weather_same == expected
