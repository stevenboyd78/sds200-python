from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

import sds200
from sds200 import (
    FavoritesExternalChangeKind,
    FavoritesExternalFieldObservation,
    FavoritesExternalFieldObservationState,
    FavoritesExternalObservationEvidence,
    FavoritesExternalRecordIdentity,
    FavoritesExternalRecordObservation,
    FavoritesExternalSourceIdentity,
    RadioReferenceConfiguration,
    RadioReferenceCredential,
    RadioReferenceError,
    RadioReferenceErrorReason,
    RadioReferenceSoapStyle,
    RadioReferenceSource,
    preview_favorites_external_source,
)

APP_KEY_REFERENCE = "SDS200_RADIOREFERENCE_APP_KEY"
PASSWORD_REFERENCE = "SDS200_RADIOREFERENCE_PASSWORD"
SYNTHETIC_APP_KEY = "synthetic-application-key"
SYNTHETIC_PASSWORD = "synthetic-user-password"


def _credential() -> RadioReferenceCredential:
    return RadioReferenceCredential(
        username="synthetic-user",
        application_key_environment_variable=APP_KEY_REFERENCE,
        password_environment_variable=PASSWORD_REFERENCE,
    )


def _configuration(
    *,
    version: str = "latest",
    style: RadioReferenceSoapStyle = RadioReferenceSoapStyle.RPC,
) -> RadioReferenceConfiguration:
    return RadioReferenceConfiguration(
        credential=_credential(),
        version=version,
        style=style,
    )


def _observation(
    record_id: str = "frequency-1",
    *,
    provider: str = "radioreference",
) -> FavoritesExternalRecordObservation:
    return FavoritesExternalRecordObservation(
        identity=FavoritesExternalRecordIdentity(
            source=FavoritesExternalSourceIdentity(
                provider=provider,
                dataset="synthetic-county",
            ),
            record_id=record_id,
        ),
        evidence=FavoritesExternalObservationEvidence(
            observed_at=datetime(2026, 8, 13, tzinfo=UTC),
            revision=None,
        ),
        fields=(
            FavoritesExternalFieldObservation(
                name="frequency",
                state=FavoritesExternalFieldObservationState.VALUE,
                value="155.1000",
            ),
        ),
    )


class _FakeSession:
    def __init__(
        self,
        observations: object = None,
        *,
        read_error: Exception | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self.observations = (
            (_observation(),)
            if observations is None
            else observations
        )
        self.read_error = read_error
        self.close_error = close_error
        self.read_calls = 0
        self.close_calls = 0

    def read_observations(
        self,
    ) -> tuple[FavoritesExternalRecordObservation, ...]:
        self.read_calls += 1
        if self.read_error is not None:
            raise self.read_error
        return self.observations  # type: ignore[return-value]

    def close(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


class _RecordingFactory:
    def __init__(
        self,
        session: object,
        *,
        error: Exception | None = None,
    ) -> None:
        self.session = session
        self.error = error
        self.calls = 0
        self.configuration: RadioReferenceConfiguration | None = None
        self.application_key: str | None = None
        self.password: str | None = None

    def __call__(
        self,
        configuration: RadioReferenceConfiguration,
        *,
        application_key: str,
        password: str,
    ) -> object:
        self.calls += 1
        self.configuration = configuration
        self.application_key = application_key
        self.password = password
        if self.error is not None:
            raise self.error
        return self.session


def _resolver(variable: str) -> str:
    values = {
        APP_KEY_REFERENCE: SYNTHETIC_APP_KEY,
        PASSWORD_REFERENCE: SYNTHETIC_PASSWORD,
    }
    return values[variable]


def test_radioreference_documented_defaults_are_stable() -> None:
    configuration = _configuration()

    assert sds200.RADIOREFERENCE_PROVIDER == "radioreference"
    assert (
        sds200.RADIOREFERENCE_SERVICE_URL
        == "https://api.radioreference.com/soap2/"
    )
    assert sds200.RADIOREFERENCE_DEFAULT_VERSION == "latest"
    assert (
        sds200.RADIOREFERENCE_WSDL_URL
        == "https://api.radioreference.com/soap2/?wsdl&v=latest"
    )
    assert configuration.version == "latest"
    assert configuration.style is RadioReferenceSoapStyle.RPC
    assert (
        configuration.wsdl_url
        == "https://api.radioreference.com/soap2/?wsdl&v=latest&s=rpc"
    )


def test_radioreference_configuration_is_immutable_and_secret_free() -> None:
    configuration = _configuration(
        version="18",
        style=RadioReferenceSoapStyle.DOCUMENT,
    )

    assert configuration.credential.username == "synthetic-user"
    assert configuration.version == "18"
    assert configuration.style is RadioReferenceSoapStyle.DOCUMENT
    assert (
        configuration.wsdl_url
        == "https://api.radioreference.com/soap2/?wsdl&v=18&s=doc"
    )
    assert SYNTHETIC_APP_KEY not in repr(configuration)
    assert SYNTHETIC_PASSWORD not in repr(configuration)

    with pytest.raises(FrozenInstanceError):
        configuration.version = "latest"  # type: ignore[misc]


@pytest.mark.parametrize(
    "version",
    (
        "",
        " latest",
        "latest ",
        "0",
        "01",
        "-1",
        "18.0",
        "LATEST",
        "１８",
    ),
)
def test_radioreference_configuration_rejects_invalid_versions(
    version: str,
) -> None:
    with pytest.raises(ValueError):
        _configuration(version=version)


def test_radioreference_configuration_requires_typed_soap_style() -> None:
    with pytest.raises(TypeError):
        RadioReferenceConfiguration(
            credential=_credential(),
            style="rpc",  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("username", "app_key_reference", "password_reference"),
    (
        ("", APP_KEY_REFERENCE, PASSWORD_REFERENCE),
        (" user ", APP_KEY_REFERENCE, PASSWORD_REFERENCE),
        ("user\nname", APP_KEY_REFERENCE, PASSWORD_REFERENCE),
        ("user", "", PASSWORD_REFERENCE),
        ("user", "BAD SECRET", PASSWORD_REFERENCE),
        ("user", "BAD=SECRET", PASSWORD_REFERENCE),
        ("user", APP_KEY_REFERENCE, ""),
        ("user", APP_KEY_REFERENCE, "BAD SECRET"),
        ("user", APP_KEY_REFERENCE, "BAD=SECRET"),
    ),
)
def test_radioreference_credential_rejects_unsafe_values(
    username: str,
    app_key_reference: str,
    password_reference: str,
) -> None:
    with pytest.raises(ValueError):
        RadioReferenceCredential(
            username=username,
            application_key_environment_variable=app_key_reference,
            password_environment_variable=password_reference,
        )


def test_radioreference_source_resolves_ephemeral_secrets_and_closes() -> None:
    session = _FakeSession()
    factory = _RecordingFactory(session)
    source = RadioReferenceSource(
        configuration=_configuration(),
        session_factory=factory,
        secret_resolver=_resolver,
    )

    observations = source.read_observations()

    assert observations == (_observation(),)
    assert factory.calls == 1
    assert factory.configuration is source.configuration
    assert factory.application_key == SYNTHETIC_APP_KEY
    assert factory.password == SYNTHETIC_PASSWORD
    assert session.read_calls == 1
    assert session.close_calls == 1
    assert SYNTHETIC_APP_KEY not in repr(source)
    assert SYNTHETIC_PASSWORD not in repr(source)


def test_radioreference_source_uses_environment_secret_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(APP_KEY_REFERENCE, SYNTHETIC_APP_KEY)
    monkeypatch.setenv(PASSWORD_REFERENCE, SYNTHETIC_PASSWORD)
    session = _FakeSession()
    factory = _RecordingFactory(session)

    source = RadioReferenceSource(
        configuration=_configuration(),
        session_factory=factory,
    )

    assert source.read_observations() == (_observation(),)
    assert factory.application_key == SYNTHETIC_APP_KEY
    assert factory.password == SYNTHETIC_PASSWORD


def test_radioreference_missing_secret_fails_before_session() -> None:
    factory = _RecordingFactory(_FakeSession())

    def missing(_variable: str) -> str:
        raise KeyError("synthetic secret lookup detail")

    source = RadioReferenceSource(
        configuration=_configuration(),
        session_factory=factory,
        secret_resolver=missing,
    )

    with pytest.raises(RadioReferenceError) as captured:
        source.read_observations()

    assert (
        captured.value.reason
        is RadioReferenceErrorReason.CREDENTIAL_UNAVAILABLE
    )
    assert "synthetic secret lookup detail" not in str(captured.value)
    assert captured.value.__cause__ is None
    assert factory.calls == 0


@pytest.mark.parametrize("value", ("", None, 123))
def test_radioreference_invalid_resolved_secret_fails_closed(
    value: object,
) -> None:
    factory = _RecordingFactory(_FakeSession())

    source = RadioReferenceSource(
        configuration=_configuration(),
        session_factory=factory,
        secret_resolver=lambda _variable: value,  # type: ignore[arg-type,return-value]
    )

    with pytest.raises(
        RadioReferenceError,
        match="credentials are unavailable",
    ):
        source.read_observations()

    assert factory.calls == 0


def test_radioreference_untyped_factory_failure_is_redacted() -> None:
    factory = _RecordingFactory(
        _FakeSession(),
        error=RuntimeError(
            f"transport leaked {SYNTHETIC_APP_KEY} {SYNTHETIC_PASSWORD}"
        ),
    )
    source = RadioReferenceSource(
        configuration=_configuration(),
        session_factory=factory,
        secret_resolver=_resolver,
    )

    with pytest.raises(RadioReferenceError) as captured:
        source.read_observations()

    assert captured.value.reason is RadioReferenceErrorReason.CONNECTION_FAILED
    assert SYNTHETIC_APP_KEY not in str(captured.value)
    assert SYNTHETIC_PASSWORD not in str(captured.value)
    assert captured.value.__cause__ is None
    assert _read_observations_secret_locals(captured.value) == {
        "application_key": "",
        "password": "",
    }


def _read_observations_secret_locals(
    error: BaseException,
) -> dict[str, object]:
    traceback = error.__traceback__
    while traceback is not None:
        if traceback.tb_frame.f_code.co_name == "read_observations":
            return {
                name: traceback.tb_frame.f_locals.get(name)
                for name in ("application_key", "password")
            }
        traceback = traceback.tb_next
    raise AssertionError("read_observations traceback frame was not found")


def test_radioreference_typed_authentication_failure_reason_is_preserved() -> None:
    error = RadioReferenceError(
        RadioReferenceErrorReason.AUTHENTICATION_FAILED
    )
    source = RadioReferenceSource(
        configuration=_configuration(),
        session_factory=_RecordingFactory(
            _FakeSession(),
            error=error,
        ),
        secret_resolver=_resolver,
    )

    with pytest.raises(RadioReferenceError) as captured:
        source.read_observations()

    assert captured.value is not error
    assert (
        captured.value.reason
        is RadioReferenceErrorReason.AUTHENTICATION_FAILED
    )
    assert captured.value.__cause__ is None
    assert _read_observations_secret_locals(captured.value) == {
        "application_key": "",
        "password": "",
    }


def test_radioreference_typed_read_failure_reason_is_preserved_and_redacted() -> None:
    error = RadioReferenceError(
        RadioReferenceErrorReason.AUTHENTICATION_FAILED
    )
    session = _FakeSession(read_error=error)
    source = RadioReferenceSource(
        configuration=_configuration(),
        session_factory=_RecordingFactory(session),
        secret_resolver=_resolver,
    )

    with pytest.raises(RadioReferenceError) as captured:
        source.read_observations()

    assert captured.value is not error
    assert (
        captured.value.reason
        is RadioReferenceErrorReason.AUTHENTICATION_FAILED
    )
    assert captured.value.__cause__ is None
    assert session.close_calls == 1
    assert _read_observations_secret_locals(captured.value) == {
        "application_key": "",
        "password": "",
    }


def test_radioreference_untyped_read_failure_is_redacted_and_closed() -> None:
    session = _FakeSession(
        read_error=RuntimeError(
            f"provider fault leaked {SYNTHETIC_PASSWORD}"
        )
    )
    source = RadioReferenceSource(
        configuration=_configuration(),
        session_factory=_RecordingFactory(session),
        secret_resolver=_resolver,
    )

    with pytest.raises(RadioReferenceError) as captured:
        source.read_observations()

    assert captured.value.reason is RadioReferenceErrorReason.SERVICE_FAILED
    assert SYNTHETIC_PASSWORD not in str(captured.value)
    assert captured.value.__cause__ is None
    assert session.close_calls == 1


def test_radioreference_primary_failure_wins_over_cleanup_failure() -> None:
    session = _FakeSession(
        read_error=RuntimeError("synthetic provider detail"),
        close_error=RuntimeError("synthetic cleanup detail"),
    )
    source = RadioReferenceSource(
        configuration=_configuration(),
        session_factory=_RecordingFactory(session),
        secret_resolver=_resolver,
    )

    with pytest.raises(RadioReferenceError) as captured:
        source.read_observations()

    assert captured.value.reason is RadioReferenceErrorReason.SERVICE_FAILED
    assert "synthetic cleanup detail" not in str(captured.value)
    assert session.close_calls == 1


def test_radioreference_cleanup_failure_after_success_is_redacted() -> None:
    session = _FakeSession(
        close_error=RuntimeError(
            f"cleanup leaked {SYNTHETIC_APP_KEY}"
        )
    )
    source = RadioReferenceSource(
        configuration=_configuration(),
        session_factory=_RecordingFactory(session),
        secret_resolver=_resolver,
    )

    with pytest.raises(RadioReferenceError) as captured:
        source.read_observations()

    assert captured.value.reason is RadioReferenceErrorReason.CLEANUP_FAILED
    assert SYNTHETIC_APP_KEY not in str(captured.value)
    assert captured.value.__cause__ is None


def test_radioreference_source_rejects_mutable_observation_collection() -> None:
    session = _FakeSession(observations=[_observation()])
    source = RadioReferenceSource(
        configuration=_configuration(),
        session_factory=_RecordingFactory(session),
        secret_resolver=_resolver,
    )

    with pytest.raises(RadioReferenceError) as captured:
        source.read_observations()

    assert captured.value.reason is RadioReferenceErrorReason.INVALID_RESPONSE
    assert session.close_calls == 1


def test_radioreference_source_rejects_wrong_observation_type() -> None:
    session = _FakeSession(observations=("not-an-observation",))
    source = RadioReferenceSource(
        configuration=_configuration(),
        session_factory=_RecordingFactory(session),
        secret_resolver=_resolver,
    )

    with pytest.raises(RadioReferenceError) as captured:
        source.read_observations()

    assert captured.value.reason is RadioReferenceErrorReason.INVALID_RESPONSE
    assert session.close_calls == 1


def test_radioreference_source_rejects_non_radioreference_provider() -> None:
    session = _FakeSession(
        observations=(_observation(provider="other-provider"),)
    )
    source = RadioReferenceSource(
        configuration=_configuration(),
        session_factory=_RecordingFactory(session),
        secret_resolver=_resolver,
    )

    with pytest.raises(RadioReferenceError) as captured:
        source.read_observations()

    assert captured.value.reason is RadioReferenceErrorReason.INVALID_RESPONSE


def test_radioreference_source_rejects_duplicate_provider_identity() -> None:
    observation = _observation()
    session = _FakeSession(observations=(observation, observation))
    source = RadioReferenceSource(
        configuration=_configuration(),
        session_factory=_RecordingFactory(session),
        secret_resolver=_resolver,
    )

    with pytest.raises(RadioReferenceError) as captured:
        source.read_observations()

    assert captured.value.reason is RadioReferenceErrorReason.INVALID_RESPONSE


def test_radioreference_source_orders_normalized_observations() -> None:
    session = _FakeSession(
        observations=(
            _observation("z-record"),
            _observation("a-record"),
        )
    )
    source = RadioReferenceSource(
        configuration=_configuration(),
        session_factory=_RecordingFactory(session),
        secret_resolver=_resolver,
    )

    observations = source.read_observations()

    assert [
        observation.identity.record_id for observation in observations
    ] == ["a-record", "z-record"]


def test_radioreference_source_integrates_with_external_preview() -> None:
    source = RadioReferenceSource(
        configuration=_configuration(),
        session_factory=_RecordingFactory(_FakeSession()),
        secret_resolver=_resolver,
    )

    preview = preview_favorites_external_source((), source)

    assert len(preview.records) == 1
    assert preview.records[0].kind is FavoritesExternalChangeKind.ADDED
    assert preview.records[0].external_identity is not None
    assert (
        preview.records[0].external_identity.source.provider
        == sds200.RADIOREFERENCE_PROVIDER
    )


def test_radioreference_error_messages_are_stable_and_message_free() -> None:
    for reason in RadioReferenceErrorReason:
        error = RadioReferenceError(reason)
        assert error.reason is reason
        assert str(error)
        assert SYNTHETIC_APP_KEY not in str(error)
        assert SYNTHETIC_PASSWORD not in str(error)

    with pytest.raises(TypeError):
        RadioReferenceError("service_failed")  # type: ignore[arg-type]


def test_radioreference_source_requires_callable_boundaries() -> None:
    with pytest.raises(TypeError):
        RadioReferenceSource(
            configuration=_configuration(),
            session_factory=None,  # type: ignore[arg-type]
        )

    with pytest.raises(TypeError):
        RadioReferenceSource(
            configuration=_configuration(),
            session_factory=_RecordingFactory(_FakeSession()),
            secret_resolver=None,  # type: ignore[arg-type]
        )


def test_radioreference_source_rejects_invalid_session_object() -> None:
    source = RadioReferenceSource(
        configuration=_configuration(),
        session_factory=_RecordingFactory(object()),
        secret_resolver=_resolver,
    )

    with pytest.raises(RadioReferenceError) as captured:
        source.read_observations()

    assert captured.value.reason is RadioReferenceErrorReason.INVALID_RESPONSE


def test_radioreference_public_symbols_are_package_exports() -> None:
    expected = (
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
    )

    for name in expected:
        assert name in sds200.__all__
        assert hasattr(sds200, name)
