from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path

import pytest

import sds200
from sds200 import (
    FavoritesExternalAcceptanceError,
    FavoritesExternalChangeKind,
    FavoritesExternalFieldBinding,
    FavoritesExternalFieldObservation,
    FavoritesExternalFieldObservationState,
    FavoritesExternalFieldOwnership,
    FavoritesExternalFieldState,
    FavoritesExternalImportError,
    FavoritesExternalNameAcceptanceExecutionResult,
    FavoritesExternalNameAcceptanceExecutor,
    FavoritesExternalNameAcceptancePlan,
    FavoritesExternalObservationEvidence,
    FavoritesExternalRecordIdentity,
    FavoritesExternalRecordObservation,
    FavoritesExternalRecordObservationState,
    FavoritesExternalRecordState,
    FavoritesExternalSourceIdentity,
    FavoritesRecordEditError,
    FavoritesRecordSourceKind,
    FavoritesRecordTarget,
    FavoritesSourceRecord,
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
    bind_favorites_external_record,
    detach_favorites_external_field,
    detach_favorites_external_record,
    execute_favorites_external_name_acceptance,
    plan_favorites_external_name_acceptance,
    preview_favorites_external_import,
    preview_favorites_external_source,
    select_favorites_record_target,
)

_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "favorites"


def _real_snapshot() -> FavoritesStorageSnapshot:
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


def _target(
    *,
    source_index: int = 3,
    filename: str = "f_000001.hpd",
    name: str = "Dispatch",
    frequency: str = "155.1000",
) -> FavoritesRecordTarget:
    record = FavoritesSourceRecord(
        content=f"C-Freq\t{name}\t{frequency}".encode("ascii"),
        line_ending=b"\r\n",
    )
    return FavoritesRecordTarget(
        source_kind=FavoritesRecordSourceKind.HPD,
        document_index=0,
        filename=filename,
        source_index=source_index,
        record=record,
    )


def _source_identity(
    *,
    provider: str = "synthetic-provider",
    dataset: str = "metro",
) -> FavoritesExternalSourceIdentity:
    return FavoritesExternalSourceIdentity(
        provider=provider,
        dataset=dataset,
    )


def _record_identity(
    record_id: str = "channel-1",
) -> FavoritesExternalRecordIdentity:
    return FavoritesExternalRecordIdentity(
        source=_source_identity(),
        record_id=record_id,
    )


def _evidence(
    revision: str = "r1",
) -> FavoritesExternalObservationEvidence:
    return FavoritesExternalObservationEvidence(
        observed_at=datetime(2026, 8, 13, tzinfo=UTC),
        revision=revision,
    )


def _value(
    name: str,
    value: str,
) -> FavoritesExternalFieldObservation:
    return FavoritesExternalFieldObservation(
        name=name,
        state=FavoritesExternalFieldObservationState.VALUE,
        value=value,
    )


def _absent(
    name: str,
) -> FavoritesExternalFieldObservation:
    return FavoritesExternalFieldObservation(
        name=name,
        state=FavoritesExternalFieldObservationState.ABSENT,
    )


def _linked_state(
    *,
    name_ownership: FavoritesExternalFieldOwnership = (
        FavoritesExternalFieldOwnership.EXTERNAL
    ),
    frequency_ownership: FavoritesExternalFieldOwnership = (
        FavoritesExternalFieldOwnership.EXTERNAL
    ),
    target: FavoritesRecordTarget | None = None,
    identity: FavoritesExternalRecordIdentity | None = None,
) -> FavoritesExternalRecordState:
    local_target = _target() if target is None else target
    external_identity = _record_identity() if identity is None else identity
    return FavoritesExternalRecordState(
        target=local_target,
        external_identity=external_identity,
        last_observation=_evidence(),
        fields=(
            FavoritesExternalFieldState(
                name="name",
                field_index=0,
                ownership=name_ownership,
                last_external=(
                    _value("name", local_target.record.fields[0])
                    if name_ownership
                    is not FavoritesExternalFieldOwnership.LOCAL
                    else None
                ),
            ),
            FavoritesExternalFieldState(
                name="frequency",
                field_index=1,
                ownership=frequency_ownership,
                last_external=(
                    _value(
                        "frequency",
                        local_target.record.fields[1],
                    )
                    if frequency_ownership
                    is not FavoritesExternalFieldOwnership.LOCAL
                    else None
                ),
            ),
        ),
    )


def _observation(
    *,
    name: str = "Dispatch",
    frequency: str = "155.1000",
    revision: str = "r2",
    identity: FavoritesExternalRecordIdentity | None = None,
    fields: tuple[FavoritesExternalFieldObservation, ...] | None = None,
) -> FavoritesExternalRecordObservation:
    return FavoritesExternalRecordObservation(
        identity=_record_identity() if identity is None else identity,
        evidence=_evidence(revision),
        fields=(
            (
                _value("name", name),
                _value("frequency", frequency),
            )
            if fields is None
            else fields
        ),
    )


def test_bind_external_record_captures_explicit_matching_provenance() -> None:
    target = _target()
    observation = _observation(revision="accepted-r1")

    state = bind_favorites_external_record(
        target,
        observation,
        (
            FavoritesExternalFieldBinding(
                name="name",
                field_index=0,
                ownership=FavoritesExternalFieldOwnership.EXTERNAL,
            ),
            FavoritesExternalFieldBinding(
                name="frequency",
                field_index=1,
                ownership=FavoritesExternalFieldOwnership.EXTERNAL,
            ),
        ),
    )

    assert state.target is target
    assert state.external_identity is observation.identity
    assert state.last_observation is observation.evidence
    assert state.fields[0].last_external is observation.fields[0]
    assert state.fields[1].last_external is observation.fields[1]


def test_bind_external_record_preserves_explicit_local_ownership() -> None:
    target = _target()
    observation = _observation(name="Provider Dispatch")

    state = bind_favorites_external_record(
        target,
        observation,
        (
            FavoritesExternalFieldBinding(
                name="name",
                field_index=0,
                ownership=FavoritesExternalFieldOwnership.LOCAL,
            ),
            FavoritesExternalFieldBinding(
                name="frequency",
                field_index=1,
                ownership=FavoritesExternalFieldOwnership.EXTERNAL,
            ),
        ),
    )

    assert state.local_value(state.fields[0]) == "Dispatch"
    assert state.fields[0].ownership is FavoritesExternalFieldOwnership.LOCAL
    assert state.fields[0].last_external is None
    assert state.fields[1].last_external is observation.fields[1]


def test_bind_external_record_rejects_unmatched_external_value() -> None:
    with pytest.raises(
        FavoritesExternalImportError,
        match="does not match the exact local value",
    ):
        bind_favorites_external_record(
            _target(),
            _observation(name="Provider Dispatch"),
            (
                FavoritesExternalFieldBinding(
                    name="name",
                    field_index=0,
                    ownership=FavoritesExternalFieldOwnership.EXTERNAL,
                ),
            ),
        )


@pytest.mark.parametrize(
    "fields",
    (
        (_absent("name"),),
        (_value("frequency", "155.1000"),),
    ),
)
def test_bind_external_record_requires_observed_value_for_external_ownership(
    fields: tuple[FavoritesExternalFieldObservation, ...],
) -> None:
    with pytest.raises(
        FavoritesExternalImportError,
        match="requires an observed provider value",
    ):
        bind_favorites_external_record(
            _target(),
            _observation(fields=fields),
            (
                FavoritesExternalFieldBinding(
                    name="name",
                    field_index=0,
                    ownership=FavoritesExternalFieldOwnership.EXTERNAL,
                ),
            ),
        )


def test_bind_external_record_rejects_removed_observation() -> None:
    observation = FavoritesExternalRecordObservation(
        identity=_record_identity(),
        evidence=_evidence(),
        state=FavoritesExternalRecordObservationState.REMOVED,
    )

    with pytest.raises(FavoritesExternalImportError, match="requires an active observation"):
        bind_favorites_external_record(
            _target(),
            observation,
            (
                FavoritesExternalFieldBinding(
                    name="name",
                    field_index=0,
                    ownership=FavoritesExternalFieldOwnership.LOCAL,
                ),
            ),
        )


def test_bind_external_record_rejects_detached_initial_ownership() -> None:
    with pytest.raises(ValueError, match="cannot be detached"):
        FavoritesExternalFieldBinding(
            name="name",
            field_index=0,
            ownership=FavoritesExternalFieldOwnership.DETACHED,
        )


def test_external_field_binding_is_immutable() -> None:
    binding = FavoritesExternalFieldBinding(
        name="name",
        field_index=0,
        ownership=FavoritesExternalFieldOwnership.EXTERNAL,
    )

    with pytest.raises(FrozenInstanceError):
        binding.field_index = 1  # type: ignore[misc]


def test_bind_external_record_requires_immutable_nonempty_bindings() -> None:
    with pytest.raises(TypeError):
        bind_favorites_external_record(
            _target(),
            _observation(),
            [  # type: ignore[arg-type]
                FavoritesExternalFieldBinding(
                    name="name",
                    field_index=0,
                    ownership=FavoritesExternalFieldOwnership.EXTERNAL,
                ),
            ],
        )

    with pytest.raises(
        FavoritesExternalImportError,
        match="requires at least one field",
    ):
        bind_favorites_external_record(
            _target(),
            _observation(),
            (),
        )


def test_bind_external_record_rejects_wrong_binding_item_type() -> None:
    with pytest.raises(TypeError, match="FavoritesExternalFieldBinding"):
        bind_favorites_external_record(
            _target(),
            _observation(),
            (object(),),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "bindings,error_fragment",
    (
        (
            (
                FavoritesExternalFieldBinding(
                    name="name",
                    field_index=0,
                    ownership=FavoritesExternalFieldOwnership.LOCAL,
                ),
                FavoritesExternalFieldBinding(
                    name="name",
                    field_index=1,
                    ownership=FavoritesExternalFieldOwnership.LOCAL,
                ),
            ),
            "duplicate field names",
        ),
        (
            (
                FavoritesExternalFieldBinding(
                    name="name",
                    field_index=0,
                    ownership=FavoritesExternalFieldOwnership.LOCAL,
                ),
                FavoritesExternalFieldBinding(
                    name="frequency",
                    field_index=0,
                    ownership=FavoritesExternalFieldOwnership.LOCAL,
                ),
            ),
            "duplicate source field indexes",
        ),
    ),
)
def test_bind_external_record_rejects_duplicate_binding_axes(
    bindings: tuple[FavoritesExternalFieldBinding, ...],
    error_fragment: str,
) -> None:
    with pytest.raises(FavoritesExternalImportError, match=error_fragment):
        bind_favorites_external_record(
            _target(),
            _observation(),
            bindings,
        )


def test_bind_external_record_rejects_out_of_range_field_index() -> None:
    with pytest.raises(
        FavoritesExternalImportError,
        match="outside the exact target source record",
    ):
        bind_favorites_external_record(
            _target(),
            _observation(),
            (
                FavoritesExternalFieldBinding(
                    name="name",
                    field_index=99,
                    ownership=FavoritesExternalFieldOwnership.LOCAL,
                ),
            ),
        )


def test_bound_external_record_flows_into_update_preview() -> None:
    target = _target()
    accepted = _observation(revision="accepted-r1")
    state = bind_favorites_external_record(
        target,
        accepted,
        (
            FavoritesExternalFieldBinding(
                name="name",
                field_index=0,
                ownership=FavoritesExternalFieldOwnership.EXTERNAL,
            ),
            FavoritesExternalFieldBinding(
                name="frequency",
                field_index=1,
                ownership=FavoritesExternalFieldOwnership.EXTERNAL,
            ),
        ),
    )

    preview = preview_favorites_external_import(
        (state,),
        (_observation(name="Fire Dispatch", revision="provider-r2"),),
    )

    assert preview.records[0].kind is FavoritesExternalChangeKind.REPLACED
    fields = {field.name: field for field in preview.records[0].fields}
    assert fields["name"].kind is FavoritesExternalChangeKind.REPLACED
    assert fields["name"].local_value == "Dispatch"
    assert fields["name"].external_value == "Fire Dispatch"
    assert fields["frequency"].kind is FavoritesExternalChangeKind.UNCHANGED


def test_bound_local_ownership_flows_into_conflict_preview() -> None:
    target = _target()
    state = bind_favorites_external_record(
        target,
        _observation(revision="accepted-r1"),
        (
            FavoritesExternalFieldBinding(
                name="name",
                field_index=0,
                ownership=FavoritesExternalFieldOwnership.LOCAL,
            ),
            FavoritesExternalFieldBinding(
                name="frequency",
                field_index=1,
                ownership=FavoritesExternalFieldOwnership.EXTERNAL,
            ),
        ),
    )

    preview = preview_favorites_external_import(
        (state,),
        (_observation(name="Provider Dispatch", revision="provider-r2"),),
    )

    assert preview.has_conflicts is True
    assert preview.records[0].kind is FavoritesExternalChangeKind.CONFLICT
    fields = {field.name: field for field in preview.records[0].fields}
    assert fields["name"].kind is FavoritesExternalChangeKind.CONFLICT
    assert fields["name"].ownership is FavoritesExternalFieldOwnership.LOCAL
    assert fields["name"].local_value == "Dispatch"
    assert fields["name"].external_value == "Provider Dispatch"


def test_bound_state_flows_through_explicit_field_detach() -> None:
    accepted = _observation(revision="accepted-r1")
    state = bind_favorites_external_record(
        _target(),
        accepted,
        (
            FavoritesExternalFieldBinding(
                name="name",
                field_index=0,
                ownership=FavoritesExternalFieldOwnership.EXTERNAL,
            ),
        ),
    )

    detached = detach_favorites_external_field(state, "name")

    assert detached.fields[0].ownership is FavoritesExternalFieldOwnership.DETACHED
    assert detached.fields[0].last_external is accepted.fields[0]
    assert detached.local_value(detached.fields[0]) == "Dispatch"

    preview = preview_favorites_external_import(
        (detached,),
        (_observation(name="Provider Dispatch", revision="provider-r2"),),
    )
    assert preview.records[0].kind is FavoritesExternalChangeKind.CONFLICT


def test_bound_state_flows_through_explicit_record_detach() -> None:
    accepted = _observation(revision="accepted-r1")
    state = bind_favorites_external_record(
        _target(),
        accepted,
        (
            FavoritesExternalFieldBinding(
                name="name",
                field_index=0,
                ownership=FavoritesExternalFieldOwnership.EXTERNAL,
            ),
            FavoritesExternalFieldBinding(
                name="frequency",
                field_index=1,
                ownership=FavoritesExternalFieldOwnership.EXTERNAL,
            ),
        ),
    )

    detached = detach_favorites_external_record(state)

    assert detached.detached is True
    assert {
        field.ownership
        for field in detached.fields
    } == {FavoritesExternalFieldOwnership.DETACHED}

    preview = preview_favorites_external_import(
        (detached,),
        (_observation(name="Provider Dispatch", revision="provider-r2"),),
    )
    assert preview.records[0].kind is FavoritesExternalChangeKind.LOCAL_ONLY
    assert preview.has_changes is False
    assert preview.has_conflicts is False


class _StaticStorageSource:
    def __init__(
        self,
        snapshot: FavoritesStorageSnapshot,
    ) -> None:
        self.snapshot = snapshot
        self.read_count = 0

    def read_snapshot(
        self,
    ) -> FavoritesStorageSnapshot:
        self.read_count += 1
        return self.snapshot


class _FailingStorageSource:
    def __init__(self) -> None:
        self.read_count = 0

    def read_snapshot(
        self,
    ) -> FavoritesStorageSnapshot:
        self.read_count += 1
        raise RuntimeError("synthetic readback failure")


def _real_name_acceptance_inputs(
    *,
    updated_name: str = "Provider Channel",
    ownership: FavoritesExternalFieldOwnership = (
        FavoritesExternalFieldOwnership.EXTERNAL
    ),
    field_index: int = 2,
) -> tuple[
    FavoritesStorageSnapshot,
    FavoritesExternalRecordState,
    FavoritesExternalRecordObservation,
]:
    snapshot = _real_snapshot()
    target = select_favorites_record_target(
        snapshot,
        5,
        document_index=0,
    )
    local_value = target.record.fields[field_index]
    accepted = _observation(
        name=local_value,
        frequency="155100000",
        revision="accepted-r1",
    )
    state = bind_favorites_external_record(
        target,
        accepted,
        (
            FavoritesExternalFieldBinding(
                name="name",
                field_index=field_index,
                ownership=ownership,
            ),
        ),
    )
    updated = _observation(
        name=updated_name,
        frequency="155100000",
        revision="provider-r2",
    )
    return snapshot, state, updated


def test_plan_external_name_acceptance_uses_existing_editor_and_write_plan() -> None:
    snapshot, state, updated = _real_name_acceptance_inputs()

    acceptance = plan_favorites_external_name_acceptance(
        snapshot,
        state,
        updated,
    )

    assert isinstance(acceptance, FavoritesExternalNameAcceptancePlan)
    assert acceptance.preview.kind is FavoritesExternalChangeKind.REPLACED
    fields = {field.name: field for field in acceptance.preview.fields}
    assert fields["name"].kind is FavoritesExternalChangeKind.REPLACED
    assert fields["name"].external_value == "Provider Channel"
    assert fields["frequency"].kind is FavoritesExternalChangeKind.ADDED

    plan = acceptance.write_plan
    assert plan.baseline_snapshot is snapshot
    assert plan.has_changes is True
    assert plan.is_blocked is False
    assert plan.matches_baseline_snapshot(snapshot) is True

    intended_target = acceptance.intended_state.target
    assert intended_target.record.fields[2] == "Provider Channel"
    assert intended_target.record.fields[:2] == state.target.record.fields[:2]
    assert intended_target.record.fields[3:] == state.target.record.fields[3:]
    assert intended_target.record.line_ending == state.target.record.line_ending

    assert acceptance.intended_state.external_identity == state.external_identity
    assert acceptance.intended_state.last_observation == updated.evidence
    assert acceptance.intended_state.fields[0].last_external == updated.fields[0]
    assert acceptance.intended_state.fields[0].field_index == 2

    assert snapshot.documents[0].content == (
        _FIXTURE_ROOT / "synthetic-favorites.hpd"
    ).read_bytes()


def test_plan_external_name_acceptance_rejects_wrong_bound_field_index() -> None:
    snapshot, state, updated = _real_name_acceptance_inputs(
        field_index=0,
    )

    with pytest.raises(
        FavoritesExternalAcceptanceError,
        match="does not match the schema-aware editable name field",
    ):
        plan_favorites_external_name_acceptance(
            snapshot,
            state,
            updated,
        )


def test_plan_external_name_acceptance_rejects_other_bound_changes() -> None:
    snapshot = _real_snapshot()
    target = select_favorites_record_target(
        snapshot,
        5,
        document_index=0,
    )
    accepted = _observation(
        name=target.record.fields[2],
        frequency=target.record.fields[4],
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
            FavoritesExternalFieldBinding(
                name="frequency",
                field_index=4,
                ownership=FavoritesExternalFieldOwnership.EXTERNAL,
            ),
        ),
    )

    changed = _observation(
        name="Provider Channel",
        frequency="156000000",
        revision="provider-r2",
    )

    with pytest.raises(
        FavoritesExternalAcceptanceError,
        match="simultaneous changes to another bound field",
    ):
        plan_favorites_external_name_acceptance(
            snapshot,
            state,
            changed,
        )


def test_name_acceptance_plan_is_immutable() -> None:
    snapshot, state, updated = _real_name_acceptance_inputs()
    acceptance = plan_favorites_external_name_acceptance(
        snapshot,
        state,
        updated,
    )

    with pytest.raises(FrozenInstanceError):
        acceptance.intended_state = state  # type: ignore[misc]


def test_plan_external_name_acceptance_rejects_other_bound_removal() -> None:
    snapshot = _real_snapshot()
    target = select_favorites_record_target(
        snapshot,
        5,
        document_index=0,
    )
    accepted = _observation(
        name=target.record.fields[2],
        frequency=target.record.fields[4],
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
            FavoritesExternalFieldBinding(
                name="frequency",
                field_index=4,
                ownership=FavoritesExternalFieldOwnership.EXTERNAL,
            ),
        ),
    )
    removed = _observation(
        revision="provider-r2",
        fields=(
            _value("name", "Provider Channel"),
            _absent("frequency"),
        ),
    )

    with pytest.raises(
        FavoritesExternalAcceptanceError,
        match="simultaneous changes to another bound field",
    ):
        plan_favorites_external_name_acceptance(
            snapshot,
            state,
            removed,
        )


def test_name_acceptance_plan_rejects_inconsistent_public_construction() -> None:
    snapshot, state, updated = _real_name_acceptance_inputs()
    acceptance = plan_favorites_external_name_acceptance(
        snapshot,
        state,
        updated,
    )

    with pytest.raises(
        ValueError,
        match="intended state must match the exact write-plan intended snapshot",
    ):
        FavoritesExternalNameAcceptancePlan(
            preview=acceptance.preview,
            write_plan=acceptance.write_plan,
            intended_state=state,
        )


def test_plan_external_name_acceptance_revalidates_stale_exact_target() -> None:
    snapshot, state, updated = _real_name_acceptance_inputs()
    stale = FavoritesStorageSnapshot(
        catalog_bytes=snapshot.catalog_bytes,
        documents=(
            FavoritesStorageDocument(
                filename=snapshot.documents[0].filename,
                content=snapshot.documents[0].content.replace(
                    b"Synthetic Channel",
                    b"Locally Changed",
                    1,
                ),
            ),
        ),
    )

    with pytest.raises(
        FavoritesRecordEditError,
        match="no longer matches the exact source record",
    ):
        plan_favorites_external_name_acceptance(
            stale,
            state,
            updated,
        )


def test_plan_external_name_acceptance_rejects_local_ownership_conflict() -> None:
    snapshot, state, updated = _real_name_acceptance_inputs(
        ownership=FavoritesExternalFieldOwnership.LOCAL,
    )

    with pytest.raises(
        FavoritesExternalAcceptanceError,
        match="requires externally owned name provenance",
    ):
        plan_favorites_external_name_acceptance(
            snapshot,
            state,
            updated,
        )


def test_plan_external_name_acceptance_rejects_detached_record() -> None:
    snapshot, state, updated = _real_name_acceptance_inputs()
    detached = detach_favorites_external_record(state)

    with pytest.raises(
        FavoritesExternalAcceptanceError,
        match="linked non-detached record",
    ):
        plan_favorites_external_name_acceptance(
            snapshot,
            detached,
            updated,
        )


def test_plan_external_name_acceptance_propagates_unsupported_name() -> None:
    snapshot, state, updated = _real_name_acceptance_inputs(
        updated_name="x" * 65,
    )

    with pytest.raises(
        FavoritesRecordEditError,
        match="printable ASCII",
    ):
        plan_favorites_external_name_acceptance(
            snapshot,
            state,
            updated,
        )


def test_plan_external_name_acceptance_rejects_unchanged_name() -> None:
    snapshot, state, _ = _real_name_acceptance_inputs()
    unchanged = _observation(
        name=state.target.record.fields[2],
        frequency="155100000",
        revision="provider-r2",
    )

    with pytest.raises(
        FavoritesExternalAcceptanceError,
        match="requires one externally owned replaced name value",
    ):
        plan_favorites_external_name_acceptance(
            snapshot,
            state,
            unchanged,
        )


def test_plan_external_name_acceptance_rejects_removed_observation() -> None:
    snapshot, state, _ = _real_name_acceptance_inputs()
    assert state.external_identity is not None
    removed = FavoritesExternalRecordObservation(
        identity=state.external_identity,
        evidence=_evidence("provider-r2"),
        state=FavoritesExternalRecordObservationState.REMOVED,
    )

    with pytest.raises(
        FavoritesExternalAcceptanceError,
        match="requires an active observation",
    ):
        plan_favorites_external_name_acceptance(
            snapshot,
            state,
            removed,
        )


def test_plan_external_name_acceptance_rejects_identity_mismatch() -> None:
    snapshot, state, _ = _real_name_acceptance_inputs()
    updated = _observation(
        name="Provider Channel",
        frequency="155100000",
        revision="provider-r2",
        identity=_record_identity("channel-2"),
    )

    with pytest.raises(
        FavoritesExternalAcceptanceError,
        match="identity does not match",
    ):
        plan_favorites_external_name_acceptance(
            snapshot,
            state,
            updated,
        )


@pytest.mark.parametrize(
    "argument",
    ("snapshot", "record", "observation"),
)
def test_plan_external_name_acceptance_requires_exact_model_types(
    argument: str,
) -> None:
    snapshot, state, updated = _real_name_acceptance_inputs()

    with pytest.raises(TypeError):
        if argument == "snapshot":
            plan_favorites_external_name_acceptance(  # type: ignore[arg-type]
                object(),
                state,
                updated,
            )
        elif argument == "record":
            plan_favorites_external_name_acceptance(  # type: ignore[arg-type]
                snapshot,
                object(),
                updated,
            )
        else:
            plan_favorites_external_name_acceptance(  # type: ignore[arg-type]
                snapshot,
                state,
                object(),
            )


def test_execute_external_name_acceptance_promotes_only_after_exact_readback() -> None:
    snapshot, state, updated = _real_name_acceptance_inputs()
    acceptance = plan_favorites_external_name_acceptance(
        snapshot,
        state,
        updated,
    )
    source = _StaticStorageSource(
        acceptance.write_plan.intended_snapshot
    )
    calls: list[object] = []
    backend_result = object()

    def executor(plan: object) -> object:
        calls.append(plan)
        return backend_result

    result = execute_favorites_external_name_acceptance(
        acceptance,
        executor,
        source,
    )

    assert isinstance(
        result,
        FavoritesExternalNameAcceptanceExecutionResult,
    )
    assert result.plan is acceptance
    assert result.execution_result is backend_result
    assert result.observed_snapshot is acceptance.write_plan.intended_snapshot
    assert result.accepted_state is acceptance.intended_state
    assert calls == [acceptance.write_plan]
    assert source.read_count == 1


def test_execute_external_name_acceptance_propagates_executor_failure_without_readback() -> None:
    snapshot, state, updated = _real_name_acceptance_inputs()
    acceptance = plan_favorites_external_name_acceptance(
        snapshot,
        state,
        updated,
    )
    source = _StaticStorageSource(
        acceptance.write_plan.intended_snapshot
    )

    class ExecutorFailure(RuntimeError):
        pass

    def executor(_: object) -> object:
        raise ExecutorFailure("synthetic executor failure")

    with pytest.raises(
        ExecutorFailure,
        match="synthetic executor failure",
    ):
        execute_favorites_external_name_acceptance(
            acceptance,
            executor,
            source,
        )

    assert source.read_count == 0


def test_execute_external_name_acceptance_rejects_unavailable_post_write_readback() -> None:
    snapshot, state, updated = _real_name_acceptance_inputs()
    acceptance = plan_favorites_external_name_acceptance(
        snapshot,
        state,
        updated,
    )
    source = _FailingStorageSource()

    with pytest.raises(
        FavoritesExternalAcceptanceError,
        match="could not verify the post-write storage snapshot",
    ):
        execute_favorites_external_name_acceptance(
            acceptance,
            lambda _: object(),
            source,
        )

    assert source.read_count == 1


def test_execute_external_name_acceptance_rejects_mismatched_post_write_snapshot() -> None:
    snapshot, state, updated = _real_name_acceptance_inputs()
    acceptance = plan_favorites_external_name_acceptance(
        snapshot,
        state,
        updated,
    )
    source = _StaticStorageSource(snapshot)

    with pytest.raises(
        FavoritesExternalAcceptanceError,
        match="does not exactly match the intended snapshot",
    ):
        execute_favorites_external_name_acceptance(
            acceptance,
            lambda _: object(),
            source,
        )

    assert source.read_count == 1


def test_execute_external_name_acceptance_rejects_invalid_post_write_evidence() -> None:
    snapshot, state, updated = _real_name_acceptance_inputs()
    acceptance = plan_favorites_external_name_acceptance(
        snapshot,
        state,
        updated,
    )

    class InvalidStorageSource:
        def read_snapshot(self) -> object:
            return object()

    with pytest.raises(
        FavoritesExternalAcceptanceError,
        match="returned invalid post-write storage evidence",
    ):
        execute_favorites_external_name_acceptance(
            acceptance,
            lambda _: object(),
            InvalidStorageSource(),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "argument",
    ("plan", "executor", "storage_source"),
)
def test_execute_external_name_acceptance_requires_execution_contracts(
    argument: str,
) -> None:
    snapshot, state, updated = _real_name_acceptance_inputs()
    acceptance = plan_favorites_external_name_acceptance(
        snapshot,
        state,
        updated,
    )
    source = _StaticStorageSource(
        acceptance.write_plan.intended_snapshot
    )

    with pytest.raises(TypeError):
        if argument == "plan":
            execute_favorites_external_name_acceptance(  # type: ignore[arg-type]
                object(),
                lambda _: object(),
                source,
            )
        elif argument == "executor":
            execute_favorites_external_name_acceptance(  # type: ignore[arg-type]
                acceptance,
                object(),
                source,
            )
        else:
            execute_favorites_external_name_acceptance(  # type: ignore[arg-type]
                acceptance,
                lambda _: object(),
                object(),
            )


def test_name_acceptance_execution_result_is_immutable() -> None:
    snapshot, state, updated = _real_name_acceptance_inputs()
    acceptance = plan_favorites_external_name_acceptance(
        snapshot,
        state,
        updated,
    )
    result = execute_favorites_external_name_acceptance(
        acceptance,
        lambda _: object(),
        _StaticStorageSource(
            acceptance.write_plan.intended_snapshot
        ),
    )

    with pytest.raises(FrozenInstanceError):
        result.accepted_state = state  # type: ignore[misc]


def test_name_acceptance_execution_result_rejects_inconsistent_public_construction() -> None:
    snapshot, state, updated = _real_name_acceptance_inputs()
    acceptance = plan_favorites_external_name_acceptance(
        snapshot,
        state,
        updated,
    )

    with pytest.raises(
        ValueError,
        match="observed snapshot must match the exact intended snapshot",
    ):
        FavoritesExternalNameAcceptanceExecutionResult(
            plan=acceptance,
            execution_result=object(),
            observed_snapshot=snapshot,
            accepted_state=acceptance.intended_state,
        )

    with pytest.raises(
        ValueError,
        match="accepted state must match the planned intended provenance",
    ):
        FavoritesExternalNameAcceptanceExecutionResult(
            plan=acceptance,
            execution_result=object(),
            observed_snapshot=acceptance.write_plan.intended_snapshot,
            accepted_state=state,
        )


def test_name_acceptance_executor_protocol_is_public_typing_contract() -> None:
    def executor(_: object) -> object:
        return object()

    typed_executor: FavoritesExternalNameAcceptanceExecutor = executor
    assert callable(typed_executor)


def test_external_identity_and_evidence_are_immutable() -> None:
    source = _source_identity()
    identity = _record_identity()
    evidence = _evidence()

    assert source.sort_key == ("synthetic-provider", "metro")
    assert identity.sort_key == (
        "synthetic-provider",
        "metro",
        "channel-1",
    )
    assert evidence.revision == "r1"

    with pytest.raises(FrozenInstanceError):
        identity.record_id = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("provider", "dataset", "record_id"),
    (
        ("", "metro", "record"),
        (" provider ", "metro", "record"),
        ("provider", "", "record"),
        ("provider", "metro", ""),
        ("provider", "metro", "bad\nrecord"),
    ),
)
def test_external_identity_rejects_unsafe_text(
    provider: str,
    dataset: str,
    record_id: str,
) -> None:
    with pytest.raises(ValueError):
        FavoritesExternalRecordIdentity(
            source=FavoritesExternalSourceIdentity(
                provider=provider,
                dataset=dataset,
            ),
            record_id=record_id,
        )


def test_external_evidence_requires_timezone_aware_time() -> None:
    with pytest.raises(ValueError):
        FavoritesExternalObservationEvidence(
            observed_at=datetime(2026, 8, 13),
            revision="r1",
        )


def test_field_observation_distinguishes_absent_from_unprovided() -> None:
    present = _value("name", "")
    absent = _absent("frequency")

    assert present.value == ""
    assert absent.value is None

    with pytest.raises(ValueError):
        FavoritesExternalFieldObservation(
            name="frequency",
            state=FavoritesExternalFieldObservationState.ABSENT,
            value="should-not-be-present",
        )


def test_removed_record_observation_rejects_fields() -> None:
    with pytest.raises(ValueError):
        FavoritesExternalRecordObservation(
            identity=_record_identity(),
            evidence=_evidence(),
            state=FavoritesExternalRecordObservationState.REMOVED,
            fields=(_value("name", "Dispatch"),),
        )


def test_local_record_state_uses_exact_record_target_provenance() -> None:
    target = _target()
    state = _linked_state(target=target)

    assert state.target is target
    assert state.local_value(state.fields[0]) == "Dispatch"
    assert state.local_value(state.fields[1]) == "155.1000"
    assert state.external_identity == _record_identity()


def test_local_only_state_cannot_claim_external_ownership() -> None:
    with pytest.raises(ValueError):
        FavoritesExternalRecordState(
            target=_target(),
            fields=(
                FavoritesExternalFieldState(
                    name="name",
                    field_index=0,
                    ownership=FavoritesExternalFieldOwnership.EXTERNAL,
                    last_external=_value("name", "Dispatch"),
                ),
            ),
        )


def test_linked_state_rejects_out_of_range_and_duplicate_fields() -> None:
    target = _target()

    with pytest.raises(ValueError):
        FavoritesExternalRecordState(
            target=target,
            external_identity=_record_identity(),
            last_observation=_evidence(),
            fields=(
                FavoritesExternalFieldState(
                    name="name",
                    field_index=9,
                    ownership=FavoritesExternalFieldOwnership.EXTERNAL,
                    last_external=_value("name", "Dispatch"),
                ),
            ),
        )

    with pytest.raises(ValueError):
        FavoritesExternalRecordState(
            target=target,
            external_identity=_record_identity(),
            last_observation=_evidence(),
            fields=(
                FavoritesExternalFieldState(
                    name="name",
                    field_index=0,
                    ownership=FavoritesExternalFieldOwnership.LOCAL,
                ),
                FavoritesExternalFieldState(
                    name="name",
                    field_index=1,
                    ownership=FavoritesExternalFieldOwnership.LOCAL,
                ),
            ),
        )


def test_preview_unchanged_external_record() -> None:
    preview = preview_favorites_external_import(
        (_linked_state(),),
        (_observation(),),
    )

    assert preview.has_changes is False
    assert preview.has_conflicts is False
    assert preview.records[0].kind is FavoritesExternalChangeKind.UNCHANGED
    assert {
        field.kind for field in preview.records[0].fields
    } == {FavoritesExternalChangeKind.UNCHANGED}


def test_preview_external_replacement_is_preview_only() -> None:
    state = _linked_state()
    original_record = state.target.record

    preview = preview_favorites_external_import(
        (state,),
        (_observation(name="Fire Dispatch"),),
    )

    record = preview.records[0]
    assert record.kind is FavoritesExternalChangeKind.REPLACED
    assert preview.has_changes is True
    assert state.target.record is original_record
    assert state.target.record.fields[0] == "Dispatch"

    fields = {field.name: field for field in record.fields}
    assert fields["name"].kind is FavoritesExternalChangeKind.REPLACED
    assert fields["name"].local_value == "Dispatch"
    assert fields["name"].external_value == "Fire Dispatch"


def test_preview_local_owned_difference_is_conflict() -> None:
    state = _linked_state(
        name_ownership=FavoritesExternalFieldOwnership.LOCAL,
    )

    preview = preview_favorites_external_import(
        (state,),
        (_observation(name="Provider Name"),),
    )

    assert preview.has_conflicts is True
    assert preview.records[0].kind is FavoritesExternalChangeKind.CONFLICT

    name = next(
        field
        for field in preview.records[0].fields
        if field.name == "name"
    )
    assert name.kind is FavoritesExternalChangeKind.CONFLICT
    assert name.ownership is FavoritesExternalFieldOwnership.LOCAL


def test_preview_local_owned_equal_value_remains_local_only() -> None:
    state = _linked_state(
        name_ownership=FavoritesExternalFieldOwnership.LOCAL,
    )

    preview = preview_favorites_external_import(
        (state,),
        (_observation(),),
    )

    name = next(
        field
        for field in preview.records[0].fields
        if field.name == "name"
    )
    assert name.kind is FavoritesExternalChangeKind.LOCAL_ONLY
    assert preview.records[0].kind is FavoritesExternalChangeKind.UNCHANGED


def test_preview_explicit_absence_can_remove_external_field() -> None:
    state = _linked_state()

    preview = preview_favorites_external_import(
        (state,),
        (
            _observation(
                fields=(
                    _value("name", "Dispatch"),
                    _absent("frequency"),
                )
            ),
        ),
    )

    fields = {field.name: field for field in preview.records[0].fields}
    assert fields["frequency"].kind is FavoritesExternalChangeKind.REMOVED
    assert (
        fields["frequency"].external_state
        is FavoritesExternalFieldObservationState.ABSENT
    )
    assert preview.records[0].kind is FavoritesExternalChangeKind.REPLACED


def test_preview_unprovided_field_does_not_infer_removal() -> None:
    state = _linked_state()

    preview = preview_favorites_external_import(
        (state,),
        (
            _observation(
                fields=(_value("name", "Dispatch"),),
            ),
        ),
    )

    fields = {field.name: field for field in preview.records[0].fields}
    assert fields["frequency"].kind is FavoritesExternalChangeKind.LOCAL_ONLY
    assert fields["frequency"].external_state is None
    assert preview.records[0].kind is FavoritesExternalChangeKind.UNCHANGED


def test_preview_new_provider_field_is_added_without_scanner_mapping() -> None:
    state = _linked_state()

    preview = preview_favorites_external_import(
        (state,),
        (
            _observation(
                fields=(
                    _value("name", "Dispatch"),
                    _value("frequency", "155.1000"),
                    _value("provider-note", "source-only"),
                )
            ),
        ),
    )

    extra = next(
        field
        for field in preview.records[0].fields
        if field.name == "provider-note"
    )
    assert extra.kind is FavoritesExternalChangeKind.ADDED
    assert extra.local_value is None
    assert preview.records[0].kind is FavoritesExternalChangeKind.REPLACED


def test_preview_unbound_active_record_is_added() -> None:
    observation = _observation(identity=_record_identity("new-channel"))

    preview = preview_favorites_external_import(
        (),
        (observation,),
    )

    assert preview.records[0].target is None
    assert preview.records[0].kind is FavoritesExternalChangeKind.ADDED
    assert preview.has_changes is True


def test_unknown_provider_tombstone_does_not_infer_local_deletion() -> None:
    observation = FavoritesExternalRecordObservation(
        identity=_record_identity("unknown"),
        evidence=_evidence("deleted-r1"),
        state=FavoritesExternalRecordObservationState.REMOVED,
    )

    preview = preview_favorites_external_import(
        (),
        (observation,),
    )

    assert preview.records[0].kind is FavoritesExternalChangeKind.UNCHANGED
    assert preview.has_changes is False


def test_linked_provider_tombstone_removes_fully_external_record() -> None:
    state = _linked_state()
    observation = FavoritesExternalRecordObservation(
        identity=_record_identity(),
        evidence=_evidence("deleted-r2"),
        state=FavoritesExternalRecordObservationState.REMOVED,
    )

    preview = preview_favorites_external_import(
        (state,),
        (observation,),
    )

    assert preview.records[0].kind is FavoritesExternalChangeKind.REMOVED
    assert {
        field.kind for field in preview.records[0].fields
    } == {FavoritesExternalChangeKind.REMOVED}


def test_provider_tombstone_conflicts_with_local_owned_field() -> None:
    state = _linked_state(
        frequency_ownership=FavoritesExternalFieldOwnership.LOCAL,
    )
    observation = FavoritesExternalRecordObservation(
        identity=_record_identity(),
        evidence=_evidence("deleted-r2"),
        state=FavoritesExternalRecordObservationState.REMOVED,
    )

    preview = preview_favorites_external_import(
        (state,),
        (observation,),
    )

    assert preview.records[0].kind is FavoritesExternalChangeKind.CONFLICT
    assert preview.has_conflicts is True


def test_missing_provider_observation_leaves_local_record_local_only() -> None:
    state = _linked_state()

    preview = preview_favorites_external_import(
        (state,),
        (),
    )

    assert preview.records[0].kind is FavoritesExternalChangeKind.LOCAL_ONLY
    assert preview.has_changes is False


def test_local_only_record_without_provider_identity_is_preserved() -> None:
    state = FavoritesExternalRecordState(
        target=_target(),
        fields=(
            FavoritesExternalFieldState(
                name="name",
                field_index=0,
                ownership=FavoritesExternalFieldOwnership.LOCAL,
            ),
        ),
    )

    preview = preview_favorites_external_import(
        (state,),
        (),
    )

    assert preview.records[0].external_identity is None
    assert preview.records[0].kind is FavoritesExternalChangeKind.LOCAL_ONLY


def test_detach_field_preserves_local_value_and_external_provenance() -> None:
    state = _linked_state()
    original = state.fields[0].last_external

    detached = detach_favorites_external_field(state, "name")

    assert detached.target is state.target
    assert detached.fields[0].ownership is FavoritesExternalFieldOwnership.DETACHED
    assert detached.fields[0].last_external is original
    assert detached.local_value(detached.fields[0]) == "Dispatch"
    assert state.fields[0].ownership is FavoritesExternalFieldOwnership.EXTERNAL


def test_detached_field_conflicts_instead_of_being_overwritten() -> None:
    state = detach_favorites_external_field(
        _linked_state(),
        "name",
    )

    preview = preview_favorites_external_import(
        (state,),
        (_observation(name="Provider Name"),),
    )

    assert preview.records[0].kind is FavoritesExternalChangeKind.CONFLICT
    name = next(
        field
        for field in preview.records[0].fields
        if field.name == "name"
    )
    assert name.ownership is FavoritesExternalFieldOwnership.DETACHED


def test_detach_record_makes_later_provider_changes_local_only() -> None:
    state = detach_favorites_external_record(_linked_state())

    assert state.detached is True
    assert {
        field.ownership for field in state.fields
    } == {FavoritesExternalFieldOwnership.DETACHED}

    preview = preview_favorites_external_import(
        (state,),
        (_observation(name="Provider Name"),),
    )

    assert preview.records[0].kind is FavoritesExternalChangeKind.LOCAL_ONLY
    assert preview.has_changes is False
    assert preview.has_conflicts is False


def test_detached_record_ignores_provider_tombstone() -> None:
    state = detach_favorites_external_record(_linked_state())
    tombstone = FavoritesExternalRecordObservation(
        identity=_record_identity(),
        evidence=_evidence("deleted-r2"),
        state=FavoritesExternalRecordObservationState.REMOVED,
    )

    preview = preview_favorites_external_import(
        (state,),
        (tombstone,),
    )

    assert preview.records[0].kind is FavoritesExternalChangeKind.LOCAL_ONLY


def test_preview_rejects_duplicate_local_provider_identity() -> None:
    first = _linked_state(target=_target(source_index=1))
    second = _linked_state(target=_target(source_index=2))

    with pytest.raises(FavoritesExternalImportError):
        preview_favorites_external_import(
            (first, second),
            (),
        )


def test_preview_rejects_duplicate_observation_identity() -> None:
    observation = _observation()

    with pytest.raises(FavoritesExternalImportError):
        preview_favorites_external_import(
            (),
            (observation, observation),
        )


def test_preview_order_is_deterministic_by_external_identity() -> None:
    second = _observation(identity=_record_identity("z-record"))
    first = _observation(identity=_record_identity("a-record"))

    preview = preview_favorites_external_import(
        (),
        (second, first),
    )

    assert [
        record.external_identity.record_id
        for record in preview.records
        if record.external_identity is not None
    ] == ["a-record", "z-record"]


def test_fakeable_source_boundary_reads_once() -> None:
    class FakeSource:
        def __init__(self) -> None:
            self.calls = 0

        def read_observations(
            self,
        ) -> tuple[FavoritesExternalRecordObservation, ...]:
            self.calls += 1
            return (_observation(),)

    source = FakeSource()
    preview = preview_favorites_external_source(
        (_linked_state(),),
        source,
    )

    assert source.calls == 1
    assert preview.records[0].kind is FavoritesExternalChangeKind.UNCHANGED


def test_source_failure_is_redacted() -> None:
    secret = "provider-token-secret"

    class FailingSource:
        def read_observations(
            self,
        ) -> tuple[FavoritesExternalRecordObservation, ...]:
            raise RuntimeError(f"transport leaked {secret}")

    with pytest.raises(FavoritesExternalImportError) as captured:
        preview_favorites_external_source(
            (),
            FailingSource(),
        )

    assert secret not in str(captured.value)
    assert captured.value.__cause__ is None


def test_source_rejects_mutable_observation_collection() -> None:
    class MutableSource:
        def read_observations(
            self,
        ) -> tuple[FavoritesExternalRecordObservation, ...]:
            return [_observation()]  # type: ignore[return-value]

    with pytest.raises(FavoritesExternalImportError):
        preview_favorites_external_source(
            (),
            MutableSource(),
        )


def test_external_favorites_public_symbols_are_package_exports() -> None:
    expected = (
        "FavoritesExternalAcceptanceError",
        "FavoritesExternalChangeKind",
        "FavoritesExternalFieldBinding",
        "FavoritesExternalFieldObservation",
        "FavoritesExternalFieldObservationState",
        "FavoritesExternalFieldOwnership",
        "FavoritesExternalFieldPreview",
        "FavoritesExternalFieldState",
        "FavoritesExternalImportError",
        "FavoritesExternalImportPreview",
        "FavoritesExternalNameAcceptanceExecutionResult",
        "FavoritesExternalNameAcceptanceExecutor",
        "FavoritesExternalNameAcceptancePlan",
        "FavoritesExternalObservationEvidence",
        "FavoritesExternalRecordIdentity",
        "FavoritesExternalRecordObservation",
        "FavoritesExternalRecordObservationState",
        "FavoritesExternalRecordPreview",
        "FavoritesExternalRecordState",
        "FavoritesExternalSource",
        "FavoritesExternalSourceIdentity",
        "bind_favorites_external_record",
        "detach_favorites_external_field",
        "detach_favorites_external_record",
        "execute_favorites_external_name_acceptance",
        "plan_favorites_external_name_acceptance",
        "preview_favorites_external_import",
        "preview_favorites_external_source",
    )

    for name in expected:
        assert name in sds200.__all__
        assert hasattr(sds200, name)
