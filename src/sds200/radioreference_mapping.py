# Offline RadioReference provider-to-external-observation mapping.

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from .favorites_external import (
    FavoritesExternalFieldObservation,
    FavoritesExternalFieldObservationState,
    FavoritesExternalObservationEvidence,
    FavoritesExternalRecordIdentity,
    FavoritesExternalRecordObservation,
    FavoritesExternalSourceIdentity,
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
        ),
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
    "radioreference_frequency_observation",
    "radioreference_soap_result_observations",
    "radioreference_talkgroup_observation",
]
