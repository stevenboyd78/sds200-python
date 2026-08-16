from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

import sds200
from sds200 import (
    FavoritesExternalAcceptanceError,
    FavoritesExternalFieldBinding,
    FavoritesExternalFieldObservation,
    FavoritesExternalFieldObservationState,
    FavoritesExternalFieldOwnership,
    FavoritesExternalNameAcceptanceDurableResult,
    FavoritesExternalNameAcceptancePersistenceError,
    FavoritesExternalNameAcceptanceProvenanceError,
    FavoritesExternalObservationEvidence,
    FavoritesExternalRecordIdentity,
    FavoritesExternalRecordObservation,
    FavoritesExternalRecordObservationState,
    FavoritesExternalRecordState,
    FavoritesExternalSourceIdentity,
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
    bind_favorites_external_record,
    execute_favorites_external_name_acceptance_durably,
    load_favorites_external_provenance,
    plan_favorites_external_name_acceptance,
    save_favorites_external_provenance,
    select_favorites_record_target,
    serialize_favorites_external_provenance,
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


def _source() -> FavoritesExternalSourceIdentity:
    return FavoritesExternalSourceIdentity(
        provider="synthetic-provider",
        dataset="synthetic-dataset",
    )


def _identity(record_id: str = "channel-1") -> FavoritesExternalRecordIdentity:
    return FavoritesExternalRecordIdentity(
        source=_source(),
        record_id=record_id,
    )


def _evidence(revision: str) -> FavoritesExternalObservationEvidence:
    return FavoritesExternalObservationEvidence(
        observed_at=datetime(2026, 8, 15, tzinfo=UTC),
        revision=revision,
    )


def _value(name: str, value: str) -> FavoritesExternalFieldObservation:
    return FavoritesExternalFieldObservation(
        name=name,
        state=FavoritesExternalFieldObservationState.VALUE,
        value=value,
    )


def _observation(
    *,
    name: str,
    revision: str,
) -> FavoritesExternalRecordObservation:
    return FavoritesExternalRecordObservation(
        identity=_identity(),
        evidence=_evidence(revision),
        state=FavoritesExternalRecordObservationState.ACTIVE,
        fields=(
            _value("name", name),
            _value("frequency", "155100000"),
        ),
    )


def _inputs() -> tuple[
    FavoritesStorageSnapshot,
    FavoritesExternalRecordState,
    FavoritesExternalRecordObservation,
]:
    snapshot = _snapshot()
    target = select_favorites_record_target(
        snapshot,
        5,
        document_index=0,
    )
    accepted = _observation(
        name=target.record.fields[2],
        revision="accepted-r1",
    )
    state = bind_favorites_external_record(
        target,
        accepted,
        (
            FavoritesExternalFieldBinding(
                name="name",
                field_index=2,
                ownership=FavoritesExternalFieldOwnership.EXTERNAL,
            ),
        ),
    )
    updated = _observation(
        name="Provider Channel",
        revision="provider-r2",
    )
    return snapshot, state, updated


def _other_state(snapshot: FavoritesStorageSnapshot) -> FavoritesExternalRecordState:
    return FavoritesExternalRecordState(
        target=select_favorites_record_target(
            snapshot,
            6,
            document_index=0,
        ),
        fields=(),
        external_identity=_identity("channel-2"),
        last_observation=_evidence("other-r1"),
    )


def _path(tmp_path: Path) -> Path:
    return tmp_path / "state" / "favorites-external-provenance.json"


class _StaticStorageSource:
    def __init__(self, snapshot: FavoritesStorageSnapshot) -> None:
        self.snapshot = snapshot
        self.read_count = 0

    def read_snapshot(self) -> FavoritesStorageSnapshot:
        self.read_count += 1
        return self.snapshot


def test_durable_name_acceptance_preserves_collection_order_and_publishes(
    tmp_path: Path,
) -> None:
    snapshot, state, updated = _inputs()
    plan = plan_favorites_external_name_acceptance(snapshot, state, updated)
    other = _other_state(snapshot)
    path = _path(tmp_path)
    save_favorites_external_provenance((other, state), path)
    source = _StaticStorageSource(plan.write_plan.intended_snapshot)
    calls: list[object] = []
    backend_result = object()

    def executor(write_plan: object) -> object:
        calls.append(write_plan)
        return backend_result

    result = execute_favorites_external_name_acceptance_durably(
        plan,
        executor,
        source,
        path,
    )

    assert isinstance(result, FavoritesExternalNameAcceptanceDurableResult)
    assert result.execution.plan is plan
    assert result.execution.execution_result is backend_result
    assert result.baseline_provenance_records == (other, state)
    assert result.provenance_records == (other, plan.intended_state)
    assert result.provenance_path == path
    assert calls == [plan.write_plan]
    assert source.read_count == 1
    assert load_favorites_external_provenance(
        path,
        plan.write_plan.intended_snapshot,
    ) == result.provenance_records


def test_durable_name_acceptance_requires_existing_provenance_before_execution(
    tmp_path: Path,
) -> None:
    snapshot, state, updated = _inputs()
    plan = plan_favorites_external_name_acceptance(snapshot, state, updated)
    source = _StaticStorageSource(plan.write_plan.intended_snapshot)
    calls: list[object] = []

    with pytest.raises(
        FavoritesExternalNameAcceptanceProvenanceError,
        match="requires existing persisted provenance",
    ):
        execute_favorites_external_name_acceptance_durably(
            plan,
            lambda write_plan: calls.append(write_plan),
            source,
            _path(tmp_path),
        )

    assert calls == []
    assert source.read_count == 0


def test_durable_name_acceptance_requires_exact_historical_baseline_before_execution(
    tmp_path: Path,
) -> None:
    snapshot, state, updated = _inputs()
    plan = plan_favorites_external_name_acceptance(snapshot, state, updated)
    stale_history = replace(
        state,
        last_observation=_evidence("different-history"),
    )
    path = _path(tmp_path)
    save_favorites_external_provenance((stale_history,), path)
    source = _StaticStorageSource(plan.write_plan.intended_snapshot)
    calls: list[object] = []

    with pytest.raises(
        FavoritesExternalNameAcceptanceProvenanceError,
        match="exact persisted baseline provenance state once",
    ):
        execute_favorites_external_name_acceptance_durably(
            plan,
            lambda write_plan: calls.append(write_plan),
            source,
            path,
        )

    assert calls == []
    assert source.read_count == 0
    assert load_favorites_external_provenance(path, snapshot) == (stale_history,)


def test_durable_name_acceptance_executor_failure_does_not_advance_provenance(
    tmp_path: Path,
) -> None:
    snapshot, state, updated = _inputs()
    plan = plan_favorites_external_name_acceptance(snapshot, state, updated)
    path = _path(tmp_path)
    save_favorites_external_provenance((state,), path)
    source = _StaticStorageSource(plan.write_plan.intended_snapshot)

    class ExecutorFailure(RuntimeError):
        pass

    def executor(_: object) -> object:
        raise ExecutorFailure("synthetic executor failure")

    with pytest.raises(ExecutorFailure, match="synthetic executor failure"):
        execute_favorites_external_name_acceptance_durably(
            plan,
            executor,
            source,
            path,
        )

    assert source.read_count == 0
    assert load_favorites_external_provenance(path, snapshot) == (state,)


def test_durable_name_acceptance_readback_failure_does_not_advance_provenance(
    tmp_path: Path,
) -> None:
    snapshot, state, updated = _inputs()
    plan = plan_favorites_external_name_acceptance(snapshot, state, updated)
    path = _path(tmp_path)
    save_favorites_external_provenance((state,), path)
    source = _StaticStorageSource(snapshot)

    with pytest.raises(
        FavoritesExternalAcceptanceError,
        match="does not exactly match the intended snapshot",
    ):
        execute_favorites_external_name_acceptance_durably(
            plan,
            lambda _: object(),
            source,
            path,
        )

    assert source.read_count == 1
    assert load_favorites_external_provenance(path, snapshot) == (state,)


def test_durable_name_acceptance_reports_post_write_provenance_race_without_overwrite(
    tmp_path: Path,
) -> None:
    snapshot, state, updated = _inputs()
    plan = plan_favorites_external_name_acceptance(snapshot, state, updated)
    path = _path(tmp_path)
    save_favorites_external_provenance((state,), path)
    raced = replace(
        state,
        last_observation=_evidence("concurrent-r9"),
    )

    class RacingStorageSource:
        def __init__(self) -> None:
            self.read_count = 0

        def read_snapshot(self) -> FavoritesStorageSnapshot:
            self.read_count += 1
            save_favorites_external_provenance((raced,), path)
            return plan.write_plan.intended_snapshot

    source = RacingStorageSource()

    with pytest.raises(
        FavoritesExternalNameAcceptancePersistenceError,
        match="storage was verified, but provenance persistence did not complete",
    ):
        execute_favorites_external_name_acceptance_durably(
            plan,
            lambda _: object(),
            source,
            path,
        )

    assert source.read_count == 1
    assert path.read_bytes() == serialize_favorites_external_provenance((raced,))


def test_durable_name_acceptance_exact_complete_baseline_guard_precedes_execution(
    tmp_path: Path,
) -> None:
    snapshot, state, updated = _inputs()
    plan = plan_favorites_external_name_acceptance(snapshot, state, updated)
    other = _other_state(snapshot)
    path = _path(tmp_path)
    save_favorites_external_provenance((other, state), path)
    source = _StaticStorageSource(plan.write_plan.intended_snapshot)
    calls: list[object] = []

    with pytest.raises(
        FavoritesExternalNameAcceptanceProvenanceError,
        match="exact expected baseline collection",
    ):
        execute_favorites_external_name_acceptance_durably(
            plan,
            lambda write_plan: calls.append(write_plan),
            source,
            path,
            expected_baseline_provenance_records=(state,),
        )

    assert calls == []
    assert source.read_count == 0
    assert load_favorites_external_provenance(path, snapshot) == (other, state)


@pytest.mark.parametrize(
    "expected",
    [
        [],
        (object(),),
    ],
)
def test_durable_name_acceptance_exact_complete_baseline_guard_requires_types(
    tmp_path: Path,
    expected: object,
) -> None:
    snapshot, state, updated = _inputs()
    plan = plan_favorites_external_name_acceptance(snapshot, state, updated)
    path = _path(tmp_path)
    save_favorites_external_provenance((state,), path)
    source = _StaticStorageSource(plan.write_plan.intended_snapshot)

    with pytest.raises(TypeError, match="expected baseline provenance"):
        execute_favorites_external_name_acceptance_durably(
            plan,
            lambda _: object(),
            source,
            path,
            expected_baseline_provenance_records=expected,  # type: ignore[arg-type]
        )

    assert source.read_count == 0


def test_durable_name_acceptance_result_rejects_inconsistent_collection_transition(
    tmp_path: Path,
) -> None:
    snapshot, state, updated = _inputs()
    plan = plan_favorites_external_name_acceptance(snapshot, state, updated)
    other = _other_state(snapshot)
    path = _path(tmp_path)
    save_favorites_external_provenance((other, state), path)

    result = execute_favorites_external_name_acceptance_durably(
        plan,
        lambda _: object(),
        _StaticStorageSource(plan.write_plan.intended_snapshot),
        path,
    )

    with pytest.raises(
        ValueError,
        match="complete baseline collection with exactly one accepted-state replacement",
    ):
        FavoritesExternalNameAcceptanceDurableResult(
            execution=result.execution,
            baseline_provenance_records=result.baseline_provenance_records,
            provenance_records=(result.execution.accepted_state,),
            provenance_path=result.provenance_path,
        )


def test_durable_name_acceptance_result_is_immutable(tmp_path: Path) -> None:
    snapshot, state, updated = _inputs()
    plan = plan_favorites_external_name_acceptance(snapshot, state, updated)
    path = _path(tmp_path)
    save_favorites_external_provenance((state,), path)

    result = execute_favorites_external_name_acceptance_durably(
        plan,
        lambda _: object(),
        _StaticStorageSource(plan.write_plan.intended_snapshot),
        path,
    )

    with pytest.raises(FrozenInstanceError):
        result.provenance_path = tmp_path / "other.json"  # type: ignore[misc]


def test_durable_name_acceptance_public_api_is_exported() -> None:
    expected = {
        "FavoritesExternalNameAcceptanceDurableResult",
        "FavoritesExternalNameAcceptancePersistenceError",
        "FavoritesExternalNameAcceptanceProvenanceError",
        "execute_favorites_external_name_acceptance_durably",
    }
    assert expected <= set(sds200.__all__)
    for name in expected:
        assert hasattr(sds200, name)
