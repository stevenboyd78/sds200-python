from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

import sds200
from sds200 import (
    FavoritesExternalProvenanceLifecycleAdvanceError,
    FavoritesExternalRefreshRecordMutationResult,
    execute_favorites_external_refresh_record_mutation,
    load_favorites_external_provenance,
)
from tests.checkpoint_c_helpers import import_plan


def _execute(tmp_path):
    lifecycle, storage, path, plan = import_plan(tmp_path)
    opaque = object()
    calls = []
    def executor(write_plan):
        calls.append(write_plan)
        storage.value = write_plan.intended_snapshot
        return opaque
    result = execute_favorites_external_refresh_record_mutation(plan, lifecycle, executor)
    return lifecycle, storage, path, plan, result, opaque, calls


def test_first_import_advances_exact_complete_lifecycle_without_provider_reread(tmp_path) -> None:
    lifecycle, storage, path, plan, result, opaque, calls = _execute(tmp_path)
    assert result.plan is plan
    assert result.durable_result.execution_result is opaque
    assert result.lifecycle_snapshot == lifecycle.snapshot()
    assert result.lifecycle_snapshot.favorites_snapshot is result.durable_result.observed_snapshot
    assert result.lifecycle_snapshot.provenance_records == plan.intended_provenance_records
    assert calls == [plan.write_plan]
    assert storage.calls == 2  # startup plus the single durability readback
    assert load_favorites_external_provenance(
        path, result.lifecycle_snapshot.favorites_snapshot
    ) == plan.intended_provenance_records


def test_stale_refresh_rejects_before_executor_readback_or_publication(tmp_path) -> None:
    lifecycle, storage, path, plan, result, _, _ = _execute(tmp_path)
    before = path.read_bytes()
    calls = []
    with pytest.raises(
        FavoritesExternalProvenanceLifecycleAdvanceError, match="selected refresh"
    ):
        execute_favorites_external_refresh_record_mutation(
            plan, lifecycle, lambda value: calls.append(value)
        )
    assert calls == []
    assert storage.calls == 2
    assert path.read_bytes() == before
    assert lifecycle.snapshot() == result.lifecycle_snapshot


def test_identity_idempotence_and_convergent_result_rejection(tmp_path) -> None:
    lifecycle, _, _, _, result, _, _ = _execute(tmp_path)
    assert lifecycle.advance_after_record_mutation(result.durable_result) == (
        result.lifecycle_snapshot
    )
    with pytest.raises(FavoritesExternalProvenanceLifecycleAdvanceError):
        lifecycle.advance_after_record_mutation(replace(result.durable_result))
    lifecycle._provenance_records = ()
    with pytest.raises(FavoritesExternalProvenanceLifecycleAdvanceError, match="no longer matches"):
        lifecycle.advance_after_record_mutation(result.durable_result)


def test_orchestration_result_boundary_is_exact_frozen_and_slotted(tmp_path) -> None:
    _, _, _, plan, result, _, _ = _execute(tmp_path)
    with pytest.raises(TypeError):
        FavoritesExternalRefreshRecordMutationResult(
            object(), result.durable_result, result.lifecycle_snapshot
        )
    with pytest.raises(TypeError):
        FavoritesExternalRefreshRecordMutationResult(plan, object(), result.lifecycle_snapshot)
    with pytest.raises(TypeError):
        FavoritesExternalRefreshRecordMutationResult(plan, result.durable_result, object())
    other_plan = replace(plan)
    foreign_durable = replace(result.durable_result, plan=other_plan)
    with pytest.raises(ValueError):
        FavoritesExternalRefreshRecordMutationResult(
            plan,
            foreign_durable,
            result.lifecycle_snapshot,
        )
    with pytest.raises(FrozenInstanceError):
        result.plan = plan  # type: ignore[misc]
    assert not hasattr(result, "__dict__")


def test_public_orchestration_exports() -> None:
    assert "FavoritesExternalRefreshRecordMutationResult" in sds200.__all__
    assert "execute_favorites_external_refresh_record_mutation" in sds200.__all__
