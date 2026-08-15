"""Pure assisted-refresh selection to name-acceptance planning composition."""

from __future__ import annotations

from dataclasses import dataclass

from .favorites_external import (
    FavoritesExternalNameAcceptancePlan,
    FavoritesExternalRecordObservation,
    FavoritesExternalRecordPreview,
    FavoritesExternalRecordState,
    plan_favorites_external_name_acceptance,
)
from .favorites_external_refresh import FavoritesExternalRefreshResult


@dataclass(frozen=True, slots=True)
class FavoritesExternalRefreshNameAcceptancePlan:
    """Retain the exact refresh selection and delegated acceptance evidence."""

    refresh_result: FavoritesExternalRefreshResult
    selected_preview: FavoritesExternalRecordPreview
    observation: FavoritesExternalRecordObservation
    baseline_state: FavoritesExternalRecordState
    acceptance_plan: FavoritesExternalNameAcceptancePlan

    def __post_init__(self) -> None:
        if not isinstance(self.refresh_result, FavoritesExternalRefreshResult):
            raise TypeError(
                "Refresh name acceptance requires FavoritesExternalRefreshResult."
            )
        if not isinstance(self.selected_preview, FavoritesExternalRecordPreview):
            raise TypeError(
                "Refresh name acceptance requires FavoritesExternalRecordPreview."
            )
        if not isinstance(self.observation, FavoritesExternalRecordObservation):
            raise TypeError(
                "Refresh name acceptance requires FavoritesExternalRecordObservation."
            )
        if not isinstance(self.baseline_state, FavoritesExternalRecordState):
            raise TypeError(
                "Refresh name acceptance requires FavoritesExternalRecordState."
            )
        if not isinstance(self.acceptance_plan, FavoritesExternalNameAcceptancePlan):
            raise TypeError(
                "Refresh name acceptance requires FavoritesExternalNameAcceptancePlan."
            )

        baseline_state, observation, acceptance_plan = _derive_acceptance(
            self.refresh_result,
            self.selected_preview,
        )
        if self.baseline_state != baseline_state:
            raise ValueError(
                "Refresh name acceptance baseline state does not match the exact "
                "selected refresh evidence."
            )
        if self.observation != observation:
            raise ValueError(
                "Refresh name acceptance observation does not match the exact "
                "selected refresh evidence."
            )
        if self.acceptance_plan != acceptance_plan:
            raise ValueError(
                "Refresh name acceptance delegated plan does not match the exact "
                "selected refresh evidence."
            )


def _derive_acceptance(
    refresh_result: FavoritesExternalRefreshResult,
    selected_preview: FavoritesExternalRecordPreview,
) -> tuple[
    FavoritesExternalRecordState,
    FavoritesExternalRecordObservation,
    FavoritesExternalNameAcceptancePlan,
]:
    if not isinstance(refresh_result, FavoritesExternalRefreshResult):
        raise TypeError(
            "Refresh name acceptance requires FavoritesExternalRefreshResult."
        )
    if not isinstance(selected_preview, FavoritesExternalRecordPreview):
        raise TypeError(
            "Refresh name acceptance requires FavoritesExternalRecordPreview."
        )
    if refresh_result.preview.records.count(selected_preview) != 1:
        raise ValueError(
            "Refresh name acceptance requires the exact selected preview once."
        )
    if selected_preview.target is None or selected_preview.external_identity is None:
        raise ValueError(
            "Refresh name acceptance requires a linked selected preview."
        )

    provenance_records = refresh_result.lifecycle_snapshot.provenance_records
    if not provenance_records:
        raise ValueError(
            "Refresh name acceptance requires an existing linked baseline record."
        )
    baselines = tuple(
        record
        for record in provenance_records
        if record.target == selected_preview.target
        and record.external_identity == selected_preview.external_identity
    )
    if len(baselines) != 1:
        raise ValueError(
            "Refresh name acceptance requires one exact linked baseline record."
        )

    observations = tuple(
        observation
        for observation in refresh_result.observations
        if observation.identity == selected_preview.external_identity
        and observation.evidence == selected_preview.evidence
    )
    if len(observations) != 1:
        raise ValueError(
            "Refresh name acceptance requires one exact matching observation."
        )

    baseline_state = baselines[0]
    observation = observations[0]
    favorites_snapshot = refresh_result.lifecycle_snapshot.favorites_snapshot
    if favorites_snapshot is None:
        raise ValueError(
            "Refresh name acceptance requires the active Favorites snapshot."
        )
    acceptance_plan = plan_favorites_external_name_acceptance(
        favorites_snapshot,
        baseline_state,
        observation,
    )
    if acceptance_plan.preview != selected_preview:
        raise ValueError(
            "Refresh name acceptance delegated preview must equal the exact selection."
        )
    return baseline_state, observation, acceptance_plan


def plan_favorites_external_refresh_name_acceptance(
    refresh_result: FavoritesExternalRefreshResult,
    selected_preview: FavoritesExternalRecordPreview,
) -> FavoritesExternalRefreshNameAcceptancePlan:
    """Compose one exact refresh preview selection into an existing name plan."""

    baseline_state, observation, acceptance_plan = _derive_acceptance(
        refresh_result,
        selected_preview,
    )
    return FavoritesExternalRefreshNameAcceptancePlan(
        refresh_result=refresh_result,
        selected_preview=selected_preview,
        observation=observation,
        baseline_state=baseline_state,
        acceptance_plan=acceptance_plan,
    )


__all__ = [
    "FavoritesExternalRefreshNameAcceptancePlan",
    "plan_favorites_external_refresh_name_acceptance",
]
