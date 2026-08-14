from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from decimal import Decimal, localcontext

import pytest

import sds200
from sds200 import (
    FavoritesExternalFieldObservationState,
    FavoritesExternalRecordObservationState,
    FavoritesExternalSourceIdentity,
    RadioReferenceFrequency,
    RadioReferenceTag,
    RadioReferenceTalkgroup,
    RadioReferenceWsdlOperation,
    radioreference_frequency_observation,
    radioreference_soap_result_observations,
    radioreference_talkgroup_observation,
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
) -> RadioReferenceTalkgroup:
    return RadioReferenceTalkgroup(
        talkgroup_id=200,
        decimal=12345,
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
    assert tuple(field.name for field in observation.fields) == ("name",)
    assert observation.fields[0].state is (
        FavoritesExternalFieldObservationState.VALUE
    )
    assert observation.fields[0].value == "Ops"


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


def test_talkgroup_observation_does_not_map_unreviewed_decimal_field() -> None:
    observation = radioreference_talkgroup_observation(
        _talkgroup(),
        source=_source(),
        observed_at=datetime(2026, 8, 13, tzinfo=UTC),
    )

    assert tuple(field.name for field in observation.fields) == ("name",)
    assert all(field.value != "12345" for field in observation.fields)


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
