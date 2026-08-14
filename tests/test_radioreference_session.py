from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

import sds200
from sds200 import (
    FavoritesExternalSourceIdentity,
    RadioReferenceConfiguration,
    RadioReferenceCredential,
    RadioReferenceError,
    RadioReferenceErrorReason,
    RadioReferenceObservationRequestPlan,
    RadioReferenceObservationSession,
    RadioReferenceObservationSessionFactory,
    RadioReferenceSoapExchange,
    RadioReferenceSource,
    RadioReferenceWsdlOperation,
    radioreference_operation_contract,
)


def _source(
    *,
    provider: str = "radioreference",
    dataset: str = "synthetic-subcategory",
) -> FavoritesExternalSourceIdentity:
    return FavoritesExternalSourceIdentity(
        provider=provider,
        dataset=dataset,
    )


@pytest.mark.parametrize(
    ("operation", "parameters"),
    (
        (
            RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
            (("scid", 7),),
        ),
        (
            RadioReferenceWsdlOperation.GET_COUNTY_FREQUENCIES_BY_TAG,
            (("ctid", 3), ("tag", 2)),
        ),
        (
            RadioReferenceWsdlOperation.GET_AGENCY_FREQUENCIES_BY_TAG,
            (("aid", 9), ("tag", 2)),
        ),
        (
            RadioReferenceWsdlOperation.GET_TRUNKED_TALKGROUPS,
            (("sid", 22), ("tgCid", 0), ("tgTag", 0), ("tgDec", 0)),
        ),
    ),
)
def test_observation_request_plan_accepts_reviewed_operation_contract(
    operation: RadioReferenceWsdlOperation,
    parameters: tuple[tuple[str, object], ...],
) -> None:
    source = _source()
    plan = RadioReferenceObservationRequestPlan(
        source=source,
        operation=operation,
        parameters=parameters,
    )

    assert plan.source is source
    assert plan.operation is operation
    assert plan.parameters == parameters
    assert plan.soap_action == radioreference_operation_contract(
        operation
    ).soap_action


def test_observation_request_plan_is_immutable() -> None:
    plan = RadioReferenceObservationRequestPlan(
        source=_source(),
        operation=RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
        parameters=(("scid", 7),),
    )

    with pytest.raises(FrozenInstanceError):
        plan.parameters = (("scid", 8),)  # type: ignore[misc]


def test_observation_request_plan_returns_fresh_parameter_mapping() -> None:
    plan = RadioReferenceObservationRequestPlan(
        source=_source(),
        operation=RadioReferenceWsdlOperation.GET_COUNTY_FREQUENCIES_BY_TAG,
        parameters=(("ctid", 3), ("tag", 2)),
    )

    first = plan.parameter_mapping()
    second = plan.parameter_mapping()

    assert first == {"ctid": 3, "tag": 2}
    assert second == first
    assert second is not first

    first["ctid"] = 99
    assert plan.parameters == (("ctid", 3), ("tag", 2))


def test_observation_request_plan_requires_radioreference_source() -> None:
    with pytest.raises(
        ValueError,
        match="source provider must be radioreference",
    ):
        RadioReferenceObservationRequestPlan(
            source=_source(provider="other-provider"),
            operation=RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
            parameters=(("scid", 7),),
        )


def test_observation_request_plan_requires_source_identity_type() -> None:
    with pytest.raises(
        TypeError,
        match="FavoritesExternalSourceIdentity",
    ):
        RadioReferenceObservationRequestPlan(
            source=object(),  # type: ignore[arg-type]
            operation=RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
            parameters=(("scid", 7),),
        )


def test_observation_request_plan_requires_typed_operation() -> None:
    with pytest.raises(
        TypeError,
        match="RadioReferenceWsdlOperation",
    ):
        RadioReferenceObservationRequestPlan(
            source=_source(),
            operation="getSubcatFreqs",  # type: ignore[arg-type]
            parameters=(("scid", 7),),
        )


@pytest.mark.parametrize(
    "operation",
    (
        RadioReferenceWsdlOperation.GET_COUNTRY_INFO,
        RadioReferenceWsdlOperation.GET_COUNTY_INFO,
        RadioReferenceWsdlOperation.SEARCH_COUNTY_FREQUENCY,
        RadioReferenceWsdlOperation.GET_TRUNKED_SYSTEM_DETAILS,
    ),
)
def test_observation_request_plan_rejects_unmapped_operations(
    operation: RadioReferenceWsdlOperation,
) -> None:
    with pytest.raises(
        ValueError,
        match="no reviewed observation request plan",
    ):
        RadioReferenceObservationRequestPlan(
            source=_source(),
            operation=operation,
            parameters=(),
        )


def test_observation_request_plan_requires_exact_tuple_container() -> None:
    with pytest.raises(TypeError, match="immutable tuple"):
        RadioReferenceObservationRequestPlan(
            source=_source(),
            operation=RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
            parameters=[("scid", 7)],  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "parameters",
    (
        (["scid", 7],),
        (("scid",),),
        (("scid", 7, 8),),
    ),
)
def test_observation_request_plan_requires_exact_parameter_pairs(
    parameters: object,
) -> None:
    with pytest.raises(TypeError, match=r"\(name, value\) tuples"):
        RadioReferenceObservationRequestPlan(
            source=_source(),
            operation=RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
            parameters=parameters,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "parameters",
    (
        (),
        (("tag", 2), ("ctid", 3)),
        (("ctid", 3),),
        (("ctid", 3), ("tag", 2), ("extra", 4)),
    ),
)
def test_observation_request_plan_requires_exact_wsdl_parameter_order(
    parameters: tuple[tuple[str, object], ...],
) -> None:
    with pytest.raises(ValueError, match="exactly match reviewed WSDL order"):
        RadioReferenceObservationRequestPlan(
            source=_source(),
            operation=RadioReferenceWsdlOperation.GET_COUNTY_FREQUENCIES_BY_TAG,
            parameters=parameters,
        )


def test_observation_request_plan_rejects_duplicate_parameter_names() -> None:
    with pytest.raises(ValueError, match="duplicate names"):
        RadioReferenceObservationRequestPlan(
            source=_source(),
            operation=RadioReferenceWsdlOperation.GET_COUNTY_FREQUENCIES_BY_TAG,
            parameters=(("ctid", 3), ("ctid", 2)),
        )


@pytest.mark.parametrize("value", (True, 7.0, "7", None))
def test_observation_request_plan_requires_exact_xsd_int_type(
    value: object,
) -> None:
    with pytest.raises(TypeError, match="xsd:int-compatible integer"):
        RadioReferenceObservationRequestPlan(
            source=_source(),
            operation=RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
            parameters=(("scid", value),),
        )


@pytest.mark.parametrize("value", (-(2**31) - 1, 2**31))
def test_observation_request_plan_rejects_xsd_int_outside_range(
    value: int,
) -> None:
    with pytest.raises(ValueError, match="outside the xsd:int range"):
        RadioReferenceObservationRequestPlan(
            source=_source(),
            operation=RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
            parameters=(("scid", value),),
        )


def test_observation_request_plan_is_secret_free_by_shape() -> None:
    plan = RadioReferenceObservationRequestPlan(
        source=_source(),
        operation=RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
        parameters=(("scid", 7),),
    )

    assert not hasattr(plan, "application_key")
    assert not hasattr(plan, "password")
    assert not hasattr(plan, "request")
    assert not hasattr(plan, "request_bytes")


def test_observation_request_plan_symbol_is_package_export() -> None:
    assert (
        sds200.RadioReferenceObservationRequestPlan
        is RadioReferenceObservationRequestPlan
    )
    assert "RadioReferenceObservationRequestPlan" in sds200.__all__


_SOAP = "http://schemas.xmlsoap.org/soap/envelope/"
_ENC = "http://schemas.xmlsoap.org/soap/encoding/"
_XSI = "http://www.w3.org/2001/XMLSchema-instance"
_XSD = "http://www.w3.org/2001/XMLSchema"
_RR = "http://api.radioreference.com/soap2"
_TEST_SECRET_A = "synthetic-secret-a"
_TEST_SECRET_B = "synthetic-secret-b"


def _configuration() -> RadioReferenceConfiguration:
    return RadioReferenceConfiguration(
        credential=RadioReferenceCredential(
            username="synthetic-user",
            application_key_environment_variable="RR_APP_KEY",
            password_environment_variable="RR_PASSWORD",
        )
    )


def _frequency_response() -> bytes:
    operation = RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES
    tag = (
        '<item xsi:type="tns:tag">'
        "<tagId>2</tagId>"
        "<tagDescr>Fire Dispatch</tagDescr>"
        "</item>"
    )
    frequency = (
        '<item xsi:type="tns:freq">'
        "<fid>101</fid>"
        "<out>155.1000</out>"
        "<in>0</in>"
        "<callsign>WXYZ123</callsign>"
        "<descr>Dispatch description</descr>"
        "<alpha>Dispatch</alpha>"
        "<tone>123.0 PL</tone>"
        "<colorCode></colorCode>"
        "<tg></tg>"
        "<slot></slot>"
        "<mode>FMN</mode>"
        "<enc>0</enc>"
        "<class>PW</class>"
        '<tags xsi:type="enc:Array" enc:arrayType="tns:tag[1]">'
        f"{tag}"
        "</tags>"
        "<scid>7</scid>"
        "<sort>10</sort>"
        "<lastUpdated>2026-08-13T09:21:04Z</lastUpdated>"
        "</item>"
    )
    return (
        f'<soap:Envelope xmlns:soap="{_SOAP}" xmlns:enc="{_ENC}" '
        f'xmlns:xsi="{_XSI}" xmlns:xsd="{_XSD}" xmlns:tns="{_RR}">'
        "<soap:Body>"
        f"<tns:{operation.value}Response>"
        '<return xsi:type="enc:Array" enc:arrayType="tns:freq[1]">'
        f"{frequency}"
        "</return>"
        f"</tns:{operation.value}Response>"
        "</soap:Body>"
        "</soap:Envelope>"
    ).encode()


class FakeSoapExchange:
    def __init__(
        self,
        response: object,
        *,
        error: BaseException | None = None,
        close_error: BaseException | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.close_error = close_error
        self.calls: list[
            tuple[RadioReferenceWsdlOperation, bytes, str]
        ] = []
        self.closed = False

    def exchange(
        self,
        operation: RadioReferenceWsdlOperation,
        request: bytes,
        *,
        soap_action: str,
    ) -> bytes:
        self.calls.append((operation, request, soap_action))
        if self.error is not None:
            raise self.error
        return self.response  # type: ignore[return-value]

    def close(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


def _frequency_plan() -> RadioReferenceObservationRequestPlan:
    return RadioReferenceObservationRequestPlan(
        source=_source(dataset="synthetic-subcategory"),
        operation=RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
        parameters=(("scid", 7),),
    )


def _session(
    exchange: RadioReferenceSoapExchange,
    *,
    now: object = None,
) -> RadioReferenceObservationSession:
    kwargs: dict[str, object] = {}
    if now is not None:
        kwargs["now"] = now
    return RadioReferenceObservationSession(
        _configuration(),
        _frequency_plan(),
        exchange,
        application_key=_TEST_SECRET_A,
        password=_TEST_SECRET_B,
        **kwargs,  # type: ignore[arg-type]
    )


def test_observation_session_composes_serializer_exchange_decoder_and_mapper() -> None:
    observed_at = datetime(2026, 8, 14, 14, 45, tzinfo=UTC)
    exchange = FakeSoapExchange(_frequency_response())
    session = _session(exchange, now=lambda: observed_at)

    observations = session.read_observations()

    assert len(observations) == 1
    observation = observations[0]
    assert observation.identity.source == _frequency_plan().source
    assert observation.identity.record_id == "frequency-101"
    assert observation.evidence.observed_at is observed_at
    assert tuple(
        (field.name, field.value)
        for field in observation.fields
    ) == (
        ("name", "Dispatch"),
        ("frequency", "155100000"),
    )

    assert len(exchange.calls) == 1
    operation, request, soap_action = exchange.calls[0]
    assert operation is RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES
    assert soap_action == _frequency_plan().soap_action
    assert b"<scid" in request
    assert b">7</" in request
    assert _TEST_SECRET_A.encode() in request
    assert _TEST_SECRET_B.encode() in request
    assert not hasattr(session, "request")
    assert not hasattr(session, "request_bytes")
    assert not hasattr(session, "response")
    assert not hasattr(session, "response_bytes")


def test_observation_session_factory_integrates_with_existing_source_boundary() -> None:
    observed_at = datetime(2026, 8, 14, 14, 46, tzinfo=UTC)
    exchange = FakeSoapExchange(_frequency_response())
    factory = RadioReferenceObservationSessionFactory(
        plan=_frequency_plan(),
        exchange_factory=lambda: exchange,
        now=lambda: observed_at,
    )
    secrets = {
        "RR_APP_KEY": _TEST_SECRET_A,
        "RR_PASSWORD": _TEST_SECRET_B,
    }
    source = RadioReferenceSource(
        configuration=_configuration(),
        session_factory=factory,
        secret_resolver=secrets.__getitem__,
    )

    observations = source.read_observations()

    assert len(observations) == 1
    assert observations[0].identity.record_id == "frequency-101"
    assert observations[0].evidence.observed_at is observed_at
    assert exchange.closed is True


def test_observation_session_requires_timezone_aware_wall_clock() -> None:
    exchange = FakeSoapExchange(_frequency_response())
    session = _session(
        exchange,
        now=lambda: datetime(2026, 8, 14, 14, 47),
    )

    with pytest.raises(
        ValueError,
        match="wall clock must return a timezone-aware datetime",
    ):
        session.read_observations()

    assert exchange.calls == []


def test_observation_session_redacts_exchange_failure() -> None:
    exchange = FakeSoapExchange(
        _frequency_response(),
        error=RuntimeError(
            f"provider failed with {_TEST_SECRET_B} and private details"
        ),
    )
    session = _session(
        exchange,
        now=lambda: datetime(2026, 8, 14, 14, 48, tzinfo=UTC),
    )

    with pytest.raises(RadioReferenceError) as raised:
        session.read_observations()

    assert raised.value.reason is RadioReferenceErrorReason.SERVICE_FAILED
    assert _TEST_SECRET_B not in str(raised.value)
    assert "private details" not in str(raised.value)


def test_observation_session_preserves_stable_exchange_error_reason() -> None:
    exchange = FakeSoapExchange(
        _frequency_response(),
        error=RadioReferenceError(
            RadioReferenceErrorReason.AUTHENTICATION_FAILED
        ),
    )
    session = _session(
        exchange,
        now=lambda: datetime(2026, 8, 14, 14, 49, tzinfo=UTC),
    )

    with pytest.raises(RadioReferenceError) as raised:
        session.read_observations()

    assert (
        raised.value.reason
        is RadioReferenceErrorReason.AUTHENTICATION_FAILED
    )


@pytest.mark.parametrize(
    "response",
    (
        bytearray(b"not immutable bytes"),
        "not bytes",
        object(),
    ),
)
def test_observation_session_requires_exact_response_bytes(
    response: object,
) -> None:
    exchange = FakeSoapExchange(response)
    session = _session(
        exchange,
        now=lambda: datetime(2026, 8, 14, 14, 50, tzinfo=UTC),
    )

    with pytest.raises(RadioReferenceError) as raised:
        session.read_observations()

    assert raised.value.reason is RadioReferenceErrorReason.INVALID_RESPONSE


def test_observation_session_redacts_malformed_response_details() -> None:
    provider_text = b"private-provider-diagnostic"
    exchange = FakeSoapExchange(provider_text)
    session = _session(
        exchange,
        now=lambda: datetime(2026, 8, 14, 14, 51, tzinfo=UTC),
    )

    with pytest.raises(RadioReferenceError) as raised:
        session.read_observations()

    assert raised.value.reason is RadioReferenceErrorReason.INVALID_RESPONSE
    assert provider_text.decode() not in str(raised.value)


def test_observation_session_close_is_idempotent_and_redacts_failure() -> None:
    exchange = FakeSoapExchange(
        _frequency_response(),
        close_error=RuntimeError(f"{_TEST_SECRET_B} private close detail"),
    )
    session = _session(exchange)

    with pytest.raises(RadioReferenceError) as raised:
        session.close()

    assert raised.value.reason is RadioReferenceErrorReason.CLEANUP_FAILED
    assert _TEST_SECRET_B not in str(raised.value)
    assert session.closed is True

    session.close()
    assert exchange.closed is True


def test_observation_session_rejects_reads_after_close() -> None:
    exchange = FakeSoapExchange(_frequency_response())
    session = _session(exchange)
    session.close()

    with pytest.raises(RadioReferenceError) as raised:
        session.read_observations()

    assert raised.value.reason is RadioReferenceErrorReason.SERVICE_FAILED
    assert exchange.calls == []


def test_observation_session_public_symbols_are_package_exports() -> None:
    assert (
        sds200.RadioReferenceObservationSession
        is RadioReferenceObservationSession
    )
    assert (
        sds200.RadioReferenceObservationSessionFactory
        is RadioReferenceObservationSessionFactory
    )
    assert sds200.RadioReferenceSoapExchange is RadioReferenceSoapExchange
    for name in (
        "RadioReferenceObservationClock",
        "RadioReferenceObservationSession",
        "RadioReferenceObservationSessionFactory",
        "RadioReferenceSoapExchange",
        "RadioReferenceSoapExchangeFactory",
    ):
        assert name in sds200.__all__
