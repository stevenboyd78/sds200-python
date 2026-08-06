from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from sds200 import (
    BroadcastifyDestinationProfile,
    DaemonDestinationFactory,
    DaemonDestinationResources,
    DaemonPlaybackDestination,
    DaemonRecordingDestination,
    DaemonRemoteProfileDestination,
    PcmSinkStatistics,
)
from sds200 import daemon_destination_activation as activation
from sds200.exceptions import ProfileError


class FakeSink:
    def __init__(self, name: str) -> None:
        self._name = name
        self.start_calls = 0
        self.stop_calls = 0
        self.submissions: list[bytes] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def running(self) -> bool:
        return self.start_calls > self.stop_calls

    @property
    def statistics(self) -> PcmSinkStatistics:
        return PcmSinkStatistics(
            bytes_submitted=sum(
                len(data)
                for data in self.submissions
            )
        )

    def start(self) -> None:
        self.start_calls += 1

    def submit_pcm(self, data: bytes) -> None:
        self.submissions.append(data)

    def stop(self) -> None:
        self.stop_calls += 1


class FakeAdapter:
    def __init__(
        self,
        backend: str,
        device: str | int | None,
    ) -> None:
        self.backend = backend
        self.device = device
        self.started = False

    @property
    def name(self) -> str:
        return f"{self.backend}:{self.device or 'default'}"

    @property
    def running(self) -> bool:
        return self.started

    def start(self, pcm_reader: object, status_reporter: object) -> None:
        del pcm_reader, status_reporter
        self.started = True

    def interrupt(self) -> None:
        self.started = False

    def close(self) -> None:
        self.started = False


class FakeMetadataPublisher:
    def __init__(self) -> None:
        self.start_calls = 0
        self.stop_calls = 0

    @property
    def running(self) -> bool:
        return self.start_calls > self.stop_calls

    def start(self) -> None:
        self.start_calls += 1

    def stop(self) -> None:
        self.stop_calls += 1


class StubRemoteProfileStore:
    def __init__(
        self,
        profile: BroadcastifyDestinationProfile | None,
    ) -> None:
        self.profile = profile
        self.requests: list[str] = []

    def get(self, name: str) -> BroadcastifyDestinationProfile:
        self.requests.append(name)
        if self.profile is None:
            raise ProfileError(
                f"Remote audio destination profile {name!r} does not exist."
            )
        return self.profile


def profile() -> BroadcastifyDestinationProfile:
    return BroadcastifyDestinationProfile(
        name="county-feed",
        server="audio.example.test",
        mount="/county",
        environment_variable="BROADCASTIFY_PASSWORD",
    )


@pytest.mark.parametrize(
    ("backend", "device", "expected_backend"),
    [
        ("auto", 2, "sounddevice"),
        ("sounddevice", "USB Audio", "sounddevice"),
        ("pipewire", "scanner-output", "pipewire"),
        ("pulseaudio", "scanner-output", "pulseaudio"),
        ("alsa", "plughw:CARD=Radio,DEV=0", "alsa"),
    ],
)
def test_playback_factory_selects_unstarted_adapter(
    backend: str,
    device: str | int,
    expected_backend: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def buffered_factory(
        *,
        name: str,
        adapter_factory: object,
        buffer_ms: int,
    ) -> FakeSink:
        observed["name"] = name
        observed["buffer_ms"] = buffer_ms
        observed["adapter"] = adapter_factory()  # type: ignore[operator]
        return FakeSink(name)

    monkeypatch.setattr(
        activation,
        "BufferedPlaybackSink",
        buffered_factory,
    )
    monkeypatch.setattr(
        activation,
        "SoundDevicePlaybackAdapter",
        lambda *, device=None: FakeAdapter("sounddevice", device),
    )
    monkeypatch.setattr(
        activation,
        "PipeWirePlaybackAdapter",
        lambda *, target=None: FakeAdapter("pipewire", target),
    )
    monkeypatch.setattr(
        activation,
        "PulseAudioPlaybackAdapter",
        lambda *, device=None: FakeAdapter("pulseaudio", device),
    )
    monkeypatch.setattr(
        activation,
        "AlsaPlaybackAdapter",
        lambda *, device=None: FakeAdapter("alsa", device),
    )

    resources = DaemonDestinationFactory().build(
        DaemonPlaybackDestination(
            name="speakers",
            backend=backend,  # type: ignore[arg-type]
            device=device,
            buffer_ms=400,
        )
    )

    assert resources.name == "speakers"
    assert resources.kind == "playback"
    assert resources.sink.name == "daemon:speakers"
    assert resources.metadata_publisher is None
    assert observed["name"] == "daemon:speakers"
    assert observed["buffer_ms"] == 400

    adapter = observed["adapter"]
    assert isinstance(adapter, FakeAdapter)
    assert adapter.backend == expected_backend
    assert adapter.device == device
    assert adapter.started is False
    assert resources.sink.running is False


def test_command_playback_backend_rejects_integer_device() -> None:
    destination = DaemonPlaybackDestination(
        name="speakers",
        backend="alsa",
        device=2,
    )

    with pytest.raises(
        ValueError,
        match="require a string device or null",
    ):
        DaemonDestinationFactory().build(destination)


def test_recording_factory_constructs_without_opening_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "archive.wav"
    observed: dict[str, object] = {}

    class FakeRecorder:
        def __init__(
            self,
            recorder_path: Path,
            *,
            overwrite: bool,
        ) -> None:
            observed["path"] = recorder_path
            observed["overwrite"] = overwrite

    def wav_factory(
        recorder: object,
        *,
        buffer_seconds: float,
    ) -> FakeSink:
        observed["recorder"] = recorder
        observed["buffer_seconds"] = buffer_seconds
        return FakeSink("wav:delegate")

    monkeypatch.setattr(
        activation,
        "PcmuWavRecorder",
        FakeRecorder,
    )
    monkeypatch.setattr(
        activation,
        "PcmWavSink",
        wav_factory,
    )

    resources = DaemonDestinationFactory().build(
        DaemonRecordingDestination(
            name="archive",
            path=path,
            overwrite=True,
            buffer_seconds=8.0,
        )
    )

    assert resources.name == "archive"
    assert resources.kind == "recording"
    assert resources.sink.name == "daemon:archive"
    assert resources.sink.running is False
    assert resources.metadata_publisher is None
    assert observed["path"] == path
    assert observed["overwrite"] is True
    assert observed["buffer_seconds"] == 8.0
    assert path.exists() is False


def test_named_recording_sink_delegates_lifecycle_and_pcm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delegate = FakeSink("wav:delegate")

    monkeypatch.setattr(
        activation,
        "PcmuWavRecorder",
        lambda path, *, overwrite: object(),
    )
    monkeypatch.setattr(
        activation,
        "PcmWavSink",
        lambda recorder, *, buffer_seconds: delegate,
    )

    resources = DaemonDestinationFactory().build(
        DaemonRecordingDestination(
            name="archive",
            path=tmp_path / "archive.wav",
        )
    )

    resources.sink.start()
    resources.sink.submit_pcm(b"\x00\x01")
    resources.sink.stop()

    assert delegate.start_calls == 1
    assert delegate.submissions == [b"\x00\x01"]
    assert delegate.stop_calls == 1
    assert resources.sink.statistics.bytes_submitted == 2


def test_remote_profile_factory_builds_pcm_and_metadata_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = StubRemoteProfileStore(profile())
    remote_sink = FakeSink("remote:county-feed")
    publisher = FakeMetadataPublisher()
    environ = {"BROADCASTIFY_PASSWORD": "secret"}
    observed: dict[str, object] = {}

    def sink_factory(
        config: object,
        *,
        environ: object,
    ) -> FakeSink:
        observed["sink_config"] = config
        observed["sink_environ"] = environ
        return remote_sink

    def metadata_factory(
        config: object,
        *,
        environ: object,
        minimum_update_interval: float,
    ) -> FakeMetadataPublisher:
        observed["metadata_config"] = config
        observed["metadata_environ"] = environ
        observed["minimum_update_interval"] = (
            minimum_update_interval
        )
        return publisher

    monkeypatch.setattr(
        activation,
        "create_broadcastify_sink",
        sink_factory,
    )
    monkeypatch.setattr(
        activation,
        "create_broadcastify_metadata_publisher",
        metadata_factory,
    )

    factory = DaemonDestinationFactory(
        remote_profile_store=store,
        environ=environ,
    )
    environ["BROADCASTIFY_PASSWORD"] = "changed-after-construction"

    resources = factory.build(
        DaemonRemoteProfileDestination(
            name="feed",
            profile="county-feed",
            publish_metadata=True,
            metadata_minimum_update_interval=2.5,
        )
    )

    assert store.requests == ["county-feed"]
    assert resources.name == "feed"
    assert resources.kind == "remote-profile"
    assert resources.sink.name == "daemon:feed"
    assert resources.sink.running is False
    assert resources.metadata_publisher is publisher
    assert remote_sink.start_calls == 0
    assert publisher.start_calls == 0

    sink_config = observed["sink_config"]
    metadata_config = observed["metadata_config"]
    assert sink_config is metadata_config
    assert sink_config.name == "county-feed"  # type: ignore[union-attr]
    assert observed["minimum_update_interval"] == 2.5
    assert observed["sink_environ"] == {
        "BROADCASTIFY_PASSWORD": "secret"
    }
    assert observed["metadata_environ"] == {
        "BROADCASTIFY_PASSWORD": "secret"
    }


def test_remote_profile_can_disable_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = StubRemoteProfileStore(profile())
    metadata_calls = 0

    monkeypatch.setattr(
        activation,
        "create_broadcastify_sink",
        lambda config, *, environ: FakeSink("remote:delegate"),
    )

    def metadata_factory(*args: object, **kwargs: object) -> object:
        nonlocal metadata_calls
        del args, kwargs
        metadata_calls += 1
        return FakeMetadataPublisher()

    monkeypatch.setattr(
        activation,
        "create_broadcastify_metadata_publisher",
        metadata_factory,
    )

    resources = DaemonDestinationFactory(
        remote_profile_store=store,
    ).build(
        DaemonRemoteProfileDestination(
            name="feed",
            profile="county-feed",
            publish_metadata=False,
        )
    )

    assert resources.metadata_publisher is None
    assert metadata_calls == 0


def test_remote_profile_lookup_failure_is_preserved() -> None:
    factory = DaemonDestinationFactory(
        remote_profile_store=StubRemoteProfileStore(None),
    )

    with pytest.raises(ProfileError, match="does not exist"):
        factory.build(
            DaemonRemoteProfileDestination(
                name="feed",
                profile="missing",
            )
        )


def test_destination_resources_are_immutable() -> None:
    resources = DaemonDestinationResources(
        DaemonPlaybackDestination(name="speakers"),
        FakeSink("daemon:speakers"),
    )

    with pytest.raises(FrozenInstanceError):
        resources.sink = FakeSink("replacement")  # type: ignore[misc]
