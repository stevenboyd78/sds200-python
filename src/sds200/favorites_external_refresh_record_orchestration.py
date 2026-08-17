"""Lifecycle orchestration for structural assisted-refresh mutations."""

from __future__ import annotations

from dataclasses import dataclass

from .favorites_external_provenance_lifecycle import (
    FavoritesExternalProvenanceLifecycle,
    FavoritesExternalProvenanceLifecycleSnapshot,
    FavoritesExternalProvenanceLifecycleState,
)
from .favorites_external_refresh_record_import import (
    FavoritesExternalRefreshRecordImportPlan,
)
from .favorites_external_refresh_record_mutation import (
    FavoritesExternalRefreshRecordMutationDurableResult,
    FavoritesExternalRefreshRecordMutationExecutor,
    FavoritesExternalRefreshRecordMutationPlan,
)
from .favorites_external_refresh_record_removal import (
    FavoritesExternalRefreshRecordDeletePlan,
)


@dataclass(frozen=True, slots=True)
class FavoritesExternalRefreshRecordMutationResult:
    plan: FavoritesExternalRefreshRecordMutationPlan
    durable_result: FavoritesExternalRefreshRecordMutationDurableResult
    lifecycle_snapshot: FavoritesExternalProvenanceLifecycleSnapshot

    def __post_init__(self) -> None:
        if type(self.plan) not in {
            FavoritesExternalRefreshRecordImportPlan,
            FavoritesExternalRefreshRecordDeletePlan,
        }:
            raise TypeError(
                "Structural mutation result requires an exact import or delete plan."
            )
        if type(self.durable_result) is not FavoritesExternalRefreshRecordMutationDurableResult:
            raise TypeError(
                "Structural mutation result requires an exact durable result."
            )
        if type(self.lifecycle_snapshot) is not FavoritesExternalProvenanceLifecycleSnapshot:
            raise TypeError(
                "Structural mutation result requires an exact lifecycle snapshot."
            )
        if self.durable_result.plan is not self.plan:
            raise ValueError("Structural mutation durable result must retain the exact plan.")
        if self.lifecycle_snapshot.state is not FavoritesExternalProvenanceLifecycleState.ACTIVE:
            raise ValueError("Structural mutation requires an active advanced lifecycle.")
        if self.lifecycle_snapshot.provenance_path != self.durable_result.provenance_path:
            raise ValueError("Structural mutation lifecycle path does not match publication.")
        if self.lifecycle_snapshot.favorites_snapshot != self.durable_result.observed_snapshot:
            raise ValueError("Structural mutation lifecycle did not adopt verified storage.")
        if (
            self.lifecycle_snapshot.provenance_records
            != self.durable_result.intended_provenance_records
        ):
            raise ValueError("Structural mutation lifecycle did not adopt complete provenance.")


def execute_favorites_external_refresh_record_mutation(
    plan: FavoritesExternalRefreshRecordMutationPlan,
    lifecycle: FavoritesExternalProvenanceLifecycle,
    executor: FavoritesExternalRefreshRecordMutationExecutor,
) -> FavoritesExternalRefreshRecordMutationResult:
    """Execute one exact plan only through the lifecycle critical section."""
    if not isinstance(lifecycle, FavoritesExternalProvenanceLifecycle):
        raise TypeError("Structural mutation requires FavoritesExternalProvenanceLifecycle.")
    durable, snapshot = lifecycle._execute_record_mutation_durably_from_snapshot(
        plan.refresh_result.lifecycle_snapshot, plan, executor
    )
    return FavoritesExternalRefreshRecordMutationResult(plan, durable, snapshot)


__all__ = [
    "FavoritesExternalRefreshRecordMutationResult",
    "execute_favorites_external_refresh_record_mutation",
]
