from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from sds200 import (
    ConfigurationError,
    DaemonDestinationCleanupFailure,
    DaemonDestinationConfiguration,
    DaemonDestinationReloader,
    DaemonDestinationReloadPreview,
    DaemonDestinationReloadResult,
    DaemonDestinationReplacementResult,
    DaemonPlaybackDestination,
    preview_daemon_destination_replacement,
)


class FakeCoordinator:
    def __init__(
        self,
        configuration: DaemonDestinationConfiguration,
        *,
        replace_error: BaseException | None = None,
        cleanup_failures: tuple[
            DaemonDestinationCleanupFailure,
            ...,
        ] = (),
    ) -> None:
        self.configuration = configuration
        self.replace_error = replace_error
        self.cleanup_failures = cleanup_failures
        self.preview_calls: list[
            DaemonDestinationConfiguration
        ] = []
        self.replace_calls: list[
            DaemonDestinationConfiguration
        ] = []

    def preview(
        self,
        replacement: DaemonDestinationConfiguration,
    ) -> object:
        self.preview_calls.append(replacement)
        return preview_daemon_destination_replacement(
            self.configuration,
            replacement,
        )

    def replace(
        self,
        replacement: DaemonDestinationConfiguration,
    ) -> DaemonDestinationReplacementResult:
        self.replace_calls.append(replacement)
        if self.replace_error is not None:
            raise self.replace_error

        preview = preview_daemon_destination_replacement(
            self.configuration,
            replacement,
        )
        self.configuration = replacement
        return DaemonDestinationReplacementResult(
            preview,
            replacement,
            self.cleanup_failures,
        )


def configuration(
    *destinations: DaemonPlaybackDestination,
) -> DaemonDestinationConfiguration:
    return DaemonDestinationConfiguration(destinations)


def test_reloader_preview_loads_once_without_mutating_coordinator(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "daemon-destinations.toml"
    current = configuration(
        DaemonPlaybackDestination(name="speakers")
    )
    replacement = configuration(
        DaemonPlaybackDestination(
            name="speakers",
            buffer_ms=500,
        ),
        DaemonPlaybackDestination(name="archive"),
    )
    coordinator = FakeCoordinator(current)
    loaded_paths: list[Path] = []

    def load(path: Path) -> DaemonDestinationConfiguration:
        loaded_paths.append(path)
        return replacement

    reloader = DaemonDestinationReloader(
        coordinator,  # type: ignore[arg-type]
        manifest,
        loader=load,
    )

    result = reloader.preview()

    assert isinstance(result, DaemonDestinationReloadPreview)
    assert result.path == manifest
    assert result.configuration == replacement
    assert result.changed
    assert result.preview.names_for("added") == ("archive",)
    assert result.preview.names_for("replaced") == ("speakers",)
    assert loaded_paths == [manifest]
    assert coordinator.preview_calls == [replacement]
    assert coordinator.replace_calls == []
    assert coordinator.configuration == current
    json.dumps(result.as_dict())


def test_reloader_applies_loaded_configuration(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "daemon-destinations.toml"
    current = DaemonDestinationConfiguration()
    replacement = configuration(
        DaemonPlaybackDestination(name="speakers")
    )
    coordinator = FakeCoordinator(current)
    reloader = DaemonDestinationReloader(
        coordinator,  # type: ignore[arg-type]
        manifest,
        loader=lambda path: replacement,
    )

    result = reloader.reload()

    assert isinstance(result, DaemonDestinationReloadResult)
    assert result.path == manifest
    assert result.changed
    assert result.clean
    assert result.configuration == replacement
    assert result.preview.names_for("added") == ("speakers",)
    assert result.cleanup_failures == ()
    assert coordinator.preview_calls == []
    assert coordinator.replace_calls == [replacement]
    assert coordinator.configuration == replacement
    json.dumps(result.as_dict())


def test_reloader_missing_manifest_commits_empty_configuration(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "missing-destinations.toml"
    current = configuration(
        DaemonPlaybackDestination(name="speakers")
    )
    coordinator = FakeCoordinator(current)
    reloader = DaemonDestinationReloader(
        coordinator,  # type: ignore[arg-type]
        manifest,
    )

    result = reloader.reload()

    assert result.changed
    assert result.preview.names_for("removed") == ("speakers",)
    assert result.configuration == DaemonDestinationConfiguration()
    assert coordinator.configuration == DaemonDestinationConfiguration()
    assert manifest.exists() is False


def test_loader_failure_preserves_committed_configuration(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "daemon-destinations.toml"
    current = configuration(
        DaemonPlaybackDestination(name="speakers")
    )
    coordinator = FakeCoordinator(current)
    failure = ConfigurationError("secret manifest detail")

    def fail(path: Path) -> DaemonDestinationConfiguration:
        del path
        raise failure

    reloader = DaemonDestinationReloader(
        coordinator,  # type: ignore[arg-type]
        manifest,
        loader=fail,
    )

    with pytest.raises(ConfigurationError) as raised:
        reloader.reload()

    assert raised.value is failure
    assert coordinator.preview_calls == []
    assert coordinator.replace_calls == []
    assert coordinator.configuration == current


def test_activation_failure_preserves_committed_configuration(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "daemon-destinations.toml"
    current = configuration(
        DaemonPlaybackDestination(name="speakers")
    )
    replacement = configuration(
        DaemonPlaybackDestination(
            name="speakers",
            buffer_ms=500,
        )
    )
    failure = RuntimeError("secret activation detail")
    coordinator = FakeCoordinator(
        current,
        replace_error=failure,
    )
    reloader = DaemonDestinationReloader(
        coordinator,  # type: ignore[arg-type]
        manifest,
        loader=lambda path: replacement,
    )

    with pytest.raises(RuntimeError) as raised:
        reloader.reload()

    assert raised.value is failure
    assert coordinator.preview_calls == []
    assert coordinator.replace_calls == [replacement]
    assert coordinator.configuration == current


def test_reload_reports_committed_cleanup_failures_without_details(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "daemon-destinations.toml"
    current = configuration(
        DaemonPlaybackDestination(name="speakers")
    )
    replacement = configuration(
        DaemonPlaybackDestination(
            name="speakers",
            buffer_ms=500,
        )
    )
    cleanup_failure = DaemonDestinationCleanupFailure(
        "speakers",
        "sink",
        "OSError",
    )
    coordinator = FakeCoordinator(
        current,
        cleanup_failures=(cleanup_failure,),
    )
    reloader = DaemonDestinationReloader(
        coordinator,  # type: ignore[arg-type]
        manifest,
        loader=lambda path: replacement,
    )

    result = reloader.reload()

    assert result.changed
    assert not result.clean
    assert result.cleanup_failures == (cleanup_failure,)
    assert coordinator.configuration == replacement
    assert "secret" not in repr(result)
    serialized = result.as_dict()
    assert serialized["cleanup_failures"] == [
        {
            "name": "speakers",
            "component": "sink",
            "error_type": "OSError",
        }
    ]
    json.dumps(serialized)


def test_reloader_rejects_invalid_loader_output_before_coordinator_use(
    tmp_path: Path,
) -> None:
    coordinator = FakeCoordinator(
        DaemonDestinationConfiguration()
    )
    reloader = DaemonDestinationReloader(
        coordinator,  # type: ignore[arg-type]
        tmp_path / "daemon-destinations.toml",
        loader=lambda path: object(),  # type: ignore[arg-type]
    )

    with pytest.raises(
        TypeError,
        match="loaders must return",
    ):
        reloader.reload()

    assert coordinator.preview_calls == []
    assert coordinator.replace_calls == []


@pytest.mark.parametrize("path", ["", "   "])
def test_reloader_rejects_empty_manifest_paths(path: str) -> None:
    coordinator = FakeCoordinator(
        DaemonDestinationConfiguration()
    )

    with pytest.raises(ValueError, match="must not be empty"):
        DaemonDestinationReloader(
            coordinator,  # type: ignore[arg-type]
            path,
        )


def test_reload_outcomes_are_immutable(tmp_path: Path) -> None:
    manifest = tmp_path / "daemon-destinations.toml"
    coordinator = FakeCoordinator(
        DaemonDestinationConfiguration()
    )
    reloader = DaemonDestinationReloader(
        coordinator,  # type: ignore[arg-type]
        manifest,
        loader=lambda path: DaemonDestinationConfiguration(),
    )

    preview = reloader.preview()
    result = reloader.reload()

    with pytest.raises(FrozenInstanceError):
        preview.path = Path("/tmp/replacement")  # type: ignore[misc]

    with pytest.raises(FrozenInstanceError):
        result.path = Path("/tmp/replacement")  # type: ignore[misc]
