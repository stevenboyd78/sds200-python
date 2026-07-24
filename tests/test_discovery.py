from __future__ import annotations

import errno
import queue
import threading
from ipaddress import IPv4Network
from pathlib import Path

import pytest

from sds200.discovery import (
    discover_network_scanners,
    local_ipv4_networks,
)


class DiscoveryHarness:
    def __init__(
        self,
        responding_hosts: set[str] | None = None,
        *,
        responses: dict[str, bytes] | None = None,
        refuse_once_hosts: set[str] | None = None,
    ) -> None:
        self.responding_hosts = responding_hosts or set()
        self.responses = responses or {}
        self.refuse_once_hosts = refuse_once_hosts or set()
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self.closed_count = 0
        self._lock = threading.Lock()

    def socket_factory(self, family: int, socket_type: int) -> FakeDiscoverySocket:
        del family, socket_type
        return FakeDiscoverySocket(self)

    def record_send(self, data: bytes, address: tuple[str, int]) -> None:
        with self._lock:
            self.sent.append((data, address))

    def record_close(self) -> None:
        with self._lock:
            self.closed_count += 1


class FakeDiscoverySocket:
    def __init__(self, harness: DiscoveryHarness) -> None:
        self.harness = harness
        self.incoming: queue.Queue[tuple[bytes, tuple[str, int]]] = queue.Queue()
        self.timeout: float | None = None
        self.bound: tuple[str, int] | None = None
        self.target_host: str | None = None
        self.refusal_raised = False

    def settimeout(self, value: float | None) -> None:
        self.timeout = value

    def bind(self, address: tuple[str, int]) -> None:
        self.bound = address

    def sendto(self, data: bytes, address: tuple[str, int]) -> int:
        self.target_host = address[0]
        self.harness.record_send(data, address)
        if address[0] in self.harness.responding_hosts:
            self.incoming.put((b"MDL,SDS200\r", address))
        return len(data)

    def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:
        del size
        if (
            self.target_host in self.harness.refuse_once_hosts
            and not self.refusal_raised
        ):
            self.refusal_raised = True
            raise ConnectionRefusedError(
                errno.ECONNREFUSED,
                "UDP destination port unreachable",
            )
        try:
            return self.incoming.get(timeout=self.timeout or 0.001)
        except queue.Empty as exc:
            raise TimeoutError from exc

    def close(self) -> None:
        self.harness.record_close()


def test_local_ipv4_networks_reads_linux_routes(tmp_path: Path) -> None:
    route = tmp_path / "route"
    route.write_text(
        "Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\n"
        "eth0 0002A8C0 00000000 0001 0 0 0 00FFFFFF 0 0 0\n"
        "eth0 00000000 0102A8C0 0003 0 0 100 00000000 0 0 0\n",
        encoding="ascii",
    )

    assert local_ipv4_networks(route) == (IPv4Network("192.168.2.0/24"),)


def test_network_discovery_probes_hosts_and_parses_model() -> None:
    harness = DiscoveryHarness({"192.0.2.2"})

    scanners = discover_network_scanners(
        ["192.0.2.0/30"],
        timeout=0.02,
        workers=2,
        socket_factory=harness.socket_factory,
    )

    assert [scanner.host for scanner in scanners] == ["192.0.2.2"]
    assert scanners[0].model == "SDS200"
    assert scanners[0].endpoint == "udp://192.0.2.2:50536"
    assert sorted(harness.sent, key=lambda item: item[1][0]) == [
        (b"MDL\r", ("192.0.2.1", 50536)),
        (b"MDL\r", ("192.0.2.2", 50536)),
    ]
    assert harness.closed_count == 2


def test_network_discovery_enforces_host_safety_limit() -> None:
    with pytest.raises(ValueError, match="exceeding --max-hosts"):
        discover_network_scanners(["192.0.2.0/24"], max_hosts=10)


def test_discovery_rejects_non_positive_workers() -> None:
    with pytest.raises(ValueError, match="workers"):
        discover_network_scanners(["192.0.2.1/32"], workers=0)


def test_large_network_finds_high_address() -> None:
    harness = DiscoveryHarness({"192.168.0.251"})

    scanners = discover_network_scanners(
        ["192.168.0.0/24"],
        timeout=0.005,
        workers=16,
        socket_factory=harness.socket_factory,
    )

    assert [scanner.host for scanner in scanners] == ["192.168.0.251"]
    assert len(harness.sent) == 254
    assert harness.closed_count == 254


def test_discovery_isolates_refusal_from_valid_scanner_reply() -> None:
    harness = DiscoveryHarness(
        {"192.0.2.2"},
        refuse_once_hosts={"192.0.2.1"},
    )

    scanners = discover_network_scanners(
        ["192.0.2.0/30"],
        timeout=0.02,
        workers=2,
        socket_factory=harness.socket_factory,
    )

    assert [scanner.host for scanner in scanners] == ["192.0.2.2"]


def test_network_discovery_ignores_handheld_model_response() -> None:
    harness = DiscoveryHarness(
        responses={"192.0.2.2": b"MDL,SDS150GBT\r"},
    )

    scanners = discover_network_scanners(
        ["192.0.2.2/32"],
        timeout=0.02,
        workers=1,
        socket_factory=harness.socket_factory,
    )

    assert scanners == []
