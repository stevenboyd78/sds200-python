from __future__ import annotations

import pytest

from sds200.web_server import (
    WEB_DASHBOARD_DEFAULT_HOST,
    WEB_DASHBOARD_DEFAULT_PORT,
    normalize_web_dashboard_host,
    normalize_web_dashboard_port,
    run_web_dashboard_server,
)


class FakeServer:
    def __init__(self) -> None:
        self.run_calls = 0

    def run(self) -> None:
        self.run_calls += 1


class FakeServerFactory:
    def __init__(self) -> None:
        self.calls: list[tuple[object, str, int, bool]] = []
        self.server = FakeServer()

    def __call__(
        self,
        app: object,
        *,
        host: str,
        port: int,
        access_log: bool,
    ) -> FakeServer:
        self.calls.append((app, host, port, access_log))
        return self.server


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("localhost", "127.0.0.1"),
        ("LOCALHOST", "127.0.0.1"),
        ("127.0.0.1", "127.0.0.1"),
        ("127.0.0.25", "127.0.0.25"),
        ("::1", "::1"),
    ],
)
def test_normalize_web_dashboard_host_accepts_loopback(
    value: str,
    expected: str,
) -> None:
    assert normalize_web_dashboard_host(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "0.0.0.0",
        "::",
        "192.168.0.25",
        "scanner.local",
    ],
)
def test_normalize_web_dashboard_host_rejects_remote_exposure(
    value: str,
) -> None:
    with pytest.raises(
        ValueError,
        match="localhost or a loopback IP address|must not be empty",
    ):
        normalize_web_dashboard_host(value)


def test_normalize_web_dashboard_port_validates_range() -> None:
    assert normalize_web_dashboard_port(1) == 1
    assert normalize_web_dashboard_port(65535) == 65535

    with pytest.raises(ValueError, match="between 1 and 65535"):
        normalize_web_dashboard_port(0)

    with pytest.raises(ValueError, match="between 1 and 65535"):
        normalize_web_dashboard_port(65536)

    with pytest.raises(TypeError, match="must be an integer"):
        normalize_web_dashboard_port(True)  # type: ignore[arg-type]


def test_run_web_dashboard_server_uses_normalized_configuration() -> None:
    app = object()
    factory = FakeServerFactory()

    result = run_web_dashboard_server(
        app,
        host="localhost",
        port=8123,
        access_log=False,
        server_factory=factory,
    )

    assert result == 0
    assert factory.calls == [
        (app, "127.0.0.1", 8123, False),
    ]
    assert factory.server.run_calls == 1


def test_run_web_dashboard_server_defaults_are_loopback_only() -> None:
    app = object()
    factory = FakeServerFactory()

    assert run_web_dashboard_server(
        app,
        server_factory=factory,
    ) == 0

    assert factory.calls == [
        (
            app,
            WEB_DASHBOARD_DEFAULT_HOST,
            WEB_DASHBOARD_DEFAULT_PORT,
            True,
        )
    ]


def test_run_web_dashboard_server_requires_callable_factory() -> None:
    with pytest.raises(
        TypeError,
        match="server factory must be callable",
    ):
        run_web_dashboard_server(
            object(),
            server_factory=object(),  # type: ignore[arg-type]
        )
