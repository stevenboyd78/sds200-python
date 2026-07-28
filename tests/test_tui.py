from __future__ import annotations

import asyncio

from rich.text import Text
from textual.widgets import Static

from sds200.theme import DEFAULT_DARK_THEME, DEFAULT_LIGHT_THEME
from sds200.tui import ScannerIdentity, ScannerTuiApp
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


def _app() -> ScannerTuiApp:
    return ScannerTuiApp(
        ScannerIdentity(
            endpoint="udp://192.168.0.251:50536",
            model="SDS200",
            firmware="Version 1.26.01",
        ),
        ScannerInfoParser().parse("GSI", XML),
        palette=DEFAULT_DARK_THEME,
    )


def _plain(widget: Static) -> str:
    content = widget.content
    assert isinstance(content, Text)
    return content.plain


def test_tui_shell_renders_identity_and_semantic_snapshot() -> None:
    async def exercise() -> None:
        app = _app()
        async with app.run_test(size=(80, 32)):
            assert "CONNECTED" in _plain(app.query_one("#connection", Static))
            assert "SDS200" in _plain(app.query_one("#identity", Static))
            assert "Utah Communications Authority" in _plain(
                app.query_one("#system", Static)
            )
            assert "Patch 65132" in _plain(app.query_one("#channel", Static))
            state = _plain(app.query_one("#state", Static))
            assert "RECEIVING" in state
            assert "STRONG (5)" in state
            assert "RECORDING" in state
            assert "UNMUTED" in state

    asyncio.run(exercise())


def test_tui_theme_binding_switches_semantic_palettes() -> None:
    async def exercise() -> None:
        app = _app()
        async with app.run_test(size=(80, 32)) as pilot:
            assert app.palette is DEFAULT_DARK_THEME
            await pilot.press("t")
            await pilot.pause()
            assert app.palette is DEFAULT_LIGHT_THEME

    asyncio.run(exercise())


def test_tui_bindings_include_clean_quit() -> None:
    bindings = {(binding.key, binding.action) for binding in ScannerTuiApp.BINDINGS}
    assert ("q", "quit") in bindings
    assert ("t", "toggle_theme") in bindings
    assert ("h", "hold_channel") in bindings
    assert ("n", "next_channel") in bindings
    assert ("p", "previous_channel") in bindings
    assert ("plus", "volume_up") in bindings
    assert ("minus", "volume_down") in bindings
    assert ("right_square_bracket", "squelch_up") in bindings
    assert ("left_square_bracket", "squelch_down") in bindings
