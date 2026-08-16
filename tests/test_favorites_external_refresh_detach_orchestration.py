from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

import sds200
import sds200.favorites_external_provenance_detach as detach_persistence
from sds200 import (
    FavoritesExternalFieldBinding,
    FavoritesExternalFieldObservation,
    FavoritesExternalFieldObservationState,
    FavoritesExternalFieldOwnership,
    FavoritesExternalObservationEvidence,
    FavoritesExternalProvenanceLifecycle,
    FavoritesExternalProvenanceLifecycleAdvanceError,
    FavoritesExternalRecordIdentity,
    FavoritesExternalRecordObservation,
    FavoritesExternalRecordState,
    FavoritesExternalRefreshDetachDurableResult,
    FavoritesExternalRefreshDetachPersistenceError,
    FavoritesExternalRefreshDetachPlan,
    FavoritesExternalRefreshDetachProvenanceError,
    FavoritesExternalRefreshDetachResult,
    FavoritesExternalRefreshDetachScope,
    FavoritesExternalRefreshSession,
    FavoritesExternalSourceIdentity,
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
    bind_favorites_external_record,
    execute_favorites_external_refresh_detach,
    execute_favorites_external_refresh_detach_durably,
    load_favorites_external_provenance,
    plan_favorites_external_refresh_detach,
    save_favorites_external_provenance,
    save_favorites_external_provenance_if_current,
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
    name: str,
    *,
    revision: str,
    identity: FavoritesExternalRecordIdentity | None = None,
) -> FavoritesExternalRecordObservation:
    return FavoritesExternalRecordObservation(
        identity=identity or _identity(),
        evidence=FavoritesExternalObservationEvidence(
            observed_at=datetime(2026, 8, 16, 7, 0, tzinfo=UTC),
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


def _linked_state(
    snapshot: FavoritesStorageSnapshot,
    *,
    record_index: int = 5,
    record_id: str = "channel-101",
) -> FavoritesExternalRecordState:
    target = select_favorites_record_target(
        snapshot,
        record_index,
        document_index=0,
    )
    return bind_favorites_external_record(
        target,
        _observation(
            target.record.fields[2],
            revision=f"{record_id}-accepted",
            identity=_identity(record_id),
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


def _prepared(
    tmp_path: Path,
    *,
    include_other: bool = False,
    scope: FavoritesExternalRefreshDetachScope = (
        FavoritesExternalRefreshDetachScope.FIELD
    ),
) -> tuple[
    FavoritesExternalProvenanceLifecycle,
    _MutableStorageSource,
    _CountingSource,
    FavoritesExternalRefreshDetachPlan,
    Path,
    FavoritesExternalRecordState | None,
]:
    snapshot = _snapshot()
    state = _linked_state(snapshot)
    other = (
        _linked_state(
            snapshot,
            record_index=6,
            record_id="channel-202",
        )
        if include_other
        else None
    )
    records = (other, state) if other is not None else (state,)
    path = tmp_path / "state" / "favorites-external-provenance.json"
    save_favorites_external_provenance(records, path)

    storage = _MutableStorageSource(snapshot)
    lifecycle = FavoritesExternalProvenanceLifecycle(storage, path)
    lifecycle.start()
    source = _CountingSource(
        (_observation("Dispatch Updated", revision="provider-r2"),)
    )
    refresh = FavoritesExternalRefreshSession(lifecycle, source).refresh()
    selected = next(
        preview
        for preview in refresh.preview.records
        if preview.external_identity == state.external_identity
    )
    plan = plan_favorites_external_refresh_detach(
        refresh,
        selected,
        scope,
        field_name=(
            "name"
            if scope is FavoritesExternalRefreshDetachScope.FIELD
            else None
        ),
    )
    return lifecycle, storage, source, plan, path, other


@pytest.mark.parametrize(
    "scope",
    [
        FavoritesExternalRefreshDetachScope.FIELD,
        FavoritesExternalRefreshDetachScope.RECORD,
    ],
)
def test_orchestration_publishes_and_advances_without_favorites_mutation(
    tmp_path: Path,
    scope: FavoritesExternalRefreshDetachScope,
) -> None:
    lifecycle, storage, source, plan, path, _ = _prepared(
        tmp_path,
        scope=scope,
    )
    baseline_snapshot = plan.refresh_result.lifecycle_snapshot.favorites_snapshot
    assert baseline_snapshot is not None

    result = execute_favorites_external_refresh_detach(plan, lifecycle)

    assert isinstance(result, FavoritesExternalRefreshDetachResult)
    assert result.plan is plan
    assert result.durable_result.plan is plan
    assert result.lifecycle_snapshot == lifecycle.snapshot()
    assert result.lifecycle_snapshot.favorites_snapshot is baseline_snapshot
    assert result.lifecycle_snapshot.provenance_records is (
        result.durable_result.provenance_records
    )
    assert result.durable_result.baseline_provenance_records == (
        plan.baseline_state,
    )
    assert result.durable_result.provenance_records == (plan.intended_state,)
    assert load_favorites_external_provenance(
        path,
        baseline_snapshot,
    ) == result.durable_result.provenance_records
    assert storage.read_calls == 1
    assert source.read_calls == 1


def test_complete_collection_order_and_unrelated_record_are_preserved(
    tmp_path: Path,
) -> None:
    lifecycle, _, _, plan, path, other = _prepared(
        tmp_path,
        include_other=True,
    )
    assert other is not None
    snapshot = plan.refresh_result.lifecycle_snapshot.favorites_snapshot
    assert snapshot is not None

    result = execute_favorites_external_refresh_detach(plan, lifecycle)

    assert result.durable_result.baseline_provenance_records == (
        other,
        plan.baseline_state,
    )
    assert result.durable_result.provenance_records == (
        other,
        plan.intended_state,
    )
    assert load_favorites_external_provenance(path, snapshot) == (
        other,
        plan.intended_state,
    )


def test_stale_refresh_is_rejected_before_second_publication(tmp_path: Path) -> None:
    lifecycle, _, _, plan, path, _ = _prepared(tmp_path)
    first = execute_favorites_external_refresh_detach(plan, lifecycle)
    before = lifecycle.snapshot()
    snapshot = before.favorites_snapshot
    assert snapshot is not None

    with pytest.raises(
        FavoritesExternalProvenanceLifecycleAdvanceError,
        match="selected refresh baseline",
    ):
        execute_favorites_external_refresh_detach(plan, lifecycle)

    assert lifecycle.snapshot() == before
    assert load_favorites_external_provenance(
        path,
        snapshot,
    ) == first.durable_result.provenance_records


def test_changed_complete_persisted_collection_is_rejected(
    tmp_path: Path,
) -> None:
    lifecycle, storage, _, plan, path, _ = _prepared(tmp_path)
    snapshot = plan.refresh_result.lifecycle_snapshot.favorites_snapshot
    assert snapshot is not None
    other = _linked_state(
        snapshot,
        record_index=6,
        record_id="channel-202",
    )
    save_favorites_external_provenance((other, plan.baseline_state), path)
    before = lifecycle.snapshot()

    with pytest.raises(
        FavoritesExternalRefreshDetachProvenanceError,
        match="exact expected baseline collection",
    ):
        execute_favorites_external_refresh_detach(plan, lifecycle)

    assert lifecycle.snapshot() == before
    assert storage.read_calls == 1
    assert load_favorites_external_provenance(
        path,
        snapshot,
    ) == (other, plan.baseline_state)


def test_foreign_lifecycle_is_rejected_before_publication(tmp_path: Path) -> None:
    _, _, _, plan, _, _ = _prepared(tmp_path)
    snapshot = plan.refresh_result.lifecycle_snapshot.favorites_snapshot
    assert snapshot is not None
    other_path = tmp_path / "other" / "favorites-external-provenance.json"
    save_favorites_external_provenance((plan.baseline_state,), other_path)
    other_storage = _MutableStorageSource(snapshot)
    other_lifecycle = FavoritesExternalProvenanceLifecycle(
        other_storage,
        other_path,
    )
    other_lifecycle.start()

    with pytest.raises(
        FavoritesExternalProvenanceLifecycleAdvanceError,
        match="selected refresh baseline",
    ):
        execute_favorites_external_refresh_detach(plan, other_lifecycle)

    assert other_storage.read_calls == 1
    assert load_favorites_external_provenance(
        other_path,
        snapshot,
    ) == (plan.baseline_state,)


def test_conditional_publication_race_fails_without_lifecycle_advancement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle, _, _, plan, path, _ = _prepared(tmp_path)
    snapshot = plan.refresh_result.lifecycle_snapshot.favorites_snapshot
    assert snapshot is not None
    other = _linked_state(
        snapshot,
        record_index=6,
        record_id="channel-202",
    )
    original = save_favorites_external_provenance_if_current
    before = lifecycle.snapshot()

    def racing_save(
        records: tuple[FavoritesExternalRecordState, ...],
        provenance_path: str | Path,
        *,
        expected_current_records: tuple[FavoritesExternalRecordState, ...] | None,
        max_bytes: int,
        max_records: int,
        max_fields_per_record: int,
    ) -> Path:
        save_favorites_external_provenance(
            (other, plan.baseline_state),
            provenance_path,
        )
        return original(
            records,
            provenance_path,
            expected_current_records=expected_current_records,
            max_bytes=max_bytes,
            max_records=max_records,
            max_fields_per_record=max_fields_per_record,
        )

    monkeypatch.setattr(
        detach_persistence,
        "save_favorites_external_provenance_if_current",
        racing_save,
    )

    with pytest.raises(
        FavoritesExternalRefreshDetachPersistenceError,
        match="persistence did not complete",
    ):
        execute_favorites_external_refresh_detach(plan, lifecycle)

    assert lifecycle.snapshot() == before
    assert load_favorites_external_provenance(
        path,
        snapshot,
    ) == (other, plan.baseline_state)


def test_direct_durable_detach_can_be_adopted_idempotently(tmp_path: Path) -> None:
    lifecycle, _, _, plan, path, _ = _prepared(tmp_path)
    baseline = lifecycle.snapshot()
    assert baseline.provenance_records is not None

    durable = execute_favorites_external_refresh_detach_durably(
        plan,
        path,
        expected_baseline_provenance_records=baseline.provenance_records,
    )

    assert lifecycle.snapshot() == baseline
    advanced = lifecycle.advance_after_refresh_detach(durable)
    assert advanced.favorites_snapshot is baseline.favorites_snapshot
    assert advanced.provenance_records is durable.provenance_records
    assert lifecycle.advance_after_refresh_detach(durable) == advanced


def test_direct_durable_detach_rejects_foreign_path(tmp_path: Path) -> None:
    lifecycle, _, _, plan, _, _ = _prepared(tmp_path)
    snapshot = plan.refresh_result.lifecycle_snapshot.favorites_snapshot
    assert snapshot is not None
    foreign_path = tmp_path / "foreign" / "favorites-external-provenance.json"
    save_favorites_external_provenance((plan.baseline_state,), foreign_path)

    with pytest.raises(
        FavoritesExternalRefreshDetachProvenanceError,
        match="path does not match",
    ):
        execute_favorites_external_refresh_detach_durably(
            plan,
            foreign_path,
        )

    assert load_favorites_external_provenance(
        foreign_path,
        snapshot,
    ) == (plan.baseline_state,)
    assert lifecycle.snapshot() == plan.refresh_result.lifecycle_snapshot


def test_direct_durable_detach_requires_complete_refresh_baseline(
    tmp_path: Path,
) -> None:
    lifecycle, _, _, plan, path, _ = _prepared(tmp_path)
    snapshot = plan.refresh_result.lifecycle_snapshot.favorites_snapshot
    assert snapshot is not None
    other = _linked_state(
        snapshot,
        record_index=6,
        record_id="channel-202",
    )
    save_favorites_external_provenance((other, plan.baseline_state), path)

    with pytest.raises(
        FavoritesExternalRefreshDetachProvenanceError,
        match="selected refresh baseline collection",
    ):
        execute_favorites_external_refresh_detach_durably(
            plan,
            path,
        )

    assert load_favorites_external_provenance(
        path,
        snapshot,
    ) == (other, plan.baseline_state)
    assert lifecycle.snapshot() == plan.refresh_result.lifecycle_snapshot


def test_same_durable_result_idempotence_requires_exact_resulting_state(
    tmp_path: Path,
) -> None:
    lifecycle, _, _, plan, path, _ = _prepared(tmp_path)
    baseline = lifecycle.snapshot()
    assert baseline.provenance_records is not None
    durable = execute_favorites_external_refresh_detach_durably(
        plan,
        path,
        expected_baseline_provenance_records=baseline.provenance_records,
    )
    lifecycle.advance_after_refresh_detach(durable)

    object.__setattr__(
        lifecycle,
        "_provenance_records",
        durable.baseline_provenance_records,
    )

    with pytest.raises(
        FavoritesExternalProvenanceLifecycleAdvanceError,
        match="no longer matches",
    ):
        lifecycle.advance_after_refresh_detach(durable)


def test_missing_persisted_provenance_is_rejected(tmp_path: Path) -> None:
    lifecycle, _, _, plan, path, _ = _prepared(tmp_path)
    path.unlink()
    before = lifecycle.snapshot()

    with pytest.raises(
        FavoritesExternalRefreshDetachProvenanceError,
        match="existing persisted provenance",
    ):
        execute_favorites_external_refresh_detach(plan, lifecycle)

    assert lifecycle.snapshot() == before


def test_closed_lifecycle_is_rejected_without_publication(tmp_path: Path) -> None:
    lifecycle, _, _, plan, path, _ = _prepared(tmp_path)
    snapshot = plan.refresh_result.lifecycle_snapshot.favorites_snapshot
    assert snapshot is not None
    lifecycle.close()

    with pytest.raises(RuntimeError, match="must be active"):
        execute_favorites_external_refresh_detach(plan, lifecycle)

    assert load_favorites_external_provenance(
        path,
        snapshot,
    ) == (plan.baseline_state,)


def test_orchestration_result_rejects_substituted_relationships(
    tmp_path: Path,
) -> None:
    lifecycle, _, _, plan, _, _ = _prepared(tmp_path)
    result = execute_favorites_external_refresh_detach(plan, lifecycle)
    mismatched = replace(
        result.lifecycle_snapshot,
        provenance_records=result.durable_result.baseline_provenance_records,
    )

    with pytest.raises(ValueError, match="lifecycle provenance"):
        FavoritesExternalRefreshDetachResult(
            plan=result.plan,
            durable_result=result.durable_result,
            lifecycle_snapshot=mismatched,
        )


def test_durable_result_rejects_substituted_provenance(tmp_path: Path) -> None:
    lifecycle, _, _, plan, path, _ = _prepared(tmp_path)
    baseline = lifecycle.snapshot()
    assert baseline.provenance_records is not None
    durable = execute_favorites_external_refresh_detach_durably(
        plan,
        path,
        expected_baseline_provenance_records=baseline.provenance_records,
    )

    with pytest.raises(ValueError, match="one detached-state replacement"):
        FavoritesExternalRefreshDetachDurableResult(
            plan=durable.plan,
            baseline_provenance_records=durable.baseline_provenance_records,
            provenance_records=durable.baseline_provenance_records,
            provenance_path=durable.provenance_path,
        )


def test_detach_results_are_frozen_and_slotted(tmp_path: Path) -> None:
    lifecycle, _, _, plan, _, _ = _prepared(tmp_path)
    result = execute_favorites_external_refresh_detach(plan, lifecycle)

    assert not hasattr(result, "__dict__")
    assert not hasattr(result.durable_result, "__dict__")
    with pytest.raises(FrozenInstanceError):
        result.plan = result.plan  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.durable_result.plan = result.plan  # type: ignore[misc]


def test_detach_execution_requires_public_types(tmp_path: Path) -> None:
    lifecycle, _, _, plan, path, _ = _prepared(tmp_path)

    with pytest.raises(TypeError, match="FavoritesExternalRefreshDetachPlan"):
        execute_favorites_external_refresh_detach_durably(  # type: ignore[arg-type]
            object(),
            path,
        )
    with pytest.raises(TypeError, match="FavoritesExternalRefreshDetachPlan"):
        execute_favorites_external_refresh_detach(  # type: ignore[arg-type]
            object(),
            lifecycle,
        )
    with pytest.raises(TypeError, match="ProvenanceLifecycle"):
        execute_favorites_external_refresh_detach(  # type: ignore[arg-type]
            plan,
            object(),
        )
    with pytest.raises(TypeError, match="DurableResult"):
        lifecycle.advance_after_refresh_detach(object())  # type: ignore[arg-type]


def test_detach_orchestration_symbols_are_package_exports() -> None:
    expected = {
        "FavoritesExternalRefreshDetachDurableResult",
        "FavoritesExternalRefreshDetachPersistenceError",
        "FavoritesExternalRefreshDetachProvenanceError",
        "FavoritesExternalRefreshDetachResult",
        "execute_favorites_external_refresh_detach",
        "execute_favorites_external_refresh_detach_durably",
    }
    assert expected <= set(sds200.__all__)
    for name in expected:
        assert hasattr(sds200, name)
