from __future__ import annotations

import socket
from collections.abc import Callable

LocalAddressResolver = Callable[[str, int], str]


def normalize_local_ipv4_bind_address(
    address: str | None,
    *,
    description: str,
) -> str | None:
    """Normalize an optional local IPv4 bind address and reject wildcard aliases."""
    if address is None:
        return None

    normalized = address.strip()
    if not normalized:
        return None

    try:
        packed_address = socket.inet_aton(normalized)
    except OSError:
        return normalized

    if packed_address == b"\x00\x00\x00\x00":
        raise ValueError(f"{description} must not bind all network interfaces.")
    return normalized


def resolve_local_ipv4_address(host: str, port: int) -> str:
    """Return the local IPv4 address selected by the route to a remote endpoint."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect((host, port))
        raw_address = probe.getsockname()[0]
    if not isinstance(raw_address, str) or raw_address in {"", "0.0.0.0"}:
        raise OSError(f"Could not determine a local IPv4 route to {host}:{port}.")
    return raw_address
