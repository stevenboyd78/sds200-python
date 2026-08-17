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
    FavoritesExternalProvenanceLifecycle,
    FavoritesExternalProvenanceLifecycleAdvanceError,
    FavoritesExternalRecordIdentity,
    FavoritesExternalRecordObservation,
    FavoritesExternalRecordState,
    FavoritesExternalRefreshFieldAcceptanceResult,
    FavoritesExternalRefreshResult,
    FavoritesExternalSourceIdentity,
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
    execute_favorites_external_refresh_field_acceptance,
    load_favorites_external_provenance,
    plan_favorites_external_refresh_field_acceptance,
    preview_favorites_external_import,
    save_favorites_external_provenance,
    select_favorites_record_target,
)

_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "favorites"


def _inputs():
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
        identity=identity,
        evidence=FavoritesExternalObservationEvidence(
            observed_at=datetime(2026, 8, 16, tzinfo=UTC), revision="provider-r2"
        ),
        fields=(field,),
    )
    state = FavoritesExternalRecordState(
        target=target,
        fields=(),
        external_identity=identity,
        last_observation=FavoritesExternalObservationEvidence(
            observed_at=datetime(2026, 8, 15, tzinfo=UTC), revision="accepted-r1"
        ),
    )
    mapping = FavoritesExternalFieldMapping(
        target=target,
        observation=observation,
        field=field,
        field_index=4,
        scanner_value="155100000",
    )
    return snapshot, (state,), (observation,), mapping


class _Storage:
    def __init__(self, snapshot: FavoritesStorageSnapshot) -> None:
        self.snapshot = snapshot
        self.calls = 0

    def read_snapshot(self) -> FavoritesStorageSnapshot:
        self.calls += 1
        return self.snapshot


def _prepared(tmp_path: Path):
    baseline, records, observations, mapping = _inputs()
    path = tmp_path / "state" / "favorites-external-provenance.json"
    save_favorites_external_provenance(records, path)
    storage = _Storage(baseline)
    lifecycle = FavoritesExternalProvenanceLifecycle(storage, path)
    lifecycle_snapshot = lifecycle.start()
    exact_refresh = FavoritesExternalRefreshResult(
        lifecycle_snapshot=lifecycle_snapshot,
        observations=observations,
        preview=preview_favorites_external_import(records, observations),
    )
    plan = plan_favorites_external_refresh_field_acceptance(
        exact_refresh, exact_refresh.preview.records[0], mapping
    )
    return lifecycle, storage, plan, path


@pytest.mark.parametrize("value", ["155100000", "155000000"])
def test_orchestration_durably_advances_changed_and_noop(
    tmp_path: Path, value: str
) -> None:
    lifecycle, storage, plan, path = _prepared(tmp_path)
    if value == "155000000":
        field = replace(plan.mapping.field, value=value)
        observation = replace(plan.mapping.observation, fields=(field,))
        mapping = replace(
            plan.mapping,
            observation=observation,
            field=field,
            scanner_value=value,
        )
        refresh = FavoritesExternalRefreshResult(
            lifecycle_snapshot=plan.refresh_result.lifecycle_snapshot,
            observations=(observation,),
            preview=preview_favorites_external_import(
                (plan.baseline_state,), (observation,)
            ),
        )
        plan = plan_favorites_external_refresh_field_acceptance(
            refresh, refresh.preview.records[0], mapping
        )
    opaque = object()
    calls: list[object] = []

    def executor(write_plan: object) -> object:
        calls.append(write_plan)
        storage.snapshot = plan.acceptance_plan.write_plan.intended_snapshot
        return opaque

    result = execute_favorites_external_refresh_field_acceptance(
        plan, lifecycle, executor
    )

    assert isinstance(result, FavoritesExternalRefreshFieldAcceptanceResult)
    assert result.plan is plan
    assert result.durable_result.execution.execution_result is opaque
    assert result.lifecycle_snapshot == lifecycle.snapshot()
    assert result.lifecycle_snapshot.favorites_snapshot is (
        result.durable_result.execution.observed_snapshot
    )
    assert result.lifecycle_snapshot.provenance_records is (
        result.durable_result.provenance_records
    )
    assert calls == [plan.acceptance_plan.write_plan]
    assert storage.calls == 2
    assert load_favorites_external_provenance(
        path, result.lifecycle_snapshot.favorites_snapshot
    ) == result.lifecycle_snapshot.provenance_records

    assert lifecycle.advance_after_field_acceptance(result.durable_result) == (
        result.lifecycle_snapshot
    )
    with pytest.raises(FavoritesExternalProvenanceLifecycleAdvanceError):
        lifecycle.advance_after_field_acceptance(replace(result.durable_result))
    with pytest.raises(FrozenInstanceError):
        result.plan = plan  # type: ignore[misc]
    assert not hasattr(result, "__dict__")


def test_stale_refresh_precedes_executor_readback_and_publication(tmp_path: Path) -> None:
    lifecycle, storage, plan, path = _prepared(tmp_path)
    baseline_content = path.read_bytes()
    lifecycle.close()
    calls: list[object] = []

    with pytest.raises(RuntimeError, match="must be active"):
        execute_favorites_external_refresh_field_acceptance(
            plan, lifecycle, lambda write_plan: calls.append(write_plan)
        )
    assert calls == []
    assert storage.calls == 1
    assert path.read_bytes() == baseline_content


def test_public_types_and_exports() -> None:
    assert "FavoritesExternalRefreshFieldAcceptanceResult" in sds200.__all__
    assert "execute_favorites_external_refresh_field_acceptance" in sds200.__all__
    with pytest.raises(TypeError):
        execute_favorites_external_refresh_field_acceptance(object(), object(), object())  # type: ignore[arg-type]
