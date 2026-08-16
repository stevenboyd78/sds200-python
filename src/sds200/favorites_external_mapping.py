"""Source-neutral external Favorites field representability evidence."""

from __future__ import annotations

from dataclasses import dataclass

from .favorites_editing import FavoritesRecordTarget
from .favorites_external import (
    FavoritesExternalFieldObservation,
    FavoritesExternalFieldObservationState,
    FavoritesExternalRecordObservation,
    FavoritesExternalRecordObservationState,
)


class FavoritesExternalFieldMappingError(ValueError):
    """Report unsupported or inconsistent external field mapping evidence."""


@dataclass(frozen=True, slots=True)
class FavoritesExternalFieldMapping:
    """Retain one exact provider-field to scanner-field representation."""

    target: FavoritesRecordTarget
    observation: FavoritesExternalRecordObservation
    field: FavoritesExternalFieldObservation
    field_index: int
    scanner_value: str

    def __post_init__(self) -> None:
        if not isinstance(self.target, FavoritesRecordTarget):
            raise TypeError(
                "External Favorites field mapping requires FavoritesRecordTarget."
            )
        if not isinstance(
            self.observation,
            FavoritesExternalRecordObservation,
        ):
            raise TypeError(
                "External Favorites field mapping requires "
                "FavoritesExternalRecordObservation."
            )
        if (
            self.observation.state
            is not FavoritesExternalRecordObservationState.ACTIVE
        ):
            raise FavoritesExternalFieldMappingError(
                "External Favorites field mapping requires an active observation."
            )
        if not isinstance(self.field, FavoritesExternalFieldObservation):
            raise TypeError(
                "External Favorites field mapping requires "
                "FavoritesExternalFieldObservation."
            )

        observed_field = next(
            (
                candidate
                for candidate in self.observation.fields
                if candidate.name == self.field.name
            ),
            None,
        )
        if observed_field is not self.field:
            raise FavoritesExternalFieldMappingError(
                "External Favorites mapped field must belong to the exact "
                "retained observation."
            )
        if self.field.state is not FavoritesExternalFieldObservationState.VALUE:
            raise FavoritesExternalFieldMappingError(
                "External Favorites field mapping requires an observed value."
            )

        if type(self.field_index) is not int or self.field_index < 0:
            raise ValueError(
                "External Favorites mapped field index must be a "
                "non-negative integer."
            )
        if self.field_index >= len(self.target.record.fields):
            raise FavoritesExternalFieldMappingError(
                "External Favorites mapped field index is outside the exact "
                "target source record."
            )
        if type(self.scanner_value) is not str:
            raise TypeError(
                "External Favorites mapped scanner value must be a string."
            )


__all__ = [
    "FavoritesExternalFieldMapping",
    "FavoritesExternalFieldMappingError",
]
