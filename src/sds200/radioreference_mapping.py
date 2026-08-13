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
from .radioreference_records import RadioReferenceFrequency

_MHZ_TO_HZ = Decimal(1_000_000)


def _whole_hz_text(frequency_mhz: Decimal) -> str:
    frequency_hz = frequency_mhz * _MHZ_TO_HZ
    if frequency_hz != frequency_hz.to_integral_value():
        raise ValueError(
            "RadioReference frequency cannot be represented as whole Hz "
            "without loss."
        )
    return str(int(frequency_hz))


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
    if not isinstance(source, FavoritesExternalSourceIdentity):
        raise TypeError(
            "RadioReference frequency observation requires "
            "FavoritesExternalSourceIdentity."
        )
    if source.provider != RADIOREFERENCE_PROVIDER:
        raise ValueError(
            "RadioReference frequency observation source provider must be "
            "radioreference."
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


__all__ = [
    "radioreference_frequency_observation",
]
