from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from sds200 import (
    DAEMON_DESTINATION_CONFIG_FILENAME,
    DAEMON_DESTINATION_CONFIG_VERSION,
    ConfigurationError,
    DaemonDestinationConfiguration,
    DaemonPlaybackDestination,
    DaemonRecordingDestination,
    DaemonRemoteProfileDestination,
    default_daemon_destination_config_path,
    load_daemon_destination_configuration,
    preview_daemon_destination_replacement,
    resolve_configuration_paths,
)


def test_default_destination_path_and_missing_manifest_are_read_only(
    tmp_path: Path,
) -> None:
    paths = resolve_configuration_paths(
        environ={},
        home=tmp_path / "home",
        system_config_dir=tmp_path / "etc" / "sdsctl",
    )
    expected = (
        paths.user_config_dir
        / DAEMON_DESTINATION_CONFIG_FILENAME
    )

    assert default_daemon_destination_config_path(paths) == expected
    assert load_daemon_destination_configuration(paths=paths) == (
        DaemonDestinationConfiguration()
    )
    assert expected.exists() is False


def test_destination_manifest_loads_typed_sorted_entries(
    tmp_path: Path,
) -> None:
    path = tmp_path / DAEMON_DESTINATION_CONFIG_FILENAME
    path.write_text(
        'version = 1\n'
        '\n'
        '[destinations.speakers]\n'
        'kind = "playback"\n'
        'backend = "PipeWire"\n'
        'device = "scanner-output"\n'
        'buffer_ms = 400\n'
        '\n'
        '[destinations.feed]\n'
        'kind = "remote-profile"\n'
        'profile = "county-feed"\n'
        'publish_metadata = true\n'
        'metadata_minimum_update_interval = 2.5\n'
        '\n'
        '[destinations.archive]\n'
        'kind = "recording"\n'
        'path = "/var/lib/sdsctl/live.wav"\n'
        'overwrite = false\n'
        'buffer_seconds = 8.0\n',
        encoding="utf-8",
    )

    configuration = load_daemon_destination_configuration(path)

    assert tuple(
        destination.name
        for destination in configuration.destinations
    ) == ("archive", "feed", "speakers")

    archive = configuration.destination("archive")
    assert archive == DaemonRecordingDestination(
        name="archive",
        path=Path("/var/lib/sdsctl/live.wav"),
        overwrite=False,
        buffer_seconds=8.0,
    )

    feed = configuration.destination("feed")
    assert feed == DaemonRemoteProfileDestination(
        name="feed",
        profile="county-feed",
        publish_metadata=True,
        metadata_minimum_update_interval=2.5,
    )

    speakers = configuration.destination("speakers")
    assert speakers == DaemonPlaybackDestination(
        name="speakers",
        backend="pipewire",
        device="scanner-output",
        buffer_ms=400,
    )

    serialized = configuration.as_dict()
    assert serialized["version"] == (
        DAEMON_DESTINATION_CONFIG_VERSION
    )
    assert list(serialized["destinations"]) == [
        "archive",
        "feed",
        "speakers",
    ]
    json.dumps(serialized)


def test_destination_configuration_sorts_and_rejects_duplicate_names() -> None:
    playback = DaemonPlaybackDestination(name="zulu")
    recording = DaemonRecordingDestination(
        name="alpha",
        path=Path("/tmp/scanner.wav"),
    )

    configuration = DaemonDestinationConfiguration(
        (playback, recording)
    )
    assert tuple(
        destination.name
        for destination in configuration.destinations
    ) == ("alpha", "zulu")

    with pytest.raises(ValueError, match="must be unique"):
        DaemonDestinationConfiguration(
            (
                playback,
                DaemonPlaybackDestination(name="zulu"),
            )
        )


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            lambda: DaemonPlaybackDestination(name=" padded "),
            "must not be empty or padded",
        ),
        (
            lambda: DaemonPlaybackDestination(
                name="speakers",
                backend="future",
            ),
            "backend must be one of",
        ),
        (
            lambda: DaemonPlaybackDestination(
                name="speakers",
                device=True,
            ),
            "device must be",
        ),
        (
            lambda: DaemonPlaybackDestination(
                name="speakers",
                buffer_ms=0,
            ),
            "buffer must be greater than zero",
        ),
        (
            lambda: DaemonRecordingDestination(
                name="archive",
                path=Path("relative.wav"),
            ),
            "path must be absolute",
        ),
        (
            lambda: DaemonRecordingDestination(
                name="archive",
                path=Path("/tmp/archive.wav"),
                buffer_seconds=float("inf"),
            ),
            "buffer must be finite",
        ),
        (
            lambda: DaemonRemoteProfileDestination(
                name="feed",
                profile="",
            ),
            "profile name must not be empty",
        ),
        (
            lambda: DaemonRemoteProfileDestination(
                name="feed",
                profile="county-feed",
                metadata_minimum_update_interval=-1,
            ),
            "minimum update interval must be at least 0",
        ),
    ],
)
def test_destination_entries_reject_unsafe_values(
    factory: object,
    message: str,
) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        factory()  # type: ignore[operator]


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ("", "version must be 1"),
        ("version = 2\n", "version must be 1"),
        ("version = true\n", "version must be 1"),
        ("version = [\n", "Could not read"),
        (
            "version = 1\nfuture = true\n",
            "unsupported top-level field",
        ),
        (
            'version = 1\ndestinations = "invalid"\n',
            r"\[destinations\] table",
        ),
        (
            "version = 1\n"
            "[destinations.invalid]\n"
            'kind = "future"\n',
            "unsupported or missing kind",
        ),
        (
            "version = 1\n"
            "[destinations.invalid]\n"
            'kind = "playback"\n'
            "future = true\n",
            "unsupported field",
        ),
        (
            "version = 1\n"
            'destinations = { invalid = "not-a-table" }\n',
            "must be a table",
        ),
    ],
)
def test_destination_manifest_rejects_invalid_documents(
    tmp_path: Path,
    document: str,
    message: str,
) -> None:
    path = tmp_path / DAEMON_DESTINATION_CONFIG_FILENAME
    path.write_text(document, encoding="utf-8")

    with pytest.raises(ConfigurationError, match=message):
        load_daemon_destination_configuration(path)


def test_destination_manifest_diagnostics_do_not_echo_field_values(
    tmp_path: Path,
) -> None:
    secret = "resolved-production-password"
    path = tmp_path / DAEMON_DESTINATION_CONFIG_FILENAME
    path.write_text(
        "version = 1\n"
        "[destinations.feed]\n"
        'kind = "remote-profile"\n'
        'profile = "county-feed"\n'
        f'password = "{secret}"\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError) as exc_info:
        load_daemon_destination_configuration(path)

    message = str(exc_info.value)
    assert "password" in message
    assert str(path) in message
    assert secret not in message


def test_destination_replacement_preview_is_deterministic() -> None:
    current = DaemonDestinationConfiguration(
        (
            DaemonPlaybackDestination(name="speakers"),
            DaemonRecordingDestination(
                name="archive",
                path=Path("/tmp/old.wav"),
            ),
            DaemonRemoteProfileDestination(
                name="legacy",
                profile="legacy-feed",
            ),
        )
    )
    replacement = DaemonDestinationConfiguration(
        (
            DaemonPlaybackDestination(name="speakers"),
            DaemonRecordingDestination(
                name="archive",
                path=Path("/tmp/new.wav"),
            ),
            DaemonRemoteProfileDestination(
                name="feed",
                profile="county-feed",
            ),
        )
    )

    preview = preview_daemon_destination_replacement(
        current,
        replacement,
    )

    assert preview.changed is True
    assert tuple(
        (change.name, change.action)
        for change in preview.changes
    ) == (
        ("archive", "replaced"),
        ("feed", "added"),
        ("legacy", "removed"),
        ("speakers", "unchanged"),
    )
    assert preview.names_for("added") == ("feed",)
    assert preview.names_for("removed") == ("legacy",)
    assert preview.names_for("replaced") == ("archive",)
    assert preview.names_for("unchanged") == ("speakers",)

    serialized = preview.as_dict()
    assert serialized["changed"] is True
    assert serialized["added"] == ["feed"]
    assert serialized["removed"] == ["legacy"]
    assert serialized["replaced"] == ["archive"]
    assert serialized["unchanged"] == ["speakers"]
    json.dumps(serialized)


def test_identical_destination_replacement_is_unchanged() -> None:
    configuration = DaemonDestinationConfiguration(
        (DaemonPlaybackDestination(name="speakers"),)
    )

    preview = preview_daemon_destination_replacement(
        configuration,
        configuration,
    )

    assert preview.changed is False
    assert preview.names_for("unchanged") == ("speakers",)


def test_destination_contract_is_immutable() -> None:
    destination = DaemonPlaybackDestination(name="speakers")
    configuration = DaemonDestinationConfiguration((destination,))
    preview = preview_daemon_destination_replacement(
        configuration,
        configuration,
    )

    with pytest.raises(FrozenInstanceError):
        destination.buffer_ms = 500  # type: ignore[misc]

    with pytest.raises(FrozenInstanceError):
        configuration.destinations = ()  # type: ignore[misc]

    with pytest.raises(FrozenInstanceError):
        preview.changes = ()  # type: ignore[misc]


def test_destination_loader_rejects_path_and_paths_together(
    tmp_path: Path,
) -> None:
    paths = resolve_configuration_paths(
        environ={},
        home=tmp_path / "home",
        system_config_dir=tmp_path / "etc" / "sdsctl",
    )

    with pytest.raises(ValueError, match="not both"):
        load_daemon_destination_configuration(
            tmp_path / DAEMON_DESTINATION_CONFIG_FILENAME,
            paths=paths,
        )
