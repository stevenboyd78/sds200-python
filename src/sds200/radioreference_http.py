"""Bounded HTTPS transport for the documented RadioReference SOAP service."""

from __future__ import annotations

import http.client
import math
import ssl
from dataclasses import dataclass, field
from typing import Final
from urllib.parse import urlsplit

from .radioreference import (
    RADIOREFERENCE_SERVICE_URL,
    RadioReferenceError,
    RadioReferenceErrorReason,
)
from .radioreference_records import (
    RadioReferenceWsdlOperation,
    radioreference_operation_contract,
)
from .radioreference_soap import RADIOREFERENCE_SOAP_DEFAULT_MAX_DOCUMENT_BYTES

RADIOREFERENCE_HTTPS_DEFAULT_TIMEOUT: Final = 15.0
RADIOREFERENCE_HTTPS_DEFAULT_MAX_REQUEST_BYTES: Final = 4 * 1024 * 1024
RADIOREFERENCE_HTTPS_DEFAULT_MAX_RESPONSE_BYTES: Final = (
    RADIOREFERENCE_SOAP_DEFAULT_MAX_DOCUMENT_BYTES
)


def _validate_timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("RadioReference HTTPS timeout must be numeric.")
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError(
            "RadioReference HTTPS timeout must be finite and positive."
        )
    return timeout


def _validate_byte_limit(value: object, *, label: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{label} must be an integer.")
    if value <= 0:
        raise ValueError(f"{label} must be positive.")
    return value


def _service_destination() -> tuple[str, int | None, str]:
    parsed = urlsplit(RADIOREFERENCE_SERVICE_URL)
    try:
        port = parsed.port
    except ValueError:
        raise RadioReferenceError(
            RadioReferenceErrorReason.SERVICE_FAILED
        ) from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise RadioReferenceError(
            RadioReferenceErrorReason.SERVICE_FAILED
        )
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    return parsed.hostname, port, target


def _invalid_response() -> RadioReferenceError:
    return RadioReferenceError(RadioReferenceErrorReason.INVALID_RESPONSE)


def _validate_response_headers(response: http.client.HTTPResponse) -> int | None:
    content_type = response.getheader("Content-Type")
    if type(content_type) is not str:
        raise _invalid_response()
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type != "text/xml":
        raise _invalid_response()

    content_encoding = response.getheader("Content-Encoding")
    if content_encoding is not None:
        if type(content_encoding) is not str:
            raise _invalid_response()
        if content_encoding.strip().lower() != "identity":
            raise _invalid_response()

    content_length = response.getheader("Content-Length")
    if content_length is None:
        return None
    if (
        type(content_length) is not str
        or not content_length
        or not content_length.isascii()
        or not content_length.isdecimal()
    ):
        raise _invalid_response()
    return int(content_length)


@dataclass(slots=True)
class RadioReferenceHttpsSoapExchange:
    """Exchange one bounded SOAP document over the fixed provider HTTPS URL."""

    timeout: float = RADIOREFERENCE_HTTPS_DEFAULT_TIMEOUT
    max_request_bytes: int = RADIOREFERENCE_HTTPS_DEFAULT_MAX_REQUEST_BYTES
    max_response_bytes: int = RADIOREFERENCE_HTTPS_DEFAULT_MAX_RESPONSE_BYTES
    _closed: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self.timeout = _validate_timeout(self.timeout)
        self.max_request_bytes = _validate_byte_limit(
            self.max_request_bytes,
            label="RadioReference HTTPS maximum request bytes",
        )
        self.max_response_bytes = _validate_byte_limit(
            self.max_response_bytes,
            label="RadioReference HTTPS maximum response bytes",
        )

    def exchange(
        self,
        operation: RadioReferenceWsdlOperation,
        request: bytes,
        *,
        soap_action: str,
    ) -> bytes:
        """POST one exact SOAP request and return its exact bounded XML body."""

        if self._closed:
            raise RadioReferenceError(RadioReferenceErrorReason.SERVICE_FAILED)
        if not isinstance(operation, RadioReferenceWsdlOperation):
            raise TypeError(
                "RadioReference operation must be RadioReferenceWsdlOperation."
            )
        if type(request) is not bytes:
            raise TypeError("RadioReference SOAP request must be bytes.")
        if not request:
            raise ValueError("RadioReference SOAP request must not be empty.")
        if len(request) > self.max_request_bytes:
            raise ValueError("RadioReference SOAP request exceeds its byte limit.")
        if type(soap_action) is not str:
            raise TypeError("RadioReference SOAP action must be a string.")
        if soap_action != radioreference_operation_contract(operation).soap_action:
            raise ValueError("RadioReference SOAP action does not match operation.")

        hostname, port, target = _service_destination()
        context = ssl.create_default_context()
        connection: http.client.HTTPSConnection | None = None
        response: http.client.HTTPResponse | None = None
        result: bytes | None = None
        primary_error: BaseException | None = None
        try:
            try:
                connection = http.client.HTTPSConnection(
                    hostname,
                    port=port,
                    timeout=self.timeout,
                    context=context,
                )
                connection.request(
                    "POST",
                    target,
                    body=request,
                    headers={
                        "Content-Type": "text/xml; charset=utf-8",
                        "SOAPAction": f'"{soap_action}"',
                        "Accept": "text/xml",
                        "Accept-Encoding": "identity",
                    },
                )
                response = connection.getresponse()
            except Exception:
                raise RadioReferenceError(
                    RadioReferenceErrorReason.CONNECTION_FAILED
                ) from None

            if response.status in (401, 403):
                raise RadioReferenceError(
                    RadioReferenceErrorReason.AUTHENTICATION_FAILED
                )
            if response.status != 200:
                raise RadioReferenceError(
                    RadioReferenceErrorReason.SERVICE_FAILED
                )

            try:
                content_length = _validate_response_headers(response)
            except RadioReferenceError:
                raise
            except Exception:
                raise _invalid_response() from None
            if (
                content_length is not None
                and content_length > self.max_response_bytes
            ):
                raise _invalid_response()
            try:
                body = response.read(self.max_response_bytes + 1)
            except Exception:
                raise RadioReferenceError(
                    RadioReferenceErrorReason.CONNECTION_FAILED
                ) from None
            if type(body) is not bytes or not body:
                raise _invalid_response()
            if len(body) > self.max_response_bytes:
                raise _invalid_response()
            result = body
        except BaseException as error:
            primary_error = error
            raise
        finally:
            cleanup_failed = False
            if response is not None:
                try:
                    response.close()
                except Exception:
                    cleanup_failed = True
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    cleanup_failed = True
            if cleanup_failed and primary_error is None:
                raise RadioReferenceError(
                    RadioReferenceErrorReason.CLEANUP_FAILED
                ) from None
        assert result is not None
        return result

    def close(self) -> None:
        """Permanently close this non-persistent exchange."""

        self._closed = True


@dataclass(frozen=True, slots=True)
class RadioReferenceHttpsSoapExchangeFactory:
    """Construct fresh fixed-endpoint HTTPS SOAP exchanges."""

    timeout: float = RADIOREFERENCE_HTTPS_DEFAULT_TIMEOUT
    max_request_bytes: int = RADIOREFERENCE_HTTPS_DEFAULT_MAX_REQUEST_BYTES
    max_response_bytes: int = RADIOREFERENCE_HTTPS_DEFAULT_MAX_RESPONSE_BYTES

    def __post_init__(self) -> None:
        object.__setattr__(self, "timeout", _validate_timeout(self.timeout))
        _validate_byte_limit(
            self.max_request_bytes,
            label="RadioReference HTTPS maximum request bytes",
        )
        _validate_byte_limit(
            self.max_response_bytes,
            label="RadioReference HTTPS maximum response bytes",
        )

    def __call__(self) -> RadioReferenceHttpsSoapExchange:
        """Return one fresh exchange with this non-secret configuration."""

        return RadioReferenceHttpsSoapExchange(
            timeout=self.timeout,
            max_request_bytes=self.max_request_bytes,
            max_response_bytes=self.max_response_bytes,
        )


__all__ = [
    "RADIOREFERENCE_HTTPS_DEFAULT_MAX_REQUEST_BYTES",
    "RADIOREFERENCE_HTTPS_DEFAULT_MAX_RESPONSE_BYTES",
    "RADIOREFERENCE_HTTPS_DEFAULT_TIMEOUT",
    "RadioReferenceHttpsSoapExchange",
    "RadioReferenceHttpsSoapExchangeFactory",
]
