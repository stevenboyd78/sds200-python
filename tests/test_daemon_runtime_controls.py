from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import replace

import pytest

from sds200 import (
    AudioFanoutSession,
    AudioStream,
    DaemonControlBusyError,
    DaemonControlOperation,
    DaemonControlResult,
    DaemonControlUnavailableError,
    DaemonRuntime,
    DaemonRuntimeState,
    PcmSinkRouter,
    RadioStateSnapshot,
    UnsupportedScannerFeatureError,
)
from sds200.exceptions import CommandTimeoutError

from .fakes import FakeAudioTransport


class FakeRadioState:
    def __init__(self) -> None:
        self._snapshot = RadioStateSnapshot(
            system="Metro",
            system_hold="On",
            department="Dispatch",
            department_hold="On",
            site="Metro Site",
            site_hold="On",
            channel="Primary",
            channel_hold="On",
        )

    @property
    def snapshot(self) -> RadioStateSnapshot:
        return self._snapshot

    def set_hold(self, scope: str, value: str | None) -> RadioStateSnapshot:
        field = f"{scope}_hold"
        self._snapshot = replace(self._snapshot, **{field: value})
        return self._snapshot


class FakeControlScanner:
    def __init__(
        self,
        order: list[str],
        *,
        fail_operation: str | None = None,
        block_operation: str | None = None,
        supports_bounded_reconnect: bool = True,
    ) -> None:
        self.order = order
        self.fail_operation = fail_operation
        self.block_operation = block_operation
        self._supports_bounded_reconnect = supports_bounded_reconnect
        self.control_started = threading.Event()
        self.release_control = threading.Event()
        self.reconnect_timeouts: list[float] = []
        self.state = FakeRadioState()
        self._connected = False
        self._psi_active = False
        self._psi_callbacks: list[Callable[[object], None]] = []
        self._state_callbacks: list[
            Callable[[RadioStateSnapshot], None]
        ] = []
        self.hold_key_codes: list[str] = []
        self.gsi_updates: list[tuple[str, str | None] | None] = []
        self.gsi_timeouts: list[float] = []

    @property
    def endpoint(self) -> str:
        return "fake://scanner"

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def psi_active(self) -> bool:
        return self._psi_active

    @property
    def supports_bounded_reconnect(self) -> bool:
        return self._supports_bounded_reconnect

    def on_psi(
        self,
        callback: Callable[[object], None],
    ) -> Callable[[], None]:
        self._psi_callbacks.append(callback)

        def unsubscribe() -> None:
            if callback in self._psi_callbacks:
                self._psi_callbacks.remove(callback)

        return unsubscribe

    def _emit_psi(self) -> None:
        for callback in tuple(self._psi_callbacks):
            callback(object())

    def on_state(
        self,
        callback: Callable[[RadioStateSnapshot], None],
    ) -> Callable[[], None]:
        self._state_callbacks.append(callback)

        def unsubscribe() -> None:
            if callback in self._state_callbacks:
                self._state_callbacks.remove(callback)

        return unsubscribe

    def emit_hold_state(self, scope: str, value: str | None) -> None:
        snapshot = self.state.set_hold(scope, value)
        for callback in tuple(self._state_callbacks):
            callback(snapshot)

    def connect(self) -> None:
        self.order.append("scanner.connect")
        self._connected = True

    def get_model(self, *, timeout: float = 2.0) -> str:
        assert timeout == 2.0
        return "SDS200"

    def get_firmware(self, *, timeout: float = 2.0) -> str:
        assert timeout == 2.0
        return "Version 1.26.01"

    def get_scanner_info(self, *, timeout: float = 3.0) -> object:
        self.gsi_timeouts.append(timeout)
        self.order.append(f"scanner.gsi:{timeout!r}")
        time.sleep(min(0.005, timeout))
        if self.gsi_updates:
            update = self.gsi_updates.pop(0)
            if update is not None:
                scope, value = update
                self.emit_hold_state(scope, value)
        return object()

    def start_scanner_info_push(
        self,
        interval_ms: int = 500,
        *,
        timeout: float = 3.0,
    ) -> object:
        assert interval_ms == 500
        assert timeout == 3.0
        self.order.append("psi.start")
        self._psi_active = True
        self._emit_psi()
        return object()

    def stop_scanner_info_push(self) -> None:
        self.order.append("psi.stop")
        self._psi_active = False

    def close(self) -> None:
        self.order.append("scanner.close")
        self._psi_active = False
        self._connected = False

    def press_hold_key(
        self,
        key_code: str,
        *,
        timeout: float = 2.0,
    ) -> None:
        self.hold_key_codes.append(key_code)
        self._control("key", key_code, timeout)

    def hold(
        self,
        target: str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        timeout: float = 2.0,
    ) -> None:
        self._control(
            "hold",
            target,
            first,
            second,
            timeout,
        )

    def next(
        self,
        target: str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        count: int = 1,
        timeout: float = 2.0,
    ) -> None:
        self._control(
            "next",
            target,
            first,
            second,
            count,
            timeout,
        )

    def previous(
        self,
        target: str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        count: int = 1,
        timeout: float = 2.0,
    ) -> None:
        self._control(
            "previous",
            target,
            first,
            second,
            count,
            timeout,
        )

    def reconnect(self, *, timeout: float = 2.0) -> None:
        self.reconnect_timeouts.append(timeout)
        self._control("reconnect")
        self._connected = True
        self._psi_active = True
        self._emit_psi()

    def _control(self, operation: str, *arguments: object) -> None:
        self.order.append(
            f"scanner.{operation}:{arguments!r}"
        )
        if self.block_operation == operation:
            self.control_started.set()
            if not self.release_control.wait(2.0):
                raise TimeoutError("Synthetic control was not released.")
        if self.fail_operation == operation:
            raise RuntimeError("secret scanner control detail")


def make_runtime(
    scanner: FakeControlScanner,
) -> DaemonRuntime:
    router = PcmSinkRouter(name="daemon-pcm")
    audio = AudioFanoutSession(
        AudioStream(FakeAudioTransport()),
        (router,),
    )
    return DaemonRuntime(scanner, audio, router)


def test_runtime_executes_existing_typed_controls_with_ordered_results() -> None:
    order: list[str] = []
    scanner = FakeControlScanner(order)
    runtime = make_runtime(scanner)
    runtime.start()

    results = (
        runtime.hold("SYS", 42, timeout=1.5),
        runtime.next("DEPT", 7, 42, count=2, timeout=1.5),
        runtime.previous("TGID", 99, count=3, timeout=1.5),
        runtime.reconnect(timeout=1.5),
    )

    assert [result.sequence for result in results] == [1, 2, 3, 4]
    assert [result.operation for result in results] == [
        DaemonControlOperation.HOLD,
        DaemonControlOperation.NEXT,
        DaemonControlOperation.PREVIOUS,
        DaemonControlOperation.RECONNECT,
    ]
    assert all(isinstance(result, DaemonControlResult) for result in results)
    assert all(
        result.snapshot.state is DaemonRuntimeState.RUNNING
        for result in results
    )
    assert all(
        result.completed_at >= result.started_at
        for result in results
    )

    payload = results[-1].as_dict()
    assert payload["sequence"] == 4
    assert payload["operation"] == "scanner.reconnect"
    snapshot_payload = payload["snapshot"]
    assert isinstance(snapshot_payload, dict)
    assert snapshot_payload["state"] == "running"

    decoded = json.loads(json.dumps(payload))
    assert decoded["sequence"] == 4
    assert decoded["operation"] == "scanner.reconnect"
    assert decoded["snapshot"]["state"] == "running"

    assert [
        entry.partition(":")[0]
        for entry in order[2:]
    ] == [
        "scanner.hold",
        "scanner.next",
        "scanner.previous",
        "scanner.reconnect",
    ]
    assert len(scanner.reconnect_timeouts) == 1
    assert 0 < scanner.reconnect_timeouts[0] <= 1.5

    runtime.stop()


@pytest.mark.parametrize(
    ("scope", "expected_keys"),
    [
        ("system", ("A",)),
        ("department", ("B",)),
        ("site", ("F", "B")),
        ("channel", ("C",)),
    ],
)
def test_hold_state_uses_verified_key_gesture_and_authoritative_gsi(
    scope: str,
    expected_keys: tuple[str, ...],
) -> None:
    scanner = FakeControlScanner([])
    scanner.gsi_updates = [None, (scope, "Off")]
    runtime = make_runtime(scanner)
    runtime.start()

    result = runtime.hold_state(scope, False, timeout=0.5)

    assert result.operation is DaemonControlOperation.HOLD_STATE
    assert tuple(scanner.hold_key_codes) == expected_keys
    assert len(scanner.gsi_timeouts) == 2
    assert all(0 < timeout <= 0.5 for timeout in scanner.gsi_timeouts)
    assert getattr(
        result.snapshot.radio_state,
        f"{scope}_hold",
    ) == "Off"

    runtime.stop()


def test_hold_state_initial_gsi_overrides_stale_cached_psi_state() -> None:
    scanner = FakeControlScanner([])
    scanner.gsi_updates = [("system", "Off")]
    runtime = make_runtime(scanner)
    runtime.start()

    result = runtime.hold_state("system", False, timeout=0.5)

    assert result.operation is DaemonControlOperation.HOLD_STATE
    assert scanner.hold_key_codes == []
    assert len(scanner.gsi_timeouts) == 1
    assert result.snapshot.radio_state.system_hold == "Off"

    runtime.stop()


def test_hold_state_noops_when_authoritative_state_already_matches() -> None:
    scanner = FakeControlScanner([])
    runtime = make_runtime(scanner)
    runtime.start()

    result = runtime.hold_state("system", True, timeout=0.5)

    assert result.operation is DaemonControlOperation.HOLD_STATE
    assert scanner.hold_key_codes == []
    assert len(scanner.gsi_timeouts) == 1

    runtime.stop()


def test_hold_state_requires_selection_only_when_enabling_hold() -> None:
    scanner = FakeControlScanner([])
    scanner.state._snapshot = replace(
        scanner.state.snapshot,
        system_index=0xFFFFFFFF,
        system_hold="Off",
    )
    runtime = make_runtime(scanner)
    runtime.start()

    with pytest.raises(
        DaemonControlUnavailableError,
        match="system selection is unavailable",
    ):
        runtime.hold_state("system", True, timeout=0.5)

    assert scanner.hold_key_codes == []

    scanner.state._snapshot = replace(
        scanner.state.snapshot,
        system_hold="On",
    )
    scanner.gsi_updates = [None, ("system", "Off")]
    result = runtime.hold_state("system", False, timeout=0.5)

    assert result.snapshot.radio_state.system_hold == "Off"
    assert scanner.hold_key_codes == ["A"]

    runtime.stop()


def test_hold_state_rejects_unavailable_authoritative_state() -> None:
    scanner = FakeControlScanner([])
    scanner.emit_hold_state("system", None)
    runtime = make_runtime(scanner)
    runtime.start()

    with pytest.raises(
        DaemonControlUnavailableError,
        match="system hold state is unavailable",
    ):
        runtime.hold_state("system", False, timeout=0.5)

    assert scanner.hold_key_codes == []

    runtime.stop()


def test_hold_state_times_out_without_authoritative_convergence() -> None:
    scanner = FakeControlScanner([])
    runtime = make_runtime(scanner)
    runtime.start()

    with pytest.raises(
        CommandTimeoutError,
        match="hold-state control",
    ):
        runtime.hold_state("channel", False, timeout=0.05)

    assert scanner.hold_key_codes == ["C"]
    assert runtime.running

    runtime.stop()


def test_hold_state_rejects_invalid_direct_parameters() -> None:
    scanner = FakeControlScanner([])
    runtime = make_runtime(scanner)
    runtime.start()

    with pytest.raises(TypeError, match="scope must be a string"):
        runtime.hold_state(1, True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="hold scope"):
        runtime.hold_state("favorites", True)
    with pytest.raises(TypeError, match="boolean"):
        runtime.hold_state("system", 1)  # type: ignore[arg-type]

    runtime.stop()


def test_controls_require_running_runtime_and_navigation_connection() -> None:
    scanner = FakeControlScanner([])
    runtime = make_runtime(scanner)

    with pytest.raises(
        DaemonControlUnavailableError,
        match="running runtime",
    ):
        runtime.hold("SYS", 42)

    runtime.start()
    scanner._connected = False

    with pytest.raises(
        DaemonControlUnavailableError,
        match="connected scanner",
    ):
        runtime.next("TGID", 99)

    result = runtime.reconnect()
    assert result.sequence == 1
    assert result.snapshot.scanner_connected is True

    runtime.stop()

    with pytest.raises(
        DaemonControlUnavailableError,
        match="running runtime",
    ):
        runtime.reconnect()


def test_reconnect_rejects_transport_without_bounded_contract() -> None:
    scanner = FakeControlScanner(
        [],
        supports_bounded_reconnect=False,
    )
    runtime = make_runtime(scanner)

    runtime.start()

    with pytest.raises(
        UnsupportedScannerFeatureError,
        match="bounded network",
    ):
        runtime.reconnect(timeout=1.0)

    runtime.stop()


def test_concurrent_controls_are_rejected_without_interleaving() -> None:
    scanner = FakeControlScanner(
        [],
        block_operation="hold",
    )
    runtime = make_runtime(scanner)
    runtime.start()
    results: list[DaemonControlResult] = []
    errors: list[BaseException] = []

    def hold() -> None:
        try:
            results.append(runtime.hold("SYS", 42))
        except BaseException as error:
            errors.append(error)

    def next_selection() -> None:
        try:
            results.append(runtime.next("TGID", 99))
        except BaseException as error:
            errors.append(error)

    hold_thread = threading.Thread(target=hold)
    next_thread = threading.Thread(target=next_selection)
    hold_thread.start()
    assert scanner.control_started.wait(1.0)

    next_thread.start()
    next_thread.join(timeout=2.0)

    assert not next_thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], DaemonControlBusyError)
    assert all("scanner.next" not in entry for entry in scanner.order)

    scanner.release_control.set()
    hold_thread.join(timeout=2.0)

    assert not hold_thread.is_alive()
    assert [result.sequence for result in results] == [1]

    runtime.stop()


def test_automatic_psi_recovery_defers_while_control_busy() -> None:
    now = [100.0]
    scanner = FakeControlScanner(
        [],
        block_operation="hold",
    )
    router = PcmSinkRouter(name="daemon-pcm")
    audio = AudioFanoutSession(
        AudioStream(FakeAudioTransport()),
        (router,),
    )
    runtime = DaemonRuntime(
        scanner,
        audio,
        router,
        psi_recover_after=5.0,
        psi_recovery_cooldown=60.0,
        clock=lambda: now[0],
    )
    runtime.start()
    errors: list[BaseException] = []

    def hold() -> None:
        try:
            runtime.hold("SYS", 42)
        except BaseException as error:
            errors.append(error)

    hold_thread = threading.Thread(target=hold)
    hold_thread.start()
    assert scanner.control_started.wait(1.0)

    now[0] = 105.1
    runtime.poll()

    assert scanner.reconnect_timeouts == []
    assert all(
        not entry.startswith("scanner.reconnect")
        for entry in scanner.order
    )

    scanner.release_control.set()
    hold_thread.join(timeout=2.0)

    assert not hold_thread.is_alive()
    assert errors == []

    # A busy poll must not consume the recovery cooldown. The next poll,
    # still within 60 seconds of the skipped attempt, must be allowed to
    # perform the stale-PSI reconnect.
    now[0] = 106.0
    runtime.poll()

    assert len(scanner.reconnect_timeouts) == 1
    assert 0 < scanner.reconnect_timeouts[0] <= 2.0
    assert sum(
        entry.startswith("scanner.reconnect")
        for entry in scanner.order
    ) == 1

    runtime.stop()


def test_shutdown_waits_for_in_flight_control() -> None:
    order: list[str] = []
    scanner = FakeControlScanner(
        order,
        block_operation="hold",
    )
    runtime = make_runtime(scanner)
    runtime.start()
    errors: list[BaseException] = []

    def hold() -> None:
        try:
            runtime.hold("SYS", 42)
        except BaseException as error:
            errors.append(error)

    def stop() -> None:
        try:
            runtime.stop()
        except BaseException as error:
            errors.append(error)

    hold_thread = threading.Thread(target=hold)
    stop_thread = threading.Thread(target=stop)
    hold_thread.start()
    assert scanner.control_started.wait(1.0)

    stop_thread.start()
    time.sleep(0.05)
    assert stop_thread.is_alive()
    assert "scanner.close" not in order

    scanner.release_control.set()
    hold_thread.join(timeout=2.0)
    stop_thread.join(timeout=2.0)

    assert not hold_thread.is_alive()
    assert not stop_thread.is_alive()
    assert not errors
    assert runtime.snapshot().state is DaemonRuntimeState.STOPPED
    assert order.index("scanner.close") > next(
        index
        for index, entry in enumerate(order)
        if entry.startswith("scanner.hold:")
    )


def test_control_failure_propagates_without_stopping_runtime() -> None:
    scanner = FakeControlScanner(
        [],
        fail_operation="next",
    )
    runtime = make_runtime(scanner)
    runtime.start()

    with pytest.raises(RuntimeError, match="secret scanner control"):
        runtime.next("TGID", 99)

    assert runtime.running
    assert runtime.snapshot().scanner_connected is True

    scanner.fail_operation = None
    result = runtime.hold("SYS", 42)
    assert result.sequence == 1

    runtime.stop()
