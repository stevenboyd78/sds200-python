from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .daemon_destination_activation import (
    DaemonDestinationCleanupFailure,
    DaemonDestinationReplacementResult,
)
from .daemon_destinations import (
    DaemonDestinationConfiguration,
    DaemonDestinationReplacementPreview,
    load_daemon_destination_configuration,
)

_DaemonDestinationLoader = Callable[
    [Path],
    DaemonDestinationConfiguration,
]


class _DaemonDestinationCoordinatorLike(Protocol):
    def preview(
        self,
        replacement: DaemonDestinationConfiguration,
    ) -> DaemonDestinationReplacementPreview: ...

    def replace(
        self,
        replacement: DaemonDestinationConfiguration,
    ) -> DaemonDestinationReplacementResult: ...


def _normalize_manifest_path(path: str | Path) -> Path:
    if isinstance(path, str):
        if not path.strip():
            raise ValueError(
                "Daemon destination reload paths must not be empty."
            )
        return Path(path)
    if isinstance(path, Path):
        return path
    raise TypeError(
        "Daemon destination reload paths must be strings or paths."
    )


@dataclass(frozen=True, slots=True)
class DaemonDestinationReloadPreview:
    """Validated manifest contents and their non-mutating replacement preview."""

    path: Path
    configuration: DaemonDestinationConfiguration
    preview: DaemonDestinationReplacementPreview

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "path",
            _normalize_manifest_path(self.path),
        )
        if not isinstance(
            self.configuration,
            DaemonDestinationConfiguration,
        ):
            raise TypeError(
                "Daemon destination reload previews require a "
                "DaemonDestinationConfiguration."
            )
        if not isinstance(
            self.preview,
            DaemonDestinationReplacementPreview,
        ):
            raise TypeError(
                "Daemon destination reload previews require a "
                "DaemonDestinationReplacementPreview."
            )

    @property
    def changed(self) -> bool:
        return self.preview.changed

    def as_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "changed": self.changed,
            "configuration": self.configuration.as_dict(),
            "preview": self.preview.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class DaemonDestinationReloadResult:
    """Committed manifest reload and isolated old-resource cleanup outcome."""

    path: Path
    replacement: DaemonDestinationReplacementResult

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "path",
            _normalize_manifest_path(self.path),
        )
        if not isinstance(
            self.replacement,
            DaemonDestinationReplacementResult,
        ):
            raise TypeError(
                "Daemon destination reload results require a "
                "DaemonDestinationReplacementResult."
            )

    @property
    def changed(self) -> bool:
        return self.replacement.changed

    @property
    def clean(self) -> bool:
        return self.replacement.clean

    @property
    def configuration(self) -> DaemonDestinationConfiguration:
        return self.replacement.configuration

    @property
    def preview(self) -> DaemonDestinationReplacementPreview:
        return self.replacement.preview

    @property
    def cleanup_failures(
        self,
    ) -> tuple[DaemonDestinationCleanupFailure, ...]:
        return self.replacement.cleanup_failures

    def as_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            **self.replacement.as_dict(),
        }


class DaemonDestinationReloader:
    """Serialize validated manifest previews and transactional replacements."""

    def __init__(
        self,
        coordinator: _DaemonDestinationCoordinatorLike,
        path: str | Path,
        *,
        loader: _DaemonDestinationLoader = (
            load_daemon_destination_configuration
        ),
    ) -> None:
        if not callable(loader):
            raise TypeError(
                "Daemon destination reload loaders must be callable."
            )

        self.coordinator = coordinator
        self.path = _normalize_manifest_path(path)
        self.loader = loader
        self._lock = threading.Lock()

    def preview(self) -> DaemonDestinationReloadPreview:
        """Load and validate the manifest without changing active resources."""

        with self._lock:
            configuration = self._load()
            preview = self.coordinator.preview(configuration)
            return DaemonDestinationReloadPreview(
                self.path,
                configuration,
                preview,
            )

    def reload(self) -> DaemonDestinationReloadResult:
        """Load, validate, and transactionally replace active destinations."""

        with self._lock:
            configuration = self._load()
            replacement = self.coordinator.replace(configuration)
            return DaemonDestinationReloadResult(
                self.path,
                replacement,
            )

    def _load(self) -> DaemonDestinationConfiguration:
        configuration = self.loader(self.path)
        if not isinstance(
            configuration,
            DaemonDestinationConfiguration,
        ):
            raise TypeError(
                "Daemon destination reload loaders must return a "
                "DaemonDestinationConfiguration."
            )
        return configuration
