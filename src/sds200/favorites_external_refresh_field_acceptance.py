"""Pure assisted-refresh selection to mapped-field acceptance planning."""

from __future__ import annotations

from dataclasses import dataclass

from .favorites_external import (
    FavoritesExternalRecordPreview,
    FavoritesExternalRecordState,
)
from .favorites_external_field_acceptance import (
    FavoritesExternalFieldAcceptancePlan,
    plan_favorites_external_field_acceptance,
)
from .favorites_external_mapping import FavoritesExternalFieldMapping
from .favorites_external_provenance_lifecycle import (
    FavoritesExternalProvenanceLifecycleState,
)
from .favorites_external_refresh import FavoritesExternalRefreshResult


@dataclass(frozen=True, slots=True)
class FavoritesExternalRefreshFieldAcceptancePlan:
    """Retain one exact refresh selection and delegated mapped-field plan."""

    refresh_result: FavoritesExternalRefreshResult
    selected_preview: FavoritesExternalRecordPreview
    mapping: FavoritesExternalFieldMapping
    baseline_state: FavoritesExternalRecordState
    acceptance_plan: FavoritesExternalFieldAcceptancePlan

    def __post_init__(self) -> None:
        if type(self.refresh_result) is not FavoritesExternalRefreshResult:
            raise TypeError(
                "Refresh field acceptance requires an exact "
                "FavoritesExternalRefreshResult."
            )
        if type(self.selected_preview) is not FavoritesExternalRecordPreview:
            raise TypeError(
                "Refresh field acceptance requires an exact "
                "FavoritesExternalRecordPreview."
            )
        if type(self.mapping) is not FavoritesExternalFieldMapping:
            raise TypeError(
                "Refresh field acceptance requires an exact "
                "FavoritesExternalFieldMapping."
            )
        if type(self.baseline_state) is not FavoritesExternalRecordState:
            raise TypeError(
                "Refresh field acceptance requires an exact "
                "FavoritesExternalRecordState."
            )
        if type(self.acceptance_plan) is not FavoritesExternalFieldAcceptancePlan:
            raise TypeError(
                "Refresh field acceptance requires an exact "
                "FavoritesExternalFieldAcceptancePlan."
            )

        baseline_state, acceptance_plan = _derive_acceptance(
            self.refresh_result,
            self.selected_preview,
            self.mapping,
        )
        if self.baseline_state is not baseline_state:
            raise ValueError(
                "Refresh field acceptance baseline state must be the exact "
                "selected persisted record."
            )
        if self.acceptance_plan != acceptance_plan:
            raise ValueError(
                "Refresh field acceptance delegated plan does not match the "
                "exact selected refresh evidence."
            )


def _derive_acceptance(
    refresh_result: FavoritesExternalRefreshResult,
    selected_preview: FavoritesExternalRecordPreview,
    mapping: FavoritesExternalFieldMapping,
) -> tuple[FavoritesExternalRecordState, FavoritesExternalFieldAcceptancePlan]:
    if type(refresh_result) is not FavoritesExternalRefreshResult:
        raise TypeError(
            "Refresh field acceptance requires an exact FavoritesExternalRefreshResult."
        )
    if type(selected_preview) is not FavoritesExternalRecordPreview:
        raise TypeError(
            "Refresh field acceptance requires an exact FavoritesExternalRecordPreview."
        )
    if type(mapping) is not FavoritesExternalFieldMapping:
        raise TypeError(
            "Refresh field acceptance requires an exact FavoritesExternalFieldMapping."
        )
    retained_previews = tuple(
        preview
        for preview in refresh_result.preview.records
        if preview is selected_preview
    )
    if len(retained_previews) != 1:
        raise ValueError(
            "Refresh field acceptance requires the exact retained selected preview once."
        )
    if selected_preview.target is None or selected_preview.external_identity is None:
        raise ValueError("Refresh field acceptance requires a linked selected preview.")

    lifecycle_snapshot = refresh_result.lifecycle_snapshot
    if (
        lifecycle_snapshot.state
        is not FavoritesExternalProvenanceLifecycleState.ACTIVE
        or lifecycle_snapshot.favorites_snapshot is None
    ):
        raise ValueError(
            "Refresh field acceptance requires an active lifecycle Favorites snapshot."
        )
    provenance_records = lifecycle_snapshot.provenance_records
    if provenance_records is None:
        raise ValueError(
            "Refresh field acceptance requires persisted baseline provenance."
        )
    baselines = tuple(
        record
        for record in provenance_records
        if record.target == selected_preview.target
        and record.external_identity == selected_preview.external_identity
    )
    if len(baselines) != 1:
        raise ValueError(
            "Refresh field acceptance requires one exact linked baseline record."
        )
    baseline_state = baselines[0]
    if mapping.target != selected_preview.target or mapping.target != baseline_state.target:
        raise ValueError(
            "Refresh field acceptance mapping target does not match the selection."
        )

    retained_observations = tuple(
        observation
        for observation in refresh_result.observations
        if observation is mapping.observation
    )
    if len(retained_observations) != 1:
        raise ValueError(
            "Refresh field acceptance requires the exact retained mapping observation once."
        )
    if (
        mapping.observation.identity != selected_preview.external_identity
        or mapping.observation.identity != baseline_state.external_identity
        or mapping.observation.evidence != selected_preview.evidence
    ):
        raise ValueError(
            "Refresh field acceptance mapping observation does not match the "
            "selected external evidence."
        )
    retained_fields = tuple(
        field for field in mapping.observation.fields if field is mapping.field
    )
    if len(retained_fields) != 1:
        raise ValueError(
            "Refresh field acceptance requires the exact retained mapped field once."
        )

    acceptance_plan = plan_favorites_external_field_acceptance(
        lifecycle_snapshot.favorites_snapshot,
        baseline_state,
        mapping,
    )
    if acceptance_plan.preview != selected_preview:
        raise ValueError(
            "Refresh field acceptance delegated preview must equal the exact selection."
        )
    return baseline_state, acceptance_plan


def plan_favorites_external_refresh_field_acceptance(
    refresh_result: FavoritesExternalRefreshResult,
    selected_preview: FavoritesExternalRecordPreview,
    mapping: FavoritesExternalFieldMapping,
) -> FavoritesExternalRefreshFieldAcceptancePlan:
    """Compose one retained refresh mapping into an existing field plan."""

    baseline_state, acceptance_plan = _derive_acceptance(
        refresh_result,
        selected_preview,
        mapping,
    )
    return FavoritesExternalRefreshFieldAcceptancePlan(
        refresh_result=refresh_result,
        selected_preview=selected_preview,
        mapping=mapping,
        baseline_state=baseline_state,
        acceptance_plan=acceptance_plan,
    )


__all__ = [
    "FavoritesExternalRefreshFieldAcceptancePlan",
    "plan_favorites_external_refresh_field_acceptance",
]
