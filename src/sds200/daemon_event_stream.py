from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Protocol, cast

from .audio_sinks import (
    AudioFanoutSnapshot,
    PcmSubscriberTransition,
)
from .daemon_events import (
    DAEMON_EVENT_DEFAULT_MAX_BYTES,
    DAEMON_EVENT_DEFAULT_QUEUE_CAPACITY,
    DaemonEventKind,
    DaemonEventPublisher,
    DaemonEventSubscription,
)
from .daemon_runtime import (
    DaemonRuntime,
    DaemonRuntimeTransition,
)
from .models import ScannerInfo
from .state import RadioStateSnapshot, StateChange

logger = logging.getLogger(__name__)


class _ScannerEventSource(Protocol):
    @property
    def endpoint(self) -> str: ...

    @property
    def state(self) -> _RadioStateSource: ...

    def on_connection(
        self,
        callback: Callable[[bool], None],
    ) -> Callable[[], None]: ...

    def on_psi(
        self,
        callback: Callable[[ScannerInfo], None],
    ) -> Callable[[], None]: ...

    def on_state_change(
        self,
        callback: Callable[[StateChange], None],
    ) -> Callable[[], None]: ...


class _RadioStateSource(Protocol):
    @property
    def snapshot(self) -> RadioStateSnapshot: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


class DaemonEventStream:
    """Compose daemon runtime sources into one ordered event publisher."""

    def __init__(
        self,
        runtime: DaemonRuntime,
        *,
        queue_capacity: int = DAEMON_EVENT_DEFAULT_QUEUE_CAPACITY,
        max_subscribers: int = 32,
        max_event_bytes: int = DAEMON_EVENT_DEFAULT_MAX_BYTES,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.runtime = runtime
        self._lock = threading.RLock()
        self._closed = False
        self._publisher = DaemonEventPublisher(
            self._snapshot,
            queue_capacity=queue_capacity,
            max_subscribers=max_subscribers,
            max_event_bytes=max_event_bytes,
            now=now,
        )
        self._unsubscribes: tuple[Callable[[], None], ...] = ()

        scanner = cast(_ScannerEventSource, runtime.scanner)
        unsubscribes: list[Callable[[], None]] = []
        try:
            unsubscribes.append(
                runtime.on_transition(self._daemon_transition)
            )
            unsubscribes.append(
                scanner.on_connection(self._scanner_connection)
            )
            unsubscribes.append(
                scanner.on_psi(self._scanner_psi)
            )
            unsubscribes.append(
                scanner.on_state_change(self._radio_state)
            )
            unsubscribes.append(
                runtime.audio.on_state(self._audio_state)
            )
            unsubscribes.append(
                runtime.router.on_transition(self._destination_health)
            )
        except BaseException:
            for unsubscribe in reversed(unsubscribes):
                unsubscribe()
            self._publisher.close()
            self._closed = True
            raise

        self._unsubscribes = tuple(unsubscribes)

    @property
    def queue_capacity(self) -> int:
        return self._publisher.queue_capacity

    @property
    def max_subscribers(self) -> int:
        return self._publisher.max_subscribers

    @property
    def max_event_bytes(self) -> int:
        return self._publisher.max_event_bytes

    @property
    def sequence(self) -> int:
        return self._publisher.sequence

    @property
    def subscriber_count(self) -> int:
        return self._publisher.subscriber_count

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def subscribe(self) -> DaemonEventSubscription:
        return self._publisher.subscribe()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return

            self._closed = True
            unsubscribes = self._unsubscribes
            self._unsubscribes = ()

            for unsubscribe in reversed(unsubscribes):
                try:
                    unsubscribe()
                except Exception:
                    logger.exception(
                        "Daemon event source unsubscribe failed"
                    )

            self._publisher.close()

    def _snapshot(self) -> Mapping[str, object]:
        return self.runtime.snapshot().as_dict()

    def _publish(
        self,
        kind: DaemonEventKind,
        payload: Mapping[str, object],
        *,
        observed_at: datetime | None = None,
    ) -> None:
        with self._lock:
            if self._closed:
                return
            self._publisher.publish(
                kind,
                payload,
                observed_at=observed_at,
            )

    def _daemon_transition(
        self,
        transition: DaemonRuntimeTransition,
    ) -> None:
        self._publish(
            DaemonEventKind.DAEMON_TRANSITION,
            transition.as_dict(),
            observed_at=transition.observed_at,
        )

    def _scanner_connection(self, connected: bool) -> None:
        scanner = cast(_ScannerEventSource, self.runtime.scanner)
        self._publish(
            DaemonEventKind.SCANNER_CONNECTION,
            {
                "endpoint": scanner.endpoint,
                "connected": connected,
            },
        )

    def _scanner_psi(self, info: ScannerInfo) -> None:
        scanner = cast(_ScannerEventSource, self.runtime.scanner)
        self._publish(
            DaemonEventKind.PSI_STATE,
            {
                "command": info.command,
                "received_at": info.received_at.isoformat(),
                "state": asdict(scanner.state.snapshot),
            },
            observed_at=info.received_at,
        )

    def _radio_state(self, change: StateChange) -> None:
        self._publish(
            DaemonEventKind.RADIO_STATE,
            {
                "fields": sorted(change.fields),
                "previous": asdict(change.previous),
                "current": asdict(change.current),
            },
        )

    def _audio_state(self, snapshot: AudioFanoutSnapshot) -> None:
        self._publish(
            DaemonEventKind.AUDIO_STATE,
            asdict(snapshot),
        )

    def _destination_health(
        self,
        transition: PcmSubscriberTransition,
    ) -> None:
        self._publish(
            DaemonEventKind.DESTINATION_HEALTH,
            transition.as_dict(),
            observed_at=transition.observed_at,
        )
