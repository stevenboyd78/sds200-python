from __future__ import annotations

from collections.abc import Mapping
from typing import Self

import pytest
from fastapi.testclient import TestClient

from sds200 import __version__
from sds200.exceptions import DaemonUnavailableError
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


def test_web_dashboard_requires_callable_client_factory() -> None:
    with pytest.raises(
        TypeError,
        match="Daemon API client factory must be callable",
    ):
        create_web_dashboard_app(None)  # type: ignore[arg-type]


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


def test_web_dashboard_index_advertises_foundation_endpoints() -> None:
    app = create_web_dashboard_app(FakeDaemonApiClient)

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.json()["links"] == {
        "health": "/healthz",
        "snapshot": "/api/v1/snapshot",
        "status": "/api/v1/status",
    }


def test_web_dashboard_status_negotiates_and_returns_snapshot() -> None:
    daemon_client = FakeDaemonApiClient(
        hello={
            "protocol": "sdsctl.daemon",
            "selected_version": 1,
        },
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
    assert response.json() == {
        "detail": WEB_DASHBOARD_UNAVAILABLE_DETAIL,
    }
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
