"""Pure source-neutral External Favorites mapped-field acceptance planning."""

from __future__ import annotations

from dataclasses import dataclass, replace

from .favorites_editing import (
    FavoritesRecordEditError,
    _replace_favorites_record_field,
    select_favorites_record_target,
)
from .favorites_external import (
    FavoritesExternalAcceptanceError,
    FavoritesExternalChangeKind,
    FavoritesExternalFieldObservationState,
    FavoritesExternalFieldOwnership,
    FavoritesExternalFieldPreview,
    FavoritesExternalFieldState,
    FavoritesExternalRecordPreview,
    FavoritesExternalRecordState,
    preview_favorites_external_import,
)
from .favorites_external_mapping import FavoritesExternalFieldMapping
from .favorites_storage import FavoritesStorageSnapshot
from .favorites_write_plan import FavoritesWritePlan, plan_favorites_write


@dataclass(frozen=True, slots=True)
class FavoritesExternalFieldAcceptancePlan:
    """Retain one exact mapped-field acceptance and its write/provenance evidence."""

    mapping: FavoritesExternalFieldMapping
    preview: FavoritesExternalRecordPreview
    write_plan: FavoritesWritePlan
    baseline_state: FavoritesExternalRecordState
    intended_state: FavoritesExternalRecordState

    def __post_init__(self) -> None:
        if not isinstance(self.mapping, FavoritesExternalFieldMapping):
            raise TypeError(
                "External Favorites field acceptance mapping must be "
                "FavoritesExternalFieldMapping."
            )
        if not isinstance(self.preview, FavoritesExternalRecordPreview):
            raise TypeError(
                "External Favorites field acceptance preview must be "
                "FavoritesExternalRecordPreview."
            )
        if not isinstance(self.write_plan, FavoritesWritePlan):
            raise TypeError(
                "External Favorites field acceptance write plan must be "
                "FavoritesWritePlan."
            )
        if not isinstance(self.baseline_state, FavoritesExternalRecordState):
            raise TypeError(
                "External Favorites field acceptance baseline state must be "
                "FavoritesExternalRecordState."
            )
        if not isinstance(self.intended_state, FavoritesExternalRecordState):
            raise TypeError(
                "External Favorites field acceptance intended state must be "
                "FavoritesExternalRecordState."
            )

        expected_preview, intended_snapshot, expected_state = (
            _derive_field_acceptance_evidence(
                self.write_plan.baseline_snapshot,
                self.baseline_state,
                self.mapping,
            )
        )
        if self.preview != expected_preview:
            raise ValueError(
                "External Favorites field acceptance preview does not match "
                "the exact baseline and mapping evidence."
            )
        if self.write_plan != plan_favorites_write(
            self.write_plan.baseline_snapshot,
            intended_snapshot,
        ):
            raise ValueError(
                "External Favorites field acceptance write plan does not match "
                "the exact mapped-field transformation."
            )
        if self.intended_state != expected_state:
            raise ValueError(
                "External Favorites field acceptance intended provenance does "
                "not match the exact mapped-field transformation."
            )


def _require_exact_baseline_target(
    snapshot: FavoritesStorageSnapshot,
    record: FavoritesExternalRecordState,
    mapping: FavoritesExternalFieldMapping,
) -> None:
    if record.target != mapping.target:
        raise FavoritesExternalAcceptanceError(
            "External Favorites field acceptance mapping target does not match "
            "the linked baseline record."
        )

    try:
        current_target = select_favorites_record_target(
            snapshot,
            record.target.source_index,
            document_index=record.target.document_index,
        )
    except FavoritesRecordEditError:
        raise FavoritesExternalAcceptanceError(
            "External Favorites field acceptance could not revalidate the exact "
            "baseline target."
        ) from None

    if current_target != record.target:
        raise FavoritesExternalAcceptanceError(
            "External Favorites field acceptance baseline target is stale."
        )


def _selected_field_state(
    record: FavoritesExternalRecordState,
    mapping: FavoritesExternalFieldMapping,
) -> FavoritesExternalFieldState | None:
    selected = next(
        (
            field
            for field in record.fields
            if field.name == mapping.field.name
        ),
        None,
    )
    collision = next(
        (
            field
            for field in record.fields
            if field.field_index == mapping.field_index
            and field.name != mapping.field.name
        ),
        None,
    )
    if collision is not None:
        raise FavoritesExternalAcceptanceError(
            "External Favorites field acceptance mapped source index is already "
            "owned by another provenance field."
        )

    if selected is None:
        return None

    if selected.ownership is not FavoritesExternalFieldOwnership.EXTERNAL:
        raise FavoritesExternalAcceptanceError(
            "External Favorites field acceptance requires an externally owned "
            "field or one previously unbound mapped field."
        )
    if selected.field_index != mapping.field_index:
        raise FavoritesExternalAcceptanceError(
            "External Favorites field acceptance mapping index does not match "
            "the existing field provenance."
        )
    return selected


def _require_selected_preview(
    preview: FavoritesExternalRecordPreview,
    record: FavoritesExternalRecordState,
    mapping: FavoritesExternalFieldMapping,
    selected_state: FavoritesExternalFieldState | None,
) -> FavoritesExternalFieldPreview:
    if preview.kind is FavoritesExternalChangeKind.CONFLICT:
        raise FavoritesExternalAcceptanceError(
            "External Favorites field acceptance does not accept a record with "
            "unresolved conflicts."
        )

    selected_preview = next(
        (
            field
            for field in preview.fields
            if field.name == mapping.field.name
        ),
        None,
    )
    if (
        selected_preview is None
        or selected_preview.ownership
        is not FavoritesExternalFieldOwnership.EXTERNAL
        or selected_preview.external_state
        is not FavoritesExternalFieldObservationState.VALUE
        or selected_preview.external_value != mapping.field.value
    ):
        raise FavoritesExternalAcceptanceError(
            "External Favorites field acceptance preview does not retain the "
            "exact mapped provider value."
        )

    expected_kinds = (
        {FavoritesExternalChangeKind.ADDED}
        if selected_state is None
        else {
            FavoritesExternalChangeKind.REPLACED,
            FavoritesExternalChangeKind.UNCHANGED,
        }
    )
    if selected_preview.kind not in expected_kinds:
        raise FavoritesExternalAcceptanceError(
            "External Favorites field acceptance selected preview has an "
            "unsupported change kind."
        )

    bound_names = {field.name for field in record.fields}
    other_bound_changes = tuple(
        field
        for field in preview.fields
        if (
            field.name in bound_names
            and field.name != mapping.field.name
            and field.kind
            in {
                FavoritesExternalChangeKind.REPLACED,
                FavoritesExternalChangeKind.REMOVED,
            }
        )
    )
    if other_bound_changes:
        raise FavoritesExternalAcceptanceError(
            "External Favorites field acceptance does not accept simultaneous "
            "changes to another bound field."
        )

    return selected_preview


def _derive_field_acceptance_evidence(
    snapshot: FavoritesStorageSnapshot,
    record: FavoritesExternalRecordState,
    mapping: FavoritesExternalFieldMapping,
) -> tuple[
    FavoritesExternalRecordPreview,
    FavoritesStorageSnapshot,
    FavoritesExternalRecordState,
]:
    if not isinstance(snapshot, FavoritesStorageSnapshot):
        raise TypeError(
            "External Favorites field acceptance requires FavoritesStorageSnapshot."
        )
    if not isinstance(record, FavoritesExternalRecordState):
        raise TypeError(
            "External Favorites field acceptance requires "
            "FavoritesExternalRecordState."
        )
    if not isinstance(mapping, FavoritesExternalFieldMapping):
        raise TypeError(
            "External Favorites field acceptance requires "
            "FavoritesExternalFieldMapping."
        )

    if record.external_identity is None or record.detached:
        raise FavoritesExternalAcceptanceError(
            "External Favorites field acceptance requires one linked "
            "non-detached record."
        )
    if mapping.observation.identity != record.external_identity:
        raise FavoritesExternalAcceptanceError(
            "External Favorites field acceptance observation identity does not "
            "match the linked record."
        )
    if mapping.scanner_value != mapping.field.value:
        raise FavoritesExternalAcceptanceError(
            "External Favorites field acceptance currently requires the mapped "
            "scanner value to equal the normalized observed value."
        )

    _require_exact_baseline_target(snapshot, record, mapping)
    selected_state = _selected_field_state(record, mapping)

    preview = preview_favorites_external_import(
        (record,),
        (mapping.observation,),
    ).records[0]
    _require_selected_preview(
        preview,
        record,
        mapping,
        selected_state,
    )

    try:
        intended_snapshot = _replace_favorites_record_field(
            snapshot,
            mapping.target,
            mapping.field_index,
            mapping.scanner_value,
        )
    except FavoritesRecordEditError as error:
        raise FavoritesExternalAcceptanceError(
            "External Favorites field acceptance could not produce the exact "
            "mapped-field snapshot."
        ) from error

    intended_target = select_favorites_record_target(
        intended_snapshot,
        record.target.source_index,
        document_index=record.target.document_index,
    )
    if len(intended_target.record.fields) != len(record.target.record.fields):
        raise FavoritesExternalAcceptanceError(
            "External Favorites field acceptance changed the source field shape."
        )

    changed_indexes = tuple(
        index
        for index, (before, after) in enumerate(
            zip(
                record.target.record.fields,
                intended_target.record.fields,
                strict=True,
            )
        )
        if before != after
    )
    expected_changed_indexes = (
        ()
        if record.target.record.fields[mapping.field_index]
        == mapping.scanner_value
        else (mapping.field_index,)
    )
    if changed_indexes != expected_changed_indexes:
        raise FavoritesExternalAcceptanceError(
            "External Favorites field acceptance changed source fields outside "
            "the exact mapped index."
        )

    if selected_state is None:
        intended_fields = (
            *record.fields,
            FavoritesExternalFieldState(
                name=mapping.field.name,
                field_index=mapping.field_index,
                ownership=FavoritesExternalFieldOwnership.EXTERNAL,
                last_external=mapping.field,
            ),
        )
    else:
        intended_fields = tuple(
            (
                replace(
                    field,
                    last_external=mapping.field,
                )
                if field is selected_state
                else field
            )
            for field in record.fields
        )

    intended_state = replace(
        record,
        target=intended_target,
        fields=intended_fields,
        last_observation=mapping.observation.evidence,
    )
    return preview, intended_snapshot, intended_state


def plan_favorites_external_field_acceptance(
    snapshot: FavoritesStorageSnapshot,
    record: FavoritesExternalRecordState,
    mapping: FavoritesExternalFieldMapping,
) -> FavoritesExternalFieldAcceptancePlan:
    """Plan one exact mapped-field acceptance without executing or persisting it."""

    preview, intended_snapshot, intended_state = _derive_field_acceptance_evidence(
        snapshot,
        record,
        mapping,
    )
    return FavoritesExternalFieldAcceptancePlan(
        mapping=mapping,
        preview=preview,
        write_plan=plan_favorites_write(
            snapshot,
            intended_snapshot,
        ),
        baseline_state=record,
        intended_state=intended_state,
    )


__all__ = [
    "FavoritesExternalFieldAcceptancePlan",
    "plan_favorites_external_field_acceptance",
]
