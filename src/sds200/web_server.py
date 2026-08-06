"""Loopback-only server adapter for the optional web dashboard."""

from __future__ import annotations

from ipaddress import ip_address
from typing import Any, Protocol, cast

WEB_DASHBOARD_DEFAULT_HOST = "127.0.0.1"
WEB_DASHBOARD_DEFAULT_PORT = 8000
WEB_DASHBOARD_INSTALL_ERROR = (
    "Web dashboard support is not installed; install it with: "
    'python -m pip install "sds200[web]"'
)


class WebDashboardServer(Protocol):
    """Minimum synchronous server lifecycle used by the CLI."""

    def run(self) -> None:
        """Run until orderly shutdown."""


class WebDashboardServerFactory(Protocol):
    """Construct one configured web server."""

    def __call__(
        self,
        app: object,
        *,
        host: str,
        port: int,
        access_log: bool,
    ) -> WebDashboardServer:
        """Return one configured server."""


def normalize_web_dashboard_host(host: str) -> str:
    """Require localhost or an explicit loopback IP address."""

    if not isinstance(host, str):
        raise TypeError("Web dashboard listen address must be a string.")

    normalized = host.strip()

    if not normalized:
        raise ValueError(
            "Web dashboard listen address must not be empty."
        )

    if normalized.lower() == "localhost":
        return WEB_DASHBOARD_DEFAULT_HOST

    try:
        address = ip_address(normalized)
    except ValueError as error:
        raise ValueError(
            "Web dashboard listen address must be localhost or a "
            "loopback IP address."
        ) from error

    if not address.is_loopback:
        raise ValueError(
            "Web dashboard listen address must be localhost or a "
            "loopback IP address."
        )

    return address.compressed


def normalize_web_dashboard_port(port: int) -> int:
    """Validate one nonzero TCP listen port."""

    if type(port) is not int:
        raise TypeError("Web dashboard listen port must be an integer.")

    if not 1 <= port <= 65535:
        raise ValueError(
            "Web dashboard listen port must be between 1 and 65535."
        )

    return port


def run_web_dashboard_server(
    app: object,
    *,
    host: str = WEB_DASHBOARD_DEFAULT_HOST,
    port: int = WEB_DASHBOARD_DEFAULT_PORT,
    access_log: bool = True,
    server_factory: WebDashboardServerFactory | None = None,
) -> int:
    """Run one loopback-only web server until shutdown."""

    normalized_host = normalize_web_dashboard_host(host)
    normalized_port = normalize_web_dashboard_port(port)

    if type(access_log) is not bool:
        raise TypeError("Web dashboard access-log setting must be boolean.")

    selected_factory = server_factory or _default_server_factory

    if not callable(selected_factory):
        raise TypeError("Web dashboard server factory must be callable.")

    server = selected_factory(
        app,
        host=normalized_host,
        port=normalized_port,
        access_log=access_log,
    )
    server.run()
    return 0


def _default_server_factory(
    app: object,
    *,
    host: str,
    port: int,
    access_log: bool,
) -> WebDashboardServer:
    try:
        import uvicorn
    except ModuleNotFoundError as error:
        if error.name == "uvicorn":
            raise ValueError(
                WEB_DASHBOARD_INSTALL_ERROR
            ) from error
        raise

    config = uvicorn.Config(
        cast(Any, app),
        host=host,
        port=port,
        access_log=access_log,
        log_config=None,
        proxy_headers=False,
        server_header=False,
    )
    return uvicorn.Server(config)


__all__ = [
    "WEB_DASHBOARD_DEFAULT_HOST",
    "WEB_DASHBOARD_DEFAULT_PORT",
    "WEB_DASHBOARD_INSTALL_ERROR",
    "WebDashboardServer",
    "WebDashboardServerFactory",
    "normalize_web_dashboard_host",
    "normalize_web_dashboard_port",
    "run_web_dashboard_server",
]
