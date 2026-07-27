from io import StringIO

import pytest
from rich.console import Console

from sds200.models import ScannerInfo
from sds200.rich_cli import RichCliRenderer, rich_style
from sds200.theme import DEFAULT_DARK_THEME, DEFAULT_LIGHT_THEME, ThemeRole, ThemeStyle
from sds200.xml_protocol import ScannerInfoParser

XML = """<?xml version="1.0" encoding="utf-8"?>
<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">
<System Name="Utah Communications Authority (P25)" />
<Department Name="Harris Dynamic Patch - Northern Utah" />
<Site Name="Utah County Simulcast" Mod="NFM" />
<TGID Name="Patch 65132" TGID="TGID:65132" SvcType="Interop" U_Id="UID:9190014" />
<SiteFrequency Freq="769.431250MHz" />
<Property VOL="10" SQL="2" Sig="5" Rssi="-42" Rec="On" Mute="Unmute" />
</ScannerInfo>"""


def _scanner_info() -> ScannerInfo:
    return ScannerInfoParser().parse("GSI", XML)


def test_rich_style_preserves_renderer_neutral_attributes() -> None:
    style = rich_style(
        ThemeStyle(
            foreground="#112233",
            background="#445566",
            bold=True,
            dim=True,
            underline=True,
        )
    )

    assert style.color is not None
    assert style.bgcolor is not None
    assert style.bold is True
    assert style.dim is True
    assert style.underline is True


def test_renderer_rejects_console_and_file_together() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        RichCliRenderer(console=Console(), file=StringIO())


def test_scanner_info_plain_output_preserves_legacy_layout() -> None:
    output = StringIO()
    renderer = RichCliRenderer(
        console=Console(
            file=output,
            force_terminal=False,
            color_system=None,
            highlight=False,
            markup=False,
            width=20,
        )
    )

    renderer.print_scanner_info(_scanner_info())

    assert output.getvalue().splitlines() == [
        "Mode:       Trunk Scan",
        "Screen:     trunk_scan",
        "System:     Utah Communications Authority (P25)",
        "Department: Harris Dynamic Patch - Northern Utah",
        "Site:       Utah County Simulcast",
        "Channel:    Patch 65132",
        "Frequency:  769.431250MHz",
        "Modulation: NFM",
        "Service:    Interop",
        "Signal:     5",
        "RSSI:       -42",
        "Battery:    -",
        "Recording:  On",
        "Mute:       Unmute",
    ]


def test_terminal_output_contains_ansi_styles() -> None:
    output = StringIO()
    renderer = RichCliRenderer(
        console=Console(
            file=output,
            force_terminal=True,
            color_system="truecolor",
            highlight=False,
            markup=False,
            width=120,
        )
    )

    renderer.print_scanner_info(_scanner_info())

    rendered = output.getvalue()
    assert "\x1b[" in rendered
    assert "Signal:" in rendered
    assert "Recording:" in rendered


def test_renderer_accepts_light_palette() -> None:
    renderer = RichCliRenderer(
        palette=DEFAULT_LIGHT_THEME,
        console=Console(file=StringIO(), force_terminal=False),
    )

    assert renderer.palette is DEFAULT_LIGHT_THEME
    assert renderer.style_for(ThemeRole.SIGNAL_STRONG) == rich_style(
        DEFAULT_LIGHT_THEME.resolve(ThemeRole.SIGNAL_STRONG)
    )
    assert renderer.style_for(ThemeRole.SIGNAL_STRONG) != rich_style(
        DEFAULT_DARK_THEME.resolve(ThemeRole.SIGNAL_STRONG)
    )
