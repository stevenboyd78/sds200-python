from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from time import monotonic, sleep

import pytest

from sds200 import (
    RemoteMetadataPublication,
    RemoteMetadataPublisher,
    RemoteMetadataPublisherConfig,
    RemoteStreamMetadata,
)
from sds200.exceptions import AudioOutputError
from sds200.presentation import ActivityStatus, AvailabilityStatus
from sds200.reliability import ReconnectPolicy
from sds200.remote_audio import EnvironmentSecret
from sds200.state import RadioStateSnapshot


class RecordingPublication:
    def __init__(
        self,
        published: list[str],
        *,
        gate: threading.Event | None = None,
        started: threading.Event | None = None,
        fail_message: str | None = None,
        published_times: list[float] | None = None,
    ) -> None:
        self.published = published
        self.gate = gate
        self.started = started
        self.fail_message = fail_message
        self.published_times = published_times
        self.title = ""
        self.interrupted = False
        self.closed = False

    def publish(self) -> None:
        if self.started is not None:
            self.started.set()
        if self.gate is not None:
            assert self.gate.wait(timeout=1.0)
        if self.interrupted:
            raise OSError("publication interrupted")
        if self.fail_message is not None:
            raise OSError(self.fail_message)
        self.published.append(self.title)
        if self.published_times is not None:
            self.published_times.append(monotonic())

    def interrupt(self) -> None:
        self.interrupted = True
        if self.gate is not None:
            self.gate.set()

    def close(self) -> None:
        self.closed = True


class SequencePublicationFactory:
    def __init__(
        self,
        *results: RemoteMetadataPublication | BaseException,
        published: list[str] | None = None,
        published_times: list[float] | None = None,
    ) -> None:
        self.results = deque(results)
        self.published = [] if published is None else published
        self.published_times = published_times
        self.calls = 0
        self.thread_ids: list[int] = []
        self.resolved_secrets: list[dict[str, str]] = []

    def __call__(
        self,
        config: RemoteMetadataPublisherConfig,
        secrets: Mapping[str, str],
        metadata: RemoteStreamMetadata,
    ) -> RemoteMetadataPublication:
        del config
        self.calls += 1
        self.thread_ids.append(threading.get_ident())
        self.resolved_secrets.append(dict(secrets))
        if self.results:
            result = self.results.popleft()
            if isinstance(result, BaseException):
                raise result
            publication = result
        else:
            publication = RecordingPublication(
                self.published,
                published_times=self.published_times,
            )
        assert isinstance(publication, RecordingPublication)
        publication.title = metadata.render_title()
        return publication


def wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float = 1.0,
) -> None:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if predicate():
            return
        sleep(0.005)
    raise AssertionError("Condition was not satisfied before the timeout.")


def metadata(title: str) -> RemoteStreamMetadata:
    return RemoteStreamMetadata(
        activity=ActivityStatus.RECEIVING,
        availability=AvailabilityStatus.AVAILABLE,
        channel=title,
    )


def publisher_config(
    *,
    secrets: Mapping[str, EnvironmentSecret] | None = None,
    minimum_update_interval: float = 0.0,
    reconnect_policy: ReconnectPolicy | None = None,
) -> RemoteMetadataPublisherConfig:
    return RemoteMetadataPublisherConfig(
        name="test-feed",
        endpoint="https://metadata.example.invalid/feed",
        secrets={} if secrets is None else secrets,
        minimum_update_interval=minimum_update_interval,
        stop_timeout=1.0,
        reconnect_policy=(
            ReconnectPolicy(
                initial_delay=0.01,
                multiplier=1.0,
                max_delay=0.01,
            )
            if reconnect_policy is None
            else reconnect_policy
        ),
    )


def test_metadata_publisher_runs_publication_on_worker() -> None:
    published: list[str] = []
    factory = SequencePublicationFactory(published=published)
    publisher = RemoteMetadataPublisher(publisher_config(), factory)
    caller_thread = threading.get_ident()

    publisher.start()
    publisher.submit(metadata("Dispatch"))
    wait_until(lambda: published == ["Dispatch"])

    snapshot = publisher.snapshot()
    assert snapshot.state == "running"
    assert snapshot.running
    assert snapshot.submissions == 1
    assert snapshot.publications == 1
    assert snapshot.attempts == 1
    assert snapshot.pending_title is None
    assert snapshot.last_published_title == "Dispatch"
    assert factory.thread_ids != [caller_thread]

    publisher.stop()
    assert publisher.snapshot().state == "stopped"
    assert not publisher.running


def test_metadata_publisher_keeps_only_newest_pending_value() -> None:
    published: list[str] = []
    gate = threading.Event()
    started = threading.Event()
    first = RecordingPublication(
        published,
        gate=gate,
        started=started,
    )
    factory = SequencePublicationFactory(first, published=published)
    publisher = RemoteMetadataPublisher(publisher_config(), factory)

    publisher.start()
    publisher.submit(metadata("First"))
    assert started.wait(timeout=1.0)

    publisher.submit(metadata("Second"))
    publisher.submit(metadata("Newest"))
    assert publisher.snapshot().pending_title == "Newest"

    gate.set()
    wait_until(lambda: published == ["First", "Newest"])
    snapshot = publisher.snapshot()
    assert snapshot.submissions == 3
    assert snapshot.publications == 2
    assert snapshot.superseded == 2

    publisher.stop()


def test_metadata_publisher_suppresses_pending_and_published_duplicates() -> None:
    published: list[str] = []
    factory = SequencePublicationFactory(published=published)
    publisher = RemoteMetadataPublisher(publisher_config(), factory)

    publisher.start()
    publisher.submit(metadata("Dispatch"))
    publisher.submit(metadata("Dispatch"))
    wait_until(lambda: published == ["Dispatch"])

    publisher.submit(metadata("Dispatch"))
    wait_until(lambda: publisher.snapshot().duplicates_suppressed == 2)

    snapshot = publisher.snapshot()
    assert snapshot.submissions == 3
    assert snapshot.publications == 1
    assert snapshot.duplicates_suppressed == 2
    publisher.stop()


def test_metadata_publisher_honors_minimum_update_interval() -> None:
    published: list[str] = []
    published_times: list[float] = []
    factory = SequencePublicationFactory(
        published=published,
        published_times=published_times,
    )
    publisher = RemoteMetadataPublisher(
        publisher_config(minimum_update_interval=0.05),
        factory,
    )

    publisher.start()
    publisher.submit(metadata("First"))
    wait_until(lambda: published == ["First"])
    publisher.submit(metadata("Second"))
    wait_until(lambda: published == ["First", "Second"])

    assert published_times[1] - published_times[0] >= 0.04
    publisher.stop()


def test_metadata_publisher_retries_and_redacts_failures() -> None:
    secret_value = "never-log-this-value"
    published: list[str] = []
    factory = SequencePublicationFactory(
        OSError(f"request failed: {secret_value}"),
        published=published,
    )
    publisher = RemoteMetadataPublisher(
        publisher_config(
            secrets={
                "password": EnvironmentSecret(
                    "SDS200_METADATA_PASSWORD"
                )
            }
        ),
        factory,
        environ={"SDS200_METADATA_PASSWORD": secret_value},
    )

    publisher.start()
    publisher.submit(metadata("Dispatch"))
    wait_until(lambda: published == ["Dispatch"])

    snapshot = publisher.snapshot()
    assert snapshot.attempts == 2
    assert snapshot.failures == 1
    assert snapshot.retry_attempt == 0
    assert snapshot.last_error is not None
    assert "<redacted>" in snapshot.last_error
    assert secret_value not in snapshot.last_error
    assert secret_value not in repr(snapshot)
    assert factory.resolved_secrets == [
        {"password": secret_value},
        {"password": secret_value},
    ]
    publisher.stop()


def test_metadata_publisher_can_recover_after_retry_exhaustion() -> None:
    published: list[str] = []
    factory = SequencePublicationFactory(
        OSError("offline"),
        OSError("still offline"),
        published=published,
    )
    publisher = RemoteMetadataPublisher(
        publisher_config(
            reconnect_policy=ReconnectPolicy(
                initial_delay=0.01,
                multiplier=1.0,
                max_delay=0.01,
                max_attempts=1,
            )
        ),
        factory,
    )

    publisher.start()
    publisher.submit(metadata("First"))
    wait_until(lambda: publisher.snapshot().state == "failed")

    failed = publisher.snapshot()
    assert failed.attempts == 2
    assert failed.failures == 2
    assert failed.pending_title is None

    publisher.submit(metadata("Recovered"))
    wait_until(lambda: published == ["Recovered"])
    assert publisher.snapshot().state == "running"
    assert publisher.snapshot().publications == 1
    publisher.stop()


def test_metadata_publisher_stop_interrupts_blocked_publication() -> None:
    published: list[str] = []
    gate = threading.Event()
    started = threading.Event()
    publication = RecordingPublication(
        published,
        gate=gate,
        started=started,
    )
    factory = SequencePublicationFactory(publication, published=published)
    publisher = RemoteMetadataPublisher(publisher_config(), factory)

    publisher.start()
    publisher.submit(metadata("Dispatch"))
    assert started.wait(timeout=1.0)

    publisher.stop()

    assert publication.interrupted
    assert publication.closed
    assert published == []
    snapshot = publisher.snapshot()
    assert snapshot.state == "stopped"
    assert snapshot.failures == 0


def test_submit_radio_state_derives_metadata_without_caller_network_io() -> None:
    published: list[str] = []
    factory = SequencePublicationFactory(published=published)
    publisher = RemoteMetadataPublisher(publisher_config(), factory)
    caller_thread = threading.get_ident()

    publisher.start()
    derived = publisher.submit_radio_state(
        RadioStateSnapshot(
            mode="Trunk Scan",
            system="County",
            channel="Dispatch",
            signal=4,
            mute="Unmute",
        ),
        connected=True,
    )
    wait_until(lambda: published == ["County | Dispatch"])

    assert derived.render_title() == "County | Dispatch"
    assert all(thread_id != caller_thread for thread_id in factory.thread_ids)
    publisher.stop()


def test_metadata_publisher_snapshot_is_json_compatible() -> None:
    observed_at = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    publisher = RemoteMetadataPublisher(
        publisher_config(),
        SequencePublicationFactory(),
        now=lambda: observed_at,
    )

    payload = publisher.snapshot().as_dict()

    assert payload["state"] == "idle"
    assert payload["running"] is False
    assert payload["submissions"] == 0
    assert payload["state_changed_at"] == observed_at.isoformat()
    assert payload["last_submitted_at"] is None
    assert payload["last_published_at"] is None
    assert payload["last_failure_at"] is None


def test_metadata_publisher_validates_lifecycle_and_clock() -> None:
    publisher = RemoteMetadataPublisher(
        publisher_config(),
        SequencePublicationFactory(),
    )

    with pytest.raises(RuntimeError, match="not running"):
        publisher.submit(metadata("Dispatch"))

    publisher.start()
    publisher.stop()

    with pytest.raises(RuntimeError, match="only be started once"):
        publisher.start()

    with pytest.raises(ValueError, match="timezone-aware"):
        RemoteMetadataPublisher(
            publisher_config(),
            SequencePublicationFactory(),
            now=lambda: datetime(2026, 8, 3),
        )


def test_metadata_publisher_config_rejects_unsafe_values() -> None:
    with pytest.raises(ValueError, match="embedded credentials"):
        RemoteMetadataPublisherConfig(
            name="unsafe",
            endpoint="https://user:password@example.invalid/feed",
        )

    with pytest.raises(ValueError, match="must not be negative"):
        publisher_config(minimum_update_interval=-1.0)


def test_metadata_publisher_stop_timeout_is_bounded() -> None:
    class StubbornPublication(RecordingPublication):
        def interrupt(self) -> None:
            self.interrupted = True

    published: list[str] = []
    started = threading.Event()
    publication = StubbornPublication(
        published,
        gate=threading.Event(),
        started=started,
    )
    factory = SequencePublicationFactory(publication, published=published)
    config = RemoteMetadataPublisherConfig(
        name="bounded",
        endpoint="https://metadata.example.invalid/feed",
        stop_timeout=0.05,
    )
    publisher = RemoteMetadataPublisher(config, factory)

    publisher.start()
    publisher.submit(metadata("Dispatch"))
    assert started.wait(timeout=1.0)

    started_at = monotonic()
    with pytest.raises(AudioOutputError, match="Timed out"):
        publisher.stop()
    assert monotonic() - started_at < 0.5

    publication.gate.set()
    wait_until(lambda: publisher.snapshot().state == "stopped")
