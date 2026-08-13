"""RadioReference documented-interface credential and source foundation."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, TypeAlias

from .favorites_external import FavoritesExternalRecordObservation

RADIOREFERENCE_PROVIDER = "radioreference"
RADIOREFERENCE_SERVICE_URL = "https://api.radioreference.com/soap2/"
RADIOREFERENCE_DEFAULT_VERSION = "latest"
RADIOREFERENCE_WSDL_URL = (
    "https://api.radioreference.com/soap2/?wsdl&v=latest"
)


class RadioReferenceSoapStyle(StrEnum):
    """Documented RadioReference SOAP message styles."""

    RPC = "rpc"
    DOCUMENT = "doc"


class RadioReferenceErrorReason(StrEnum):
    """Stable redacted RadioReference failure classes."""

    CREDENTIAL_UNAVAILABLE = "credential_unavailable"
    CONNECTION_FAILED = "connection_failed"
    AUTHENTICATION_FAILED = "authentication_failed"
    SERVICE_FAILED = "service_failed"
    INVALID_RESPONSE = "invalid_response"
    CLEANUP_FAILED = "cleanup_failed"


_RADIOREFERENCE_ERROR_MESSAGES = {
    RadioReferenceErrorReason.CREDENTIAL_UNAVAILABLE: (
        "RadioReference credentials are unavailable."
    ),
    RadioReferenceErrorReason.CONNECTION_FAILED: (
        "Could not establish the RadioReference service session."
    ),
    RadioReferenceErrorReason.AUTHENTICATION_FAILED: (
        "RadioReference authentication failed."
    ),
    RadioReferenceErrorReason.SERVICE_FAILED: (
        "Could not read RadioReference observations."
    ),
    RadioReferenceErrorReason.INVALID_RESPONSE: (
        "RadioReference returned invalid normalized observations."
    ),
    RadioReferenceErrorReason.CLEANUP_FAILED: (
        "Could not close the RadioReference service session."
    ),
}


class RadioReferenceError(RuntimeError):
    """Report one stable RadioReference failure without provider text."""

    def __init__(self, reason: RadioReferenceErrorReason) -> None:
        if not isinstance(reason, RadioReferenceErrorReason):
            raise TypeError(
                "RadioReference error reason must be RadioReferenceErrorReason."
            )
        self.reason = reason
        super().__init__(_RADIOREFERENCE_ERROR_MESSAGES[reason])


def _validate_text(value: str, *, label: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{label} must be a string.")
    if not value or value.strip() != value:
        raise ValueError(f"{label} must not be empty or padded.")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{label} must not contain control characters.")
    return value


def _validate_secret_reference(value: str, *, label: str) -> str:
    reference = _validate_text(value, label=label)
    if "=" in reference or any(character.isspace() for character in reference):
        raise ValueError(f"{label} is invalid.")
    return reference


def _validate_version(value: str) -> str:
    version = _validate_text(value, label="RadioReference Web Service version")
    if version == "latest":
        return version
    if (
        not version.isascii()
        or not version.isdecimal()
        or version.startswith("0")
    ):
        raise ValueError(
            "RadioReference Web Service version must be 'latest' "
            "or a positive integer string."
        )
    return version


@dataclass(frozen=True, slots=True)
class RadioReferenceCredential:
    """Username plus application-key and password secret references."""

    username: str
    application_key_environment_variable: str
    password_environment_variable: str

    def __post_init__(self) -> None:
        _validate_text(
            self.username,
            label="RadioReference username",
        )
        _validate_secret_reference(
            self.application_key_environment_variable,
            label="RadioReference application-key environment-variable name",
        )
        _validate_secret_reference(
            self.password_environment_variable,
            label="RadioReference password environment-variable name",
        )


@dataclass(frozen=True, slots=True)
class RadioReferenceConfiguration:
    """Secret-free settings for the documented RadioReference service."""

    credential: RadioReferenceCredential
    version: str = RADIOREFERENCE_DEFAULT_VERSION
    style: RadioReferenceSoapStyle = RadioReferenceSoapStyle.RPC

    def __post_init__(self) -> None:
        if not isinstance(self.credential, RadioReferenceCredential):
            raise TypeError(
                "RadioReference configuration requires "
                "RadioReferenceCredential."
            )
        object.__setattr__(
            self,
            "version",
            _validate_version(self.version),
        )
        if not isinstance(self.style, RadioReferenceSoapStyle):
            raise TypeError(
                "RadioReference SOAP style must be RadioReferenceSoapStyle."
            )

    @property
    def wsdl_url(self) -> str:
        """Return a deterministic documented WSDL URL without credentials."""

        return (
            f"{RADIOREFERENCE_SERVICE_URL}?wsdl"
            f"&v={self.version}"
            f"&s={self.style.value}"
        )


class RadioReferenceSession(Protocol):
    """Narrow provider session returning normalized external observations."""

    def read_observations(
        self,
    ) -> tuple[FavoritesExternalRecordObservation, ...]:
        """Return one immutable normalized RadioReference observation set."""
        ...

    def close(self) -> None:
        """Close the provider session deterministically."""
        ...


class RadioReferenceSessionFactory(Protocol):
    """Construct one provider session from ephemeral resolved secrets."""

    def __call__(
        self,
        configuration: RadioReferenceConfiguration,
        *,
        application_key: str,
        password: str,
    ) -> RadioReferenceSession:
        """Return one owned RadioReference session."""
        ...


RadioReferenceSecretResolver: TypeAlias = Callable[[str], str]


def _environment_secret_resolver(variable: str) -> str:
    return os.environ[variable]


def _invalid_response() -> RadioReferenceError:
    return RadioReferenceError(RadioReferenceErrorReason.INVALID_RESPONSE)


def _validate_observations(
    observations: object,
) -> tuple[FavoritesExternalRecordObservation, ...]:
    if type(observations) is not tuple:
        raise _invalid_response()

    typed = observations
    if any(
        not isinstance(observation, FavoritesExternalRecordObservation)
        for observation in typed
    ):
        raise _invalid_response()

    if any(
        observation.identity.source.provider != RADIOREFERENCE_PROVIDER
        for observation in typed
    ):
        raise _invalid_response()

    identities = tuple(observation.identity for observation in typed)
    if len(set(identities)) != len(identities):
        raise _invalid_response()

    return tuple(
        sorted(
            typed,
            key=lambda observation: observation.identity.sort_key,
        )
    )


def _close_after_primary_failure(session: object) -> None:
    closer = getattr(session, "close", None)
    if not callable(closer):
        return
    try:
        closer()
    except Exception:
        return


@dataclass(frozen=True, slots=True)
class RadioReferenceSource:
    """Resolve secrets, own one fakeable session, and return safe observations."""

    configuration: RadioReferenceConfiguration
    session_factory: RadioReferenceSessionFactory
    secret_resolver: RadioReferenceSecretResolver = _environment_secret_resolver

    def __post_init__(self) -> None:
        if not isinstance(self.configuration, RadioReferenceConfiguration):
            raise TypeError(
                "RadioReference source requires RadioReferenceConfiguration."
            )
        if not callable(self.session_factory):
            raise TypeError(
                "RadioReference session factory must be callable."
            )
        if not callable(self.secret_resolver):
            raise TypeError(
                "RadioReference secret resolver must be callable."
            )

    def _resolve_secret(self, reference: str) -> str:
        try:
            value = self.secret_resolver(reference)
        except Exception:
            raise RadioReferenceError(
                RadioReferenceErrorReason.CREDENTIAL_UNAVAILABLE
            ) from None

        if type(value) is not str or not value:
            raise RadioReferenceError(
                RadioReferenceErrorReason.CREDENTIAL_UNAVAILABLE
            )
        return value

    def read_observations(
        self,
    ) -> tuple[FavoritesExternalRecordObservation, ...]:
        """Read one normalized observation set without retaining secret values."""

        application_key = ""
        password = ""
        try:
            application_key = self._resolve_secret(
                self.configuration.credential.application_key_environment_variable
            )
            password = self._resolve_secret(
                self.configuration.credential.password_environment_variable
            )

            try:
                session = self.session_factory(
                    self.configuration,
                    application_key=application_key,
                    password=password,
                )
            except RadioReferenceError as error:
                raise RadioReferenceError(error.reason) from None
            except Exception:
                raise RadioReferenceError(
                    RadioReferenceErrorReason.CONNECTION_FAILED
                ) from None

            reader = getattr(session, "read_observations", None)
            closer = getattr(session, "close", None)
            if not callable(reader) or not callable(closer):
                _close_after_primary_failure(session)
                raise _invalid_response()

            try:
                observations = reader()
            except RadioReferenceError as error:
                primary_error: RadioReferenceError | None = RadioReferenceError(
                    error.reason
                )
                validated = None
            except Exception:
                primary_error = RadioReferenceError(
                    RadioReferenceErrorReason.SERVICE_FAILED
                )
                validated = None
            else:
                try:
                    validated = _validate_observations(observations)
                except RadioReferenceError as error:
                    primary_error = RadioReferenceError(error.reason)
                    validated = None
                else:
                    primary_error = None

            if primary_error is not None:
                _close_after_primary_failure(session)
                raise primary_error from None

            try:
                closer()
            except Exception:
                raise RadioReferenceError(
                    RadioReferenceErrorReason.CLEANUP_FAILED
                ) from None

            if validated is None:
                raise _invalid_response()
            return validated
        finally:
            application_key = ""
            password = ""


__all__ = [
    "RADIOREFERENCE_DEFAULT_VERSION",
    "RADIOREFERENCE_PROVIDER",
    "RADIOREFERENCE_SERVICE_URL",
    "RADIOREFERENCE_WSDL_URL",
    "RadioReferenceConfiguration",
    "RadioReferenceCredential",
    "RadioReferenceError",
    "RadioReferenceErrorReason",
    "RadioReferenceSecretResolver",
    "RadioReferenceSession",
    "RadioReferenceSessionFactory",
    "RadioReferenceSoapStyle",
    "RadioReferenceSource",
]
