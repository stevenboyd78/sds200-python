from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

import sds200
from sds200 import (
    FavoritesExternalAcceptanceError,
    FavoritesExternalChangeKind,
    FavoritesExternalFieldBinding,
    FavoritesExternalFieldObservation,
    FavoritesExternalFieldObservationState,
    FavoritesExternalFieldOwnership,
    FavoritesExternalNameAcceptancePlan,
    FavoritesExternalObservationEvidence,
    FavoritesExternalProvenanceLifecycle,
    FavoritesExternalProvenanceLifecycleSnapshot,
    FavoritesExternalProvenanceLifecycleState,
    FavoritesExternalRecordIdentity,
    FavoritesExternalRecordObservation,
    FavoritesExternalRecordObservationState,
    FavoritesExternalRecordState,
    FavoritesExternalRefreshNameAcceptancePlan,
    FavoritesExternalRefreshResult,
    FavoritesExternalRefreshSession,
    FavoritesExternalSourceIdentity,
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
    bind_favorites_external_record,
    plan_favorites_external_refresh_name_acceptance,
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
    state: FavoritesExternalRecordObservationState = (
        FavoritesExternalRecordObservationState.ACTIVE
    ),
    include_frequency: bool = True,
) -> FavoritesExternalRecordObservation:
    fields = ()
    if state is FavoritesExternalRecordObservationState.ACTIVE:
        fields = (
            FavoritesExternalFieldObservation(
                name="name",
                state=FavoritesExternalFieldObservationState.VALUE,
                value=name,
            ),
        )
        if include_frequency:
            fields += (
                FavoritesExternalFieldObservation(
                    name="frequency",
                    state=FavoritesExternalFieldObservationState.VALUE,
                    value="155100000",
                ),
            )
    return FavoritesExternalRecordObservation(
        identity=identity or _identity(),
        evidence=FavoritesExternalObservationEvidence(
            observed_at=datetime(2026, 8, 15, 3, 0, tzinfo=UTC),
            revision=revision,
        ),
        fields=fields,
        state=state,
    )


def _state(
    *,
    ownership: FavoritesExternalFieldOwnership = FavoritesExternalFieldOwnership.EXTERNAL,
) -> tuple[FavoritesStorageSnapshot, FavoritesExternalRecordState]:
    snapshot = _snapshot()
    target = select_favorites_record_target(snapshot, 5, document_index=0)
    state = bind_favorites_external_record(
        target,
        _observation(target.record.fields[2]),
        (
            FavoritesExternalFieldBinding(
                name="name",
                field_index=2,
                ownership=ownership,
            ),
        ),
    )
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
    FavoritesExternalRecordObservation,
]:
    _, state = _state()
    observation = _observation("Dispatch Updated", revision="revision-2")
    return _result((state,), (observation,)), state, observation


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


def test_refresh_selection_composes_exact_existing_name_plan() -> None:
    result, state, observation = _replacement_result()
    selected = result.preview.records[0]

    composition = plan_favorites_external_refresh_name_acceptance(result, selected)

    assert composition.refresh_result is result
    assert composition.selected_preview is selected
    assert composition.baseline_state is state
    assert composition.observation is observation
    assert isinstance(composition.acceptance_plan, FavoritesExternalNameAcceptancePlan)
    assert composition.acceptance_plan.preview == selected
    assert composition.acceptance_plan.write_plan.baseline_snapshot is (
        result.lifecycle_snapshot.favorites_snapshot
    )
    before = state.target.record.fields
    after = composition.acceptance_plan.intended_state.target.record.fields
    changed_indexes = tuple(
        index
        for index, pair in enumerate(zip(before, after, strict=True))
        if pair[0] != pair[1]
    )
    assert changed_indexes == (2,)
    assert after[4] == before[4]


def test_planner_performs_no_second_source_read_or_lifecycle_snapshot(
    tmp_path: Path,
) -> None:
    snapshot, state = _state()
    path = tmp_path / "provenance.json"
    save_favorites_external_provenance((state,), path)
    lifecycle = _CountingLifecycle(snapshot, path)
    lifecycle.start()
    source = _CountingSource((_observation("Dispatch Updated", revision="revision-2"),))
    result = FavoritesExternalRefreshSession(lifecycle, source).refresh()

    plan_favorites_external_refresh_name_acceptance(result, result.preview.records[0])

    assert lifecycle.snapshot_calls == 1
    assert source.read_calls == 1


@pytest.mark.parametrize("records", [None, ()])
def test_missing_or_empty_provenance_rejected(
    records: tuple[FavoritesExternalRecordState, ...] | None,
) -> None:
    result = _result(records, (_observation("Dispatch Updated"),))
    with pytest.raises(ValueError, match="linked selected preview"):
        plan_favorites_external_refresh_name_acceptance(result, result.preview.records[0])


def test_preview_not_in_result_and_evidence_mismatch_rejected() -> None:
    result, _, _ = _replacement_result()
    selected = result.preview.records[0]
    absent = replace(selected, evidence=replace(selected.evidence, revision="other"))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="exact selected preview once"):
        plan_favorites_external_refresh_name_acceptance(result, absent)


@pytest.mark.parametrize("case", ["added", "removed", "conflict", "local_only", "unchanged"])
def test_unsupported_refresh_selection_rejected(case: str) -> None:
    _, linked = _state()
    if case == "added":
        result = _result((), (_observation("New Record"),))
    elif case == "removed":
        result = _result(
            (linked,),
            (_observation(state=FavoritesExternalRecordObservationState.REMOVED),),
        )
    elif case == "conflict":
        _, local = _state(ownership=FavoritesExternalFieldOwnership.LOCAL)
        result = _result((local,), (_observation("Provider Changed"),))
    elif case == "local_only":
        result = _result((linked,), ())
    else:
        result = _result(
            (linked,),
            (
                _observation(
                    linked.target.record.fields[2],
                    include_frequency=False,
                ),
            ),
        )

    selected = result.preview.records[0]
    assert selected.kind is FavoritesExternalChangeKind(case)
    with pytest.raises((ValueError, FavoritesExternalAcceptanceError)):
        plan_favorites_external_refresh_name_acceptance(result, selected)


def test_planner_requires_exact_public_types() -> None:
    result, _, _ = _replacement_result()
    with pytest.raises(TypeError):
        plan_favorites_external_refresh_name_acceptance(object(), result.preview.records[0])  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        plan_favorites_external_refresh_name_acceptance(result, object())  # type: ignore[arg-type]


def test_composition_is_frozen_and_slotted() -> None:
    result, _, _ = _replacement_result()
    composition = plan_favorites_external_refresh_name_acceptance(
        result, result.preview.records[0]
    )
    assert not hasattr(composition, "__dict__")
    with pytest.raises(FrozenInstanceError):
        composition.selected_preview = composition.selected_preview  # type: ignore[misc]


@pytest.mark.parametrize("field", ["refresh", "preview", "observation", "baseline", "plan"])
def test_manual_construction_rejects_mismatched_evidence(field: str) -> None:
    result, _, _ = _replacement_result()
    valid = plan_favorites_external_refresh_name_acceptance(result, result.preview.records[0])
    _, state = _state()
    other_result = _result((state,), (_observation("Other", revision="other"),))
    other = plan_favorites_external_refresh_name_acceptance(
        other_result, other_result.preview.records[0]
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
        "refresh_result": (
            other_result if field == "refresh" else valid.refresh_result
        ),
        "selected_preview": (
            mismatched_preview if field == "preview" else valid.selected_preview
        ),
        "observation": (
            _observation("Other", revision="other")
            if field == "observation"
            else valid.observation
        ),
        "baseline_state": (
            mismatched_baseline if field == "baseline" else valid.baseline_state
        ),
        "acceptance_plan": (
            other.acceptance_plan if field == "plan" else valid.acceptance_plan
        ),
    }
    with pytest.raises(ValueError):
        FavoritesExternalRefreshNameAcceptancePlan(**values)  # type: ignore[arg-type]


def test_refresh_acceptance_symbols_are_package_exports() -> None:
    for name in (
        "FavoritesExternalRefreshNameAcceptancePlan",
        "plan_favorites_external_refresh_name_acceptance",
    ):
        assert name in sds200.__all__
        assert hasattr(sds200, name)
