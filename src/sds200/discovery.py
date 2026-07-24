from __future__ import annotations

import errno
import logging
import socket
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network, ip_network
from pathlib import Path
from time import monotonic
from typing import Protocol, cast

from .network import DEFAULT_UDP_PORT
from .scanner import ScannerModel, capabilities_for_model, normalize_model_name

DEFAULT_DISCOVERY_TIMEOUT = 0.6
DEFAULT_DISCOVERY_WORKERS = 32
DEFAULT_MAX_DISCOVERY_HOSTS = 4096
_PROC_NET_ROUTE = Path("/proc/net/route")

logger = logging.getLogger(__name__)

_TRANSIENT_RECEIVE_ERRNOS = frozenset(
    {
        errno.ECONNREFUSED,
        errno.EHOSTUNREACH,
        errno.ENETUNREACH,
    }
)


class DiscoverySocketLike(Protocol):
    def settimeout(self, value: float | None) -> None: ...
    def bind(self, address: tuple[str, int]) -> None: ...
    def sendto(self, data: bytes, address: tuple[str, int]) -> int: ...
    def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]: ...
    def close(self) -> None: ...


DiscoverySocketFactory = Callable[[int, int], DiscoverySocketLike]


def default_discovery_socket_factory(
    family: int,
    socket_type: int,
) -> DiscoverySocketLike:
    return cast(DiscoverySocketLike, socket.socket(family, socket_type))


@dataclass(frozen=True, slots=True)
class NetworkScanner:
    host: str
    port: int
    model: ScannerModel
    latency_ms: float

    @property
    def endpoint(self) -> str:
        return f"udp://{self.host}:{self.port}"


def _little_endian_ipv4(raw: str) -> IPv4Address:
    value = int(raw, 16)
    return IPv4Address(value.to_bytes(4, byteorder="little"))


def local_ipv4_networks(
    route_path: Path = _PROC_NET_ROUTE,
) -> tuple[IPv4Network, ...]:
    """Return directly connected IPv4 networks from Linux's routing table."""
    try:
        lines = route_path.read_text(encoding="ascii").splitlines()
    except OSError:
        return ()

    networks: set[IPv4Network] = set()
    for line in lines[1:]:
        fields = line.split()
        if len(fields) < 8:
            continue
        interface, destination_hex, _, flags_hex, _, _, _, mask_hex = fields[:8]
        if interface == "lo":
            continue
        try:
            flags = int(flags_hex, 16)
            destination = _little_endian_ipv4(destination_hex)
            mask = _little_endian_ipv4(mask_hex)
            network = IPv4Network(f"{destination}/{mask}", strict=False)
        except (ValueError, TypeError):
            continue
        if not flags & 0x1 or network.prefixlen == 0 or network.is_loopback:
            continue
        networks.add(network)
    return tuple(
        sorted(networks, key=lambda item: (int(item.network_address), item.prefixlen))
    )


def resolve_discovery_networks(
    networks: Iterable[str | IPv4Network] | None,
) -> tuple[IPv4Network, ...]:
    if networks is None:
        detected = local_ipv4_networks()
        if not detected:
            raise ValueError(
                "Could not determine a local IPv4 network; supply --network CIDR."
            )
        return detected

    resolved: set[IPv4Network] = set()
    for value in networks:
        parsed = (
            value
            if isinstance(value, IPv4Network)
            else ip_network(value, strict=False)
        )
        if not isinstance(parsed, IPv4Network):
            raise ValueError(f"Only IPv4 discovery networks are supported: {value}")
        resolved.add(parsed)
    if not resolved:
        raise ValueError("At least one discovery network is required.")
    return tuple(
        sorted(resolved, key=lambda item: (int(item.network_address), item.prefixlen))
    )


def _decode_model_response(data: bytes) -> ScannerModel | None:
    response = data.decode("utf-8", errors="replace").strip("\x00\r\n ")
    if not response.upper().startswith("MDL,"):
        return None
    reported_model = response.split(",", 1)[1].strip()
    model = normalize_model_name(reported_model)
    if model is None or not capabilities_for_model(model).network_control:
        return None
    return model


def _probe_network_host(
    address: IPv4Address,
    *,
    port: int,
    timeout: float,
    bind_address: str,
    socket_factory: DiscoverySocketFactory,
) -> NetworkScanner | None:
    """Probe one host using an isolated UDP socket."""
    host = str(address)
    udp_socket = socket_factory(socket.AF_INET, socket.SOCK_DGRAM)

    with closing(udp_socket):
        try:
            udp_socket.bind((bind_address, 0))
            udp_socket.settimeout(timeout)
            started_at = monotonic()
            sent = udp_socket.sendto(b"MDL\r", (host, port))
        except OSError:
            return None

        if sent != 4:
            return None

        deadline = started_at + timeout
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                return None
            udp_socket.settimeout(remaining)

            try:
                data, source = udp_socket.recvfrom(4096)
            except TimeoutError:
                return None
            except (ConnectionRefusedError, ConnectionResetError) as exc:
                logger.debug("Ignoring UDP discovery refusal from %s: %s", host, exc)
                continue
            except OSError as exc:
                if exc.errno in _TRANSIENT_RECEIVE_ERRNOS:
                    logger.debug(
                        "Ignoring transient UDP discovery error from %s: %s",
                        host,
                        exc,
                    )
                    continue
                return None

            received_at = monotonic()
            source_host, source_port = source
            if source_host != host:
                continue

            model = _decode_model_response(data)
            if model is None:
                continue

            return NetworkScanner(
                host=host,
                port=source_port or port,
                model=model,
                latency_ms=max(0.0, (received_at - started_at) * 1000.0),
            )


def discover_network_scanners(
    networks: Iterable[str | IPv4Network] | None = None,
    *,
    port: int = DEFAULT_UDP_PORT,
    timeout: float = DEFAULT_DISCOVERY_TIMEOUT,
    workers: int = DEFAULT_DISCOVERY_WORKERS,
    max_hosts: int = DEFAULT_MAX_DISCOVERY_HOSTS,
    bind_address: str = "",
    socket_factory: DiscoverySocketFactory = default_discovery_socket_factory,
) -> list[NetworkScanner]:
    """Actively probe IPv4 hosts with the harmless ``MDL`` command.

    Each target uses an isolated UDP socket. Bounded parallelism prevents
    large CIDR scans from flooding the neighbour table while ensuring that
    ICMP errors from ordinary hosts cannot hide a valid network-capable SDS-series response.
    """
    if not 1 <= port <= 65535:
        raise ValueError("Discovery UDP port must be between 1 and 65535.")
    if timeout <= 0:
        raise ValueError("Discovery timeout must be greater than zero.")
    if workers <= 0:
        raise ValueError("Discovery workers must be greater than zero.")
    if max_hosts <= 0:
        raise ValueError("Maximum discovery hosts must be greater than zero.")

    resolved_networks = resolve_discovery_networks(networks)
    hosts = tuple(
        sorted(
            {address for network in resolved_networks for address in network.hosts()},
            key=int,
        )
    )
    if len(hosts) > max_hosts:
        raise ValueError(
            f"Discovery would probe {len(hosts)} hosts, exceeding --max-hosts "
            f"{max_hosts}. Narrow the CIDR or raise the limit explicitly."
        )
    if not hosts:
        return []

    def probe(address: IPv4Address) -> NetworkScanner | None:
        return _probe_network_host(
            address,
            port=port,
            timeout=timeout,
            bind_address=bind_address,
            socket_factory=socket_factory,
        )

    worker_count = min(workers, len(hosts))
    with ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix="sds200-discovery",
    ) as executor:
        results = tuple(executor.map(probe, hosts))

    return sorted(
        (scanner for scanner in results if scanner is not None),
        key=lambda scanner: IPv4Address(scanner.host),
    )
