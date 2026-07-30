from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable, Mapping
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

    sink.start()
    sink.submit_pcm(b"\x01\x00")
    wait_until(lambda: sink.snapshot().state == "failed")

    snapshot = sink.snapshot()
    assert snapshot.connection_attempts == 3
    assert snapshot.failures == 3
    assert snapshot.statistics.bytes_dropped == 2
    assert snapshot.last_error is not None
    assert secret_value not in snapshot.last_error

    with pytest.raises(AudioOutputError) as error:
        sink.stop()
    assert secret_value not in str(error.value)
    assert "<redacted>" in str(error.value)


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
