from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from rich.text import Text
from textual.widgets import Static

from sds200.theme import DEFAULT_DARK_THEME, DEFAULT_LIGHT_THEME
from sds200.tui import ScannerIdentity, ScannerTuiApp
from sds200.tui_logging import TuiLogBuffer
from sds200.xml_protocol import ScannerInfoParser

FIXTURES = Path(__file__).parent / "fixtures" / "scanner_info"

XML = """<?xml version="1.0" encoding="utf-8"?>
<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">
<System Name="Utah Communications Authority (P25)" />
<Department Name="Harris Dynamic Patch - Northern Utah" />
<Site Name="Utah County Simulcast" Mod="NFM" />
<TGID Name="Patch 65132" TGID="TGID:65132" SvcType="Interop" U_Id="UID:9190014" />
<SiteFrequency Freq="769.431250MHz" />
<Property VOL="10" SQL="2" Sig="5" Rssi="-42" Rec="On" Mute="Unmute" />
</ScannerInfo>"""


def _app(log_buffer: TuiLogBuffer | None = None) -> ScannerTuiApp:
    return ScannerTuiApp(
        ScannerIdentity(
            endpoint="udp://192.168.0.251:50536",
            model="SDS200",
            firmware="Version 1.26.01",
        ),
        ScannerInfoParser().parse("GSI", XML),
        log_buffer=log_buffer,
        palette=DEFAULT_DARK_THEME,
    )


def _fixture_app(name: str) -> ScannerTuiApp:
    xml = (FIXTURES / name).read_text(encoding="utf-8")
    return ScannerTuiApp(
        ScannerIdentity(
            endpoint="udp://192.168.0.251:50536",
            model="SDS200",
            firmware="Version 1.26.01",
        ),
        ScannerInfoParser().parse("GSI", xml),
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
            assert "Utah Communications Authority" in _plain(app.query_one("#system", Static))
            assert "Patch 65132" in _plain(app.query_one("#channel", Static))
            state = _plain(app.query_one("#state", Static))
            assert "RECEIVING" in state
            assert "STRONG (5)" in state
            assert "RECORDING" in state
            assert "UNMUTED" in state
            audio = _plain(app.query_one("#audio", Static))
            assert "Live playback: UNAVAILABLE" in audio
            assert "Recording: UNAVAILABLE" in audio

    asyncio.run(exercise())


def test_tui_renders_mode_aware_quick_search_and_close_call_details() -> None:
    async def exercise() -> None:
        cases = (
            (
                "synthetic-quick-search.xml",
                (
                    "Mode: Quick Search Hold",
                    "V_Screen: quick_search",
                    "State node: SrchFrequency",
                ),
                (
                    "Search frequency: 154.280000MHz",
                    "Modulation: NFM",
                    "Hold: ON",
                    "Signal: GOOD (3)",
                    "RSSI: -82",
                    "Detected tone / code: CTCSS 123.0Hz",
                ),
            ),
            (
                "synthetic-close-call-searching.xml",
                (
                    "Mode: Close Call Only",
                    "V_Screen: cc_searching",
                    "State node: -",
                ),
                (
                    "Close Call frequency: -",
                    "Modulation: -",
                    "Hold: -",
                    "Detected tone / code: -",
                ),
            ),
            (
                "synthetic-close-call.xml",
                (
                    "Mode: Close Call Only",
                    "V_Screen: close_call",
                    "State node: SrchFrequency",
                ),
                (
                    "Close Call frequency: 155.752500MHz",
                    "Modulation: NFM",
                    "Hold: OFF",
                    "Signal: STRONG (4)",
                    "RSSI: -71",
                    "Detected tone / code: NAC 293h",
                ),
            ),
            (
                "synthetic-close-call-hits.xml",
                (
                    "Mode: Close Call",
                    "V_Screen: cchits_with_scan",
                    "State node: CcHitsChannel",
                ),
                (
                    "Close Call hit: Synthetic Close Call Hit",
                    "Frequency: 155.752500MHz",
                    "Modulation: NFM",
                    "Hold: OFF",
                    "Signal: STRONG (4)",
                    "RSSI: -71",
                    "Detected tone / code: NAC 293h",
                ),
            ),
        )

        for fixture_name, system_values, channel_values in cases:
            app = _fixture_app(fixture_name)
            async with app.run_test(size=(80, 36)):
                system_widget = app.query_one("#system", Static)
                channel_widget = app.query_one("#channel", Static)
                system = _plain(system_widget)
                channel = _plain(channel_widget)

                assert system_widget.border_title == "Screen Mode"
                assert channel_widget.border_title == (
                    "Quick Search"
                    if fixture_name == "synthetic-quick-search.xml"
                    else "Close Call"
                )
                for value in system_values:
                    assert value in system
                for value in channel_values:
                    assert value in channel

    asyncio.run(exercise())


def test_tui_panels_have_descriptive_border_titles() -> None:
    async def exercise() -> None:
        app = _app()

        async with app.run_test(size=(120, 40)):
            expected = {
                "#keys": "Keyboard Reference",
                "#connection": "Connection",
                "#identity": "Scanner",
                "#system": "System / Site",
                "#channel": "Channel",
                "#state": "Scanner State",
                "#audio": "Audio",
                "#status": "Live PSI / Controls",
                "#logs": "Operational Logs",
            }

            for selector, title in expected.items():
                assert app.query_one(selector, Static).border_title == title

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
    assert ("c", "reconnect") in bindings
    assert ("ctrl+p", "command_palette") in bindings
    palette_binding = next(
        binding for binding in ScannerTuiApp.BINDINGS if binding.action == "command_palette"
    )
    assert palette_binding.description == "Command Palette"
    assert palette_binding.key_display == "^p"
    assert palette_binding.show
    assert ("question_mark", "toggle_key_help") in bindings
    assert ("g", "toggle_logs") in bindings
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
            assert "Reconnect scanner" in keys
            assert "Show or hide operational logs" in keys
            assert "Command Palette" in keys

            await pilot.press("question_mark")
            await pilot.pause()
            assert not compact.key_help_visible
            assert not compact.screen.has_class("show-keys")

        pi_screen = _app()
        async with pi_screen.run_test(size=(90, 28)) as pilot:
            await pilot.pause()
            assert pi_screen.screen.has_class("-standard")
            assert pi_screen.screen.has_class("-short")
            assert not pi_screen.screen.has_class("-compact")

            body = pi_screen.query_one("#body")
            connection = pi_screen.query_one("#connection")
            system = pi_screen.query_one("#system")
            channel = pi_screen.query_one("#channel")
            state = pi_screen.query_one("#state")
            audio = pi_screen.query_one("#audio")
            status = pi_screen.query_one("#status")
            logs = pi_screen.query_one("#logs")

            assert connection.region.y == body.region.y
            assert system.region.y == connection.region.bottom
            assert channel.region.y == system.region.bottom
            assert state.region.y == channel.region.bottom
            assert audio.region.y == state.region.bottom
            assert status.region.y == audio.region.bottom
            assert status.region.bottom <= body.region.bottom
            assert logs.region.y >= body.region.bottom

        standard = _app()
        async with standard.run_test(size=(90, 32)) as pilot:
            await pilot.pause()
            assert standard.screen.has_class("-standard")
            assert standard.screen.has_class("-tall")

            body = standard.query_one("#body")
            connection = standard.query_one("#connection")
            system = standard.query_one("#system")

            assert connection.region.y > body.region.y
            assert system.region.y > connection.region.bottom

        wide = _app()
        async with wide.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert wide.screen.has_class("-wide")
            assert wide.screen.has_class("-tall")

    asyncio.run(exercise())


def test_tui_log_panel_is_visible_by_default_and_retains_hidden_records() -> None:
    async def exercise() -> None:
        buffer = TuiLogBuffer(limit=3)
        buffer.append("2026-07-30 WARNING sds200.test: first warning")
        app = _app(buffer)

        async with app.run_test(size=(120, 40)) as pilot:
            logs = app.query_one("#logs", Static)
            assert app.logs_visible
            assert "first warning" in _plain(logs)

            await pilot.press("g")
            await pilot.pause()
            assert not app.logs_visible
            assert app.screen.has_class("hide-logs")

            status = app.query_one("#status", Static)
            status_lines = _plain(status).splitlines()
            assert "Detail:" in status_lines[-1]
            assert status.size.height >= len(status_lines)

            buffer.append("2026-07-30 ERROR sds200.test: hidden error")
            app._poll_log_buffer()

            await pilot.press("g")
            await pilot.pause()
            assert app.logs_visible
            assert not app.screen.has_class("hide-logs")
            assert "hidden error" in _plain(logs)

    asyncio.run(exercise())


def test_tui_status_transitions_include_local_since_timestamps() -> None:
    async def exercise() -> None:
        now = [datetime(2026, 7, 28, 4, 18, 32)]
        app = ScannerTuiApp(
            ScannerIdentity(
                endpoint="udp://192.168.0.251:50536",
                model="SDS200",
                firmware="Version 1.26.01",
            ),
            ScannerInfoParser().parse("GSI", XML),
            palette=DEFAULT_DARK_THEME,
            now=lambda: now[0],
        )

        async with app.run_test(size=(80, 32)) as pilot:
            connection = _plain(app.query_one("#connection", Static))
            status = _plain(app.query_one("#status", Static))
            assert "CONNECTED since 04:18:32" in connection
            assert "AVAILABLE since 04:18:32" in status
            assert "NORMAL since 04:18:32" in status

            now[0] = datetime(2026, 7, 28, 4, 20, 5)
            app._apply_connection(False)
            await pilot.pause()
            connection = _plain(app.query_one("#connection", Static))
            status = _plain(app.query_one("#status", Static))
            assert "DISCONNECTED since 04:20:05" in connection
            assert "UNAVAILABLE since 04:20:05" in status
            assert "ERROR since 04:20:05" in status

    asyncio.run(exercise())


def test_tui_renders_mode_aware_weather_details() -> None:
    async def exercise() -> None:
        cases = (
            (
                "synthetic-weather.xml",
                (
                    "Mode: WX Scan",
                    "V_Screen: wx_alert",
                    "State node: WxChannel",
                ),
                (
                    "Weather channel: WX 7",
                    "Frequency: 162.550000MHz",
                    "Modulation: FM",
                    "Weather mode: Monitor Weather",
                    "Hold: OFF",
                    "Signal: STRONG (5)",
                    "RSSI: -58",
                    "SAME selection: -",
                ),
            ),
            (
                "synthetic-weather-hold.xml",
                (
                    "Mode: WX Hold",
                    "V_Screen: wx_alert",
                    "State node: WxChannel",
                ),
                (
                    "Weather channel: WX 7",
                    "Frequency: 162.550000MHz",
                    "Modulation: FM",
                    "Weather mode: Monitor Weather",
                    "Hold: ON",
                    "Signal: STRONG (5)",
                    "RSSI: -58",
                    "SAME selection: -",
                ),
            ),
            (
                "synthetic-weather-alert.xml",
                (
                    "Mode: Weather Alert Hold",
                    "V_Screen: weather_alert",
                    "State node: WxChannel",
                ),
                (
                    "Weather channel: WX 4: Synthetic Weather Channel 4",
                    "Frequency: 162.475000MHz",
                    "Modulation: FM",
                    "Weather mode: Weather Alert",
                    "Hold: ON",
                    "Signal: STRONG (4)",
                    "RSSI: -64",
                    "SAME selection: Front Range Counties",
                ),
            ),
        )

        for fixture_name, system_values, channel_values in cases:
            app = _fixture_app(fixture_name)
            async with app.run_test(size=(80, 36)):
                system_widget = app.query_one("#system", Static)
                channel_widget = app.query_one("#channel", Static)
                system = _plain(system_widget)
                channel = _plain(channel_widget)

                assert system_widget.border_title == "Screen Mode"
                assert channel_widget.border_title == "Weather"
                for value in system_values:
                    assert value in system
                for value in channel_values:
                    assert value in channel

    asyncio.run(exercise())


def test_tui_renders_mode_aware_tone_out_details() -> None:
    async def exercise() -> None:
        cases = (
            (
                "synthetic-tone-out.xml",
                (
                    "Mode: Tone-Out",
                    "V_Screen: tone_out",
                    "State node: ToneOutChannel",
                ),
                (
                    "Tone Out profile: FTO 3: Synthetic Tone Out 3",
                    "Frequency: 154.190000MHz",
                    "Modulation: NFM",
                    "Tone A: 600.9Hz",
                    "Tone B: 1006.9Hz",
                    "Hold: OFF",
                    "Signal: WEAK (1)",
                    "RSSI: -104",
                ),
            ),
            (
                "synthetic-tone-out-hold.xml",
                (
                    "Mode: Tone-Out",
                    "V_Screen: tone_out",
                    "State node: ToneOutChannel",
                ),
                (
                    "Tone Out profile: FTO 12: Synthetic Tone Out 12",
                    "Frequency: 153.830000MHz",
                    "Modulation: NFM",
                    "Tone A: 879.0Hz",
                    "Tone B: 0.0Hz",
                    "Hold: ON",
                    "Signal: STRONG (4)",
                    "RSSI: -73",
                ),
            ),
        )

        for fixture_name, system_values, channel_values in cases:
            app = _fixture_app(fixture_name)
            async with app.run_test(size=(80, 36)):
                system_widget = app.query_one("#system", Static)
                channel_widget = app.query_one("#channel", Static)
                system = _plain(system_widget)
                channel = _plain(channel_widget)

                assert system_widget.border_title == "Screen Mode"
                assert channel_widget.border_title == "Tone Out"
                for value in system_values:
                    assert value in system
                for value in channel_values:
                    assert value in channel

    asyncio.run(exercise())
