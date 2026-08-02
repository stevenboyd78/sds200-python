from __future__ import annotations

import pytest

from sds200.network import UdpTransport
from sds200.network_audio import NetworkAudioTransport
from sds200.socket_utils import normalize_local_ipv4_bind_address

_WILDCARD_ALIASES = (
    "0",
    "0.0",
    "0.0.0",
    "0.0.0.0",
    "00.00.00.00",
    "0x0",
)


@pytest.mark.parametrize("address", [None, "", "   "])
def test_normalize_local_ipv4_bind_address_treats_empty_as_unset(
    address: str | None,
) -> None:
    assert (
        normalize_local_ipv4_bind_address(
            address,
            description="Local test address",
        )
        is None
    )


def test_normalize_local_ipv4_bind_address_preserves_specific_address() -> None:
    assert (
        normalize_local_ipv4_bind_address(
            " 192.0.2.10 ",
            description="Local test address",
        )
        == "192.0.2.10"
    )


@pytest.mark.parametrize("address", _WILDCARD_ALIASES)
def test_normalize_local_ipv4_bind_address_rejects_wildcard_aliases(
    address: str,
) -> None:
    with pytest.raises(
        ValueError,
        match="Local test address must not bind all network interfaces",
    ):
        normalize_local_ipv4_bind_address(
            address,
            description="Local test address",
        )


def test_udp_transport_normalizes_empty_bind_address() -> None:
    transport = UdpTransport("192.0.2.25", local_host="")
    assert transport.local_host is None


def test_network_audio_normalizes_empty_bind_address() -> None:
    transport = NetworkAudioTransport("192.0.2.25", local_host="")
    assert transport.local_host is None


@pytest.mark.parametrize("address", _WILDCARD_ALIASES)
def test_udp_transport_rejects_wildcard_bind_aliases(address: str) -> None:
    with pytest.raises(
        ValueError,
        match="Local UDP address must not bind all network interfaces",
    ):
        UdpTransport("192.0.2.25", local_host=address)


@pytest.mark.parametrize("address", _WILDCARD_ALIASES)
def test_network_audio_rejects_wildcard_bind_aliases(address: str) -> None:
    with pytest.raises(
        ValueError,
        match="Local RTP address must not bind all network interfaces",
    ):
        NetworkAudioTransport("192.0.2.25", local_host=address)
