"""Daemon-backed HTTP application and browser dashboard shell."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from functools import cache
from importlib.resources import files
from typing import Protocol, TypeAlias

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response

from . import __version__
from .exceptions import SDS200Error

WEB_DASHBOARD_API_PROTOCOL = "sdsctl.web"
WEB_DASHBOARD_API_VERSION = 1
WEB_DASHBOARD_UNAVAILABLE_DETAIL = "The scanner daemon is unavailable."

_WEB_ASSET_PACKAGE = "sds200.web_assets"
_WEB_RESPONSE_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'none'; "
        "style-src 'self'; "
        "script-src 'self'; "
        "connect-src 'self'; "
        "img-src 'self'; "
        "font-src 'self'; "
        "base-uri 'none'; "
        "form-action 'none'; "
        "frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}

logger = logging.getLogger(__name__)


class DaemonApiClientLike(Protocol):
    """Minimum daemon API client contract required by the web service."""

    def hello(self) -> Mapping[str, object]:
        """Return negotiated daemon capabilities."""

    def runtime_snapshot(self) -> Mapping[str, object]:
        """Return one authoritative daemon runtime snapshot."""


class DaemonApiClientContext(Protocol):
    """Context-managed daemon API client."""

    def __enter__(self) -> DaemonApiClientLike:
        """Open and return the daemon client."""

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        """Close the daemon client."""


DaemonApiClientFactory: TypeAlias = Callable[[], DaemonApiClientContext]
_DaemonQuery: TypeAlias = Callable[
    [DaemonApiClientLike],
    Mapping[str, object],
]


def create_web_dashboard_app(
    api_client_factory: DaemonApiClientFactory,
) -> FastAPI:
    """Create the daemon-backed web application without scanner ownership."""

    if not callable(api_client_factory):
        raise TypeError("Daemon API client factory must be callable.")

    app = FastAPI(
        title="sdsctl web dashboard",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/v1/openapi.json",
    )

    @app.get(
        "/",
        include_in_schema=False,
        response_class=HTMLResponse,
    )
    def index() -> HTMLResponse:
        return HTMLResponse(
            content=_read_web_asset("dashboard.html"),
            headers=dict(_WEB_RESPONSE_HEADERS),
        )

    @app.get(
        "/assets/dashboard.css",
        include_in_schema=False,
        response_class=Response,
    )
    def stylesheet() -> Response:
        return _asset_response(
            "dashboard.css",
            media_type="text/css",
        )

    @app.get(
        "/assets/dashboard.js",
        include_in_schema=False,
        response_class=Response,
    )
    def script() -> Response:
        return _asset_response(
            "dashboard.js",
            media_type="application/javascript",
        )

    @app.get("/api/v1")
    def api_index() -> dict[str, object]:
        return {
            "service": _service_metadata(),
            "links": {
                "dashboard": "/",
                "health": "/healthz",
                "snapshot": "/api/v1/snapshot",
                "status": "/api/v1/status",
            },
        }

    @app.get("/healthz")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "service": _service_metadata(),
        }

    @app.get("/api/v1/status")
    def status() -> dict[str, object]:
        return {
            **_api_envelope(),
            "daemon": _query_daemon(
                api_client_factory,
                _daemon_status,
            ),
        }

    @app.get("/api/v1/snapshot")
    def snapshot() -> dict[str, object]:
        return {
            **_api_envelope(),
            "snapshot": _query_daemon(
                api_client_factory,
                _daemon_snapshot,
            ),
        }

    return app


@cache
def _read_web_asset(name: str) -> str:
    return (
        files(_WEB_ASSET_PACKAGE)
        .joinpath(name)
        .read_text(encoding="utf-8")
    )


def _asset_response(
    name: str,
    *,
    media_type: str,
) -> Response:
    return Response(
        content=_read_web_asset(name),
        media_type=media_type,
        headers=dict(_WEB_RESPONSE_HEADERS),
    )


def _service_metadata() -> dict[str, object]:
    return {
        "name": "sdsctl-web",
        "package_version": __version__,
        "protocol": WEB_DASHBOARD_API_PROTOCOL,
        "version": WEB_DASHBOARD_API_VERSION,
    }


def _api_envelope() -> dict[str, object]:
    return {
        "protocol": WEB_DASHBOARD_API_PROTOCOL,
        "version": WEB_DASHBOARD_API_VERSION,
    }


def _query_daemon(
    api_client_factory: DaemonApiClientFactory,
    query: _DaemonQuery,
) -> dict[str, object]:
    try:
        with api_client_factory() as client:
            return dict(query(client))
    except (SDS200Error, OSError) as error:
        logger.warning(
            "web dashboard daemon request failed error_type=%s",
            error.__class__.__name__,
        )
        raise HTTPException(
            status_code=503,
            detail=WEB_DASHBOARD_UNAVAILABLE_DETAIL,
        ) from None


def _daemon_status(
    client: DaemonApiClientLike,
) -> Mapping[str, object]:
    return {
        "hello": dict(client.hello()),
        "snapshot": dict(client.runtime_snapshot()),
    }


def _daemon_snapshot(
    client: DaemonApiClientLike,
) -> Mapping[str, object]:
    client.hello()
    return client.runtime_snapshot()


__all__ = [
    "DaemonApiClientContext",
    "DaemonApiClientFactory",
    "DaemonApiClientLike",
    "WEB_DASHBOARD_API_PROTOCOL",
    "WEB_DASHBOARD_API_VERSION",
    "WEB_DASHBOARD_UNAVAILABLE_DETAIL",
    "create_web_dashboard_app",
]
