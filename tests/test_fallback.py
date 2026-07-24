from __future__ import annotations

import time

from sds200.fallback import FallbackTransport, TransportCandidate

from .fakes import FailingStartTransport, FailingWriteTransport, FakeTransport


def test_fallback_uses_second_candidate_when_preferred_fails() -> None:
    backup = FakeTransport("fake://backup")
    transport = FallbackTransport(
        (
            TransportCandidate(
                "preferred",
                "fake://preferred",
                lambda: FailingStartTransport("fake://preferred"),
            ),
            TransportCandidate("backup", backup.endpoint, lambda: backup),
        )
    )

    transport.start(lambda line: None)
    try:
        assert transport.connected
        assert transport.active_candidate == "backup"
        assert transport.endpoint == "fake://backup"
        assert transport.statistics["activation_failures"] == 1
    finally:
        transport.stop()


def test_fallback_switches_after_live_disconnect() -> None:
    preferred = FakeTransport("fake://preferred")
    backup = FakeTransport("fake://backup")
    transport = FallbackTransport(
        (
            TransportCandidate("preferred", preferred.endpoint, lambda: preferred),
            TransportCandidate("backup", backup.endpoint, lambda: backup),
        ),
        retry_interval=0.01,
    )

    transport.start(lambda line: None)
    preferred.set_connected(False)
    deadline = time.monotonic() + 1.0
    while transport.endpoint != backup.endpoint and time.monotonic() < deadline:
        time.sleep(0.005)

    try:
        assert transport.endpoint == backup.endpoint
        assert transport.statistics["failovers"] == 1
    finally:
        transport.stop()


def test_fallback_retries_command_once_after_switch() -> None:
    preferred = FailingWriteTransport("fake://preferred")
    backup = FakeTransport("fake://backup")
    transport = FallbackTransport(
        (
            TransportCandidate("preferred", preferred.endpoint, lambda: preferred),
            TransportCandidate("backup", backup.endpoint, lambda: backup),
        ),
        retry_interval=0.01,
        failover_timeout=1.0,
    )

    transport.start(lambda line: None)
    try:
        transport.write_command("MDL")
        assert backup.writes == ["MDL"]
        assert transport.statistics["write_retries"] == 1
    finally:
        transport.stop()
