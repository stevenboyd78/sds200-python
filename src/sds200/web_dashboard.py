"""Daemon-backed HTTP application and browser dashboard shell."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable, Mapping
from functools import cache
from importlib.resources import files
from typing import Protocol, TypeAlias

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response, StreamingResponse

from . import __version__
from .daemon_events import DaemonEvent
from .daemon_recording_file_client import (
    DaemonRecordingFileDownload,
    DaemonRecordingFileRequestError,
)
from .daemon_recording_file_protocol import RecordingFileResponseStatus
from .exceptions import DaemonRequestError, SDS200Error
from .pcmu_protocol import encode_pcmu_delivery
from .pcmu_subscriptions import PcmuPacketDelivery

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
        "media-src 'self'; "
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
_API_DOCS_RESPONSE_HEADERS = {
    **_WEB_RESPONSE_HEADERS,
    "Content-Security-Policy": (
        "default-src 'none'; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; "
        "connect-src 'self'; "
        "img-src 'self' data:; "
        "font-src 'self' data:; "
        "base-uri 'none'; "
        "form-action 'none'; "
        "frame-ancestors 'none'"
    ),
}
_EVENT_STREAM_RESPONSE_HEADERS = {
    "Cache-Control": "no-store",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
    "X-Content-Type-Options": "nosniff",
}
_AUDIO_STREAM_RESPONSE_HEADERS = {
    "Cache-Control": "no-store",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
    "X-Content-Type-Options": "nosniff",
}
_RECORDING_FILE_RESPONSE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
}

WEB_DASHBOARD_RECORDING_BUSY_DETAIL = (
    "A daemon recording is already active or awaiting finalization."
)
WEB_DASHBOARD_RECORDING_UNAVAILABLE_DETAIL = (
    "Daemon recording is currently unavailable."
)
WEB_DASHBOARD_RECORDING_FAILED_DETAIL = (
    "The daemon recording operation could not be completed."
)
WEB_DASHBOARD_RECORDING_INVALID_IDENTIFIER_DETAIL = (
    "The recording identifier is invalid."
)
WEB_DASHBOARD_RECORDING_NOT_FOUND_DETAIL = "The recording was not found."
WEB_DASHBOARD_RECORDING_NOT_PLAYABLE_DETAIL = (
    "The recording is not playable."
)

logger = logging.getLogger(__name__)


class DaemonApiClientLike(Protocol):
    """Minimum daemon API client contract required by the web service."""

    def hello(self) -> Mapping[str, object]:
        """Return negotiated daemon capabilities."""

    def runtime_snapshot(self) -> Mapping[str, object]:
        """Return one authoritative daemon runtime snapshot."""

    def recording_status(self) -> Mapping[str, object]:
        """Return the daemon-owned recording state."""

    def recording_start(self) -> Mapping[str, object]:
        """Start one daemon-owned recording."""

    def recording_stop(self) -> Mapping[str, object]:
        """Stop and finalize the active daemon-owned recording."""

    def recordings_list(self) -> Mapping[str, object]:
        """Return the finalized daemon recording inventory."""


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


class DaemonEventClientLike(Protocol):
    """Minimum ordered daemon event-client contract required by the web service."""

    def receive(self) -> DaemonEvent:
        """Receive one validated ordered daemon event."""

    def close(self) -> None:
        """Close the daemon event connection."""


class DaemonPcmuClientLike(Protocol):
    """Minimum daemon PCMU client contract required by the web service."""

    max_endpoint_bytes: int
    max_frame_bytes: int

    def connect(self) -> object:
        """Connect to the daemon PCMU service without waiting for audio."""

    def receive(self) -> PcmuPacketDelivery:
        """Receive one validated PCMU delivery."""

    def close(self) -> None:
        """Close the daemon PCMU connection."""


class DaemonRecordingFileClientLike(Protocol):
    """Minimum daemon recording-file client contract for the web service."""

    def open(self, identifier: str) -> DaemonRecordingFileDownload:
        """Open one finalized recording by inventory-relative identifier."""


DaemonApiClientFactory: TypeAlias = Callable[[], DaemonApiClientContext]
DaemonEventClientFactory: TypeAlias = Callable[[], DaemonEventClientLike]
DaemonPcmuClientFactory: TypeAlias = Callable[[], DaemonPcmuClientLike]
DaemonRecordingFileClientFactory: TypeAlias = Callable[
    [],
    DaemonRecordingFileClientLike,
]
_DaemonQuery: TypeAlias = Callable[
    [DaemonApiClientLike],
    Mapping[str, object],
]


def create_web_dashboard_app(
    api_client_factory: DaemonApiClientFactory,
    event_client_factory: DaemonEventClientFactory | None = None,
    pcmu_client_factory: DaemonPcmuClientFactory | None = None,
    recording_file_client_factory: (
        DaemonRecordingFileClientFactory | None
    ) = None,
) -> FastAPI:
    """Create the daemon-backed web application without scanner ownership."""

    if not callable(api_client_factory):
        raise TypeError("Daemon API client factory must be callable.")
    if event_client_factory is not None and not callable(event_client_factory):
        raise TypeError("Daemon event client factory must be callable or None.")
    if pcmu_client_factory is not None and not callable(pcmu_client_factory):
        raise TypeError("Daemon PCMU client factory must be callable or None.")
    if (
        recording_file_client_factory is not None
        and not callable(recording_file_client_factory)
    ):
        raise TypeError(
            "Daemon recording-file client factory must be callable or None."
        )

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
        return _asset_response("dashboard.css", media_type="text/css")

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

    @app.get(
        "/assets/audio-worklet.js",
        include_in_schema=False,
        response_class=Response,
    )
    def audio_worklet_script() -> Response:
        return _asset_response(
            "audio-worklet.js",
            media_type="application/javascript",
        )

    @app.get(
        "/assets/favicon.svg",
        include_in_schema=False,
        response_class=Response,
    )
    def favicon() -> Response:
        return _asset_response(
            "favicon.svg",
            media_type="image/svg+xml",
        )

    @app.get(
        "/api/v1/docs",
        include_in_schema=False,
        response_class=HTMLResponse,
    )
    def swagger_ui() -> HTMLResponse:
        return _api_docs_response("api-docs-swagger.html")

    @app.get(
        "/api/v1/redoc",
        include_in_schema=False,
        response_class=HTMLResponse,
    )
    def redoc() -> HTMLResponse:
        return _api_docs_response("api-docs-redoc.html")

    @app.get(
        "/assets/api-docs/swagger-ui.css",
        include_in_schema=False,
        response_class=Response,
    )
    def swagger_ui_stylesheet() -> Response:
        return _asset_response(
            "vendor/swagger-ui-5.32.11/swagger-ui.css",
            media_type="text/css",
        )

    @app.get(
        "/assets/api-docs/swagger-ui-bundle.js",
        include_in_schema=False,
        response_class=Response,
    )
    def swagger_ui_bundle() -> Response:
        return _asset_response(
            "vendor/swagger-ui-5.32.11/swagger-ui-bundle.js",
            media_type="application/javascript",
        )

    @app.get(
        "/assets/api-docs/swagger-ui-init.js",
        include_in_schema=False,
        response_class=Response,
    )
    def swagger_ui_init() -> Response:
        return _asset_response(
            "api-docs-swagger.js",
            media_type="application/javascript",
        )

    @app.get(
        "/assets/api-docs/redoc.standalone.js",
        include_in_schema=False,
        response_class=Response,
    )
    def redoc_bundle() -> Response:
        return _asset_response(
            "vendor/redoc-2.5.3/redoc.standalone.js",
            media_type="application/javascript",
        )

    @app.get(
        "/assets/api-docs/redoc-init.js",
        include_in_schema=False,
        response_class=Response,
    )
    def redoc_init() -> Response:
        return _asset_response(
            "api-docs-redoc.js",
            media_type="application/javascript",
        )

    @app.get("/api/v1")
    def api_index() -> dict[str, object]:
        return {
            "service": _service_metadata(),
            "links": {
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
            "daemon": _query_daemon(api_client_factory, _daemon_status),
        }

    @app.get("/api/v1/snapshot")
    def snapshot() -> dict[str, object]:
        return {
            **_api_envelope(),
            "snapshot": _query_daemon(api_client_factory, _daemon_snapshot),
        }

    @app.get("/api/v1/recording")
    def recording_status() -> dict[str, object]:
        return {
            **_api_envelope(),
            "recording": _query_recording_daemon(
                api_client_factory,
                lambda client: client.recording_status(),
            ),
        }

    @app.post("/api/v1/recording/start")
    def recording_start() -> dict[str, object]:
        return {
            **_api_envelope(),
            "recording": _query_recording_daemon(
                api_client_factory,
                lambda client: client.recording_start(),
            ),
        }

    @app.post("/api/v1/recording/stop")
    def recording_stop() -> dict[str, object]:
        return {
            **_api_envelope(),
            "recording": _query_recording_daemon(
                api_client_factory,
                lambda client: client.recording_stop(),
            ),
        }

    @app.get("/api/v1/recordings")
    def recordings() -> dict[str, object]:
        return {
            **_api_envelope(),
            "recordings": _query_recording_daemon(
                api_client_factory,
                lambda client: client.recordings_list(),
            ),
        }

    @app.get(
        "/api/v1/recordings/file/{identifier:path}",
        responses={
            200: {
                "content": {"audio/wav": {}},
                "description": "Finalized daemon recording",
            },
        },
    )
    def recording_file(identifier: str) -> StreamingResponse:
        return _recording_file_response(
            recording_file_client_factory,
            identifier,
        )

    @app.get(
        "/api/v1/events",
        responses={
            200: {
                "content": {"text/event-stream": {}},
                "description": "Ordered daemon events",
            },
        },
    )
    def events() -> StreamingResponse:
        return _event_stream_response(event_client_factory)

    @app.get(
        "/api/v1/audio",
        responses={
            200: {
                "content": {"application/octet-stream": {}},
                "description": "Daemon-owned PCMU audio frames",
            },
        },
    )
    def audio() -> StreamingResponse:
        return _audio_stream_response(pcmu_client_factory)

    return app


@cache
def _read_web_asset(name: str) -> str:
    return (
        files(_WEB_ASSET_PACKAGE)
        .joinpath(*name.split("/"))
        .read_text(encoding="utf-8")
    )


def _asset_response(name: str, *, media_type: str) -> Response:
    return Response(
        content=_read_web_asset(name),
        media_type=media_type,
        headers=dict(_WEB_RESPONSE_HEADERS),
    )


def _api_docs_response(name: str) -> HTMLResponse:
    return HTMLResponse(
        content=_read_web_asset(name),
        headers=dict(_API_DOCS_RESPONSE_HEADERS),
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


def _query_recording_daemon(
    api_client_factory: DaemonApiClientFactory,
    query: _DaemonQuery,
) -> dict[str, object]:
    try:
        with api_client_factory() as client:
            return dict(query(client))
    except DaemonRequestError as error:
        status_code, detail = _recording_request_error_response(error)
        logger.warning(
            "web dashboard daemon recording request failed error_type=%s code=%s",
            error.__class__.__name__,
            error.code,
        )
        raise HTTPException(
            status_code=status_code,
            detail=detail,
        ) from None
    except (SDS200Error, OSError) as error:
        logger.warning(
            "web dashboard daemon recording connection failed error_type=%s",
            error.__class__.__name__,
        )
        raise HTTPException(
            status_code=503,
            detail=WEB_DASHBOARD_RECORDING_UNAVAILABLE_DETAIL,
        ) from None


def _recording_request_error_response(
    error: DaemonRequestError,
) -> tuple[int, str]:
    if error.code == "recording_busy":
        return 409, WEB_DASHBOARD_RECORDING_BUSY_DETAIL
    if error.code == "recording_unavailable":
        return 503, WEB_DASHBOARD_RECORDING_UNAVAILABLE_DETAIL
    if error.code == "recording_failed":
        return 503, WEB_DASHBOARD_RECORDING_FAILED_DETAIL
    return 503, WEB_DASHBOARD_RECORDING_FAILED_DETAIL


def _recording_file_response(
    recording_file_client_factory: (
        DaemonRecordingFileClientFactory | None
    ),
    identifier: str,
) -> StreamingResponse:
    if recording_file_client_factory is None:
        raise HTTPException(
            status_code=503,
            detail=WEB_DASHBOARD_RECORDING_UNAVAILABLE_DETAIL,
        )

    try:
        client = recording_file_client_factory()
        download = client.open(identifier)
    except DaemonRecordingFileRequestError as error:
        status_code, detail = _recording_file_error_response(error.status)
        logger.warning(
            "web dashboard recording-file request failed error_type=%s status=%s",
            error.__class__.__name__,
            error.status.name,
        )
        raise HTTPException(status_code=status_code, detail=detail) from None
    except (SDS200Error, OSError) as error:
        logger.warning(
            "web dashboard recording-file connection failed error_type=%s",
            error.__class__.__name__,
        )
        raise HTTPException(
            status_code=503,
            detail=WEB_DASHBOARD_RECORDING_UNAVAILABLE_DETAIL,
        ) from None

    return StreamingResponse(
        content=_iter_recording_file(download),
        media_type="audio/wav",
        headers={
            **_RECORDING_FILE_RESPONSE_HEADERS,
            "Content-Length": str(download.content_length),
        },
    )


def _recording_file_error_response(
    status: RecordingFileResponseStatus,
) -> tuple[int, str]:
    if status is RecordingFileResponseStatus.INVALID_IDENTIFIER:
        return 400, WEB_DASHBOARD_RECORDING_INVALID_IDENTIFIER_DETAIL
    if status is RecordingFileResponseStatus.NOT_FOUND:
        return 404, WEB_DASHBOARD_RECORDING_NOT_FOUND_DETAIL
    if status is RecordingFileResponseStatus.NOT_PLAYABLE:
        return 409, WEB_DASHBOARD_RECORDING_NOT_PLAYABLE_DETAIL
    if status is RecordingFileResponseStatus.UNAVAILABLE:
        return 409, WEB_DASHBOARD_RECORDING_UNAVAILABLE_DETAIL
    return 503, WEB_DASHBOARD_RECORDING_FAILED_DETAIL


def _event_stream_response(
    event_client_factory: DaemonEventClientFactory | None,
) -> StreamingResponse:
    if event_client_factory is None:
        raise HTTPException(
            status_code=503,
            detail=WEB_DASHBOARD_UNAVAILABLE_DETAIL,
        )

    client: DaemonEventClientLike | None = None
    try:
        client = event_client_factory()
        first_event = client.receive()
    except (SDS200Error, OSError) as error:
        if client is not None:
            client.close()
        logger.warning(
            "web dashboard daemon event connection failed error_type=%s",
            error.__class__.__name__,
        )
        raise HTTPException(
            status_code=503,
            detail=WEB_DASHBOARD_UNAVAILABLE_DETAIL,
        ) from None

    return StreamingResponse(
        content=_iter_daemon_events(client, first_event),
        media_type="text/event-stream",
        headers=dict(_EVENT_STREAM_RESPONSE_HEADERS),
    )


def _audio_stream_response(
    pcmu_client_factory: DaemonPcmuClientFactory | None,
) -> StreamingResponse:
    if pcmu_client_factory is None:
        raise HTTPException(
            status_code=503,
            detail=WEB_DASHBOARD_UNAVAILABLE_DETAIL,
        )

    client: DaemonPcmuClientLike | None = None
    try:
        client = pcmu_client_factory()
        client.connect()
    except (SDS200Error, OSError) as error:
        if client is not None:
            client.close()
        logger.warning(
            "web dashboard daemon PCMU connection failed error_type=%s",
            error.__class__.__name__,
        )
        raise HTTPException(
            status_code=503,
            detail=WEB_DASHBOARD_UNAVAILABLE_DETAIL,
        ) from None

    return StreamingResponse(
        content=_iter_daemon_audio(client),
        media_type="application/octet-stream",
        headers=dict(_AUDIO_STREAM_RESPONSE_HEADERS),
    )


async def _iter_recording_file(
    download: DaemonRecordingFileDownload,
) -> AsyncIterator[bytes]:
    try:
        while not download.closed:
            payload = await asyncio.to_thread(download.read, 64 * 1024)
            if not payload:
                return
            yield payload
    except (SDS200Error, OSError, ValueError) as error:
        logger.warning(
            "web dashboard recording-file stream ended error_type=%s",
            error.__class__.__name__,
        )
    finally:
        download.close()


async def _iter_daemon_events(
    client: DaemonEventClientLike,
    first_event: DaemonEvent,
) -> AsyncIterator[bytes]:
    try:
        yield _encode_server_sent_event(first_event)
        while True:
            event = await asyncio.to_thread(client.receive)
            yield _encode_server_sent_event(event)
    except (SDS200Error, OSError) as error:
        logger.warning(
            "web dashboard daemon event stream ended error_type=%s",
            error.__class__.__name__,
        )
    finally:
        client.close()


async def _iter_daemon_audio(
    client: DaemonPcmuClientLike,
) -> AsyncIterator[bytes]:
    try:
        while True:
            delivery = await asyncio.to_thread(client.receive)
            yield encode_pcmu_delivery(
                delivery,
                max_endpoint_bytes=client.max_endpoint_bytes,
                max_frame_bytes=client.max_frame_bytes,
            )
    except (SDS200Error, OSError) as error:
        logger.warning(
            "web dashboard daemon PCMU stream ended error_type=%s",
            error.__class__.__name__,
        )
    finally:
        client.close()


def _encode_server_sent_event(event: DaemonEvent) -> bytes:
    payload = json.dumps(
        event.as_dict(),
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"id: {event.sequence}\ndata: {payload}\n\n".encode()


def _daemon_status(client: DaemonApiClientLike) -> Mapping[str, object]:
    return {
        "hello": dict(client.hello()),
        "snapshot": dict(client.runtime_snapshot()),
    }


def _daemon_snapshot(client: DaemonApiClientLike) -> Mapping[str, object]:
    client.hello()
    return client.runtime_snapshot()


__all__ = [
    "DaemonApiClientContext",
    "DaemonApiClientFactory",
    "DaemonApiClientLike",
    "DaemonEventClientFactory",
    "DaemonEventClientLike",
    "DaemonPcmuClientFactory",
    "DaemonPcmuClientLike",
    "DaemonRecordingFileClientFactory",
    "DaemonRecordingFileClientLike",
    "WEB_DASHBOARD_API_PROTOCOL",
    "WEB_DASHBOARD_API_VERSION",
    "WEB_DASHBOARD_UNAVAILABLE_DETAIL",
    "create_web_dashboard_app",
]
