"""Offline RadioReference observation request planning and session composition."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .favorites_external import FavoritesExternalSourceIdentity
from .radioreference import RADIOREFERENCE_PROVIDER
from .radioreference_records import (
    RADIOREFERENCE_AUTH_INFO_TYPE,
    RadioReferenceWsdlOperation,
    radioreference_operation_contract,
)

_XSD_INT_MIN: Final = -(2**31)
_XSD_INT_MAX: Final = 2**31 - 1

_OBSERVATION_OPERATIONS: Final = frozenset(
    {
        RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
        RadioReferenceWsdlOperation.GET_COUNTY_FREQUENCIES_BY_TAG,
        RadioReferenceWsdlOperation.GET_AGENCY_FREQUENCIES_BY_TAG,
        RadioReferenceWsdlOperation.GET_TRUNKED_TALKGROUPS,
    }
)


def _require_observation_source(
    source: FavoritesExternalSourceIdentity,
) -> FavoritesExternalSourceIdentity:
    if not isinstance(source, FavoritesExternalSourceIdentity):
        raise TypeError(
            "RadioReference observation request plan requires "
            "FavoritesExternalSourceIdentity."
        )
    if source.provider != RADIOREFERENCE_PROVIDER:
        raise ValueError(
            "RadioReference observation request plan source provider must be "
            "radioreference."
        )
    return source


def _expected_parameters(
    operation: RadioReferenceWsdlOperation,
) -> tuple[tuple[str, str], ...]:
    contract = radioreference_operation_contract(operation)
    return tuple(
        (parameter.name, parameter.type_name)
        for parameter in contract.request_parameters
        if parameter.type_name != RADIOREFERENCE_AUTH_INFO_TYPE
    )


def _validate_parameter_value(
    name: str,
    type_name: str,
    value: object,
) -> None:
    if type_name != "xsd:int":
        raise ValueError(
            "RadioReference observation request plan encountered an "
            "unsupported reviewed parameter type."
        )
    if type(value) is not int:
        raise TypeError(
            f"RadioReference observation request parameter {name!r} must be "
            "an xsd:int-compatible integer."
        )
    if not _XSD_INT_MIN <= value <= _XSD_INT_MAX:
        raise ValueError(
            f"RadioReference observation request parameter {name!r} is "
            "outside the xsd:int range."
        )


@dataclass(frozen=True, slots=True)
class RadioReferenceObservationRequestPlan:
    """Bind one normalized dataset to one reviewed observation SOAP request."""

    source: FavoritesExternalSourceIdentity
    operation: RadioReferenceWsdlOperation
    parameters: tuple[tuple[str, object], ...]

    def __post_init__(self) -> None:
        _require_observation_source(self.source)
        if not isinstance(self.operation, RadioReferenceWsdlOperation):
            raise TypeError(
                "RadioReference observation request plan requires "
                "RadioReferenceWsdlOperation."
            )
        if self.operation not in _OBSERVATION_OPERATIONS:
            raise ValueError(
                "RadioReference SOAP operation has no reviewed observation "
                "request plan."
            )
        if type(self.parameters) is not tuple:
            raise TypeError(
                "RadioReference observation request parameters must be an "
                "immutable tuple."
            )
        if any(
            type(parameter) is not tuple or len(parameter) != 2
            for parameter in self.parameters
        ):
            raise TypeError(
                "RadioReference observation request parameters must contain "
                "immutable (name, value) tuples."
            )

        names = tuple(parameter[0] for parameter in self.parameters)
        if any(type(name) is not str for name in names):
            raise TypeError(
                "RadioReference observation request parameter names must be "
                "strings."
            )
        if len(set(names)) != len(names):
            raise ValueError(
                "RadioReference observation request parameters contain "
                "duplicate names."
            )

        expected = _expected_parameters(self.operation)
        expected_names = tuple(name for name, _type_name in expected)
        if names != expected_names:
            raise ValueError(
                "RadioReference observation request parameters must exactly "
                "match reviewed WSDL order."
            )

        for (name, value), (_expected_name, type_name) in zip(
            self.parameters,
            expected,
            strict=True,
        ):
            _validate_parameter_value(name, type_name, value)

    @property
    def soap_action(self) -> str:
        """Return the reviewed SOAPAction without transport behavior."""

        return radioreference_operation_contract(self.operation).soap_action

    def parameter_mapping(self) -> dict[str, object]:
        """Return one fresh serializer mapping without changing the plan."""

        return dict(self.parameters)


__all__ = [
    "RadioReferenceObservationRequestPlan",
]
