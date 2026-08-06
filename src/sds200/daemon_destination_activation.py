from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

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
    DaemonDestinationKind,
    DaemonPlaybackDestination,
    DaemonRecordingDestination,
    DaemonRemoteProfileDestination,
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
