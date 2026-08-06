from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from sds200 import (
    DaemonDestinationConfiguration,
    DaemonDestinationCoordinator,
    DaemonDestinationReplacementResult,
    DaemonDestinationResources,
    DaemonPlaybackDestination,
    DaemonRecordingDestination,
    PcmSinkStatistics,
)
from sds200.state import RadioStateSnapshot


class FakeState:
    def __init__(self) -> None:
        self.snapshot = RadioStateSnapshot(
            system="County",
            channel="Dispatch",
        )


class FakeScanner:
    def __init__(self) -> None:
        self.connected = True
        self.state = FakeState()
        self._state_callbacks: list[
            Callable[[RadioStateSnapshot], None]
        ] = []
        self._connection_callbacks: list[Callable[[bool], None]] = []

    def on_state(
        self,
        callback: Callable[[RadioStateSnapshot], None],
    ) -> Callable[[], None]:
        self._state_callbacks.append(callback)

        def unsubscribe() -> None:
            if callback in self._state_callbacks:
                self._state_callbacks.remove(callback)

        return unsubscribe

    def on_connection(
        self,
        callback: Callable[[bool], None],
    ) -> Callable[[], None]:
        self._connection_callbacks.append(callback)

        def unsubscribe() -> None:
            if callback in self._connection_callbacks:
                self._connection_callbacks.remove(callback)

        return unsubscribe

    def emit_state(self, snapshot: RadioStateSnapshot) -> None:
        self.state.snapshot = snapshot
        for callback in tuple(self._state_callbacks):
            callback(snapshot)

    def emit_connection(self, connected: bool) -> None:
        self.connected = connected
        for callback in tuple(self._connection_callbacks):
            callback(connected)


class FakeSink:
    def __init__(
        self,
        name: str,
        order: list[str],
        *,
        fail_start: bool = False,
        fail_stop: bool = False,
    ) -> None:
        self._name = name
        self.order = order
        self.fail_start = fail_start
        self.fail_stop = fail_stop
        self._running = False
        self.start_calls = 0
        self.stop_calls = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def running(self) -> bool:
        return self._running

    @property
    def statistics(self) -> PcmSinkStatistics:
        return PcmSinkStatistics()

    def start(self) -> None:
        self.order.append(f"sink.start:{self.name}")
        self.start_calls += 1
        if self.fail_start:
            raise RuntimeError("secret sink startup detail")
        self._running = True

    def submit_pcm(self, data: bytes) -> None:
        del data

    def stop(self) -> None:
        self.order.append(f"sink.stop:{self.name}")
        self.stop_calls += 1
        self._running = False
        if self.fail_stop:
            raise RuntimeError("secret sink shutdown detail")


class FakeMetadataPublisher:
    def __init__(
        self,
        name: str,
        order: list[str],
        *,
        fail_start: bool = False,
        fail_stop: bool = False,
    ) -> None:
        self.name = name
        self.order = order
        self.fail_start = fail_start
        self.fail_stop = fail_stop
        self.running = False
        self.submissions: list[
            tuple[RadioStateSnapshot, bool | None]
        ] = []

    def start(self) -> None:
        self.order.append(f"metadata.start:{self.name}")
        if self.fail_start:
            raise RuntimeError("secret metadata startup detail")
        self.running = True

    def submit_radio_state(
        self,
        snapshot: RadioStateSnapshot,
        *,
        connected: bool | None = None,
        degraded: bool = False,
        stale: bool = False,
    ) -> object:
        del degraded, stale
        if not self.running:
            raise RuntimeError("metadata publisher is not running")
        self.order.append(f"metadata.submit:{self.name}")
        self.submissions.append((snapshot, connected))
        return object()

    def stop(self) -> None:
        self.order.append(f"metadata.stop:{self.name}")
        self.running = False
        if self.fail_stop:
            raise RuntimeError("secret metadata shutdown detail")


class FakeRuntime:
    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.scanner = FakeScanner()
        self.attached: list[FakeSink] = []

    def attach_sink(self, sink: FakeSink) -> None:
        self.order.append(f"runtime.attach:{sink.name}")
        sink.start()
        if sink not in self.attached:
            self.attached.append(sink)

    def detach_sink(
        self,
        sink: FakeSink,
        *,
        stop: bool = True,
    ) -> None:
        self.order.append(
            f"runtime.detach:{sink.name}:stop={str(stop).lower()}"
        )
        if sink in self.attached:
            self.attached.remove(sink)
        if stop:
            sink.stop()


class FakeFactory:
    def __init__(self) -> None:
        self.entries: dict[
            str,
            DaemonDestinationResources | BaseException,
        ] = {}
        self.builds: list[str] = []

    def build(
        self,
        destination: object,
    ) -> DaemonDestinationResources:
        name = destination.name  # type: ignore[union-attr]
        self.builds.append(name)
        entry = self.entries[name]
        if isinstance(entry, BaseException):
            raise entry
        return entry


def playback(name: str) -> DaemonPlaybackDestination:
    return DaemonPlaybackDestination(name=name)


def configuration(
    *destinations: DaemonPlaybackDestination | DaemonRecordingDestination,
) -> DaemonDestinationConfiguration:
    return DaemonDestinationConfiguration(destinations)


def resources(
    destination: DaemonPlaybackDestination | DaemonRecordingDestination,
    sink: FakeSink,
    publisher: FakeMetadataPublisher | None = None,
) -> DaemonDestinationResources:
    return DaemonDestinationResources(
        destination,
        sink,  # type: ignore[arg-type]
        publisher,  # type: ignore[arg-type]
    )


def test_coordinator_start_activates_initial_configuration_once() -> None:
    order: list[str] = []
    runtime = FakeRuntime(order)
    factory = FakeFactory()
    destination = playback("speakers")
    sink = FakeSink("daemon:speakers", order)
    factory.entries["speakers"] = resources(destination, sink)

    coordinator = DaemonDestinationCoordinator(
        runtime,  # type: ignore[arg-type]
        factory=factory,
        initial_configuration=configuration(destination),
    )

    first = coordinator.start()
    second = coordinator.start()

    assert first.changed
    assert second.changed is False
    assert factory.builds == ["speakers"]
    assert runtime.attached == [sink]
    assert sink.start_calls == 1

    coordinator.stop()
    assert coordinator.closed
    assert not sink.running


def test_coordinator_start_can_retry_after_activation_failure() -> None:
    order: list[str] = []
    runtime = FakeRuntime(order)
    factory = FakeFactory()
    destination = playback("speakers")
    failing_sink = FakeSink(
        "daemon:speakers-failing",
        order,
        fail_start=True,
    )
    factory.entries["speakers"] = resources(
        destination,
        failing_sink,
    )
    coordinator = DaemonDestinationCoordinator(
        runtime,  # type: ignore[arg-type]
        factory=factory,
        initial_configuration=configuration(destination),
    )

    with pytest.raises(RuntimeError, match="secret sink startup"):
        coordinator.start()

    healthy_sink = FakeSink("daemon:speakers", order)
    factory.entries["speakers"] = resources(
        destination,
        healthy_sink,
    )

    result = coordinator.start()

    assert result.changed
    assert factory.builds == ["speakers", "speakers"]
    assert runtime.attached == [healthy_sink]
    assert healthy_sink.running

    coordinator.close()


def test_coordinator_activates_sorted_resources_and_initial_metadata() -> None:
    order: list[str] = []
    runtime = FakeRuntime(order)
    factory = FakeFactory()

    archive = playback("archive")
    feed = playback("feed")
    archive_sink = FakeSink("daemon:archive", order)
    feed_sink = FakeSink("daemon:feed", order)
    publisher = FakeMetadataPublisher("feed", order)

    factory.entries = {
        "archive": resources(archive, archive_sink),
        "feed": resources(feed, feed_sink, publisher),
    }
    coordinator = DaemonDestinationCoordinator(
        runtime,  # type: ignore[arg-type]
        factory=factory,
    )

    result = coordinator.replace(configuration(feed, archive))

    assert isinstance(result, DaemonDestinationReplacementResult)
    assert result.changed
    assert result.clean
    assert result.preview.names_for("added") == ("archive", "feed")
    assert coordinator.configuration == configuration(archive, feed)
    assert tuple(resource.name for resource in coordinator.resources) == (
        "archive",
        "feed",
    )
    assert factory.builds == ["archive", "feed"]
    assert runtime.attached == [archive_sink, feed_sink]
    assert publisher.running
    assert publisher.submissions == [
        (runtime.scanner.state.snapshot, True)
    ]
    json.dumps(result.as_dict())

    coordinator.close()


def test_coordinator_fans_out_state_and_connection_metadata() -> None:
    order: list[str] = []
    runtime = FakeRuntime(order)
    factory = FakeFactory()
    destination = playback("feed")
    sink = FakeSink("daemon:feed", order)
    publisher = FakeMetadataPublisher("feed", order)
    factory.entries["feed"] = resources(
        destination,
        sink,
        publisher,
    )
    coordinator = DaemonDestinationCoordinator(
        runtime,  # type: ignore[arg-type]
        factory=factory,
    )
    coordinator.replace(configuration(destination))
    publisher.submissions.clear()

    updated = RadioStateSnapshot(
        system="Metro",
        channel="Tac 1",
    )
    runtime.scanner.emit_state(updated)
    runtime.scanner.emit_connection(False)

    assert publisher.submissions == [
        (updated, True),
        (updated, False),
    ]

    coordinator.close()
    before = list(publisher.submissions)
    runtime.scanner.emit_state(
        RadioStateSnapshot(channel="After close")
    )
    runtime.scanner.emit_connection(True)

    assert publisher.submissions == before
    assert not publisher.running
    assert sink not in runtime.attached


def test_build_failure_has_no_runtime_side_effects() -> None:
    order: list[str] = []
    runtime = FakeRuntime(order)
    factory = FakeFactory()
    current = playback("current")
    current_sink = FakeSink("daemon:current", order)
    factory.entries["current"] = resources(current, current_sink)

    coordinator = DaemonDestinationCoordinator(
        runtime,  # type: ignore[arg-type]
        factory=factory,
    )
    coordinator.replace(configuration(current))
    order.clear()

    added = playback("added")
    broken = playback("broken")
    factory.entries["added"] = resources(
        added,
        FakeSink("daemon:added", order),
    )
    factory.entries["broken"] = RuntimeError(
        "secret construction detail"
    )

    with pytest.raises(RuntimeError, match="secret construction"):
        coordinator.replace(configuration(current, broken, added))

    assert order == []
    assert coordinator.configuration == configuration(current)
    assert runtime.attached == [current_sink]
    assert current_sink.running

    coordinator.close()


def test_activation_failure_rolls_back_additions_and_replacement() -> None:
    order: list[str] = []
    runtime = FakeRuntime(order)
    factory = FakeFactory()

    current = playback("beta")
    old_sink = FakeSink("daemon:beta-old", order)
    factory.entries["beta"] = resources(current, old_sink)
    coordinator = DaemonDestinationCoordinator(
        runtime,  # type: ignore[arg-type]
        factory=factory,
    )
    coordinator.replace(configuration(current))
    order.clear()

    alpha = playback("alpha")
    replacement = DaemonPlaybackDestination(
        name="beta",
        buffer_ms=500,
    )
    alpha_sink = FakeSink("daemon:alpha", order)
    failing_sink = FakeSink(
        "daemon:beta-new",
        order,
        fail_start=True,
    )
    factory.entries["alpha"] = resources(alpha, alpha_sink)
    factory.entries["beta"] = resources(replacement, failing_sink)

    with pytest.raises(RuntimeError, match="secret sink startup"):
        coordinator.replace(configuration(alpha, replacement))

    assert coordinator.configuration == configuration(current)
    assert runtime.attached == [old_sink]
    assert old_sink.running
    assert not alpha_sink.running
    assert not failing_sink.running
    assert "runtime.detach:daemon:beta-old:stop=false" in order
    assert "runtime.attach:daemon:beta-old" in order
    assert "runtime.detach:daemon:alpha:stop=true" in order

    coordinator.close()


def test_successful_replacement_commits_before_cleanup_failures() -> None:
    order: list[str] = []
    runtime = FakeRuntime(order)
    factory = FakeFactory()

    current = playback("feed")
    old_sink = FakeSink(
        "daemon:feed-old",
        order,
        fail_stop=True,
    )
    old_publisher = FakeMetadataPublisher(
        "feed-old",
        order,
        fail_stop=True,
    )
    factory.entries["feed"] = resources(
        current,
        old_sink,
        old_publisher,
    )
    coordinator = DaemonDestinationCoordinator(
        runtime,  # type: ignore[arg-type]
        factory=factory,
    )
    coordinator.replace(configuration(current))
    order.clear()

    replacement = DaemonPlaybackDestination(
        name="feed",
        buffer_ms=500,
    )
    new_sink = FakeSink("daemon:feed-new", order)
    new_publisher = FakeMetadataPublisher("feed-new", order)
    factory.entries["feed"] = resources(
        replacement,
        new_sink,
        new_publisher,
    )

    result = coordinator.replace(configuration(replacement))

    assert coordinator.configuration == configuration(replacement)
    assert runtime.attached == [new_sink]
    assert new_sink.running
    assert new_publisher.running
    assert not result.clean
    assert [
        (failure.name, failure.component, failure.error_type)
        for failure in result.cleanup_failures
    ] == [
        ("feed", "metadata", "RuntimeError"),
        ("feed", "sink", "RuntimeError"),
    ]
    assert "secret" not in repr(result)
    json.dumps(result.as_dict())

    coordinator.close()


def test_removal_detaches_and_stops_resource() -> None:
    order: list[str] = []
    runtime = FakeRuntime(order)
    factory = FakeFactory()
    destination = playback("speakers")
    sink = FakeSink("daemon:speakers", order)
    factory.entries["speakers"] = resources(destination, sink)

    coordinator = DaemonDestinationCoordinator(
        runtime,  # type: ignore[arg-type]
        factory=factory,
    )
    coordinator.replace(configuration(destination))
    order.clear()

    result = coordinator.replace(DaemonDestinationConfiguration())

    assert result.preview.names_for("removed") == ("speakers",)
    assert result.clean
    assert coordinator.resources == ()
    assert runtime.attached == []
    assert not sink.running
    assert order == [
        "runtime.detach:daemon:speakers:stop=true",
        "sink.stop:daemon:speakers",
    ]

    coordinator.close()


def test_unchanged_replacement_does_not_rebuild_resources() -> None:
    order: list[str] = []
    runtime = FakeRuntime(order)
    factory = FakeFactory()
    destination = playback("speakers")
    sink = FakeSink("daemon:speakers", order)
    factory.entries["speakers"] = resources(destination, sink)

    coordinator = DaemonDestinationCoordinator(
        runtime,  # type: ignore[arg-type]
        factory=factory,
    )
    coordinator.replace(configuration(destination))
    factory.builds.clear()
    order.clear()

    result = coordinator.replace(configuration(destination))

    assert not result.changed
    assert result.clean
    assert factory.builds == []
    assert order == []
    assert runtime.attached == [sink]

    coordinator.close()


def test_close_is_idempotent_and_prevents_reuse() -> None:
    order: list[str] = []
    runtime = FakeRuntime(order)
    factory = FakeFactory()
    destination = DaemonRecordingDestination(
        name="archive",
        path=Path("/tmp/archive.wav"),
    )
    sink = FakeSink("daemon:archive", order)
    factory.entries["archive"] = resources(destination, sink)

    coordinator = DaemonDestinationCoordinator(
        runtime,  # type: ignore[arg-type]
        factory=factory,
    )
    coordinator.replace(configuration(destination))

    coordinator.close()
    coordinator.close()

    assert coordinator.closed
    assert coordinator.configuration == DaemonDestinationConfiguration()
    assert coordinator.resources == ()

    with pytest.raises(RuntimeError, match="after close"):
        coordinator.preview(DaemonDestinationConfiguration())

    with pytest.raises(RuntimeError, match="after close"):
        coordinator.replace(DaemonDestinationConfiguration())
