from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from threading import Event, RLock

from rich.text import Text
from textual.widgets import Static

from sds200.commands import NavigationTarget
from sds200.models import ScannerInfo
from sds200.state import RadioStateSnapshot
from sds200.transport import TransportDiagnostic
from sds200.tui import ScannerIdentity, ScannerTuiApp
from sds200.xml_protocol import ScannerInfoParser

XML = """<?xml version="1.0" encoding="utf-8"?>
<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">
<System Name="Example P25 System" Index="100" />
<Department Name="Example Department" Index="200" />
<Site Name="Example Simulcast" Index="300" Mod="NFM" />
<TGID Name="Example Dispatch" Index="400" TGID="TGID:65132" SvcType="Interop" />
<SiteFrequency Freq="769.431250MHz" />
<Property VOL="10" SQL="2" Sig="5" Rssi="-86" Rec="Off" Mute="Unmute" />
</ScannerInfo>"""

Unsubscribe = Callable[[], None]


class FakeControlRadio:
    def __init__(self, initial: ScannerInfo) -> None:
        self.connected = True
        self.initial = initial
        self.started = Event()
        self.calls: list[tuple[object, ...]] = []
        self.fail_hold = False
        self._lock = RLock()
        self._connection_callbacks: list[Callable[[bool], None]] = []

    def hold(
        self,
        target: NavigationTarget | str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        timeout: float = 2.0,
    ) -> None:
        del second, timeout
        if self.fail_hold:
            raise RuntimeError("hold rejected")
        self.calls.append(("hold", target, first))

    def next(
        self,
        target: NavigationTarget | str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        count: int = 1,
        timeout: float = 2.0,
    ) -> None:
        del second, timeout
        self.calls.append(("next", target, first, count))

    def previous(
        self,
        target: NavigationTarget | str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        count: int = 1,
        timeout: float = 2.0,
    ) -> None:
        del second, timeout
        self.calls.append(("previous", target, first, count))

    def set_volume(self, level: int, *, timeout: float = 2.0) -> None:
        del timeout
        self.calls.append(("volume", level))

    def set_squelch(self, level: int, *, timeout: float = 2.0) -> None:
        del timeout
        self.calls.append(("squelch", level))

    def on_state(
        self,
        callback: Callable[[RadioStateSnapshot], None],
    ) -> Unsubscribe:
        del callback
        return lambda: None

    def on_connection(self, callback: Callable[[bool], None]) -> Unsubscribe:
        with self._lock:
            self._connection_callbacks.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._connection_callbacks:
                    self._connection_callbacks.remove(callback)

        return unsubscribe

    def on_diagnostic(
        self,
        callback: Callable[[TransportDiagnostic], None],
    ) -> Unsubscribe:
        del callback
        return lambda: None

    @contextmanager
    def scanner_info_push(
        self,
        interval_ms: int = 500,
        *,
        timeout: float = 3.0,
    ) -> Iterator[ScannerInfo]:
        del interval_ms, timeout
        self.started.set()
        yield self.initial

    def emit_connection(self, connected: bool) -> None:
        self.connected = connected
        with self._lock:
            callbacks = tuple(self._connection_callbacks)
        for callback in callbacks:
            callback(connected)


def _app(radio: FakeControlRadio) -> ScannerTuiApp:
    return ScannerTuiApp(
        ScannerIdentity(
            endpoint="fake://scanner",
            model="SDS200",
            firmware="Version 1.26.01",
        ),
        radio.initial,
        radio=radio,
        interval_ms=250,
    )


def _plain(widget: Static) -> str:
    content = widget.content
    assert isinstance(content, Text)
    return content.plain


async def _wait_for_calls(
    radio: FakeControlRadio,
    count: int,
) -> None:
    for _ in range(100):
        if len(radio.calls) >= count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"Expected {count} control calls, received {radio.calls!r}")


def test_tui_controls_execute_in_order_and_update_status() -> None:
    async def exercise() -> None:
        radio = FakeControlRadio(ScannerInfoParser().parse("GSI", XML))
        app = _app(radio)

        async with app.run_test(size=(80, 38)) as pilot:
            assert await asyncio.to_thread(radio.started.wait, 1.0)
            await pilot.press("h", "n", "p", "plus", "right_square_bracket")
            await _wait_for_calls(radio, 5)
            await pilot.pause()

            assert radio.calls == [
                ("hold", "TGID", 400),
                ("next", "TGID", 400, 1),
                ("previous", "TGID", 400, 1),
                ("volume", 11),
                ("squelch", 3),
            ]
            status = _plain(app.query_one("#status", Static))
            assert "Volume: 11/29" in status
            assert "Squelch: 3/19" in status
            assert "Completed: Squelch 3" in status

        assert not app.control_thread_alive

    asyncio.run(exercise())


def test_tui_controls_report_disconnected_and_failed_commands() -> None:
    async def exercise() -> None:
        radio = FakeControlRadio(ScannerInfoParser().parse("GSI", XML))
        app = _app(radio)

        async with app.run_test(size=(80, 38)) as pilot:
            assert await asyncio.to_thread(radio.started.wait, 1.0)
            await asyncio.to_thread(radio.emit_connection, False)
            await pilot.pause()
            await pilot.press("h")
            await pilot.pause()
            assert radio.calls == []
            assert "Unavailable" in _plain(app.query_one("#status", Static))

            await asyncio.to_thread(radio.emit_connection, True)
            radio.fail_hold = True
            await pilot.pause()
            await pilot.press("h")
            for _ in range(100):
                await pilot.pause()
                status = _plain(app.query_one("#status", Static))
                if "Failed: Hold channel: hold rejected" in status:
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("Control failure was not displayed")

    asyncio.run(exercise())
