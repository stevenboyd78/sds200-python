"""Assisted-refresh composition for external Favorites previews."""

from __future__ import annotations

import threading
from dataclasses import dataclass

from .favorites_external import (
    FavoritesExternalImportPreview,
    FavoritesExternalRecordObservation,
    FavoritesExternalSource,
    preview_favorites_external_import,
)
from .favorites_external_provenance_lifecycle import (
    FavoritesExternalProvenanceLifecycle,
    FavoritesExternalProvenanceLifecycleSnapshot,
    FavoritesExternalProvenanceLifecycleState,
)


@dataclass(frozen=True, slots=True)
class FavoritesExternalRefreshResult:
    """Immutable evidence from one successful external Favorites refresh."""

    lifecycle_snapshot: FavoritesExternalProvenanceLifecycleSnapshot
    observations: tuple[FavoritesExternalRecordObservation, ...]
    preview: FavoritesExternalImportPreview

    def __post_init__(self) -> None:
        if not isinstance(
            self.lifecycle_snapshot,
            FavoritesExternalProvenanceLifecycleSnapshot,
        ):
            raise TypeError(
                "External Favorites refresh result requires a lifecycle snapshot."
            )
        if self.lifecycle_snapshot.state is not FavoritesExternalProvenanceLifecycleState.ACTIVE:
            raise ValueError(
                "External Favorites refresh result requires an active lifecycle snapshot."
            )
        if type(self.observations) is not tuple:
            raise TypeError(
                "External Favorites refresh observations must be an immutable tuple."
            )
        if any(
            not isinstance(observation, FavoritesExternalRecordObservation)
            for observation in self.observations
        ):
            raise TypeError(
                "External Favorites refresh observations must contain only "
                "FavoritesExternalRecordObservation values."
            )
        if not isinstance(self.preview, FavoritesExternalImportPreview):
            raise TypeError(
                "External Favorites refresh result requires "
                "FavoritesExternalImportPreview."
            )
        expected_preview = preview_favorites_external_import(
            self.lifecycle_snapshot.provenance_records or (),
            self.observations,
        )
        if self.preview != expected_preview:
            raise ValueError(
                "External Favorites refresh preview must match its retained evidence."
            )


class FavoritesExternalRefreshSession:
    """Compose explicit provider reads with active provenance lifecycle evidence."""

    def __init__(
        self,
        lifecycle: FavoritesExternalProvenanceLifecycle,
        source: FavoritesExternalSource,
    ) -> None:
        if not isinstance(lifecycle, FavoritesExternalProvenanceLifecycle):
            raise TypeError(
                "External Favorites refresh session requires "
                "FavoritesExternalProvenanceLifecycle."
            )
        read_observations = getattr(source, "read_observations", None)
        if not callable(read_observations):
            raise TypeError(
                "External Favorites refresh session requires FavoritesExternalSource."
            )

        self.lifecycle = lifecycle
        self.source = source
        self._refresh_lock = threading.Lock()

    def refresh(self) -> FavoritesExternalRefreshResult:
        """Read and preview one fresh observation set against active provenance."""

        with self._refresh_lock:
            lifecycle_snapshot = self.lifecycle.snapshot()
            if (
                lifecycle_snapshot.state
                is not FavoritesExternalProvenanceLifecycleState.ACTIVE
            ):
                raise RuntimeError(
                    "External Favorites refresh requires an active provenance lifecycle."
                )

            observations = self.source.read_observations()
            preview = preview_favorites_external_import(
                lifecycle_snapshot.provenance_records or (),
                observations,
            )
            return FavoritesExternalRefreshResult(
                lifecycle_snapshot=lifecycle_snapshot,
                observations=observations,
                preview=preview,
            )


__all__ = [
    "FavoritesExternalRefreshResult",
    "FavoritesExternalRefreshSession",
]
