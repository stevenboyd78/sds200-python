"""Assisted-refresh mapped-field acceptance execution orchestration."""

from __future__ import annotations

from dataclasses import dataclass

from .favorites_external_field_acceptance import (
    FavoritesExternalFieldAcceptanceExecutor,
)
from .favorites_external_field_provenance_acceptance import (
    FavoritesExternalFieldAcceptanceDurableResult,
)
from .favorites_external_provenance_lifecycle import (
    FavoritesExternalProvenanceLifecycle,
    FavoritesExternalProvenanceLifecycleSnapshot,
    FavoritesExternalProvenanceLifecycleState,
)
from .favorites_external_refresh_field_acceptance import (
    FavoritesExternalRefreshFieldAcceptancePlan,
)


@dataclass(frozen=True, slots=True)
class FavoritesExternalRefreshFieldAcceptanceResult:
    """Retain exact field planning, durability, and lifecycle evidence."""

    plan: FavoritesExternalRefreshFieldAcceptancePlan
    durable_result: FavoritesExternalFieldAcceptanceDurableResult
    lifecycle_snapshot: FavoritesExternalProvenanceLifecycleSnapshot

    def __post_init__(self) -> None:
        if type(self.plan) is not FavoritesExternalRefreshFieldAcceptancePlan:
            raise TypeError(
                "Refresh field acceptance result requires an exact "
                "FavoritesExternalRefreshFieldAcceptancePlan."
            )
        if type(self.durable_result) is not FavoritesExternalFieldAcceptanceDurableResult:
            raise TypeError(
                "Refresh field acceptance result requires an exact "
                "FavoritesExternalFieldAcceptanceDurableResult."
            )
        if type(self.lifecycle_snapshot) is not FavoritesExternalProvenanceLifecycleSnapshot:
            raise TypeError(
                "Refresh field acceptance result requires an exact "
                "FavoritesExternalProvenanceLifecycleSnapshot."
            )
        if self.durable_result.execution.plan != self.plan.acceptance_plan:
            raise ValueError(
                "Refresh field acceptance durable execution does not match "
                "the exact selected acceptance plan."
            )
        if self.lifecycle_snapshot.state is not FavoritesExternalProvenanceLifecycleState.ACTIVE:
            raise ValueError(
                "Refresh field acceptance result requires an active advanced lifecycle."
            )
        if self.lifecycle_snapshot.provenance_path != self.durable_result.provenance_path:
            raise ValueError(
                "Refresh field acceptance lifecycle path does not match durability."
            )
        if (
            self.lifecycle_snapshot.favorites_snapshot
            != self.durable_result.execution.observed_snapshot
        ):
            raise ValueError(
                "Refresh field acceptance lifecycle Favorites evidence does not "
                "match the verified observation."
            )
        if self.lifecycle_snapshot.provenance_records != self.durable_result.provenance_records:
            raise ValueError(
                "Refresh field acceptance lifecycle provenance does not match durability."
            )


def execute_favorites_external_refresh_field_acceptance(
    plan: FavoritesExternalRefreshFieldAcceptancePlan,
    lifecycle: FavoritesExternalProvenanceLifecycle,
    executor: FavoritesExternalFieldAcceptanceExecutor,
) -> FavoritesExternalRefreshFieldAcceptanceResult:
    """Execute one exact assisted-refresh mapped-field selection durably."""

    if type(plan) is not FavoritesExternalRefreshFieldAcceptancePlan:
        raise TypeError(
            "Refresh field acceptance execution requires an exact "
            "FavoritesExternalRefreshFieldAcceptancePlan."
        )
    if not isinstance(lifecycle, FavoritesExternalProvenanceLifecycle):
        raise TypeError(
            "Refresh field acceptance execution requires "
            "FavoritesExternalProvenanceLifecycle."
        )
    if not callable(executor):
        raise TypeError("Refresh field acceptance execution requires a callable executor.")

    durable_result, lifecycle_snapshot = (
        lifecycle._execute_field_acceptance_durably_from_snapshot(
            plan.refresh_result.lifecycle_snapshot,
            plan.acceptance_plan,
            executor,
        )
    )
    return FavoritesExternalRefreshFieldAcceptanceResult(
        plan=plan,
        durable_result=durable_result,
        lifecycle_snapshot=lifecycle_snapshot,
    )


__all__ = [
    "FavoritesExternalRefreshFieldAcceptanceResult",
    "execute_favorites_external_refresh_field_acceptance",
]
