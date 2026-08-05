from __future__ import annotations

import json
import threading
import time

import pytest

from sds200 import (
    AudioFanoutSession,
    AudioStream,
    DaemonControlOperation,
    DaemonControlResult,
    DaemonControlUnavailableError,
    DaemonRuntime,
    DaemonRuntimeState,
    PcmSinkRouter,
    RadioStateSnapshot,
)

from .fakes import FakeAudioTransport


class FakeRadioState:
    @property
    def snapshot(self) -> RadioStateSnapshot:
        return RadioStateSnapshot(
            system="Metro",
            department="Dispatch",
            channel="Primary",
        )


class FakeControlScanner:
    def __init__(
        self,
        order: list[str],
        *,
        fail_operation: str | None = None,
        block_operation: str | None = None,
    ) -> None:
        self.order = order
        self.fail_operation = fail_operation
        self.block_operation = block_operation
        self.control_started = threading.Event()
        self.release_control = threading.Event()
        self.state = FakeRadioState()
        self._connected = False
        self._psi_active = False

    @property
    def endpoint(self) -> str:
        return "fake://scanner"

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def psi_active(self) -> bool:
        return self._psi_active

    def connect(self) -> None:
        self.order.append("scanner.connect")
        self._connected = True

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
        return object()

    def stop_scanner_info_push(self) -> None:
        self.order.append("psi.stop")
        self._psi_active = False

    def close(self) -> None:
        self.order.append("scanner.close")
        self._psi_active = False
        self._connected = False

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

    def reconnect(self) -> None:
        self._control("reconnect")
        self._connected = True
        self._psi_active = True

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
        runtime.reconnect(),
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

    assert order[2:] == [
        "scanner.hold:('SYS', 42, None, 1.5)",
        "scanner.next:('DEPT', 7, 42, 2, 1.5)",
        "scanner.previous:('TGID', 99, None, 3, 1.5)",
        "scanner.reconnect:()",
    ]

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


def test_concurrent_controls_are_serialized() -> None:
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
    time.sleep(0.05)
    assert all("scanner.next" not in entry for entry in scanner.order)

    scanner.release_control.set()
    hold_thread.join(timeout=2.0)
    next_thread.join(timeout=2.0)

    assert not hold_thread.is_alive()
    assert not next_thread.is_alive()
    assert not errors
    assert [result.sequence for result in results] == [1, 2]
    assert any("scanner.next" in entry for entry in scanner.order)

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
