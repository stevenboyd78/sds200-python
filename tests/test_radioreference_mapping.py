from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from decimal import Decimal, localcontext
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
    FavoritesExternalRecordState,
    FavoritesExternalSourceIdentity,
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
    RadioReferenceFrequency,
    RadioReferenceTag,
    RadioReferenceTalkgroup,
    RadioReferenceWsdlOperation,
    plan_favorites_external_field_acceptance,
    radioreference_favorites_frequency_mapping,
    radioreference_favorites_frequency_name_mapping,
    radioreference_favorites_talkgroup_decimal_mapping,
    radioreference_favorites_talkgroup_name_mapping,
    radioreference_frequency_observation,
    radioreference_soap_result_observations,
    radioreference_talkgroup_observation,
    select_favorites_record_target,
)

_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "favorites"


def _favorites_snapshot() -> FavoritesStorageSnapshot:
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


def _conventional_channel_target():
    return select_favorites_record_target(
        _favorites_snapshot(),
        5,
        document_index=0,
    )


def _talkgroup_channel_target():
    return select_favorites_record_target(
        _favorites_snapshot(),
        14,
        document_index=0,
    )



def _frequency(
    *,
    alpha_tag: str = "Dispatch",
    description: str = "Dispatch description",
    output_frequency: Decimal | None = None,
) -> RadioReferenceFrequency:
    resolved_output_frequency = (
        Decimal("155.1000")
        if output_frequency is None
        else output_frequency
    )
    return RadioReferenceFrequency(
        frequency_id=101,
        output_frequency=resolved_output_frequency,
        input_frequency=Decimal("0"),
        callsign="WXYZ123",
        description=description,
        alpha_tag=alpha_tag,
        tone="123.0 PL",
        color_code="",
        talkgroup="",
        slot="",
        mode="FMN",
        encryption=0,
        class_code="PW",
        tags=(RadioReferenceTag(tag_id=2, description="Fire Dispatch"),),
        subcategory_id=7,
        sort=10,
        last_updated=datetime(2026, 8, 13, 9, 21, 4),
    )



def _talkgroup(
    *,
    alpha_tag: str = "Ops",
    description: str = "Operations",
    decimal: int = 12345,
    talkgroup_id: int = 200,
) -> RadioReferenceTalkgroup:
    return RadioReferenceTalkgroup(
        talkgroup_id=talkgroup_id,
        decimal=decimal,
        subfleet="",
        ltr=False,
        slot="",
        description=description,
        alpha_tag=alpha_tag,
        mode="D",
        encryption=0,
        tags=(RadioReferenceTag(tag_id=2, description="Fire Dispatch"),),
        category_id=30,
        sort=1,
        date=datetime(2026, 8, 13, 9, 21, 4),
    )


def _source(
    *,
    provider: str = "radioreference",
    dataset: str = "synthetic-county",
) -> FavoritesExternalSourceIdentity:
    return FavoritesExternalSourceIdentity(
        provider=provider,
        dataset=dataset,
    )


def test_frequency_observation_maps_only_reviewed_first_slice() -> None:
    source = _source()
    observed_at = datetime(2026, 8, 13, 13, 45, tzinfo=UTC)

    observation = radioreference_frequency_observation(
        _frequency(),
        source=source,
        observed_at=observed_at,
    )

    assert observation.identity.source is source
    assert observation.identity.record_id == "frequency-101"
    assert observation.evidence.observed_at is observed_at
    assert observation.evidence.revision is None
    assert observation.state is FavoritesExternalRecordObservationState.ACTIVE
    assert tuple(field.name for field in observation.fields) == (
        "name",
        "frequency",
    )
    assert all(
        field.state is FavoritesExternalFieldObservationState.VALUE
        for field in observation.fields
    )
    assert tuple(field.value for field in observation.fields) == (
        "Dispatch",
        "155100000",
    )


def test_frequency_observation_preserves_alpha_tag_without_fallback() -> None:
    observation = radioreference_frequency_observation(
        _frequency(alpha_tag="", description="Do not use this description"),
        source=_source(),
        observed_at=datetime(2026, 8, 13, tzinfo=UTC),
    )

    assert observation.fields[0].name == "name"
    assert observation.fields[0].value == ""


def test_frequency_observation_preserves_padded_alpha_tag_value() -> None:
    observation = radioreference_frequency_observation(
        _frequency(alpha_tag=" Dispatch "),
        source=_source(),
        observed_at=datetime(2026, 8, 13, tzinfo=UTC),
    )

    assert observation.fields[0].value == " Dispatch "


@pytest.mark.parametrize(
    ("frequency_mhz", "expected_hz"),
    (
        (Decimal("0"), "0"),
        (Decimal("155.1000"), "155100000"),
        (Decimal("851.0125"), "851012500"),
        (Decimal("769.431250"), "769431250"),
    ),
)
def test_frequency_observation_converts_exact_mhz_to_whole_hz(
    frequency_mhz: Decimal,
    expected_hz: str,
) -> None:
    observation = radioreference_frequency_observation(
        _frequency(output_frequency=frequency_mhz),
        source=_source(),
        observed_at=datetime(2026, 8, 13, tzinfo=UTC),
    )

    assert observation.fields[1].value == expected_hz


def test_frequency_observation_rejects_fractional_hz_without_rounding() -> None:
    frequency = replace(
        _frequency(),
        output_frequency=Decimal("155.1000001"),
    )

    with pytest.raises(
        ValueError,
        match="cannot be represented as whole Hz without loss",
    ):
        radioreference_frequency_observation(
            frequency,
            source=_source(),
            observed_at=datetime(2026, 8, 13, tzinfo=UTC),
        )


def test_frequency_observation_fractional_hz_rejection_is_context_independent() -> None:
    frequency = replace(
        _frequency(),
        output_frequency=Decimal("155.1000001"),
    )

    with localcontext() as context:
        context.prec = 8
        with pytest.raises(
            ValueError,
            match="cannot be represented as whole Hz without loss",
        ):
            radioreference_frequency_observation(
                frequency,
                source=_source(),
                observed_at=datetime(2026, 8, 13, tzinfo=UTC),
            )


def test_frequency_observation_does_not_treat_last_updated_as_revision() -> None:
    frequency = replace(
        _frequency(),
        last_updated=datetime(2030, 1, 2, 3, 4, 5),
    )

    observation = radioreference_frequency_observation(
        frequency,
        source=_source(),
        observed_at=datetime(2026, 8, 13, tzinfo=UTC),
    )

    assert observation.evidence.revision is None


def test_frequency_observation_requires_radioreference_source() -> None:
    with pytest.raises(
        ValueError,
        match="source provider must be radioreference",
    ):
        radioreference_frequency_observation(
            _frequency(),
            source=_source(provider="other-provider"),
            observed_at=datetime(2026, 8, 13, tzinfo=UTC),
        )


def test_frequency_observation_requires_timezone_aware_observation_time() -> None:
    with pytest.raises(
        ValueError,
        match="observation time must be timezone-aware",
    ):
        radioreference_frequency_observation(
            _frequency(),
            source=_source(),
            observed_at=datetime(2026, 8, 13),
        )


def test_frequency_observation_rejects_wrong_argument_types() -> None:
    with pytest.raises(
        TypeError,
        match="requires RadioReferenceFrequency",
    ):
        radioreference_frequency_observation(
            object(),  # type: ignore[arg-type]
            source=_source(),
            observed_at=datetime(2026, 8, 13, tzinfo=UTC),
        )

    with pytest.raises(
        TypeError,
        match="requires FavoritesExternalSourceIdentity",
    ):
        radioreference_frequency_observation(
            _frequency(),
            source=object(),  # type: ignore[arg-type]
            observed_at=datetime(2026, 8, 13, tzinfo=UTC),
        )


def test_frequency_observation_is_immutable() -> None:
    observation = radioreference_frequency_observation(
        _frequency(),
        source=_source(),
        observed_at=datetime(2026, 8, 13, tzinfo=UTC),
    )

    with pytest.raises(FrozenInstanceError):
        observation.fields = ()  # type: ignore[misc]




def test_rr_frequency_maps_to_exact_c_freq_frequency_field() -> None:
    target = _conventional_channel_target()
    observation = radioreference_frequency_observation(
        _frequency(output_frequency=Decimal("155.1000")),
        source=_source(),
        observed_at=datetime(2026, 8, 16, tzinfo=UTC),
    )

    mapping = radioreference_favorites_frequency_mapping(
        target,
        observation,
    )

    assert isinstance(mapping, FavoritesExternalFieldMapping)
    assert mapping.target is target
    assert mapping.observation is observation
    assert mapping.field is observation.fields[1]
    assert mapping.field.name == "frequency"
    assert mapping.field_index == 4
    assert mapping.scanner_value == "155100000"
    assert target.record.command == "C-Freq"
    assert target.record.fields[4] == "155000000"


def test_rr_frequency_mapping_accepts_already_equal_local_value() -> None:
    target = _conventional_channel_target()
    observation = radioreference_frequency_observation(
        _frequency(output_frequency=Decimal("155.0000")),
        source=_source(),
        observed_at=datetime(2026, 8, 16, tzinfo=UTC),
    )

    mapping = radioreference_favorites_frequency_mapping(
        target,
        observation,
    )

    assert mapping.scanner_value == target.record.fields[4]


def test_rr_frequency_mapping_requires_hpd_c_freq_target() -> None:
    snapshot = _favorites_snapshot()
    observation = radioreference_frequency_observation(
        _frequency(),
        source=_source(),
        observed_at=datetime(2026, 8, 16, tzinfo=UTC),
    )
    catalog_target = select_favorites_record_target(snapshot, 2)
    group_target = select_favorites_record_target(
        snapshot,
        4,
        document_index=0,
    )

    with pytest.raises(
        FavoritesExternalFieldMappingError,
        match="requires an HPD target",
    ):
        radioreference_favorites_frequency_mapping(
            catalog_target,
            observation,
        )

    with pytest.raises(
        FavoritesExternalFieldMappingError,
        match="requires a C-Freq target",
    ):
        radioreference_favorites_frequency_mapping(
            group_target,
            observation,
        )


def test_rr_frequency_mapping_requires_radioreference_source() -> None:
    observation = radioreference_frequency_observation(
        _frequency(),
        source=_source(),
        observed_at=datetime(2026, 8, 16, tzinfo=UTC),
    )
    other_source = FavoritesExternalSourceIdentity(
        provider="other-provider",
        dataset=observation.identity.source.dataset,
    )
    substituted = replace(
        observation,
        identity=replace(
            observation.identity,
            source=other_source,
        ),
    )

    with pytest.raises(
        ValueError,
        match="source provider must be radioreference",
    ):
        radioreference_favorites_frequency_mapping(
            _conventional_channel_target(),
            substituted,
        )


def test_rr_frequency_mapping_requires_conventional_frequency_identity() -> None:
    observation = FavoritesExternalRecordObservation(
        identity=FavoritesExternalRecordIdentity(
            source=_source(),
            record_id="talkgroup-200",
        ),
        evidence=FavoritesExternalObservationEvidence(
            observed_at=datetime(2026, 8, 16, tzinfo=UTC),
        ),
        fields=(
            FavoritesExternalFieldObservation(
                name="frequency",
                state=FavoritesExternalFieldObservationState.VALUE,
                value="155100000",
            ),
        ),
    )

    with pytest.raises(
        FavoritesExternalFieldMappingError,
        match="requires a reviewed conventional frequency observation",
    ):
        radioreference_favorites_frequency_mapping(
            _conventional_channel_target(),
            observation,
        )


@pytest.mark.parametrize(
    "record_id",
    (
        "frequency-0",
        "frequency-101",
        "frequency--1",
        "frequency--2147483648",
        "frequency-2147483647",
    ),
)
def test_rr_frequency_mapping_accepts_reviewed_frequency_identity_shape(
    record_id: str,
) -> None:
    observation = FavoritesExternalRecordObservation(
        identity=FavoritesExternalRecordIdentity(
            source=_source(),
            record_id=record_id,
        ),
        evidence=FavoritesExternalObservationEvidence(
            observed_at=datetime(2026, 8, 16, tzinfo=UTC),
        ),
        fields=(
            FavoritesExternalFieldObservation(
                name="frequency",
                state=FavoritesExternalFieldObservationState.VALUE,
                value="155100000",
            ),
        ),
    )

    mapping = radioreference_favorites_frequency_mapping(
        _conventional_channel_target(),
        observation,
    )

    assert mapping.observation is observation


@pytest.mark.parametrize(
    "record_id",
    (
        "frequency--0",
        "frequency-01",
        "frequency--01",
        "frequency-2147483648",
        "frequency--2147483649",
        "frequency-999999999999",
        "frequency--999999999999",
    ),
)
def test_rr_frequency_mapping_rejects_impossible_frequency_identity(
    record_id: str,
) -> None:
    observation = FavoritesExternalRecordObservation(
        identity=FavoritesExternalRecordIdentity(
            source=_source(),
            record_id=record_id,
        ),
        evidence=FavoritesExternalObservationEvidence(
            observed_at=datetime(2026, 8, 16, tzinfo=UTC),
        ),
        fields=(
            FavoritesExternalFieldObservation(
                name="frequency",
                state=FavoritesExternalFieldObservationState.VALUE,
                value="155100000",
            ),
        ),
    )

    with pytest.raises(
        FavoritesExternalFieldMappingError,
        match="requires a reviewed conventional frequency observation",
    ):
        radioreference_favorites_frequency_mapping(
            _conventional_channel_target(),
            observation,
        )


def test_rr_frequency_mapping_requires_active_observation() -> None:
    active = radioreference_frequency_observation(
        _frequency(),
        source=_source(),
        observed_at=datetime(2026, 8, 16, tzinfo=UTC),
    )
    removed = FavoritesExternalRecordObservation(
        identity=active.identity,
        evidence=active.evidence,
        state=FavoritesExternalRecordObservationState.REMOVED,
    )

    with pytest.raises(
        FavoritesExternalFieldMappingError,
        match="requires an active observation",
    ):
        radioreference_favorites_frequency_mapping(
            _conventional_channel_target(),
            removed,
        )


def test_rr_frequency_mapping_requires_normalized_frequency_value() -> None:
    missing = FavoritesExternalRecordObservation(
        identity=FavoritesExternalRecordIdentity(
            source=_source(),
            record_id="frequency-101",
        ),
        evidence=FavoritesExternalObservationEvidence(
            observed_at=datetime(2026, 8, 16, tzinfo=UTC),
        ),
        fields=(
            FavoritesExternalFieldObservation(
                name="name",
                state=FavoritesExternalFieldObservationState.VALUE,
                value="Dispatch",
            ),
        ),
    )

    with pytest.raises(
        FavoritesExternalFieldMappingError,
        match="requires the normalized frequency field",
    ):
        radioreference_favorites_frequency_mapping(
            _conventional_channel_target(),
            missing,
        )

    absent = FavoritesExternalRecordObservation(
        identity=FavoritesExternalRecordIdentity(
            source=_source(),
            record_id="frequency-101",
        ),
        evidence=FavoritesExternalObservationEvidence(
            observed_at=datetime(2026, 8, 16, tzinfo=UTC),
        ),
        fields=(
            FavoritesExternalFieldObservation(
                name="frequency",
                state=FavoritesExternalFieldObservationState.ABSENT,
            ),
        ),
    )
    with pytest.raises(
        FavoritesExternalFieldMappingError,
        match="requires an observed frequency value",
    ):
        radioreference_favorites_frequency_mapping(
            _conventional_channel_target(),
            absent,
        )


@pytest.mark.parametrize(
    "value",
    (
        "",
        "0155100000",
        "+155100000",
        "155.1000",
        "155100000 ",
        " 155100000",
    ),
)
def test_rr_frequency_mapping_rejects_noncanonical_whole_hz_text(
    value: str,
) -> None:
    observation = FavoritesExternalRecordObservation(
        identity=FavoritesExternalRecordIdentity(
            source=_source(),
            record_id="frequency-101",
        ),
        evidence=FavoritesExternalObservationEvidence(
            observed_at=datetime(2026, 8, 16, tzinfo=UTC),
        ),
        fields=(
            FavoritesExternalFieldObservation(
                name="frequency",
                state=FavoritesExternalFieldObservationState.VALUE,
                value=value,
            ),
        ),
    )

    with pytest.raises(
        FavoritesExternalFieldMappingError,
        match="canonical whole-Hz decimal text",
    ):
        radioreference_favorites_frequency_mapping(
            _conventional_channel_target(),
            observation,
        )


def test_rr_frequency_mapping_rejects_wrong_argument_types() -> None:
    observation = radioreference_frequency_observation(
        _frequency(),
        source=_source(),
        observed_at=datetime(2026, 8, 16, tzinfo=UTC),
    )

    with pytest.raises(TypeError, match="FavoritesRecordTarget"):
        radioreference_favorites_frequency_mapping(  # type: ignore[arg-type]
            object(),
            observation,
        )

    with pytest.raises(TypeError, match="FavoritesExternalRecordObservation"):
        radioreference_favorites_frequency_mapping(  # type: ignore[arg-type]
            _conventional_channel_target(),
            object(),
        )


def test_rr_frequency_mapping_symbol_is_package_export() -> None:
    assert (
        sds200.radioreference_favorites_frequency_mapping
        is radioreference_favorites_frequency_mapping
    )
    assert "radioreference_favorites_frequency_mapping" in sds200.__all__



def test_talkgroup_observation_maps_only_reviewed_first_slice() -> None:
    source = _source()
    observed_at = datetime(2026, 8, 13, 13, 45, tzinfo=UTC)

    observation = radioreference_talkgroup_observation(
        _talkgroup(),
        source=source,
        observed_at=observed_at,
    )

    assert observation.identity.source is source
    assert observation.identity.record_id == "talkgroup-200"
    assert observation.evidence.observed_at is observed_at
    assert observation.evidence.revision is None
    assert observation.state is FavoritesExternalRecordObservationState.ACTIVE
    assert tuple(field.name for field in observation.fields) == (
        "name",
        "decimal",
    )
    assert observation.fields[0].state is (
        FavoritesExternalFieldObservationState.VALUE
    )
    assert observation.fields[0].value == "Ops"
    assert observation.fields[1].state is (
        FavoritesExternalFieldObservationState.VALUE
    )
    assert observation.fields[1].value == "12345"


def test_talkgroup_observation_preserves_alpha_tag_without_fallback() -> None:
    observation = radioreference_talkgroup_observation(
        _talkgroup(alpha_tag="", description="Do not use this description"),
        source=_source(),
        observed_at=datetime(2026, 8, 13, tzinfo=UTC),
    )

    assert observation.fields[0].value == ""


def test_talkgroup_observation_preserves_padded_alpha_tag_value() -> None:
    observation = radioreference_talkgroup_observation(
        _talkgroup(alpha_tag=" Ops "),
        source=_source(),
        observed_at=datetime(2026, 8, 13, tzinfo=UTC),
    )

    assert observation.fields[0].value == " Ops "


@pytest.mark.parametrize(("decimal", "expected"), ((12345, "12345"), (0, "0"), (-1, "-1")))
def test_talkgroup_observation_maps_canonical_decimal_independent_of_identity(
    decimal: int,
    expected: str,
) -> None:
    observation = radioreference_talkgroup_observation(
        _talkgroup(decimal=decimal, talkgroup_id=200),
        source=_source(),
        observed_at=datetime(2026, 8, 13, tzinfo=UTC),
    )

    assert tuple(field.name for field in observation.fields) == ("name", "decimal")
    assert observation.fields[1].value == expected
    assert observation.fields[1].value != observation.identity.record_id.removeprefix(
        "talkgroup-"
    )


def test_talkgroup_observation_does_not_treat_date_as_revision() -> None:
    observation = radioreference_talkgroup_observation(
        replace(
            _talkgroup(),
            date=datetime(2030, 1, 2, 3, 4, 5),
        ),
        source=_source(),
        observed_at=datetime(2026, 8, 13, tzinfo=UTC),
    )

    assert observation.evidence.revision is None


def test_talkgroup_observation_requires_radioreference_source() -> None:
    with pytest.raises(
        ValueError,
        match="source provider must be radioreference",
    ):
        radioreference_talkgroup_observation(
            _talkgroup(),
            source=_source(provider="other-provider"),
            observed_at=datetime(2026, 8, 13, tzinfo=UTC),
        )


def test_talkgroup_observation_requires_timezone_aware_observation_time() -> None:
    with pytest.raises(
        ValueError,
        match="observation time must be timezone-aware",
    ):
        radioreference_talkgroup_observation(
            _talkgroup(),
            source=_source(),
            observed_at=datetime(2026, 8, 13),
        )


def test_talkgroup_observation_rejects_wrong_argument_type() -> None:
    with pytest.raises(
        TypeError,
        match="requires RadioReferenceTalkgroup",
    ):
        radioreference_talkgroup_observation(
            object(),  # type: ignore[arg-type]
            source=_source(),
            observed_at=datetime(2026, 8, 13, tzinfo=UTC),
        )


def test_talkgroup_mapping_symbol_is_package_export() -> None:
    assert (
        sds200.radioreference_talkgroup_observation
        is radioreference_talkgroup_observation
    )


def test_radioreference_mapping_symbol_is_package_export() -> None:
    assert (
        sds200.radioreference_frequency_observation
        is radioreference_frequency_observation
    )

@pytest.mark.parametrize(
    "operation",
    (
        RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
        RadioReferenceWsdlOperation.GET_COUNTY_FREQUENCIES_BY_TAG,
        RadioReferenceWsdlOperation.GET_AGENCY_FREQUENCIES_BY_TAG,
    ),
)
def test_soap_result_adapter_maps_reviewed_frequency_operations(
    operation: RadioReferenceWsdlOperation,
) -> None:
    source = _source(dataset="synthetic-frequency-result")
    observed_at = datetime(2026, 8, 14, 11, 30, tzinfo=UTC)

    observations = radioreference_soap_result_observations(
        operation,
        (
            replace(_frequency(), frequency_id=202, alpha_tag="Second"),
            replace(_frequency(), frequency_id=101, alpha_tag="First"),
        ),
        source=source,
        observed_at=observed_at,
    )

    assert [item.identity.record_id for item in observations] == [
        "frequency-202",
        "frequency-101",
    ]
    assert all(item.identity.source is source for item in observations)
    assert all(item.evidence.observed_at is observed_at for item in observations)


def test_soap_result_adapter_maps_reviewed_talkgroup_operation() -> None:
    source = _source(dataset="synthetic-talkgroup-result")
    observed_at = datetime(2026, 8, 14, 11, 30, tzinfo=UTC)

    observations = radioreference_soap_result_observations(
        RadioReferenceWsdlOperation.GET_TRUNKED_TALKGROUPS,
        (
            replace(_talkgroup(), talkgroup_id=300, alpha_tag="Third"),
            replace(_talkgroup(), talkgroup_id=200, alpha_tag="Second"),
        ),
        source=source,
        observed_at=observed_at,
    )

    assert [item.identity.record_id for item in observations] == [
        "talkgroup-300",
        "talkgroup-200",
    ]
    assert [item.fields[0].value for item in observations] == [
        "Third",
        "Second",
    ]
    assert [item.fields[1].value for item in observations] == [
        "12345",
        "12345",
    ]


def _reconstructed_observation(
    *,
    record_id: str,
    name: str | None = "Provider Name",
    decimal: str | None = "12345",
    state: FavoritesExternalRecordObservationState = (
        FavoritesExternalRecordObservationState.ACTIVE
    ),
    provider: str = "radioreference",
) -> FavoritesExternalRecordObservation:
    fields = []
    if name is not None:
        fields.append(
            FavoritesExternalFieldObservation(
                name="name",
                state=FavoritesExternalFieldObservationState.VALUE,
                value=name,
            )
        )
    if decimal is not None:
        fields.append(
            FavoritesExternalFieldObservation(
                name="decimal",
                state=FavoritesExternalFieldObservationState.VALUE,
                value=decimal,
            )
        )
    return FavoritesExternalRecordObservation(
        identity=FavoritesExternalRecordIdentity(
            source=_source(provider=provider),
            record_id=record_id,
        ),
        evidence=FavoritesExternalObservationEvidence(
            observed_at=datetime(2026, 8, 16, tzinfo=UTC),
        ),
        fields=tuple(fields),
        state=state,
    )


@pytest.mark.parametrize(
    ("value", "accepted"),
    (("", True), (" " * 64, True), (" padded ", True), ("x" * 65, False),
     ("bad\n", False), ("café", False)),
)
@pytest.mark.parametrize(
    ("mapper", "target", "record_id"),
    (
        (
            radioreference_favorites_frequency_name_mapping,
            _conventional_channel_target,
            "frequency-101",
        ),
        (
            radioreference_favorites_talkgroup_name_mapping,
            _talkgroup_channel_target,
            "talkgroup-200",
        ),
    ),
)
def test_rr_name_mappings_preserve_exact_favorites_name_tag_domain(
    value: str,
    accepted: bool,
    mapper,
    target,
    record_id: str,
) -> None:
    observation = _reconstructed_observation(record_id=record_id, name=value)
    if not accepted:
        with pytest.raises(FavoritesExternalFieldMappingError, match="Name Tag"):
            mapper(target(), observation)
        return

    mapping = mapper(target(), observation)
    assert mapping.field is observation.fields[0]
    assert mapping.field_index == 2
    assert mapping.scanner_value == value


@pytest.mark.parametrize(
    "record_id",
    ("talkgroup-0", "talkgroup-1", "talkgroup--1", "talkgroup-2147483647", "talkgroup--2147483648"),
)
def test_rr_talkgroup_name_mapping_accepts_canonical_xsd_int_identity(record_id: str) -> None:
    mapping = radioreference_favorites_talkgroup_name_mapping(
        _talkgroup_channel_target(), _reconstructed_observation(record_id=record_id)
    )
    assert mapping.scanner_value == "Provider Name"


@pytest.mark.parametrize(
    "record_id",
    ("frequency-1", "talkgroup--0", "talkgroup-01", "talkgroup--01",
     "talkgroup-2147483648", "talkgroup--2147483649", "talkgroup-x"),
)
def test_rr_talkgroup_mappings_reject_unreviewed_identity(record_id: str) -> None:
    observation = _reconstructed_observation(record_id=record_id)
    for mapper in (
        radioreference_favorites_talkgroup_name_mapping,
        radioreference_favorites_talkgroup_decimal_mapping,
    ):
        with pytest.raises(FavoritesExternalFieldMappingError, match="reviewed talkgroup"):
            mapper(_talkgroup_channel_target(), observation)


@pytest.mark.parametrize("value", ("0", "1", "2147483647"))
def test_rr_talkgroup_decimal_mapping_accepts_scanner_domain(value: str) -> None:
    observation = _reconstructed_observation(record_id="talkgroup-200", decimal=value)
    mapping = radioreference_favorites_talkgroup_decimal_mapping(
        _talkgroup_channel_target(), observation
    )
    assert mapping.field is observation.fields[1]
    assert mapping.field_index == 4
    assert mapping.scanner_value == value
    assert mapping.scanner_value != "200"


@pytest.mark.parametrize(
    "value",
    ("-1", "01", "+1", " 1", "1 ", "1.0", "1e0", "2147483648", ""),
)
def test_rr_talkgroup_decimal_mapping_rejects_unrepresentable_text(value: str) -> None:
    with pytest.raises(FavoritesExternalFieldMappingError, match="canonical non-negative"):
        radioreference_favorites_talkgroup_decimal_mapping(
            _talkgroup_channel_target(),
            _reconstructed_observation(record_id="talkgroup-200", decimal=value),
        )


def test_rr_talkgroup_negative_dto_observation_is_valid_but_unrepresentable() -> None:
    observation = radioreference_talkgroup_observation(
        _talkgroup(decimal=-2147483648),
        source=_source(),
        observed_at=datetime(2026, 8, 16, tzinfo=UTC),
    )
    assert observation.fields[1].value == "-2147483648"
    with pytest.raises(FavoritesExternalFieldMappingError):
        radioreference_favorites_talkgroup_decimal_mapping(
            _talkgroup_channel_target(), observation
        )


def test_checkpoint_d_mapping_symbols_are_package_exports() -> None:
    for name, symbol in (
        (
            "radioreference_favorites_frequency_name_mapping",
            radioreference_favorites_frequency_name_mapping,
        ),
        (
            "radioreference_favorites_talkgroup_name_mapping",
            radioreference_favorites_talkgroup_name_mapping,
        ),
        (
            "radioreference_favorites_talkgroup_decimal_mapping",
            radioreference_favorites_talkgroup_decimal_mapping,
        ),
    ):
        assert getattr(sds200, name) is symbol
        assert name in sds200.__all__


@pytest.mark.parametrize(
    ("mapper", "target_factory", "observation"),
    (
        (
            radioreference_favorites_frequency_name_mapping,
            _conventional_channel_target,
            _reconstructed_observation(record_id="frequency-101"),
        ),
        (
            radioreference_favorites_talkgroup_name_mapping,
            _talkgroup_channel_target,
            _reconstructed_observation(record_id="talkgroup-200"),
        ),
        (
            radioreference_favorites_talkgroup_decimal_mapping,
            _talkgroup_channel_target,
            _reconstructed_observation(record_id="talkgroup-200"),
        ),
    ),
)
def test_checkpoint_d_mappings_compose_as_one_selected_field(
    mapper,
    target_factory,
    observation: FavoritesExternalRecordObservation,
) -> None:
    snapshot = _favorites_snapshot()
    target = target_factory()
    mapping = mapper(target, observation)
    record = FavoritesExternalRecordState(
        target=target,
        fields=(),
        external_identity=observation.identity,
        last_observation=FavoritesExternalObservationEvidence(
            observed_at=datetime(2026, 8, 15, tzinfo=UTC),
        ),
    )

    plan = plan_favorites_external_field_acceptance(snapshot, record, mapping)

    assert plan.mapping is mapping
    assert plan.intended_state.target.record.fields[mapping.field_index] == (
        mapping.scanner_value
    )
    assert tuple(field.name for field in plan.intended_state.fields) == (
        mapping.field.name,
    )


@pytest.mark.parametrize(
    "operation",
    (
        RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
        RadioReferenceWsdlOperation.GET_COUNTY_FREQUENCIES_BY_TAG,
        RadioReferenceWsdlOperation.GET_AGENCY_FREQUENCIES_BY_TAG,
        RadioReferenceWsdlOperation.GET_TRUNKED_TALKGROUPS,
    ),
)
def test_soap_result_adapter_preserves_supported_empty_tuple(
    operation: RadioReferenceWsdlOperation,
) -> None:
    observations = radioreference_soap_result_observations(
        operation,
        (),
        source=_source(),
        observed_at=datetime(2026, 8, 14, tzinfo=UTC),
    )

    assert observations == ()
    assert type(observations) is tuple


@pytest.mark.parametrize(
    "operation,result,error_fragment",
    (
        (
            RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
            [_frequency()],
            "frequency SOAP result",
        ),
        (
            RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
            (_talkgroup(),),
            "frequency SOAP result",
        ),
        (
            RadioReferenceWsdlOperation.GET_TRUNKED_TALKGROUPS,
            [_talkgroup()],
            "talkgroup SOAP result",
        ),
        (
            RadioReferenceWsdlOperation.GET_TRUNKED_TALKGROUPS,
            (_frequency(),),
            "talkgroup SOAP result",
        ),
    ),
)
def test_soap_result_adapter_rejects_mismatched_supported_result_shape(
    operation: RadioReferenceWsdlOperation,
    result: object,
    error_fragment: str,
) -> None:
    with pytest.raises(TypeError, match=error_fragment):
        radioreference_soap_result_observations(
            operation,
            result,
            source=_source(),
            observed_at=datetime(2026, 8, 14, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    "operation",
    (
        RadioReferenceWsdlOperation.SEARCH_COUNTY_FREQUENCY,
        RadioReferenceWsdlOperation.SEARCH_STATE_FREQUENCY,
        RadioReferenceWsdlOperation.SEARCH_METRO_FREQUENCY,
        RadioReferenceWsdlOperation.GET_COUNTRY_INFO,
        RadioReferenceWsdlOperation.GET_TRUNKED_SYSTEM_DETAILS,
    ),
)
def test_soap_result_adapter_fails_closed_for_unreviewed_operation(
    operation: RadioReferenceWsdlOperation,
) -> None:
    with pytest.raises(
        ValueError,
        match="no reviewed observation mapping",
    ):
        radioreference_soap_result_observations(
            operation,
            (),
            source=_source(),
            observed_at=datetime(2026, 8, 14, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    ("operation", "result"),
    (
        (
            RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
            (
                _frequency(),
                replace(_frequency(), alpha_tag="Duplicate ID"),
            ),
        ),
        (
            RadioReferenceWsdlOperation.GET_TRUNKED_TALKGROUPS,
            (
                _talkgroup(),
                replace(_talkgroup(), alpha_tag="Duplicate ID"),
            ),
        ),
    ),
)
def test_soap_result_adapter_rejects_duplicate_provider_identity(
    operation: RadioReferenceWsdlOperation,
    result: object,
) -> None:
    with pytest.raises(
        ValueError,
        match="duplicate provider record identities",
    ):
        radioreference_soap_result_observations(
            operation,
            result,
            source=_source(),
            observed_at=datetime(2026, 8, 14, tzinfo=UTC),
        )


def test_soap_result_adapter_rejects_wrong_operation_type() -> None:
    with pytest.raises(
        TypeError,
        match="requires RadioReferenceWsdlOperation",
    ):
        radioreference_soap_result_observations(
            object(),  # type: ignore[arg-type]
            (),
            source=_source(),
            observed_at=datetime(2026, 8, 14, tzinfo=UTC),
        )


def test_soap_result_adapter_requires_radioreference_source() -> None:
    with pytest.raises(
        ValueError,
        match="source provider must be radioreference",
    ):
        radioreference_soap_result_observations(
            RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
            (),
            source=_source(provider="other-provider"),
            observed_at=datetime(2026, 8, 14, tzinfo=UTC),
        )


def test_soap_result_adapter_requires_timezone_aware_observation_time() -> None:
    with pytest.raises(
        ValueError,
        match="observation time must be timezone-aware",
    ):
        radioreference_soap_result_observations(
            RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
            (_frequency(),),
            source=_source(),
            observed_at=datetime(2026, 8, 14),
        )


def test_soap_result_adapter_symbol_is_package_export() -> None:
    assert (
        sds200.radioreference_soap_result_observations
        is radioreference_soap_result_observations
    )
