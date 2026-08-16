"""Assisted-refresh detach durability and lifecycle orchestration."""

from __future__ import annotations

from dataclasses import dataclass

from .favorites_external_provenance_detach import (
    FavoritesExternalRefreshDetachDurableResult,
)
from .favorites_external_provenance_lifecycle import (
    FavoritesExternalProvenanceLifecycle,
    FavoritesExternalProvenanceLifecycleSnapshot,
    FavoritesExternalProvenanceLifecycleState,
)
from .favorites_external_refresh_detach import FavoritesExternalRefreshDetachPlan


@dataclass(frozen=True, slots=True)
class FavoritesExternalRefreshDetachResult:
    """Retain exact detach planning, durability, and lifecycle evidence."""

    plan: FavoritesExternalRefreshDetachPlan
    durable_result: FavoritesExternalRefreshDetachDurableResult
    lifecycle_snapshot: FavoritesExternalProvenanceLifecycleSnapshot

    def __post_init__(self) -> None:
        if type(self.plan) is not FavoritesExternalRefreshDetachPlan:
            raise TypeError(
                "Refresh detach result requires an exact "
                "FavoritesExternalRefreshDetachPlan."
            )
        if type(self.durable_result) is not FavoritesExternalRefreshDetachDurableResult:
            raise TypeError(
                "Refresh detach result requires an exact "
                "FavoritesExternalRefreshDetachDurableResult."
            )
        if type(self.lifecycle_snapshot) is not FavoritesExternalProvenanceLifecycleSnapshot:
            raise TypeError(
                "Refresh detach result requires an exact "
                "FavoritesExternalProvenanceLifecycleSnapshot."
            )
        if self.durable_result.plan != self.plan:
            raise ValueError(
                "Refresh detach durable result does not match the exact selected plan."
            )
        if (
            self.lifecycle_snapshot.state
            is not FavoritesExternalProvenanceLifecycleState.ACTIVE
        ):
            raise ValueError(
                "Refresh detach result requires an active advanced lifecycle."
            )
        if self.lifecycle_snapshot.provenance_path != self.durable_result.provenance_path:
            raise ValueError(
                "Refresh detach lifecycle path does not match the durable detach."
            )
        if (
            self.lifecycle_snapshot.favorites_snapshot
            != self.plan.refresh_result.lifecycle_snapshot.favorites_snapshot
        ):
            raise ValueError(
                "Refresh detach lifecycle Favorites evidence must remain "
                "the selected refresh baseline snapshot."
            )
        if (
            self.lifecycle_snapshot.provenance_records
            != self.durable_result.provenance_records
        ):
            raise ValueError(
                "Refresh detach lifecycle provenance does not match "
                "the durable detach."
            )


def execute_favorites_external_refresh_detach(
    plan: FavoritesExternalRefreshDetachPlan,
    lifecycle: FavoritesExternalProvenanceLifecycle,
) -> FavoritesExternalRefreshDetachResult:
    """Durably publish and adopt one exact assisted-refresh detach decision."""

    if type(plan) is not FavoritesExternalRefreshDetachPlan:
        raise TypeError(
            "Refresh detach execution requires an exact "
            "FavoritesExternalRefreshDetachPlan."
        )
    if not isinstance(lifecycle, FavoritesExternalProvenanceLifecycle):
        raise TypeError(
            "Refresh detach execution requires "
            "FavoritesExternalProvenanceLifecycle."
        )

    durable_result, lifecycle_snapshot = (
        lifecycle._execute_refresh_detach_durably_from_snapshot(
            plan.refresh_result.lifecycle_snapshot,
            plan,
        )
    )
    return FavoritesExternalRefreshDetachResult(
        plan=plan,
        durable_result=durable_result,
        lifecycle_snapshot=lifecycle_snapshot,
    )


__all__ = [
    "FavoritesExternalRefreshDetachResult",
    "execute_favorites_external_refresh_detach",
]
