#!/usr/bin/env python3
"""Generate deterministic documentation screenshots of the web dashboard."""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from struct import unpack
from typing import Self
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import urlopen

import uvicorn
from fastapi.responses import HTMLResponse

from sds200.web_dashboard import create_web_dashboard_app

_THEME_STORAGE_KEY = "sdsctl.web.theme"
_THEMES = (
    "system",
    "lcars",
    "matrix",
    "first-responder",
    "amateur-radio",
)
_CONTROL_OPERATIONS = (
    "scanner.hold_state",
    "scanner.next",
    "scanner.previous",
    "scanner.reconnect",
)


@dataclass(frozen=True, slots=True)
class Capture:
    filename: str
    theme: str
    width: int
    height: int


CAPTURES = (
    Capture("theme-system-1920x1080.png", "system", 1920, 1080),
    Capture("theme-lcars-1920x1080.png", "lcars", 1920, 1080),
    Capture("theme-matrix-1920x1080.png", "matrix", 1920, 1080),
    Capture(
        "theme-first-responder-1920x1080.png",
        "first-responder",
        1920,
        1080,
    ),
    Capture(
        "theme-amateur-radio-1920x1080.png",
        "amateur-radio",
        1920,
        1080,
    ),
    Capture(
        "theme-amateur-radio-1366x768.png",
        "amateur-radio",
        1366,
        768,
    ),
)


DEMO_HELLO: Mapping[str, object] = {
    "protocol": "sdsctl.daemon",
    "selected_version": 1,
    "read_only": False,
    "control_operations": list(_CONTROL_OPERATIONS),
}


DEMO_RECORDING: Mapping[str, object] = {
    "status": "recording",
    "active": True,
    "closed": False,
    "elapsed_seconds": 192.4,
    "packets": 9632,
    "samples": 1541120,
    "audio_duration_seconds": 192.64,
    "recording": "demo/live.wav",
    "reliability": {
        "packets_lost": 2,
        "duplicate_packets": 1,
        "late_packets": 3,
        "malformed_packets": 0,
        "unexpected_source_packets": 0,
        "timestamp_discontinuities": 1,
    },
}


DEMO_RECORDINGS: Mapping[str, object] = {
    "limit": 50,
    "total_entries": 12,
    "summary": {"managed_units": 12},
    "issues": [],
    "entries": [
        {
            "audio": "2026/demo/Metro_Dispatch_1512.wav",
            "recorded_at": "2026-08-08T15:12:18-06:00",
            "duration_seconds": 148.7,
            "audio_size_bytes": 1190044,
            "playable": True,
        },
        {
            "audio": "2026/demo/County_Fireground_1507.wav",
            "recorded_at": "2026-08-08T15:07:42-06:00",
            "duration_seconds": 83.2,
            "audio_size_bytes": 666040,
            "playable": True,
        },
        {
            "audio": "2026/demo/Airport_Ops_1458.wav",
            "recorded_at": "2026-08-08T14:58:09-06:00",
            "duration_seconds": 61.5,
            "audio_size_bytes": 492036,
            "playable": True,
        },
    ],
}


DEMO_SNAPSHOT: Mapping[str, object] = {
    "state": "running",
    "scanner_connected": True,
    "scanner_model": "SDS200",
    "scanner_firmware": "1.26.01",
    "scanner_endpoint": "udp://192.0.2.25:50536",
    "psi_active": True,
    "psi_interval_ms": 500,
    "transition_sequence": 1842,
    "radio_state": {
        "system": "Demo Metro Public Safety",
        "department": "Central Dispatch",
        "site": "Metro Simulcast",
        "channel": "Dispatch 1 (Demo)",
        "mode": "P25",
        "screen_kind": "TGID",
        "screen": "TGID",
        "signal": 5,
        "rssi": -67,
        "system_index": 120,
        "department_index": 240,
        "site_index": 12,
        "channel_index": 400,
        "channel_kind": "TGID",
        "system_hold": "Off",
        "department_hold": "Off",
        "site_hold": "Off",
        "channel_hold": "On",
    },
    "audio": {
        "running": True,
        "state": "streaming",
    },
    "router": {
        "running": True,
    },
    "recording": dict(DEMO_RECORDING),
}


class DemoDaemonApiClient:
    """Immutable fake daemon client used only by the screenshot helper."""

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception_type, exception, traceback

    def hello(self) -> dict[str, object]:
        return dict(DEMO_HELLO)

    def runtime_snapshot(self) -> dict[str, object]:
        return dict(DEMO_SNAPSHOT)

    def recording_status(self) -> dict[str, object]:
        return dict(DEMO_RECORDING)

    def recording_start(self) -> dict[str, object]:
        return dict(DEMO_RECORDING)

    def recording_stop(self) -> dict[str, object]:
        stopped = dict(DEMO_RECORDING)
        stopped.update({"status": "stopped", "active": False})
        return stopped

    def recordings_list(self) -> dict[str, object]:
        return dict(DEMO_RECORDINGS)

    def hold_state(
        self,
        scope: str,
        held: bool,
        *,
        timeout: float = 4.0,
    ) -> dict[str, object]:
        del scope, held, timeout
        return _control_result("scanner.hold_state")

    def next(
        self,
        target: str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        count: int = 1,
        timeout: float = 2.0,
    ) -> dict[str, object]:
        del target, first, second, count, timeout
        return _control_result("scanner.next")

    def previous(
        self,
        target: str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        count: int = 1,
        timeout: float = 2.0,
    ) -> dict[str, object]:
        del target, first, second, count, timeout
        return _control_result("scanner.previous")

    def reconnect(self, *, timeout: float = 2.0) -> dict[str, object]:
        del timeout
        return _control_result("scanner.reconnect")


def _control_result(operation: str) -> dict[str, object]:
    return {
        "sequence": 1,
        "operation": operation,
        "started_at": "2026-08-08T21:12:00+00:00",
        "completed_at": "2026-08-08T21:12:00+00:00",
        "snapshot": dict(DEMO_SNAPSHOT),
    }


def _demo_theme_response(theme: str) -> HTMLResponse:
    """Return a fixed screenshot-setup page for one repository-defined theme."""

    content = (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '  <meta charset="utf-8">\n'
        "  <title>sdsctl web screenshot setup</title>\n"
        "</head>\n"
        "<body>\n"
        "<script>\n"
        f"localStorage.setItem({_THEME_STORAGE_KEY!r}, {theme!r});\n"
        'location.replace("/");\n'
        "</script>\n"
        "</body>\n"
        "</html>\n"
    )
    return HTMLResponse(
        content=content,
        headers={"Cache-Control": "no-store"},
    )


def create_demo_app():
    """Return the production dashboard wired to deterministic fake data."""

    app = create_web_dashboard_app(DemoDaemonApiClient)

    @app.get(
        "/__demo/theme/system",
        include_in_schema=False,
        response_class=HTMLResponse,
    )
    def select_demo_system_theme() -> HTMLResponse:
        return _demo_theme_response("system")

    @app.get(
        "/__demo/theme/lcars",
        include_in_schema=False,
        response_class=HTMLResponse,
    )
    def select_demo_lcars_theme() -> HTMLResponse:
        return _demo_theme_response("lcars")

    @app.get(
        "/__demo/theme/matrix",
        include_in_schema=False,
        response_class=HTMLResponse,
    )
    def select_demo_matrix_theme() -> HTMLResponse:
        return _demo_theme_response("matrix")

    @app.get(
        "/__demo/theme/first-responder",
        include_in_schema=False,
        response_class=HTMLResponse,
    )
    def select_demo_first_responder_theme() -> HTMLResponse:
        return _demo_theme_response("first-responder")

    @app.get(
        "/__demo/theme/amateur-radio",
        include_in_schema=False,
        response_class=HTMLResponse,
    )
    def select_demo_amateur_radio_theme() -> HTMLResponse:
        return _demo_theme_response("amateur-radio")

    return app


def _find_chrome(explicit: str | None) -> str:
    candidates = (
        explicit,
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
    )
    for candidate in candidates:
        if candidate is None:
            continue
        resolved = shutil.which(candidate)
        if resolved is not None:
            return resolved
        path = Path(candidate)
        if path.is_file():
            return str(path.resolve())
    raise SystemExit(
        "Chrome/Chromium was not found. Use --chrome to provide an executable."
    )


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_server(base_url: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{base_url}/healthz", timeout=0.5) as response:
                if response.status == 200:
                    return
        except (OSError, URLError) as error:
            last_error = error
        time.sleep(0.1)
    raise RuntimeError(
        f"demo server did not become ready at {base_url}: {last_error}"
    )


def _capture(
    *,
    chrome: str,
    profile_dir: Path,
    base_url: str,
    capture: Capture,
    output_dir: Path,
    virtual_time_ms: int,
    capture_timeout_seconds: float,
) -> Path:
    destination = (output_dir / capture.filename).resolve()
    destination.unlink(missing_ok=True)

    url = f"{base_url}/__demo/theme/{quote(capture.theme, safe='')}"
    command = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--no-first-run",
        "--no-default-browser-check",
        "--hide-scrollbars",
        "--force-device-scale-factor=1",
        "--run-all-compositor-stages-before-draw",
        f"--virtual-time-budget={virtual_time_ms}",
        f"--window-size={capture.width},{capture.height}",
        f"--user-data-dir={profile_dir}",
        f"--screenshot={destination}",
        url,
    ]

    try:
        completed = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=capture_timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        if _valid_screenshot(destination, capture):
            print(
                "WARNING: Chrome exceeded the capture timeout after writing "
                f"a valid screenshot: {capture.filename}"
            )
            return destination
        output = error.stdout or ""
        raise RuntimeError(
            "Chrome screenshot capture timed out before producing a valid "
            f"image for {capture.filename}:\n{output}"
        ) from error

    if completed.returncode != 0:
        raise RuntimeError(
            "Chrome screenshot capture failed "
            f"for {capture.filename}:\n{completed.stdout}"
        )
    if not _valid_screenshot(destination, capture):
        raise RuntimeError(
            f"Chrome did not create a valid screenshot: {destination}"
        )

    return destination


def _valid_screenshot(path: Path, capture: Capture) -> bool:
    if not path.is_file() or path.stat().st_size < 24:
        return False

    header = path.read_bytes()[:24]
    if header[:8] != b"\x89PNG\r\n\x1a\n":
        return False

    width, height = unpack(">II", header[16:24])
    return (width, height) == (capture.width, capture.height)


def _selected_captures(names: Sequence[str]) -> tuple[Capture, ...]:
    if not names:
        return CAPTURES

    requested = set(names)
    available = {capture.filename for capture in CAPTURES}
    unknown = sorted(requested - available)
    if unknown:
        joined = ", ".join(unknown)
        raise SystemExit(f"unknown capture filename(s): {joined}")

    return tuple(
        capture for capture in CAPTURES if capture.filename in requested
    )


def _serve(port: int) -> None:
    uvicorn.run(
        create_demo_app(),
        host="127.0.0.1",
        port=port,
        access_log=False,
        log_level="warning",
    )


def _generate(args: argparse.Namespace) -> None:
    chrome = _find_chrome(args.chrome)
    captures = _selected_captures(args.only)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    port = args.port or _available_port()
    base_url = f"http://127.0.0.1:{port}"

    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    server = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--serve",
            "--port",
            str(port),
        ],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        _wait_for_server(base_url)
        with tempfile.TemporaryDirectory(
            prefix="sds200-web-screenshots-"
        ) as profile:
            profile_root = Path(profile)
            print(f"Chrome: {chrome}")
            print(f"Demo server: {base_url}")
            print(f"Output: {output_dir}")

            for index, capture in enumerate(captures, start=1):
                profile_dir = profile_root / f"capture-{index:02d}"
                profile_dir.mkdir()
                destination = _capture(
                    chrome=chrome,
                    profile_dir=profile_dir,
                    base_url=base_url,
                    capture=capture,
                    output_dir=output_dir,
                    virtual_time_ms=args.virtual_time_ms,
                    capture_timeout_seconds=args.capture_timeout_seconds,
                )
                size = destination.stat().st_size
                print(
                    f"Captured {capture.theme:16} "
                    f"{capture.width}x{capture.height}: "
                    f"{destination} ({size} bytes)"
                )
    finally:
        server.terminate()
        try:
            server.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5.0)

        if server.stdout is not None:
            server_output = server.stdout.read()
            server.stdout.close()
            if server.returncode not in (0, -15) and server_output:
                print(server_output, file=sys.stderr)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate deterministic Chrome screenshots of the real packaged "
            "sdsctl web dashboard using fictional demo scanner data."
        )
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="local demo-server port; generation chooses a free port by default",
    )
    parser.add_argument(
        "--chrome",
        help="Chrome/Chromium executable or command name",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("docs/assets/web-dashboard"),
        help="screenshot output directory",
    )
    parser.add_argument(
        "--virtual-time-ms",
        type=int,
        default=2500,
        help="virtual milliseconds to allow the dashboard to settle",
    )
    parser.add_argument(
        "--capture-timeout-seconds",
        type=float,
        default=20.0,
        help="maximum wall-clock seconds allowed for each Chrome capture",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="FILENAME",
        help="capture only one named output; may be repeated",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="list deterministic captures without running Chrome",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()

    if args.list:
        for capture in CAPTURES:
            print(
                f"{capture.filename}: "
                f"theme={capture.theme} "
                f"viewport={capture.width}x{capture.height}"
            )
        return

    if args.serve:
        if args.port <= 0:
            raise SystemExit("--serve requires a positive --port")
        _serve(args.port)
        return

    if args.virtual_time_ms < 0:
        raise SystemExit("--virtual-time-ms must be non-negative")
    if args.capture_timeout_seconds <= 0:
        raise SystemExit("--capture-timeout-seconds must be positive")

    _generate(args)


if __name__ == "__main__":
    main()
