from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

import sds200
from sds200 import (
    FavoritesExternalAcceptanceError,
    FavoritesExternalFieldAcceptanceDurableResult,
    FavoritesExternalFieldAcceptancePersistenceError,
    FavoritesExternalFieldAcceptanceProvenanceError,
    FavoritesExternalFieldMapping,
    FavoritesExternalFieldObservation,
    FavoritesExternalFieldObservationState,
    FavoritesExternalObservationEvidence,
    FavoritesExternalRecordIdentity,
    FavoritesExternalRecordObservation,
    FavoritesExternalRecordState,
    FavoritesExternalSourceIdentity,
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
    execute_favorites_external_field_acceptance_durably,
    load_favorites_external_provenance,
    plan_favorites_external_field_acceptance,
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


def _identity(record_id: str = "frequency-101") -> FavoritesExternalRecordIdentity:
    return FavoritesExternalRecordIdentity(
        source=FavoritesExternalSourceIdentity(
            provider="synthetic-provider",
            dataset="metro",
        ),
        record_id=record_id,
    )


def _evidence(day: int) -> FavoritesExternalObservationEvidence:
    return FavoritesExternalObservationEvidence(
        observed_at=datetime(2026, 8, day, tzinfo=UTC),
    )


def _inputs(*, frequency: str = "155100000"):
    snapshot = _snapshot()
    target = select_favorites_record_target(snapshot, 5, document_index=0)
    field = FavoritesExternalFieldObservation(
        name="frequency",
        state=FavoritesExternalFieldObservationState.VALUE,
        value=frequency,
    )
    observation = FavoritesExternalRecordObservation(
        identity=_identity(),
        evidence=_evidence(16),
        fields=(field,),
    )
    state = FavoritesExternalRecordState(
        target=target,
        fields=(),
        external_identity=observation.identity,
        last_observation=_evidence(15),
    )
    mapping = FavoritesExternalFieldMapping(
        target=target,
        observation=observation,
        field=field,
        field_index=4,
        scanner_value=frequency,
    )
    return snapshot, state, plan_favorites_external_field_acceptance(
        snapshot,
        state,
        mapping,
    )


def _other(snapshot: FavoritesStorageSnapshot) -> FavoritesExternalRecordState:
    return FavoritesExternalRecordState(
        target=select_favorites_record_target(snapshot, 6, document_index=0),
        fields=(),
        external_identity=_identity("other"),
        last_observation=_evidence(14),
    )


def _path(tmp_path: Path) -> Path:
    return tmp_path / "state" / "favorites-external-provenance.json"


class _Source:
    def __init__(self, snapshot: FavoritesStorageSnapshot) -> None:
        self.snapshot = snapshot
        self.calls = 0

    def read_snapshot(self) -> FavoritesStorageSnapshot:
        self.calls += 1
        return self.snapshot


def test_changed_field_acceptance_preserves_complete_collection_and_execution(
    tmp_path: Path,
) -> None:
    snapshot, state, plan = _inputs()
    other = _other(snapshot)
    path = _path(tmp_path)
    save_favorites_external_provenance((other, state), path)
    source = _Source(plan.write_plan.intended_snapshot)
    calls: list[object] = []
    backend_result = object()

    def executor(write_plan: object) -> object:
        calls.append(write_plan)
        return backend_result

    result = execute_favorites_external_field_acceptance_durably(
        plan, executor, source, path
    )

    assert result.baseline_provenance_records == (other, state)
    assert result.provenance_records == (other, plan.intended_state)
    assert result.execution.execution_result is backend_result
    assert calls == [plan.write_plan]
    assert source.calls == 1
    assert load_favorites_external_provenance(
        path, plan.write_plan.intended_snapshot
    ) == (other, plan.intended_state)


def test_noop_executes_reads_back_and_advances_only_provenance(tmp_path: Path) -> None:
    snapshot, state, plan = _inputs(frequency="155000000")
    assert plan.write_plan.is_noop
    path = _path(tmp_path)
    save_favorites_external_provenance((state,), path)
    source = _Source(snapshot)
    calls: list[object] = []

    result = execute_favorites_external_field_acceptance_durably(
        plan, lambda write_plan: calls.append(write_plan), source, path
    )

    assert calls == [plan.write_plan]
    assert source.calls == 1
    assert result.execution.observed_snapshot == snapshot
    assert result.provenance_records == (plan.intended_state,)
    assert result.provenance_records != (state,)


def test_missing_or_wrong_historical_provenance_precedes_execution(
    tmp_path: Path,
) -> None:
    snapshot, state, plan = _inputs()
    source = _Source(plan.write_plan.intended_snapshot)
    calls: list[object] = []
    path = _path(tmp_path)

    with pytest.raises(FavoritesExternalFieldAcceptanceProvenanceError):
        execute_favorites_external_field_acceptance_durably(
            plan, lambda value: calls.append(value), source, path
        )
    assert calls == [] and source.calls == 0

    stale = replace(state, last_observation=_evidence(13))
    save_favorites_external_provenance((stale,), path)
    with pytest.raises(
        FavoritesExternalFieldAcceptanceProvenanceError,
        match="exact persisted baseline provenance state once",
    ):
        execute_favorites_external_field_acceptance_durably(
            plan, lambda value: calls.append(value), source, path
        )
    assert load_favorites_external_provenance(path, snapshot) == (stale,)
    assert calls == [] and source.calls == 0


def test_exact_complete_baseline_guard_and_types_precede_execution(
    tmp_path: Path,
) -> None:
    snapshot, state, plan = _inputs()
    other = _other(snapshot)
    path = _path(tmp_path)
    save_favorites_external_provenance((other, state), path)
    source = _Source(plan.write_plan.intended_snapshot)

    with pytest.raises(
        FavoritesExternalFieldAcceptanceProvenanceError,
        match="exact expected baseline collection",
    ):
        execute_favorites_external_field_acceptance_durably(
            plan,
            lambda _: None,
            source,
            path,
            expected_baseline_provenance_records=(state,),
        )
    for invalid in ([], (object(),)):
        with pytest.raises(TypeError, match="expected baseline provenance"):
            execute_favorites_external_field_acceptance_durably(
                plan,
                lambda _: None,
                source,
                path,
                expected_baseline_provenance_records=invalid,  # type: ignore[arg-type]
            )
    assert source.calls == 0


def test_executor_or_readback_failure_leaves_provenance_untouched(
    tmp_path: Path,
) -> None:
    snapshot, state, plan = _inputs()
    path = _path(tmp_path)
    save_favorites_external_provenance((state,), path)

    class Failure(RuntimeError):
        pass

    with pytest.raises(Failure, match="executor"):
        execute_favorites_external_field_acceptance_durably(
            plan,
            lambda _: (_ for _ in ()).throw(Failure("executor")),
            _Source(plan.write_plan.intended_snapshot),
            path,
        )
    assert load_favorites_external_provenance(path, snapshot) == (state,)

    source = _Source(snapshot)
    with pytest.raises(FavoritesExternalAcceptanceError, match="post-write storage"):
        execute_favorites_external_field_acceptance_durably(
            plan, lambda _: None, source, path
        )
    assert source.calls == 1
    assert load_favorites_external_provenance(path, snapshot) == (state,)


def test_conditional_publication_race_is_redacted_and_not_overwritten(
    tmp_path: Path,
) -> None:
    snapshot, state, plan = _inputs()
    path = _path(tmp_path)
    save_favorites_external_provenance((state,), path)
    raced = replace(state, last_observation=_evidence(12))

    class RacingSource:
        def read_snapshot(self) -> FavoritesStorageSnapshot:
            save_favorites_external_provenance((raced,), path)
            return plan.write_plan.intended_snapshot

    with pytest.raises(
        FavoritesExternalFieldAcceptancePersistenceError,
        match="storage was verified, but provenance persistence did not complete",
    ):
        execute_favorites_external_field_acceptance_durably(
            plan, lambda _: None, RacingSource(), path
        )
    assert path.read_bytes() == serialize_favorites_external_provenance((raced,))


def test_result_invariants_slots_immutability_and_exports(tmp_path: Path) -> None:
    _, state, plan = _inputs()
    path = _path(tmp_path)
    save_favorites_external_provenance((state,), path)
    result = execute_favorites_external_field_acceptance_durably(
        plan, lambda _: None, _Source(plan.write_plan.intended_snapshot), path
    )

    with pytest.raises(FrozenInstanceError):
        result.provenance_path = tmp_path / "other"  # type: ignore[misc]
    with pytest.raises((AttributeError, TypeError)):
        result.extra = None  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="complete baseline collection"):
        FavoritesExternalFieldAcceptanceDurableResult(
            execution=result.execution,
            baseline_provenance_records=result.baseline_provenance_records,
            provenance_records=(),
            provenance_path=result.provenance_path,
        )

    expected = {
        "FavoritesExternalFieldAcceptanceDurableResult",
        "FavoritesExternalFieldAcceptancePersistenceError",
        "FavoritesExternalFieldAcceptanceProvenanceError",
        "execute_favorites_external_field_acceptance_durably",
    }
    assert expected <= set(sds200.__all__)
    assert all(hasattr(sds200, name) for name in expected)
