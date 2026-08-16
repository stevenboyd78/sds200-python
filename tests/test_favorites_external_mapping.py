from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

import sds200
from sds200 import (
    FavoritesExternalFieldMapping,
    FavoritesExternalFieldMappingError,
    FavoritesExternalFieldObservation,
    FavoritesExternalFieldObservationState,
    FavoritesExternalObservationEvidence,
    FavoritesExternalRecordIdentity,
    FavoritesExternalRecordObservation,
    FavoritesExternalRecordObservationState,
    FavoritesExternalSourceIdentity,
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
    select_favorites_record_target,
)

_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "favorites"


def _target():
    snapshot = FavoritesStorageSnapshot(
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
    return select_favorites_record_target(
        snapshot,
        5,
        document_index=0,
    )


def _observation(
    *,
    frequency_state: FavoritesExternalFieldObservationState = (
        FavoritesExternalFieldObservationState.VALUE
    ),
    frequency_value: str | None = "155100000",
    state: FavoritesExternalRecordObservationState = (
        FavoritesExternalRecordObservationState.ACTIVE
    ),
) -> FavoritesExternalRecordObservation:
    fields = (
        ()
        if state is FavoritesExternalRecordObservationState.REMOVED
        else (
            FavoritesExternalFieldObservation(
                name="frequency",
                state=frequency_state,
                value=frequency_value,
            ),
        )
    )
    return FavoritesExternalRecordObservation(
        identity=FavoritesExternalRecordIdentity(
            source=FavoritesExternalSourceIdentity(
                provider="synthetic-provider",
                dataset="metro",
            ),
            record_id="frequency-101",
        ),
        evidence=FavoritesExternalObservationEvidence(
            observed_at=datetime(2026, 8, 16, tzinfo=UTC),
        ),
        fields=fields,
        state=state,
    )


def test_field_mapping_retains_exact_structural_evidence() -> None:
    target = _target()
    observation = _observation()
    field = observation.fields[0]

    mapping = FavoritesExternalFieldMapping(
        target=target,
        observation=observation,
        field=field,
        field_index=4,
        scanner_value="155100000",
    )

    assert mapping.target is target
    assert mapping.observation is observation
    assert mapping.field is field
    assert mapping.field_index == 4
    assert mapping.scanner_value == "155100000"


def test_field_mapping_does_not_require_current_local_value_equality() -> None:
    target = _target()
    observation = _observation()

    mapping = FavoritesExternalFieldMapping(
        target=target,
        observation=observation,
        field=observation.fields[0],
        field_index=4,
        scanner_value="155100000",
    )

    assert target.record.fields[4] == "155000000"
    assert mapping.scanner_value != target.record.fields[4]


def test_field_mapping_is_frozen_and_slot_backed() -> None:
    observation = _observation()
    mapping = FavoritesExternalFieldMapping(
        target=_target(),
        observation=observation,
        field=observation.fields[0],
        field_index=4,
        scanner_value="155100000",
    )

    assert not hasattr(mapping, "__dict__")
    with pytest.raises(FrozenInstanceError):
        mapping.field_index = 5  # type: ignore[misc]


@pytest.mark.parametrize(
    ("kwargs", "match"),
    (
        ({"target": object()}, "FavoritesRecordTarget"),
        ({"observation": object()}, "FavoritesExternalRecordObservation"),
        ({"field": object()}, "FavoritesExternalFieldObservation"),
        ({"scanner_value": 155100000}, "scanner value must be a string"),
    ),
)
def test_field_mapping_rejects_wrong_public_argument_types(
    kwargs: dict[str, object],
    match: str,
) -> None:
    target = _target()
    observation = _observation()
    arguments: dict[str, object] = {
        "target": target,
        "observation": observation,
        "field": observation.fields[0],
        "field_index": 4,
        "scanner_value": "155100000",
    }
    arguments.update(kwargs)

    with pytest.raises(TypeError, match=match):
        FavoritesExternalFieldMapping(**arguments)  # type: ignore[arg-type]


def test_field_mapping_requires_active_observation() -> None:
    observation = _observation(
        state=FavoritesExternalRecordObservationState.REMOVED,
    )
    field = FavoritesExternalFieldObservation(
        name="frequency",
        state=FavoritesExternalFieldObservationState.VALUE,
        value="155100000",
    )

    with pytest.raises(
        FavoritesExternalFieldMappingError,
        match="requires an active observation",
    ):
        FavoritesExternalFieldMapping(
            target=_target(),
            observation=observation,
            field=field,
            field_index=4,
            scanner_value="155100000",
        )


def test_field_mapping_requires_exact_field_from_retained_observation() -> None:
    observation = _observation()
    substituted = replace(observation.fields[0])

    assert substituted == observation.fields[0]
    assert substituted is not observation.fields[0]

    with pytest.raises(
        FavoritesExternalFieldMappingError,
        match="must belong to the exact retained observation",
    ):
        FavoritesExternalFieldMapping(
            target=_target(),
            observation=observation,
            field=substituted,
            field_index=4,
            scanner_value="155100000",
        )


def test_field_mapping_requires_observed_value() -> None:
    observation = _observation(
        frequency_state=FavoritesExternalFieldObservationState.ABSENT,
        frequency_value=None,
    )

    with pytest.raises(
        FavoritesExternalFieldMappingError,
        match="requires an observed value",
    ):
        FavoritesExternalFieldMapping(
            target=_target(),
            observation=observation,
            field=observation.fields[0],
            field_index=4,
            scanner_value="",
        )


@pytest.mark.parametrize("field_index", (-1, True))
def test_field_mapping_rejects_invalid_field_index(field_index: int) -> None:
    observation = _observation()

    with pytest.raises(ValueError, match="non-negative integer"):
        FavoritesExternalFieldMapping(
            target=_target(),
            observation=observation,
            field=observation.fields[0],
            field_index=field_index,
            scanner_value="155100000",
        )


def test_field_mapping_rejects_out_of_range_field_index() -> None:
    observation = _observation()

    with pytest.raises(
        FavoritesExternalFieldMappingError,
        match="outside the exact target source record",
    ):
        FavoritesExternalFieldMapping(
            target=_target(),
            observation=observation,
            field=observation.fields[0],
            field_index=99,
            scanner_value="155100000",
        )


def test_field_mapping_symbols_are_package_exports() -> None:
    assert sds200.FavoritesExternalFieldMapping is FavoritesExternalFieldMapping
    assert (
        sds200.FavoritesExternalFieldMappingError
        is FavoritesExternalFieldMappingError
    )
    assert "FavoritesExternalFieldMapping" in sds200.__all__
    assert "FavoritesExternalFieldMappingError" in sds200.__all__
