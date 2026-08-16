from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

import sds200
from sds200 import (
    FavoritesExternalAcceptanceError,
    FavoritesExternalChangeKind,
    FavoritesExternalFieldAcceptanceExecutionResult,
    FavoritesExternalFieldAcceptanceExecutor,
    FavoritesExternalFieldAcceptancePlan,
    FavoritesExternalFieldMapping,
    FavoritesExternalFieldObservation,
    FavoritesExternalFieldObservationState,
    FavoritesExternalFieldOwnership,
    FavoritesExternalFieldState,
    FavoritesExternalObservationEvidence,
    FavoritesExternalRecordIdentity,
    FavoritesExternalRecordObservation,
    FavoritesExternalRecordState,
    FavoritesExternalSourceIdentity,
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
    FavoritesWritePlan,
    execute_favorites_external_field_acceptance,
    plan_favorites_external_field_acceptance,
    radioreference_favorites_frequency_mapping,
    select_favorites_record_target,
)

_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "favorites"


def _snapshot() -> FavoritesStorageSnapshot:
    return FavoritesStorageSnapshot(
        catalog_bytes=(_FIXTURE_ROOT / "synthetic-f_list.cfg").read_bytes(),
        documents=(
            FavoritesStorageDocument(
                filename="f_000001.hpd",
                content=(
                    _FIXTURE_ROOT / "synthetic-favorites.hpd"
                ).read_bytes(),
            ),
        ),
    )


def _identity() -> FavoritesExternalRecordIdentity:
    return FavoritesExternalRecordIdentity(
        source=FavoritesExternalSourceIdentity(
            provider="synthetic-provider",
            dataset="metro",
        ),
        record_id="frequency-101",
    )


def _evidence(day: int) -> FavoritesExternalObservationEvidence:
    return FavoritesExternalObservationEvidence(
        observed_at=datetime(2026, 8, day, tzinfo=UTC),
    )


def _field(
    name: str,
    value: str,
) -> FavoritesExternalFieldObservation:
    return FavoritesExternalFieldObservation(
        name=name,
        state=FavoritesExternalFieldObservationState.VALUE,
        value=value,
    )


def _observation(
    *,
    frequency: str = "155100000",
    name: str | None = None,
    identity: FavoritesExternalRecordIdentity | None = None,
) -> FavoritesExternalRecordObservation:
    fields = []
    if name is not None:
        fields.append(_field("name", name))
    fields.append(_field("frequency", frequency))
    return FavoritesExternalRecordObservation(
        identity=_identity() if identity is None else identity,
        evidence=_evidence(16),
        fields=tuple(fields),
    )


def _target(snapshot: FavoritesStorageSnapshot | None = None):
    actual = _snapshot() if snapshot is None else snapshot
    target = select_favorites_record_target(
        actual,
        5,
        document_index=0,
    )
    assert target.record.command == "C-Freq"
    assert target.record.fields[2] == "Synthetic Channel"
    assert target.record.fields[4] == "155000000"
    return target


def _record(
    *,
    snapshot: FavoritesStorageSnapshot | None = None,
    fields: tuple[FavoritesExternalFieldState, ...] = (),
    identity: FavoritesExternalRecordIdentity | None = None,
    detached: bool = False,
) -> FavoritesExternalRecordState:
    return FavoritesExternalRecordState(
        target=_target(snapshot),
        fields=fields,
        external_identity=_identity() if identity is None else identity,
        last_observation=_evidence(15),
        detached=detached,
    )


def _mapping(
    *,
    target=None,
    observation: FavoritesExternalRecordObservation | None = None,
    scanner_value: str | None = None,
) -> FavoritesExternalFieldMapping:
    actual_observation = _observation() if observation is None else observation
    frequency = next(
        field
        for field in actual_observation.fields
        if field.name == "frequency"
    )
    return FavoritesExternalFieldMapping(
        target=_target() if target is None else target,
        observation=actual_observation,
        field=frequency,
        field_index=4,
        scanner_value=(
            frequency.value if scanner_value is None else scanner_value
        ),
    )


def _bound_frequency_state(
    *,
    ownership: FavoritesExternalFieldOwnership = (
        FavoritesExternalFieldOwnership.EXTERNAL
    ),
    field_index: int = 4,
) -> FavoritesExternalFieldState:
    return FavoritesExternalFieldState(
        name="frequency",
        field_index=field_index,
        ownership=ownership,
        last_external=(
            _field("frequency", "155000000")
            if ownership is not FavoritesExternalFieldOwnership.LOCAL
            else None
        ),
    )


class _StorageSource:
    def __init__(
        self,
        result: object,
        *,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls = 0

    def read_snapshot(self) -> object:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


def test_plan_accepts_unbound_mapped_frequency_and_changes_only_field_four() -> None:
    snapshot = _snapshot()
    record = _record(snapshot=snapshot)
    observation = _observation()
    mapping = _mapping(
        target=record.target,
        observation=observation,
    )

    plan = plan_favorites_external_field_acceptance(
        snapshot,
        record,
        mapping,
    )

    assert plan.mapping is mapping
    assert plan.baseline_state is record
    assert plan.write_plan.baseline_snapshot is snapshot
    assert plan.write_plan.has_changes
    assert not plan.write_plan.is_blocked

    intended = plan.intended_state.target.record
    assert intended.fields[4] == "155100000"
    assert intended.line_ending == record.target.record.line_ending
    assert tuple(
        index
        for index, (before, after) in enumerate(
            zip(
                record.target.record.fields,
                intended.fields,
                strict=True,
            )
        )
        if before != after
    ) == (4,)

    field = plan.intended_state.fields[-1]
    assert field.name == "frequency"
    assert field.field_index == 4
    assert field.ownership is FavoritesExternalFieldOwnership.EXTERNAL
    assert field.last_external is mapping.field
    assert plan.intended_state.external_identity == record.external_identity
    assert plan.intended_state.last_observation is observation.evidence

    selected_preview = next(
        field
        for field in plan.preview.fields
        if field.name == "frequency"
    )
    assert selected_preview.kind is FavoritesExternalChangeKind.ADDED


def test_plan_can_bind_unbound_field_without_rewriting_equal_scanner_bytes() -> None:
    snapshot = _snapshot()
    record = _record(snapshot=snapshot)
    observation = _observation(frequency="155000000")
    mapping = _mapping(
        target=record.target,
        observation=observation,
    )

    plan = plan_favorites_external_field_acceptance(
        snapshot,
        record,
        mapping,
    )

    assert plan.write_plan.is_noop
    assert plan.write_plan.intended_snapshot == snapshot
    assert plan.intended_state.target == record.target
    assert plan.intended_state.fields[-1].last_external is mapping.field
    assert plan.intended_state.last_observation is observation.evidence


def test_plan_updates_existing_external_field_provenance() -> None:
    snapshot = _snapshot()
    existing = _bound_frequency_state()
    record = _record(
        snapshot=snapshot,
        fields=(existing,),
    )
    observation = _observation()
    mapping = _mapping(
        target=record.target,
        observation=observation,
    )

    plan = plan_favorites_external_field_acceptance(
        snapshot,
        record,
        mapping,
    )

    assert len(plan.intended_state.fields) == 1
    intended_field = plan.intended_state.fields[0]
    assert intended_field.name == "frequency"
    assert intended_field.field_index == 4
    assert intended_field.ownership is FavoritesExternalFieldOwnership.EXTERNAL
    assert intended_field.last_external is mapping.field
    assert plan.intended_state.target.record.fields[4] == "155100000"

    selected_preview = next(
        field
        for field in plan.preview.fields
        if field.name == "frequency"
    )
    assert selected_preview.kind is FavoritesExternalChangeKind.REPLACED


def test_plan_allows_existing_external_field_noop_evidence_update() -> None:
    snapshot = _snapshot()
    existing = _bound_frequency_state()
    record = _record(
        snapshot=snapshot,
        fields=(existing,),
    )
    observation = _observation(frequency="155000000")
    mapping = _mapping(
        target=record.target,
        observation=observation,
    )

    plan = plan_favorites_external_field_acceptance(
        snapshot,
        record,
        mapping,
    )

    assert plan.write_plan.is_noop
    assert plan.intended_state.fields[0].last_external is mapping.field
    selected_preview = next(
        field
        for field in plan.preview.fields
        if field.name == "frequency"
    )
    assert selected_preview.kind is FavoritesExternalChangeKind.UNCHANGED


def test_plan_preserves_unrelated_bound_field_provenance() -> None:
    snapshot = _snapshot()
    name_observation = _field("name", "Synthetic Channel")
    name_state = FavoritesExternalFieldState(
        name="name",
        field_index=2,
        ownership=FavoritesExternalFieldOwnership.EXTERNAL,
        last_external=name_observation,
    )
    record = _record(
        snapshot=snapshot,
        fields=(name_state,),
    )
    observation = _observation(
        name="Synthetic Channel",
    )
    mapping = _mapping(
        target=record.target,
        observation=observation,
    )

    plan = plan_favorites_external_field_acceptance(
        snapshot,
        record,
        mapping,
    )

    assert plan.intended_state.fields[0] is name_state
    assert plan.intended_state.fields[1].name == "frequency"
    assert plan.intended_state.fields[1].last_external is mapping.field


def test_plan_allows_other_unbound_provider_fields_to_remain_pending() -> None:
    snapshot = _snapshot()
    record = _record(snapshot=snapshot)
    observation = FavoritesExternalRecordObservation(
        identity=_identity(),
        evidence=_evidence(16),
        fields=(
            _field("name", "Provider Channel"),
            _field("frequency", "155100000"),
        ),
    )
    mapping = _mapping(
        target=record.target,
        observation=observation,
    )

    plan = plan_favorites_external_field_acceptance(
        snapshot,
        record,
        mapping,
    )

    assert plan.intended_state.fields == (
        FavoritesExternalFieldState(
            name="frequency",
            field_index=4,
            ownership=FavoritesExternalFieldOwnership.EXTERNAL,
            last_external=mapping.field,
        ),
    )
    assert next(
        field for field in plan.preview.fields if field.name == "name"
    ).kind is FavoritesExternalChangeKind.ADDED


@pytest.mark.parametrize(
    ("argument", "value", "match"),
    (
        ("snapshot", object(), "FavoritesStorageSnapshot"),
        ("record", object(), "FavoritesExternalRecordState"),
        ("mapping", object(), "FavoritesExternalFieldMapping"),
    ),
)
def test_plan_rejects_wrong_public_argument_types(
    argument: str,
    value: object,
    match: str,
) -> None:
    snapshot = _snapshot()
    record = _record(snapshot=snapshot)
    mapping = _mapping(target=record.target)
    arguments: dict[str, object] = {
        "snapshot": snapshot,
        "record": record,
        "mapping": mapping,
    }
    arguments[argument] = value

    with pytest.raises(TypeError, match=match):
        plan_favorites_external_field_acceptance(
            **arguments,  # type: ignore[arg-type]
        )


def test_plan_requires_linked_non_detached_record() -> None:
    snapshot = _snapshot()
    local_only = FavoritesExternalRecordState(
        target=_target(snapshot),
        fields=(),
    )
    mapping = _mapping(target=local_only.target)

    with pytest.raises(
        FavoritesExternalAcceptanceError,
        match="linked non-detached record",
    ):
        plan_favorites_external_field_acceptance(
            snapshot,
            local_only,
            mapping,
        )

    detached = _record(
        snapshot=snapshot,
        detached=True,
    )
    detached_mapping = _mapping(target=detached.target)

    with pytest.raises(
        FavoritesExternalAcceptanceError,
        match="linked non-detached record",
    ):
        plan_favorites_external_field_acceptance(
            snapshot,
            detached,
            detached_mapping,
        )


def test_plan_requires_matching_external_identity() -> None:
    snapshot = _snapshot()
    record = _record(snapshot=snapshot)
    foreign_identity = FavoritesExternalRecordIdentity(
        source=_identity().source,
        record_id="frequency-202",
    )
    observation = _observation(identity=foreign_identity)
    mapping = _mapping(
        target=record.target,
        observation=observation,
    )

    with pytest.raises(
        FavoritesExternalAcceptanceError,
        match="identity does not match",
    ):
        plan_favorites_external_field_acceptance(
            snapshot,
            record,
            mapping,
        )


def test_plan_requires_mapping_target_to_equal_baseline_target() -> None:
    snapshot = _snapshot()
    record = _record(snapshot=snapshot)
    other_target = select_favorites_record_target(
        snapshot,
        4,
        document_index=0,
    )
    observation = _observation()
    mapping = FavoritesExternalFieldMapping(
        target=other_target,
        observation=observation,
        field=observation.fields[-1],
        field_index=4,
        scanner_value="155100000",
    )

    with pytest.raises(
        FavoritesExternalAcceptanceError,
        match="mapping target does not match",
    ):
        plan_favorites_external_field_acceptance(
            snapshot,
            record,
            mapping,
        )


def test_plan_rejects_stale_baseline_target() -> None:
    snapshot = _snapshot()
    record = _record(snapshot=snapshot)
    observation = _observation()
    mapping = _mapping(
        target=record.target,
        observation=observation,
    )

    stale_document = snapshot.documents[0]
    stale_snapshot = replace(
        snapshot,
        documents=(
            replace(
                stale_document,
                content=stale_document.content.replace(
                    b"Synthetic Channel",
                    b"Changed Channel  ",
                    1,
                ),
            ),
        ),
    )

    with pytest.raises(
        FavoritesExternalAcceptanceError,
        match="baseline target is stale",
    ):
        plan_favorites_external_field_acceptance(
            stale_snapshot,
            record,
            mapping,
        )


def test_plan_requires_scanner_value_to_equal_normalized_observed_value() -> None:
    snapshot = _snapshot()
    record = _record(snapshot=snapshot)
    observation = _observation()
    mapping = _mapping(
        target=record.target,
        observation=observation,
        scanner_value="155200000",
    )

    with pytest.raises(
        FavoritesExternalAcceptanceError,
        match="scanner value to equal the normalized observed value",
    ):
        plan_favorites_external_field_acceptance(
            snapshot,
            record,
            mapping,
        )


@pytest.mark.parametrize(
    "ownership",
    (
        FavoritesExternalFieldOwnership.LOCAL,
        FavoritesExternalFieldOwnership.DETACHED,
    ),
)
def test_plan_rejects_local_or_detached_existing_field(
    ownership: FavoritesExternalFieldOwnership,
) -> None:
    snapshot = _snapshot()
    field = _bound_frequency_state(ownership=ownership)
    record = _record(
        snapshot=snapshot,
        fields=(field,),
    )
    mapping = _mapping(target=record.target)

    with pytest.raises(
        FavoritesExternalAcceptanceError,
        match="externally owned field or one previously unbound",
    ):
        plan_favorites_external_field_acceptance(
            snapshot,
            record,
            mapping,
        )


def test_plan_rejects_existing_field_index_mismatch() -> None:
    snapshot = _snapshot()
    field = _bound_frequency_state(field_index=5)
    record = _record(
        snapshot=snapshot,
        fields=(field,),
    )
    mapping = _mapping(target=record.target)

    with pytest.raises(
        FavoritesExternalAcceptanceError,
        match="mapping index does not match",
    ):
        plan_favorites_external_field_acceptance(
            snapshot,
            record,
            mapping,
        )


def test_plan_rejects_mapped_index_owned_by_another_provenance_field() -> None:
    snapshot = _snapshot()
    collision = FavoritesExternalFieldState(
        name="other",
        field_index=4,
        ownership=FavoritesExternalFieldOwnership.LOCAL,
    )
    record = _record(
        snapshot=snapshot,
        fields=(collision,),
    )
    mapping = _mapping(target=record.target)

    with pytest.raises(
        FavoritesExternalAcceptanceError,
        match="already owned by another provenance field",
    ):
        plan_favorites_external_field_acceptance(
            snapshot,
            record,
            mapping,
        )


def test_plan_rejects_unresolved_conflict_on_other_bound_field() -> None:
    snapshot = _snapshot()
    local_name = FavoritesExternalFieldState(
        name="name",
        field_index=2,
        ownership=FavoritesExternalFieldOwnership.LOCAL,
    )
    record = _record(
        snapshot=snapshot,
        fields=(local_name,),
    )
    observation = _observation(name="Provider Channel")
    mapping = _mapping(
        target=record.target,
        observation=observation,
    )

    with pytest.raises(
        FavoritesExternalAcceptanceError,
        match="unresolved conflicts",
    ):
        plan_favorites_external_field_acceptance(
            snapshot,
            record,
            mapping,
        )


def test_plan_rejects_simultaneous_change_to_other_bound_field() -> None:
    snapshot = _snapshot()
    old_name = _field("name", "Synthetic Channel")
    name_state = FavoritesExternalFieldState(
        name="name",
        field_index=2,
        ownership=FavoritesExternalFieldOwnership.EXTERNAL,
        last_external=old_name,
    )
    record = _record(
        snapshot=snapshot,
        fields=(name_state,),
    )
    observation = _observation(name="Provider Channel")
    mapping = _mapping(
        target=record.target,
        observation=observation,
    )

    with pytest.raises(
        FavoritesExternalAcceptanceError,
        match="simultaneous changes to another bound field",
    ):
        plan_favorites_external_field_acceptance(
            snapshot,
            record,
            mapping,
        )


def test_plan_is_frozen_slot_backed_and_validates_reconstructed_evidence() -> None:
    snapshot = _snapshot()
    record = _record(snapshot=snapshot)
    mapping = _mapping(target=record.target)
    plan = plan_favorites_external_field_acceptance(
        snapshot,
        record,
        mapping,
    )

    assert not hasattr(plan, "__dict__")
    with pytest.raises(FrozenInstanceError):
        plan.baseline_state = record  # type: ignore[misc]

    with pytest.raises(ValueError, match="intended provenance"):
        replace(
            plan,
            intended_state=record,
        )


def test_radioreference_frequency_mapping_composes_into_field_acceptance() -> None:
    snapshot = _snapshot()
    target = _target(snapshot)
    source = FavoritesExternalSourceIdentity(
        provider="radioreference",
        dataset="synthetic-county",
    )
    observation = FavoritesExternalRecordObservation(
        identity=FavoritesExternalRecordIdentity(
            source=source,
            record_id="frequency-101",
        ),
        evidence=_evidence(16),
        fields=(
            _field("name", "Dispatch"),
            _field("frequency", "155100000"),
        ),
    )
    record = FavoritesExternalRecordState(
        target=target,
        fields=(),
        external_identity=observation.identity,
        last_observation=_evidence(15),
    )

    mapping = radioreference_favorites_frequency_mapping(
        target,
        observation,
    )
    plan = plan_favorites_external_field_acceptance(
        snapshot,
        record,
        mapping,
    )

    assert mapping.field_index == 4
    assert mapping.scanner_value == "155100000"
    assert plan.mapping is mapping
    assert plan.write_plan.has_changes
    assert not plan.write_plan.is_blocked
    assert plan.intended_state.target.record.fields[4] == "155100000"
    assert plan.intended_state.fields == (
        FavoritesExternalFieldState(
            name="frequency",
            field_index=4,
            ownership=FavoritesExternalFieldOwnership.EXTERNAL,
            last_external=mapping.field,
        ),
    )


def test_execute_field_acceptance_uses_exact_write_plan_and_verifies_readback() -> None:
    snapshot = _snapshot()
    record = _record(snapshot=snapshot)
    mapping = _mapping(target=record.target)
    plan = plan_favorites_external_field_acceptance(
        snapshot,
        record,
        mapping,
    )
    backend_evidence = object()
    executed: list[FavoritesWritePlan] = []

    def executor(write_plan: FavoritesWritePlan) -> object:
        executed.append(write_plan)
        return backend_evidence

    source = _StorageSource(plan.write_plan.intended_snapshot)

    result = execute_favorites_external_field_acceptance(
        plan,
        executor,
        source,  # type: ignore[arg-type]
    )

    assert executed == [plan.write_plan]
    assert source.calls == 1
    assert result.plan is plan
    assert result.execution_result is backend_evidence
    assert result.observed_snapshot is plan.write_plan.intended_snapshot
    assert result.accepted_state is plan.intended_state


def test_execute_field_acceptance_preserves_noop_provenance_acceptance() -> None:
    snapshot = _snapshot()
    record = _record(snapshot=snapshot)
    observation = _observation(frequency="155000000")
    mapping = _mapping(
        target=record.target,
        observation=observation,
    )
    plan = plan_favorites_external_field_acceptance(
        snapshot,
        record,
        mapping,
    )
    executed: list[FavoritesWritePlan] = []

    def executor(write_plan: FavoritesWritePlan) -> str:
        executed.append(write_plan)
        return "noop"

    source = _StorageSource(snapshot)

    result = execute_favorites_external_field_acceptance(
        plan,
        executor,
        source,  # type: ignore[arg-type]
    )

    assert plan.write_plan.is_noop
    assert executed == [plan.write_plan]
    assert source.calls == 1
    assert result.observed_snapshot is snapshot
    assert result.accepted_state is plan.intended_state
    assert result.accepted_state.fields[-1].last_external is mapping.field
    assert result.accepted_state.last_observation is observation.evidence


def test_execute_field_acceptance_propagates_executor_failure_without_readback() -> None:
    snapshot = _snapshot()
    record = _record(snapshot=snapshot)
    plan = plan_favorites_external_field_acceptance(
        snapshot,
        record,
        _mapping(target=record.target),
    )
    source = _StorageSource(plan.write_plan.intended_snapshot)

    class BackendError(RuntimeError):
        pass

    def executor(write_plan: FavoritesWritePlan) -> object:
        assert write_plan is plan.write_plan
        raise BackendError("backend failed")

    with pytest.raises(BackendError, match="backend failed"):
        execute_favorites_external_field_acceptance(
            plan,
            executor,
            source,  # type: ignore[arg-type]
        )

    assert source.calls == 0


def test_execute_field_acceptance_redacts_storage_read_failure() -> None:
    snapshot = _snapshot()
    record = _record(snapshot=snapshot)
    plan = plan_favorites_external_field_acceptance(
        snapshot,
        record,
        _mapping(target=record.target),
    )
    secret = "provider-secret-value"
    source = _StorageSource(
        plan.write_plan.intended_snapshot,
        error=RuntimeError(secret),
    )

    with pytest.raises(
        FavoritesExternalAcceptanceError,
        match="could not verify the post-write storage snapshot",
    ) as captured:
        execute_favorites_external_field_acceptance(
            plan,
            lambda _: object(),
            source,  # type: ignore[arg-type]
        )

    assert secret not in str(captured.value)
    assert source.calls == 1


def test_execute_field_acceptance_rejects_invalid_storage_evidence() -> None:
    snapshot = _snapshot()
    record = _record(snapshot=snapshot)
    plan = plan_favorites_external_field_acceptance(
        snapshot,
        record,
        _mapping(target=record.target),
    )
    source = _StorageSource(object())

    with pytest.raises(
        FavoritesExternalAcceptanceError,
        match="invalid post-write storage evidence",
    ):
        execute_favorites_external_field_acceptance(
            plan,
            lambda _: object(),
            source,  # type: ignore[arg-type]
        )

    assert source.calls == 1


def test_execute_field_acceptance_rejects_post_write_snapshot_mismatch() -> None:
    snapshot = _snapshot()
    record = _record(snapshot=snapshot)
    plan = plan_favorites_external_field_acceptance(
        snapshot,
        record,
        _mapping(target=record.target),
    )
    assert plan.write_plan.intended_snapshot != snapshot
    source = _StorageSource(snapshot)

    with pytest.raises(
        FavoritesExternalAcceptanceError,
        match="does not exactly match the intended snapshot",
    ):
        execute_favorites_external_field_acceptance(
            plan,
            lambda _: object(),
            source,  # type: ignore[arg-type]
        )

    assert source.calls == 1


def test_execute_field_acceptance_validates_public_argument_boundaries() -> None:
    snapshot = _snapshot()
    record = _record(snapshot=snapshot)
    plan = plan_favorites_external_field_acceptance(
        snapshot,
        record,
        _mapping(target=record.target),
    )
    source = _StorageSource(plan.write_plan.intended_snapshot)

    with pytest.raises(TypeError, match="FavoritesExternalFieldAcceptancePlan"):
        execute_favorites_external_field_acceptance(
            object(),  # type: ignore[arg-type]
            lambda _: object(),
            source,  # type: ignore[arg-type]
        )

    with pytest.raises(TypeError, match="executor must be callable"):
        execute_favorites_external_field_acceptance(
            plan,
            object(),  # type: ignore[arg-type]
            source,  # type: ignore[arg-type]
        )

    with pytest.raises(TypeError, match="read_snapshot"):
        execute_favorites_external_field_acceptance(
            plan,
            lambda _: object(),
            object(),  # type: ignore[arg-type]
        )


def test_execution_result_is_frozen_slot_backed_and_validates_evidence() -> None:
    snapshot = _snapshot()
    record = _record(snapshot=snapshot)
    plan = plan_favorites_external_field_acceptance(
        snapshot,
        record,
        _mapping(target=record.target),
    )
    result = FavoritesExternalFieldAcceptanceExecutionResult(
        plan=plan,
        execution_result=object(),
        observed_snapshot=plan.write_plan.intended_snapshot,
        accepted_state=plan.intended_state,
    )

    assert not hasattr(result, "__dict__")
    with pytest.raises(FrozenInstanceError):
        result.accepted_state = record  # type: ignore[misc]

    with pytest.raises(ValueError, match="observed snapshot"):
        replace(
            result,
            observed_snapshot=snapshot,
        )
    with pytest.raises(ValueError, match="accepted state"):
        replace(
            result,
            accepted_state=record,
        )


def test_radioreference_frequency_mapping_composes_through_execution() -> None:
    snapshot = _snapshot()
    target = _target(snapshot)
    source_identity = FavoritesExternalSourceIdentity(
        provider="radioreference",
        dataset="synthetic-county",
    )
    observation = FavoritesExternalRecordObservation(
        identity=FavoritesExternalRecordIdentity(
            source=source_identity,
            record_id="frequency-101",
        ),
        evidence=_evidence(16),
        fields=(
            _field("name", "Dispatch"),
            _field("frequency", "155100000"),
        ),
    )
    record = FavoritesExternalRecordState(
        target=target,
        fields=(),
        external_identity=observation.identity,
        last_observation=_evidence(15),
    )
    mapping = radioreference_favorites_frequency_mapping(
        target,
        observation,
    )
    plan = plan_favorites_external_field_acceptance(
        snapshot,
        record,
        mapping,
    )
    source = _StorageSource(plan.write_plan.intended_snapshot)

    result = execute_favorites_external_field_acceptance(
        plan,
        lambda write_plan: write_plan,
        source,  # type: ignore[arg-type]
    )

    assert result.plan.mapping is mapping
    assert result.execution_result is plan.write_plan
    assert result.accepted_state.target.record.fields[4] == "155100000"
    assert result.accepted_state.fields[-1].last_external is mapping.field


def test_field_acceptance_symbols_are_package_exports() -> None:
    assert (
        sds200.FavoritesExternalFieldAcceptanceExecutionResult
        is FavoritesExternalFieldAcceptanceExecutionResult
    )
    assert (
        sds200.FavoritesExternalFieldAcceptanceExecutor
        is FavoritesExternalFieldAcceptanceExecutor
    )
    assert (
        sds200.FavoritesExternalFieldAcceptancePlan
        is FavoritesExternalFieldAcceptancePlan
    )
    assert (
        sds200.execute_favorites_external_field_acceptance
        is execute_favorites_external_field_acceptance
    )
    assert (
        sds200.plan_favorites_external_field_acceptance
        is plan_favorites_external_field_acceptance
    )
    assert "FavoritesExternalFieldAcceptanceExecutionResult" in sds200.__all__
    assert "FavoritesExternalFieldAcceptanceExecutor" in sds200.__all__
    assert "FavoritesExternalFieldAcceptancePlan" in sds200.__all__
    assert "execute_favorites_external_field_acceptance" in sds200.__all__
    assert "plan_favorites_external_field_acceptance" in sds200.__all__
    assert not hasattr(sds200, "_replace_favorites_record_field")
