"""Offline RadioReference observation request planning and session composition."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final, Protocol, TypeAlias

from .favorites_external import (
    FavoritesExternalRecordObservation,
    FavoritesExternalSourceIdentity,
)
from .radioreference import (
    RADIOREFERENCE_PROVIDER,
    RadioReferenceConfiguration,
    RadioReferenceError,
    RadioReferenceErrorReason,
)
from .radioreference_mapping import radioreference_soap_result_observations
from .radioreference_records import (
    RADIOREFERENCE_AUTH_INFO_TYPE,
    RadioReferenceWsdlOperation,
    radioreference_operation_contract,
)
from .radioreference_soap import RadioReferenceSoapDecoder
from .radioreference_soap_request import RadioReferenceSoapRequestSerializer

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


class RadioReferenceSoapExchange(Protocol):
    """Fakeable byte exchange without defining production HTTP/TLS behavior."""

    def exchange(
        self,
        operation: RadioReferenceWsdlOperation,
        request: bytes,
        *,
        soap_action: str,
    ) -> bytes:
        """Return exact SOAP response bytes for one ephemeral request."""
        ...

    def close(self) -> None:
        """Close exchange-owned resources deterministically."""
        ...


RadioReferenceSoapExchangeFactory: TypeAlias = Callable[
    [],
    RadioReferenceSoapExchange,
]
RadioReferenceObservationClock: TypeAlias = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _require_aware_datetime(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(
            "RadioReference observation wall clock must return datetime."
        )
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(
            "RadioReference observation wall clock must return a "
            "timezone-aware datetime."
        )
    return value


def _fresh_error(reason: RadioReferenceErrorReason) -> RadioReferenceError:
    return RadioReferenceError(reason)


class RadioReferenceObservationSession:
    """Compose one reviewed request plan through offline SOAP boundaries."""

    __slots__ = (
        "configuration",
        "plan",
        "_application_key",
        "_closed",
        "_decoder",
        "_exchange",
        "_now",
        "_password",
        "_serializer",
    )

    def __init__(
        self,
        configuration: RadioReferenceConfiguration,
        plan: RadioReferenceObservationRequestPlan,
        exchange: RadioReferenceSoapExchange,
        *,
        application_key: str,
        password: str,
        serializer: RadioReferenceSoapRequestSerializer | None = None,
        decoder: RadioReferenceSoapDecoder | None = None,
        now: RadioReferenceObservationClock = _utc_now,
    ) -> None:
        if not isinstance(configuration, RadioReferenceConfiguration):
            raise TypeError(
                "RadioReference observation session requires "
                "RadioReferenceConfiguration."
            )
        if not isinstance(plan, RadioReferenceObservationRequestPlan):
            raise TypeError(
                "RadioReference observation session requires "
                "RadioReferenceObservationRequestPlan."
            )
        if not callable(getattr(exchange, "exchange", None)):
            raise TypeError(
                "RadioReference observation session exchange must provide "
                "exchange()."
            )
        if not callable(getattr(exchange, "close", None)):
            raise TypeError(
                "RadioReference observation session exchange must provide "
                "close()."
            )
        if type(application_key) is not str:
            raise TypeError(
                "RadioReference observation session application key must be "
                "a string."
            )
        if not application_key:
            raise ValueError(
                "RadioReference observation session application key must not "
                "be empty."
            )
        if type(password) is not str:
            raise TypeError(
                "RadioReference observation session password must be a string."
            )
        if not password:
            raise ValueError(
                "RadioReference observation session password must not be empty."
            )
        selected_serializer = (
            RadioReferenceSoapRequestSerializer()
            if serializer is None
            else serializer
        )
        if not isinstance(
            selected_serializer,
            RadioReferenceSoapRequestSerializer,
        ):
            raise TypeError(
                "RadioReference observation session requires "
                "RadioReferenceSoapRequestSerializer."
            )
        selected_decoder = (
            RadioReferenceSoapDecoder()
            if decoder is None
            else decoder
        )
        if not isinstance(selected_decoder, RadioReferenceSoapDecoder):
            raise TypeError(
                "RadioReference observation session requires "
                "RadioReferenceSoapDecoder."
            )
        if not callable(now):
            raise TypeError(
                "RadioReference observation session wall clock must be "
                "callable."
            )

        self.configuration = configuration
        self.plan = plan
        self._exchange = exchange
        self._serializer = selected_serializer
        self._decoder = selected_decoder
        self._now = now
        self._application_key = application_key
        self._password = password
        self._closed = False

    @property
    def closed(self) -> bool:
        """Report whether this owned offline composition is closed."""

        return self._closed

    def read_observations(
        self,
    ) -> tuple[FavoritesExternalRecordObservation, ...]:
        """Serialize, exchange, decode, and normalize one reviewed dataset read."""

        if self._closed:
            raise _fresh_error(RadioReferenceErrorReason.SERVICE_FAILED)

        observed_at = _require_aware_datetime(self._now())
        request = b""
        response = b""
        try:
            try:
                request = self._serializer.serialize(
                    self.plan.operation,
                    self.plan.parameter_mapping(),
                    self.configuration,
                    application_key=self._application_key,
                    password=self._password,
                )
            except RadioReferenceError as error:
                raise _fresh_error(error.reason) from None
            except Exception:
                raise _fresh_error(
                    RadioReferenceErrorReason.SERVICE_FAILED
                ) from None

            try:
                response = self._exchange.exchange(
                    self.plan.operation,
                    request,
                    soap_action=self.plan.soap_action,
                )
            except RadioReferenceError as error:
                raise _fresh_error(error.reason) from None
            except Exception:
                raise _fresh_error(
                    RadioReferenceErrorReason.SERVICE_FAILED
                ) from None

            if type(response) is not bytes:
                raise _fresh_error(
                    RadioReferenceErrorReason.INVALID_RESPONSE
                )

            try:
                decoded = self._decoder.decode(
                    self.plan.operation,
                    response,
                )
            except RadioReferenceError as error:
                raise _fresh_error(error.reason) from None
            except Exception:
                raise _fresh_error(
                    RadioReferenceErrorReason.INVALID_RESPONSE
                ) from None

            try:
                return radioreference_soap_result_observations(
                    self.plan.operation,
                    decoded,
                    source=self.plan.source,
                    observed_at=observed_at,
                )
            except RadioReferenceError as error:
                raise _fresh_error(error.reason) from None
            except Exception:
                raise _fresh_error(
                    RadioReferenceErrorReason.INVALID_RESPONSE
                ) from None
        finally:
            request = b""
            response = b""

    def close(self) -> None:
        """Clear secret references and close the fakeable exchange once."""

        if self._closed:
            return

        self._closed = True
        self._application_key = ""
        self._password = ""
        try:
            self._exchange.close()
        except Exception:
            raise _fresh_error(
                RadioReferenceErrorReason.CLEANUP_FAILED
            ) from None


@dataclass(frozen=True, slots=True)
class RadioReferenceObservationSessionFactory:
    """Build owned offline observation sessions for RadioReferenceSource."""

    plan: RadioReferenceObservationRequestPlan
    exchange_factory: RadioReferenceSoapExchangeFactory
    serializer: RadioReferenceSoapRequestSerializer = field(
        default_factory=RadioReferenceSoapRequestSerializer
    )
    decoder: RadioReferenceSoapDecoder = field(
        default_factory=RadioReferenceSoapDecoder
    )
    now: RadioReferenceObservationClock = _utc_now

    def __post_init__(self) -> None:
        if not isinstance(self.plan, RadioReferenceObservationRequestPlan):
            raise TypeError(
                "RadioReference observation session factory requires "
                "RadioReferenceObservationRequestPlan."
            )
        if not callable(self.exchange_factory):
            raise TypeError(
                "RadioReference SOAP exchange factory must be callable."
            )
        if not isinstance(
            self.serializer,
            RadioReferenceSoapRequestSerializer,
        ):
            raise TypeError(
                "RadioReference observation session factory requires "
                "RadioReferenceSoapRequestSerializer."
            )
        if not isinstance(self.decoder, RadioReferenceSoapDecoder):
            raise TypeError(
                "RadioReference observation session factory requires "
                "RadioReferenceSoapDecoder."
            )
        if not callable(self.now):
            raise TypeError(
                "RadioReference observation session factory wall clock must "
                "be callable."
            )

    def __call__(
        self,
        configuration: RadioReferenceConfiguration,
        *,
        application_key: str,
        password: str,
    ) -> RadioReferenceObservationSession:
        try:
            exchange = self.exchange_factory()
        except RadioReferenceError as error:
            raise _fresh_error(error.reason) from None
        except Exception:
            raise _fresh_error(
                RadioReferenceErrorReason.CONNECTION_FAILED
            ) from None

        try:
            return RadioReferenceObservationSession(
                configuration,
                self.plan,
                exchange,
                application_key=application_key,
                password=password,
                serializer=self.serializer,
                decoder=self.decoder,
                now=self.now,
            )
        except Exception:
            with suppress(Exception):
                exchange.close()
            raise


__all__ = [
    "RadioReferenceObservationClock",
    "RadioReferenceObservationRequestPlan",
    "RadioReferenceObservationSession",
    "RadioReferenceObservationSessionFactory",
    "RadioReferenceSoapExchange",
    "RadioReferenceSoapExchangeFactory",
]
