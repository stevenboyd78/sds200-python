from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

import sds200
from sds200 import (
    FavoritesExternalChangeKind,
    FavoritesExternalFieldBinding,
    FavoritesExternalFieldObservation,
    FavoritesExternalFieldObservationState,
    FavoritesExternalFieldOwnership,
    FavoritesExternalObservationEvidence,
    FavoritesExternalRecordIdentity,
    FavoritesExternalRecordObservation,
    FavoritesExternalSourceIdentity,
    FavoritesRecordSourceKind,
    FavoritesRecordTarget,
    FavoritesSourceRecord,
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
    RadioReferenceConfiguration,
    RadioReferenceCredential,
    RadioReferenceError,
    RadioReferenceErrorReason,
    RadioReferenceFrequency,
    RadioReferenceSoapStyle,
    RadioReferenceSource,
    RadioReferenceTag,
    bind_favorites_external_record,
    execute_favorites_external_name_acceptance,
    plan_favorites_external_name_acceptance,
    preview_favorites_external_source,
    radioreference_frequency_observation,
    select_favorites_record_target,
)

_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "favorites"

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


def _mapped_frequency_observation(
    *,
    alpha_tag: str = "Dispatch",
) -> FavoritesExternalRecordObservation:
    return radioreference_frequency_observation(
        RadioReferenceFrequency(
            frequency_id=101,
            output_frequency=Decimal("155.1000"),
            input_frequency=Decimal("0"),
            callsign="WXYZ123",
            description="Dispatch description",
            alpha_tag=alpha_tag,
            tone="123.0 PL",
            color_code="",
            talkgroup="",
            slot="",
            mode="FMN",
            encryption=0,
            class_code="PW",
            tags=(RadioReferenceTag(tag_id=2, description="Fire Dispatch"),),
            subcategory_id=7,
            sort=10,
            last_updated=datetime(2026, 8, 13, 9, 21, 4),
        ),
        source=FavoritesExternalSourceIdentity(
            provider="radioreference",
            dataset="synthetic-county",
        ),
        observed_at=datetime(2026, 8, 13, tzinfo=UTC),
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


def test_radioreference_bound_provenance_flows_into_source_update_preview() -> None:
    accepted = _mapped_frequency_observation(alpha_tag="Dispatch")
    target = FavoritesRecordTarget(
        source_kind=FavoritesRecordSourceKind.HPD,
        document_index=0,
        filename="f_000001.hpd",
        source_index=3,
        record=FavoritesSourceRecord(
            content=b"C-Freq\tDispatch\t155.1000",
            line_ending=b"\r\n",
        ),
    )
    state = bind_favorites_external_record(
        target,
        accepted,
        (
            FavoritesExternalFieldBinding(
                name="name",
                field_index=0,
                ownership=FavoritesExternalFieldOwnership.EXTERNAL,
            ),
        ),
    )
    source = RadioReferenceSource(
        configuration=_configuration(),
        session_factory=_RecordingFactory(
            _FakeSession(
                observations=(
                    _mapped_frequency_observation(alpha_tag="Fire Dispatch"),
                ),
            )
        ),
        secret_resolver=_resolver,
    )

    preview = preview_favorites_external_source((state,), source)

    assert accepted.fields[0].name == "name"
    assert accepted.fields[0].value == "Dispatch"
    assert accepted.fields[1].name == "frequency"
    assert accepted.fields[1].value == "155100000"
    assert len(preview.records) == 1
    assert preview.records[0].kind is FavoritesExternalChangeKind.REPLACED
    assert preview.records[0].external_identity == accepted.identity
    assert preview.records[0].target is target
    assert len(preview.records[0].fields) == 2
    fields = {
        field.name: field
        for field in preview.records[0].fields
    }

    name = fields["name"]
    assert name.kind is FavoritesExternalChangeKind.REPLACED
    assert name.local_value == "Dispatch"
    assert name.external_value == "Fire Dispatch"

    frequency = fields["frequency"]
    assert frequency.kind is FavoritesExternalChangeKind.ADDED
    assert frequency.local_value is None
    assert frequency.external_value == "155100000"




def test_radioreference_mapped_name_flows_into_real_favorites_acceptance_plan() -> None:
    snapshot = FavoritesStorageSnapshot(
        catalog_bytes=(_FIXTURE_ROOT / "synthetic-f_list.cfg").read_bytes(),
        documents=(
            FavoritesStorageDocument(
                filename="f_000001.hpd",
                content=(
                    _FIXTURE_ROOT / "synthetic-favorites.hpd"
                ).read_bytes(),
            ),
        ),
    )
    target = select_favorites_record_target(
        snapshot,
        5,
        document_index=0,
    )
    accepted = _mapped_frequency_observation(
        alpha_tag=target.record.fields[2],
    )
    state = bind_favorites_external_record(
        target,
        accepted,
        (
            FavoritesExternalFieldBinding(
                name="name",
                field_index=2,
                ownership=FavoritesExternalFieldOwnership.EXTERNAL,
            ),
        ),
    )
    updated = _mapped_frequency_observation(alpha_tag="Fire Dispatch")

    acceptance = plan_favorites_external_name_acceptance(
        snapshot,
        state,
        updated,
    )

    fields = {field.name: field for field in acceptance.preview.fields}
    assert fields["name"].kind is FavoritesExternalChangeKind.REPLACED
    assert fields["name"].external_value == "Fire Dispatch"
    assert fields["frequency"].kind is FavoritesExternalChangeKind.ADDED
    assert fields["frequency"].local_value is None
    assert fields["frequency"].external_value == "155100000"

    assert acceptance.write_plan.has_changes is True
    assert acceptance.write_plan.is_blocked is False
    assert acceptance.intended_state.target.record.fields[2] == "Fire Dispatch"
    assert acceptance.intended_state.fields[0].field_index == 2
    assert acceptance.intended_state.fields[0].last_external == updated.fields[0]

    class ReadbackSource:
        def read_snapshot(self) -> FavoritesStorageSnapshot:
            return acceptance.write_plan.intended_snapshot

    execution = execute_favorites_external_name_acceptance(
        acceptance,
        lambda _: "synthetic-backend-result",
        ReadbackSource(),
    )

    assert execution.execution_result == "synthetic-backend-result"
    assert execution.accepted_state is acceptance.intended_state
    assert execution.accepted_state.target.record.fields[2] == "Fire Dispatch"
    assert all(
        field.name != "frequency"
        for field in execution.accepted_state.fields
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
