from __future__ import annotations

import xml.etree.ElementTree as ET
from decimal import Decimal

import pytest

import sds200
from sds200 import (
    RADIOREFERENCE_PROGRAMMING_OPERATION_CONTRACTS,
    RADIOREFERENCE_SOAP_ENCODING_STYLE,
    RADIOREFERENCE_SOAP_NAMESPACE,
    RADIOREFERENCE_SOAP_REQUEST_DEFAULT_MAX_DOCUMENT_BYTES,
    RadioReferenceConfiguration,
    RadioReferenceCredential,
    RadioReferenceSoapRequestSerializer,
    RadioReferenceSoapStyle,
    RadioReferenceWsdlOperation,
    radioreference_operation_contract,
)

SOAP = "http://schemas.xmlsoap.org/soap/envelope/"
XSI = "http://www.w3.org/2001/XMLSchema-instance"

APP_KEY = "synthetic-application-key"
PASSWORD = "synthetic-user-password"


def _configuration(
    *,
    style: RadioReferenceSoapStyle = RadioReferenceSoapStyle.RPC,
) -> RadioReferenceConfiguration:
    return RadioReferenceConfiguration(
        credential=RadioReferenceCredential(
            username="synthetic-user",
            application_key_environment_variable=(
                "SDS200_TEST_RADIOREFERENCE_APPLICATION_KEY"
            ),
            password_environment_variable="SDS200_TEST_RADIOREFERENCE_PASSWORD",
        ),
        version="18",
        style=style,
    )


def _parameter_value(type_name: str) -> object:
    if type_name == "xsd:int":
        return 7
    if type_name == "xsd:decimal":
        return Decimal("155.1000")
    if type_name == "xsd:string":
        return " synthetic & value "
    raise AssertionError(type_name)


def _parameters(
    operation: RadioReferenceWsdlOperation,
) -> dict[str, object]:
    contract = radioreference_operation_contract(operation)
    return {
        parameter.name: _parameter_value(parameter.type_name)
        for parameter in contract.request_parameters
        if parameter.name != "authInfo"
    }


def _serialize(
    operation: RadioReferenceWsdlOperation,
    parameters: dict[str, object] | None = None,
    *,
    configuration: RadioReferenceConfiguration | None = None,
    application_key: str = APP_KEY,
    password: str = PASSWORD,
    max_document_bytes: int = (
        RADIOREFERENCE_SOAP_REQUEST_DEFAULT_MAX_DOCUMENT_BYTES
    ),
) -> bytes:
    return RadioReferenceSoapRequestSerializer(
        max_document_bytes=max_document_bytes
    ).serialize(
        operation,
        _parameters(operation) if parameters is None else parameters,
        _configuration() if configuration is None else configuration,
        application_key=application_key,
        password=password,
    )


def test_request_serializer_default_limit_is_stable() -> None:
    assert RADIOREFERENCE_SOAP_REQUEST_DEFAULT_MAX_DOCUMENT_BYTES == 4 * 1024 * 1024


@pytest.mark.parametrize("operation", list(RadioReferenceWsdlOperation))
def test_serializer_covers_every_reviewed_programming_operation(
    operation: RadioReferenceWsdlOperation,
) -> None:
    xml = _serialize(operation)
    contract = radioreference_operation_contract(operation)

    assert contract.soap_action == (
        f"{RADIOREFERENCE_SOAP_NAMESPACE}#{operation.value}"
    )

    root = ET.fromstring(xml)
    assert root.tag == f"{{{SOAP}}}Envelope"

    body = root.find(f"{{{SOAP}}}Body")
    assert body is not None
    assert body.attrib[f"{{{SOAP}}}encodingStyle"] == (
        RADIOREFERENCE_SOAP_ENCODING_STYLE
    )

    operation_element = list(body)
    assert len(operation_element) == 1
    call = operation_element[0]
    assert call.tag == f"{{{RADIOREFERENCE_SOAP_NAMESPACE}}}{operation.value}"

    assert [child.tag for child in call] == [
        parameter.name for parameter in contract.request_parameters
    ]


def test_serializer_emits_deterministic_rpc_encoded_request() -> None:
    xml = _serialize(
        RadioReferenceWsdlOperation.GET_COUNTRY_INFO,
        {"coid": 7},
        application_key="synthetic<&app",
        password="synthetic<&password",
    )

    assert xml == (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<soap:Envelope '
        b'xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" '
        b'xmlns:enc="http://schemas.xmlsoap.org/soap/encoding/" '
        b'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        b'xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        b'xmlns:tns="http://api.radioreference.com/soap2">'
        b'<soap:Body '
        b'soap:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        b'<tns:getCountryInfo>'
        b'<coid xsi:type="xsd:int">7</coid>'
        b'<authInfo xsi:type="tns:authInfo">'
        b'<username xsi:type="xsd:string">synthetic-user</username>'
        b'<password xsi:type="xsd:string">synthetic&lt;&amp;password</password>'
        b'<appKey xsi:type="xsd:string">synthetic&lt;&amp;app</appKey>'
        b'<version xsi:type="xsd:string">18</version>'
        b'<style xsi:type="xsd:string">rpc</style>'
        b'</authInfo>'
        b'</tns:getCountryInfo>'
        b'</soap:Body>'
        b'</soap:Envelope>'
    )


def test_auth_info_order_and_values_follow_reviewed_schema() -> None:
    xml = _serialize(RadioReferenceWsdlOperation.GET_COUNTRY_INFO)

    root = ET.fromstring(xml)
    body = root.find(f"{{{SOAP}}}Body")
    assert body is not None
    call = list(body)[0]
    auth_info = list(call)[1]

    assert auth_info.tag == "authInfo"
    assert auth_info.attrib[f"{{{XSI}}}type"] == "tns:authInfo"
    assert [child.tag for child in auth_info] == [
        "username",
        "password",
        "appKey",
        "version",
        "style",
    ]
    assert [child.text for child in auth_info] == [
        "synthetic-user",
        PASSWORD,
        APP_KEY,
        "18",
        "rpc",
    ]
    assert {
        child.attrib[f"{{{XSI}}}type"] for child in auth_info
    } == {"xsd:string"}



@pytest.mark.parametrize("value", [-(2**31), 2**31 - 1])
def test_serializer_accepts_xsd_int_boundaries(value: int) -> None:
    xml = _serialize(
        RadioReferenceWsdlOperation.GET_COUNTRY_INFO,
        {"coid": value},
    )

    assert f">{value}<".encode() in xml


@pytest.mark.parametrize("value", [-(2**31) - 1, 2**31])
def test_serializer_rejects_xsd_int_outside_range(value: int) -> None:
    with pytest.raises(ValueError, match="outside the xsd:int range"):
        _serialize(
            RadioReferenceWsdlOperation.GET_COUNTRY_INFO,
            {"coid": value},
        )


@pytest.mark.parametrize("value", [True, 1.0, "1"])
def test_serializer_requires_exact_integer_type(value: object) -> None:
    with pytest.raises(TypeError, match="xsd:int-compatible integer"):
        _serialize(
            RadioReferenceWsdlOperation.GET_COUNTRY_INFO,
            {"coid": value},
        )


def test_serializer_preserves_xsd_string_text_and_escapes_xml() -> None:
    xml = _serialize(
        RadioReferenceWsdlOperation.SEARCH_COUNTY_FREQUENCY,
        {
            "ctid": 7,
            "freq": Decimal("155.1000"),
            "tone": " 123 & <tone> ",
        },
    )

    assert b'<freq xsi:type="xsd:decimal">155.1000</freq>' in xml
    assert (
        b'<tone xsi:type="xsd:string"> 123 &amp; &lt;tone&gt; </tone>'
        in xml
    )


def test_serializer_emits_decimal_without_exponent_notation() -> None:
    xml = _serialize(
        RadioReferenceWsdlOperation.SEARCH_COUNTY_FREQUENCY,
        {
            "ctid": 7,
            "freq": Decimal("1E+3"),
            "tone": "",
        },
    )

    assert b'<freq xsi:type="xsd:decimal">1000</freq>' in xml
    assert b"1E+3" not in xml


@pytest.mark.parametrize(
    "value",
    (
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
    ),
)
def test_serializer_rejects_nonfinite_decimal(value: Decimal) -> None:
    with pytest.raises(ValueError, match="finite for xsd:decimal"):
        _serialize(
            RadioReferenceWsdlOperation.SEARCH_COUNTY_FREQUENCY,
            {"ctid": 7, "freq": value, "tone": ""},
        )


@pytest.mark.parametrize("value", [155.1, 155, "155.1"])
def test_serializer_requires_decimal_type(value: object) -> None:
    with pytest.raises(TypeError, match="Decimal for xsd:decimal"):
        _serialize(
            RadioReferenceWsdlOperation.SEARCH_COUNTY_FREQUENCY,
            {"ctid": 7, "freq": value, "tone": ""},
        )


@pytest.mark.parametrize(
    "parameters",
    (
        {},
        {"coid": 7, "unexpected": 1},
        {"coid": 7, "authInfo": object()},
    ),
)
def test_serializer_requires_exact_operation_parameter_set(
    parameters: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="exactly match"):
        _serialize(
            RadioReferenceWsdlOperation.GET_COUNTRY_INFO,
            parameters,
        )


def test_serializer_rejects_non_string_parameter_name() -> None:
    serializer = RadioReferenceSoapRequestSerializer()

    with pytest.raises(TypeError, match="parameter names must be strings"):
        serializer.serialize(
            RadioReferenceWsdlOperation.GET_COUNTRY_INFO,
            {1: 7},  # type: ignore[dict-item]
            _configuration(),
            application_key=APP_KEY,
            password=PASSWORD,
        )


def test_serializer_rejects_unreviewed_document_style() -> None:
    with pytest.raises(ValueError, match="only the reviewed RPC style"):
        _serialize(
            RadioReferenceWsdlOperation.GET_COUNTRY_INFO,
            configuration=_configuration(style=RadioReferenceSoapStyle.DOCUMENT),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("application_key", 1),
        ("password", 1),
    ),
)
def test_serializer_requires_string_resolved_secrets(
    field: str,
    value: object,
) -> None:
    kwargs: dict[str, object] = {
        "application_key": APP_KEY,
        "password": PASSWORD,
    }
    kwargs[field] = value

    with pytest.raises(TypeError, match="must be a string"):
        RadioReferenceSoapRequestSerializer().serialize(
            RadioReferenceWsdlOperation.GET_COUNTRY_INFO,
            {"coid": 7},
            _configuration(),
            **kwargs,  # type: ignore[arg-type]
        )


def test_serializer_rejects_xml_incompatible_secret_without_echoing_it() -> None:
    secret = "synthetic-secret\x00hidden"

    with pytest.raises(ValueError) as exc_info:
        _serialize(
            RadioReferenceWsdlOperation.GET_COUNTRY_INFO,
            password=secret,
        )

    assert secret not in str(exc_info.value)
    assert "XML 1.0" in str(exc_info.value)


def test_serializer_enforces_document_byte_limit() -> None:
    with pytest.raises(ValueError, match="document-byte limit"):
        _serialize(
            RadioReferenceWsdlOperation.GET_COUNTRY_INFO,
            max_document_bytes=64,
        )


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_serializer_rejects_invalid_document_byte_limit(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        RadioReferenceSoapRequestSerializer(
            max_document_bytes=value  # type: ignore[arg-type]
        )



@pytest.mark.parametrize(
    "name",
    (
        "RADIOREFERENCE_SOAP_REQUEST_DEFAULT_MAX_DOCUMENT_BYTES",
        "RadioReferenceSoapRequestSerializer",
    ),
)
def test_request_serializer_exports_are_public(name: str) -> None:
    assert name in sds200.__all__
    assert hasattr(sds200, name)


def test_reviewed_operation_contracts_remain_unchanged() -> None:
    assert len(RADIOREFERENCE_PROGRAMMING_OPERATION_CONTRACTS) == 19
    assert {
        contract.operation
        for contract in RADIOREFERENCE_PROGRAMMING_OPERATION_CONTRACTS
    } == set(RadioReferenceWsdlOperation)
