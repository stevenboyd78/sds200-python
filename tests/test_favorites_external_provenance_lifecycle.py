from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

import sds200
from sds200 import (
    FAVORITES_EXTERNAL_PROVENANCE_FILENAME,
    FavoritesExternalFieldBinding,
    FavoritesExternalFieldObservation,
    FavoritesExternalFieldObservationState,
    FavoritesExternalFieldOwnership,
    FavoritesExternalNameAcceptanceDurableResult,
    FavoritesExternalObservationEvidence,
    FavoritesExternalProvenanceError,
    FavoritesExternalProvenanceLifecycle,
    FavoritesExternalProvenanceLifecycleAdvanceError,
    FavoritesExternalProvenanceLifecycleSnapshot,
    FavoritesExternalProvenanceLifecycleState,
    FavoritesExternalRecordIdentity,
    FavoritesExternalRecordObservation,
    FavoritesExternalRecordState,
    FavoritesExternalSourceIdentity,
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
    bind_favorites_external_record,
    execute_favorites_external_name_acceptance_durably,
    plan_favorites_external_name_acceptance,
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


def _linked_state(snapshot: FavoritesStorageSnapshot) -> FavoritesExternalRecordState:
    target = select_favorites_record_target(snapshot, 5, document_index=0)
    observation = FavoritesExternalRecordObservation(
        identity=FavoritesExternalRecordIdentity(
            source=FavoritesExternalSourceIdentity(
                provider="synthetic-provider",
                dataset="metro",
            ),
            record_id="channel-101",
        ),
        evidence=FavoritesExternalObservationEvidence(
            observed_at=datetime(2026, 8, 15, 3, 0, tzinfo=UTC),
            revision="accepted-r1",
        ),
        fields=(
            FavoritesExternalFieldObservation(
                name="name",
                state=FavoritesExternalFieldObservationState.VALUE,
                value=target.record.fields[2],
            ),
        ),
    )
    return bind_favorites_external_record(
        target,
        observation,
        (
            FavoritesExternalFieldBinding(
                name="name",
                field_index=2,
                ownership=FavoritesExternalFieldOwnership.EXTERNAL,
            ),
        ),
    )


def _state_path(tmp_path: Path) -> Path:
    return tmp_path / "state" / "sdsctl" / FAVORITES_EXTERNAL_PROVENANCE_FILENAME


class FakeStorageSource:
    def __init__(
        self,
        snapshot: object,
        *,
        error: BaseException | None = None,
    ) -> None:
        self.value = snapshot
        self.error = error
        self.read_calls = 0

    def read_snapshot(self) -> object:
        self.read_calls += 1
        if self.error is not None:
            raise self.error
        return self.value


def _durable_result(
    tmp_path: Path,
    *,
    baseline_records: tuple[FavoritesExternalRecordState, ...] | None = None,
    baseline_state: FavoritesExternalRecordState | None = None,
    path: Path | None = None,
) -> tuple[
    FavoritesExternalNameAcceptanceDurableResult,
    FakeStorageSource,
]:
    baseline = _snapshot()
    state = baseline_state or _linked_state(baseline)
    updated = FavoritesExternalRecordObservation(
        identity=state.external_identity,
        evidence=FavoritesExternalObservationEvidence(
            observed_at=datetime(2026, 8, 15, 4, 0, tzinfo=UTC),
            revision="provider-r2",
        ),
        fields=(
            FavoritesExternalFieldObservation(
                name="name",
                state=FavoritesExternalFieldObservationState.VALUE,
                value="Provider Channel",
            ),
        ),
    )
    plan = plan_favorites_external_name_acceptance(baseline, state, updated)
    provenance_path = path or _state_path(tmp_path)
    save_favorites_external_provenance(
        baseline_records if baseline_records is not None else (state,),
        provenance_path,
    )
    source = FakeStorageSource(plan.write_plan.intended_snapshot)
    result = execute_favorites_external_name_acceptance_durably(
        plan,
        lambda write_plan: write_plan,
        source,
        provenance_path,
    )
    return result, source


def test_lifecycle_starts_idle_with_no_restoration_evidence(tmp_path: Path) -> None:
    source = FakeStorageSource(_snapshot())
    path = _state_path(tmp_path)
    lifecycle = FavoritesExternalProvenanceLifecycle(source, path)

    observed = lifecycle.snapshot()

    assert observed == FavoritesExternalProvenanceLifecycleSnapshot(
        state=FavoritesExternalProvenanceLifecycleState.IDLE,
        provenance_path=path,
        favorites_snapshot=None,
        provenance_records=None,
        last_error=None,
    )
    assert observed.provenance_present is None
    assert source.read_calls == 0


def test_start_restores_missing_state_against_one_fresh_snapshot(tmp_path: Path) -> None:
    favorites_snapshot = _snapshot()
    source = FakeStorageSource(favorites_snapshot)
    lifecycle = FavoritesExternalProvenanceLifecycle(
        source,
        _state_path(tmp_path),
    )

    restored = lifecycle.start()

    assert restored.state is FavoritesExternalProvenanceLifecycleState.ACTIVE
    assert restored.favorites_snapshot == favorites_snapshot
    assert restored.favorites_snapshot is favorites_snapshot
    assert restored.provenance_records is None
    assert restored.provenance_present is False
    assert restored.last_error is None
    assert source.read_calls == 1


def test_start_preserves_present_empty_state(tmp_path: Path) -> None:
    favorites_snapshot = _snapshot()
    path = _state_path(tmp_path)
    save_favorites_external_provenance((), path)
    source = FakeStorageSource(favorites_snapshot)
    lifecycle = FavoritesExternalProvenanceLifecycle(source, path)

    restored = lifecycle.start()

    assert restored.provenance_records == ()
    assert restored.provenance_present is True
    assert restored.favorites_snapshot is favorites_snapshot
    assert source.read_calls == 1


def test_start_restores_exact_linked_state(tmp_path: Path) -> None:
    favorites_snapshot = _snapshot()
    state = _linked_state(favorites_snapshot)
    path = _state_path(tmp_path)
    save_favorites_external_provenance((state,), path)
    source = FakeStorageSource(favorites_snapshot)
    lifecycle = FavoritesExternalProvenanceLifecycle(source, path)

    restored = lifecycle.start()

    assert restored.provenance_records == (state,)
    assert restored.provenance_present is True
    assert restored.favorites_snapshot is favorites_snapshot
    assert source.read_calls == 1


def test_active_start_is_idempotent_and_does_not_reread_storage(tmp_path: Path) -> None:
    favorites_snapshot = _snapshot()
    source = FakeStorageSource(favorites_snapshot)
    path = _state_path(tmp_path)
    lifecycle = FavoritesExternalProvenanceLifecycle(source, path)

    first = lifecycle.start()
    save_favorites_external_provenance((), path)
    path.write_bytes(b"not canonical provenance")
    second = lifecycle.start()

    assert first == second
    assert source.read_calls == 1
    assert second.state is FavoritesExternalProvenanceLifecycleState.ACTIVE
    assert second.provenance_records is None


def test_stale_provenance_fails_closed_without_partial_restoration_evidence(
    tmp_path: Path,
) -> None:
    baseline = _snapshot()
    state = _linked_state(baseline)
    path = _state_path(tmp_path)
    save_favorites_external_provenance((state,), path)
    stale = FavoritesStorageSnapshot(
        catalog_bytes=baseline.catalog_bytes,
        documents=(
            FavoritesStorageDocument(
                filename="f_000002.hpd",
                content=baseline.documents[0].content,
            ),
        ),
    )
    source = FakeStorageSource(stale)
    lifecycle = FavoritesExternalProvenanceLifecycle(source, path)

    with pytest.raises(FavoritesExternalProvenanceError):
        lifecycle.start()

    failed = lifecycle.snapshot()
    assert failed.state is FavoritesExternalProvenanceLifecycleState.FAILED
    assert failed.favorites_snapshot is None
    assert failed.provenance_records is None
    assert failed.provenance_present is None
    assert failed.last_error == "FavoritesExternalProvenanceError"
    assert source.read_calls == 1


def test_changed_record_identity_fails_closed_without_partial_evidence(
    tmp_path: Path,
) -> None:
    baseline = _snapshot()
    state = _linked_state(baseline)
    path = _state_path(tmp_path)
    save_favorites_external_provenance((state,), path)
    changed = FavoritesStorageSnapshot(
        catalog_bytes=baseline.catalog_bytes,
        documents=(
            FavoritesStorageDocument(
                filename=baseline.documents[0].filename,
                content=baseline.documents[0].content.replace(
                    b"Synthetic Channel",
                    b"Synthetic Changed",
                    1,
                ),
            ),
        ),
    )
    source = FakeStorageSource(changed)
    lifecycle = FavoritesExternalProvenanceLifecycle(source, path)

    with pytest.raises(FavoritesExternalProvenanceError):
        lifecycle.start()

    failed = lifecycle.snapshot()
    assert failed.state is FavoritesExternalProvenanceLifecycleState.FAILED
    assert failed.favorites_snapshot is None
    assert failed.provenance_records is None
    assert failed.last_error == "FavoritesExternalProvenanceError"
    assert source.read_calls == 1


def test_moved_record_fails_closed_without_partial_evidence(tmp_path: Path) -> None:
    baseline = _snapshot()
    state = _linked_state(baseline)
    path = _state_path(tmp_path)
    save_favorites_external_provenance((state,), path)
    moved = FavoritesStorageSnapshot(
        catalog_bytes=baseline.catalog_bytes,
        documents=(
            FavoritesStorageDocument(
                filename="f_000002.hpd",
                content=baseline.documents[0].content,
            ),
            baseline.documents[0],
        ),
    )
    source = FakeStorageSource(moved)
    lifecycle = FavoritesExternalProvenanceLifecycle(source, path)

    with pytest.raises(FavoritesExternalProvenanceError):
        lifecycle.start()

    failed = lifecycle.snapshot()
    assert failed.state is FavoritesExternalProvenanceLifecycleState.FAILED
    assert failed.favorites_snapshot is None
    assert failed.provenance_records is None
    assert failed.last_error == "FavoritesExternalProvenanceError"
    assert source.read_calls == 1


def test_source_failure_is_redacted_in_lifecycle_state(tmp_path: Path) -> None:
    source = FakeStorageSource(
        _snapshot(),
        error=RuntimeError("secret source failure detail"),
    )
    lifecycle = FavoritesExternalProvenanceLifecycle(
        source,
        _state_path(tmp_path),
    )

    with pytest.raises(RuntimeError, match="secret source failure detail"):
        lifecycle.start()

    failed = lifecycle.snapshot()
    assert failed.state is FavoritesExternalProvenanceLifecycleState.FAILED
    assert failed.last_error == "RuntimeError"
    assert "secret source failure detail" not in repr(failed)
    assert source.read_calls == 1


def test_process_control_exception_is_reraised_and_terminally_redacted(
    tmp_path: Path,
) -> None:
    source = FakeStorageSource(_snapshot(), error=KeyboardInterrupt())
    lifecycle = FavoritesExternalProvenanceLifecycle(source, _state_path(tmp_path))

    with pytest.raises(KeyboardInterrupt):
        lifecycle.start()

    observed = lifecycle.snapshot()
    assert observed.state is FavoritesExternalProvenanceLifecycleState.FAILED
    assert observed.favorites_snapshot is None
    assert observed.provenance_records is None
    assert observed.last_error == "KeyboardInterrupt"
    assert source.read_calls == 1
    with pytest.raises(RuntimeError, match="cannot be retried"):
        lifecycle.start()
    assert source.read_calls == 1


def test_invalid_storage_snapshot_type_fails_closed(tmp_path: Path) -> None:
    source = FakeStorageSource(object())
    lifecycle = FavoritesExternalProvenanceLifecycle(
        source,
        _state_path(tmp_path),
    )

    with pytest.raises(TypeError, match="must return FavoritesStorageSnapshot"):
        lifecycle.start()

    failed = lifecycle.snapshot()
    assert failed.state is FavoritesExternalProvenanceLifecycleState.FAILED
    assert failed.last_error == "TypeError"
    assert source.read_calls == 1


def test_failed_lifecycle_cannot_be_retried(tmp_path: Path) -> None:
    source = FakeStorageSource(
        _snapshot(),
        error=RuntimeError("startup failed"),
    )
    lifecycle = FavoritesExternalProvenanceLifecycle(
        source,
        _state_path(tmp_path),
    )

    with pytest.raises(RuntimeError, match="startup failed"):
        lifecycle.start()
    with pytest.raises(RuntimeError, match="cannot be retried"):
        lifecycle.start()

    assert source.read_calls == 1


def test_close_is_idempotent_and_prevents_start(tmp_path: Path) -> None:
    source = FakeStorageSource(_snapshot())
    lifecycle = FavoritesExternalProvenanceLifecycle(
        source,
        _state_path(tmp_path),
    )

    lifecycle.close()
    lifecycle.close()

    closed = lifecycle.snapshot()
    assert closed.state is FavoritesExternalProvenanceLifecycleState.CLOSED
    assert closed.favorites_snapshot is None
    assert closed.provenance_records is None
    with pytest.raises(RuntimeError, match="closed and cannot be started"):
        lifecycle.start()
    assert source.read_calls == 0


def test_close_retains_successful_restoration_evidence(tmp_path: Path) -> None:
    favorites_snapshot = _snapshot()
    state = _linked_state(favorites_snapshot)
    path = _state_path(tmp_path)
    save_favorites_external_provenance((state,), path)
    lifecycle = FavoritesExternalProvenanceLifecycle(
        FakeStorageSource(favorites_snapshot),
        path,
    )

    active = lifecycle.start()
    lifecycle.close()
    closed = lifecycle.snapshot()

    assert active.state is FavoritesExternalProvenanceLifecycleState.ACTIVE
    assert closed.state is FavoritesExternalProvenanceLifecycleState.CLOSED
    assert closed.favorites_snapshot is favorites_snapshot
    assert closed.provenance_records == (state,)
    assert closed.last_error is None
    assert closed.provenance_present is True


def test_close_after_failure_retains_only_redacted_failure_evidence(
    tmp_path: Path,
) -> None:
    source = FakeStorageSource(
        _snapshot(),
        error=RuntimeError("secret startup failure"),
    )
    lifecycle = FavoritesExternalProvenanceLifecycle(source, _state_path(tmp_path))

    with pytest.raises(RuntimeError, match="secret startup failure"):
        lifecycle.start()
    lifecycle.close()

    closed = lifecycle.snapshot()
    assert closed.state is FavoritesExternalProvenanceLifecycleState.CLOSED
    assert closed.favorites_snapshot is None
    assert closed.provenance_records is None
    assert closed.provenance_present is None
    assert closed.last_error == "RuntimeError"
    assert "secret startup failure" not in repr(closed)
    with pytest.raises(RuntimeError, match="closed and cannot be started"):
        lifecycle.start()
    assert source.read_calls == 1


@pytest.mark.parametrize(
    ("path", "error"),
    [
        ("", ValueError),
        ("relative.json", ValueError),
        (object(), TypeError),
    ],
)
def test_lifecycle_requires_absolute_state_file(
    path: object,
    error: type[BaseException],
) -> None:
    with pytest.raises(error):
        FavoritesExternalProvenanceLifecycle(
            FakeStorageSource(_snapshot()),
            path,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("max_bytes", "max_records", "max_fields"),
    [
        (0, 4096, 256),
        (True, 4096, 256),
        (1024, 0, 256),
        (1024, True, 256),
        (1024, 4096, 0),
        (1024, 4096, True),
    ],
)
def test_lifecycle_rejects_invalid_structural_limits(
    tmp_path: Path,
    max_bytes: int,
    max_records: int,
    max_fields: int,
) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        FavoritesExternalProvenanceLifecycle(
            FakeStorageSource(_snapshot()),
            _state_path(tmp_path),
            max_bytes=max_bytes,
            max_records=max_records,
            max_fields_per_record=max_fields,
        )


def test_lifecycle_requires_storage_source(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="FavoritesStorageSource"):
        FavoritesExternalProvenanceLifecycle(
            object(),  # type: ignore[arg-type]
            _state_path(tmp_path),
        )


def test_lifecycle_snapshot_is_immutable(tmp_path: Path) -> None:
    snapshot = FavoritesExternalProvenanceLifecycle(
        FakeStorageSource(_snapshot()),
        _state_path(tmp_path),
    ).snapshot()

    with pytest.raises(FrozenInstanceError):
        snapshot.last_error = "changed"  # type: ignore[misc]


def test_advance_after_real_durable_name_acceptance_uses_retained_evidence_only(
    tmp_path: Path,
) -> None:
    baseline = _snapshot()
    state = _linked_state(baseline)
    path = _state_path(tmp_path)
    save_favorites_external_provenance((state,), path)
    lifecycle_source = FakeStorageSource(baseline)
    lifecycle = FavoritesExternalProvenanceLifecycle(lifecycle_source, path)
    before = lifecycle.start()
    result, execution_source = _durable_result(tmp_path, path=path)
    path.unlink()

    advanced = lifecycle.advance_after_name_acceptance(result)
    reapplied = lifecycle.advance_after_name_acceptance(result)

    assert before.state is FavoritesExternalProvenanceLifecycleState.ACTIVE
    assert before.favorites_snapshot is baseline
    assert before.provenance_records == (state,)
    assert advanced.state is FavoritesExternalProvenanceLifecycleState.ACTIVE
    assert advanced.favorites_snapshot == result.execution.observed_snapshot
    assert advanced.favorites_snapshot is result.execution.observed_snapshot
    assert advanced.provenance_records == result.provenance_records
    assert advanced.provenance_records is result.provenance_records
    assert advanced.last_error is None
    assert reapplied == advanced
    assert lifecycle_source.read_calls == 1
    assert execution_source.read_calls == 1
    assert not path.exists()
    assert before.favorites_snapshot is baseline
    assert before.provenance_records == (state,)

    lifecycle.close()
    closed = lifecycle.snapshot()
    assert closed.state is FavoritesExternalProvenanceLifecycleState.CLOSED
    assert closed.favorites_snapshot is result.execution.observed_snapshot
    assert closed.provenance_records is result.provenance_records


def test_advance_idempotence_requires_same_adopted_durable_result(
    tmp_path: Path,
) -> None:
    baseline = _snapshot()
    state_a = _linked_state(baseline)
    state_b = replace(
        state_a,
        last_observation=FavoritesExternalObservationEvidence(
            observed_at=datetime(2026, 8, 15, 3, 30, tzinfo=UTC),
            revision="provider-historical-r0",
        ),
    )
    path = _state_path(tmp_path)
    save_favorites_external_provenance((state_a,), path)
    lifecycle = FavoritesExternalProvenanceLifecycle(
        FakeStorageSource(baseline),
        path,
    )
    lifecycle.start()
    result_a, source_a = _durable_result(
        tmp_path,
        baseline_records=(state_a,),
        baseline_state=state_a,
        path=path,
    )
    result_b, source_b = _durable_result(
        tmp_path,
        baseline_records=(state_b,),
        baseline_state=state_b,
        path=path,
    )

    advanced = lifecycle.advance_after_name_acceptance(result_a)
    reapplied = lifecycle.advance_after_name_acceptance(result_a)
    before_rejected_result = lifecycle.snapshot()

    assert result_a is not result_b
    assert result_a.baseline_provenance_records != result_b.baseline_provenance_records
    assert result_a.execution.observed_snapshot == result_b.execution.observed_snapshot
    assert result_a.provenance_records == result_b.provenance_records
    assert reapplied == advanced
    with pytest.raises(
        FavoritesExternalProvenanceLifecycleAdvanceError,
        match="Favorites evidence",
    ):
        lifecycle.advance_after_name_acceptance(result_b)
    assert lifecycle.snapshot() == before_rejected_result
    assert source_a.read_calls == 1
    assert source_b.read_calls == 1


def test_advance_accepts_genuinely_sequential_durable_result(tmp_path: Path) -> None:
    baseline = _snapshot()
    state = _linked_state(baseline)
    path = _state_path(tmp_path)
    save_favorites_external_provenance((state,), path)
    lifecycle = FavoritesExternalProvenanceLifecycle(
        FakeStorageSource(baseline),
        path,
    )
    lifecycle.start()
    first, _ = _durable_result(tmp_path, path=path)
    first_advanced = lifecycle.advance_after_name_acceptance(first)
    next_observation = FavoritesExternalRecordObservation(
        identity=first.execution.accepted_state.external_identity,
        evidence=FavoritesExternalObservationEvidence(
            observed_at=datetime(2026, 8, 15, 5, 0, tzinfo=UTC),
            revision="provider-r3",
        ),
        fields=(
            FavoritesExternalFieldObservation(
                name="name",
                state=FavoritesExternalFieldObservationState.VALUE,
                value="Provider Channel Next",
            ),
        ),
    )
    next_plan = plan_favorites_external_name_acceptance(
        first.execution.observed_snapshot,
        first.execution.accepted_state,
        next_observation,
    )
    next_source = FakeStorageSource(next_plan.write_plan.intended_snapshot)
    second = execute_favorites_external_name_acceptance_durably(
        next_plan,
        lambda write_plan: write_plan,
        next_source,
        path,
    )

    second_advanced = lifecycle.advance_after_name_acceptance(second)
    second_reapplied = lifecycle.advance_after_name_acceptance(second)

    assert first_advanced.favorites_snapshot is first.execution.observed_snapshot
    assert second.baseline_provenance_records == first.provenance_records
    assert second_advanced.favorites_snapshot is second.execution.observed_snapshot
    assert second_advanced.provenance_records is second.provenance_records
    assert second_reapplied == second_advanced
    assert next_source.read_calls == 1


def test_advance_requires_exact_durable_result_type(tmp_path: Path) -> None:
    lifecycle = FavoritesExternalProvenanceLifecycle(
        FakeStorageSource(_snapshot()),
        _state_path(tmp_path),
    )
    before = lifecycle.snapshot()

    with pytest.raises(TypeError, match="DurableResult"):
        lifecycle.advance_after_name_acceptance(object())  # type: ignore[arg-type]

    assert lifecycle.snapshot() == before


@pytest.mark.parametrize("lifecycle_state", ["idle", "failed", "closed"])
def test_advance_rejects_non_active_lifecycle_without_changing_evidence(
    tmp_path: Path,
    lifecycle_state: str,
) -> None:
    result, _ = _durable_result(tmp_path)
    source = FakeStorageSource(_snapshot())
    lifecycle = FavoritesExternalProvenanceLifecycle(source, result.provenance_path)
    if lifecycle_state == "failed":
        source.error = RuntimeError("synthetic failure")
        with pytest.raises(RuntimeError, match="synthetic failure"):
            lifecycle.start()
    elif lifecycle_state == "closed":
        lifecycle.close()
    before = lifecycle.snapshot()

    with pytest.raises(RuntimeError, match="must be active"):
        lifecycle.advance_after_name_acceptance(result)

    assert lifecycle.snapshot() == before


def test_advance_rejects_mismatched_path_without_changing_evidence(
    tmp_path: Path,
) -> None:
    result, _ = _durable_result(tmp_path, path=_state_path(tmp_path))
    other_path = tmp_path / "other" / FAVORITES_EXTERNAL_PROVENANCE_FILENAME
    state = _linked_state(_snapshot())
    save_favorites_external_provenance((state,), other_path)
    lifecycle = FavoritesExternalProvenanceLifecycle(
        FakeStorageSource(_snapshot()),
        other_path,
    )
    before = lifecycle.start()

    with pytest.raises(FavoritesExternalProvenanceLifecycleAdvanceError, match="path"):
        lifecycle.advance_after_name_acceptance(result)

    assert lifecycle.snapshot() == before


def test_advance_rejects_stale_favorites_without_changing_evidence(
    tmp_path: Path,
) -> None:
    result, _ = _durable_result(tmp_path)
    result.provenance_path.unlink()
    stale = replace(
        _snapshot(),
        catalog_bytes=_snapshot().catalog_bytes + b"\r\n",
    )
    lifecycle = FavoritesExternalProvenanceLifecycle(
        FakeStorageSource(stale),
        result.provenance_path,
    )
    before = lifecycle.start()

    with pytest.raises(
        FavoritesExternalProvenanceLifecycleAdvanceError,
        match="Favorites evidence",
    ):
        lifecycle.advance_after_name_acceptance(result)

    assert lifecycle.snapshot() == before


@pytest.mark.parametrize("records", [None, ()])
def test_advance_rejects_missing_or_empty_provenance_without_changing_evidence(
    tmp_path: Path,
    records: tuple[()] | None,
) -> None:
    result, _ = _durable_result(tmp_path)
    if records is None:
        result.provenance_path.unlink()
    else:
        save_favorites_external_provenance(records, result.provenance_path)
    lifecycle = FavoritesExternalProvenanceLifecycle(
        FakeStorageSource(_snapshot()),
        result.provenance_path,
    )
    before = lifecycle.start()

    with pytest.raises(
        FavoritesExternalProvenanceLifecycleAdvanceError,
        match="records",
    ):
        lifecycle.advance_after_name_acceptance(result)

    assert lifecycle.snapshot() == before


def test_advance_rejects_foreign_valid_durable_provenance_collection(
    tmp_path: Path,
) -> None:
    baseline = _snapshot()
    state = _linked_state(baseline)
    other = FavoritesExternalRecordState(
        target=select_favorites_record_target(baseline, 6, document_index=0),
        fields=(),
        external_identity=FavoritesExternalRecordIdentity(
            source=state.external_identity.source,
            record_id="channel-foreign",
        ),
        last_observation=FavoritesExternalObservationEvidence(
            observed_at=datetime(2026, 8, 15, 3, 30, tzinfo=UTC),
            revision="foreign-r1",
        ),
    )
    result, _ = _durable_result(tmp_path, baseline_records=(other, state))
    save_favorites_external_provenance((state,), result.provenance_path)
    lifecycle = FavoritesExternalProvenanceLifecycle(
        FakeStorageSource(baseline),
        result.provenance_path,
    )
    before = lifecycle.start()

    with pytest.raises(
        FavoritesExternalProvenanceLifecycleAdvanceError,
        match="records",
    ):
        lifecycle.advance_after_name_acceptance(result)

    assert lifecycle.snapshot() == before


@pytest.mark.parametrize(
    ("favorites_snapshot", "provenance_records", "last_error"),
    [
        (None, (), None),
        (_snapshot(), None, "RuntimeError"),
        (_snapshot(), (), "RuntimeError"),
    ],
)
def test_closed_snapshot_rejects_inconsistent_evidence(
    tmp_path: Path,
    favorites_snapshot: FavoritesStorageSnapshot | None,
    provenance_records: tuple[FavoritesExternalRecordState, ...] | None,
    last_error: str | None,
) -> None:
    with pytest.raises(ValueError, match="Closed external Favorites"):
        FavoritesExternalProvenanceLifecycleSnapshot(
            state=FavoritesExternalProvenanceLifecycleState.CLOSED,
            provenance_path=_state_path(tmp_path),
            favorites_snapshot=favorites_snapshot,
            provenance_records=provenance_records,
            last_error=last_error,
        )


def test_snapshot_rejects_empty_failure_evidence(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="error must not be empty"):
        FavoritesExternalProvenanceLifecycleSnapshot(
            state=FavoritesExternalProvenanceLifecycleState.FAILED,
            provenance_path=_state_path(tmp_path),
            favorites_snapshot=None,
            provenance_records=None,
            last_error=" ",
        )


def test_lifecycle_public_api_is_exported() -> None:
    expected = {
        "FavoritesExternalProvenanceLifecycle",
        "FavoritesExternalProvenanceLifecycleAdvanceError",
        "FavoritesExternalProvenanceLifecycleSnapshot",
        "FavoritesExternalProvenanceLifecycleState",
    }
    assert expected <= set(sds200.__all__)
    for name in expected:
        assert getattr(sds200, name) is not None
