from __future__ import annotations

import ssl
from dataclasses import fields
from datetime import UTC, datetime

import pytest

import sds200
import sds200.radioreference_http as radioreference_http
from sds200.favorites_external import FavoritesExternalSourceIdentity
from sds200.radioreference import (
    RadioReferenceConfiguration,
    RadioReferenceCredential,
    RadioReferenceError,
    RadioReferenceErrorReason,
)
from sds200.radioreference_http import (
    RADIOREFERENCE_HTTPS_DEFAULT_MAX_REQUEST_BYTES,
    RADIOREFERENCE_HTTPS_DEFAULT_MAX_RESPONSE_BYTES,
    RADIOREFERENCE_HTTPS_DEFAULT_TIMEOUT,
    RadioReferenceHttpsSoapExchange,
    RadioReferenceHttpsSoapExchangeFactory,
)
from sds200.radioreference_records import (
    RadioReferenceWsdlOperation,
    radioreference_operation_contract,
)
from sds200.radioreference_session import (
    RadioReferenceObservationRequestPlan,
    RadioReferenceObservationSessionFactory,
)
from sds200.radioreference_soap import RADIOREFERENCE_SOAP_DEFAULT_MAX_DOCUMENT_BYTES

OPERATION = RadioReferenceWsdlOperation.GET_COUNTRY_INFO
SOAP_ACTION = radioreference_operation_contract(OPERATION).soap_action
REQUEST = b"<synthetic-request/>"


class FakeResponse:
    def __init__(
        self,
        body: bytes = b"<synthetic-response/>",
        *,
        status: int = 200,
        headers: dict[str, object] | None = None,
        read_error: Exception | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self.status = status
        self.body = body
        self.headers = headers or {"Content-Type": "text/xml"}
        self.read_error = read_error
        self.close_error = close_error
        self.read_sizes: list[int] = []
        self.closed = False

    def getheader(self, name: str) -> object | None:
        return self.headers.get(name)

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        if self.read_error is not None:
            raise self.read_error
        return self.body[:size]

    def close(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


class FakeConnection:
    response = FakeResponse()
    construction_error: Exception | None = None
    request_error: Exception | None = None
    response_error: Exception | None = None
    close_error: Exception | None = None
    instances: list[FakeConnection] = []

    def __init__(self, host: str, **kwargs: object) -> None:
        if self.construction_error is not None:
            raise self.construction_error
        self.host = host
        self.kwargs = kwargs
        self.requests: list[tuple[str, str, bytes, dict[str, str]]] = []
        self.closed = False
        type(self).instances.append(self)

    def request(
        self,
        method: str,
        target: str,
        body: bytes,
        headers: dict[str, str],
    ) -> None:
        if self.request_error is not None:
            raise self.request_error
        self.requests.append((method, target, body, headers))

    def getresponse(self) -> FakeResponse:
        if self.response_error is not None:
            raise self.response_error
        return self.response

    def close(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


@pytest.fixture(autouse=True)
def fake_https(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeConnection.response = FakeResponse()
    FakeConnection.construction_error = None
    FakeConnection.request_error = None
    FakeConnection.response_error = None
    FakeConnection.close_error = None
    FakeConnection.instances = []
    monkeypatch.setattr(
        radioreference_http.http.client, "HTTPSConnection", FakeConnection
    )


def exchange() -> bytes:
    return RadioReferenceHttpsSoapExchange().exchange(
        OPERATION, REQUEST, soap_action=SOAP_ACTION
    )


def assert_reason(
    reason: RadioReferenceErrorReason,
) -> pytest.ExceptionInfo[RadioReferenceError]:
    return pytest.raises(
        RadioReferenceError,
        match="RadioReference",
        check=lambda error: error.reason is reason,
    )


def test_defaults_and_factory_are_bounded_and_fresh() -> None:
    value = RadioReferenceHttpsSoapExchange()
    assert value.timeout == RADIOREFERENCE_HTTPS_DEFAULT_TIMEOUT
    assert value.max_request_bytes == RADIOREFERENCE_HTTPS_DEFAULT_MAX_REQUEST_BYTES
    assert value.max_response_bytes == RADIOREFERENCE_SOAP_DEFAULT_MAX_DOCUMENT_BYTES
    assert (
        value.max_response_bytes
        == RADIOREFERENCE_HTTPS_DEFAULT_MAX_RESPONSE_BYTES
    )
    factory = RadioReferenceHttpsSoapExchangeFactory()
    assert factory() is not factory()
    assert {item.name for item in fields(factory)} == {
        "timeout", "max_request_bytes", "max_response_bytes"
    }


@pytest.mark.parametrize("value", [True, "1", None])
def test_timeout_rejects_wrong_type(value: object) -> None:
    with pytest.raises(TypeError):
        RadioReferenceHttpsSoapExchange(timeout=value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), -float("inf")])
def test_timeout_rejects_nonpositive_or_nonfinite(value: float) -> None:
    with pytest.raises(ValueError):
        RadioReferenceHttpsSoapExchange(timeout=value)


@pytest.mark.parametrize("name", ["max_request_bytes", "max_response_bytes"])
@pytest.mark.parametrize(
    "value,error",
    [
        (True, TypeError),
        (1.0, TypeError),
        ("1", TypeError),
        (0, ValueError),
        (-1, ValueError),
    ],
)
def test_byte_limits_require_exact_positive_integers(
    name: str, value: object, error: type[Exception]
) -> None:
    with pytest.raises(error):
        RadioReferenceHttpsSoapExchange(**{name: value})  # type: ignore[arg-type]
    with pytest.raises(error):
        RadioReferenceHttpsSoapExchangeFactory(**{name: value})  # type: ignore[arg-type]


def test_exact_https_request_and_default_tls_context(monkeypatch: pytest.MonkeyPatch) -> None:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    monkeypatch.setattr(radioreference_http.ssl, "create_default_context", lambda: context)
    assert exchange() == b"<synthetic-response/>"
    connection = FakeConnection.instances[0]
    assert connection.host == "api.radioreference.com"
    assert connection.kwargs == {"port": None, "timeout": 15.0, "context": context}
    assert connection.requests == [
        (
            "POST", "/soap2/", REQUEST,
            {
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": f'"{SOAP_ACTION}"',
                "Accept": "text/xml",
                "Accept-Encoding": "identity",
            },
        )
    ]
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED


@pytest.mark.parametrize(
    "operation,request_body,action,error",
    [
        ("getCountryInfo", REQUEST, SOAP_ACTION, TypeError),
        (OPERATION, bytearray(REQUEST), SOAP_ACTION, TypeError),
        (OPERATION, b"", SOAP_ACTION, ValueError),
        (OPERATION, REQUEST, 1, TypeError),
        (OPERATION, REQUEST, "wrong-action", ValueError),
    ],
)
def test_invalid_requests_fail_before_connection(
    operation: object,
    request_body: object,
    action: object,
    error: type[Exception],
) -> None:
    with pytest.raises(error):
        RadioReferenceHttpsSoapExchange().exchange(  # type: ignore[arg-type]
            operation, request_body, soap_action=action
        )
    assert FakeConnection.instances == []


def test_request_limit_and_closed_state() -> None:
    value = RadioReferenceHttpsSoapExchange(max_request_bytes=len(REQUEST))
    assert value.exchange(OPERATION, REQUEST, soap_action=SOAP_ACTION)
    with pytest.raises(ValueError):
        RadioReferenceHttpsSoapExchange(max_request_bytes=len(REQUEST) - 1).exchange(
            OPERATION, REQUEST, soap_action=SOAP_ACTION
        )
    value.close()
    value.close()
    with assert_reason(RadioReferenceErrorReason.SERVICE_FAILED):
        value.exchange(OPERATION, REQUEST, soap_action=SOAP_ACTION)


@pytest.mark.parametrize(
    "content_type",
    ["text/xml", "text/xml; charset=utf-8", "TEXT/XML ; charset=UTF-8"],
)
def test_success_media_types_and_exact_bounded_body(content_type: str) -> None:
    body = b"\x00synthetic\xff"
    FakeConnection.response = FakeResponse(body, headers={"Content-Type": content_type})
    assert RadioReferenceHttpsSoapExchange(max_response_bytes=len(body)).exchange(
        OPERATION, REQUEST, soap_action=SOAP_ACTION
    ) == body
    assert FakeConnection.response.read_sizes == [len(body) + 1]


@pytest.mark.parametrize(
    "body,headers",
    [
        (b"", {"Content-Type": "text/xml"}),
        (b"x", {"Content-Type": "application/xml"}),
        (b"x", {"Content-Type": "text/xml", "Content-Encoding": "gzip"}),
        (b"x", {"Content-Type": "text/xml", "Content-Length": "bad"}),
        (b"x", {"Content-Type": "text/xml", "Content-Length": " 1"}),
    ],
)
def test_invalid_success_responses(body: bytes, headers: dict[str, object]) -> None:
    FakeConnection.response = FakeResponse(body, headers=headers)
    with assert_reason(RadioReferenceErrorReason.INVALID_RESPONSE):
        exchange()


def test_declared_and_actual_response_limits() -> None:
    FakeConnection.response = FakeResponse(
        b"not-read", headers={"Content-Type": "text/xml", "Content-Length": "3"}
    )
    with assert_reason(RadioReferenceErrorReason.INVALID_RESPONSE):
        RadioReferenceHttpsSoapExchange(max_response_bytes=2).exchange(
            OPERATION, REQUEST, soap_action=SOAP_ACTION
        )
    assert FakeConnection.response.read_sizes == []

    FakeConnection.response = FakeResponse(b"abc", headers={"Content-Type": "text/xml"})
    with assert_reason(RadioReferenceErrorReason.INVALID_RESPONSE):
        RadioReferenceHttpsSoapExchange(max_response_bytes=2).exchange(
            OPERATION, REQUEST, soap_action=SOAP_ACTION
        )
    assert FakeConnection.response.read_sizes == [3]


@pytest.mark.parametrize(
    "status,reason",
    [(401, RadioReferenceErrorReason.AUTHENTICATION_FAILED),
     (403, RadioReferenceErrorReason.AUTHENTICATION_FAILED),
     (302, RadioReferenceErrorReason.SERVICE_FAILED),
     (400, RadioReferenceErrorReason.SERVICE_FAILED),
     (500, RadioReferenceErrorReason.SERVICE_FAILED)],
)
def test_http_errors_are_redacted(status: int, reason: RadioReferenceErrorReason) -> None:
    secret = "provider-secret-reason-location-body"
    FakeConnection.response = FakeResponse(
        secret.encode(), status=status,
        headers={"Content-Type": "text/xml", "Location": secret},
    )
    with pytest.raises(RadioReferenceError) as captured:
        exchange()
    assert captured.value.reason is reason
    assert secret not in str(captured.value)
    assert FakeConnection.response.read_sizes == []
    assert len(FakeConnection.instances) == 1


@pytest.mark.parametrize("stage", ["construction", "request", "response", "read"])
def test_transport_failures_are_redacted_and_cleaned(stage: str) -> None:
    low_level = RuntimeError("private low-level TLS provider text")
    if stage == "construction":
        FakeConnection.construction_error = low_level
    elif stage == "request":
        FakeConnection.request_error = low_level
    elif stage == "response":
        FakeConnection.response_error = low_level
    else:
        FakeConnection.response = FakeResponse(read_error=low_level)
    with pytest.raises(RadioReferenceError) as captured:
        exchange()
    assert captured.value.reason is RadioReferenceErrorReason.CONNECTION_FAILED
    assert "private" not in str(captured.value)
    if FakeConnection.instances:
        assert FakeConnection.instances[0].closed
    if stage == "read":
        assert FakeConnection.response.closed


def test_cleanup_on_success_and_cleanup_failure_classification() -> None:
    assert exchange()
    assert FakeConnection.response.closed
    assert FakeConnection.instances[0].closed

    FakeConnection.response = FakeResponse(close_error=RuntimeError("secret"))
    with assert_reason(RadioReferenceErrorReason.CLEANUP_FAILED):
        exchange()
    assert FakeConnection.instances[0].closed


def test_primary_failure_wins_over_cleanup_failure() -> None:
    FakeConnection.response = FakeResponse(
        status=500, close_error=RuntimeError("cleanup secret")
    )
    FakeConnection.close_error = RuntimeError("connection cleanup secret")
    with assert_reason(RadioReferenceErrorReason.SERVICE_FAILED):
        exchange()


def test_public_exports() -> None:
    assert sds200.RadioReferenceHttpsSoapExchange is RadioReferenceHttpsSoapExchange
    assert sds200.RadioReferenceHttpsSoapExchangeFactory is RadioReferenceHttpsSoapExchangeFactory
    assert sds200.RADIOREFERENCE_HTTPS_DEFAULT_TIMEOUT == 15.0
    assert sds200.RADIOREFERENCE_HTTPS_DEFAULT_MAX_REQUEST_BYTES == 4 * 1024 * 1024
    assert (
        sds200.RADIOREFERENCE_HTTPS_DEFAULT_MAX_RESPONSE_BYTES
        == RADIOREFERENCE_SOAP_DEFAULT_MAX_DOCUMENT_BYTES
    )


def _synthetic_frequency_response() -> bytes:
    soap = "http://schemas.xmlsoap.org/soap/envelope/"
    encoding = "http://schemas.xmlsoap.org/soap/encoding/"
    instance = "http://www.w3.org/2001/XMLSchema-instance"
    schema = "http://www.w3.org/2001/XMLSchema"
    provider = "http://api.radioreference.com/soap2"
    operation = RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES
    return (
        f'<soap:Envelope xmlns:soap="{soap}" xmlns:enc="{encoding}" '
        f'xmlns:xsi="{instance}" xmlns:xsd="{schema}" '
        f'xmlns:tns="{provider}"><soap:Body>'
        f"<tns:{operation.value}Response>"
        '<return xsi:type="enc:Array" enc:arrayType="tns:freq[1]">'
        '<item xsi:type="tns:freq"><fid>101</fid><out>155.1000</out>'
        "<in>0</in><callsign>SYNTHETIC</callsign>"
        "<descr>Synthetic description</descr><alpha>Dispatch</alpha>"
        "<tone></tone><colorCode></colorCode><tg></tg><slot></slot>"
        "<mode>FMN</mode><enc>0</enc><class>PW</class>"
        '<tags xsi:type="enc:Array" enc:arrayType="tns:tag[0]"></tags>'
        "<scid>7</scid><sort>1</sort>"
        "<lastUpdated>2026-08-13T09:21:04Z</lastUpdated>"
        "</item></return>"
        f"</tns:{operation.value}Response>"
        "</soap:Body></soap:Envelope>"
    ).encode()


def test_factory_composes_end_to_end_with_existing_observation_session() -> None:
    operation = RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES
    plan = RadioReferenceObservationRequestPlan(
        source=FavoritesExternalSourceIdentity(
            provider="radioreference",
            dataset="synthetic-subcategory",
        ),
        operation=operation,
        parameters=(("scid", 7),),
    )
    FakeConnection.response = FakeResponse(_synthetic_frequency_response())
    session_factory = RadioReferenceObservationSessionFactory(
        plan=plan,
        exchange_factory=RadioReferenceHttpsSoapExchangeFactory(),
        now=lambda: datetime(2026, 8, 14, 14, 45, tzinfo=UTC),
    )
    session = session_factory(
        RadioReferenceConfiguration(
            credential=RadioReferenceCredential(
                username="synthetic-user",
                application_key_environment_variable="SYNTHETIC_APP_KEY",
                password_environment_variable="SYNTHETIC_PASSWORD",
            )
        ),
        application_key="synthetic-application-key",
        password="synthetic-password",
    )

    observations = session.read_observations()

    assert len(observations) == 1
    assert observations[0].identity.record_id == "frequency-101"
    request = FakeConnection.instances[0].requests[0]
    assert request[3]["SOAPAction"] == f'"{plan.soap_action}"'
    assert b"synthetic-application-key" in request[2]
    assert b"synthetic-password" in request[2]
    assert FakeConnection.response.closed
    assert FakeConnection.instances[0].closed
    production_exchange = session._exchange  # noqa: SLF001
    session.close()
    assert production_exchange._closed is True  # noqa: SLF001
