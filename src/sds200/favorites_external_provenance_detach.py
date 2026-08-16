"""Durable provenance-only completion for assisted-refresh detach decisions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .favorites_external import FavoritesExternalRecordState
from .favorites_external_provenance import (
    FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_BYTES,
    FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_FIELDS_PER_RECORD,
    FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_RECORDS,
    deserialize_favorites_external_provenance,
    serialize_favorites_external_provenance,
)
from .favorites_external_provenance_storage import (
    FavoritesExternalProvenanceStorageError,
    load_favorites_external_provenance,
    save_favorites_external_provenance_if_current,
)

if TYPE_CHECKING:
    from .favorites_external_refresh_detach import FavoritesExternalRefreshDetachPlan


class FavoritesExternalRefreshDetachProvenanceError(ValueError):
    """Report invalid persisted provenance for one planned refresh detach."""


class FavoritesExternalRefreshDetachPersistenceError(RuntimeError):
    """Report failed provenance publication for one planned refresh detach."""


@dataclass(frozen=True, slots=True)
class FavoritesExternalRefreshDetachDurableResult:
    """One exact refresh detach with durably published complete provenance."""

    plan: FavoritesExternalRefreshDetachPlan
    baseline_provenance_records: tuple[FavoritesExternalRecordState, ...]
    provenance_records: tuple[FavoritesExternalRecordState, ...]
    provenance_path: Path

    def __post_init__(self) -> None:
        from .favorites_external_refresh_detach import FavoritesExternalRefreshDetachPlan

        if type(self.plan) is not FavoritesExternalRefreshDetachPlan:
            raise TypeError(
                "Durable refresh detach requires an exact "
                "FavoritesExternalRefreshDetachPlan."
            )
        if type(self.baseline_provenance_records) is not tuple:
            raise TypeError(
                "Durable refresh detach baseline provenance records must be "
                "an immutable tuple."
            )
        if type(self.provenance_records) is not tuple:
            raise TypeError(
                "Durable refresh detach provenance records must be an immutable tuple."
            )
        if any(
            not isinstance(record, FavoritesExternalRecordState)
            for record in self.baseline_provenance_records
        ):
            raise TypeError(
                "Durable refresh detach baseline provenance must contain only "
                "FavoritesExternalRecordState values."
            )
        if any(
            not isinstance(record, FavoritesExternalRecordState)
            for record in self.provenance_records
        ):
            raise TypeError(
                "Durable refresh detach provenance must contain only "
                "FavoritesExternalRecordState values."
            )
        if not isinstance(self.provenance_path, Path):
            raise TypeError(
                "Durable refresh detach provenance path must be pathlib.Path."
            )
        if not self.provenance_path.is_absolute() or not self.provenance_path.name:
            raise ValueError(
                "Durable refresh detach provenance path must identify an absolute file."
            )
        if (
            self.provenance_path
            != self.plan.refresh_result.lifecycle_snapshot.provenance_path
        ):
            raise ValueError(
                "Durable refresh detach provenance path does not match "
                "the exact selected refresh evidence."
            )
        if (
            self.baseline_provenance_records
            != self.plan.refresh_result.lifecycle_snapshot.provenance_records
        ):
            raise ValueError(
                "Durable refresh detach baseline provenance does not match "
                "the exact selected refresh evidence."
            )

        expected_records = _replace_exact_baseline(
            self.baseline_provenance_records,
            self.plan,
        )
        if expected_records != self.provenance_records:
            raise ValueError(
                "Durable refresh detach provenance must equal the complete "
                "baseline collection with exactly one detached-state replacement."
            )
        if self.provenance_records.count(self.plan.intended_state) != 1:
            raise ValueError(
                "Durable refresh detach provenance must contain the exact "
                "intended detached state once."
            )


def _replace_exact_baseline(
    records: tuple[FavoritesExternalRecordState, ...],
    plan: FavoritesExternalRefreshDetachPlan,
) -> tuple[FavoritesExternalRecordState, ...]:
    indexes = tuple(
        index
        for index, record in enumerate(records)
        if record == plan.baseline_state
    )
    if len(indexes) != 1:
        raise FavoritesExternalRefreshDetachProvenanceError(
            "Refresh detach requires the exact persisted baseline provenance "
            "state once."
        )

    intended = list(records)
    intended[indexes[0]] = plan.intended_state
    return tuple(intended)


def execute_favorites_external_refresh_detach_durably(
    plan: FavoritesExternalRefreshDetachPlan,
    provenance_path: str | Path,
    *,
    max_bytes: int = FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_BYTES,
    max_records: int = FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_RECORDS,
    max_fields_per_record: int = (
        FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_FIELDS_PER_RECORD
    ),
    expected_baseline_provenance_records: (
        tuple[FavoritesExternalRecordState, ...] | None
    ) = None,
) -> FavoritesExternalRefreshDetachDurableResult:
    """Validate and conditionally publish one provenance-only refresh detach."""

    from .favorites_external_refresh_detach import FavoritesExternalRefreshDetachPlan

    if type(plan) is not FavoritesExternalRefreshDetachPlan:
        raise TypeError(
            "Durable refresh detach requires an exact "
            "FavoritesExternalRefreshDetachPlan."
        )
    if not isinstance(provenance_path, (str, Path)):
        raise TypeError(
            "Durable refresh detach provenance path must be str or pathlib.Path."
        )
    if isinstance(provenance_path, str) and not provenance_path.strip():
        raise ValueError(
            "Durable refresh detach provenance path must not be empty."
        )
    resolved_path = Path(provenance_path)
    if resolved_path != plan.refresh_result.lifecycle_snapshot.provenance_path:
        raise FavoritesExternalRefreshDetachProvenanceError(
            "Refresh detach provenance path does not match "
            "the exact selected refresh evidence."
        )

    if (
        expected_baseline_provenance_records is not None
        and type(expected_baseline_provenance_records) is not tuple
    ):
        raise TypeError(
            "Durable refresh detach expected baseline provenance records must "
            "be an immutable tuple or None."
        )
    if expected_baseline_provenance_records is not None and any(
        not isinstance(record, FavoritesExternalRecordState)
        for record in expected_baseline_provenance_records
    ):
        raise TypeError(
            "Durable refresh detach expected baseline provenance must contain "
            "only FavoritesExternalRecordState values."
        )

    favorites_snapshot = plan.refresh_result.lifecycle_snapshot.favorites_snapshot
    if favorites_snapshot is None:
        raise FavoritesExternalRefreshDetachProvenanceError(
            "Refresh detach requires retained Favorites snapshot evidence."
        )

    current_records = load_favorites_external_provenance(
        resolved_path,
        favorites_snapshot,
        max_bytes=max_bytes,
        max_records=max_records,
        max_fields_per_record=max_fields_per_record,
    )
    if current_records is None:
        raise FavoritesExternalRefreshDetachProvenanceError(
            "Refresh detach requires existing persisted provenance."
        )
    if (
        expected_baseline_provenance_records is not None
        and current_records != expected_baseline_provenance_records
    ):
        raise FavoritesExternalRefreshDetachProvenanceError(
            "Refresh detach persisted provenance does not match the exact "
            "expected baseline collection."
        )
    if (
        current_records
        != plan.refresh_result.lifecycle_snapshot.provenance_records
    ):
        raise FavoritesExternalRefreshDetachProvenanceError(
            "Refresh detach persisted provenance does not match "
            "the exact selected refresh baseline collection."
        )

    intended_records = _replace_exact_baseline(current_records, plan)
    intended_content = serialize_favorites_external_provenance(
        intended_records,
        max_bytes=max_bytes,
        max_records=max_records,
        max_fields_per_record=max_fields_per_record,
    )
    rebound = deserialize_favorites_external_provenance(
        intended_content,
        favorites_snapshot,
        max_bytes=max_bytes,
        max_records=max_records,
        max_fields_per_record=max_fields_per_record,
    )
    if rebound != intended_records:
        raise FavoritesExternalRefreshDetachProvenanceError(
            "Refresh detach intended provenance did not exactly rebind to "
            "the unchanged Favorites snapshot."
        )

    try:
        published_path = save_favorites_external_provenance_if_current(
            intended_records,
            resolved_path,
            expected_current_records=current_records,
            max_bytes=max_bytes,
            max_records=max_records,
            max_fields_per_record=max_fields_per_record,
        )
    except FavoritesExternalProvenanceStorageError:
        raise FavoritesExternalRefreshDetachPersistenceError(
            "Refresh detach provenance persistence did not complete."
        ) from None

    return FavoritesExternalRefreshDetachDurableResult(
        plan=plan,
        baseline_provenance_records=current_records,
        provenance_records=intended_records,
        provenance_path=published_path,
    )


__all__ = [
    "FavoritesExternalRefreshDetachDurableResult",
    "FavoritesExternalRefreshDetachPersistenceError",
    "FavoritesExternalRefreshDetachProvenanceError",
    "execute_favorites_external_refresh_detach_durably",
]
