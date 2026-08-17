from __future__ import annotations

from dataclasses import replace

import pytest

import sds200
from sds200 import (
    FavoritesExternalChangeKind,
    FavoritesExternalProvenanceLifecycle,
    FavoritesExternalRefreshDetachPlan,
    FavoritesExternalRefreshDetachScope,
    FavoritesExternalRefreshResult,
    deserialize_favorites_external_provenance,
    plan_favorites_external_refresh_record_delete,
    plan_favorites_external_refresh_record_keep_local,
    preview_favorites_external_import,
    serialize_favorites_external_provenance,
)
from tests.checkpoint_c_helpers import Storage, linked_state, removed_observation, snapshot


def _refresh(tmp_path, *, index=5, detached=False, local=False):
    source = snapshot()
    state = linked_state(source, index, "remove-me", detached=detached)
    if local:
        state = replace(
            state,
            fields=(replace(state.fields[0], ownership=sds200.FavoritesExternalFieldOwnership.LOCAL,
                            last_external=None),),
        )
    path = tmp_path / "missing-is-fine.json"
    lifecycle = FavoritesExternalProvenanceLifecycle(Storage(source), path)
    lifecycle.start()
    lifecycle._provenance_records = (state,)  # exact synthetic retained lifecycle evidence
    observation = removed_observation("remove-me")
    snap = lifecycle.snapshot()
    refresh = FavoritesExternalRefreshResult(
        snap, (observation,), preview_favorites_external_import((state,), (observation,))
    )
    return state, observation, refresh


def test_delete_uses_current_removed_evidence_but_historical_baseline(tmp_path) -> None:
    state, observation, refresh = _refresh(tmp_path)
    selected = refresh.preview.records[0]
    assert selected.kind is FavoritesExternalChangeKind.REMOVED
    assert state.last_observation != observation.evidence
    plan = plan_favorites_external_refresh_record_delete(refresh, selected)
    assert plan.baseline_state is state
    assert plan.baseline_state.last_observation != plan.observation.evidence
    assert plan.intended_provenance_records == ()
    assert deserialize_favorites_external_provenance(
        serialize_favorites_external_provenance(plan.intended_provenance_records),
        plan.write_plan.intended_snapshot,
    ) == ()


def test_delete_rebases_later_record_without_changing_history(tmp_path) -> None:
    source = snapshot()
    removed = linked_state(source, 5, "remove-me")
    later = linked_state(source, 14, "later")
    lifecycle = FavoritesExternalProvenanceLifecycle(Storage(source), tmp_path / "none.json")
    lifecycle.start()
    lifecycle._provenance_records = (later, removed)
    observation = removed_observation("remove-me")
    refresh = FavoritesExternalRefreshResult(
        lifecycle.snapshot(), (observation,),
        preview_favorites_external_import((later, removed), (observation,)),
    )
    selected = next(
        p
        for p in refresh.preview.records
        if p.external_identity == removed.external_identity
    )
    plan = plan_favorites_external_refresh_record_delete(refresh, selected)
    shifted = plan.intended_provenance_records[0]
    assert shifted.target.source_index == later.target.source_index - 1
    assert shifted.target.record.raw_bytes == later.target.record.raw_bytes
    assert replace(shifted, target=later.target) == later
    assert plan.baseline_provenance_records == (later, removed)


def test_delete_requires_exact_removed_preview_target_identity_and_leaf(tmp_path) -> None:
    state, _, refresh = _refresh(tmp_path)
    selected = refresh.preview.records[0]
    with pytest.raises(ValueError, match="exact retained"):
        plan_favorites_external_refresh_record_delete(refresh, replace(selected))
    _, _, conflict_refresh = _refresh(tmp_path, local=True)
    with pytest.raises(ValueError, match="REMOVED"):
        plan_favorites_external_refresh_record_delete(
            conflict_refresh, conflict_refresh.preview.records[0]
        )
    detached_state, observation, _ = _refresh(tmp_path, detached=True)
    detached_snapshot = replace(
        refresh.lifecycle_snapshot, provenance_records=(detached_state,)
    )
    detached_refresh = object.__new__(FavoritesExternalRefreshResult)
    object.__setattr__(detached_refresh, "lifecycle_snapshot", detached_snapshot)
    object.__setattr__(detached_refresh, "observations", (observation,))
    object.__setattr__(detached_refresh, "preview", refresh.preview)
    with pytest.raises(ValueError, match="detached"):
        plan_favorites_external_refresh_record_delete(
            detached_refresh, detached_refresh.preview.records[0]
        )
    assert detached_state.detached and observation.state.name == "REMOVED"
    unsupported = linked_state(snapshot(), 3, "remove-me")
    lifecycle = FavoritesExternalProvenanceLifecycle(Storage(snapshot()), tmp_path / "other.json")
    lifecycle.start()
    lifecycle._provenance_records = (unsupported,)
    bad = FavoritesExternalRefreshResult(
        lifecycle.snapshot(), (removed_observation("remove-me"),),
        preview_favorites_external_import((unsupported,), (removed_observation("remove-me"),)),
    )
    with pytest.raises(ValueError, match="deletion is not supported"):
        plan_favorites_external_refresh_record_delete(bad, bad.preview.records[0])
    assert state.target == selected.target


@pytest.mark.parametrize("local", [False, True])
def test_keep_local_delegates_record_detach_without_favorites_mutation(tmp_path, local) -> None:
    _, observation, refresh = _refresh(tmp_path, local=local)
    selected = refresh.preview.records[0]
    assert selected.kind in {
        FavoritesExternalChangeKind.REMOVED,
        FavoritesExternalChangeKind.CONFLICT,
    }
    plan = plan_favorites_external_refresh_record_keep_local(refresh, selected)
    assert type(plan) is FavoritesExternalRefreshDetachPlan
    assert plan.scope is FavoritesExternalRefreshDetachScope.RECORD
    assert plan.refresh_result.lifecycle_snapshot.favorites_snapshot is (
        refresh.lifecycle_snapshot.favorites_snapshot
    )
    assert plan.selected_preview.evidence == observation.evidence
    assert plan.selected_preview.external_identity == observation.identity


def test_keep_local_rejects_foreign_or_non_removed_evidence(tmp_path) -> None:
    _, _, refresh = _refresh(tmp_path)
    with pytest.raises(ValueError, match="exact retained"):
        plan_favorites_external_refresh_record_keep_local(
            refresh, replace(refresh.preview.records[0])
        )


def test_public_removal_exports() -> None:
    assert "plan_favorites_external_refresh_record_delete" in sds200.__all__
    assert "plan_favorites_external_refresh_record_keep_local" in sds200.__all__
