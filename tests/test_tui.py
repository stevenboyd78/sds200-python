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
    assert isinstance(content, (str, Text))
    return content if isinstance(content, str) else content.plain


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
    assert ("question_mark", "toggle_key_help") in bindings
    assert ("h", "hold_channel") in bindings
    assert ("s", "hold_system") in bindings
    assert ("d", "hold_department") in bindings
    assert ("i", "hold_site") in bindings
    assert ("n", "next_channel") in bindings
    assert ("p", "previous_channel") in bindings
    assert ("plus", "volume_up") in bindings
    assert ("minus", "volume_down") in bindings
    assert ("right_square_bracket", "squelch_up") in bindings
    assert ("left_square_bracket", "squelch_down") in bindings


def test_tui_responsive_breakpoints_and_key_help() -> None:
    async def exercise() -> None:
        compact = _app()
        async with compact.run_test(size=(64, 20)) as pilot:
            await pilot.pause()
            assert compact.screen.has_class("-compact")
            assert compact.screen.has_class("-short")
            assert not compact.key_help_visible

            await pilot.press("question_mark")
            await pilot.pause()
            assert compact.key_help_visible
            assert compact.screen.has_class("show-keys")
            keys = _plain(compact.query_one("#keys", Static))
            assert "Hold current channel" in keys
            assert "Hold current system / department" in keys
            assert "Hold current site" in keys
            assert "Raise / lower squelch" in keys

            await pilot.press("question_mark")
            await pilot.pause()
            assert not compact.key_help_visible
            assert not compact.screen.has_class("show-keys")

        wide = _app()
        async with wide.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert wide.screen.has_class("-wide")
            assert wide.screen.has_class("-tall")

    asyncio.run(exercise())
