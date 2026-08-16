"""Pure assisted-refresh selection to external Favorites detach planning."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .favorites_external import (
    FavoritesExternalFieldOwnership,
    FavoritesExternalRecordPreview,
    FavoritesExternalRecordState,
    detach_favorites_external_field,
    detach_favorites_external_record,
)
from .favorites_external_refresh import FavoritesExternalRefreshResult


class FavoritesExternalRefreshDetachScope(StrEnum):
    """Classify one explicit provenance-only detach decision."""

    FIELD = "field"
    RECORD = "record"


@dataclass(frozen=True, slots=True)
class FavoritesExternalRefreshDetachPlan:
    """Retain one exact assisted-refresh detach decision and intended provenance."""

    refresh_result: FavoritesExternalRefreshResult
    selected_preview: FavoritesExternalRecordPreview
    baseline_state: FavoritesExternalRecordState
    scope: FavoritesExternalRefreshDetachScope
    field_name: str | None
    intended_state: FavoritesExternalRecordState

    def __post_init__(self) -> None:
        if not isinstance(self.refresh_result, FavoritesExternalRefreshResult):
            raise TypeError(
                "Refresh detach requires FavoritesExternalRefreshResult."
            )
        if not isinstance(self.selected_preview, FavoritesExternalRecordPreview):
            raise TypeError(
                "Refresh detach requires FavoritesExternalRecordPreview."
            )
        if not isinstance(self.baseline_state, FavoritesExternalRecordState):
            raise TypeError(
                "Refresh detach requires FavoritesExternalRecordState."
            )
        if not isinstance(self.scope, FavoritesExternalRefreshDetachScope):
            raise TypeError(
                "Refresh detach scope must be FavoritesExternalRefreshDetachScope."
            )
        if self.field_name is not None and type(self.field_name) is not str:
            raise TypeError("Refresh detach field name must be a string or None.")
        if not isinstance(self.intended_state, FavoritesExternalRecordState):
            raise TypeError(
                "Refresh detach intended state must be FavoritesExternalRecordState."
            )

        baseline_state, intended_state = _derive_detach(
            self.refresh_result,
            self.selected_preview,
            self.scope,
            field_name=self.field_name,
        )
        if self.baseline_state != baseline_state:
            raise ValueError(
                "Refresh detach baseline state does not match the exact "
                "selected refresh evidence."
            )
        if self.intended_state != intended_state:
            raise ValueError(
                "Refresh detach intended state does not match the exact "
                "detach transformation."
            )


def _resolve_baseline(
    refresh_result: FavoritesExternalRefreshResult,
    selected_preview: FavoritesExternalRecordPreview,
) -> FavoritesExternalRecordState:
    if not isinstance(refresh_result, FavoritesExternalRefreshResult):
        raise TypeError("Refresh detach requires FavoritesExternalRefreshResult.")
    if not isinstance(selected_preview, FavoritesExternalRecordPreview):
        raise TypeError("Refresh detach requires FavoritesExternalRecordPreview.")
    if refresh_result.preview.records.count(selected_preview) != 1:
        raise ValueError(
            "Refresh detach requires the exact selected preview once."
        )
    if selected_preview.target is None or selected_preview.external_identity is None:
        raise ValueError("Refresh detach requires a linked selected preview.")

    provenance_records = refresh_result.lifecycle_snapshot.provenance_records
    if not provenance_records:
        raise ValueError(
            "Refresh detach requires an existing linked baseline record."
        )
    baselines = tuple(
        record
        for record in provenance_records
        if record.target == selected_preview.target
        and record.external_identity == selected_preview.external_identity
    )
    if len(baselines) != 1:
        raise ValueError(
            "Refresh detach requires one exact linked baseline record."
        )

    baseline_state = baselines[0]
    if baseline_state.detached:
        raise ValueError(
            "Refresh detach requires a linked record that is not already detached."
        )
    return baseline_state


def _derive_detach(
    refresh_result: FavoritesExternalRefreshResult,
    selected_preview: FavoritesExternalRecordPreview,
    scope: FavoritesExternalRefreshDetachScope,
    *,
    field_name: str | None,
) -> tuple[FavoritesExternalRecordState, FavoritesExternalRecordState]:
    if not isinstance(scope, FavoritesExternalRefreshDetachScope):
        raise TypeError(
            "Refresh detach scope must be FavoritesExternalRefreshDetachScope."
        )

    baseline_state = _resolve_baseline(refresh_result, selected_preview)

    if scope is FavoritesExternalRefreshDetachScope.FIELD:
        if field_name is None:
            raise ValueError("Field detach requires one explicit field name.")
        if type(field_name) is not str:
            raise TypeError("Refresh detach field name must be a string or None.")

        fields = tuple(
            field for field in baseline_state.fields if field.name == field_name
        )
        if len(fields) != 1:
            raise ValueError(
                "Field detach requires one exact bound provenance field."
            )
        if fields[0].ownership is not FavoritesExternalFieldOwnership.EXTERNAL:
            raise ValueError(
                "Field detach requires one externally owned provenance field."
            )
        intended_state = detach_favorites_external_field(
            baseline_state,
            field_name,
        )
    else:
        if field_name is not None:
            raise ValueError("Record detach must not specify a field name.")
        intended_state = detach_favorites_external_record(baseline_state)

    if intended_state.target != baseline_state.target:
        raise ValueError(
            "Refresh detach must preserve the exact local Favorites target."
        )
    if intended_state == baseline_state:
        raise ValueError("Refresh detach must produce a provenance ownership change.")
    return baseline_state, intended_state


def plan_favorites_external_refresh_detach(
    refresh_result: FavoritesExternalRefreshResult,
    selected_preview: FavoritesExternalRecordPreview,
    scope: FavoritesExternalRefreshDetachScope,
    *,
    field_name: str | None = None,
) -> FavoritesExternalRefreshDetachPlan:
    """Plan one explicit provenance-only detach from exact refresh evidence."""

    baseline_state, intended_state = _derive_detach(
        refresh_result,
        selected_preview,
        scope,
        field_name=field_name,
    )
    return FavoritesExternalRefreshDetachPlan(
        refresh_result=refresh_result,
        selected_preview=selected_preview,
        baseline_state=baseline_state,
        scope=scope,
        field_name=field_name,
        intended_state=intended_state,
    )


__all__ = [
    "FavoritesExternalRefreshDetachPlan",
    "FavoritesExternalRefreshDetachScope",
    "plan_favorites_external_refresh_detach",
]
