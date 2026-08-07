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
from sds200.exceptions import DaemonDisconnectedError, DaemonUnavailableError
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
    ) -> None:
        self.hello_result = dict(hello or {})
        self.snapshot_result = dict(snapshot or {})
        self.error = error
        self.entered = False
        self.closed = False
        self.hello_calls = 0
        self.snapshot_calls = 0

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
    assert "<title>sdsctl scanner dashboard</title>" in response.text
    assert 'id="main-content"' in response.text
    assert 'id="status-badge"' in response.text
    assert 'id="audio-play"' in response.text
    assert 'id="audio-stop"' in response.text
    assert "Milestone 20.2" not in response.text
    assert 'href="/assets/favicon.svg"' in response.text
    assert 'type="image/svg+xml"' in response.text
    assert 'href="/assets/dashboard.css"' in response.text
    assert 'src="/assets/dashboard.js"' in response.text
    assert "<style" not in response.text
    assert "<script>" not in response.text


def test_web_dashboard_serves_packaged_static_assets() -> None:
    def forbidden_factory() -> FakeDaemonApiClient:
        raise AssertionError("static assets must not connect to the daemon")

    app = create_web_dashboard_app(forbidden_factory)

    with TestClient(app) as client:
        stylesheet = client.get("/assets/dashboard.css")
        script = client.get("/assets/dashboard.js")
        audio_worklet = client.get("/assets/audio-worklet.js")
        favicon = client.get("/assets/favicon.svg")

    assert stylesheet.status_code == 200
    assert stylesheet.headers["content-type"].startswith("text/css")
    assert stylesheet.headers["cache-control"] == "no-store"
    assert "--content-width:" in stylesheet.text
    assert "@media (prefers-reduced-motion: reduce)" in stylesheet.text

    assert script.status_code == 200
    assert script.headers["content-type"].startswith("application/javascript")
    assert script.headers["cache-control"] == "no-store"
    assert 'fetch("/api/v1/status"' in script.text
    assert 'new EventSource("/api/v1/events")' in script.text
    assert "FALLBACK_REFRESH_INTERVAL_MS" in script.text
    assert "RECONCILE_INTERVAL_MS" in script.text
    assert 'fetch("/api/v1/audio"' in script.text
    assert 'audioWorklet.addModule("/assets/audio-worklet.js")' in script.text
    assert "new AudioWorkletNode" in script.text
    assert "new AbortController" in script.text
    assert "getBigUint64" in script.text
    assert "PCMU stream gap does not match daemon queue-loss counters" in script.text
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
        "events": "/api/v1/events",
        "health": "/healthz",
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


def test_web_dashboard_disables_interactive_docs() -> None:
    app = create_web_dashboard_app(FakeDaemonApiClient)

    with TestClient(app) as client:
        docs_response = client.get("/docs")
        redoc_response = client.get("/redoc")
        openapi_response = client.get("/api/v1/openapi.json")

    assert docs_response.status_code == 404
    assert redoc_response.status_code == 404
    assert openapi_response.status_code == 200
    assert openapi_response.json()["info"]["version"] == __version__
    assert "/api/v1/events" in openapi_response.json()["paths"]
    assert "/api/v1/audio" in openapi_response.json()["paths"]
