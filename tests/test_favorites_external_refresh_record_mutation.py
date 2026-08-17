from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

import sds200
import sds200.favorites_external_refresh_record_mutation as mutation
from sds200 import (
    FavoritesExternalProvenanceStorageError,
    FavoritesExternalRefreshRecordMutationDurableResult,
    FavoritesExternalRefreshRecordMutationError,
    FavoritesExternalRefreshRecordMutationPersistenceError,
    execute_favorites_external_refresh_record_mutation_durably,
    load_favorites_external_provenance,
    save_favorites_external_provenance,
)
from tests.checkpoint_c_helpers import Storage, import_plan


def test_true_first_import_delegates_once_reads_once_and_publishes(tmp_path) -> None:
    _, _, path, plan = import_plan(tmp_path)
    storage = Storage(plan.write_plan.intended_snapshot)
    opaque = object()
    calls = []
    result = execute_favorites_external_refresh_record_mutation_durably(
        plan, lambda value: calls.append(value) or opaque, storage, path
    )
    assert result.execution_result is opaque
    assert result.baseline_provenance_records is None
    assert result.observed_snapshot is plan.write_plan.intended_snapshot
    assert calls == [plan.write_plan]
    assert storage.calls == 1
    assert path.is_file()
    assert load_favorites_external_provenance(path, result.observed_snapshot) == (
        plan.intended_provenance_records
    )


def test_present_empty_is_not_absent_and_baseline_precedes_executor(tmp_path) -> None:
    _, _, path, absent_plan = import_plan(tmp_path)
    save_favorites_external_provenance((), path)
    calls = []
    with pytest.raises(FavoritesExternalRefreshRecordMutationError, match="exact baseline"):
        execute_favorites_external_refresh_record_mutation_durably(
            absent_plan, lambda value: calls.append(value), Storage(object()), path
        )
    assert calls == []


def test_executor_exception_propagates_without_read_or_publication(tmp_path) -> None:
    _, _, path, plan = import_plan(tmp_path)
    storage = Storage(plan.write_plan.intended_snapshot)
    def fail(_):
        raise LookupError("opaque backend failure")
    with pytest.raises(LookupError, match="opaque backend"):
        execute_favorites_external_refresh_record_mutation_durably(plan, fail, storage, path)
    assert storage.calls == 0
    assert not path.exists()


@pytest.mark.parametrize("readback", [OSError("private detail"), object()])
def test_bad_readback_is_redacted_and_does_not_publish(tmp_path, readback) -> None:
    _, _, path, plan = import_plan(tmp_path)
    with pytest.raises(FavoritesExternalRefreshRecordMutationError) as caught:
        execute_favorites_external_refresh_record_mutation_durably(
            plan, lambda _: object(), Storage(readback), path
        )
    assert "private detail" not in str(caught.value)
    assert not path.exists()


def test_mismatched_readback_does_not_publish(tmp_path) -> None:
    _, _, path, plan = import_plan(tmp_path)
    with pytest.raises(FavoritesExternalRefreshRecordMutationError, match="exactly match"):
        execute_favorites_external_refresh_record_mutation_durably(
            plan, lambda _: object(), Storage(plan.write_plan.baseline_snapshot), path
        )
    assert not path.exists()


def test_conditional_race_after_verified_write_is_stable_and_no_rollback(
    tmp_path, monkeypatch
) -> None:
    _, _, path, plan = import_plan(tmp_path)
    calls = []
    def race(*args, **kwargs):
        save_favorites_external_provenance((), path)
        raise FavoritesExternalProvenanceStorageError("race detail")
    monkeypatch.setattr(mutation, "save_favorites_external_provenance_if_current", race)
    with pytest.raises(FavoritesExternalRefreshRecordMutationPersistenceError) as caught:
        execute_favorites_external_refresh_record_mutation_durably(
            plan,
            lambda value: calls.append(value),
            Storage(plan.write_plan.intended_snapshot),
            path,
        )
    assert str(caught.value) == (
        "Structural Favorites storage was verified, but provenance persistence did not complete."
    )
    assert calls == [plan.write_plan]
    assert load_favorites_external_provenance(path, plan.write_plan.intended_snapshot) == ()


def test_durable_result_boundary_rejects_malformed_constructor_evidence(tmp_path) -> None:
    _, _, path, plan = import_plan(tmp_path)
    good = FavoritesExternalRefreshRecordMutationDurableResult(
        plan, object(), plan.write_plan.intended_snapshot, None,
        plan.intended_provenance_records, path,
    )
    with pytest.raises(TypeError):
        replace(good, plan=object())
    with pytest.raises(TypeError):
        replace(good, observed_snapshot=object())
    with pytest.raises(TypeError):
        replace(good, baseline_provenance_records=[])
    with pytest.raises(TypeError):
        replace(good, intended_provenance_records=[])
    with pytest.raises(TypeError):
        replace(good, intended_provenance_records=(object(),))
    with pytest.raises(TypeError):
        replace(good, provenance_path=str(path))
    with pytest.raises(ValueError):
        replace(good, provenance_path=Path("relative.json"))
    with pytest.raises(FrozenInstanceError):
        good.plan = plan  # type: ignore[misc]
    assert not hasattr(good, "__dict__")


def test_public_mutation_exports() -> None:
    for name in (
        "FavoritesExternalRefreshRecordMutationExecutor",
        "FavoritesExternalRefreshRecordMutationDurableResult",
        "execute_favorites_external_refresh_record_mutation_durably",
    ):
        assert name in sds200.__all__
