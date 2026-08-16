"""Assisted-refresh name-acceptance execution orchestration."""

from __future__ import annotations

from dataclasses import dataclass

from .favorites_external import FavoritesExternalNameAcceptanceExecutor
from .favorites_external_provenance_acceptance import (
    FavoritesExternalNameAcceptanceDurableResult,
)
from .favorites_external_provenance_lifecycle import (
    FavoritesExternalProvenanceLifecycle,
    FavoritesExternalProvenanceLifecycleSnapshot,
    FavoritesExternalProvenanceLifecycleState,
)
from .favorites_external_refresh_acceptance import (
    FavoritesExternalRefreshNameAcceptancePlan,
)


@dataclass(frozen=True, slots=True)
class FavoritesExternalRefreshNameAcceptanceResult:
    """Retain exact planning, durability, and lifecycle-advancement evidence."""

    plan: FavoritesExternalRefreshNameAcceptancePlan
    durable_result: FavoritesExternalNameAcceptanceDurableResult
    lifecycle_snapshot: FavoritesExternalProvenanceLifecycleSnapshot

    def __post_init__(self) -> None:
        if type(self.plan) is not FavoritesExternalRefreshNameAcceptancePlan:
            raise TypeError(
                "Refresh name acceptance result requires an exact "
                "FavoritesExternalRefreshNameAcceptancePlan."
            )
        if type(self.durable_result) is not FavoritesExternalNameAcceptanceDurableResult:
            raise TypeError(
                "Refresh name acceptance result requires an exact "
                "FavoritesExternalNameAcceptanceDurableResult."
            )
        if type(self.lifecycle_snapshot) is not FavoritesExternalProvenanceLifecycleSnapshot:
            raise TypeError(
                "Refresh name acceptance result requires an exact "
                "FavoritesExternalProvenanceLifecycleSnapshot."
            )
        if self.durable_result.execution.plan != self.plan.acceptance_plan:
            raise ValueError(
                "Refresh name acceptance durable execution does not match "
                "the exact selected acceptance plan."
            )
        if (
            self.lifecycle_snapshot.state
            is not FavoritesExternalProvenanceLifecycleState.ACTIVE
        ):
            raise ValueError(
                "Refresh name acceptance result requires an active advanced lifecycle."
            )
        if self.lifecycle_snapshot.provenance_path != self.durable_result.provenance_path:
            raise ValueError(
                "Refresh name acceptance lifecycle path does not match "
                "the durable acceptance."
            )
        if (
            self.lifecycle_snapshot.favorites_snapshot
            != self.durable_result.execution.observed_snapshot
        ):
            raise ValueError(
                "Refresh name acceptance lifecycle Favorites evidence does not "
                "match the durable acceptance."
            )
        if (
            self.lifecycle_snapshot.provenance_records
            != self.durable_result.provenance_records
        ):
            raise ValueError(
                "Refresh name acceptance lifecycle provenance does not match "
                "the durable acceptance."
            )


def execute_favorites_external_refresh_name_acceptance(
    plan: FavoritesExternalRefreshNameAcceptancePlan,
    lifecycle: FavoritesExternalProvenanceLifecycle,
    executor: FavoritesExternalNameAcceptanceExecutor,
) -> FavoritesExternalRefreshNameAcceptanceResult:
    """Execute one exact assisted-refresh name selection through durability."""

    if type(plan) is not FavoritesExternalRefreshNameAcceptancePlan:
        raise TypeError(
            "Refresh name acceptance execution requires an exact "
            "FavoritesExternalRefreshNameAcceptancePlan."
        )
    if not isinstance(lifecycle, FavoritesExternalProvenanceLifecycle):
        raise TypeError(
            "Refresh name acceptance execution requires "
            "FavoritesExternalProvenanceLifecycle."
        )
    if not callable(executor):
        raise TypeError(
            "Refresh name acceptance execution requires a callable executor."
        )

    durable_result, lifecycle_snapshot = (
        lifecycle._execute_name_acceptance_durably_from_snapshot(
            plan.refresh_result.lifecycle_snapshot,
            plan.acceptance_plan,
            executor,
        )
    )
    return FavoritesExternalRefreshNameAcceptanceResult(
        plan=plan,
        durable_result=durable_result,
        lifecycle_snapshot=lifecycle_snapshot,
    )


__all__ = [
    "FavoritesExternalRefreshNameAcceptanceResult",
    "execute_favorites_external_refresh_name_acceptance",
]
