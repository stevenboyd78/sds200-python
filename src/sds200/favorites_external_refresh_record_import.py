"""Pure assisted-refresh planning for one explicit record import."""

from __future__ import annotations

from dataclasses import dataclass, replace

from .favorites_editing import (
    FavoritesRecordSourceKind,
    FavoritesRecordTarget,
    create_favorites_record_after,
    select_favorites_record_target,
)
from .favorites_external import (
    FavoritesExternalChangeKind,
    FavoritesExternalFieldBinding,
    FavoritesExternalFieldOwnership,
    FavoritesExternalRecordObservation,
    FavoritesExternalRecordObservationState,
    FavoritesExternalRecordPreview,
    FavoritesExternalRecordState,
    bind_favorites_external_record,
)
from .favorites_external_provenance import (
    deserialize_favorites_external_provenance,
    serialize_favorites_external_provenance,
)
from .favorites_external_refresh import FavoritesExternalRefreshResult
from .favorites_file import FavoritesSourceRecord
from .favorites_storage import FavoritesStorageSnapshot
from .favorites_write_plan import FavoritesWritePlan, plan_favorites_write


def _reselect_shifted_state(
    state: FavoritesExternalRecordState,
    intended_snapshot: FavoritesStorageSnapshot,
    *,
    document_index: int,
    pivot: int,
    delta: int,
) -> FavoritesExternalRecordState:
    old = state.target
    new_index = old.source_index
    if (
        old.source_kind is FavoritesRecordSourceKind.HPD
        and old.document_index == document_index
        and old.source_index > pivot
    ):
        new_index += delta
    target = select_favorites_record_target(
        intended_snapshot,
        new_index,
        document_index=old.document_index,
    )
    if (
        target.source_kind is not old.source_kind
        or target.document_index != old.document_index
        or target.filename != old.filename
        or target.record.raw_bytes != old.record.raw_bytes
    ):
        raise ValueError(
            "Structural Favorites mutation could not exactly rebind unchanged provenance."
        )
    return replace(state, target=target)


def _prove_provenance(
    records: tuple[FavoritesExternalRecordState, ...],
    snapshot: FavoritesStorageSnapshot,
) -> None:
    content = serialize_favorites_external_provenance(records)
    rebound = deserialize_favorites_external_provenance(
        content,
        snapshot,
    )
    if rebound != records:
        raise ValueError(
            "Structural Favorites intended provenance did not exactly rebind."
        )


def _derive_import(
    refresh_result: FavoritesExternalRefreshResult,
    selected_preview: FavoritesExternalRecordPreview,
    anchor: FavoritesRecordTarget,
    template: FavoritesSourceRecord,
    bindings: tuple[FavoritesExternalFieldBinding, ...],
) -> tuple[
    FavoritesExternalRecordObservation,
    FavoritesWritePlan,
    tuple[FavoritesExternalRecordState, ...] | None,
    FavoritesExternalRecordState,
    tuple[FavoritesExternalRecordState, ...],
]:
    if type(refresh_result) is not FavoritesExternalRefreshResult:
        raise TypeError("Record import requires an exact FavoritesExternalRefreshResult.")
    if type(selected_preview) is not FavoritesExternalRecordPreview:
        raise TypeError("Record import requires an exact FavoritesExternalRecordPreview.")
    if sum(preview is selected_preview for preview in refresh_result.preview.records) != 1:
        raise ValueError("Record import requires the exact retained selected preview once.")
    if (
        selected_preview.kind is not FavoritesExternalChangeKind.ADDED
        or selected_preview.target is not None
        or selected_preview.external_identity is None
        or selected_preview.evidence is None
    ):
        raise ValueError("Record import requires one unbound ADDED provider preview.")
    matches = tuple(
        observation
        for observation in refresh_result.observations
        if observation.identity == selected_preview.external_identity
        and observation.evidence == selected_preview.evidence
        and observation.state is FavoritesExternalRecordObservationState.ACTIVE
    )
    if len(matches) != 1 or sum(
        observation is matches[0]
        for observation in refresh_result.observations
    ) != 1:
        raise ValueError("Record import requires one exact retained active observation.")
    observation = matches[0]
    if type(bindings) is not tuple:
        raise TypeError("Record import bindings must be an immutable tuple.")
    if not any(
        binding.ownership is FavoritesExternalFieldOwnership.EXTERNAL
        for binding in bindings
        if isinstance(binding, FavoritesExternalFieldBinding)
    ):
        raise ValueError("Record import requires at least one externally owned binding.")
    snapshot = refresh_result.lifecycle_snapshot.favorites_snapshot
    if snapshot is None:
        raise ValueError("Record import requires an active Favorites snapshot.")
    # The editor performs the exact anchor and template validation.
    intended_snapshot = create_favorites_record_after(snapshot, anchor, template)
    created_target = select_favorites_record_target(
        intended_snapshot,
        anchor.source_index + 1,
        document_index=anchor.document_index,
    )
    if (
        created_target.source_kind is not FavoritesRecordSourceKind.HPD
        or created_target.document_index != anchor.document_index
        or created_target.filename != anchor.filename
        or created_target.record != template
    ):
        raise ValueError("Record import did not create the exact supplied HPD template.")
    intended_state = bind_favorites_external_record(created_target, observation, bindings)
    write_plan = plan_favorites_write(snapshot, intended_snapshot)
    if write_plan.is_blocked or not write_plan.has_changes:
        raise ValueError("Record import requires a real unblocked byte-changing write plan.")
    baseline = refresh_result.lifecycle_snapshot.provenance_records
    if anchor.document_index is None:
        raise ValueError("Record import requires an HPD anchor document index.")
    preserved = tuple(
        _reselect_shifted_state(
            state,
            intended_snapshot,
            document_index=anchor.document_index,
            pivot=anchor.source_index,
            delta=1,
        )
        for state in (baseline or ())
    )
    intended_records = preserved + (intended_state,)
    _prove_provenance(intended_records, intended_snapshot)
    return observation, write_plan, baseline, intended_state, intended_records


@dataclass(frozen=True, slots=True)
class FavoritesExternalRefreshRecordImportPlan:
    """Retain all exact evidence for one structural assisted import."""

    refresh_result: FavoritesExternalRefreshResult
    selected_preview: FavoritesExternalRecordPreview
    observation: FavoritesExternalRecordObservation
    anchor: FavoritesRecordTarget
    template: FavoritesSourceRecord
    bindings: tuple[FavoritesExternalFieldBinding, ...]
    write_plan: FavoritesWritePlan
    baseline_provenance_records: tuple[FavoritesExternalRecordState, ...] | None
    intended_state: FavoritesExternalRecordState
    intended_provenance_records: tuple[FavoritesExternalRecordState, ...]

    def __post_init__(self) -> None:
        derived = _derive_import(
            self.refresh_result, self.selected_preview, self.anchor, self.template, self.bindings
        )
        if self.observation is not derived[0] or (
            self.write_plan != derived[1]
            or self.baseline_provenance_records != derived[2]
            or self.intended_state != derived[3]
            or self.intended_provenance_records != derived[4]
        ):
            raise ValueError("Record import plan does not match its exact refresh evidence.")


def plan_favorites_external_refresh_record_import(
    refresh_result: FavoritesExternalRefreshResult,
    selected_preview: FavoritesExternalRecordPreview,
    anchor: FavoritesRecordTarget,
    template: FavoritesSourceRecord,
    bindings: tuple[FavoritesExternalFieldBinding, ...],
) -> FavoritesExternalRefreshRecordImportPlan:
    """Plan one explicit exact-template record import."""

    observation, write_plan, baseline, intended_state, intended_records = _derive_import(
        refresh_result, selected_preview, anchor, template, bindings
    )
    return FavoritesExternalRefreshRecordImportPlan(
        refresh_result, selected_preview, observation, anchor, template, bindings,
        write_plan, baseline, intended_state, intended_records,
    )


__all__ = [
    "FavoritesExternalRefreshRecordImportPlan",
    "plan_favorites_external_refresh_record_import",
]
