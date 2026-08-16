from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

import sds200
from sds200 import (
    FavoritesExternalFieldBinding,
    FavoritesExternalFieldObservation,
    FavoritesExternalFieldObservationState,
    FavoritesExternalFieldOwnership,
    FavoritesExternalNameAcceptanceProvenanceError,
    FavoritesExternalObservationEvidence,
    FavoritesExternalProvenanceLifecycle,
    FavoritesExternalProvenanceLifecycleAdvanceError,
    FavoritesExternalRecordIdentity,
    FavoritesExternalRecordObservation,
    FavoritesExternalRecordState,
    FavoritesExternalRefreshNameAcceptanceResult,
    FavoritesExternalRefreshSession,
    FavoritesExternalSourceIdentity,
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
    bind_favorites_external_record,
    execute_favorites_external_name_acceptance_durably,
    execute_favorites_external_refresh_name_acceptance,
    load_favorites_external_provenance,
    plan_favorites_external_refresh_name_acceptance,
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


def _evidence(revision: str) -> FavoritesExternalObservationEvidence:
    return FavoritesExternalObservationEvidence(
        observed_at=datetime(2026, 8, 16, tzinfo=UTC),
        revision=revision,
    )


def _observation(
    name: str,
    *,
    revision: str,
    identity: FavoritesExternalRecordIdentity | None = None,
) -> FavoritesExternalRecordObservation:
    return FavoritesExternalRecordObservation(
        identity=identity or _identity(),
        evidence=_evidence(revision),
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


def _linked_state(snapshot: FavoritesStorageSnapshot) -> FavoritesExternalRecordState:
    target = select_favorites_record_target(snapshot, 5, document_index=0)
    return bind_favorites_external_record(
        target,
        _observation(target.record.fields[2], revision="accepted-r1"),
        (
            FavoritesExternalFieldBinding(
                name="name",
                field_index=2,
                ownership=FavoritesExternalFieldOwnership.EXTERNAL,
            ),
        ),
    )


def _other_state(snapshot: FavoritesStorageSnapshot) -> FavoritesExternalRecordState:
    target = select_favorites_record_target(snapshot, 6, document_index=0)
    return bind_favorites_external_record(
        target,
        _observation(
            target.record.fields[2],
            revision="other-r1",
            identity=_identity("channel-202"),
        ),
        (
            FavoritesExternalFieldBinding(
                name="name",
                field_index=2,
                ownership=FavoritesExternalFieldOwnership.EXTERNAL,
            ),
        ),
    )


class _MutableStorageSource:
    def __init__(self, snapshot: FavoritesStorageSnapshot) -> None:
        self.snapshot = snapshot
        self.read_calls = 0

    def read_snapshot(self) -> FavoritesStorageSnapshot:
        self.read_calls += 1
        return self.snapshot


class _CountingExternalSource:
    def __init__(
        self,
        observations: tuple[FavoritesExternalRecordObservation, ...],
    ) -> None:
        self.observations = observations
        self.read_calls = 0

    def read_observations(self) -> tuple[FavoritesExternalRecordObservation, ...]:
        self.read_calls += 1
        return self.observations


def _prepared(
    tmp_path: Path,
) -> tuple[
    FavoritesExternalProvenanceLifecycle,
    _MutableStorageSource,
    _CountingExternalSource,
    object,
    Path,
]:
    baseline = _snapshot()
    state = _linked_state(baseline)
    path = tmp_path / "state" / "favorites-external-provenance.json"
    save_favorites_external_provenance((state,), path)
    storage = _MutableStorageSource(baseline)
    lifecycle = FavoritesExternalProvenanceLifecycle(storage, path)
    lifecycle.start()
    source = _CountingExternalSource(
        (_observation("Dispatch Updated", revision="provider-r2"),)
    )
    refresh = FavoritesExternalRefreshSession(lifecycle, source).refresh()
    plan = plan_favorites_external_refresh_name_acceptance(
        refresh,
        refresh.preview.records[0],
    )
    return lifecycle, storage, source, plan, path


def test_orchestration_executes_exact_plan_publishes_and_advances(
    tmp_path: Path,
) -> None:
    lifecycle, storage, source, plan, path = _prepared(tmp_path)
    calls: list[object] = []
    backend_result = object()

    def executor(write_plan: object) -> object:
        calls.append(write_plan)
        storage.snapshot = plan.acceptance_plan.write_plan.intended_snapshot
        return backend_result

    result = execute_favorites_external_refresh_name_acceptance(
        plan,
        lifecycle,
        executor,
    )

    assert isinstance(result, FavoritesExternalRefreshNameAcceptanceResult)
    assert result.plan is plan
    assert result.durable_result.execution.plan is plan.acceptance_plan
    assert result.durable_result.execution.execution_result is backend_result
    assert result.lifecycle_snapshot == lifecycle.snapshot()
    assert result.lifecycle_snapshot.favorites_snapshot is (
        result.durable_result.execution.observed_snapshot
    )
    assert result.lifecycle_snapshot.provenance_records is (
        result.durable_result.provenance_records
    )
    assert calls == [plan.acceptance_plan.write_plan]
    assert storage.read_calls == 2
    assert source.read_calls == 1
    assert load_favorites_external_provenance(
        path,
        result.durable_result.execution.observed_snapshot,
    ) == result.durable_result.provenance_records


def test_stale_refresh_is_rejected_before_second_executor_call(
    tmp_path: Path,
) -> None:
    lifecycle, storage, _, plan, _ = _prepared(tmp_path)
    first_calls: list[object] = []

    def first_executor(write_plan: object) -> object:
        first_calls.append(write_plan)
        storage.snapshot = plan.acceptance_plan.write_plan.intended_snapshot
        return object()

    durable = execute_favorites_external_name_acceptance_durably(
        plan.acceptance_plan,
        first_executor,
        storage,
        lifecycle.provenance_path,
        expected_baseline_provenance_records=(
            plan.refresh_result.lifecycle_snapshot.provenance_records
        ),
    )
    lifecycle.advance_after_name_acceptance(durable)
    before = lifecycle.snapshot()
    second_calls: list[object] = []

    with pytest.raises(
        FavoritesExternalProvenanceLifecycleAdvanceError,
        match="selected refresh baseline",
    ):
        execute_favorites_external_refresh_name_acceptance(
            plan,
            lifecycle,
            lambda write_plan: second_calls.append(write_plan),
        )

    assert first_calls == [plan.acceptance_plan.write_plan]
    assert second_calls == []
    assert lifecycle.snapshot() == before


def test_changed_complete_persisted_collection_rejected_before_write(
    tmp_path: Path,
) -> None:
    lifecycle, storage, _, plan, path = _prepared(tmp_path)
    other = _other_state(plan.acceptance_plan.write_plan.baseline_snapshot)
    save_favorites_external_provenance((other, plan.baseline_state), path)
    calls: list[object] = []
    before = lifecycle.snapshot()

    with pytest.raises(
        FavoritesExternalNameAcceptanceProvenanceError,
        match="exact expected baseline collection",
    ):
        execute_favorites_external_refresh_name_acceptance(
            plan,
            lifecycle,
            lambda write_plan: calls.append(write_plan),
        )

    assert calls == []
    assert storage.read_calls == 1
    assert lifecycle.snapshot() == before
    assert load_favorites_external_provenance(
        path,
        plan.acceptance_plan.write_plan.baseline_snapshot,
    ) == (other, plan.baseline_state)


def test_foreign_lifecycle_rejected_before_write(tmp_path: Path) -> None:
    _, _, _, plan, _ = _prepared(tmp_path)
    other_path = tmp_path / "other" / "favorites-external-provenance.json"
    save_favorites_external_provenance((plan.baseline_state,), other_path)
    other_storage = _MutableStorageSource(
        plan.acceptance_plan.write_plan.baseline_snapshot
    )
    other_lifecycle = FavoritesExternalProvenanceLifecycle(
        other_storage,
        other_path,
    )
    other_lifecycle.start()
    calls: list[object] = []

    with pytest.raises(
        FavoritesExternalProvenanceLifecycleAdvanceError,
        match="selected refresh baseline",
    ):
        execute_favorites_external_refresh_name_acceptance(
            plan,
            other_lifecycle,
            lambda write_plan: calls.append(write_plan),
        )

    assert calls == []
    assert other_storage.read_calls == 1


def test_executor_failure_leaves_lifecycle_and_provenance_at_baseline(
    tmp_path: Path,
) -> None:
    lifecycle, storage, _, plan, path = _prepared(tmp_path)
    before = lifecycle.snapshot()

    class SyntheticFailure(RuntimeError):
        pass

    def executor(_: object) -> object:
        raise SyntheticFailure("synthetic failure")

    with pytest.raises(SyntheticFailure, match="synthetic failure"):
        execute_favorites_external_refresh_name_acceptance(
            plan,
            lifecycle,
            executor,
        )

    assert storage.read_calls == 1
    assert lifecycle.snapshot() == before
    assert load_favorites_external_provenance(
        path,
        plan.acceptance_plan.write_plan.baseline_snapshot,
    ) == before.provenance_records


def test_result_rejects_substituted_relationships(tmp_path: Path) -> None:
    lifecycle, storage, _, plan, _ = _prepared(tmp_path)

    def executor(write_plan: object) -> object:
        storage.snapshot = plan.acceptance_plan.write_plan.intended_snapshot
        return write_plan

    result = execute_favorites_external_refresh_name_acceptance(
        plan,
        lifecycle,
        executor,
    )
    mismatched = replace(
        result.lifecycle_snapshot,
        provenance_records=result.durable_result.baseline_provenance_records,
    )

    with pytest.raises(ValueError, match="lifecycle provenance"):
        FavoritesExternalRefreshNameAcceptanceResult(
            plan=result.plan,
            durable_result=result.durable_result,
            lifecycle_snapshot=mismatched,
        )


def test_result_is_frozen_and_slotted(tmp_path: Path) -> None:
    lifecycle, storage, _, plan, _ = _prepared(tmp_path)

    def executor(_: object) -> object:
        storage.snapshot = plan.acceptance_plan.write_plan.intended_snapshot
        return object()

    result = execute_favorites_external_refresh_name_acceptance(
        plan,
        lifecycle,
        executor,
    )

    assert not hasattr(result, "__dict__")
    with pytest.raises(FrozenInstanceError):
        result.lifecycle_snapshot = result.lifecycle_snapshot  # type: ignore[misc]


def test_orchestration_accepts_lifecycle_subclass(tmp_path: Path) -> None:
    baseline = _snapshot()
    state = _linked_state(baseline)
    path = tmp_path / "state" / "favorites-external-provenance.json"
    save_favorites_external_provenance((state,), path)

    class InstrumentedLifecycle(FavoritesExternalProvenanceLifecycle):
        pass

    storage = _MutableStorageSource(baseline)
    lifecycle = InstrumentedLifecycle(storage, path)
    lifecycle.start()
    source = _CountingExternalSource(
        (_observation("Dispatch Updated", revision="provider-r2"),)
    )
    refresh = FavoritesExternalRefreshSession(lifecycle, source).refresh()
    plan = plan_favorites_external_refresh_name_acceptance(
        refresh,
        refresh.preview.records[0],
    )

    def executor(_: object) -> object:
        storage.snapshot = plan.acceptance_plan.write_plan.intended_snapshot
        return object()

    result = execute_favorites_external_refresh_name_acceptance(
        plan,
        lifecycle,
        executor,
    )

    assert result.lifecycle_snapshot == lifecycle.snapshot()
    assert source.read_calls == 1


def test_orchestration_requires_exact_public_types(tmp_path: Path) -> None:
    lifecycle, _, _, plan, _ = _prepared(tmp_path)

    with pytest.raises(TypeError, match="RefreshNameAcceptancePlan"):
        execute_favorites_external_refresh_name_acceptance(  # type: ignore[arg-type]
            object(),
            lifecycle,
            lambda _: object(),
        )
    with pytest.raises(TypeError, match="ProvenanceLifecycle"):
        execute_favorites_external_refresh_name_acceptance(  # type: ignore[arg-type]
            plan,
            object(),
            lambda _: object(),
        )
    with pytest.raises(TypeError, match="callable executor"):
        execute_favorites_external_refresh_name_acceptance(  # type: ignore[arg-type]
            plan,
            lifecycle,
            object(),
        )


def test_orchestration_symbols_are_package_exports() -> None:
    expected = {
        "FavoritesExternalRefreshNameAcceptanceResult",
        "execute_favorites_external_refresh_name_acceptance",
    }
    assert expected <= set(sds200.__all__)
    for name in expected:
        assert hasattr(sds200, name)
