from __future__ import annotations

import time

from sds200.fallback import FallbackTransport, TransportCandidate
from sds200.reliability import ReconnectPolicy
from sds200.transport import TransportDiagnostic

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


def test_fallback_reports_previous_and_active_endpoints() -> None:
    preferred = FakeTransport("fake://preferred")
    backup = FakeTransport("fake://backup")
    diagnostics: list[TransportDiagnostic] = []
    transport = FallbackTransport(
        (
            TransportCandidate("preferred", preferred.endpoint, lambda: preferred),
            TransportCandidate("backup", backup.endpoint, lambda: backup),
        ),
        retry_interval=0.01,
    )
    transport.set_diagnostic_handler(diagnostics.append)

    transport.start(lambda line: None)
    preferred.set_connected(False)
    deadline = time.monotonic() + 1.0
    while transport.endpoint != backup.endpoint and time.monotonic() < deadline:
        time.sleep(0.005)

    try:
        activated = [
            diagnostic
            for diagnostic in diagnostics
            if diagnostic.kind == "transport_activated"
        ]
        assert activated[-1].endpoint == backup.endpoint
        assert activated[-1].previous_endpoint == preferred.endpoint
        assert transport.statistics["last_switch_from"] == preferred.endpoint
        assert transport.statistics["last_switch_to"] == backup.endpoint
    finally:
        transport.stop()


def test_fallback_stops_after_reconnect_policy_is_exhausted() -> None:
    preferred = FakeTransport("fake://preferred")
    preferred_calls = 0

    def preferred_factory() -> FakeTransport:
        nonlocal preferred_calls
        preferred_calls += 1
        if preferred_calls == 1:
            return preferred
        return FailingStartTransport("fake://preferred")

    diagnostics: list[TransportDiagnostic] = []
    transport = FallbackTransport(
        (
            TransportCandidate("preferred", preferred.endpoint, preferred_factory),
            TransportCandidate(
                "backup",
                "fake://backup",
                lambda: FailingStartTransport("fake://backup"),
            ),
        ),
        retry_interval=0.01,
        reconnect_policy=ReconnectPolicy(
            initial_delay=0.01,
            multiplier=1.0,
            max_delay=0.01,
            max_attempts=1,
        ),
    )
    transport.set_diagnostic_handler(diagnostics.append)

    transport.start(lambda line: None)
    preferred.set_connected(False)
    deadline = time.monotonic() + 1.0
    while (
        transport.statistics["reconnect_exhausted"] != 1
        and time.monotonic() < deadline
    ):
        time.sleep(0.005)

    try:
        assert not transport.connected
        assert transport.statistics["reconnect_attempts"] == 1
        assert transport.statistics["reconnect_exhausted"] == 1
        assert diagnostics[-1].kind == "reconnect_exhausted"
    finally:
        transport.stop()
