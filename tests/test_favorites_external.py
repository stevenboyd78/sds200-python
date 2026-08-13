from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

import sds200
from sds200 import (
    FavoritesExternalChangeKind,
    FavoritesExternalFieldObservation,
    FavoritesExternalFieldObservationState,
    FavoritesExternalFieldOwnership,
    FavoritesExternalFieldState,
    FavoritesExternalImportError,
    FavoritesExternalObservationEvidence,
    FavoritesExternalRecordIdentity,
    FavoritesExternalRecordObservation,
    FavoritesExternalRecordObservationState,
    FavoritesExternalRecordState,
    FavoritesExternalSourceIdentity,
    FavoritesRecordSourceKind,
    FavoritesRecordTarget,
    FavoritesSourceRecord,
    detach_favorites_external_field,
    detach_favorites_external_record,
    preview_favorites_external_import,
    preview_favorites_external_source,
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
        "FavoritesExternalChangeKind",
        "FavoritesExternalFieldObservation",
        "FavoritesExternalFieldObservationState",
        "FavoritesExternalFieldOwnership",
        "FavoritesExternalFieldPreview",
        "FavoritesExternalFieldState",
        "FavoritesExternalImportError",
        "FavoritesExternalImportPreview",
        "FavoritesExternalObservationEvidence",
        "FavoritesExternalRecordIdentity",
        "FavoritesExternalRecordObservation",
        "FavoritesExternalRecordObservationState",
        "FavoritesExternalRecordPreview",
        "FavoritesExternalRecordState",
        "FavoritesExternalSource",
        "FavoritesExternalSourceIdentity",
        "detach_favorites_external_field",
        "detach_favorites_external_record",
        "preview_favorites_external_import",
        "preview_favorites_external_source",
    )

    for name in expected:
        assert name in sds200.__all__
        assert hasattr(sds200, name)
