from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import pytest

from sds200.fallback import (
    FallbackTransport,
    PreferredRecoveryPolicy,
    TransportCandidate,
)
from sds200.reliability import ReconnectPolicy
from sds200.replay import ReplayTransport
from sds200.transport import TransportDiagnostic

from .fakes import (
    FailingStartTransport,
    FailingWriteTransport,
    FakeTransport,
    ModelProbeTransport,
)


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


def _wait_until(predicate: Callable[[], bool], *, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert predicate()


def test_preferred_recovery_policy_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="probe interval"):
        PreferredRecoveryPolicy(probe_interval=0)
    with pytest.raises(ValueError, match="probe timeout"):
        PreferredRecoveryPolicy(probe_timeout=0)
    with pytest.raises(ValueError, match="stability window"):
        PreferredRecoveryPolicy(stability_window=-1)
    with pytest.raises(ValueError, match="cooldown"):
        PreferredRecoveryPolicy(cooldown=-1)


def test_fallback_recovers_preferred_transport_after_validated_probe() -> None:
    preferred = FakeTransport("fake://preferred")
    recovered = ModelProbeTransport("fake://preferred")
    backup = FakeTransport("fake://backup")
    preferred_calls = 0
    diagnostics: list[TransportDiagnostic] = []

    def preferred_factory() -> FakeTransport:
        nonlocal preferred_calls
        preferred_calls += 1
        return preferred if preferred_calls == 1 else recovered

    transport = FallbackTransport(
        (
            TransportCandidate("preferred", preferred.endpoint, preferred_factory),
            TransportCandidate("backup", backup.endpoint, lambda: backup),
        ),
        retry_interval=0.01,
        preferred_recovery_policy=PreferredRecoveryPolicy(
            probe_interval=0.01,
            probe_timeout=0.1,
            stability_window=0,
            cooldown=0,
        ),
        recovery_probe_validator=lambda line: line == "MDL,SDS200",
    )
    transport.set_diagnostic_handler(diagnostics.append)

    transport.start(lambda line: None)
    preferred.set_connected(False)
    _wait_until(lambda: transport.endpoint == backup.endpoint)
    _wait_until(lambda: transport.endpoint == recovered.endpoint and recovered.connected)

    try:
        assert transport.active_candidate == "preferred"
        assert recovered.writes == ["MDL"]
        assert transport.statistics["preferred_recovery_probe_attempts"] == 1
        assert transport.statistics["preferred_recoveries"] == 1
        assert any(
            item.kind == "preferred_recovery_succeeded" for item in diagnostics
        )
    finally:
        transport.stop()


def test_failed_recovery_probe_keeps_working_fallback_active() -> None:
    preferred = FakeTransport("fake://preferred")
    backup = FakeTransport("fake://backup")
    preferred_calls = 0

    def preferred_factory() -> FakeTransport:
        nonlocal preferred_calls
        preferred_calls += 1
        if preferred_calls == 1:
            return preferred
        return ModelProbeTransport("fake://preferred", model="SDS100")

    transport = FallbackTransport(
        (
            TransportCandidate("preferred", preferred.endpoint, preferred_factory),
            TransportCandidate("backup", backup.endpoint, lambda: backup),
        ),
        retry_interval=0.01,
        preferred_recovery_policy=PreferredRecoveryPolicy(
            probe_interval=0.01,
            probe_timeout=0.03,
            stability_window=0,
            cooldown=0,
        ),
        recovery_probe_validator=lambda line: line == "MDL,SDS200",
    )

    transport.start(lambda line: None)
    preferred.set_connected(False)
    _wait_until(lambda: transport.endpoint == backup.endpoint)
    _wait_until(
        lambda: transport.statistics["preferred_recovery_probe_failures"] >= 1
    )

    try:
        assert transport.endpoint == backup.endpoint
        assert transport.statistics["preferred_recoveries"] == 0
    finally:
        transport.stop()


def test_recovery_guard_defers_promotion_until_scanner_is_idle() -> None:
    preferred = FakeTransport("fake://preferred")
    recovered = ModelProbeTransport("fake://preferred")
    backup = FakeTransport("fake://backup")
    preferred_calls = 0
    idle = False

    def preferred_factory() -> FakeTransport:
        nonlocal preferred_calls
        preferred_calls += 1
        return preferred if preferred_calls == 1 else recovered

    transport = FallbackTransport(
        (
            TransportCandidate("preferred", preferred.endpoint, preferred_factory),
            TransportCandidate("backup", backup.endpoint, lambda: backup),
        ),
        retry_interval=0.01,
        preferred_recovery_policy=PreferredRecoveryPolicy(
            probe_interval=0.01,
            probe_timeout=0.1,
            stability_window=0,
            cooldown=0,
        ),
    )
    transport.set_recovery_guard(lambda: idle)

    transport.start(lambda line: None)
    preferred.set_connected(False)
    _wait_until(lambda: transport.endpoint == backup.endpoint)
    _wait_until(lambda: transport.statistics["preferred_recovery_deferred"] >= 1)
    assert transport.endpoint == backup.endpoint

    idle = True
    _wait_until(lambda: transport.active_candidate == "preferred")
    try:
        assert transport.statistics["preferred_recoveries"] == 1
    finally:
        transport.stop()


def test_recovery_stability_window_rejects_flapping_candidate() -> None:
    preferred = FakeTransport("fake://preferred")
    backup = FakeTransport("fake://backup")
    preferred_calls = 0

    def preferred_factory() -> FakeTransport:
        nonlocal preferred_calls
        preferred_calls += 1
        if preferred_calls == 1:
            return preferred
        return ModelProbeTransport(
            "fake://preferred",
            disconnect_after_probe=True,
        )

    transport = FallbackTransport(
        (
            TransportCandidate("preferred", preferred.endpoint, preferred_factory),
            TransportCandidate("backup", backup.endpoint, lambda: backup),
        ),
        retry_interval=0.01,
        preferred_recovery_policy=PreferredRecoveryPolicy(
            probe_interval=0.01,
            probe_timeout=0.1,
            stability_window=0.03,
            cooldown=0,
        ),
    )

    transport.start(lambda line: None)
    preferred.set_connected(False)
    _wait_until(lambda: transport.endpoint == backup.endpoint)
    _wait_until(
        lambda: transport.statistics["preferred_recovery_probe_failures"] >= 1
    )

    try:
        assert transport.endpoint == backup.endpoint
        assert transport.statistics["preferred_recoveries"] == 0
    finally:
        transport.stop()


def test_failover_request_cancels_in_progress_preferred_recovery() -> None:
    initial = FakeTransport("fake://preferred-initial")
    probe = ModelProbeTransport("fake://preferred-probe")
    restored = FakeTransport("fake://preferred-restored")
    backup = FakeTransport("fake://backup")
    preferred_calls = 0
    diagnostics: list[TransportDiagnostic] = []

    def preferred_factory() -> FakeTransport:
        nonlocal preferred_calls
        preferred_calls += 1
        if preferred_calls == 1:
            return initial
        if preferred_calls == 2:
            return probe
        return restored

    transport = FallbackTransport(
        (
            TransportCandidate("preferred", initial.endpoint, preferred_factory),
            TransportCandidate("backup", backup.endpoint, lambda: backup),
        ),
        retry_interval=0.01,
        preferred_recovery_policy=PreferredRecoveryPolicy(
            probe_interval=0.01,
            probe_timeout=0.1,
            stability_window=0.1,
            cooldown=0,
        ),
    )
    transport.set_diagnostic_handler(diagnostics.append)

    transport.start(lambda line: None)
    initial.set_connected(False)
    _wait_until(lambda: transport.active_candidate == "backup")
    _wait_until(lambda: probe.probes == 1)
    backup.set_connected(False)
    _wait_until(lambda: transport.endpoint == restored.endpoint)

    try:
        assert transport.statistics["preferred_recoveries"] == 0
        assert not any(
            item.kind == "preferred_recovery_succeeded" for item in diagnostics
        )
    finally:
        transport.stop()


def test_preferred_recovery_is_disabled_by_default() -> None:
    preferred = FakeTransport("fake://preferred")
    backup = FakeTransport("fake://backup")
    preferred_calls = 0

    def preferred_factory() -> FakeTransport:
        nonlocal preferred_calls
        preferred_calls += 1
        return preferred

    transport = FallbackTransport(
        (
            TransportCandidate("preferred", preferred.endpoint, preferred_factory),
            TransportCandidate("backup", backup.endpoint, lambda: backup),
        ),
        retry_interval=0.01,
    )

    transport.start(lambda line: None)
    preferred.set_connected(False)
    _wait_until(lambda: transport.endpoint == backup.endpoint)
    time.sleep(0.05)

    try:
        assert preferred_calls == 1
        assert transport.statistics["preferred_recovery_enabled"] is False
        assert transport.statistics["preferred_recovery_probe_attempts"] == 0
    finally:
        transport.stop()


@pytest.mark.parametrize(
    ("preferred_name", "backup_name", "fixture_name"),
    (
        ("serial", "network", "sds200-usb-recovery-probe.jsonl"),
        ("network", "serial", "sds200-network-recovery-probe.jsonl"),
    ),
)
def test_preferred_recovery_uses_replay_validated_scanner_probe(
    preferred_name: str,
    backup_name: str,
    fixture_name: str,
) -> None:
    initial = FakeTransport(f"fake://{preferred_name}")
    backup = FakeTransport(f"fake://{backup_name}")
    fixture = Path(__file__).parent / "fixtures" / "replay" / fixture_name
    preferred_calls = 0

    def preferred_factory() -> FakeTransport | ReplayTransport:
        nonlocal preferred_calls
        preferred_calls += 1
        if preferred_calls == 1:
            return initial
        return ReplayTransport.from_file(fixture)

    transport = FallbackTransport(
        (
            TransportCandidate(preferred_name, initial.endpoint, preferred_factory),
            TransportCandidate(backup_name, backup.endpoint, lambda: backup),
        ),
        retry_interval=0.01,
        preferred_recovery_policy=PreferredRecoveryPolicy(
            probe_interval=0.01,
            probe_timeout=0.1,
            stability_window=0,
            cooldown=0,
        ),
        recovery_probe_validator=lambda line: line == "MDL,SDS200",
    )

    transport.start(lambda line: None)
    initial.set_connected(False)
    _wait_until(lambda: transport.active_candidate == backup_name)
    _wait_until(lambda: transport.active_candidate == preferred_name)

    try:
        assert transport.statistics["preferred_recoveries"] == 1
        assert transport.statistics["preferred_recovery_probe_failures"] == 0
    finally:
        transport.stop()


def test_stop_interrupts_preferred_recovery_probe() -> None:
    preferred = FakeTransport("fake://preferred")
    backup = FakeTransport("fake://backup")
    preferred_calls = 0

    def preferred_factory() -> FakeTransport:
        nonlocal preferred_calls
        preferred_calls += 1
        if preferred_calls == 1:
            return preferred
        return ModelProbeTransport("fake://preferred", model=None)

    transport = FallbackTransport(
        (
            TransportCandidate("preferred", preferred.endpoint, preferred_factory),
            TransportCandidate("backup", backup.endpoint, lambda: backup),
        ),
        retry_interval=0.01,
        preferred_recovery_policy=PreferredRecoveryPolicy(
            probe_interval=0.01,
            probe_timeout=1.0,
            stability_window=0,
            cooldown=0,
        ),
    )

    transport.start(lambda line: None)
    preferred.set_connected(False)
    _wait_until(lambda: transport.active_candidate == "backup")
    _wait_until(
        lambda: transport.statistics["preferred_recovery_probe_attempts"] == 1
    )

    started = time.monotonic()
    transport.stop()

    assert time.monotonic() - started < 0.5
