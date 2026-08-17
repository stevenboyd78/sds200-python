"""Pure assisted-refresh planning for provider-removal decisions."""

from __future__ import annotations

from dataclasses import dataclass

from .favorites_editing import delete_favorites_record
from .favorites_external import (
    FavoritesExternalChangeKind,
    FavoritesExternalRecordObservation,
    FavoritesExternalRecordObservationState,
    FavoritesExternalRecordPreview,
    FavoritesExternalRecordState,
)
from .favorites_external_refresh import FavoritesExternalRefreshResult
from .favorites_external_refresh_detach import (
    FavoritesExternalRefreshDetachPlan,
    FavoritesExternalRefreshDetachScope,
    plan_favorites_external_refresh_detach,
)
from .favorites_external_refresh_record_import import (
    _prove_provenance,
    _reselect_shifted_state,
)
from .favorites_write_plan import FavoritesWritePlan, plan_favorites_write


def _removal_observation(
    refresh_result: FavoritesExternalRefreshResult,
    selected: FavoritesExternalRecordPreview,
) -> FavoritesExternalRecordObservation:
    if (
        type(refresh_result) is not FavoritesExternalRefreshResult
        or type(selected) is not FavoritesExternalRecordPreview
    ):
        raise TypeError("Record removal requires exact refresh and preview values.")
    if sum(preview is selected for preview in refresh_result.preview.records) != 1:
        raise ValueError("Record removal requires the exact retained selected preview once.")
    if selected.target is None or selected.external_identity is None or selected.evidence is None:
        raise ValueError("Record removal requires a linked provider preview.")
    matches = tuple(
        observation for observation in refresh_result.observations
        if observation.identity == selected.external_identity
        and observation.evidence == selected.evidence
        and observation.state is FavoritesExternalRecordObservationState.REMOVED
    )
    if len(matches) != 1 or sum(item is matches[0] for item in refresh_result.observations) != 1:
        raise ValueError("Record removal requires one exact retained removed observation.")
    return matches[0]


def _derive_delete(
    refresh_result: FavoritesExternalRefreshResult,
    selected: FavoritesExternalRecordPreview,
) -> tuple[
    FavoritesExternalRecordObservation,
    FavoritesExternalRecordState,
    FavoritesWritePlan,
    tuple[FavoritesExternalRecordState, ...],
    tuple[FavoritesExternalRecordState, ...],
]:
    observation = _removal_observation(refresh_result, selected)
    if selected.kind is not FavoritesExternalChangeKind.REMOVED:
        raise ValueError("Record deletion requires an unambiguous REMOVED preview.")
    baseline_records = refresh_result.lifecycle_snapshot.provenance_records
    if baseline_records is None:
        raise ValueError("Record deletion requires persisted baseline provenance.")
    matches = tuple(
        state for state in baseline_records
        if state.target == selected.target and state.external_identity == selected.external_identity
    )
    if len(matches) != 1:
        raise ValueError("Record deletion requires one exact linked baseline state.")
    baseline = matches[0]
    if baseline.detached:
        raise ValueError("A detached record cannot be selected for provider deletion.")
    if baseline.target.document_index is None:
        raise ValueError("Record deletion requires an HPD target document index.")
    snapshot = refresh_result.lifecycle_snapshot.favorites_snapshot
    if snapshot is None:
        raise ValueError("Record deletion requires an active Favorites snapshot.")
    intended_snapshot = delete_favorites_record(snapshot, baseline.target)
    write_plan = plan_favorites_write(snapshot, intended_snapshot)
    if write_plan.is_blocked or not write_plan.has_changes:
        raise ValueError("Record deletion requires a real unblocked byte-changing write plan.")
    intended_records = tuple(
        _reselect_shifted_state(
            state, intended_snapshot,
            document_index=baseline.target.document_index,
            pivot=baseline.target.source_index, delta=-1,
        )
        for state in baseline_records if state is not baseline
    )
    if len(intended_records) != len(baseline_records) - 1:
        raise ValueError("Record deletion must remove exactly one provenance state.")
    _prove_provenance(intended_records, intended_snapshot)
    return observation, baseline, write_plan, baseline_records, intended_records


@dataclass(frozen=True, slots=True)
class FavoritesExternalRefreshRecordDeletePlan:
    refresh_result: FavoritesExternalRefreshResult
    selected_preview: FavoritesExternalRecordPreview
    observation: FavoritesExternalRecordObservation
    baseline_state: FavoritesExternalRecordState
    write_plan: FavoritesWritePlan
    baseline_provenance_records: tuple[FavoritesExternalRecordState, ...]
    intended_provenance_records: tuple[FavoritesExternalRecordState, ...]

    def __post_init__(self) -> None:
        derived = _derive_delete(self.refresh_result, self.selected_preview)
        if self.observation is not derived[0] or self.baseline_state is not derived[1] or (
            self.write_plan != derived[2] or self.baseline_provenance_records != derived[3]
            or self.intended_provenance_records != derived[4]
        ):
            raise ValueError("Record delete plan does not match its exact refresh evidence.")


def plan_favorites_external_refresh_record_delete(
    refresh_result: FavoritesExternalRefreshResult,
    selected_preview: FavoritesExternalRecordPreview,
) -> FavoritesExternalRefreshRecordDeletePlan:
    """Plan one exact unambiguous provider-owned leaf deletion."""
    values = _derive_delete(refresh_result, selected_preview)
    return FavoritesExternalRefreshRecordDeletePlan(refresh_result, selected_preview, *values)


def plan_favorites_external_refresh_record_keep_local(
    refresh_result: FavoritesExternalRefreshResult,
    selected_preview: FavoritesExternalRecordPreview,
) -> FavoritesExternalRefreshDetachPlan:
    """Preserve local bytes and delegate a provider-removal record detach."""
    _removal_observation(refresh_result, selected_preview)
    if selected_preview.kind not in {
        FavoritesExternalChangeKind.REMOVED,
        FavoritesExternalChangeKind.CONFLICT,
    }:
        raise ValueError("Keep-local requires a provider-removal preview.")
    return plan_favorites_external_refresh_detach(
        refresh_result, selected_preview, FavoritesExternalRefreshDetachScope.RECORD
    )


__all__ = [
    "FavoritesExternalRefreshRecordDeletePlan",
    "plan_favorites_external_refresh_record_delete",
    "plan_favorites_external_refresh_record_keep_local",
]
