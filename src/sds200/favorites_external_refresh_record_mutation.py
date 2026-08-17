"""Durability boundary for structural assisted-refresh record mutations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeAlias

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
from .favorites_external_refresh_record_import import FavoritesExternalRefreshRecordImportPlan
from .favorites_external_refresh_record_removal import FavoritesExternalRefreshRecordDeletePlan
from .favorites_storage import FavoritesStorageSnapshot, FavoritesStorageSource
from .favorites_write_plan import FavoritesWritePlan


class FavoritesExternalRefreshRecordMutationError(ValueError):
    """Report invalid structural planning, provenance, or readback evidence."""


class FavoritesExternalRefreshRecordMutationPersistenceError(RuntimeError):
    """Report failed provenance publication after verified structural storage."""


class FavoritesExternalRefreshRecordMutationExecutor(Protocol):
    def __call__(self, plan: FavoritesWritePlan, /) -> object: ...


FavoritesExternalRefreshRecordMutationPlan: TypeAlias = (
    FavoritesExternalRefreshRecordImportPlan | FavoritesExternalRefreshRecordDeletePlan
)


@dataclass(frozen=True, slots=True)
class FavoritesExternalRefreshRecordMutationDurableResult:
    plan: FavoritesExternalRefreshRecordMutationPlan
    execution_result: object
    observed_snapshot: FavoritesStorageSnapshot
    baseline_provenance_records: tuple[FavoritesExternalRecordState, ...] | None
    intended_provenance_records: tuple[FavoritesExternalRecordState, ...]
    provenance_path: Path

    def __post_init__(self) -> None:
        if type(self.plan) not in {
            FavoritesExternalRefreshRecordImportPlan,
            FavoritesExternalRefreshRecordDeletePlan,
        }:
            raise TypeError("Structural mutation result requires an exact import or delete plan.")
        if not isinstance(self.observed_snapshot, FavoritesStorageSnapshot):
            raise TypeError(
                "Structural mutation result requires FavoritesStorageSnapshot evidence."
            )
        if self.baseline_provenance_records is not None and type(
            self.baseline_provenance_records
        ) is not tuple:
            raise TypeError(
                "Structural mutation result baseline provenance must be a tuple or None."
            )
        if self.baseline_provenance_records is not None and any(
            not isinstance(record, FavoritesExternalRecordState)
            for record in self.baseline_provenance_records
        ):
            raise TypeError(
                "Structural mutation result baseline provenance contains invalid records."
            )
        if type(self.intended_provenance_records) is not tuple:
            raise TypeError(
                "Structural mutation result intended provenance must be an immutable tuple."
            )
        if any(
            not isinstance(record, FavoritesExternalRecordState)
            for record in self.intended_provenance_records
        ):
            raise TypeError(
                "Structural mutation result intended provenance contains invalid records."
            )
        if not isinstance(self.provenance_path, Path):
            raise TypeError(
                "Structural mutation result provenance path must be pathlib.Path."
            )
        if not self.provenance_path.is_absolute() or not self.provenance_path.name:
            raise ValueError(
                "Structural mutation result provenance path must identify an absolute file."
            )
        if self.observed_snapshot != self.plan.write_plan.intended_snapshot:
            raise ValueError("Structural mutation result requires the exact intended snapshot.")
        if self.baseline_provenance_records != self.plan.baseline_provenance_records:
            raise ValueError("Structural mutation result baseline does not match its plan.")
        if self.intended_provenance_records != self.plan.intended_provenance_records:
            raise ValueError("Structural mutation result provenance does not match its plan.")
        if self.provenance_path != self.plan.refresh_result.lifecycle_snapshot.provenance_path:
            raise ValueError("Structural mutation result provenance path does not match its plan.")


def execute_favorites_external_refresh_record_mutation_durably(
    plan: FavoritesExternalRefreshRecordMutationPlan,
    executor: FavoritesExternalRefreshRecordMutationExecutor,
    storage_source: FavoritesStorageSource,
    provenance_path: str | Path,
    *,
    max_bytes: int = FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_BYTES,
    max_records: int = FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_RECORDS,
    max_fields_per_record: int = FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_FIELDS_PER_RECORD,
) -> FavoritesExternalRefreshRecordMutationDurableResult:
    """Execute, independently verify, and conditionally publish one mutation."""
    if type(plan) not in {
        FavoritesExternalRefreshRecordImportPlan,
        FavoritesExternalRefreshRecordDeletePlan,
    }:
        raise TypeError("Structural mutation requires an exact import or delete plan.")
    if not callable(executor):
        raise TypeError("Structural mutation executor must be callable.")
    reader = getattr(storage_source, "read_snapshot", None)
    if not callable(reader):
        raise TypeError("Structural mutation storage source must provide read_snapshot().")
    if not isinstance(provenance_path, (str, Path)):
        raise TypeError("Structural mutation provenance path must be str or pathlib.Path.")
    resolved = Path(provenance_path)
    if resolved != plan.refresh_result.lifecycle_snapshot.provenance_path:
        raise FavoritesExternalRefreshRecordMutationError(
            "Structural mutation provenance path does not match the selected refresh evidence."
        )
    current = load_favorites_external_provenance(
        resolved, plan.write_plan.baseline_snapshot,
        max_bytes=max_bytes, max_records=max_records,
        max_fields_per_record=max_fields_per_record,
    )
    if current != plan.baseline_provenance_records:
        raise FavoritesExternalRefreshRecordMutationError(
            "Structural mutation persisted provenance does not match the exact baseline."
        )
    content = serialize_favorites_external_provenance(
        plan.intended_provenance_records,
        max_bytes=max_bytes, max_records=max_records,
        max_fields_per_record=max_fields_per_record,
    )
    rebound = deserialize_favorites_external_provenance(
        content, plan.write_plan.intended_snapshot,
        max_bytes=max_bytes, max_records=max_records,
        max_fields_per_record=max_fields_per_record,
    )
    if rebound != plan.intended_provenance_records:
        raise FavoritesExternalRefreshRecordMutationError(
            "Structural mutation intended provenance did not exactly rebind."
        )
    execution_result = executor(plan.write_plan)
    try:
        observed = reader()
    except Exception:
        raise FavoritesExternalRefreshRecordMutationError(
            "Structural mutation could not verify the post-write storage snapshot."
        ) from None
    if not isinstance(observed, FavoritesStorageSnapshot):
        raise FavoritesExternalRefreshRecordMutationError(
            "Structural mutation returned invalid post-write storage evidence."
        )
    if observed != plan.write_plan.intended_snapshot:
        raise FavoritesExternalRefreshRecordMutationError(
            "Structural mutation post-write storage does not exactly match the intended snapshot."
        )
    try:
        published = save_favorites_external_provenance_if_current(
            plan.intended_provenance_records, resolved,
            expected_current_records=current,
            max_bytes=max_bytes, max_records=max_records,
            max_fields_per_record=max_fields_per_record,
        )
    except FavoritesExternalProvenanceStorageError:
        raise FavoritesExternalRefreshRecordMutationPersistenceError(
            "Structural Favorites storage was verified, but provenance "
            "persistence did not complete."
        ) from None
    return FavoritesExternalRefreshRecordMutationDurableResult(
        plan, execution_result, observed, current,
        plan.intended_provenance_records, published,
    )


__all__ = [
    "FavoritesExternalRefreshRecordMutationDurableResult",
    "FavoritesExternalRefreshRecordMutationError",
    "FavoritesExternalRefreshRecordMutationExecutor",
    "FavoritesExternalRefreshRecordMutationPersistenceError",
    "FavoritesExternalRefreshRecordMutationPlan",
    "execute_favorites_external_refresh_record_mutation_durably",
]
