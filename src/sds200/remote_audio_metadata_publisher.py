from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from types import MappingProxyType
from typing import Literal, Protocol, runtime_checkable
from urllib.parse import urlsplit

from .exceptions import AudioOutputError
from .reliability import ReconnectPolicy
from .remote_audio import EnvironmentSecret
from .remote_audio_metadata import (
    RemoteStreamMetadata,
    remote_stream_metadata_from_state,
)
from .state import RadioStateSnapshot

logger = logging.getLogger(__name__)

RemoteMetadataPublisherState = Literal[
    "idle",
    "running",
    "backoff",
    "failed",
    "stopping",
    "stopped",
]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _require_aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(
            "Remote metadata wall clock must return a timezone-aware datetime."
        )
    return value


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _validate_text(label: str, value: str) -> None:
    if not value or value.strip() != value:
        raise ValueError(f"{label} must not be empty or padded.")
    if "\n" in value or "\r" in value:
        raise ValueError(f"{label} must not contain line breaks.")


def _redact_error(
    error: BaseException,
    secret_values: tuple[str, ...],
) -> str:
    message = f"{type(error).__name__}: {error}"
    for value in sorted(
        (secret for secret in secret_values if secret),
        key=len,
        reverse=True,
    ):
        message = message.replace(value, "<redacted>")
    return message


@dataclass(frozen=True, slots=True)
class RemoteMetadataPublisherConfig:
    """Service-neutral settings for one optional metadata destination."""

    name: str
    endpoint: str
    secrets: Mapping[str, EnvironmentSecret] = field(default_factory=dict)
    minimum_update_interval: float = 0.0
    stop_timeout: float = 5.0
    reconnect_policy: ReconnectPolicy = field(default_factory=ReconnectPolicy)

    def __post_init__(self) -> None:
        _validate_text("Remote metadata destination name", self.name)
        _validate_text("Remote metadata destination endpoint", self.endpoint)
        parsed = urlsplit(self.endpoint)
        if parsed.username is not None or parsed.password is not None:
            raise ValueError(
                "Remote metadata destination endpoints must not contain "
                "embedded credentials."
            )
        if self.minimum_update_interval < 0:
            raise ValueError(
                "Remote metadata minimum update interval must not be negative."
            )
        if self.stop_timeout <= 0:
            raise ValueError(
                "Remote metadata publisher stop timeout must be greater than zero."
            )

        copied_secrets = dict(self.secrets)
        for key, reference in copied_secrets.items():
            _validate_text("Remote metadata secret name", key)
            if not isinstance(reference, EnvironmentSecret):
                raise TypeError(
                    "Remote metadata secrets must contain "
                    "EnvironmentSecret values."
                )
        object.__setattr__(
            self,
            "secrets",
            MappingProxyType(copied_secrets),
        )

    def resolve_secrets(
        self,
        environ: Mapping[str, str] | None = None,
    ) -> Mapping[str, str]:
        return MappingProxyType(
            {
                name: reference.resolve(environ)
                for name, reference in self.secrets.items()
            }
        )


@runtime_checkable
class RemoteMetadataPublication(Protocol):
    """One interruptible blocking metadata publication attempt."""

    def publish(self) -> None: ...

    def interrupt(self) -> None:
        """Promptly interrupt an in-flight publication."""
        ...

    def close(self) -> None: ...


RemoteMetadataPublicationFactory = Callable[
    [
        RemoteMetadataPublisherConfig,
        Mapping[str, str],
        RemoteStreamMetadata,
    ],
    RemoteMetadataPublication,
]


@dataclass(frozen=True, slots=True)
class RemoteMetadataPublisherSnapshot:
    """Immutable operational metrics for one metadata publisher worker."""

    name: str
    endpoint: str
    state: RemoteMetadataPublisherState
    running: bool
    submissions: int
    publications: int
    duplicates_suppressed: int
    superseded: int
    attempts: int
    failures: int
    retry_attempt: int
    next_retry_delay: float | None
    pending_title: str | None
    last_published_title: str | None
    state_changed_at: datetime
    last_submitted_at: datetime | None
    last_published_at: datetime | None
    last_failure_at: datetime | None
    last_error: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "endpoint": self.endpoint,
            "state": self.state,
            "running": self.running,
            "submissions": self.submissions,
            "publications": self.publications,
            "duplicates_suppressed": self.duplicates_suppressed,
            "superseded": self.superseded,
            "attempts": self.attempts,
            "failures": self.failures,
            "retry_attempt": self.retry_attempt,
            "next_retry_delay": self.next_retry_delay,
            "pending_title": self.pending_title,
            "last_published_title": self.last_published_title,
            "state_changed_at": self.state_changed_at.isoformat(),
            "last_submitted_at": _isoformat(self.last_submitted_at),
            "last_published_at": _isoformat(self.last_published_at),
            "last_failure_at": _isoformat(self.last_failure_at),
            "last_error": self.last_error,
        }


class RemoteMetadataPublisher:
    """Publish newest scanner metadata without blocking PSI callbacks."""

    def __init__(
        self,
        config: RemoteMetadataPublisherConfig,
        publication_factory: RemoteMetadataPublicationFactory,
        *,
        environ: Mapping[str, str] | None = None,
        now: Callable[[], datetime] = _utc_now,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.config = config
        self._publication_factory = publication_factory
        self._environ = environ
        self._now = now
        self._clock = clock

        initial_state_at = _require_aware_datetime(now())
        self._condition = threading.Condition(threading.RLock())
        self._thread: threading.Thread | None = None
        self._active_publication: RemoteMetadataPublication | None = None
        self._active_secret_values: tuple[str, ...] = ()
        self._pending: tuple[int, RemoteStreamMetadata, str] | None = None
        self._generation = 0
        self._started = False
        self._stopping = False
        self._stopped = False
        self._state: RemoteMetadataPublisherState = "idle"
        self._state_changed_at = initial_state_at
        self._submissions = 0
        self._publications = 0
        self._duplicates_suppressed = 0
        self._superseded = 0
        self._attempts = 0
        self._failures = 0
        self._retry_attempt = 0
        self._next_retry_delay: float | None = None
        self._last_published_title: str | None = None
        self._last_published_clock: float | None = None
        self._last_submitted_at: datetime | None = None
        self._last_published_at: datetime | None = None
        self._last_failure_at: datetime | None = None
        self._last_error: str | None = None

    @property
    def running(self) -> bool:
        with self._condition:
            thread = self._thread
            return thread is not None and thread.is_alive() and not self._stopped

    def snapshot(self) -> RemoteMetadataPublisherSnapshot:
        with self._condition:
            return self._snapshot_locked()

    def start(self) -> None:
        with self._condition:
            if self._started:
                if self._stopped:
                    raise RuntimeError(
                        "Remote metadata publishers can only be started once."
                    )
                return
            self._started = True
            self._set_state_locked("running")
            thread = threading.Thread(
                target=self._run,
                name=f"sds200-metadata-{self.config.name}",
                daemon=True,
            )
            self._thread = thread
            thread.start()

        logger.info(
            "remote metadata publisher started name=%s endpoint=%s",
            self.config.name,
            self.config.endpoint,
        )

    def submit(self, metadata: RemoteStreamMetadata) -> None:
        if not isinstance(metadata, RemoteStreamMetadata):
            raise TypeError(
                "Remote metadata publishers require RemoteStreamMetadata."
            )
        title = metadata.render_title()
        observed_at = _require_aware_datetime(self._now())

        with self._condition:
            thread = self._thread
            if (
                thread is None
                or not thread.is_alive()
                or self._stopping
                or self._stopped
            ):
                raise RuntimeError(
                    "Remote metadata publisher is not running."
                )

            self._submissions += 1
            self._last_submitted_at = observed_at
            pending_title = (
                None if self._pending is None else self._pending[2]
            )
            if title == pending_title or (
                self._pending is None
                and title == self._last_published_title
            ):
                self._duplicates_suppressed += 1
                return

            if self._pending is not None:
                self._superseded += 1

            self._generation += 1
            self._pending = (self._generation, metadata, title)
            self._retry_attempt = 0
            self._next_retry_delay = None
            if self._state in {"backoff", "failed"}:
                self._set_state_locked(
                    "running",
                    observed_at=observed_at,
                )
            self._condition.notify_all()

    def submit_radio_state(
        self,
        snapshot: RadioStateSnapshot,
        *,
        connected: bool | None = None,
        degraded: bool = False,
        stale: bool = False,
    ) -> RemoteStreamMetadata:
        """Derive and enqueue metadata without performing network I/O."""

        metadata = remote_stream_metadata_from_state(
            snapshot,
            connected=connected,
            degraded=degraded,
            stale=stale,
        )
        self.submit(metadata)
        return metadata

    def stop(self) -> None:
        with self._condition:
            if not self._started or self._stopped:
                return
            if not self._stopping:
                self._stopping = True
                self._pending = None
                self._generation += 1
                self._set_state_locked("stopping")
                self._condition.notify_all()
            thread = self._thread
            publication = self._active_publication
            secret_values = self._active_secret_values

        if publication is not None:
            try:
                publication.interrupt()
            except Exception as error:
                safe_error = _redact_error(error, secret_values)
                observed_at = _require_aware_datetime(self._now())
                with self._condition:
                    self._failures += 1
                    self._last_failure_at = observed_at
                    self._last_error = safe_error
                logger.warning(
                    "remote metadata interrupt failed "
                    "name=%s endpoint=%s error=%s",
                    self.config.name,
                    self.config.endpoint,
                    safe_error,
                )

        if thread is threading.current_thread():
            return

        if thread is not None:
            thread.join(timeout=self.config.stop_timeout)
            if thread.is_alive():
                raise AudioOutputError(
                    "Timed out while stopping remote metadata destination "
                    f"{self.config.name!r}."
                )

        logger.info(
            "remote metadata publisher stopped name=%s endpoint=%s",
            self.config.name,
            self.config.endpoint,
        )

    def close(self) -> None:
        self.stop()

    def _run(self) -> None:
        try:
            while True:
                with self._condition:
                    while self._pending is None and not self._stopping:
                        self._condition.wait()
                    if self._stopping:
                        return

                    pending = self._pending
                    assert pending is not None
                    generation, metadata, title = pending

                    last_clock = self._last_published_clock
                    if last_clock is not None:
                        remaining = (
                            self.config.minimum_update_interval
                            - (self._clock() - last_clock)
                        )
                        if remaining > 0:
                            self._condition.wait(timeout=remaining)
                            continue

                    self._attempts += 1
                    self._set_state_locked("running")

                resolved_secrets: Mapping[str, str] = MappingProxyType({})
                secret_values: tuple[str, ...] = ()
                publication: RemoteMetadataPublication | None = None
                publication_error: Exception | None = None

                try:
                    resolved_secrets = self.config.resolve_secrets(
                        self._environ
                    )
                    secret_values = tuple(resolved_secrets.values())
                    publication = self._publication_factory(
                        self.config,
                        resolved_secrets,
                        metadata,
                    )
                    with self._condition:
                        if self._stopping:
                            publication.interrupt()
                            return
                        self._active_publication = publication
                        self._active_secret_values = secret_values
                    publication.publish()
                except Exception as error:
                    publication_error = error
                finally:
                    if publication is not None:
                        try:
                            publication.close()
                        except Exception as error:
                            if publication_error is None:
                                publication_error = error
                        with self._condition:
                            if self._active_publication is publication:
                                self._active_publication = None
                                self._active_secret_values = ()

                with self._condition:
                    if self._stopping:
                        return

                if publication_error is not None:
                    if not self._handle_failure(
                        publication_error,
                        secret_values,
                        generation,
                    ):
                        return
                    continue

                observed_at = _require_aware_datetime(self._now())
                published_clock = self._clock()
                with self._condition:
                    self._publications += 1
                    self._last_published_title = title
                    self._last_published_at = observed_at
                    self._last_published_clock = published_clock
                    self._retry_attempt = 0
                    self._next_retry_delay = None
                    if (
                        self._pending is not None
                        and self._pending[0] == generation
                    ):
                        self._pending = None
                    self._set_state_locked(
                        "running",
                        observed_at=observed_at,
                    )
                    self._condition.notify_all()
        finally:
            with self._condition:
                self._active_publication = None
                self._active_secret_values = ()
                self._thread = None
                self._stopped = True
                self._set_state_locked("stopped")
                self._condition.notify_all()

    def _handle_failure(
        self,
        error: BaseException,
        secret_values: tuple[str, ...],
        generation: int,
    ) -> bool:
        safe_error = _redact_error(error, secret_values)
        observed_at = _require_aware_datetime(self._now())

        with self._condition:
            if self._stopping:
                return False

            self._failures += 1
            self._last_failure_at = observed_at
            self._last_error = safe_error

            if (
                self._pending is None
                or self._pending[0] != generation
            ):
                self._retry_attempt = 0
                self._next_retry_delay = None
                self._set_state_locked(
                    "running",
                    observed_at=observed_at,
                )
                return True

            retry_attempt = self._retry_attempt + 1
            self._retry_attempt = retry_attempt
            policy = self.config.reconnect_policy

            if not policy.allows(retry_attempt):
                self._pending = None
                self._next_retry_delay = None
                self._set_state_locked(
                    "failed",
                    observed_at=observed_at,
                )
                self._condition.notify_all()
                logger.error(
                    "remote metadata destination exhausted retries "
                    "name=%s endpoint=%s attempt=%d error=%s",
                    self.config.name,
                    self.config.endpoint,
                    retry_attempt,
                    safe_error,
                )
                return True

            delay = policy.delay_for(retry_attempt)
            self._next_retry_delay = delay
            self._set_state_locked(
                "backoff",
                observed_at=observed_at,
            )
            self._condition.notify_all()

        logger.warning(
            "remote metadata retry scheduled "
            "name=%s endpoint=%s attempt=%d delay=%.3f error=%s",
            self.config.name,
            self.config.endpoint,
            retry_attempt,
            delay,
            safe_error,
        )

        with self._condition:
            self._condition.wait_for(
                lambda: (
                    self._stopping
                    or self._pending is None
                    or self._pending[0] != generation
                ),
                timeout=delay,
            )
            if self._stopping:
                return False
            if (
                self._pending is None
                or self._pending[0] != generation
            ):
                self._retry_attempt = 0
                self._next_retry_delay = None
                self._set_state_locked("running")
            return True

    def _set_state_locked(
        self,
        state: RemoteMetadataPublisherState,
        *,
        observed_at: datetime | None = None,
    ) -> None:
        if state == self._state:
            return
        self._state = state
        self._state_changed_at = _require_aware_datetime(
            self._now() if observed_at is None else observed_at
        )

    def _snapshot_locked(self) -> RemoteMetadataPublisherSnapshot:
        thread = self._thread
        pending_title = (
            None if self._pending is None else self._pending[2]
        )
        return RemoteMetadataPublisherSnapshot(
            name=self.config.name,
            endpoint=self.config.endpoint,
            state=self._state,
            running=(
                thread is not None
                and thread.is_alive()
                and not self._stopped
            ),
            submissions=self._submissions,
            publications=self._publications,
            duplicates_suppressed=self._duplicates_suppressed,
            superseded=self._superseded,
            attempts=self._attempts,
            failures=self._failures,
            retry_attempt=self._retry_attempt,
            next_retry_delay=self._next_retry_delay,
            pending_title=pending_title,
            last_published_title=self._last_published_title,
            state_changed_at=self._state_changed_at,
            last_submitted_at=self._last_submitted_at,
            last_published_at=self._last_published_at,
            last_failure_at=self._last_failure_at,
            last_error=self._last_error,
        )
