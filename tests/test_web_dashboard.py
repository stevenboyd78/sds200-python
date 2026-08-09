from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Self

import pytest
from fastapi.testclient import TestClient

import sds200.web_dashboard as web_dashboard
from sds200 import __version__
from sds200.daemon_events import DaemonEvent, DaemonEventKind
from sds200.daemon_recording_file_client import DaemonRecordingFileRequestError
from sds200.daemon_recording_file_protocol import RecordingFileResponseStatus
from sds200.exceptions import (
    DaemonDisconnectedError,
    DaemonRequestError,
    DaemonUnavailableError,
)
from sds200.pcmu import PcmuPacket
from sds200.pcmu_protocol import encode_pcmu_delivery
from sds200.pcmu_subscriptions import PcmuPacketDelivery, PcmuPublication
from sds200.web_dashboard import (
    WEB_DASHBOARD_API_PROTOCOL,
    WEB_DASHBOARD_API_VERSION,
    WEB_DASHBOARD_UNAVAILABLE_DETAIL,
    create_web_dashboard_app,
)


class FakeDaemonApiClient:
    def __init__(
        self,
        *,
        hello: Mapping[str, object] | None = None,
        snapshot: Mapping[str, object] | None = None,
        error: BaseException | None = None,
        recording_error: BaseException | None = None,
        control_error: BaseException | None = None,
    ) -> None:
        self.hello_result = dict(hello or {})
        self.snapshot_result = dict(snapshot or {})
        self.error = error
        self.recording_error = recording_error
        self.control_error = control_error
        self.entered = False
        self.closed = False
        self.hello_calls = 0
        self.snapshot_calls = 0
        self.recording_status_calls = 0
        self.recording_start_calls = 0
        self.recording_stop_calls = 0
        self.recordings_list_calls = 0
        self.control_calls: list[tuple[object, ...]] = []

    def __enter__(self) -> Self:
        self.entered = True
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception_type, exception, traceback
        self.closed = True

    def hello(self) -> dict[str, object]:
        self.hello_calls += 1
        if self.error is not None:
            raise self.error
        return dict(self.hello_result)

    def runtime_snapshot(self) -> dict[str, object]:
        self.snapshot_calls += 1
        if self.error is not None:
            raise self.error
        return dict(self.snapshot_result)

    def recording_status(self) -> dict[str, object]:
        self.recording_status_calls += 1
        self._raise_recording_error()
        return {"status": "idle", "active": False}

    def recording_start(self) -> dict[str, object]:
        self.recording_start_calls += 1
        self._raise_recording_error()
        return {"status": "recording", "active": True}

    def recording_stop(self) -> dict[str, object]:
        self.recording_stop_calls += 1
        self._raise_recording_error()
        return {"status": "stopped", "active": False}

    def recordings_list(self) -> dict[str, object]:
        self.recordings_list_calls += 1
        self._raise_recording_error()
        return {
            "limit": 50,
            "total_entries": 1,
            "summary": {"managed_units": 1},
            "issues": [],
            "entries": [{"audio": "2026/test.wav"}],
        }

    def hold_state(
        self,
        scope: str,
        held: bool,
        *,
        timeout: float = 4.0,
    ) -> dict[str, object]:
        self.control_calls.append(("hold_state", scope, held, timeout))
        self._raise_control_error()
        return self._control_result("scanner.hold_state")

    def next(
        self,
        target: str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        count: int = 1,
        timeout: float = 2.0,
    ) -> dict[str, object]:
        self.control_calls.append(
            ("next", target, first, second, count, timeout)
        )
        self._raise_control_error()
        return self._control_result("scanner.next")

    def previous(
        self,
        target: str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        count: int = 1,
        timeout: float = 2.0,
    ) -> dict[str, object]:
        self.control_calls.append(
            ("previous", target, first, second, count, timeout)
        )
        self._raise_control_error()
        return self._control_result("scanner.previous")

    def reconnect(self, *, timeout: float = 2.0) -> dict[str, object]:
        self.control_calls.append(("reconnect", timeout))
        self._raise_control_error()
        return self._control_result("scanner.reconnect")

    def _control_result(self, operation: str) -> dict[str, object]:
        return {
            "sequence": len(self.control_calls),
            "operation": operation,
            "started_at": "2026-08-08T00:00:00+00:00",
            "completed_at": "2026-08-08T00:00:00+00:00",
            "snapshot": dict(self.snapshot_result),
        }

    def _raise_control_error(self) -> None:
        if self.control_error is not None:
            raise self.control_error

    def _raise_recording_error(self) -> None:
        if self.recording_error is not None:
            raise self.recording_error


class FakeDaemonRecordingFileDownload:
    def __init__(self, payload: bytes) -> None:
        self.content_length = len(payload)
        self._payload = payload
        self._offset = 0
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        if self.closed:
            raise ValueError("closed")
        if size < 0:
            size = len(self._payload) - self._offset
        end = min(len(self._payload), self._offset + size)
        payload = self._payload[self._offset:end]
        self._offset = end
        if self._offset == len(self._payload):
            self.closed = True
        return payload

    def close(self) -> None:
        self.closed = True


class FakeDaemonRecordingFileClient:
    def __init__(
        self,
        *,
        payload: bytes = b"RIFFtest",
        error: BaseException | None = None,
    ) -> None:
        self.payload = payload
        self.error = error
        self.identifiers: list[str] = []
        self.downloads: list[FakeDaemonRecordingFileDownload] = []

    def open(self, identifier: str) -> FakeDaemonRecordingFileDownload:
        self.identifiers.append(identifier)
        if self.error is not None:
            raise self.error
        download = FakeDaemonRecordingFileDownload(self.payload)
        self.downloads.append(download)
        return download


class FakeDaemonEventClient:
    def __init__(
        self,
        *,
        events: list[DaemonEvent] | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.events = list(events or [])
        self.error = error
        self.receive_calls = 0
        self.closed = False

    def receive(self) -> DaemonEvent:
        self.receive_calls += 1
        if self.error is not None:
            raise self.error
        if self.events:
            return self.events.pop(0)
        raise DaemonDisconnectedError("test event stream completed")

    def close(self) -> None:
        self.closed = True


class BlockingDaemonEventClient:
    def __init__(self) -> None:
        self.receive_started = threading.Event()
        self.release_receive = threading.Event()
        self.closed = False

    def receive(self) -> DaemonEvent:
        self.receive_started.set()
        self.release_receive.wait(timeout=5.0)
        raise DaemonDisconnectedError("test event stream cancelled")

    def close(self) -> None:
        self.closed = True
        self.release_receive.set()


class FakeDaemonPcmuClient:
    def __init__(
        self,
        *,
        deliveries: list[PcmuPacketDelivery] | None = None,
        connect_error: BaseException | None = None,
    ) -> None:
        self.deliveries = list(deliveries or [])
        self.connect_error = connect_error
        self.max_endpoint_bytes = 4096
        self.max_frame_bytes = 128 * 1024
        self.connect_calls = 0
        self.receive_calls = 0
        self.closed = False

    def connect(self) -> object:
        self.connect_calls += 1
        if self.connect_error is not None:
            raise self.connect_error
        return self

    def receive(self) -> PcmuPacketDelivery:
        self.receive_calls += 1
        if self.deliveries:
            return self.deliveries.pop(0)
        raise DaemonDisconnectedError("test PCMU stream completed")

    def close(self) -> None:
        self.closed = True


class BlockingDaemonPcmuClient:
    def __init__(self) -> None:
        self.max_endpoint_bytes = 4096
        self.max_frame_bytes = 128 * 1024
        self.receive_started = threading.Event()
        self.release_receive = threading.Event()
        self.closed = False

    def connect(self) -> object:
        return self

    def receive(self) -> PcmuPacketDelivery:
        self.receive_started.set()
        self.release_receive.wait(timeout=5.0)
        raise DaemonDisconnectedError("test PCMU stream cancelled")

    def close(self) -> None:
        self.closed = True
        self.release_receive.set()


def pcmu_delivery(
    stream_sequence: int,
    payload: bytes,
    *,
    packets_dropped: int = 0,
    payload_bytes_dropped: int = 0,
    overflows: int = 0,
) -> PcmuPacketDelivery:
    return PcmuPacketDelivery(
        publication=PcmuPublication(
            stream_sequence=stream_sequence,
            packet=PcmuPacket(
                endpoint="rtsp://192.0.2.25/au:scanner.au",
                sequence=stream_sequence,
                timestamp=stream_sequence * 160,
                ssrc=7,
                payload=payload,
            ),
        ),
        packets_dropped=packets_dropped,
        payload_bytes_dropped=payload_bytes_dropped,
        overflows=overflows,
    )


def test_web_dashboard_requires_callable_client_factory() -> None:
    with pytest.raises(
        TypeError,
        match="Daemon API client factory must be callable",
    ):
        create_web_dashboard_app(None)  # type: ignore[arg-type]


def test_web_dashboard_requires_callable_event_client_factory() -> None:
    with pytest.raises(
        TypeError,
        match="Daemon event client factory must be callable or None",
    ):
        create_web_dashboard_app(
            FakeDaemonApiClient,
            object(),  # type: ignore[arg-type]
        )


def test_web_dashboard_requires_callable_pcmu_client_factory() -> None:
    with pytest.raises(
        TypeError,
        match="Daemon PCMU client factory must be callable or None",
    ):
        create_web_dashboard_app(
            FakeDaemonApiClient,
            FakeDaemonEventClient,
            object(),  # type: ignore[arg-type]
        )


def test_web_dashboard_requires_callable_recording_file_client_factory() -> None:
    with pytest.raises(
        TypeError,
        match="Daemon recording-file client factory must be callable or None",
    ):
        create_web_dashboard_app(
            FakeDaemonApiClient,
            FakeDaemonEventClient,
            FakeDaemonPcmuClient,
            object(),  # type: ignore[arg-type]
        )


def test_web_dashboard_rejects_non_boolean_home_assistant_ingress() -> None:
    with pytest.raises(
        TypeError,
        match="Home Assistant Ingress setting must be boolean",
    ):
        create_web_dashboard_app(
            FakeDaemonApiClient,
            home_assistant_ingress=1,  # type: ignore[arg-type]
        )


def test_web_dashboard_home_assistant_ingress_rejects_other_clients() -> None:
    def forbidden_factory() -> FakeDaemonApiClient:
        raise AssertionError("rejected ingress request must not reach daemon")

    app = create_web_dashboard_app(
        forbidden_factory,
        home_assistant_ingress=True,
    )

    with TestClient(
        app,
        client=("172.30.32.3", 50000),
    ) as client:
        response = client.get("/api/v1/status")

    assert response.status_code == 403
    assert response.json() == {
        "detail": (
            web_dashboard.WEB_DASHBOARD_HOME_ASSISTANT_INGRESS_FORBIDDEN_DETAIL
        )
    }
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_web_dashboard_home_assistant_ingress_allows_supervisor_client() -> None:
    def forbidden_factory() -> FakeDaemonApiClient:
        raise AssertionError("dashboard shell must not connect to daemon")

    app = create_web_dashboard_app(
        forbidden_factory,
        home_assistant_ingress=True,
    )

    with TestClient(
        app,
        client=(
            web_dashboard.WEB_DASHBOARD_HOME_ASSISTANT_INGRESS_CLIENT,
            50000,
        ),
    ) as client:
        shell = client.get("/")
        stylesheet = client.get("/assets/dashboard.css")
        docs = client.get("/api/v1/docs")

    for response in (shell, stylesheet, docs):
        assert response.status_code == 200
        assert "x-frame-options" not in response.headers
        content_security_policy = response.headers[
            "content-security-policy"
        ]
        assert "frame-ancestors 'self'" in content_security_policy
        assert "frame-ancestors 'none'" not in content_security_policy


def test_web_dashboard_shell_does_not_connect_to_daemon() -> None:
    def forbidden_factory() -> FakeDaemonApiClient:
        raise AssertionError("dashboard shell must not connect to the daemon")

    app = create_web_dashboard_app(forbidden_factory)

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert "'unsafe-inline'" not in response.headers["content-security-policy"]
    assert "<title>sdsctl scanner dashboard</title>" in response.text
    assert 'id="main-content"' in response.text
    assert 'id="status-badge"' in response.text
    assert 'id="audio-play"' in response.text
    assert 'id="audio-stop"' in response.text
    assert 'id="recording-start"' in response.text
    assert 'id="recording-stop"' in response.text
    assert 'id="recordings-list"' in response.text
    assert 'id="saved-recording-player"' in response.text
    assert 'id="scanner-control-status"' in response.text
    assert "<h3>Hold / release</h3>" in response.text
    assert 'id="scanner-hold-channel"' in response.text
    assert 'id="scanner-hold-system-state"' in response.text
    assert 'id="scanner-hold-department-state"' in response.text
    assert 'id="scanner-hold-site-state"' in response.text
    assert 'id="scanner-hold-channel-state"' in response.text
    assert 'aria-describedby="scanner-hold-channel-state"' in response.text
    assert response.text.count('aria-pressed="false"') == 4
    assert 'id="scanner-previous"' in response.text
    assert 'id="scanner-next"' in response.text
    assert 'id="scanner-reconnect"' in response.text
    assert "media-src 'self'" in response.headers["content-security-policy"]
    assert "Milestone 20.2" not in response.text
    assert 'href="assets/favicon.svg"' in response.text
    assert 'type="image/svg+xml"' in response.text
    assert 'id="theme-select"' in response.text
    assert '<option value="system">System</option>' in response.text
    assert '<option value="lcars">LCARS-inspired</option>' in response.text
    assert '<option value="matrix">Matrix-inspired</option>' in response.text
    assert '<option value="first-responder">First Responder</option>' in response.text
    assert '<option value="amateur-radio">Amateur Radio</option>' in response.text
    assert 'src="assets/theme-bootstrap.js"' in response.text
    assert 'href="assets/dashboard.css"' in response.text
    assert response.text.index('src="assets/theme-bootstrap.js"') < response.text.index(
        'href="assets/dashboard.css"'
    )
    assert 'src="assets/dashboard.js"' in response.text
    assert "<style" not in response.text
    assert "<script>" not in response.text


def test_web_dashboard_serves_packaged_static_assets() -> None:
    def forbidden_factory() -> FakeDaemonApiClient:
        raise AssertionError("static assets must not connect to the daemon")

    app = create_web_dashboard_app(forbidden_factory)

    with TestClient(app) as client:
        shell = client.get("/")
        stylesheet = client.get("/assets/dashboard.css")
        theme_bootstrap = client.get("/assets/theme-bootstrap.js")
        script = client.get("/assets/dashboard.js")
        audio_worklet = client.get("/assets/audio-worklet.js")
        favicon = client.get("/assets/favicon.svg")

    assert shell.status_code == 200
    assert stylesheet.status_code == 200
    assert stylesheet.headers["content-type"].startswith("text/css")
    assert stylesheet.headers["cache-control"] == "no-store"
    assert "--content-width:" in stylesheet.text
    assert "@media (prefers-reduced-motion: reduce)" in stylesheet.text
    assert ".recording-panel" in stylesheet.text
    assert ".scanner-controls" in stylesheet.text
    assert ".scanner-control-status" in stylesheet.text
    assert ".scanner-hold-control" in stylesheet.text
    assert ".scanner-hold-state" in stylesheet.text
    assert ".scanner-hold-state[hidden]" in stylesheet.text
    assert ".scanner-hold-active" not in stylesheet.text
    assert "#scanner-previous," not in stylesheet.text
    assert "#scanner-reconnect," in stylesheet.text
    assert ".recording-list" in stylesheet.text
    assert ".saved-playback" in stylesheet.text
    assert ':root[data-theme="lcars"]' in stylesheet.text
    assert ':root[data-theme="matrix"]' in stylesheet.text
    assert ':root[data-theme="first-responder"]' in stylesheet.text
    assert ':root[data-theme="amateur-radio"]' in stylesheet.text
    assert ".theme-picker" in stylesheet.text
    assert "@keyframes matrix-grid-drift" in stylesheet.text
    assert "animation: matrix-grid-drift 18s linear infinite" in stylesheet.text
    assert ':root[data-theme="lcars"] .site-header::before' in stylesheet.text
    assert ':root[data-theme="matrix"] .panel::before' in stylesheet.text
    assert ':root[data-theme="first-responder"] .panel::after' in stylesheet.text
    assert ':root[data-theme="amateur-radio"] .site-header::after' in stylesheet.text
    assert "animation-duration: 0.01ms !important" in stylesheet.text
    assert "animation-iteration-count: 1 !important" in stylesheet.text
    assert "--content-width: 220rem" in stylesheet.text
    assert "@media (min-width: 100rem)" in stylesheet.text
    assert "@media (max-width: 56rem)" in stylesheet.text
    assert "grid-template-columns: repeat(4, minmax(0, 1fr))" in stylesheet.text
    assert ':root[data-theme="matrix"] .panel-header::before' in stylesheet.text
    assert ':root[data-theme="first-responder"] .status-badge' in stylesheet.text
    assert ':root[data-theme="amateur-radio"] #radio-system' in stylesheet.text
    assert (
        ':root[data-theme="amateur-radio"] .dashboard-grid '
        '> .scanner-controls-panel::before'
        in stylesheet.text
    )
    assert "@media (min-width: 64rem) and (min-height: 36rem)" in stylesheet.text
    assert "height: 100dvh" in stylesheet.text
    assert "grid-template-rows: repeat(2, minmax(0, 1fr))" in stylesheet.text
    assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in stylesheet.text
    assert "minmax(0, 0.9fr)" in stylesheet.text
    assert "minmax(0, 1.08fr)" in stylesheet.text
    assert "minmax(0, 1.02fr)" in stylesheet.text
    assert "grid-template-columns: repeat(3, minmax(0, 1fr));" in stylesheet.text
    assert "overflow-y: auto" in stylesheet.text
    assert "grid-template-rows: auto auto auto minmax(0, 1fr)" in stylesheet.text
    assert "--lcars-panel:" in stylesheet.text
    assert ':root[data-theme="lcars"] .dashboard-grid::before' in stylesheet.text
    assert ':root[data-theme="lcars"] .dashboard-grid::after' in stylesheet.text
    assert "var(--lcars-panel-soft)" in stylesheet.text
    assert (
        ':root[data-theme="lcars"] .dashboard-grid::before,'
        in stylesheet.text
    )
    assert "content: none;" in stylesheet.text
    assert "overflow-x: hidden;" in stylesheet.text
    assert "width: min(27%, 8.5rem);" in stylesheet.text
    assert "--term-accent:" in stylesheet.text
    assert ':root[data-theme="matrix"] .dashboard-grid > .panel::after' in stylesheet.text
    assert "color-mix(in srgb, var(--term-accent)" in stylesheet.text
    assert ':root[data-theme="matrix"] .scanner-control-group::after' in stylesheet.text
    assert "--dispatch-accent:" in stylesheet.text
    assert (
        ':root[data-theme="first-responder"] .dashboard-grid > .panel::before'
        in stylesheet.text
    )
    assert (
        ':root[data-theme="first-responder"] .scanner-control-group::before'
        in stylesheet.text
    )
    assert "--radio-accent:" in stylesheet.text
    assert (
        ':root[data-theme="amateur-radio"] .panel-emphasis .primary-value'
        in stylesheet.text
    )
    assert (
        ':root[data-theme="amateur-radio"] .dashboard-grid '
        '> .scanner-controls-panel::before'
        in stylesheet.text
    )
    assert "conic-gradient(" in stylesheet.text
    assert (
        "@media (min-width: 64rem) and (max-width: 90rem) "
        "and (min-height: 36rem)"
        in stylesheet.text
    )
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in stylesheet.text
    assert ':root[data-theme="amateur-radio"] .recording-telemetry dd' in stylesheet.text

    assert "theme-stage" in shell.text
    assert 'aria-hidden="true"' in shell.text
    assert "theme-stage-h" in shell.text
    assert ".theme-stage {" in stylesheet.text
    assert "@keyframes matrix-stage-fall" in stylesheet.text
    assert "@keyframes dispatch-radar-sweep" in stylesheet.text
    assert "@keyframes radio-display-scan" in stylesheet.text
    assert ':root[data-theme="lcars"] .theme-stage-a' in stylesheet.text
    assert ':root[data-theme="matrix"] .theme-stage-g' in stylesheet.text
    assert (
        ':root[data-theme="first-responder"] .theme-stage-c'
        in stylesheet.text
    )
    assert (
        ':root[data-theme="amateur-radio"] .theme-stage-e'
        in stylesheet.text
    )
    assert "contain: paint;" in stylesheet.text
    assert ".site-header {\n  position: relative;\n  z-index: 3;\n}" in stylesheet.text
    assert (
        ".dashboard,\n.site-footer {\n  position: relative;\n  z-index: 1;\n}"
        in stylesheet.text
    )
    assert ".skip-link {\n  z-index: 10;\n}" in stylesheet.text
    assert (
        ".site-footer,\n.skip-link {\n  position: relative;"
        not in stylesheet.text
    )

    assert "@keyframes cinematic-bloom-breathe" in stylesheet.text
    assert "@keyframes cinematic-scan-pass" in stylesheet.text
    assert "@keyframes cinematic-alert-wash" in stylesheet.text
    assert "@keyframes cinematic-meter-scan" in stylesheet.text
    assert ':root[data-theme="lcars"] .theme-stage-g::after' in stylesheet.text
    assert ':root[data-theme="matrix"] .theme-stage-a::before' in stylesheet.text
    assert (
        ':root[data-theme="first-responder"] .theme-stage-a::after'
        in stylesheet.text
    )
    assert (
        ':root[data-theme="amateur-radio"] .panel-emphasis::after'
        in stylesheet.text
    )
    assert "perspective: 44rem;" in stylesheet.text
    assert "backdrop-filter: blur(0.55px);" in stylesheet.text

    assert theme_bootstrap.status_code == 200
    assert theme_bootstrap.headers["content-type"].startswith(
        "application/javascript"
    )
    assert theme_bootstrap.headers["cache-control"] == "no-store"
    assert '"sdsctl.web.theme"' in theme_bootstrap.text
    assert '"system"' in theme_bootstrap.text
    assert '"lcars"' in theme_bootstrap.text
    assert '"matrix"' in theme_bootstrap.text
    assert '"first-responder"' in theme_bootstrap.text
    assert '"amateur-radio"' in theme_bootstrap.text
    assert "localStorage.getItem" in theme_bootstrap.text
    assert "localStorage.setItem" in theme_bootstrap.text
    assert "document.documentElement.dataset.theme" in theme_bootstrap.text
    assert "innerHTML" not in theme_bootstrap.text

    assert script.status_code == 200
    assert script.headers["content-type"].startswith("application/javascript")
    assert script.headers["cache-control"] == "no-store"
    assert 'fetch(webUrl("api/v1/status")' in script.text
    assert 'new EventSource(webUrl("api/v1/events"))' in script.text
    assert "FALLBACK_REFRESH_INTERVAL_MS" in script.text
    assert "RECONCILE_INTERVAL_MS" in script.text
    assert 'fetch(webUrl("api/v1/audio")' in script.text
    assert 'audioWorklet.addModule(webUrl("assets/audio-worklet.js"))' in script.text
    assert "new AudioWorkletNode" in script.text
    assert "new AbortController" in script.text
    assert "getBigUint64" in script.text
    assert "PCMU stream gap does not match daemon queue-loss counters" in script.text
    assert 'fetch(webUrl("api/v1/recording")' in script.text
    assert 'fetch(webUrl("api/v1/recordings")' in script.text
    assert 'performRecordingAction("start")' in script.text
    assert 'performRecordingAction("stop")' in script.text
    assert 'performScannerHoldState("channel")' in script.text
    assert 'performScannerControl("reconnect", "Reconnect scanner")' in script.text
    assert 'daemonControlSupported("scanner.hold_state")' in script.text
    assert "setScannerHoldControl" in script.text
    assert 'radio.channel_hold === "On"' in script.text
    assert "button.disabled = !available" in script.text
    assert 'button.setAttribute("aria-pressed"' in script.text
    assert "JSON.stringify(body)" in script.text
    assert "indicator.hidden = !held" in script.text
    assert '"scanner-hold-active"' not in script.text
    assert "scannerControlMutationInProgress" in script.text
    assert "initializeThemeControl" in script.text
    assert 'element("theme-select")' in script.text
    assert "controller.select(select.value)" in script.text
    assert "control.snapshot" in script.text
    assert "daemonEventGeneration" in script.text
    assert "eventGenerationAtStart" in script.text
    assert "reconcileStatusAfterControl" in script.text
    assert "fetchStatusPayload" in script.text
    assert "The scanner control has already completed successfully." in script.text
    assert "daemonEventGeneration === eventGenerationAtStart" in script.text
    assert "value < 0xffffffff" in script.text
    assert 'kind === "recording.state"' in script.text
    assert "recordingStatusAvailable" in script.text
    assert "recording: payload" in script.text
    assert '["idle", "stopped", "failed"].includes(status)' in script.text
    assert "RECORDING_REFRESH_INTERVAL_MS" in script.text
    assert "recordingFileUrl" in script.text
    assert "encodeURIComponent" in script.text
    assert "document.createElement" in script.text
    assert "saved-recording-player" in script.text
    assert "textContent" in script.text
    assert "innerHTML" not in script.text

    assert audio_worklet.status_code == 200
    assert audio_worklet.headers["content-type"].startswith(
        "application/javascript"
    )
    assert audio_worklet.headers["cache-control"] == "no-store"
    assert 'registerProcessor("sds200-pcmu"' in audio_worklet.text
    assert "decodeMulaw" in audio_worklet.text
    assert "AudioWorkletProcessor" in audio_worklet.text
    assert "innerHTML" not in audio_worklet.text

    assert favicon.status_code == 200
    assert favicon.headers["content-type"].startswith("image/svg+xml")
    assert favicon.headers["cache-control"] == "no-store"
    assert "<svg" in favicon.text
    assert 'aria-hidden="true"' in favicon.text


def test_web_dashboard_health_does_not_connect_to_daemon() -> None:
    def forbidden_factory() -> FakeDaemonApiClient:
        raise AssertionError("health endpoint must not connect to the daemon")

    app = create_web_dashboard_app(forbidden_factory)

    with TestClient(app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": {
            "name": "sdsctl-web",
            "package_version": __version__,
            "protocol": WEB_DASHBOARD_API_PROTOCOL,
            "version": WEB_DASHBOARD_API_VERSION,
        },
    }


def test_web_dashboard_api_index_advertises_endpoints() -> None:
    app = create_web_dashboard_app(FakeDaemonApiClient)

    with TestClient(app) as client:
        response = client.get("/api/v1")

    assert response.status_code == 200
    assert response.json()["links"] == {
        "audio": "/api/v1/audio",
        "dashboard": "/",
        "docs": "/api/v1/docs",
        "events": "/api/v1/events",
        "health": "/healthz",
        "openapi": "/api/v1/openapi.json",
        "recording": "/api/v1/recording",
        "recordings": "/api/v1/recordings",
        "recording_file": "/api/v1/recordings/file/{identifier}",
        "redoc": "/api/v1/redoc",
        "scanner_hold": "/api/v1/scanner/hold/{scope}",
        "scanner_next": "/api/v1/scanner/next",
        "scanner_previous": "/api/v1/scanner/previous",
        "scanner_reconnect": "/api/v1/scanner/reconnect",
        "snapshot": "/api/v1/snapshot",
        "status": "/api/v1/status",
    }


def test_web_dashboard_status_negotiates_and_returns_snapshot() -> None:
    daemon_client = FakeDaemonApiClient(
        hello={"protocol": "sdsctl.daemon", "selected_version": 1},
        snapshot={
            "scanner_endpoint": "192.168.0.251",
            "scanner_connected": True,
        },
    )
    app = create_web_dashboard_app(lambda: daemon_client)

    with TestClient(app) as client:
        response = client.get("/api/v1/status")

    assert response.status_code == 200
    assert response.json() == {
        "protocol": WEB_DASHBOARD_API_PROTOCOL,
        "version": WEB_DASHBOARD_API_VERSION,
        "daemon": {
            "hello": {
                "protocol": "sdsctl.daemon",
                "selected_version": 1,
            },
            "snapshot": {
                "scanner_endpoint": "192.168.0.251",
                "scanner_connected": True,
            },
        },
    }
    assert daemon_client.entered is True
    assert daemon_client.closed is True
    assert daemon_client.hello_calls == 1
    assert daemon_client.snapshot_calls == 1


def test_web_dashboard_snapshot_negotiates_before_snapshot() -> None:
    daemon_client = FakeDaemonApiClient(
        hello={"selected_version": 1},
        snapshot={"scanner_connected": False},
    )
    app = create_web_dashboard_app(lambda: daemon_client)

    with TestClient(app) as client:
        response = client.get("/api/v1/snapshot")

    assert response.status_code == 200
    assert response.json() == {
        "protocol": WEB_DASHBOARD_API_PROTOCOL,
        "version": WEB_DASHBOARD_API_VERSION,
        "snapshot": {"scanner_connected": False},
    }
    assert daemon_client.hello_calls == 1
    assert daemon_client.snapshot_calls == 1
    assert daemon_client.closed is True


def test_web_dashboard_streams_ordered_daemon_events_as_sse() -> None:
    observed_at = datetime(2026, 8, 6, 18, 30, tzinfo=UTC)
    snapshot = DaemonEvent.create(
        7,
        DaemonEventKind.SNAPSHOT,
        {"state": "running", "scanner_connected": True},
        observed_at=observed_at,
    )
    connection = DaemonEvent.create(
        8,
        DaemonEventKind.SCANNER_CONNECTION,
        {
            "endpoint": "udp://192.0.2.25:50536",
            "connected": False,
        },
        observed_at=observed_at,
    )
    event_client = FakeDaemonEventClient(events=[snapshot, connection])
    app = create_web_dashboard_app(
        FakeDaemonApiClient,
        lambda: event_client,
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/events")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["x-content-type-options"] == "nosniff"

    lines = response.text.splitlines()
    assert [line for line in lines if line.startswith("id: ")] == [
        "id: 7",
        "id: 8",
    ]

    payloads = [
        json.loads(line.removeprefix("data: "))
        for line in lines
        if line.startswith("data: ")
    ]
    assert payloads == [snapshot.as_dict(), connection.as_dict()]
    assert event_client.receive_calls == 3
    assert event_client.closed is True


def test_web_dashboard_event_stream_cancellation_closes_daemon_client() -> None:
    observed_at = datetime(2026, 8, 7, 16, 0, tzinfo=UTC)
    snapshot = DaemonEvent.create(
        41,
        DaemonEventKind.SNAPSHOT,
        {"state": "running", "scanner_connected": True},
        observed_at=observed_at,
    )
    event_client = BlockingDaemonEventClient()

    async def exercise() -> None:
        iterator = web_dashboard._iter_daemon_events(event_client, snapshot)
        first = await anext(iterator)
        assert first.startswith(b"id: 41\n")

        pending = asyncio.create_task(anext(iterator))
        receive_started = await asyncio.to_thread(
            event_client.receive_started.wait,
            1.0,
        )
        assert receive_started is True

        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending

    asyncio.run(exercise())

    assert event_client.closed is True


def test_web_dashboard_redacts_initial_event_stream_failures() -> None:
    event_client = FakeDaemonEventClient(
        error=DaemonUnavailableError(
            "Daemon event socket was not found: /private/sdsctl/events.sock"
        )
    )
    app = create_web_dashboard_app(
        FakeDaemonApiClient,
        lambda: event_client,
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/events")

    assert response.status_code == 503
    assert response.json() == {"detail": WEB_DASHBOARD_UNAVAILABLE_DETAIL}
    assert "/private/sdsctl/events.sock" not in response.text
    assert event_client.closed is True


def test_web_dashboard_events_require_configured_factory() -> None:
    app = create_web_dashboard_app(FakeDaemonApiClient)

    with TestClient(app) as client:
        response = client.get("/api/v1/events")

    assert response.status_code == 503
    assert response.json() == {"detail": WEB_DASHBOARD_UNAVAILABLE_DETAIL}


def test_web_dashboard_streams_validated_daemon_pcmu_frames() -> None:
    first = pcmu_delivery(11, b"\xff\x7f\x00")
    second = pcmu_delivery(
        12,
        b"\x10\x20",
        packets_dropped=1,
        payload_bytes_dropped=3,
        overflows=1,
    )
    pcmu_client = FakeDaemonPcmuClient(deliveries=[first, second])
    app = create_web_dashboard_app(
        FakeDaemonApiClient,
        FakeDaemonEventClient,
        lambda: pcmu_client,
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/audio")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/octet-stream")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.content == (
        encode_pcmu_delivery(first) + encode_pcmu_delivery(second)
    )
    assert pcmu_client.connect_calls == 1
    assert pcmu_client.receive_calls == 3
    assert pcmu_client.closed is True


def test_web_dashboard_audio_stream_cancellation_closes_daemon_client() -> None:
    pcmu_client = BlockingDaemonPcmuClient()

    async def exercise() -> None:
        iterator = web_dashboard._iter_daemon_audio(pcmu_client)
        pending = asyncio.create_task(anext(iterator))
        receive_started = await asyncio.to_thread(
            pcmu_client.receive_started.wait,
            1.0,
        )
        assert receive_started is True

        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending

    asyncio.run(exercise())

    assert pcmu_client.closed is True


def test_web_dashboard_redacts_initial_pcmu_connection_failures() -> None:
    pcmu_client = FakeDaemonPcmuClient(
        connect_error=DaemonUnavailableError(
            "Daemon PCMU socket was not found: /private/sdsctl/pcmu.sock"
        )
    )
    app = create_web_dashboard_app(
        FakeDaemonApiClient,
        FakeDaemonEventClient,
        lambda: pcmu_client,
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/audio")

    assert response.status_code == 503
    assert response.json() == {"detail": WEB_DASHBOARD_UNAVAILABLE_DETAIL}
    assert "/private/sdsctl/pcmu.sock" not in response.text
    assert pcmu_client.connect_calls == 1
    assert pcmu_client.receive_calls == 0
    assert pcmu_client.closed is True


def test_web_dashboard_audio_requires_configured_factory() -> None:
    app = create_web_dashboard_app(FakeDaemonApiClient)

    with TestClient(app) as client:
        response = client.get("/api/v1/audio")

    assert response.status_code == 503
    assert response.json() == {"detail": WEB_DASHBOARD_UNAVAILABLE_DETAIL}


def test_web_dashboard_redacts_daemon_failures() -> None:
    daemon_client = FakeDaemonApiClient(
        error=DaemonUnavailableError(
            "Daemon socket was not found: /private/sdsctl/daemon.sock"
        )
    )
    app = create_web_dashboard_app(lambda: daemon_client)

    with TestClient(app) as client:
        response = client.get("/api/v1/status")

    assert response.status_code == 503
    assert response.json() == {"detail": WEB_DASHBOARD_UNAVAILABLE_DETAIL}
    assert "/private/sdsctl/daemon.sock" not in response.text
    assert daemon_client.closed is True



def _web_control_hello(*operations: str) -> dict[str, object]:
    return {
        "read_only": False,
        "control_operations": list(operations),
    }


def _web_control_snapshot() -> dict[str, object]:
    return {
        "state": "running",
        "scanner_connected": True,
        "radio_state": {
            "system_index": 100,
            "system_hold": "Off",
            "department_index": 200,
            "department_hold": "On",
            "site_index": 300,
            "site_hold": "Off",
            "channel_index": 400,
            "channel_kind": "TGID",
            "channel_hold": "On",
        },
    }


def test_web_dashboard_scanner_controls_resolve_current_snapshot() -> None:
    daemon_client = FakeDaemonApiClient(
        hello=_web_control_hello(
            "scanner.hold_state",
            "scanner.next",
            "scanner.previous",
            "scanner.reconnect",
        ),
        snapshot=_web_control_snapshot(),
    )
    app = create_web_dashboard_app(lambda: daemon_client)

    with TestClient(app) as client:
        system = client.post(
            "/api/v1/scanner/hold/system",
            json={"held": True},
        )
        department = client.post(
            "/api/v1/scanner/hold/department",
            json={"held": False},
        )
        site = client.post(
            "/api/v1/scanner/hold/site",
            json={"held": True},
        )
        channel = client.post(
            "/api/v1/scanner/hold/channel",
            json={"held": False},
        )
        previous = client.post("/api/v1/scanner/previous")
        next_response = client.post("/api/v1/scanner/next")
        reconnect = client.post("/api/v1/scanner/reconnect")

    for response in (
        system,
        department,
        site,
        channel,
        previous,
        next_response,
        reconnect,
    ):
        assert response.status_code == 200
        assert response.json()["control"]["snapshot"] == _web_control_snapshot()

    assert daemon_client.control_calls == [
        ("hold_state", "system", True, 4.0),
        ("hold_state", "department", False, 4.0),
        ("hold_state", "site", True, 4.0),
        ("hold_state", "channel", False, 4.0),
        ("previous", "TGID", 400, None, 1, 2.0),
        ("next", "TGID", 400, None, 1, 2.0),
        ("reconnect", 2.0),
    ]
    assert daemon_client.hello_calls == 7
    assert daemon_client.snapshot_calls == 2


def test_web_dashboard_rejects_unadvertised_scanner_control() -> None:
    daemon_client = FakeDaemonApiClient(
        hello=_web_control_hello("scanner.reconnect"),
        snapshot=_web_control_snapshot(),
    )
    app = create_web_dashboard_app(lambda: daemon_client)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/scanner/hold/channel",
            json={"held": True},
        )

    assert response.status_code == 409
    assert response.json() == {
        "detail": web_dashboard.WEB_DASHBOARD_CONTROL_UNAVAILABLE_DETAIL,
    }
    assert daemon_client.snapshot_calls == 0
    assert daemon_client.control_calls == []


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"held": "true"},
        {"held": True, "extra": 1},
    ],
)
def test_web_dashboard_rejects_invalid_hold_state_body(
    payload: object,
) -> None:
    daemon_client = FakeDaemonApiClient(
        hello=_web_control_hello("scanner.hold_state"),
        snapshot=_web_control_snapshot(),
    )
    app = create_web_dashboard_app(lambda: daemon_client)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/scanner/hold/system",
            json=payload,
        )

    assert response.status_code == 400
    assert response.json() == {
        "detail": web_dashboard.WEB_DASHBOARD_CONTROL_INVALID_DETAIL,
    }
    assert daemon_client.hello_calls == 0
    assert daemon_client.control_calls == []


def test_web_dashboard_rejects_unavailable_current_selection() -> None:
    snapshot = _web_control_snapshot()
    snapshot["radio_state"] = {
        "channel_index": 400,
        "channel_kind": "SrchFrequency",
    }
    daemon_client = FakeDaemonApiClient(
        hello=_web_control_hello("scanner.next"),
        snapshot=snapshot,
    )
    app = create_web_dashboard_app(lambda: daemon_client)

    with TestClient(app) as client:
        response = client.post("/api/v1/scanner/next")

    assert response.status_code == 409
    assert response.json() == {
        "detail": (
            web_dashboard.WEB_DASHBOARD_CONTROL_SELECTION_UNAVAILABLE_DETAIL
        ),
    }
    assert daemon_client.control_calls == []


@pytest.mark.parametrize(
    ("code", "status_code", "detail"),
    [
        (
            "control_busy",
            409,
            web_dashboard.WEB_DASHBOARD_CONTROL_BUSY_DETAIL,
        ),
        (
            "control_unavailable",
            409,
            web_dashboard.WEB_DASHBOARD_CONTROL_UNAVAILABLE_DETAIL,
        ),
        (
            "unsupported_operation",
            409,
            web_dashboard.WEB_DASHBOARD_CONTROL_UNAVAILABLE_DETAIL,
        ),
        (
            "control_timeout",
            504,
            web_dashboard.WEB_DASHBOARD_CONTROL_TIMEOUT_DETAIL,
        ),
        (
            "control_rejected",
            409,
            web_dashboard.WEB_DASHBOARD_CONTROL_REJECTED_DETAIL,
        ),
        (
            "invalid_parameters",
            400,
            web_dashboard.WEB_DASHBOARD_CONTROL_INVALID_DETAIL,
        ),
        (
            "control_failed",
            503,
            web_dashboard.WEB_DASHBOARD_CONTROL_FAILED_DETAIL,
        ),
    ],
)
def test_web_dashboard_maps_scanner_control_errors(
    code: str,
    status_code: int,
    detail: str,
) -> None:
    daemon_client = FakeDaemonApiClient(
        hello=_web_control_hello("scanner.reconnect"),
        snapshot=_web_control_snapshot(),
        control_error=DaemonRequestError(
            code,
            "secret daemon detail /private/control.sock",
            request_id="control-web-1",
        ),
    )
    app = create_web_dashboard_app(lambda: daemon_client)

    with TestClient(app) as client:
        response = client.post("/api/v1/scanner/reconnect")

    assert response.status_code == status_code
    assert response.json() == {"detail": detail}
    assert "secret" not in response.text
    assert "/private/control.sock" not in response.text


def test_web_dashboard_redacts_scanner_control_connection_failures() -> None:
    daemon_client = FakeDaemonApiClient(
        error=DaemonUnavailableError("/private/sdsctl/daemon.sock"),
    )
    app = create_web_dashboard_app(lambda: daemon_client)

    with TestClient(app) as client:
        response = client.post("/api/v1/scanner/reconnect")

    assert response.status_code == 503
    assert response.json() == {
        "detail": web_dashboard.WEB_DASHBOARD_CONTROL_UNAVAILABLE_DETAIL,
    }
    assert "/private/sdsctl/daemon.sock" not in response.text
    assert daemon_client.control_calls == []


def test_web_dashboard_semantic_release_does_not_depend_on_cached_index() -> None:
    unavailable = (1 << 32) - 1
    daemon_client = FakeDaemonApiClient(
        hello=_web_control_hello(
            "scanner.hold_state",
            "scanner.next",
            "scanner.previous",
        ),
        snapshot={
            "state": "running",
            "scanner_connected": True,
            "radio_state": {
                "channel_kind": "TGID",
                "channel_index": unavailable,
                "channel_hold": "On",
            },
        },
    )
    app = create_web_dashboard_app(lambda: daemon_client)

    with TestClient(app) as client:
        channel_release = client.post(
            "/api/v1/scanner/hold/channel",
            json={"held": False},
        )
        next_channel = client.post("/api/v1/scanner/next")
        previous_channel = client.post("/api/v1/scanner/previous")

    assert channel_release.status_code == 200
    for response in (next_channel, previous_channel):
        assert response.status_code == 409
        assert response.json() == {
            "detail": (
                web_dashboard.WEB_DASHBOARD_CONTROL_SELECTION_UNAVAILABLE_DETAIL
            ),
        }
    assert daemon_client.control_calls == [
        ("hold_state", "channel", False, 4.0),
    ]


def test_web_dashboard_recording_routes_proxy_daemon_api() -> None:
    daemon_client = FakeDaemonApiClient()
    app = create_web_dashboard_app(lambda: daemon_client)

    with TestClient(app) as client:
        status = client.get("/api/v1/recording")
        started = client.post("/api/v1/recording/start")
        stopped = client.post("/api/v1/recording/stop")
        recordings = client.get("/api/v1/recordings")

    assert status.status_code == 200
    assert status.json()["recording"]["status"] == "idle"
    assert started.status_code == 200
    assert started.json()["recording"]["status"] == "recording"
    assert stopped.status_code == 200
    assert stopped.json()["recording"]["status"] == "stopped"
    assert recordings.status_code == 200
    assert recordings.json()["recordings"]["total_entries"] == 1
    assert daemon_client.recording_status_calls == 1
    assert daemon_client.recording_start_calls == 1
    assert daemon_client.recording_stop_calls == 1
    assert daemon_client.recordings_list_calls == 1


@pytest.mark.parametrize(
    ("code", "status_code", "detail"),
    [
        ("recording_busy", 409, web_dashboard.WEB_DASHBOARD_RECORDING_BUSY_DETAIL),
        (
            "recording_unavailable",
            503,
            web_dashboard.WEB_DASHBOARD_RECORDING_UNAVAILABLE_DETAIL,
        ),
        (
            "recording_failed",
            503,
            web_dashboard.WEB_DASHBOARD_RECORDING_FAILED_DETAIL,
        ),
    ],
)
def test_web_dashboard_maps_recording_api_errors(
    code: str,
    status_code: int,
    detail: str,
) -> None:
    daemon_client = FakeDaemonApiClient(
        recording_error=DaemonRequestError(
            code,
            "secret daemon detail /private/recordings",
            request_id="recording-web-1",
        )
    )
    app = create_web_dashboard_app(lambda: daemon_client)

    with TestClient(app) as client:
        response = client.post("/api/v1/recording/start")

    assert response.status_code == status_code
    assert response.json() == {"detail": detail}
    assert "secret" not in response.text
    assert "/private/recordings" not in response.text


def test_web_dashboard_streams_recording_via_private_daemon_client() -> None:
    def forbidden_pcmu_factory() -> FakeDaemonPcmuClient:
        raise AssertionError("saved recording playback must not open daemon PCMU")

    recording_file_client = FakeDaemonRecordingFileClient(
        payload=b"RIFF" + (b"\x00" * 32)
    )
    app = create_web_dashboard_app(
        FakeDaemonApiClient,
        FakeDaemonEventClient,
        forbidden_pcmu_factory,
        lambda: recording_file_client,
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/recordings/file/2026/08/test.wav"
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/wav")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-length"] == "36"
    assert response.content == b"RIFF" + (b"\x00" * 32)
    assert recording_file_client.identifiers == ["2026/08/test.wav"]
    assert recording_file_client.downloads[0].closed is True


@pytest.mark.parametrize(
    ("status", "status_code", "detail"),
    [
        (
            RecordingFileResponseStatus.INVALID_IDENTIFIER,
            400,
            web_dashboard.WEB_DASHBOARD_RECORDING_INVALID_IDENTIFIER_DETAIL,
        ),
        (
            RecordingFileResponseStatus.NOT_FOUND,
            404,
            web_dashboard.WEB_DASHBOARD_RECORDING_NOT_FOUND_DETAIL,
        ),
        (
            RecordingFileResponseStatus.NOT_PLAYABLE,
            409,
            web_dashboard.WEB_DASHBOARD_RECORDING_NOT_PLAYABLE_DETAIL,
        ),
        (
            RecordingFileResponseStatus.UNAVAILABLE,
            409,
            web_dashboard.WEB_DASHBOARD_RECORDING_UNAVAILABLE_DETAIL,
        ),
        (
            RecordingFileResponseStatus.FAILED,
            503,
            web_dashboard.WEB_DASHBOARD_RECORDING_FAILED_DETAIL,
        ),
    ],
)
def test_web_dashboard_maps_recording_file_errors(
    status: RecordingFileResponseStatus,
    status_code: int,
    detail: str,
) -> None:
    recording_file_client = FakeDaemonRecordingFileClient(
        error=DaemonRecordingFileRequestError(status)
    )
    app = create_web_dashboard_app(
        FakeDaemonApiClient,
        FakeDaemonEventClient,
        FakeDaemonPcmuClient,
        lambda: recording_file_client,
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/recordings/file/private/secret.wav"
        )

    assert response.status_code == status_code
    assert response.json() == {"detail": detail}
    assert "private/secret.wav" not in response.text


def test_web_dashboard_recording_file_requires_configured_factory() -> None:
    app = create_web_dashboard_app(FakeDaemonApiClient)

    with TestClient(app) as client:
        response = client.get("/api/v1/recordings/file/test.wav")

    assert response.status_code == 503
    assert response.json() == {
        "detail": web_dashboard.WEB_DASHBOARD_RECORDING_UNAVAILABLE_DETAIL
    }


def test_web_dashboard_redacts_recording_file_connection_failures() -> None:
    recording_file_client = FakeDaemonRecordingFileClient(
        error=DaemonUnavailableError(
            "Daemon recording-file socket was not found: "
            "/private/sdsctl/recordings.sock"
        )
    )
    app = create_web_dashboard_app(
        FakeDaemonApiClient,
        FakeDaemonEventClient,
        FakeDaemonPcmuClient,
        lambda: recording_file_client,
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/recordings/file/test.wav")

    assert response.status_code == 503
    assert response.json() == {
        "detail": web_dashboard.WEB_DASHBOARD_RECORDING_UNAVAILABLE_DETAIL
    }
    assert "/private/sdsctl/recordings.sock" not in response.text


def test_web_dashboard_serves_local_interactive_docs_without_daemon() -> None:
    def forbidden_factory() -> FakeDaemonApiClient:
        raise AssertionError("API documentation must not connect to the daemon")

    app = create_web_dashboard_app(forbidden_factory)

    with TestClient(app) as client:
        swagger_response = client.get("/api/v1/docs")
        redoc_response = client.get("/api/v1/redoc")
        swagger_css = client.get("/assets/api-docs/swagger-ui.css")
        swagger_bundle = client.get("/assets/api-docs/swagger-ui-bundle.js")
        swagger_init = client.get("/assets/api-docs/swagger-ui-init.js")
        redoc_bundle = client.get("/assets/api-docs/redoc.standalone.js")
        redoc_init = client.get("/assets/api-docs/redoc-init.js")
        legacy_docs_response = client.get("/docs")
        legacy_redoc_response = client.get("/redoc")
        openapi_response = client.get("/api/v1/openapi.json")

    assert swagger_response.status_code == 200
    assert swagger_response.headers["content-type"].startswith("text/html")
    assert swagger_response.headers["cache-control"] == "no-store"
    swagger_csp = swagger_response.headers["content-security-policy"]
    assert "default-src 'none'" in swagger_csp
    assert "style-src 'self' 'unsafe-inline'" in swagger_csp
    assert "script-src 'self'" in swagger_csp
    assert "connect-src 'self'" in swagger_csp
    assert "https:" not in swagger_csp
    assert 'href="../../assets/api-docs/swagger-ui.css"' in swagger_response.text
    assert (
        'src="../../assets/api-docs/swagger-ui-bundle.js"'
        in swagger_response.text
    )
    assert 'src="../../assets/api-docs/swagger-ui-init.js"' in swagger_response.text
    assert "https://" not in swagger_response.text
    assert "http://" not in swagger_response.text
    assert "<style" not in swagger_response.text
    assert "<script>" not in swagger_response.text

    assert redoc_response.status_code == 200
    assert redoc_response.headers["content-type"].startswith("text/html")
    assert redoc_response.headers["cache-control"] == "no-store"
    redoc_csp = redoc_response.headers["content-security-policy"]
    assert "style-src 'self' 'unsafe-inline'" in redoc_csp
    assert "script-src 'self'" in redoc_csp
    assert "connect-src 'self'" in redoc_csp
    assert "https:" not in redoc_csp
    assert 'src="../../assets/api-docs/redoc.standalone.js"' in redoc_response.text
    assert 'src="../../assets/api-docs/redoc-init.js"' in redoc_response.text
    assert "https://" not in redoc_response.text
    assert "http://" not in redoc_response.text
    assert "<style" not in redoc_response.text
    assert "<script>" not in redoc_response.text

    assert swagger_css.status_code == 200
    assert swagger_css.headers["content-type"].startswith("text/css")
    assert len(swagger_css.content) == 178977

    assert swagger_bundle.status_code == 200
    assert swagger_bundle.headers["content-type"].startswith(
        "application/javascript"
    )
    assert len(swagger_bundle.content) == 1551729

    assert swagger_init.status_code == 200
    assert swagger_init.headers["content-type"].startswith(
        "application/javascript"
    )
    assert 'url: "openapi.json"' in swagger_init.text
    assert "validatorUrl: null" in swagger_init.text
    assert "https://" not in swagger_init.text
    assert "http://" not in swagger_init.text

    assert redoc_bundle.status_code == 200
    assert redoc_bundle.headers["content-type"].startswith(
        "application/javascript"
    )
    assert len(redoc_bundle.content) == 1097271

    assert redoc_init.status_code == 200
    assert redoc_init.headers["content-type"].startswith(
        "application/javascript"
    )
    assert '"openapi.json"' in redoc_init.text
    assert "https://" not in redoc_init.text
    assert "http://" not in redoc_init.text

    assert legacy_docs_response.status_code == 404
    assert legacy_redoc_response.status_code == 404
    assert openapi_response.status_code == 200
    assert openapi_response.json()["info"]["version"] == __version__
    assert "/api/v1/docs" not in openapi_response.json()["paths"]
    assert "/api/v1/redoc" not in openapi_response.json()["paths"]
    assert "/api/v1/events" in openapi_response.json()["paths"]
    assert "/api/v1/audio" in openapi_response.json()["paths"]
    assert "/api/v1/recording" in openapi_response.json()["paths"]
    assert "/api/v1/scanner/hold/{scope}" in openapi_response.json()["paths"]
    assert "/api/v1/scanner/next" in openapi_response.json()["paths"]
    assert "/api/v1/scanner/previous" in openapi_response.json()["paths"]
    assert "/api/v1/scanner/reconnect" in openapi_response.json()["paths"]
    assert "/api/v1/recordings" in openapi_response.json()["paths"]
    assert (
        "/api/v1/recordings/file/{identifier}"
        in openapi_response.json()["paths"]
    )
