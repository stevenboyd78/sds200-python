"""Offline RadioReference SOAP 1.1 RPC/encoded request serialization."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Final
from xml.sax.saxutils import escape

from .radioreference import RadioReferenceConfiguration, RadioReferenceSoapStyle
from .radioreference_records import (
    RADIOREFERENCE_AUTH_INFO_FIELDS,
    RADIOREFERENCE_AUTH_INFO_TYPE,
    RADIOREFERENCE_SOAP_ENCODING_STYLE,
    RADIOREFERENCE_SOAP_NAMESPACE,
    RadioReferenceWsdlOperation,
    RadioReferenceWsdlParameter,
    radioreference_operation_contract,
)

RADIOREFERENCE_SOAP_REQUEST_DEFAULT_MAX_DOCUMENT_BYTES: Final = 4 * 1024 * 1024

_SOAP_ENVELOPE_NAMESPACE: Final = "http://schemas.xmlsoap.org/soap/envelope/"
_XML_SCHEMA_INSTANCE_NAMESPACE: Final = "http://www.w3.org/2001/XMLSchema-instance"
_XML_SCHEMA_NAMESPACE: Final = "http://www.w3.org/2001/XMLSchema"

_XSD_INT_MIN = -(2**31)
_XSD_INT_MAX = 2**31 - 1


def _validate_positive_limit(value: int, *, label: str) -> None:
    if type(value) is not int:
        raise TypeError(f"{label} must be an integer.")
    if value <= 0:
        raise ValueError(f"{label} must be greater than zero.")


def _require_xml_string(value: object, *, label: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{label} must be a string.")
    for character in value:
        codepoint = ord(character)
        if (
            codepoint not in {0x09, 0x0A, 0x0D}
            and not 0x20 <= codepoint <= 0xD7FF
            and not 0xE000 <= codepoint <= 0xFFFD
            and not 0x10000 <= codepoint <= 0x10FFFF
        ):
            raise ValueError(
                f"{label} contains a character that XML 1.0 cannot represent."
            )
    return value


def _xml_text(value: object, *, label: str) -> str:
    return escape(_require_xml_string(value, label=label))


def _xsd_int_text(value: object, *, label: str) -> str:
    if type(value) is not int:
        raise TypeError(f"{label} must be an xsd:int-compatible integer.")
    if not _XSD_INT_MIN <= value <= _XSD_INT_MAX:
        raise ValueError(f"{label} is outside the xsd:int range.")
    return str(value)


def _xsd_decimal_text(value: object, *, label: str) -> str:
    if type(value) is not Decimal:
        raise TypeError(f"{label} must be Decimal for xsd:decimal.")
    if not value.is_finite():
        raise ValueError(f"{label} must be finite for xsd:decimal.")
    return format(value, "f")


def _scalar_text(
    parameter: RadioReferenceWsdlParameter,
    value: object,
) -> str:
    label = f"RadioReference SOAP parameter {parameter.name!r}"
    if parameter.type_name == "xsd:int":
        return _xsd_int_text(value, label=label)
    if parameter.type_name == "xsd:decimal":
        return _xsd_decimal_text(value, label=label)
    if parameter.type_name == "xsd:string":
        return _xml_text(value, label=label)
    raise ValueError(
        "RadioReference SOAP operation contains an unsupported reviewed "
        "request parameter type."
    )


def _scalar_element(
    parameter: RadioReferenceWsdlParameter,
    value: object,
) -> str:
    return (
        f'<{parameter.name} xsi:type="{parameter.type_name}">'
        f"{_scalar_text(parameter, value)}"
        f"</{parameter.name}>"
    )


def _auth_info_element(
    configuration: RadioReferenceConfiguration,
    *,
    application_key: object,
    password: object,
) -> str:
    values: dict[str, object] = {
        "username": configuration.credential.username,
        "password": password,
        "appKey": application_key,
        "version": configuration.version,
        "style": configuration.style.value,
    }

    fields = []
    for field_contract in RADIOREFERENCE_AUTH_INFO_FIELDS:
        value = values[field_contract.name]
        fields.append(
            f'<{field_contract.name} xsi:type="{field_contract.type_name}">'
            f"{_xml_text(value, label='RadioReference SOAP authInfo field')}"
            f"</{field_contract.name}>"
        )

    return (
        f'<authInfo xsi:type="{RADIOREFERENCE_AUTH_INFO_TYPE}">'
        f"{''.join(fields)}"
        "</authInfo>"
    )


def _serialize_envelope(
    operation: RadioReferenceWsdlOperation,
    parameters: Mapping[str, object],
    configuration: RadioReferenceConfiguration,
    *,
    application_key: object,
    password: object,
) -> bytes:
    contract = radioreference_operation_contract(operation)

    expected_parameters = tuple(
        parameter
        for parameter in contract.request_parameters
        if parameter.type_name != RADIOREFERENCE_AUTH_INFO_TYPE
    )
    expected_names = {parameter.name for parameter in expected_parameters}

    if any(type(name) is not str for name in parameters):
        raise TypeError("RadioReference SOAP parameter names must be strings.")
    if set(parameters) != expected_names:
        raise ValueError(
            "RadioReference SOAP parameters must exactly match the reviewed "
            "operation contract."
        )

    children: list[str] = []
    for parameter in contract.request_parameters:
        if parameter.type_name == RADIOREFERENCE_AUTH_INFO_TYPE:
            children.append(
                _auth_info_element(
                    configuration,
                    application_key=application_key,
                    password=password,
                )
            )
            continue
        children.append(_scalar_element(parameter, parameters[parameter.name]))

    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<soap:Envelope xmlns:soap="{_SOAP_ENVELOPE_NAMESPACE}" '
        f'xmlns:enc="{RADIOREFERENCE_SOAP_ENCODING_STYLE}" '
        f'xmlns:xsi="{_XML_SCHEMA_INSTANCE_NAMESPACE}" '
        f'xmlns:xsd="{_XML_SCHEMA_NAMESPACE}" '
        f'xmlns:tns="{RADIOREFERENCE_SOAP_NAMESPACE}">'
        f'<soap:Body soap:encodingStyle="{RADIOREFERENCE_SOAP_ENCODING_STYLE}">'
        f"<tns:{operation.value}>"
        f"{''.join(children)}"
        f"</tns:{operation.value}>"
        "</soap:Body>"
        "</soap:Envelope>"
    ).encode()

    return xml


@dataclass(frozen=True, slots=True)
class RadioReferenceSoapRequestSerializer:
    """Serialize reviewed RadioReference operations without network access."""

    max_document_bytes: int = RADIOREFERENCE_SOAP_REQUEST_DEFAULT_MAX_DOCUMENT_BYTES

    def __post_init__(self) -> None:
        _validate_positive_limit(
            self.max_document_bytes,
            label="RadioReference SOAP request document-byte limit",
        )

    def serialize(
        self,
        operation: RadioReferenceWsdlOperation,
        parameters: Mapping[str, object],
        configuration: RadioReferenceConfiguration,
        *,
        application_key: str,
        password: str,
    ) -> bytes:
        """Return deterministic ephemeral RPC/encoded SOAP request bytes."""

        if not isinstance(operation, RadioReferenceWsdlOperation):
            raise TypeError(
                "RadioReference SOAP operation must be "
                "RadioReferenceWsdlOperation."
            )
        if not isinstance(parameters, Mapping):
            raise TypeError(
                "RadioReference SOAP parameters must be a mapping."
            )
        if not isinstance(configuration, RadioReferenceConfiguration):
            raise TypeError(
                "RadioReference SOAP request requires "
                "RadioReferenceConfiguration."
            )
        if configuration.style is not RadioReferenceSoapStyle.RPC:
            raise ValueError(
                "RadioReference SOAP request serialization supports only the "
                "reviewed RPC style."
            )

        _require_xml_string(
            application_key,
            label="RadioReference SOAP application key",
        )
        _require_xml_string(
            password,
            label="RadioReference SOAP password",
        )

        xml = _serialize_envelope(
            operation,
            parameters,
            configuration,
            application_key=application_key,
            password=password,
        )
        if len(xml) > self.max_document_bytes:
            raise ValueError(
                "RadioReference SOAP request exceeds the document-byte limit."
            )

        return xml


__all__ = [
    "RADIOREFERENCE_SOAP_REQUEST_DEFAULT_MAX_DOCUMENT_BYTES",
    "RadioReferenceSoapRequestSerializer",
]
