from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

import sds200
from sds200 import (
    FavoritesExternalChangeKind,
    FavoritesExternalFieldBinding,
    FavoritesExternalFieldObservation,
    FavoritesExternalFieldObservationState,
    FavoritesExternalFieldOwnership,
    FavoritesExternalObservationEvidence,
    FavoritesExternalProvenanceLifecycle,
    FavoritesExternalProvenanceLifecycleSnapshot,
    FavoritesExternalProvenanceLifecycleState,
    FavoritesExternalRecordIdentity,
    FavoritesExternalRecordObservation,
    FavoritesExternalRecordState,
    FavoritesExternalRefreshDetachPlan,
    FavoritesExternalRefreshDetachScope,
    FavoritesExternalRefreshResult,
    FavoritesExternalRefreshSession,
    FavoritesExternalSourceIdentity,
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
    bind_favorites_external_record,
    detach_favorites_external_field,
    detach_favorites_external_record,
    plan_favorites_external_refresh_detach,
    preview_favorites_external_import,
    save_favorites_external_provenance,
    select_favorites_record_target,
)

_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "favorites"


def _snapshot() -> FavoritesStorageSnapshot:
    return FavoritesStorageSnapshot(
        catalog_bytes=(_FIXTURE_ROOT / "synthetic-f_list.cfg").read_bytes(),
        documents=(
            FavoritesStorageDocument(
                filename="f_000001.hpd",
                content=(_FIXTURE_ROOT / "synthetic-favorites.hpd").read_bytes(),
            ),
        ),
    )


def _identity(record_id: str = "channel-101") -> FavoritesExternalRecordIdentity:
    return FavoritesExternalRecordIdentity(
        source=FavoritesExternalSourceIdentity(
            provider="synthetic-provider",
            dataset="metro",
        ),
        record_id=record_id,
    )


def _observation(
    name: str = "Synthetic Channel",
    *,
    revision: str = "revision-1",
    identity: FavoritesExternalRecordIdentity | None = None,
) -> FavoritesExternalRecordObservation:
    return FavoritesExternalRecordObservation(
        identity=identity or _identity(),
        evidence=FavoritesExternalObservationEvidence(
            observed_at=datetime(2026, 8, 15, 3, 0, tzinfo=UTC),
            revision=revision,
        ),
        fields=(
            FavoritesExternalFieldObservation(
                name="name",
                state=FavoritesExternalFieldObservationState.VALUE,
                value=name,
            ),
            FavoritesExternalFieldObservation(
                name="frequency",
                state=FavoritesExternalFieldObservationState.VALUE,
                value="155100000",
            ),
        ),
    )


def _state(
    *,
    ownership: FavoritesExternalFieldOwnership = (
        FavoritesExternalFieldOwnership.EXTERNAL
    ),
    include_local_frequency: bool = False,
) -> tuple[FavoritesStorageSnapshot, FavoritesExternalRecordState]:
    snapshot = _snapshot()
    target = select_favorites_record_target(snapshot, 5, document_index=0)
    accepted = _observation(target.record.fields[2])
    bindings: tuple[FavoritesExternalFieldBinding, ...] = (
        FavoritesExternalFieldBinding(
            name="name",
            field_index=2,
            ownership=ownership,
        ),
    )
    if include_local_frequency:
        bindings += (
            FavoritesExternalFieldBinding(
                name="frequency",
                field_index=4,
                ownership=FavoritesExternalFieldOwnership.LOCAL,
            ),
        )
    state = bind_favorites_external_record(target, accepted, bindings)
    return snapshot, state


def _result(
    records: tuple[FavoritesExternalRecordState, ...] | None,
    observations: tuple[FavoritesExternalRecordObservation, ...],
) -> FavoritesExternalRefreshResult:
    snapshot = _snapshot()
    lifecycle = FavoritesExternalProvenanceLifecycleSnapshot(
        state=FavoritesExternalProvenanceLifecycleState.ACTIVE,
        provenance_path=Path("/tmp/synthetic-provenance.json"),
        favorites_snapshot=snapshot,
        provenance_records=records,
        last_error=None,
    )
    return FavoritesExternalRefreshResult(
        lifecycle_snapshot=lifecycle,
        observations=observations,
        preview=preview_favorites_external_import(records or (), observations),
    )


def _replacement_result() -> tuple[
    FavoritesExternalRefreshResult,
    FavoritesExternalRecordState,
]:
    _, state = _state()
    return (
        _result(
            (state,),
            (_observation("Dispatch Updated", revision="revision-2"),),
        ),
        state,
    )


class _StorageSource:
    def __init__(self, snapshot: FavoritesStorageSnapshot) -> None:
        self.snapshot = snapshot

    def read_snapshot(self) -> FavoritesStorageSnapshot:
        return self.snapshot


class _CountingLifecycle(FavoritesExternalProvenanceLifecycle):
    def __init__(self, snapshot: FavoritesStorageSnapshot, path: Path) -> None:
        super().__init__(_StorageSource(snapshot), path)
        self.snapshot_calls = 0

    def snapshot(self) -> FavoritesExternalProvenanceLifecycleSnapshot:
        self.snapshot_calls += 1
        return super().snapshot()


class _CountingSource:
    def __init__(
        self,
        observations: tuple[FavoritesExternalRecordObservation, ...],
    ) -> None:
        self.observations = observations
        self.read_calls = 0

    def read_observations(self) -> tuple[FavoritesExternalRecordObservation, ...]:
        self.read_calls += 1
        return self.observations


def test_field_detach_plan_preserves_exact_refresh_and_local_target() -> None:
    result, state = _replacement_result()
    selected = result.preview.records[0]

    plan = plan_favorites_external_refresh_detach(
        result,
        selected,
        FavoritesExternalRefreshDetachScope.FIELD,
        field_name="name",
    )

    assert plan.refresh_result is result
    assert plan.selected_preview is selected
    assert plan.baseline_state is state
    assert plan.scope is FavoritesExternalRefreshDetachScope.FIELD
    assert plan.field_name == "name"
    assert plan.intended_state.target is state.target
    assert plan.intended_state.target.record.raw_bytes == state.target.record.raw_bytes
    assert plan.intended_state.external_identity is state.external_identity
    assert plan.intended_state.last_observation is state.last_observation
    assert plan.intended_state.detached is False
    assert plan.intended_state.fields[0].ownership is (
        FavoritesExternalFieldOwnership.DETACHED
    )
    assert plan.intended_state.fields[0].last_external is state.fields[0].last_external


def test_record_detach_plan_preserves_local_fields_and_external_evidence() -> None:
    _, state = _state(include_local_frequency=True)
    result = _result(
        (state,),
        (_observation("Dispatch Updated", revision="revision-2"),),
    )
    selected = result.preview.records[0]

    plan = plan_favorites_external_refresh_detach(
        result,
        selected,
        FavoritesExternalRefreshDetachScope.RECORD,
    )

    assert plan.baseline_state is state
    assert plan.intended_state.target is state.target
    assert plan.intended_state.target.record.raw_bytes == state.target.record.raw_bytes
    assert plan.intended_state.external_identity is state.external_identity
    assert plan.intended_state.last_observation is state.last_observation
    assert plan.intended_state.detached is True
    fields = {field.name: field for field in plan.intended_state.fields}
    assert fields["name"].ownership is FavoritesExternalFieldOwnership.DETACHED
    assert fields["name"].last_external is state.fields[0].last_external
    assert fields["frequency"].ownership is FavoritesExternalFieldOwnership.LOCAL
    assert fields["frequency"].last_external is None


def test_detach_planning_performs_no_second_source_read_or_lifecycle_snapshot(
    tmp_path: Path,
) -> None:
    snapshot, state = _state()
    path = tmp_path / "provenance.json"
    save_favorites_external_provenance((state,), path)
    lifecycle = _CountingLifecycle(snapshot, path)
    lifecycle.start()
    source = _CountingSource(
        (_observation("Dispatch Updated", revision="revision-2"),)
    )
    result = FavoritesExternalRefreshSession(lifecycle, source).refresh()

    plan_favorites_external_refresh_detach(
        result,
        result.preview.records[0],
        FavoritesExternalRefreshDetachScope.FIELD,
        field_name="name",
    )

    assert lifecycle.snapshot_calls == 1
    assert source.read_calls == 1


def test_field_detach_is_allowed_from_unchanged_linked_selection() -> None:
    _, state = _state()
    observation = _observation(
        state.target.record.fields[2],
        revision="revision-2",
    )
    observation = replace(observation, fields=observation.fields[:1])
    result = _result((state,), (observation,))
    selected = result.preview.records[0]
    assert selected.kind is FavoritesExternalChangeKind.UNCHANGED

    plan = plan_favorites_external_refresh_detach(
        result,
        selected,
        FavoritesExternalRefreshDetachScope.FIELD,
        field_name="name",
    )

    assert plan.intended_state.fields[0].ownership is (
        FavoritesExternalFieldOwnership.DETACHED
    )


def test_record_detach_is_allowed_from_unmatched_linked_selection() -> None:
    _, state = _state()
    result = _result((state,), ())
    selected = result.preview.records[0]
    assert selected.kind is FavoritesExternalChangeKind.LOCAL_ONLY

    plan = plan_favorites_external_refresh_detach(
        result,
        selected,
        FavoritesExternalRefreshDetachScope.RECORD,
    )

    assert plan.intended_state.detached is True


@pytest.mark.parametrize("records", [None, ()])
def test_missing_or_empty_provenance_rejected(
    records: tuple[FavoritesExternalRecordState, ...] | None,
) -> None:
    result = _result(records, (_observation("New Record"),))
    with pytest.raises(ValueError, match="linked selected preview"):
        plan_favorites_external_refresh_detach(
            result,
            result.preview.records[0],
            FavoritesExternalRefreshDetachScope.RECORD,
        )


def test_preview_not_in_result_rejected() -> None:
    result, _ = _replacement_result()
    selected = result.preview.records[0]
    assert selected.evidence is not None
    foreign = replace(
        selected,
        evidence=replace(selected.evidence, revision="other"),
    )

    with pytest.raises(ValueError, match="exact selected preview once"):
        plan_favorites_external_refresh_detach(
            result,
            foreign,
            FavoritesExternalRefreshDetachScope.RECORD,
        )


def test_field_detach_rejects_local_and_already_detached_fields() -> None:
    _, local = _state(ownership=FavoritesExternalFieldOwnership.LOCAL)
    local_result = _result(
        (local,),
        (_observation("Provider Changed", revision="revision-2"),),
    )
    with pytest.raises(ValueError, match="externally owned provenance field"):
        plan_favorites_external_refresh_detach(
            local_result,
            local_result.preview.records[0],
            FavoritesExternalRefreshDetachScope.FIELD,
            field_name="name",
        )

    _, external = _state()
    detached = detach_favorites_external_field(external, "name")
    detached_result = _result(
        (detached,),
        (_observation("Provider Changed", revision="revision-2"),),
    )
    with pytest.raises(ValueError, match="externally owned provenance field"):
        plan_favorites_external_refresh_detach(
            detached_result,
            detached_result.preview.records[0],
            FavoritesExternalRefreshDetachScope.FIELD,
            field_name="name",
        )


def test_record_detach_rejects_already_detached_record() -> None:
    _, state = _state()
    detached = detach_favorites_external_record(state)
    result = _result(
        (detached,),
        (_observation("Provider Changed", revision="revision-2"),),
    )

    with pytest.raises(ValueError, match="not already detached"):
        plan_favorites_external_refresh_detach(
            result,
            result.preview.records[0],
            FavoritesExternalRefreshDetachScope.RECORD,
        )


@pytest.mark.parametrize(
    ("scope", "field_name", "match"),
    [
        (
            FavoritesExternalRefreshDetachScope.FIELD,
            None,
            "explicit field name",
        ),
        (
            FavoritesExternalRefreshDetachScope.FIELD,
            "missing",
            "exact bound provenance field",
        ),
        (
            FavoritesExternalRefreshDetachScope.RECORD,
            "name",
            "must not specify a field name",
        ),
    ],
)
def test_scope_and_field_name_relationships_rejected(
    scope: FavoritesExternalRefreshDetachScope,
    field_name: str | None,
    match: str,
) -> None:
    result, _ = _replacement_result()
    with pytest.raises(ValueError, match=match):
        plan_favorites_external_refresh_detach(
            result,
            result.preview.records[0],
            scope,
            field_name=field_name,
        )


def test_planner_requires_public_types() -> None:
    result, _ = _replacement_result()
    with pytest.raises(TypeError):
        plan_favorites_external_refresh_detach(  # type: ignore[arg-type]
            object(),
            result.preview.records[0],
            FavoritesExternalRefreshDetachScope.RECORD,
        )
    with pytest.raises(TypeError):
        plan_favorites_external_refresh_detach(  # type: ignore[arg-type]
            result,
            object(),
            FavoritesExternalRefreshDetachScope.RECORD,
        )
    with pytest.raises(TypeError):
        plan_favorites_external_refresh_detach(  # type: ignore[arg-type]
            result,
            result.preview.records[0],
            "record",
        )


def test_detach_plan_is_frozen_and_slotted() -> None:
    result, _ = _replacement_result()
    plan = plan_favorites_external_refresh_detach(
        result,
        result.preview.records[0],
        FavoritesExternalRefreshDetachScope.RECORD,
    )
    assert not hasattr(plan, "__dict__")
    with pytest.raises(FrozenInstanceError):
        plan.intended_state = plan.baseline_state  # type: ignore[misc]


@pytest.mark.parametrize("field", ["refresh", "preview", "baseline", "scope", "intended"])
def test_manual_construction_rejects_substituted_evidence(field: str) -> None:
    result, _ = _replacement_result()
    valid = plan_favorites_external_refresh_detach(
        result,
        result.preview.records[0],
        FavoritesExternalRefreshDetachScope.FIELD,
        field_name="name",
    )

    other_result = _result(
        (valid.baseline_state,),
        (_observation("Other", revision="other"),),
    )
    assert valid.selected_preview.evidence is not None
    mismatched_preview = replace(
        valid.selected_preview,
        evidence=replace(valid.selected_preview.evidence, revision="other"),
    )
    assert valid.baseline_state.last_observation is not None
    mismatched_baseline = replace(
        valid.baseline_state,
        last_observation=replace(
            valid.baseline_state.last_observation,
            revision="other",
        ),
    )

    values = {
        "refresh_result": other_result if field == "refresh" else valid.refresh_result,
        "selected_preview": (
            mismatched_preview if field == "preview" else valid.selected_preview
        ),
        "baseline_state": (
            mismatched_baseline if field == "baseline" else valid.baseline_state
        ),
        "scope": (
            FavoritesExternalRefreshDetachScope.RECORD
            if field == "scope"
            else valid.scope
        ),
        "field_name": None if field == "scope" else valid.field_name,
        "intended_state": (
            valid.baseline_state if field == "intended" else valid.intended_state
        ),
    }

    with pytest.raises(ValueError):
        FavoritesExternalRefreshDetachPlan(**values)  # type: ignore[arg-type]


def test_refresh_detach_symbols_are_package_exports() -> None:
    for name in (
        "FavoritesExternalRefreshDetachPlan",
        "FavoritesExternalRefreshDetachScope",
        "plan_favorites_external_refresh_detach",
    ):
        assert name in sds200.__all__
        assert hasattr(sds200, name)
