from io import StringIO

import pytest
from rich.text import Text

from sds200 import cli
from sds200.rich_cli import (
    RichCliRenderer,
    palette_for_name,
    resolve_color_mode,
)
from sds200.theme import DEFAULT_DARK_THEME, DEFAULT_LIGHT_THEME
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


@pytest.mark.parametrize(
    ("requested", "environment", "expected"),
    [
        ("auto", {}, "auto"),
        ("auto", {"NO_COLOR": ""}, "never"),
        ("auto", {"FORCE_COLOR": "1"}, "always"),
        ("auto", {"FORCE_COLOR": "0"}, "never"),
        ("auto", {"NO_COLOR": "1", "FORCE_COLOR": "1"}, "never"),
        ("always", {"NO_COLOR": "1"}, "always"),
        ("never", {"FORCE_COLOR": "1"}, "never"),
    ],
)
def test_color_mode_precedence(
    requested: str,
    environment: dict[str, str],
    expected: str,
) -> None:
    assert resolve_color_mode(requested, environ=environment) == expected


def test_color_mode_rejects_unknown_value() -> None:
    with pytest.raises(ValueError, match="color mode"):
        resolve_color_mode("sometimes", environ={})


def test_palette_names_select_built_in_themes() -> None:
    assert palette_for_name("dark") is DEFAULT_DARK_THEME
    assert palette_for_name(" LIGHT ") is DEFAULT_LIGHT_THEME
    with pytest.raises(ValueError, match="theme"):
        palette_for_name("solarized")


def test_parser_exposes_color_and_theme_controls() -> None:
    args = cli.build_parser().parse_args(
        ["--color", "always", "--theme", "light", "scanner-info"]
    )
    assert args.color == "always"
    assert args.theme == "light"

    no_color = cli.build_parser().parse_args(["--no-color", "scanner-info"])
    assert no_color.color == "never"


def test_color_changes_styling_but_not_scanner_information() -> None:
    info = ScannerInfoParser().parse("GSI", XML)
    plain_output = StringIO()
    styled_output = StringIO()

    plain = RichCliRenderer(file=plain_output, color="never")
    styled = RichCliRenderer(
        palette=DEFAULT_LIGHT_THEME,
        file=styled_output,
        color="always",
    )
    plain.print_scanner_info(info)
    styled.print_scanner_info(info)

    plain_text = plain_output.getvalue()
    styled_text = styled_output.getvalue()
    assert plain.color_mode == "never"
    assert styled.color_mode == "always"
    assert "\x1b[" not in plain_text
    assert "\x1b[" in styled_text
    assert Text.from_ansi(styled_text).plain.splitlines() == plain_text.splitlines()
    assert "Signal:     5" in plain_text
    assert "Recording:  On" in plain_text
    assert "Mute:       Unmute" in plain_text
