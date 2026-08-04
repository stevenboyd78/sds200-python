from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from typing import Literal

import pytest

from sds200.audio import AudioChunk, AudioChunkHandler, AudioStream
from sds200.audio_sinks import (
    AudioFanoutSession,
    PcmSinkRouter,
    PcmSinkStatistics,
)
from sds200.daemon_runtime import (
    DaemonRuntime,
    DaemonRuntimeState,
    DaemonRuntimeTransition,
)
from sds200.state import RadioStateSnapshot

from .fakes import FakeAudioTransport


class FakeRadioState:
    @property
    def snapshot(self) -> RadioStateSnapshot:
        return RadioStateSnapshot(
            system="Metro",
            department="Dispatch",
            channel="Primary",
        )


class FakeScanner:
    def __init__(
        self,
        order: list[str],
        *,
        fail_at: Literal["connect", "psi"] | None = None,
    ) -> None:
        self.order = order
        self.fail_at = fail_at
        self._connected = False
        self._psi_active = False
        self.state = FakeRadioState()
        self.close_calls = 0

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
        if self.fail_at == "connect":
            raise RuntimeError("secret scanner connection detail")
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
        if self.fail_at == "psi":
            raise RuntimeError("secret PSI startup detail")
        self._psi_active = True
        return object()

    def stop_scanner_info_push(self) -> None:
        self.order.append("psi.stop")
        self._psi_active = False

    def close(self) -> None:
        self.order.append("scanner.close")
        self.close_calls += 1
        self._psi_active = False
        self._connected = False


class TrackingAudioTransport(FakeAudioTransport):
    def __init__(
        self,
        order: list[str],
        *,
        fail_start: bool = False,
        fail_stop: bool = False,
    ) -> None:
        super().__init__()
        self.order = order
        self.fail_start = fail_start
        self.fail_stop = fail_stop
        self.stop_calls = 0

    def start(self, handler: AudioChunkHandler) -> None:
        self.order.append("audio.start")
        if self.fail_start:
            raise RuntimeError("secret audio startup detail")
        super().start(handler)

    def stop(self) -> None:
        self.order.append("audio.stop")
        self.stop_calls += 1
        super().stop()
        if self.fail_stop:
            raise RuntimeError("secret audio shutdown detail")


class TrackingRouter(PcmSinkRouter):
    def __init__(self, order: list[str]) -> None:
        super().__init__()
        self.order = order
        self.stop_calls = 0

    def start(self) -> None:
        self.order.append("router.start")
        super().start()

    def stop(self) -> None:
        self.order.append("router.stop")
        self.stop_calls += 1
        super().stop()


class CollectingSink:
    def __init__(self, name: str) -> None:
        self._name = name
        self._running = False
        self.received: list[bytes] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def running(self) -> bool:
        return self._running

    @property
    def statistics(self) -> PcmSinkStatistics:
        written = sum(len(data) for data in self.received)
        return PcmSinkStatistics(
            bytes_submitted=written,
            bytes_written=written,
        )

    def start(self) -> None:
        self._running = True

    def submit_pcm(self, data: bytes) -> None:
        assert self._running
        self.received.append(data)

    def stop(self) -> None:
        self._running = False


class FailingStartSink(CollectingSink):
    def start(self) -> None:
        raise RuntimeError("secret destination startup detail")


def make_runtime(
    *,
    scanner_fail_at: Literal["connect", "psi"] | None = None,
    audio_fail_start: bool = False,
    audio_fail_stop: bool = False,
) -> tuple[
    DaemonRuntime,
    FakeScanner,
    TrackingAudioTransport,
    TrackingRouter,
    list[str],
]:
    order: list[str] = []
    scanner = FakeScanner(order, fail_at=scanner_fail_at)
    transport = TrackingAudioTransport(
        order,
        fail_start=audio_fail_start,
        fail_stop=audio_fail_stop,
    )
    router = TrackingRouter(order)
    audio = AudioFanoutSession(AudioStream(transport), (router,))
    runtime = DaemonRuntime(scanner, audio, router)
    return runtime, scanner, transport, router, order


def test_runtime_owns_startup_and_reverse_order_shutdown() -> None:
    runtime, scanner, transport, router, order = make_runtime()
    transitions: list[DaemonRuntimeTransition] = []
    runtime.on_transition(transitions.append)

    runtime.start()

    assert order == [
        "scanner.connect",
        "psi.start",
        "router.start",
        "audio.start",
    ]
    running = runtime.snapshot()
    assert running.state is DaemonRuntimeState.RUNNING
    assert running.scanner_connected
    assert running.psi_active
    assert running.audio.running
    assert running.router.running
    assert running.radio_state.channel == "Primary"

    runtime.stop()

    assert order == [
        "scanner.connect",
        "psi.start",
        "router.start",
        "audio.start",
        "audio.stop",
        "router.stop",
        "psi.stop",
        "scanner.close",
    ]
    stopped = runtime.snapshot()
    assert stopped.state is DaemonRuntimeState.STOPPED
    assert not stopped.active
    assert not stopped.scanner_connected
    assert not stopped.psi_active
    assert not stopped.audio.running
    assert not stopped.router.running
    assert scanner.close_calls == 1
    assert transport.stop_calls == 1
    assert router.stop_calls == 1
    assert [transition.sequence for transition in transitions] == [1, 2, 3, 4]
    assert [transition.state for transition in transitions] == [
        DaemonRuntimeState.STARTING,
        DaemonRuntimeState.RUNNING,
        DaemonRuntimeState.STOPPING,
        DaemonRuntimeState.STOPPED,
    ]

    payload = stopped.as_dict()
    assert payload["state"] == "stopped"
    assert payload["radio_state"]["channel"] == "Primary"
    assert payload["audio"]["endpoint"] == "audio://scanner"
    assert json.loads(json.dumps(payload))["state"] == "stopped"


@pytest.mark.parametrize("failure", ["connect", "psi", "audio"])
def test_runtime_cleans_up_partial_start_and_redacts_failure(
    failure: str,
) -> None:
    runtime, scanner, transport, router, _ = make_runtime(
        scanner_fail_at=(
            failure
            if failure in {"connect", "psi"}
            else None
        ),
        audio_fail_start=failure == "audio",
    )
    transitions: list[DaemonRuntimeTransition] = []
    runtime.on_transition(transitions.append)

    with pytest.raises(RuntimeError, match="secret"):
        runtime.start()

    snapshot = runtime.snapshot()
    assert snapshot.state is DaemonRuntimeState.FAILED
    assert snapshot.last_error == "RuntimeError"
    assert snapshot.last_failure_at is not None
    assert not snapshot.scanner_connected
    assert not snapshot.psi_active
    assert not snapshot.audio.running
    assert not snapshot.router.running
    assert scanner.close_calls == 1
    assert [transition.state for transition in transitions] == [
        DaemonRuntimeState.STARTING,
        DaemonRuntimeState.FAILED,
    ]
    assert "secret" not in repr(snapshot)
    assert "secret" not in repr(transitions[-1])

    runtime.stop()


def test_runtime_routes_dynamic_sinks_through_one_audio_stream() -> None:
    runtime, _, transport, _, _ = make_runtime()
    first = CollectingSink("first")
    second = CollectingSink("second")

    runtime.attach_sink(first)
    runtime.start()
    transport.feed(AudioChunk(b"\xff\x7f"))

    runtime.attach_sink(second)
    transport.feed(AudioChunk(b"\x00"))

    runtime.detach_sink(first)
    transport.feed(AudioChunk(b"\x01\x02"))
    runtime.stop()

    assert [len(data) for data in first.received] == [4, 2]
    assert [len(data) for data in second.received] == [2, 4]
    assert not first.running
    assert not second.running
    assert runtime.snapshot().audio.packets == 3


def test_destination_start_failure_does_not_stop_runtime() -> None:
    runtime, _, transport, _, _ = make_runtime()
    healthy = CollectingSink("healthy")
    failing = FailingStartSink("failing")
    runtime.attach_sink(healthy)
    runtime.start()

    with pytest.raises(RuntimeError, match="secret destination"):
        runtime.attach_sink(failing)

    assert runtime.running
    transport.feed(AudioChunk(b"\xff"))
    assert len(healthy.received) == 1

    snapshot = runtime.snapshot()
    failed = next(
        subscriber
        for subscriber in snapshot.router.subscribers
        if subscriber.name == "failing"
    )
    assert failed.last_error == "RuntimeError"
    assert "secret" not in repr(failed)

    runtime.stop()


def test_preattached_destination_failure_isolated_during_start() -> None:
    runtime, _, transport, _, _ = make_runtime()
    healthy = CollectingSink("healthy")
    failing = FailingStartSink("failing")
    runtime.attach_sink(healthy)
    runtime.attach_sink(failing)

    runtime.start()

    assert runtime.running
    transport.feed(AudioChunk(b"\xff"))
    assert len(healthy.received) == 1

    snapshot = runtime.snapshot()
    failed = next(
        subscriber
        for subscriber in snapshot.router.subscribers
        if subscriber.name == "failing"
    )
    assert failed.state == "failed"
    assert not failed.attached
    assert failed.last_error == "RuntimeError"
    assert "secret" not in repr(failed)

    runtime.stop()


def test_runtime_stop_is_idempotent_and_serialized() -> None:
    runtime, scanner, transport, router, _ = make_runtime()
    runtime.start()
    errors: list[BaseException] = []

    def stop_runtime() -> None:
        try:
            runtime.stop()
        except BaseException as error:
            errors.append(error)

    threads = [
        threading.Thread(target=stop_runtime),
        threading.Thread(target=stop_runtime),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2.0)

    assert all(not thread.is_alive() for thread in threads)
    assert not errors
    assert scanner.close_calls == 1
    assert transport.stop_calls == 1
    assert router.stop_calls == 1
    assert runtime.snapshot().state is DaemonRuntimeState.STOPPED

    runtime.stop()
    assert scanner.close_calls == 1


def test_runtime_reports_shutdown_failure_after_full_cleanup() -> None:
    runtime, scanner, _, router, order = make_runtime(audio_fail_stop=True)
    runtime.start()

    with pytest.raises(RuntimeError, match="secret audio shutdown"):
        runtime.stop()

    snapshot = runtime.snapshot()
    assert snapshot.state is DaemonRuntimeState.FAILED
    assert snapshot.last_error == "RuntimeError"
    assert scanner.close_calls == 1
    assert router.stop_calls == 1
    assert order[-3:] == [
        "router.stop",
        "psi.stop",
        "scanner.close",
    ]
    assert "secret" not in repr(snapshot)


def test_runtime_transitions_use_ordered_aware_timestamps() -> None:
    initial = datetime(2026, 8, 4, tzinfo=UTC)
    timestamps = iter(
        initial + timedelta(seconds=offset)
        for offset in range(5)
    )
    order: list[str] = []
    scanner = FakeScanner(order)
    transport = TrackingAudioTransport(order)
    router = TrackingRouter(order)
    audio = AudioFanoutSession(AudioStream(transport), (router,))
    runtime = DaemonRuntime(
        scanner,
        audio,
        router,
        now=lambda: next(timestamps),
    )
    transitions: list[DaemonRuntimeTransition] = []
    runtime.on_transition(transitions.append)

    runtime.start()
    runtime.stop()

    assert [transition.observed_at for transition in transitions] == [
        initial + timedelta(seconds=1),
        initial + timedelta(seconds=2),
        initial + timedelta(seconds=3),
        initial + timedelta(seconds=4),
    ]
    assert all(
        transition.observed_at.utcoffset() is not None
        for transition in transitions
    )
    assert runtime.snapshot().state_changed_at == (
        initial + timedelta(seconds=4)
    )


def test_runtime_can_only_be_started_once_after_shutdown() -> None:
    runtime, _, _, _, _ = make_runtime()

    runtime.start()
    runtime.start()
    runtime.stop()

    with pytest.raises(RuntimeError, match="only be started once"):
        runtime.start()


def test_runtime_requires_router_to_be_in_audio_fanout() -> None:
    order: list[str] = []
    scanner = FakeScanner(order)
    router = TrackingRouter(order)
    other_router = TrackingRouter(order)
    audio = AudioFanoutSession(
        AudioStream(TrackingAudioTransport(order)),
        (other_router,),
    )

    with pytest.raises(ValueError, match="must include"):
        DaemonRuntime(scanner, audio, router)


def test_runtime_transition_listener_failure_is_isolated() -> None:
    runtime, _, _, _, _ = make_runtime()
    observed: list[DaemonRuntimeTransition] = []

    def fail_listener(transition: DaemonRuntimeTransition) -> None:
        del transition
        raise RuntimeError("listener failed")

    runtime.on_transition(fail_listener)
    runtime.on_transition(observed.append)

    runtime.start()
    runtime.stop()

    assert [transition.state for transition in observed] == [
        DaemonRuntimeState.STARTING,
        DaemonRuntimeState.RUNNING,
        DaemonRuntimeState.STOPPING,
        DaemonRuntimeState.STOPPED,
    ]
