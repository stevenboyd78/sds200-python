# Offline RadioReference provider-to-external-observation mapping.

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal

from .favorites_editing import (
    FavoritesRecordSourceKind,
    FavoritesRecordTarget,
)
from .favorites_external import (
    FavoritesExternalFieldObservation,
    FavoritesExternalFieldObservationState,
    FavoritesExternalObservationEvidence,
    FavoritesExternalRecordIdentity,
    FavoritesExternalRecordObservation,
    FavoritesExternalRecordObservationState,
    FavoritesExternalSourceIdentity,
)
from .favorites_external_mapping import (
    FavoritesExternalFieldMapping,
    FavoritesExternalFieldMappingError,
)
from .radioreference import RADIOREFERENCE_PROVIDER
from .radioreference_records import (
    RadioReferenceFrequency,
    RadioReferenceTalkgroup,
    RadioReferenceWsdlOperation,
)


def _whole_hz_text(frequency_mhz: Decimal) -> str:
    numerator, denominator = frequency_mhz.as_integer_ratio()
    scaled_numerator = numerator * 1_000_000
    frequency_hz, remainder = divmod(scaled_numerator, denominator)
    if remainder:
        raise ValueError(
            "RadioReference frequency cannot be represented as whole Hz "
            "without loss."
        )
    return str(frequency_hz)


def _require_radioreference_source(
    source: FavoritesExternalSourceIdentity,
    *,
    label: str,
) -> FavoritesExternalSourceIdentity:
    if not isinstance(source, FavoritesExternalSourceIdentity):
        raise TypeError(
            f"{label} requires FavoritesExternalSourceIdentity."
        )
    if source.provider != RADIOREFERENCE_PROVIDER:
        raise ValueError(
            f"{label} source provider must be radioreference."
        )
    return source


def radioreference_frequency_observation(
    frequency: RadioReferenceFrequency,
    *,
    source: FavoritesExternalSourceIdentity,
    observed_at: datetime,
) -> FavoritesExternalRecordObservation:
    # Map one provider conventional frequency into a normalized observation.

    if not isinstance(frequency, RadioReferenceFrequency):
        raise TypeError(
            "RadioReference frequency observation requires "
            "RadioReferenceFrequency."
        )
    _require_radioreference_source(
        source,
        label="RadioReference frequency observation",
    )

    return FavoritesExternalRecordObservation(
        identity=FavoritesExternalRecordIdentity(
            source=source,
            record_id=f"frequency-{frequency.frequency_id}",
        ),
        evidence=FavoritesExternalObservationEvidence(
            observed_at=observed_at,
            revision=None,
        ),
        fields=(
            FavoritesExternalFieldObservation(
                name="name",
                state=FavoritesExternalFieldObservationState.VALUE,
                value=frequency.alpha_tag,
            ),
            FavoritesExternalFieldObservation(
                name="frequency",
                state=FavoritesExternalFieldObservationState.VALUE,
                value=_whole_hz_text(frequency.output_frequency),
            ),
        ),
    )



def radioreference_talkgroup_observation(
    talkgroup: RadioReferenceTalkgroup,
    *,
    source: FavoritesExternalSourceIdentity,
    observed_at: datetime,
) -> FavoritesExternalRecordObservation:
    # Map one provider talkgroup into the reviewed normalized first slice.

    if not isinstance(talkgroup, RadioReferenceTalkgroup):
        raise TypeError(
            "RadioReference talkgroup observation requires "
            "RadioReferenceTalkgroup."
        )
    _require_radioreference_source(
        source,
        label="RadioReference talkgroup observation",
    )

    return FavoritesExternalRecordObservation(
        identity=FavoritesExternalRecordIdentity(
            source=source,
            record_id=f"talkgroup-{talkgroup.talkgroup_id}",
        ),
        evidence=FavoritesExternalObservationEvidence(
            observed_at=observed_at,
            revision=None,
        ),
        fields=(
            FavoritesExternalFieldObservation(
                name="name",
                state=FavoritesExternalFieldObservationState.VALUE,
                value=talkgroup.alpha_tag,
            ),
            FavoritesExternalFieldObservation(
                name="decimal",
                state=FavoritesExternalFieldObservationState.VALUE,
                value=str(talkgroup.decimal),
            ),
        ),
    )


_RADIOREFERENCE_FAVORITES_FREQUENCY_FIELD_INDEX = 4
_RADIOREFERENCE_FAVORITES_NAME_FIELD_INDEX = 2
_RADIOREFERENCE_FAVORITES_TALKGROUP_DECIMAL_FIELD_INDEX = 4
_RADIOREFERENCE_FREQUENCY_RECORD_ID = re.compile(
    r"frequency-(0|[1-9][0-9]*|-[1-9][0-9]*)\Z"
)
_RADIOREFERENCE_TALKGROUP_RECORD_ID = re.compile(
    r"talkgroup-(0|[1-9][0-9]*|-[1-9][0-9]*)\Z"
)
_XSD_INT_MIN = -(2**31)
_XSD_INT_MAX = 2**31 - 1
_CANONICAL_WHOLE_HZ_TEXT = re.compile(r"(?:0|[1-9][0-9]*)\Z")


def _is_radioreference_frequency_record_id(record_id: str) -> bool:
    match = _RADIOREFERENCE_FREQUENCY_RECORD_ID.fullmatch(record_id)
    if match is None:
        return False
    value = int(match.group(1))
    return _XSD_INT_MIN <= value <= _XSD_INT_MAX


def _is_radioreference_talkgroup_record_id(record_id: str) -> bool:
    match = _RADIOREFERENCE_TALKGROUP_RECORD_ID.fullmatch(record_id)
    if match is None:
        return False
    value = int(match.group(1))
    return _XSD_INT_MIN <= value <= _XSD_INT_MAX


def _radioreference_favorites_name_mapping(
    target: FavoritesRecordTarget,
    observation: FavoritesExternalRecordObservation,
    *,
    command: str,
    identity_is_valid: bool,
    identity_label: str,
    label: str,
) -> FavoritesExternalFieldMapping:
    if not isinstance(target, FavoritesRecordTarget):
        raise TypeError(f"{label} requires FavoritesRecordTarget.")
    if not isinstance(observation, FavoritesExternalRecordObservation):
        raise TypeError(
            f"{label} requires FavoritesExternalRecordObservation."
        )
    _require_radioreference_source(observation.identity.source, label=label)
    if observation.state is not FavoritesExternalRecordObservationState.ACTIVE:
        raise FavoritesExternalFieldMappingError(
            f"{label} requires an active observation."
        )
    if not identity_is_valid:
        raise FavoritesExternalFieldMappingError(
            f"{label} requires a reviewed {identity_label} observation."
        )
    if target.source_kind is not FavoritesRecordSourceKind.HPD:
        raise FavoritesExternalFieldMappingError(f"{label} requires an HPD target.")
    if target.record.command != command:
        raise FavoritesExternalFieldMappingError(
            f"{label} requires a {command} target."
        )
    name_field = next(
        (field for field in observation.fields if field.name == "name"),
        None,
    )
    if name_field is None:
        raise FavoritesExternalFieldMappingError(
            f"{label} requires the normalized name field."
        )
    if name_field.state is not FavoritesExternalFieldObservationState.VALUE:
        raise FavoritesExternalFieldMappingError(
            f"{label} requires an observed name value."
        )
    name_value = name_field.value
    assert name_value is not None
    if len(name_value) > 64 or any(
        not 0x20 <= ord(character) <= 0x7E for character in name_value
    ):
        raise FavoritesExternalFieldMappingError(
            f"{label} requires a Favorites Name Tag of at most 64 printable "
            "ASCII characters."
        )
    return FavoritesExternalFieldMapping(
        target=target,
        observation=observation,
        field=name_field,
        field_index=_RADIOREFERENCE_FAVORITES_NAME_FIELD_INDEX,
        scanner_value=name_value,
    )


def radioreference_favorites_frequency_name_mapping(
    target: FavoritesRecordTarget,
    observation: FavoritesExternalRecordObservation,
) -> FavoritesExternalFieldMapping:
    """Map one normalized RR conventional name to the exact C-Freq Name field."""

    return _radioreference_favorites_name_mapping(
        target,
        observation,
        command="C-Freq",
        identity_is_valid=_is_radioreference_frequency_record_id(
            observation.identity.record_id
        )
        if isinstance(observation, FavoritesExternalRecordObservation)
        else False,
        identity_label="conventional frequency",
        label="RadioReference Favorites frequency name mapping",
    )


def radioreference_favorites_talkgroup_name_mapping(
    target: FavoritesRecordTarget,
    observation: FavoritesExternalRecordObservation,
) -> FavoritesExternalFieldMapping:
    """Map one normalized RR talkgroup name to the exact TGID Name field."""

    return _radioreference_favorites_name_mapping(
        target,
        observation,
        command="TGID",
        identity_is_valid=_is_radioreference_talkgroup_record_id(
            observation.identity.record_id
        )
        if isinstance(observation, FavoritesExternalRecordObservation)
        else False,
        identity_label="talkgroup",
        label="RadioReference Favorites talkgroup name mapping",
    )


def radioreference_favorites_talkgroup_decimal_mapping(
    target: FavoritesRecordTarget,
    observation: FavoritesExternalRecordObservation,
) -> FavoritesExternalFieldMapping:
    """Map one normalized RR talkgroup decimal value to the exact TGID ID field."""

    label = "RadioReference Favorites talkgroup decimal mapping"
    if not isinstance(target, FavoritesRecordTarget):
        raise TypeError(f"{label} requires FavoritesRecordTarget.")
    if not isinstance(observation, FavoritesExternalRecordObservation):
        raise TypeError(f"{label} requires FavoritesExternalRecordObservation.")
    _require_radioreference_source(observation.identity.source, label=label)
    if observation.state is not FavoritesExternalRecordObservationState.ACTIVE:
        raise FavoritesExternalFieldMappingError(
            f"{label} requires an active observation."
        )
    if not _is_radioreference_talkgroup_record_id(observation.identity.record_id):
        raise FavoritesExternalFieldMappingError(
            f"{label} requires a reviewed talkgroup observation."
        )
    if target.source_kind is not FavoritesRecordSourceKind.HPD:
        raise FavoritesExternalFieldMappingError(f"{label} requires an HPD target.")
    if target.record.command != "TGID":
        raise FavoritesExternalFieldMappingError(f"{label} requires a TGID target.")
    decimal_field = next(
        (field for field in observation.fields if field.name == "decimal"),
        None,
    )
    if decimal_field is None:
        raise FavoritesExternalFieldMappingError(
            f"{label} requires the normalized decimal field."
        )
    if decimal_field.state is not FavoritesExternalFieldObservationState.VALUE:
        raise FavoritesExternalFieldMappingError(
            f"{label} requires an observed decimal value."
        )
    decimal_value = decimal_field.value
    assert decimal_value is not None
    if (
        _CANONICAL_WHOLE_HZ_TEXT.fullmatch(decimal_value) is None
        or int(decimal_value) > _XSD_INT_MAX
    ):
        raise FavoritesExternalFieldMappingError(
            f"{label} requires canonical non-negative xsd:int decimal text."
        )
    return FavoritesExternalFieldMapping(
        target=target,
        observation=observation,
        field=decimal_field,
        field_index=_RADIOREFERENCE_FAVORITES_TALKGROUP_DECIMAL_FIELD_INDEX,
        scanner_value=decimal_value,
    )


def radioreference_favorites_frequency_mapping(
    target: FavoritesRecordTarget,
    observation: FavoritesExternalRecordObservation,
) -> FavoritesExternalFieldMapping:
    """Map one normalized RR conventional frequency to an exact C-Freq field."""

    if not isinstance(target, FavoritesRecordTarget):
        raise TypeError(
            "RadioReference Favorites frequency mapping requires "
            "FavoritesRecordTarget."
        )
    if not isinstance(observation, FavoritesExternalRecordObservation):
        raise TypeError(
            "RadioReference Favorites frequency mapping requires "
            "FavoritesExternalRecordObservation."
        )
    _require_radioreference_source(
        observation.identity.source,
        label="RadioReference Favorites frequency mapping",
    )

    if observation.state is not FavoritesExternalRecordObservationState.ACTIVE:
        raise FavoritesExternalFieldMappingError(
            "RadioReference Favorites frequency mapping requires an active "
            "observation."
        )
    if not _is_radioreference_frequency_record_id(
        observation.identity.record_id
    ):
        raise FavoritesExternalFieldMappingError(
            "RadioReference Favorites frequency mapping requires a reviewed "
            "conventional frequency observation."
        )
    if target.source_kind is not FavoritesRecordSourceKind.HPD:
        raise FavoritesExternalFieldMappingError(
            "RadioReference Favorites frequency mapping requires an HPD target."
        )
    if target.record.command != "C-Freq":
        raise FavoritesExternalFieldMappingError(
            "RadioReference Favorites frequency mapping requires a C-Freq target."
        )

    frequency_field = next(
        (
            field
            for field in observation.fields
            if field.name == "frequency"
        ),
        None,
    )
    if frequency_field is None:
        raise FavoritesExternalFieldMappingError(
            "RadioReference Favorites frequency mapping requires the normalized "
            "frequency field."
        )
    if frequency_field.state is not FavoritesExternalFieldObservationState.VALUE:
        raise FavoritesExternalFieldMappingError(
            "RadioReference Favorites frequency mapping requires an observed "
            "frequency value."
        )

    frequency_value = frequency_field.value
    assert frequency_value is not None
    if _CANONICAL_WHOLE_HZ_TEXT.fullmatch(frequency_value) is None:
        raise FavoritesExternalFieldMappingError(
            "RadioReference Favorites frequency mapping requires canonical "
            "whole-Hz decimal text."
        )

    return FavoritesExternalFieldMapping(
        target=target,
        observation=observation,
        field=frequency_field,
        field_index=_RADIOREFERENCE_FAVORITES_FREQUENCY_FIELD_INDEX,
        scanner_value=frequency_value,
    )


_FREQUENCY_OBSERVATION_OPERATIONS = frozenset(
    {
        RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
        RadioReferenceWsdlOperation.GET_COUNTY_FREQUENCIES_BY_TAG,
        RadioReferenceWsdlOperation.GET_AGENCY_FREQUENCIES_BY_TAG,
    }
)


def _require_unique_observation_identities(
    observations: tuple[FavoritesExternalRecordObservation, ...],
) -> None:
    identities = tuple(observation.identity for observation in observations)
    if len(set(identities)) != len(identities):
        raise ValueError(
            "RadioReference SOAP result contains duplicate provider "
            "record identities."
        )


def radioreference_soap_result_observations(
    operation: RadioReferenceWsdlOperation,
    result: object,
    *,
    source: FavoritesExternalSourceIdentity,
    observed_at: datetime,
) -> tuple[FavoritesExternalRecordObservation, ...]:
    # Map one reviewed decoded SOAP result without inspecting transport state.

    if not isinstance(operation, RadioReferenceWsdlOperation):
        raise TypeError(
            "RadioReference SOAP result observation adapter requires "
            "RadioReferenceWsdlOperation."
        )
    _require_radioreference_source(
        source,
        label="RadioReference SOAP result observation adapter",
    )

    if operation in _FREQUENCY_OBSERVATION_OPERATIONS:
        if type(result) is not tuple or any(
            not isinstance(item, RadioReferenceFrequency) for item in result
        ):
            raise TypeError(
                "RadioReference frequency SOAP result must be an immutable "
                "tuple of RadioReferenceFrequency values."
            )
        observations = tuple(
            radioreference_frequency_observation(
                frequency,
                source=source,
                observed_at=observed_at,
            )
            for frequency in result
        )
        _require_unique_observation_identities(observations)
        return observations

    if operation is RadioReferenceWsdlOperation.GET_TRUNKED_TALKGROUPS:
        if type(result) is not tuple or any(
            not isinstance(item, RadioReferenceTalkgroup) for item in result
        ):
            raise TypeError(
                "RadioReference talkgroup SOAP result must be an immutable "
                "tuple of RadioReferenceTalkgroup values."
            )
        observations = tuple(
            radioreference_talkgroup_observation(
                talkgroup,
                source=source,
                observed_at=observed_at,
            )
            for talkgroup in result
        )
        _require_unique_observation_identities(observations)
        return observations

    raise ValueError(
        "RadioReference SOAP operation has no reviewed observation mapping."
    )


__all__ = [
    "radioreference_favorites_frequency_mapping",
    "radioreference_favorites_frequency_name_mapping",
    "radioreference_favorites_talkgroup_decimal_mapping",
    "radioreference_favorites_talkgroup_name_mapping",
    "radioreference_frequency_observation",
    "radioreference_soap_result_observations",
    "radioreference_talkgroup_observation",
]
