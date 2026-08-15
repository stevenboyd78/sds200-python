"""Durable provenance completion for verified external Favorites acceptance."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .favorites_external import (
    FavoritesExternalNameAcceptanceExecutionResult,
    FavoritesExternalNameAcceptanceExecutor,
    FavoritesExternalNameAcceptancePlan,
    FavoritesExternalRecordState,
    execute_favorites_external_name_acceptance,
)
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
from .favorites_storage import FavoritesStorageSource


class FavoritesExternalNameAcceptanceProvenanceError(ValueError):
    """Report invalid persisted provenance for one planned name acceptance."""


class FavoritesExternalNameAcceptancePersistenceError(RuntimeError):
    """Report failed provenance publication after verified Favorites mutation."""


@dataclass(frozen=True, slots=True)
class FavoritesExternalNameAcceptanceDurableResult:
    """Verified name acceptance with durably published complete provenance."""

    execution: FavoritesExternalNameAcceptanceExecutionResult
    baseline_provenance_records: tuple[FavoritesExternalRecordState, ...]
    provenance_records: tuple[FavoritesExternalRecordState, ...]
    provenance_path: Path

    def __post_init__(self) -> None:
        if not isinstance(
            self.execution,
            FavoritesExternalNameAcceptanceExecutionResult,
        ):
            raise TypeError(
                "Durable external Favorites name acceptance execution must be "
                "FavoritesExternalNameAcceptanceExecutionResult."
            )
        if type(self.baseline_provenance_records) is not tuple:
            raise TypeError(
                "Durable external Favorites baseline provenance records must be "
                "an immutable tuple."
            )
        if type(self.provenance_records) is not tuple:
            raise TypeError(
                "Durable external Favorites provenance records must be an immutable tuple."
            )
        if any(
            not isinstance(record, FavoritesExternalRecordState)
            for record in self.baseline_provenance_records
        ):
            raise TypeError(
                "Durable external Favorites baseline provenance must contain only "
                "FavoritesExternalRecordState values."
            )
        if any(
            not isinstance(record, FavoritesExternalRecordState)
            for record in self.provenance_records
        ):
            raise TypeError(
                "Durable external Favorites provenance must contain only "
                "FavoritesExternalRecordState values."
            )
        if not isinstance(self.provenance_path, Path):
            raise TypeError(
                "Durable external Favorites provenance path must be pathlib.Path."
            )
        expected_records = _replace_exact_baseline(
            self.baseline_provenance_records,
            self.execution.plan,
        )
        if expected_records != self.provenance_records:
            raise ValueError(
                "Durable external Favorites provenance must equal the complete "
                "baseline collection with exactly one accepted-state replacement."
            )
        if self.provenance_records.count(self.execution.accepted_state) != 1:
            raise ValueError(
                "Durable external Favorites provenance must contain the exact "
                "accepted state once."
            )


def _replace_exact_baseline(
    records: tuple[FavoritesExternalRecordState, ...],
    plan: FavoritesExternalNameAcceptancePlan,
) -> tuple[FavoritesExternalRecordState, ...]:
    indexes = tuple(
        index
        for index, record in enumerate(records)
        if record == plan.baseline_state
    )
    if len(indexes) != 1:
        raise FavoritesExternalNameAcceptanceProvenanceError(
            "External Favorites name acceptance requires the exact persisted "
            "baseline provenance state once."
        )

    intended = list(records)
    intended[indexes[0]] = plan.intended_state
    return tuple(intended)


def execute_favorites_external_name_acceptance_durably(
    plan: FavoritesExternalNameAcceptancePlan,
    executor: FavoritesExternalNameAcceptanceExecutor,
    storage_source: FavoritesStorageSource,
    provenance_path: str | Path,
    *,
    max_bytes: int = FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_BYTES,
    max_records: int = FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_RECORDS,
    max_fields_per_record: int = (
        FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_FIELDS_PER_RECORD
    ),
) -> FavoritesExternalNameAcceptanceDurableResult:
    """Execute, verify, and durably complete one planned name acceptance."""

    if not isinstance(plan, FavoritesExternalNameAcceptancePlan):
        raise TypeError(
            "Durable external Favorites name acceptance requires "
            "FavoritesExternalNameAcceptancePlan."
        )

    current_records = load_favorites_external_provenance(
        provenance_path,
        plan.write_plan.baseline_snapshot,
        max_bytes=max_bytes,
        max_records=max_records,
        max_fields_per_record=max_fields_per_record,
    )
    if current_records is None:
        raise FavoritesExternalNameAcceptanceProvenanceError(
            "External Favorites name acceptance requires existing persisted provenance."
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
        plan.write_plan.intended_snapshot,
        max_bytes=max_bytes,
        max_records=max_records,
        max_fields_per_record=max_fields_per_record,
    )
    if rebound != intended_records:
        raise FavoritesExternalNameAcceptanceProvenanceError(
            "External Favorites name acceptance intended provenance did not "
            "exactly rebind to the intended snapshot."
        )

    execution = execute_favorites_external_name_acceptance(
        plan,
        executor,
        storage_source,
    )

    try:
        published_path = save_favorites_external_provenance_if_current(
            intended_records,
            provenance_path,
            expected_current_records=current_records,
            max_bytes=max_bytes,
            max_records=max_records,
            max_fields_per_record=max_fields_per_record,
        )
    except FavoritesExternalProvenanceStorageError:
        raise FavoritesExternalNameAcceptancePersistenceError(
            "External Favorites name acceptance storage was verified, but "
            "provenance persistence did not complete."
        ) from None

    return FavoritesExternalNameAcceptanceDurableResult(
        execution=execution,
        baseline_provenance_records=current_records,
        provenance_records=intended_records,
        provenance_path=published_path,
    )


__all__ = [
    "FavoritesExternalNameAcceptanceDurableResult",
    "FavoritesExternalNameAcceptancePersistenceError",
    "FavoritesExternalNameAcceptanceProvenanceError",
    "execute_favorites_external_name_acceptance_durably",
]
