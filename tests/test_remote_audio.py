from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from time import monotonic, sleep

import pytest

from sds200.audio_recording import PCM_SAMPLE_WIDTH
from sds200.exceptions import AudioOutputError
from sds200.reliability import ReconnectPolicy
from sds200.remote_audio import (
    EnvironmentSecret,
    RemoteAudioConnection,
    RemoteDestinationConfig,
    RemotePcmSink,
    RemotePcmSinkTransition,
)


class RecordingConnection:
    def __init__(
        self,
        *,
        fail_message: str | None = None,
        write_gate: threading.Event | None = None,
        write_started: threading.Event | None = None,
    ) -> None:
        self.fail_message = fail_message
        self.write_gate = write_gate
        self.write_started = write_started
        self.writes: list[bytes] = []
        self.interrupted = False
        self.closed = False

    def write_pcm(self, data: bytes) -> None:
        if self.write_started is not None:
            self.write_started.set()
        if self.write_gate is not None:
            assert self.write_gate.wait(timeout=1.0)
        if self.fail_message is not None:
            message, self.fail_message = self.fail_message, None
            raise OSError(message)
        self.writes.append(data)

    def interrupt(self) -> None:
        self.interrupted = True
        if self.write_gate is not None:
            self.write_gate.set()

    def close(self) -> None:
        self.closed = True


class SequenceFactory:
    def __init__(
        self,
        *results: RemoteAudioConnection | BaseException,
    ) -> None:
        self.results: deque[RemoteAudioConnection | BaseException] = deque(results)
        self.calls = 0
        self.resolved_secrets: list[dict[str, str]] = []

    def __call__(
        self,
        config: RemoteDestinationConfig,
        secrets: Mapping[str, str],
    ) -> RemoteAudioConnection:
        del config
        self.calls += 1
        self.resolved_secrets.append(dict(secrets))
        if not self.results:
            raise AssertionError("Connection factory received an unexpected call.")
        result = self.results.popleft()
        if isinstance(result, BaseException):
            raise result
        return result


def wait_until(predicate: Callable[[], bool], *, timeout: float = 1.0) -> None:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if predicate():
            return
        sleep(0.005)
    raise AssertionError("Condition was not satisfied before the timeout.")


def remote_config(
    *,
    secrets: Mapping[str, EnvironmentSecret] | None = None,
    buffer_seconds: float = 0.01,
    reconnect_policy: ReconnectPolicy | None = None,
) -> RemoteDestinationConfig:
    return RemoteDestinationConfig(
        name="test-feed",
        endpoint="test://audio/feed",
        secrets={} if secrets is None else secrets,
        buffer_seconds=buffer_seconds,
        stop_timeout=1.0,
        reconnect_policy=(
            ReconnectPolicy(initial_delay=0.01, multiplier=1.0, max_delay=0.01)
            if reconnect_policy is None
            else reconnect_policy
        ),
    )


def test_environment_secret_resolves_without_exposing_value() -> None:
    secret = EnvironmentSecret("SDS200_FEED_PASSWORD")
    value = "feed-password"

    assert secret.resolve({"SDS200_FEED_PASSWORD": value}) == value
    assert value not in repr(secret)

    with pytest.raises(AudioOutputError, match="SDS200_FEED_PASSWORD"):
        secret.resolve({})


def test_remote_destination_rejects_embedded_credentials() -> None:
    with pytest.raises(ValueError, match="embedded credentials"):
        RemoteDestinationConfig(
            name="unsafe",
            endpoint="https://user:password@example.invalid/feed",
        )


def test_remote_sink_writes_on_worker_and_closes_connection() -> None:
    connection = RecordingConnection()
    factory = SequenceFactory(connection)
    sink = RemotePcmSink(remote_config(), factory)
    pcm = b"\x01\x00\x02\x00"

    sink.start()
    sink.submit_pcm(pcm)
    wait_until(lambda: connection.writes == [pcm])

    snapshot = sink.snapshot()
    assert snapshot.connected
    assert snapshot.successful_connections == 1
    assert snapshot.statistics.bytes_submitted == len(pcm)
    assert snapshot.statistics.bytes_written == len(pcm)
    assert snapshot.statistics.bytes_dropped == 0

    sink.stop()
    assert connection.closed
    assert not sink.running


def test_remote_sink_drops_oldest_queued_audio_while_write_blocks() -> None:
    write_gate = threading.Event()
    write_started = threading.Event()
    connection = RecordingConnection(
        write_gate=write_gate,
        write_started=write_started,
    )
    factory = SequenceFactory(connection)
    capacity_seconds = (2 * PCM_SAMPLE_WIDTH) / (8000 * PCM_SAMPLE_WIDTH)
    sink = RemotePcmSink(
        remote_config(buffer_seconds=capacity_seconds),
        factory,
    )
    first = b"\x01\x00\x02\x00"
    second = b"\x03\x00\x04\x00"
    newest = b"\x05\x00\x06\x00"

    sink.start()
    sink.submit_pcm(first)
    assert write_started.wait(timeout=1.0)

    sink.submit_pcm(second)
    sink.submit_pcm(newest)
    queued = sink.statistics
    assert queued.bytes_submitted == len(first) + len(second) + len(newest)
    assert queued.bytes_dropped == len(second)
    assert queued.queued_bytes == len(newest)
    assert queued.overflows == 1

    write_gate.set()
    wait_until(lambda: connection.writes == [first, newest])
    sink.stop()

    statistics = sink.statistics
    assert statistics.bytes_written == len(first) + len(newest)
    assert statistics.bytes_dropped == len(second)


def test_remote_sink_reconnects_and_redacts_connection_errors() -> None:
    secret_value = "top-secret-feed-key"
    first = RecordingConnection(fail_message=f"connection lost: {secret_value}")
    second = RecordingConnection()
    factory = SequenceFactory(first, second)
    config = remote_config(
        secrets={"password": EnvironmentSecret("SDS200_FEED_PASSWORD")},
    )
    sink = RemotePcmSink(
        config,
        factory,
        environ={"SDS200_FEED_PASSWORD": secret_value},
    )
    first_pcm = b"\x01\x00"
    second_pcm = b"\x02\x00"

    sink.start()
    sink.submit_pcm(first_pcm)
    wait_until(lambda: sink.snapshot().failures == 1)
    sink.submit_pcm(second_pcm)
    wait_until(lambda: second.writes == [second_pcm])

    snapshot = sink.snapshot()
    assert snapshot.connection_attempts == 2
    assert snapshot.successful_connections == 2
    assert snapshot.reconnects == 1
    assert snapshot.statistics.bytes_dropped == len(first_pcm)
    assert snapshot.last_error is not None
    assert "<redacted>" in snapshot.last_error
    assert secret_value not in snapshot.last_error
    assert secret_value not in repr(snapshot)
    assert factory.resolved_secrets == [
        {"password": secret_value},
        {"password": secret_value},
    ]

    sink.stop()
    assert first.closed
    assert second.closed


def test_initial_connection_retries_do_not_count_as_reconnects() -> None:
    connection = RecordingConnection()
    factory = SequenceFactory(OSError("offline"), connection)
    sink = RemotePcmSink(remote_config(), factory)
    pcm = b"\x01\x00"

    sink.start()
    sink.submit_pcm(pcm)
    wait_until(lambda: connection.writes == [pcm])

    snapshot = sink.snapshot()
    assert snapshot.connection_attempts == 2
    assert snapshot.successful_connections == 1
    assert snapshot.reconnects == 0
    assert snapshot.failures == 1

    sink.stop()


def test_remote_sink_reports_retry_exhaustion_without_secret_leakage() -> None:
    secret_value = "never-log-this"
    factory = SequenceFactory(
        OSError(f"connect failed: {secret_value}"),
        OSError(f"connect failed: {secret_value}"),
        OSError(f"connect failed: {secret_value}"),
    )
    config = remote_config(
        secrets={"password": EnvironmentSecret("SDS200_FEED_PASSWORD")},
        reconnect_policy=ReconnectPolicy(
            initial_delay=0.01,
            multiplier=1.0,
            max_delay=0.01,
            max_attempts=2,
        ),
    )
    sink = RemotePcmSink(
        config,
        factory,
        environ={"SDS200_FEED_PASSWORD": secret_value},
    )
    transitions: list[RemotePcmSinkTransition] = []
    sink.on_transition(transitions.append)

    sink.start()
    sink.submit_pcm(b"\x01\x00")
    wait_until(lambda: sink.snapshot().state == "failed")

    snapshot = sink.snapshot()
    assert snapshot.health == "failed"
    assert snapshot.connection_attempts == 3
    assert snapshot.failures == 3
    assert snapshot.statistics.bytes_dropped == 2
    assert snapshot.last_error is not None
    assert secret_value not in snapshot.last_error
    assert transitions[-1].state == "failed"
    assert transitions[-1].health == "failed"
    assert secret_value not in repr(transitions[-1])

    with pytest.raises(AudioOutputError) as error:
        sink.stop()
    assert secret_value not in str(error.value)
    assert "<redacted>" in str(error.value)


def test_remote_sink_shutdown_interrupts_blocked_write() -> None:
    write_gate = threading.Event()
    write_started = threading.Event()
    connection = RecordingConnection(
        fail_message="write interrupted",
        write_gate=write_gate,
        write_started=write_started,
    )
    sink = RemotePcmSink(remote_config(), SequenceFactory(connection))
    pcm = b"\x01\x00"

    sink.start()
    sink.submit_pcm(pcm)
    assert write_started.wait(timeout=1.0)
    sink.stop()

    assert connection.interrupted
    assert connection.closed
    assert not sink.running
    snapshot = sink.snapshot()
    assert snapshot.statistics.bytes_dropped == len(pcm)
    assert snapshot.failures == 0


def test_remote_sink_shutdown_interrupts_backoff() -> None:
    factory = SequenceFactory(OSError("offline"))
    config = remote_config(
        reconnect_policy=ReconnectPolicy(
            initial_delay=60.0,
            multiplier=1.0,
            max_delay=60.0,
        ),
    )
    sink = RemotePcmSink(config, factory)

    sink.start()
    sink.submit_pcm(b"\x01\x00")
    wait_until(lambda: sink.snapshot().state == "backoff")
    sink.stop()

    assert not sink.running
    assert sink.snapshot().state == "stopped"


def test_remote_sink_emits_health_transitions_with_deterministic_timestamps() -> None:
    initial = datetime(2026, 8, 3, 10, 0, tzinfo=UTC)
    timestamps = iter(initial + timedelta(seconds=index) for index in range(5))
    connection = RecordingConnection()
    sink = RemotePcmSink(
        remote_config(),
        SequenceFactory(connection),
        now=lambda: next(timestamps),
    )
    transitions: list[RemotePcmSinkTransition] = []
    unsubscribe = sink.on_transition(transitions.append)

    sink.start()
    assert transitions == []

    pcm = b"\x01\x00"
    sink.submit_pcm(pcm)
    wait_until(lambda: connection.writes == [pcm])
    wait_until(lambda: len(transitions) == 2)

    sink.stop()
    unsubscribe()

    assert [transition.sequence for transition in transitions] == [1, 2, 3, 4]
    assert [transition.state for transition in transitions] == [
        "connecting",
        "connected",
        "stopping",
        "stopped",
    ]
    assert [transition.health for transition in transitions] == [
        "degraded",
        "healthy",
        "inactive",
        "inactive",
    ]
    assert transitions[0].previous_state == "idle"
    assert transitions[0].previous_health == "inactive"
    assert transitions[1].observed_at == initial + timedelta(seconds=2)

    snapshot = sink.snapshot()
    assert snapshot.state == "stopped"
    assert snapshot.health == "inactive"
    assert snapshot.transition_sequence == 4
    assert snapshot.state_changed_at == initial + timedelta(seconds=4)
    assert snapshot.last_connected_at == initial + timedelta(seconds=2)
    assert snapshot.last_failure_at is None

    payload = snapshot.as_dict()
    assert payload["state"] == "stopped"
    assert payload["health"] == "inactive"
    assert payload["transition_sequence"] == 4
    assert payload["state_changed_at"] == (
        initial + timedelta(seconds=4)
    ).isoformat()
    assert payload["last_connected_at"] == (
        initial + timedelta(seconds=2)
    ).isoformat()
    assert payload["statistics"] == {
        "bytes_submitted": len(pcm),
        "bytes_written": len(pcm),
        "bytes_dropped": 0,
        "queued_bytes": 0,
        "underflows": 0,
        "overflows": 0,
        "callback_statuses": 0,
    }

    transition_payload = transitions[1].as_dict()
    assert transition_payload["previous_state"] == "connecting"
    assert transition_payload["state"] == "connected"
    assert transition_payload["health"] == "healthy"
    assert transition_payload["snapshot"]["state"] == "connected"


def test_remote_sink_health_tracks_failure_backoff_and_reconnect() -> None:
    initial = datetime(2026, 8, 3, 11, 0, tzinfo=UTC)
    timestamps = iter(initial + timedelta(seconds=index) for index in range(7))
    connection = RecordingConnection()
    sink = RemotePcmSink(
        remote_config(),
        SequenceFactory(OSError("offline"), connection),
        now=lambda: next(timestamps),
    )
    transitions: list[RemotePcmSinkTransition] = []
    sink.on_transition(transitions.append)

    sink.start()
    pcm = b"\x01\x00"
    sink.submit_pcm(pcm)
    wait_until(lambda: connection.writes == [pcm])

    snapshot = sink.snapshot()
    assert snapshot.state == "connected"
    assert snapshot.health == "healthy"
    assert snapshot.transition_sequence == 4
    assert snapshot.last_failure_at == initial + timedelta(seconds=2)
    assert snapshot.last_connected_at == initial + timedelta(seconds=4)
    assert snapshot.failures == 1
    assert snapshot.reconnects == 0
    assert [transition.state for transition in transitions] == [
        "connecting",
        "backoff",
        "connecting",
        "connected",
    ]
    assert transitions[1].health == "degraded"
    assert transitions[1].snapshot.last_failure_at == (
        initial + timedelta(seconds=2)
    )

    sink.stop()
    assert [transition.state for transition in transitions[-2:]] == [
        "stopping",
        "stopped",
    ]


def test_remote_sink_transition_listener_failure_is_isolated() -> None:
    connection = RecordingConnection()
    sink = RemotePcmSink(remote_config(), SequenceFactory(connection))
    observed: list[RemotePcmSinkTransition] = []

    def fail_listener(transition: RemotePcmSinkTransition) -> None:
        del transition
        raise RuntimeError("listener failed")

    sink.on_transition(fail_listener)
    sink.on_transition(observed.append)

    sink.start()
    pcm = b"\x01\x00"
    sink.submit_pcm(pcm)
    wait_until(lambda: connection.writes == [pcm])
    wait_until(lambda: any(item.state == "connected" for item in observed))
    sink.stop()

    assert [transition.state for transition in observed] == [
        "connecting",
        "connected",
        "stopping",
        "stopped",
    ]


def test_remote_sink_rejects_naive_wall_clock() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        RemotePcmSink(
            remote_config(),
            SequenceFactory(RecordingConnection()),
            now=lambda: datetime(2026, 8, 3),
        )


def test_remote_sink_serializes_concurrent_stop_transitions() -> None:
    connection = RecordingConnection()
    factory = SequenceFactory(connection)
    sink = RemotePcmSink(remote_config(), factory)
    connecting_callback_entered = threading.Event()
    release_connecting_callback = threading.Event()
    observed: list[RemotePcmSinkTransition] = []
    stop_errors: list[BaseException] = []

    def block_connecting_callback(transition: RemotePcmSinkTransition) -> None:
        if transition.state != "connecting":
            return
        connecting_callback_entered.set()
        release_connecting_callback.wait(timeout=1.0)

    sink.on_transition(block_connecting_callback)
    sink.on_transition(observed.append)

    sink.start()
    sink.submit_pcm(b"\x01\x00")
    assert connecting_callback_entered.wait(timeout=1.0)

    def stop_sink() -> None:
        try:
            sink.stop()
        except BaseException as error:
            stop_errors.append(error)

    stop_thread = threading.Thread(target=stop_sink)
    stop_thread.start()
    wait_until(lambda: sink.snapshot().state == "stopping")

    release_connecting_callback.set()
    stop_thread.join(timeout=1.0)

    assert not stop_thread.is_alive()
    assert stop_errors == []
    assert factory.calls == 0
    assert not connection.closed
    assert connection.writes == []
    assert [transition.sequence for transition in observed] == [1, 2, 3]
    assert [transition.state for transition in observed] == [
        "connecting",
        "stopping",
        "stopped",
    ]
    assert sink.snapshot().transition_sequence == 3


def test_remote_sink_transition_subscription_can_be_removed() -> None:
    connection = RecordingConnection()
    sink = RemotePcmSink(remote_config(), SequenceFactory(connection))
    observed: list[RemotePcmSinkTransition] = []
    unsubscribe = sink.on_transition(observed.append)
    unsubscribe()

    sink.start()
    pcm = b"\x01\x00"
    sink.submit_pcm(pcm)
    wait_until(lambda: connection.writes == [pcm])
    sink.stop()

    assert observed == []


def test_remote_sink_transition_listener_can_stop_from_worker() -> None:
    connection = RecordingConnection()
    factory = SequenceFactory(connection)
    sink = RemotePcmSink(remote_config(), factory)
    observed: list[RemotePcmSinkTransition] = []

    def stop_while_connecting(transition: RemotePcmSinkTransition) -> None:
        if transition.state == "connecting":
            sink.stop()

    sink.on_transition(stop_while_connecting)
    sink.on_transition(observed.append)

    sink.start()
    sink.submit_pcm(b"\x01\x00")
    wait_until(lambda: sink.snapshot().state == "stopped")

    assert factory.calls == 0
    assert not connection.closed
    assert not sink.running
    assert [transition.sequence for transition in observed] == [1, 2, 3]
    assert [transition.state for transition in observed] == [
        "connecting",
        "stopping",
        "stopped",
    ]


def test_remote_sink_stopping_listener_can_repeat_stop() -> None:
    connection = RecordingConnection()
    sink = RemotePcmSink(remote_config(), SequenceFactory(connection))
    observed: list[RemotePcmSinkTransition] = []

    def repeat_stop(transition: RemotePcmSinkTransition) -> None:
        if transition.state == "stopping":
            sink.stop()

    sink.on_transition(repeat_stop)
    sink.on_transition(observed.append)

    sink.start()
    pcm = b"\x01\x00"
    sink.submit_pcm(pcm)
    wait_until(lambda: connection.writes == [pcm])
    sink.stop()

    assert connection.closed
    assert not sink.running
    assert [transition.state for transition in observed] == [
        "connecting",
        "connected",
        "stopping",
        "stopped",
    ]
