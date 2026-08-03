from __future__ import annotations

from pathlib import Path

import pytest

from sds200 import (
    REMOTE_AUDIO_PROFILE_VERSION,
    BroadcastifyDestinationProfile,
    RemoteAudioProfileStore,
    default_remote_audio_profile_path,
)
from sds200.exceptions import ProfileError
from sds200.remote_audio import EnvironmentSecret


def minimal_profile(
    name: str = "county-feed",
    **overrides: object,
) -> BroadcastifyDestinationProfile:
    values: dict[str, object] = {
        "name": name,
        "server": "audio1.broadcastify.com",
        "mount": "/abc123",
        "password_env": "SDS200_BROADCASTIFY_PASSWORD",
    }
    values.update(overrides)
    return BroadcastifyDestinationProfile(**values)


def test_default_remote_audio_profile_path_uses_legacy_config_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    assert default_remote_audio_profile_path() == (
        tmp_path / "sds200" / "remote-audio-profiles.toml"
    )


def test_remote_audio_profile_store_round_trip_and_conversion(
    tmp_path: Path,
) -> None:
    path = tmp_path / "remote-audio-profiles.toml"
    store = RemoteAudioProfileStore(path)
    profile = minimal_profile()

    store.put(profile)

    loaded = store.get("county-feed")
    config = loaded.to_broadcastify_config()

    assert loaded == profile
    assert loaded.kind == "broadcastify"
    assert config.name == profile.name
    assert config.endpoint == "http://audio1.broadcastify.com:80/abc123"
    assert config.password == EnvironmentSecret(
        "SDS200_BROADCASTIFY_PASSWORD"
    )
    assert config.reconnect_policy == profile.reconnect_policy
    assert path.read_text(encoding="utf-8").startswith(
        f"version = {REMOTE_AUDIO_PROFILE_VERSION}\n"
    )


def test_remote_audio_profile_store_preserves_all_settings(
    tmp_path: Path,
) -> None:
    path = tmp_path / "remote-audio-profiles.toml"
    store = RemoteAudioProfileStore(path)
    profile = minimal_profile(
        port=8000,
        stream_name="County Public Safety",
        genre="Public Safety",
        public=False,
        ffmpeg_executable="/usr/local/bin/ffmpeg",
        connect_timeout=4.0,
        socket_timeout=6.0,
        encoder_stop_timeout=1.0,
        buffer_seconds=8.0,
        stop_timeout=9.0,
        reconnect_initial_delay=0.5,
        reconnect_multiplier=1.5,
        reconnect_max_delay=12.0,
        reconnect_max_attempts=7,
    )

    store.put(profile)

    loaded = store.get(profile.name)
    config = loaded.to_broadcastify_config()

    assert loaded == profile
    assert config.port == 8000
    assert config.stream_name == "County Public Safety"
    assert config.genre == "Public Safety"
    assert config.public is False
    assert config.ffmpeg_executable == "/usr/local/bin/ffmpeg"
    assert config.connect_timeout == 4.0
    assert config.socket_timeout == 6.0
    assert config.encoder_stop_timeout == 1.0
    assert config.buffer_seconds == 8.0
    assert config.stop_timeout == 9.0
    assert config.reconnect_policy.initial_delay == 0.5
    assert config.reconnect_policy.multiplier == 1.5
    assert config.reconnect_policy.max_delay == 12.0
    assert config.reconnect_policy.max_attempts == 7


def test_remote_audio_profile_store_orders_profiles_deterministically(
    tmp_path: Path,
) -> None:
    path = tmp_path / "remote-audio-profiles.toml"
    store = RemoteAudioProfileStore(path)
    store.put(minimal_profile("zulu"))
    store.put(minimal_profile("alpha"))

    assert [profile.name for profile in store.list()] == ["alpha", "zulu"]

    document = path.read_text(encoding="utf-8")
    assert document.index('[destinations."alpha"]') < document.index(
        '[destinations."zulu"]'
    )


def test_remote_audio_profile_store_remove_and_missing_profile(
    tmp_path: Path,
) -> None:
    store = RemoteAudioProfileStore(tmp_path / "remote-audio-profiles.toml")
    store.put(minimal_profile())
    store.remove("county-feed")

    assert store.list() == ()
    with pytest.raises(ProfileError, match="does not exist"):
        store.get("county-feed")
    with pytest.raises(ProfileError, match="does not exist"):
        store.remove("county-feed")


def test_remote_audio_profile_file_never_contains_resolved_secret(
    tmp_path: Path,
) -> None:
    path = tmp_path / "remote-audio-profiles.toml"
    profile = minimal_profile()
    RemoteAudioProfileStore(path).put(profile)

    secret = "resolved-production-password"
    resolved = profile.to_broadcastify_config().password.resolve(
        {"SDS200_BROADCASTIFY_PASSWORD": secret}
    )
    document = path.read_text(encoding="utf-8")

    assert resolved == secret
    assert secret not in document
    assert "SDS200_BROADCASTIFY_PASSWORD" in document
    assert secret not in repr(profile)


def test_remote_audio_profile_store_loads_minimal_document_with_defaults(
    tmp_path: Path,
) -> None:
    path = tmp_path / "remote-audio-profiles.toml"
    path.write_text(
        'version = 1\n\n'
        '[destinations."county-feed"]\n'
        'kind = "broadcastify"\n'
        'server = "audio1.broadcastify.com"\n'
        'mount = "/abc123"\n'
        'password_env = "SDS200_BROADCASTIFY_PASSWORD"\n',
        encoding="utf-8",
    )

    profile = RemoteAudioProfileStore(path).get("county-feed")

    assert profile == minimal_profile()
    assert profile.reconnect_max_attempts is None


def test_remote_audio_profile_store_rejects_malformed_toml(
    tmp_path: Path,
) -> None:
    path = tmp_path / "remote-audio-profiles.toml"
    path.write_text("version = [", encoding="utf-8")

    with pytest.raises(ProfileError, match="Could not read"):
        RemoteAudioProfileStore(path).list()


@pytest.mark.parametrize(
    "version",
    [
        "",
        "version = 2\n",
        "version = true\n",
        "version = 1.0\n",
    ],
)
def test_remote_audio_profile_store_rejects_unsupported_version(
    tmp_path: Path,
    version: str,
) -> None:
    path = tmp_path / "remote-audio-profiles.toml"
    path.write_text(version, encoding="utf-8")

    with pytest.raises(ProfileError, match="version must be 1"):
        RemoteAudioProfileStore(path).list()


def test_remote_audio_profile_store_refuses_unknown_kind_without_rewriting(
    tmp_path: Path,
) -> None:
    path = tmp_path / "remote-audio-profiles.toml"
    original = (
        'version = 1\n\n'
        '[destinations."future"]\n'
        'kind = "future-adapter"\n'
        'opaque = "preserve-me"\n'
    )
    path.write_text(original, encoding="utf-8")
    store = RemoteAudioProfileStore(path)

    with pytest.raises(ProfileError, match="unsupported field|unsupported kind"):
        store.put(minimal_profile())

    assert path.read_text(encoding="utf-8") == original


def test_remote_audio_profile_store_rejects_unknown_supported_kind_field(
    tmp_path: Path,
) -> None:
    path = tmp_path / "remote-audio-profiles.toml"
    path.write_text(
        'version = 1\n\n'
        '[destinations."county-feed"]\n'
        'kind = "broadcastify"\n'
        'server = "audio1.broadcastify.com"\n'
        'mount = "/abc123"\n'
        'password_env = "SDS200_BROADCASTIFY_PASSWORD"\n'
        'future_option = true\n',
        encoding="utf-8",
    )

    with pytest.raises(ProfileError, match="unsupported field"):
        RemoteAudioProfileStore(path).get("county-feed")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("port", '"80"', "invalid port"),
        ("public", '"yes"', "invalid public"),
        (
            "reconnect_max_attempts",
            "true",
            "invalid reconnect_max_attempts",
        ),
    ],
)
def test_remote_audio_profile_store_rejects_invalid_field_types(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    path = tmp_path / "remote-audio-profiles.toml"
    path.write_text(
        'version = 1\n\n'
        '[destinations."county-feed"]\n'
        'kind = "broadcastify"\n'
        'server = "audio1.broadcastify.com"\n'
        'mount = "/abc123"\n'
        'password_env = "SDS200_BROADCASTIFY_PASSWORD"\n'
        f"{field} = {value}\n",
        encoding="utf-8",
    )

    with pytest.raises(ProfileError, match=message):
        RemoteAudioProfileStore(path).get("county-feed")


def test_broadcastify_destination_profile_rejects_invalid_secret_reference() -> None:
    with pytest.raises(ProfileError, match="environment-variable name"):
        minimal_profile(password_env=" padded ")


def test_broadcastify_destination_profile_rejects_invalid_reconnect_policy() -> None:
    with pytest.raises(ProfileError, match="Reconnect maximum delay"):
        minimal_profile(
            reconnect_initial_delay=10.0,
            reconnect_max_delay=5.0,
        )
