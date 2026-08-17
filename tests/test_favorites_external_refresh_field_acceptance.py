from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

import sds200
from sds200 import (
    FavoritesExternalFieldMapping,
    FavoritesExternalFieldObservation,
    FavoritesExternalFieldObservationState,
    FavoritesExternalObservationEvidence,
    FavoritesExternalProvenanceLifecycleSnapshot,
    FavoritesExternalProvenanceLifecycleState,
    FavoritesExternalRecordIdentity,
    FavoritesExternalRecordObservation,
    FavoritesExternalRecordState,
    FavoritesExternalRefreshFieldAcceptancePlan,
    FavoritesExternalRefreshResult,
    FavoritesExternalSourceIdentity,
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
    plan_favorites_external_refresh_field_acceptance,
    preview_favorites_external_import,
    select_favorites_record_target,
)

_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "favorites"


def _evidence(revision: str) -> FavoritesExternalObservationEvidence:
    return FavoritesExternalObservationEvidence(
        observed_at=datetime(2026, 8, 16, tzinfo=UTC), revision=revision
    )


def _inputs() -> tuple[FavoritesExternalRefreshResult, FavoritesExternalFieldMapping]:
    snapshot = FavoritesStorageSnapshot(
        catalog_bytes=(_FIXTURE_ROOT / "synthetic-f_list.cfg").read_bytes(),
        documents=(
            FavoritesStorageDocument(
                filename="f_000001.hpd",
                content=(_FIXTURE_ROOT / "synthetic-favorites.hpd").read_bytes(),
            ),
        ),
    )
    target = select_favorites_record_target(snapshot, 5, document_index=0)
    identity = FavoritesExternalRecordIdentity(
        source=FavoritesExternalSourceIdentity(
            provider="synthetic-provider", dataset="metro"
        ),
        record_id="frequency-101",
    )
    field = FavoritesExternalFieldObservation(
        name="frequency",
        state=FavoritesExternalFieldObservationState.VALUE,
        value="155100000",
    )
    observation = FavoritesExternalRecordObservation(
        identity=identity, evidence=_evidence("provider-r2"), fields=(field,)
    )
    state = FavoritesExternalRecordState(
        target=target,
        fields=(),
        external_identity=identity,
        last_observation=_evidence("accepted-r1"),
    )
    lifecycle_snapshot = FavoritesExternalProvenanceLifecycleSnapshot(
        state=FavoritesExternalProvenanceLifecycleState.ACTIVE,
        provenance_path=Path("/tmp/favorites-field-test.json"),
        favorites_snapshot=snapshot,
        provenance_records=(state,),
        last_error=None,
    )
    observations = (observation,)
    refresh = FavoritesExternalRefreshResult(
        lifecycle_snapshot=lifecycle_snapshot,
        observations=observations,
        preview=preview_favorites_external_import((state,), observations),
    )
    mapping = FavoritesExternalFieldMapping(
        target=target,
        observation=observation,
        field=field,
        field_index=4,
        scanner_value="155100000",
    )
    return refresh, mapping


def test_selects_exact_refresh_mapping_and_delegates_preview() -> None:
    refresh, mapping = _inputs()
    selected = refresh.preview.records[0]

    plan = plan_favorites_external_refresh_field_acceptance(
        refresh, selected, mapping
    )

    assert isinstance(plan, FavoritesExternalRefreshFieldAcceptancePlan)
    assert plan.refresh_result is refresh
    assert plan.selected_preview is selected
    assert plan.mapping is mapping
    assert plan.baseline_state is refresh.lifecycle_snapshot.provenance_records[0]
    assert plan.acceptance_plan.mapping is mapping
    assert plan.acceptance_plan.preview == selected
    assert "FavoritesExternalRefreshFieldAcceptancePlan" in sds200.__all__
    assert "plan_favorites_external_refresh_field_acceptance" in sds200.__all__
    with pytest.raises(FrozenInstanceError):
        plan.mapping = mapping  # type: ignore[misc]
    assert not hasattr(plan, "__dict__")


def test_rejects_absent_reconstructed_or_foreign_refresh_evidence() -> None:
    refresh, mapping = _inputs()
    selected = refresh.preview.records[0]

    with pytest.raises(ValueError, match="retained selected preview"):
        plan_favorites_external_refresh_field_acceptance(
            refresh, replace(selected), mapping
        )
    with pytest.raises(ValueError, match="retained mapping observation"):
        plan_favorites_external_refresh_field_acceptance(
            refresh,
            selected,
            replace(mapping, observation=replace(mapping.observation)),
        )
    other_target = select_favorites_record_target(
        refresh.lifecycle_snapshot.favorites_snapshot, 6, document_index=0
    )
    with pytest.raises(ValueError, match="mapping target"):
        plan_favorites_external_refresh_field_acceptance(
            refresh, selected, replace(mapping, target=other_target)
        )


def test_types_and_result_invariants_fail_closed() -> None:
    refresh, mapping = _inputs()
    selected = refresh.preview.records[0]
    plan = plan_favorites_external_refresh_field_acceptance(
        refresh, selected, mapping
    )
    with pytest.raises(TypeError):
        plan_favorites_external_refresh_field_acceptance(object(), selected, mapping)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="baseline state"):
        FavoritesExternalRefreshFieldAcceptancePlan(
            refresh_result=refresh,
            selected_preview=selected,
            mapping=mapping,
            baseline_state=replace(plan.baseline_state),
            acceptance_plan=plan.acceptance_plan,
        )
