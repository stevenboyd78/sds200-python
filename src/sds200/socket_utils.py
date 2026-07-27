from __future__ import annotations

import socket
from collections.abc import Callable

LocalAddressResolver = Callable[[str, int], str]


def resolve_local_ipv4_address(host: str, port: int) -> str:
    """Return the local IPv4 address selected by the route to a remote endpoint."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect((host, port))
        raw_address = probe.getsockname()[0]
    if not isinstance(raw_address, str) or raw_address in {"", "0.0.0.0"}:
        raise OSError(f"Could not determine a local IPv4 route to {host}:{port}.")
    return raw_address
