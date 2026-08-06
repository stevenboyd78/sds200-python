from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Protocol, TypeAlias

from .audio_recording import PcmuWavRecorder
from .audio_sinks import (
    BufferedPlaybackSink,
    LocalPlaybackAdapterFactory,
    PcmSink,
    PcmSinkStatistics,
    PcmWavSink,
    SoundDevicePlaybackAdapter,
)
from .broadcastify import (
    create_broadcastify_metadata_publisher,
    create_broadcastify_sink,
)
from .daemon_destinations import (
    DaemonDestination,
    DaemonDestinationConfiguration,
    DaemonDestinationKind,
    DaemonDestinationReplacementPreview,
    DaemonPlaybackDestination,
    DaemonRecordingDestination,
    DaemonRemoteProfileDestination,
    preview_daemon_destination_replacement,
)
from .local_playback import (
    AlsaPlaybackAdapter,
    PipeWirePlaybackAdapter,
    PulseAudioPlaybackAdapter,
)
from .remote_audio_metadata_publisher import RemoteMetadataPublisher
from .remote_audio_profiles import (
    BroadcastifyDestinationProfile,
    RemoteAudioProfileStore,
)
from .state import RadioStateSnapshot

logger = logging.getLogger(__name__)

DaemonDestinationCleanupComponent: TypeAlias = Literal[
    "sink",
    "metadata",
]


class _RemoteAudioProfileStoreLike(Protocol):
    def get(self, name: str) -> BroadcastifyDestinationProfile: ...


class _NamedPcmSink:
    """Expose one constructed sink under its stable daemon destination name."""

    def __init__(self, name: str, delegate: PcmSink) -> None:
        self._name = name
        self.delegate = delegate

    @property
    def name(self) -> str:
        return self._name

    @property
    def running(self) -> bool:
        return self.delegate.running

    @property
    def statistics(self) -> PcmSinkStatistics:
        return self.delegate.statistics

    def start(self) -> None:
        self.delegate.start()

    def submit_pcm(self, data: bytes) -> None:
        self.delegate.submit_pcm(data)

    def stop(self) -> None:
        self.delegate.stop()


@dataclass(frozen=True, slots=True)
class DaemonDestinationResources:
    """Unstarted resources constructed for one desired daemon destination."""

    destination: DaemonDestination
    sink: PcmSink
    metadata_publisher: RemoteMetadataPublisher | None = None

    @property
    def name(self) -> str:
        return self.destination.name

    @property
    def kind(self) -> DaemonDestinationKind:
        return self.destination.kind


class DaemonDestinationFactory:
    """Construct daemon-owned destination resources without starting them."""

    def __init__(
        self,
        *,
        remote_profile_store: _RemoteAudioProfileStoreLike | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.remote_profile_store = (
            RemoteAudioProfileStore()
            if remote_profile_store is None
            else remote_profile_store
        )
        self.environ = (
            None
            if environ is None
            else MappingProxyType(dict(environ))
        )

    def build(
        self,
        destination: DaemonDestination,
    ) -> DaemonDestinationResources:
        if isinstance(destination, DaemonPlaybackDestination):
            return self._build_playback(destination)
        if isinstance(destination, DaemonRecordingDestination):
            return self._build_recording(destination)
        if isinstance(destination, DaemonRemoteProfileDestination):
            return self._build_remote_profile(destination)
        raise TypeError(
            "Daemon destination factories require a typed destination."
        )

    def _build_playback(
        self,
        destination: DaemonPlaybackDestination,
    ) -> DaemonDestinationResources:
        adapter_factory = self._playback_adapter_factory(destination)
        sink = BufferedPlaybackSink(
            name=f"daemon:{destination.name}",
            adapter_factory=adapter_factory,
            buffer_ms=destination.buffer_ms,
        )
        return DaemonDestinationResources(destination, sink)

    def _playback_adapter_factory(
        self,
        destination: DaemonPlaybackDestination,
    ) -> LocalPlaybackAdapterFactory:
        backend = destination.backend
        device = destination.device

        if backend in {"auto", "sounddevice"}:
            return lambda: SoundDevicePlaybackAdapter(device=device)

        if isinstance(device, int):
            raise ValueError(
                "Daemon command playback backends require a string "
                "device or null."
            )
        text_device = device

        if backend == "pipewire":
            return lambda: PipeWirePlaybackAdapter(target=text_device)
        if backend == "pulseaudio":
            return lambda: PulseAudioPlaybackAdapter(device=text_device)
        if backend == "alsa":
            return lambda: AlsaPlaybackAdapter(device=text_device)

        raise AssertionError(
            f"Unsupported validated daemon playback backend: {backend}"
        )

    def _build_recording(
        self,
        destination: DaemonRecordingDestination,
    ) -> DaemonDestinationResources:
        recorder = PcmuWavRecorder(
            destination.path,
            overwrite=destination.overwrite,
        )
        delegate = PcmWavSink(
            recorder,
            buffer_seconds=destination.buffer_seconds,
        )
        sink = _NamedPcmSink(
            f"daemon:{destination.name}",
            delegate,
        )
        return DaemonDestinationResources(destination, sink)

    def _build_remote_profile(
        self,
        destination: DaemonRemoteProfileDestination,
    ) -> DaemonDestinationResources:
        profile = self.remote_profile_store.get(destination.profile)
        config = profile.to_broadcastify_config()

        delegate = create_broadcastify_sink(
            config,
            environ=self.environ,
        )
        sink = _NamedPcmSink(
            f"daemon:{destination.name}",
            delegate,
        )

        metadata_publisher = (
            create_broadcastify_metadata_publisher(
                config,
                environ=self.environ,
                minimum_update_interval=(
                    destination.metadata_minimum_update_interval
                ),
            )
            if destination.publish_metadata
            else None
        )
        return DaemonDestinationResources(
            destination,
            sink,
            metadata_publisher,
        )


class _DaemonDestinationFactoryLike(Protocol):
    def build(
        self,
        destination: DaemonDestination,
    ) -> DaemonDestinationResources: ...


class _RadioStateLike(Protocol):
    @property
    def snapshot(self) -> RadioStateSnapshot: ...


class _DestinationScannerLike(Protocol):
    @property
    def connected(self) -> bool: ...

    @property
    def state(self) -> _RadioStateLike: ...

    def on_state(
        self,
        callback: Callable[[RadioStateSnapshot], None],
    ) -> Callable[[], None]: ...

    def on_connection(
        self,
        callback: Callable[[bool], None],
    ) -> Callable[[], None]: ...


class _DestinationRuntimeLike(Protocol):
    @property
    def scanner(self) -> _DestinationScannerLike: ...

    def attach_sink(self, sink: PcmSink) -> None: ...

    def detach_sink(
        self,
        sink: PcmSink,
        *,
        stop: bool = True,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class DaemonDestinationCleanupFailure:
    """Redacted post-commit cleanup failure for one old resource."""

    name: str
    component: DaemonDestinationCleanupComponent
    error_type: str

    def __post_init__(self) -> None:
        if not self.name or self.name.strip() != self.name:
            raise ValueError(
                "Daemon destination cleanup failure names must not be "
                "empty or padded."
            )
        if self.component not in {"sink", "metadata"}:
            raise ValueError(
                "Unsupported daemon destination cleanup component."
            )
        if not self.error_type or self.error_type.strip() != self.error_type:
            raise ValueError(
                "Daemon destination cleanup error types must not be "
                "empty or padded."
            )

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "component": self.component,
            "error_type": self.error_type,
        }


@dataclass(frozen=True, slots=True)
class DaemonDestinationReplacementResult:
    """Committed destination replacement and isolated cleanup outcome."""

    preview: DaemonDestinationReplacementPreview
    configuration: DaemonDestinationConfiguration
    cleanup_failures: tuple[DaemonDestinationCleanupFailure, ...] = ()

    @property
    def changed(self) -> bool:
        return self.preview.changed

    @property
    def clean(self) -> bool:
        return not self.cleanup_failures

    def as_dict(self) -> dict[str, object]:
        return {
            "changed": self.changed,
            "clean": self.clean,
            "preview": self.preview.as_dict(),
            "configuration": self.configuration.as_dict(),
            "cleanup_failures": [
                failure.as_dict()
                for failure in self.cleanup_failures
            ],
        }


@dataclass(slots=True)
class _StagedDestinationActivation:
    resources: DaemonDestinationResources
    previous: DaemonDestinationResources | None
    previous_detached: bool = False
    sink_attempted: bool = False
    metadata_attempted: bool = False


class DaemonDestinationCoordinator:
    """Activate and replace daemon-owned destinations transactionally."""

    def __init__(
        self,
        runtime: _DestinationRuntimeLike,
        *,
        factory: _DaemonDestinationFactoryLike | None = None,
        initial_configuration: (
            DaemonDestinationConfiguration | None
        ) = None,
    ) -> None:
        if (
            initial_configuration is not None
            and not isinstance(
                initial_configuration,
                DaemonDestinationConfiguration,
            )
        ):
            raise TypeError(
                "Initial daemon destinations must be a "
                "DaemonDestinationConfiguration."
            )

        self.runtime = runtime
        self.factory = (
            DaemonDestinationFactory()
            if factory is None
            else factory
        )
        self._lock = threading.RLock()
        self._initial_configuration = (
            DaemonDestinationConfiguration()
            if initial_configuration is None
            else initial_configuration
        )
        self._configuration = DaemonDestinationConfiguration()
        self._resources: dict[str, DaemonDestinationResources] = {}
        self._started = False
        self._closed = False
        self._unsubscribes: tuple[Callable[[], None], ...] = ()

        unsubscribes: list[Callable[[], None]] = []
        try:
            unsubscribes.append(
                runtime.scanner.on_state(self._radio_state)
            )
            unsubscribes.append(
                runtime.scanner.on_connection(self._connection_state)
            )
        except BaseException:
            for unsubscribe in reversed(unsubscribes):
                unsubscribe()
            raise
        self._unsubscribes = tuple(unsubscribes)

    @property
    def configuration(self) -> DaemonDestinationConfiguration:
        with self._lock:
            return self._configuration

    @property
    def resources(self) -> tuple[DaemonDestinationResources, ...]:
        with self._lock:
            return tuple(
                self._resources[name]
                for name in sorted(self._resources)
            )

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def start(self) -> DaemonDestinationReplacementResult:
        with self._lock:
            self._require_open_locked()
            if self._started:
                return DaemonDestinationReplacementResult(
                    preview_daemon_destination_replacement(
                        self._configuration,
                        self._configuration,
                    ),
                    self._configuration,
                )

            result = self.replace(self._initial_configuration)
            self._started = True
            return result

    def stop(self) -> None:
        self.close()

    def preview(
        self,
        replacement: DaemonDestinationConfiguration,
    ) -> DaemonDestinationReplacementPreview:
        with self._lock:
            self._require_open_locked()
            return preview_daemon_destination_replacement(
                self._configuration,
                replacement,
            )

    def replace(
        self,
        replacement: DaemonDestinationConfiguration,
    ) -> DaemonDestinationReplacementResult:
        if not isinstance(replacement, DaemonDestinationConfiguration):
            raise TypeError(
                "Daemon destination replacements require a "
                "DaemonDestinationConfiguration."
            )

        with self._lock:
            self._require_open_locked()
            preview = preview_daemon_destination_replacement(
                self._configuration,
                replacement,
            )
            if not preview.changed:
                return DaemonDestinationReplacementResult(
                    preview,
                    self._configuration,
                )

            candidates: dict[str, DaemonDestinationResources] = {}
            for change in preview.changes:
                if change.action not in {"added", "replaced"}:
                    continue
                desired = change.after
                assert desired is not None
                candidates[change.name] = self.factory.build(desired)

            staged: list[_StagedDestinationActivation] = []
            try:
                for change in preview.changes:
                    if change.action not in {"added", "replaced"}:
                        continue

                    candidate = candidates[change.name]
                    previous = self._resources.get(change.name)
                    stage = _StagedDestinationActivation(
                        candidate,
                        previous,
                    )
                    staged.append(stage)

                    if previous is not None:
                        self.runtime.detach_sink(
                            previous.sink,
                            stop=False,
                        )
                        stage.previous_detached = True

                    stage.sink_attempted = True
                    self.runtime.attach_sink(candidate.sink)

                    publisher = candidate.metadata_publisher
                    if publisher is not None:
                        stage.metadata_attempted = True
                        publisher.start()
                        self._submit_current_metadata(publisher)
            except BaseException as error:
                rollback_failures = self._rollback(staged)
                if rollback_failures:
                    logger.error(
                        "daemon destination activation rollback failed "
                        "activation_error=%s rollback_errors=%s",
                        error.__class__.__name__,
                        ",".join(
                            rollback_error.__class__.__name__
                            for rollback_error in rollback_failures
                        ),
                    )
                raise

            previous_resources = self._resources
            committed = dict(previous_resources)
            for name in preview.names_for("removed"):
                committed.pop(name)
            for name in (
                *preview.names_for("added"),
                *preview.names_for("replaced"),
            ):
                committed[name] = candidates[name]

            self._configuration = replacement
            self._resources = committed

            cleanup_failures: list[
                DaemonDestinationCleanupFailure
            ] = []
            for change in reversed(preview.changes):
                if change.action not in {"removed", "replaced"}:
                    continue
                previous = previous_resources[change.name]
                cleanup_failures.extend(
                    self._cleanup_resource(
                        previous,
                        attached=change.action == "removed",
                    )
                )

            return DaemonDestinationReplacementResult(
                preview,
                replacement,
                tuple(cleanup_failures),
            )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True

            unsubscribes = self._unsubscribes
            self._unsubscribes = ()
            resources = tuple(
                self._resources[name]
                for name in sorted(self._resources, reverse=True)
            )
            self._resources = {}
            self._configuration = DaemonDestinationConfiguration()
            self._started = False

            for unsubscribe in reversed(unsubscribes):
                try:
                    unsubscribe()
                except Exception as error:
                    logger.warning(
                        "daemon destination unsubscribe failed error=%s",
                        error.__class__.__name__,
                    )

            failures: list[DaemonDestinationCleanupFailure] = []
            for resource in resources:
                failures.extend(
                    self._cleanup_resource(resource, attached=True)
                )

        if failures:
            first = failures[0]
            raise RuntimeError(
                "Could not fully stop daemon destination "
                f"{first.name!r}: {first.error_type}."
            )

    def _require_open_locked(self) -> None:
        if self._closed:
            raise RuntimeError(
                "Daemon destination coordinators cannot be used after close."
            )

    def _submit_current_metadata(
        self,
        publisher: RemoteMetadataPublisher,
    ) -> None:
        publisher.submit_radio_state(
            self.runtime.scanner.state.snapshot,
            connected=self.runtime.scanner.connected,
        )

    def _radio_state(self, snapshot: RadioStateSnapshot) -> None:
        with self._lock:
            if self._closed:
                return
            connected = self.runtime.scanner.connected
            publishers = self._metadata_publishers_locked()

        for name, publisher in publishers:
            try:
                publisher.submit_radio_state(
                    snapshot,
                    connected=connected,
                )
            except Exception as error:
                logger.warning(
                    "daemon destination metadata submission failed "
                    "destination=%s error=%s",
                    name,
                    error.__class__.__name__,
                )

    def _connection_state(self, connected: bool) -> None:
        with self._lock:
            if self._closed:
                return
            snapshot = self.runtime.scanner.state.snapshot
            publishers = self._metadata_publishers_locked()

        for name, publisher in publishers:
            try:
                publisher.submit_radio_state(
                    snapshot,
                    connected=connected,
                )
            except Exception as error:
                logger.warning(
                    "daemon destination metadata connection update failed "
                    "destination=%s error=%s",
                    name,
                    error.__class__.__name__,
                )

    def _metadata_publishers_locked(
        self,
    ) -> tuple[tuple[str, RemoteMetadataPublisher], ...]:
        output: list[tuple[str, RemoteMetadataPublisher]] = []
        for name in sorted(self._resources):
            publisher = self._resources[name].metadata_publisher
            if publisher is not None:
                output.append((name, publisher))
        return tuple(output)

    def _rollback(
        self,
        staged: list[_StagedDestinationActivation],
    ) -> list[BaseException]:
        failures: list[BaseException] = []
        for stage in reversed(staged):
            publisher = stage.resources.metadata_publisher
            if stage.metadata_attempted and publisher is not None:
                try:
                    publisher.stop()
                except BaseException as error:
                    failures.append(error)

            if stage.sink_attempted:
                try:
                    self.runtime.detach_sink(
                        stage.resources.sink,
                        stop=True,
                    )
                except BaseException as error:
                    failures.append(error)

            if (
                stage.previous_detached
                and stage.previous is not None
            ):
                try:
                    self.runtime.attach_sink(stage.previous.sink)
                except BaseException as error:
                    failures.append(error)
        return failures

    def _cleanup_resource(
        self,
        resources: DaemonDestinationResources,
        *,
        attached: bool,
    ) -> tuple[DaemonDestinationCleanupFailure, ...]:
        failures: list[DaemonDestinationCleanupFailure] = []
        publisher = resources.metadata_publisher

        if publisher is not None:
            try:
                publisher.stop()
            except BaseException as error:
                failures.append(
                    DaemonDestinationCleanupFailure(
                        resources.name,
                        "metadata",
                        error.__class__.__name__,
                    )
                )
                logger.warning(
                    "daemon destination metadata cleanup failed "
                    "destination=%s error=%s",
                    resources.name,
                    error.__class__.__name__,
                )

        try:
            if attached:
                self.runtime.detach_sink(
                    resources.sink,
                    stop=True,
                )
            else:
                resources.sink.stop()
        except BaseException as error:
            failures.append(
                DaemonDestinationCleanupFailure(
                    resources.name,
                    "sink",
                    error.__class__.__name__,
                )
            )
            logger.warning(
                "daemon destination sink cleanup failed "
                "destination=%s error=%s",
                resources.name,
                error.__class__.__name__,
            )

        return tuple(failures)
