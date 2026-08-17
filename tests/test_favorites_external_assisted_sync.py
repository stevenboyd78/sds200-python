from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

import sds200
import sds200.radioreference_http as radioreference_http
from sds200 import (
    FavoritesExternalAssistedSynchronizationService,
    FavoritesExternalChangeKind,
    FavoritesExternalFieldBinding,
    FavoritesExternalFieldObservation,
    FavoritesExternalFieldObservationState,
    FavoritesExternalFieldOwnership,
    FavoritesExternalObservationEvidence,
    FavoritesExternalProvenanceLifecycle,
    FavoritesExternalRecordIdentity,
    FavoritesExternalRecordObservation,
    FavoritesExternalRefreshDetachPlan,
    FavoritesExternalRefreshDetachResult,
    FavoritesExternalRefreshDetachScope,
    FavoritesExternalRefreshFieldAcceptancePlan,
    FavoritesExternalRefreshFieldAcceptanceResult,
    FavoritesExternalRefreshNameAcceptancePlan,
    FavoritesExternalRefreshNameAcceptanceResult,
    FavoritesExternalRefreshRecordDeletePlan,
    FavoritesExternalRefreshRecordMutationResult,
    FavoritesExternalRefreshResult,
    FavoritesExternalSourceIdentity,
    RadioReferenceAssistedSynchronizationSourceFactory,
    RadioReferenceConfiguration,
    RadioReferenceCredential,
    RadioReferenceFavoritesMappedField,
    RadioReferenceHttpsSoapExchangeFactory,
    RadioReferenceObservationRequestPlan,
    RadioReferenceObservationSessionFactory,
    RadioReferenceSource,
    RadioReferenceWsdlOperation,
    bind_favorites_external_record,
    preview_favorites_external_import,
    save_favorites_external_provenance,
    select_favorites_record_target,
)
from tests.checkpoint_c_helpers import (
    Storage,
    active_observation,
    import_plan,
    linked_state,
    removed_observation,
    snapshot,
)
from tests.test_radioreference_http import (
    FakeConnection,
    FakeResponse,
    _synthetic_frequency_response,
)


class _Source:
    def __init__(self, *values: object) -> None:
        self.values = values
        self.calls = 0

    def read_observations(self) -> object:
        value = self.values[self.calls]
        self.calls += 1
        if isinstance(value, BaseException):
            raise value
        return value


def _lifecycle(tmp_path: Path) -> FavoritesExternalProvenanceLifecycle:
    lifecycle = FavoritesExternalProvenanceLifecycle(
        Storage(snapshot()), tmp_path / "state" / "provenance.json"
    )
    lifecycle.start()
    return lifecycle


def _observation(revision: str) -> FavoritesExternalRecordObservation:
    return FavoritesExternalRecordObservation(
        FavoritesExternalRecordIdentity(
            FavoritesExternalSourceIdentity("synthetic-provider", "metro"),
            "channel-1",
        ),
        FavoritesExternalObservationEvidence(
            datetime(2026, 8, 16, tzinfo=UTC), revision
        ),
        (
            FavoritesExternalFieldObservation(
                "name", FavoritesExternalFieldObservationState.VALUE, "Channel"
            ),
        ),
    )


def test_service_construction_and_each_explicit_refresh_read_once(
    tmp_path: Path,
) -> None:
    first = (_observation("one"),)
    second = (_observation("two"),)
    source = _Source(first, second)
    lifecycle = _lifecycle(tmp_path)

    service = FavoritesExternalAssistedSynchronizationService(lifecycle, source)
    assert source.calls == 0

    first_result = service.refresh()
    second_result = service.refresh()

    assert type(first_result) is FavoritesExternalRefreshResult
    assert first_result.observations is first
    assert first_result.lifecycle_snapshot == lifecycle.snapshot()
    assert first_result.preview == preview_favorites_external_import((), first)
    assert second_result.observations is second
    assert source.calls == 2


def test_service_construction_boundaries_and_current_snapshot(tmp_path: Path) -> None:
    lifecycle = _lifecycle(tmp_path)

    with pytest.raises(TypeError, match="exact.*Lifecycle"):
        FavoritesExternalAssistedSynchronizationService(object(), _Source())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="FavoritesExternalSource"):
        FavoritesExternalAssistedSynchronizationService(lifecycle, object())  # type: ignore[arg-type]

    source = _Source(())
    service = FavoritesExternalAssistedSynchronizationService(lifecycle, source)
    assert service.lifecycle_snapshot == lifecycle.snapshot()
    assert source.calls == 0


def test_refresh_propagates_source_failure_without_an_extra_read(tmp_path: Path) -> None:
    source = _Source(RuntimeError("provider detail"))
    service = FavoritesExternalAssistedSynchronizationService(
        _lifecycle(tmp_path), source
    )

    with pytest.raises(RuntimeError, match="provider detail"):
        service.refresh()
    assert source.calls == 1


def test_service_record_import_plan_and_execution_delegate_exactly(
    tmp_path: Path,
) -> None:
    lifecycle, storage, _path, existing_plan = import_plan(tmp_path)
    source = _Source()
    service = FavoritesExternalAssistedSynchronizationService(lifecycle, source)
    plan = service.plan_record_import(
        existing_plan.refresh_result,
        existing_plan.selected_preview,
        existing_plan.anchor,
        existing_plan.template,
        existing_plan.bindings,
    )
    calls: list[object] = []
    opaque = object()

    def executor(write_plan: object) -> object:
        calls.append(write_plan)
        storage.value = plan.write_plan.intended_snapshot
        return opaque

    result = service.execute_record_mutation(plan, executor)

    assert type(plan) is type(existing_plan)
    assert plan.template is existing_plan.template
    assert calls == [plan.write_plan]
    assert result.durable_result.execution_result is opaque
    assert result.lifecycle_snapshot == service.lifecycle_snapshot
    assert source.calls == 0


def test_every_planning_family_rejects_stale_and_foreign_refresh_before_delegation(
    tmp_path: Path,
) -> None:
    lifecycle, storage, _path, existing_plan = import_plan(tmp_path)
    source = _Source()
    service = FavoritesExternalAssistedSynchronizationService(lifecycle, source)
    refresh = existing_plan.refresh_result
    selected = existing_plan.selected_preview
    calls: list[object] = []

    def executor(write_plan: object) -> object:
        calls.append(write_plan)
        storage.value = existing_plan.write_plan.intended_snapshot
        return object()

    service.execute_record_mutation(existing_plan, executor)
    assert calls == [existing_plan.write_plan]

    stale_calls = (
        lambda: service.plan_name_acceptance(refresh, selected),
        lambda: service.plan_field_acceptance(refresh, selected, object()),
        lambda: service.radioreference_field_mapping(
            refresh, selected, object(), RadioReferenceFavoritesMappedField.NAME
        ),
        lambda: service.plan_record_import(
            refresh,
            selected,
            existing_plan.anchor,
            existing_plan.template,
            existing_plan.bindings,
        ),
        lambda: service.plan_record_delete(refresh, selected),
        lambda: service.plan_record_keep_local(refresh, selected),
        lambda: service.plan_detach(
            refresh, selected, FavoritesExternalRefreshDetachScope.RECORD
        ),
    )
    for call in stale_calls:
        with pytest.raises(ValueError, match="stale or belongs to another"):
            call()
    assert source.calls == 0

    foreign_lifecycle = _lifecycle(tmp_path / "foreign")
    foreign_source = _Source(())
    foreign_service = FavoritesExternalAssistedSynchronizationService(
        foreign_lifecycle, foreign_source
    )
    foreign_refresh = foreign_service.refresh()
    with pytest.raises(ValueError, match="stale or belongs to another"):
        service.plan_record_import(
            foreign_refresh,
            selected,
            existing_plan.anchor,
            existing_plan.template,
            existing_plan.bindings,
        )
    assert foreign_source.calls == 1
    assert source.calls == 0


def test_planning_requires_exact_refresh_and_active_lifecycle(tmp_path: Path) -> None:
    lifecycle = _lifecycle(tmp_path)
    service = FavoritesExternalAssistedSynchronizationService(lifecycle, _Source())
    with pytest.raises(TypeError, match="exact refresh result"):
        service.plan_record_delete(object(), object())  # type: ignore[arg-type]

    source = _Source(())
    service = FavoritesExternalAssistedSynchronizationService(lifecycle, source)
    refresh = service.refresh()
    lifecycle.close()
    with pytest.raises(RuntimeError, match="must be active"):
        service.plan_record_delete(refresh, object())  # type: ignore[arg-type]
    assert source.calls == 1


def test_radioreference_mapping_dispatch_requires_exact_refresh_evidence(
    tmp_path: Path,
) -> None:
    favorites = snapshot()
    target = select_favorites_record_target(favorites, 5, document_index=0)
    identity = FavoritesExternalRecordIdentity(
        FavoritesExternalSourceIdentity("radioreference", "county-1"),
        "frequency-101",
    )
    baseline_observation = FavoritesExternalRecordObservation(
        identity,
        FavoritesExternalObservationEvidence(
            datetime(2026, 8, 15, tzinfo=UTC), "old"
        ),
        (
            FavoritesExternalFieldObservation(
                "name", FavoritesExternalFieldObservationState.VALUE, target.record.fields[2]
            ),
            FavoritesExternalFieldObservation(
                "frequency", FavoritesExternalFieldObservationState.VALUE, "155000000"
            ),
        ),
    )
    state = bind_favorites_external_record(
        target,
        baseline_observation,
        (
            FavoritesExternalFieldBinding(
                "name", 2, FavoritesExternalFieldOwnership.EXTERNAL
            ),
            FavoritesExternalFieldBinding(
                "frequency", 4, FavoritesExternalFieldOwnership.EXTERNAL
            ),
        ),
    )
    path = tmp_path / "state" / "provenance.json"
    save_favorites_external_provenance((state,), path)
    lifecycle = FavoritesExternalProvenanceLifecycle(Storage(favorites), path)
    lifecycle.start()
    observation = FavoritesExternalRecordObservation(
        identity,
        FavoritesExternalObservationEvidence(
            datetime(2026, 8, 16, tzinfo=UTC), "new"
        ),
        (
            FavoritesExternalFieldObservation(
                "name",
                FavoritesExternalFieldObservationState.VALUE,
                target.record.fields[2],
            ),
            FavoritesExternalFieldObservation(
                "frequency", FavoritesExternalFieldObservationState.VALUE, "155100000"
            ),
        ),
    )
    source = _Source((observation,))
    service = FavoritesExternalAssistedSynchronizationService(lifecycle, source)
    refresh = service.refresh()
    selected = refresh.preview.records[0]

    name = service.radioreference_field_mapping(
        refresh, selected, observation, RadioReferenceFavoritesMappedField.NAME
    )
    frequency = service.radioreference_field_mapping(
        refresh, selected, observation, RadioReferenceFavoritesMappedField.FREQUENCY
    )

    assert name.field_index == 2
    assert name.scanner_value == target.record.fields[2]
    assert frequency.field_index == 4
    assert frequency.scanner_value == "155100000"
    assert service.plan_field_acceptance(refresh, selected, frequency).mapping is frequency
    with pytest.raises(ValueError, match="exact retained observation"):
        service.radioreference_field_mapping(
            refresh,
            selected,
            FavoritesExternalRecordObservation(
                observation.identity, observation.evidence, observation.fields
            ),
            RadioReferenceFavoritesMappedField.NAME,
        )
    with pytest.raises(ValueError, match="not supported"):
        service.radioreference_field_mapping(
            refresh,
            selected,
            observation,
            RadioReferenceFavoritesMappedField.TALKGROUP_DECIMAL,
        )
    assert source.calls == 1


def _linked_service(
    tmp_path: Path,
    observation: FavoritesExternalRecordObservation,
    *,
    target_index: int = 5,
    bindings: tuple[FavoritesExternalFieldBinding, ...] | None = None,
    source_values: tuple[object, ...] | None = None,
) -> tuple[
    FavoritesExternalAssistedSynchronizationService,
    FavoritesExternalProvenanceLifecycle,
    Storage,
    _Source,
]:
    favorites = snapshot()
    target = select_favorites_record_target(favorites, target_index, document_index=0)
    accepted = replace(
        observation,
        evidence=FavoritesExternalObservationEvidence(
            datetime(2026, 8, 15, tzinfo=UTC), "accepted-r1"
        ),
        fields=tuple(
            replace(field, value=target.record.fields[binding.field_index])
            for field, binding in zip(
                observation.fields,
                bindings
                or (
                    FavoritesExternalFieldBinding(
                        "name", 2, FavoritesExternalFieldOwnership.EXTERNAL
                    ),
                ),
                strict=True,
            )
        ),
    )
    actual_bindings = bindings or (
        FavoritesExternalFieldBinding(
            "name", 2, FavoritesExternalFieldOwnership.EXTERNAL
        ),
    )
    state = bind_favorites_external_record(target, accepted, actual_bindings)
    path = tmp_path / "state" / "provenance.json"
    save_favorites_external_provenance((state,), path)
    storage = Storage(favorites)
    lifecycle = FavoritesExternalProvenanceLifecycle(storage, path)
    lifecycle.start()
    source = _Source(*(source_values or ((observation,),)))
    return (
        FavoritesExternalAssistedSynchronizationService(lifecycle, source),
        lifecycle,
        storage,
        source,
    )


def test_service_name_acceptance_executes_exact_plan_and_advances_lifecycle(
    tmp_path: Path,
) -> None:
    current = active_observation("channel-1", "Dispatch Updated")
    service, lifecycle, storage, source = _linked_service(tmp_path, current)
    refresh = service.refresh()
    selected = refresh.preview.records[0]
    plan = service.plan_name_acceptance(refresh, selected)
    calls: list[object] = []
    opaque = object()

    def executor(write_plan: object) -> object:
        calls.append(write_plan)
        storage.value = plan.acceptance_plan.write_plan.intended_snapshot
        return opaque

    result = service.execute_name_acceptance(plan, executor)

    assert refresh.observations == (current,)
    assert type(plan) is FavoritesExternalRefreshNameAcceptancePlan
    assert plan.refresh_result is refresh
    assert plan.selected_preview is selected
    assert source.calls == 1
    assert type(result) is FavoritesExternalRefreshNameAcceptanceResult
    assert calls == [plan.acceptance_plan.write_plan]
    assert result.durable_result.execution.execution_result is opaque
    assert storage.value == plan.acceptance_plan.write_plan.intended_snapshot
    assert result.lifecycle_snapshot == lifecycle.snapshot() == service.lifecycle_snapshot
    assert result.lifecycle_snapshot.favorites_snapshot is (
        result.durable_result.execution.observed_snapshot
    )
    assert result.lifecycle_snapshot.provenance_records is (
        result.durable_result.provenance_records
    )
    assert source.calls == 1
    with pytest.raises(ValueError, match="stale or belongs to another"):
        service.plan_name_acceptance(refresh, selected)


def test_service_radioreference_frequency_acceptance_executes_without_reread(
    tmp_path: Path,
) -> None:
    identity = FavoritesExternalRecordIdentity(
        FavoritesExternalSourceIdentity("radioreference", "county-1"),
        "frequency-101",
    )
    observation = FavoritesExternalRecordObservation(
        identity,
        FavoritesExternalObservationEvidence(
            datetime(2026, 8, 16, tzinfo=UTC), "provider-r2"
        ),
        (
            FavoritesExternalFieldObservation(
                "name",
                FavoritesExternalFieldObservationState.VALUE,
                "Synthetic Channel",
            ),
            FavoritesExternalFieldObservation(
                "frequency", FavoritesExternalFieldObservationState.VALUE, "155100000"
            ),
        ),
    )
    bindings = (
        FavoritesExternalFieldBinding("name", 2, FavoritesExternalFieldOwnership.EXTERNAL),
        FavoritesExternalFieldBinding(
            "frequency", 4, FavoritesExternalFieldOwnership.EXTERNAL
        ),
    )
    service, lifecycle, storage, source = _linked_service(
        tmp_path, observation, bindings=bindings
    )
    refresh = service.refresh()
    selected = refresh.preview.records[0]
    mapping = service.radioreference_field_mapping(
        refresh,
        selected,
        observation,
        RadioReferenceFavoritesMappedField.FREQUENCY,
    )
    plan = service.plan_field_acceptance(refresh, selected, mapping)
    calls: list[object] = []
    opaque = object()

    def executor(write_plan: object) -> object:
        calls.append(write_plan)
        storage.value = plan.acceptance_plan.write_plan.intended_snapshot
        return opaque

    result = service.execute_field_acceptance(plan, executor)

    assert mapping.field is observation.fields[1]
    assert mapping.field_index == 4
    assert mapping.scanner_value == "155100000"
    assert type(plan) is FavoritesExternalRefreshFieldAcceptancePlan
    assert plan.mapping is mapping
    assert type(result) is FavoritesExternalRefreshFieldAcceptanceResult
    assert calls == [plan.acceptance_plan.write_plan]
    assert result.durable_result.execution.execution_result is opaque
    assert result.lifecycle_snapshot == lifecycle.snapshot() == service.lifecycle_snapshot
    assert result.lifecycle_snapshot.provenance_records is (
        result.durable_result.provenance_records
    )
    assert source.calls == 1
    with pytest.raises(ValueError, match="stale or belongs to another"):
        service.plan_field_acceptance(refresh, selected, mapping)


@pytest.mark.parametrize(
    ("target_index", "record_id", "fields", "reviewed", "field_index", "value"),
    (
        (
            5,
            "frequency-101",
            (("name", "Dispatch Updated"), ("frequency", "155000000")),
            RadioReferenceFavoritesMappedField.NAME,
            2,
            "Dispatch Updated",
        ),
        (
            5,
            "frequency-101",
            (("name", "Synthetic Channel"), ("frequency", "155100000")),
            RadioReferenceFavoritesMappedField.FREQUENCY,
            4,
            "155100000",
        ),
        (
            14,
            "talkgroup-200",
            (("name", "Talkgroup Updated"), ("decimal", "1000")),
            RadioReferenceFavoritesMappedField.NAME,
            2,
            "Talkgroup Updated",
        ),
        (
            14,
            "talkgroup-200",
            (("name", "Synthetic Dispatch"), ("decimal", "1201")),
            RadioReferenceFavoritesMappedField.TALKGROUP_DECIMAL,
            4,
            "1201",
        ),
    ),
)
def test_service_dispatches_all_reviewed_radioreference_field_combinations(
    tmp_path: Path,
    target_index: int,
    record_id: str,
    fields: tuple[tuple[str, str], ...],
    reviewed: RadioReferenceFavoritesMappedField,
    field_index: int,
    value: str,
) -> None:
    identity = FavoritesExternalRecordIdentity(
        FavoritesExternalSourceIdentity("radioreference", "reviewed-dataset"), record_id
    )
    observation = FavoritesExternalRecordObservation(
        identity,
        FavoritesExternalObservationEvidence(datetime(2026, 8, 16, tzinfo=UTC), "r2"),
        tuple(
            FavoritesExternalFieldObservation(
                name, FavoritesExternalFieldObservationState.VALUE, field_value
            )
            for name, field_value in fields
        ),
    )
    bindings = tuple(
        FavoritesExternalFieldBinding(
            name, 2 if name == "name" else 4, FavoritesExternalFieldOwnership.EXTERNAL
        )
        for name, _ in fields
    )
    service, _lifecycle_value, _storage, source = _linked_service(
        tmp_path, observation, target_index=target_index, bindings=bindings
    )
    refresh = service.refresh()
    selected = refresh.preview.records[0]
    mapping = service.radioreference_field_mapping(
        refresh, selected, observation, reviewed
    )

    assert mapping.field_index == field_index
    assert mapping.scanner_value == value
    assert service.plan_field_acceptance(refresh, selected, mapping).mapping is mapping
    assert source.calls == 1


@pytest.mark.parametrize(
    ("target_index", "record_id", "fields", "unsupported"),
    (
        (
            14,
            "talkgroup-200",
            (("name", "Synthetic Dispatch"), ("decimal", "1201")),
            RadioReferenceFavoritesMappedField.FREQUENCY,
        ),
        (
            5,
            "frequency-101",
            (("name", "Dispatch"), ("frequency", "155100000")),
            RadioReferenceFavoritesMappedField.TALKGROUP_DECIMAL,
        ),
    ),
)
def test_service_rejects_unreviewed_radioreference_field_combinations(
    tmp_path: Path,
    target_index: int,
    record_id: str,
    fields: tuple[tuple[str, str], ...],
    unsupported: RadioReferenceFavoritesMappedField,
) -> None:
    observation = FavoritesExternalRecordObservation(
        FavoritesExternalRecordIdentity(
            FavoritesExternalSourceIdentity("radioreference", "reviewed-dataset"),
            record_id,
        ),
        FavoritesExternalObservationEvidence(datetime(2026, 8, 16, tzinfo=UTC), "r2"),
        tuple(
            FavoritesExternalFieldObservation(
                name, FavoritesExternalFieldObservationState.VALUE, value
            )
            for name, value in fields
        ),
    )
    bindings = tuple(
        FavoritesExternalFieldBinding(
            name, 2 if name == "name" else 4, FavoritesExternalFieldOwnership.EXTERNAL
        )
        for name, _ in fields
    )
    service, _lifecycle_value, _storage, source = _linked_service(
        tmp_path, observation, target_index=target_index, bindings=bindings
    )
    refresh = service.refresh()
    with pytest.raises(ValueError, match="not supported"):
        service.radioreference_field_mapping(
            refresh, refresh.preview.records[0], observation, unsupported
        )
    assert source.calls == 1


def _removal_service(
    tmp_path: Path, *, conflict: bool = False
) -> tuple[FavoritesExternalAssistedSynchronizationService, Storage, _Source]:
    favorites = snapshot()
    state = linked_state(favorites, 5, "remove-me")
    if conflict:
        state = replace(
            state,
            fields=(
                replace(
                    state.fields[0],
                    ownership=FavoritesExternalFieldOwnership.LOCAL,
                    last_external=None,
                ),
            ),
        )
    path = tmp_path / "state" / "provenance.json"
    save_favorites_external_provenance((state,), path)
    storage = Storage(favorites)
    lifecycle = FavoritesExternalProvenanceLifecycle(storage, path)
    lifecycle.start()
    source = _Source((removed_observation("remove-me"),))
    return FavoritesExternalAssistedSynchronizationService(lifecycle, source), storage, source


def test_service_explicit_provider_removal_delete_executes_exact_mutation(
    tmp_path: Path,
) -> None:
    service, storage, source = _removal_service(tmp_path)
    baseline = storage.value
    baseline_provenance = service.lifecycle_snapshot.provenance_records
    refresh = service.refresh()
    selected = refresh.preview.records[0]

    assert selected.kind is FavoritesExternalChangeKind.REMOVED
    assert storage.value is baseline
    assert service.lifecycle_snapshot.provenance_records is baseline_provenance
    plan = service.plan_record_delete(refresh, selected)
    calls: list[object] = []
    opaque = object()

    def executor(write_plan: object) -> object:
        calls.append(write_plan)
        storage.value = plan.write_plan.intended_snapshot
        return opaque

    result = service.execute_record_mutation(plan, executor)

    assert type(plan) is FavoritesExternalRefreshRecordDeletePlan
    assert type(result) is FavoritesExternalRefreshRecordMutationResult
    assert calls == [plan.write_plan]
    assert result.durable_result.execution_result is opaque
    assert storage.value == plan.write_plan.intended_snapshot
    assert result.lifecycle_snapshot == service.lifecycle_snapshot
    assert result.lifecycle_snapshot.favorites_snapshot is result.durable_result.observed_snapshot
    assert result.lifecycle_snapshot.provenance_records == ()
    assert source.calls == 1

    conflict_service, _conflict_storage, conflict_source = _removal_service(
        tmp_path / "conflict", conflict=True
    )
    conflict_refresh = conflict_service.refresh()
    assert conflict_refresh.preview.records[0].kind is FavoritesExternalChangeKind.CONFLICT
    with pytest.raises(ValueError, match="REMOVED"):
        conflict_service.plan_record_delete(
            conflict_refresh, conflict_refresh.preview.records[0]
        )
    assert conflict_source.calls == 1


@pytest.mark.parametrize("conflict", [False, True])
def test_service_explicit_keep_local_detaches_without_favorites_write(
    tmp_path: Path, conflict: bool
) -> None:
    service, storage, source = _removal_service(tmp_path, conflict=conflict)
    exact_bytes = storage.value.documents[0].content
    refresh = service.refresh()
    selected = refresh.preview.records[0]
    plan = service.plan_record_keep_local(refresh, selected)
    result = service.execute_detach(plan)

    assert selected.kind is (
        FavoritesExternalChangeKind.CONFLICT
        if conflict
        else FavoritesExternalChangeKind.REMOVED
    )
    assert type(plan) is FavoritesExternalRefreshDetachPlan
    assert plan.scope is FavoritesExternalRefreshDetachScope.RECORD
    assert type(result) is FavoritesExternalRefreshDetachResult
    assert storage.value.documents[0].content == exact_bytes
    assert result.lifecycle_snapshot == service.lifecycle_snapshot
    assert result.lifecycle_snapshot.provenance_records[0].detached
    assert source.calls == 1
    if conflict:
        with pytest.raises(ValueError, match="stale or belongs to another"):
            service.plan_record_delete(refresh, selected)


def test_service_direct_record_and_field_detach_wrappers_advance_without_write(
    tmp_path: Path,
) -> None:
    current = active_observation("channel-1", "Dispatch Updated")
    service, _lifecycle_value, storage, source = _linked_service(tmp_path, current)
    exact_snapshot = storage.value
    refresh = service.refresh()
    selected = refresh.preview.records[0]
    record_plan = service.plan_detach(
        refresh, selected, FavoritesExternalRefreshDetachScope.RECORD
    )
    field_plan = service.plan_detach(
        refresh,
        selected,
        FavoritesExternalRefreshDetachScope.FIELD,
        field_name="name",
    )
    result = service.execute_detach(field_plan)

    assert type(record_plan) is FavoritesExternalRefreshDetachPlan
    assert record_plan.scope is FavoritesExternalRefreshDetachScope.RECORD
    assert type(field_plan) is FavoritesExternalRefreshDetachPlan
    assert field_plan.scope is FavoritesExternalRefreshDetachScope.FIELD
    assert field_plan.field_name == "name"
    assert type(result) is FavoritesExternalRefreshDetachResult
    assert storage.value is exact_snapshot
    assert storage.value.documents[0].content == exact_snapshot.documents[0].content
    assert result.lifecycle_snapshot == service.lifecycle_snapshot
    assert result.lifecycle_snapshot.provenance_records[0].fields[0].ownership is (
        FavoritesExternalFieldOwnership.DETACHED
    )
    assert source.calls == 1
    with pytest.raises(ValueError, match="stale or belongs to another"):
        service.plan_detach(
            refresh, selected, FavoritesExternalRefreshDetachScope.RECORD
        )


def test_service_sequential_refresh_acceptance_then_detach_reads_only_explicitly(
    tmp_path: Path,
) -> None:
    first_observation = active_observation("channel-1", "Dispatch Updated")
    second_observation = replace(
        first_observation,
        evidence=FavoritesExternalObservationEvidence(
            datetime(2026, 8, 17, tzinfo=UTC), "provider-r3"
        ),
    )
    service, _lifecycle_value, storage, source = _linked_service(
        tmp_path,
        first_observation,
        source_values=((first_observation,), (second_observation,)),
    )
    first = service.refresh()
    assert source.calls == 1
    first_plan = service.plan_name_acceptance(first, first.preview.records[0])

    def executor(write_plan: object) -> object:
        storage.value = first_plan.acceptance_plan.write_plan.intended_snapshot
        return object()

    first_result = service.execute_name_acceptance(first_plan, executor)
    assert source.calls == 1
    with pytest.raises(ValueError, match="stale or belongs to another"):
        service.plan_name_acceptance(first, first.preview.records[0])

    second = service.refresh()
    assert source.calls == 2
    assert second.lifecycle_snapshot == first_result.lifecycle_snapshot
    second_plan = service.plan_detach(
        second,
        second.preview.records[0],
        FavoritesExternalRefreshDetachScope.FIELD,
        field_name="name",
    )
    second_result = service.execute_detach(second_plan)

    assert source.calls == 2
    assert second_result.lifecycle_snapshot == service.lifecycle_snapshot
    assert second_result.lifecycle_snapshot.provenance_records[0].fields[0].ownership is (
        FavoritesExternalFieldOwnership.DETACHED
    )


def test_service_radioreference_dispatch_rejects_malformed_public_inputs(
    tmp_path: Path,
) -> None:
    favorites = snapshot()
    target = select_favorites_record_target(favorites, 5, document_index=0)
    observation = FavoritesExternalRecordObservation(
        FavoritesExternalRecordIdentity(
            FavoritesExternalSourceIdentity("radioreference", "county-1"),
            "frequency-101",
        ),
        FavoritesExternalObservationEvidence(datetime(2026, 8, 16, tzinfo=UTC), "r2"),
        (
            FavoritesExternalFieldObservation(
                "name",
                FavoritesExternalFieldObservationState.VALUE,
                target.record.fields[2],
            ),
        ),
    )
    service, _lifecycle_value, _storage, source = _linked_service(
        tmp_path, observation
    )
    refresh = service.refresh()
    selected = refresh.preview.records[0]

    with pytest.raises(TypeError, match="RadioReferenceFavoritesMappedField"):
        service.radioreference_field_mapping(
            refresh, selected, observation, "name"  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="exact selected preview"):
        service.radioreference_field_mapping(
            refresh,
            object(),  # type: ignore[arg-type]
            observation,
            RadioReferenceFavoritesMappedField.NAME,
        )
    with pytest.raises(TypeError, match="exact observation"):
        service.radioreference_field_mapping(
            refresh,
            selected,
            object(),  # type: ignore[arg-type]
            RadioReferenceFavoritesMappedField.NAME,
        )
    assert source.calls == 1


def test_production_source_factory_is_frozen_slotted_and_lazy() -> None:
    secret_calls: list[str] = []
    configuration = RadioReferenceConfiguration(
        RadioReferenceCredential("operator", "RR_APP", "RR_PASSWORD")
    )
    plan = RadioReferenceObservationRequestPlan(
        FavoritesExternalSourceIdentity("radioreference", "subcategory-7"),
        RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
        (("scid", 7),),
    )
    exchange_factory = RadioReferenceHttpsSoapExchangeFactory()
    factory = RadioReferenceAssistedSynchronizationSourceFactory(
        configuration,
        plan,
        exchange_factory,
        secret_calls.append,
    )

    source = factory()

    assert type(source) is RadioReferenceSource
    assert type(source.session_factory) is RadioReferenceObservationSessionFactory
    assert source.session_factory.exchange_factory is exchange_factory
    assert secret_calls == []
    assert factory() is not source
    assert "secret_resolver" not in repr(factory)
    assert not hasattr(factory, "__dict__")
    with pytest.raises(FrozenInstanceError):
        factory.configuration = configuration  # type: ignore[misc]


def test_production_source_factory_rejects_malformed_composition() -> None:
    configuration = RadioReferenceConfiguration(
        RadioReferenceCredential("operator", "RR_APP", "RR_PASSWORD")
    )
    plan = RadioReferenceObservationRequestPlan(
        FavoritesExternalSourceIdentity("radioreference", "subcategory-7"),
        RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
        (("scid", 7),),
    )
    exchange_factory = RadioReferenceHttpsSoapExchangeFactory()
    with pytest.raises(TypeError, match="exact configuration"):
        RadioReferenceAssistedSynchronizationSourceFactory(
            object(), plan, exchange_factory  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="exact request plan"):
        RadioReferenceAssistedSynchronizationSourceFactory(
            configuration, object(), exchange_factory  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="HTTPS exchange factory"):
        RadioReferenceAssistedSynchronizationSourceFactory(
            configuration, plan, object()  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="must be callable"):
        RadioReferenceAssistedSynchronizationSourceFactory(
            configuration, plan, exchange_factory, object()  # type: ignore[arg-type]
        )


def test_production_factory_full_offline_source_composition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application_key = "synthetic-application-secret"
    password = "synthetic-password-secret"
    resolved: list[str] = []

    def resolver(reference: str) -> str:
        resolved.append(reference)
        return {"RR_APP": application_key, "RR_PASSWORD": password}[reference]

    FakeConnection.instances = []
    FakeConnection.construction_error = None
    FakeConnection.request_error = None
    FakeConnection.response_error = None
    FakeConnection.close_error = None
    FakeConnection.response = FakeResponse(_synthetic_frequency_response())
    monkeypatch.setattr(
        radioreference_http.http.client, "HTTPSConnection", FakeConnection
    )
    configuration = RadioReferenceConfiguration(
        RadioReferenceCredential("operator", "RR_APP", "RR_PASSWORD")
    )
    plan = RadioReferenceObservationRequestPlan(
        FavoritesExternalSourceIdentity("radioreference", "subcategory-7"),
        RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
        (("scid", 7),),
    )
    factory = RadioReferenceAssistedSynchronizationSourceFactory(
        configuration,
        plan,
        RadioReferenceHttpsSoapExchangeFactory(),
        resolver,
    )

    first = factory()
    second = factory()
    assert first is not second
    assert first.session_factory is not second.session_factory
    assert resolved == []
    assert FakeConnection.instances == []

    observations = first.read_observations()

    assert resolved == ["RR_APP", "RR_PASSWORD"]
    assert len(FakeConnection.instances) == 1
    connection = FakeConnection.instances[0]
    assert len(connection.requests) == 1
    method, target, _body, headers = connection.requests[0]
    assert method == "POST"
    assert target == "/soap2/"
    assert headers["SOAPAction"] == f'"{plan.soap_action}"'
    assert len(observations) == 1
    assert observations[0].identity == FavoritesExternalRecordIdentity(
        plan.source, "frequency-101"
    )
    fields = {field.name: field.value for field in observations[0].fields}
    assert fields["name"] == "Dispatch"
    assert fields["frequency"] == "155100000"
    assert FakeConnection.response.closed
    assert connection.closed
    redacted = repr(factory) + repr(first) + repr(observations)
    assert application_key not in redacted
    assert password not in redacted

    with pytest.raises(ValueError, match="RadioReference"):
        RadioReferenceObservationRequestPlan(
            FavoritesExternalSourceIdentity("synthetic-provider", "subcategory-7"),
            RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
            (("scid", 7),),
        )


def test_public_application_api() -> None:
    expected = {
        "FavoritesExternalAssistedSynchronizationService",
        "RadioReferenceAssistedSynchronizationSourceFactory",
        "RadioReferenceFavoritesMappedField",
    }
    assert expected <= set(sds200.__all__)
    assert sds200.FavoritesExternalAssistedSynchronizationService is (
        FavoritesExternalAssistedSynchronizationService
    )
    assert tuple(RadioReferenceFavoritesMappedField) == (
        RadioReferenceFavoritesMappedField.NAME,
        RadioReferenceFavoritesMappedField.FREQUENCY,
        RadioReferenceFavoritesMappedField.TALKGROUP_DECIMAL,
    )
    forbidden = ("sync", "automatic_sync", "import_all", "delete_all")
    assert all(
        not hasattr(FavoritesExternalAssistedSynchronizationService, name)
        for name in forbidden
    )
