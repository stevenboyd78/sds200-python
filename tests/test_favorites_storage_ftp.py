from __future__ import annotations

import ftplib
from collections.abc import Callable
from dataclasses import FrozenInstanceError
from typing import cast

import pytest

import sds200
import sds200.favorites_storage_ftp as ftp_storage
from sds200 import (
    FAVORITES_FTP_DEFAULT_MAX_CATALOG_BYTES,
    FAVORITES_FTP_DEFAULT_MAX_DOCUMENT_BYTES,
    FAVORITES_FTP_DEFAULT_MAX_LISTING_ENTRIES,
    FAVORITES_FTP_DEFAULT_MAX_SNAPSHOT_BYTES,
    FAVORITES_FTP_DEFAULT_PORT,
    FAVORITES_FTP_DEFAULT_TIMEOUT,
    FavoritesFtpReadConfiguration,
    FavoritesFtpReadCredential,
    FavoritesFtpSession,
    FavoritesFtpStorageError,
    FavoritesFtpStorageErrorReason,
    FavoritesFtpStorageSource,
)

_SECRET_VARIABLE = "SDSCTL_TEST_FAVORITES_FTP_READ_PASSWORD"
_SECRET_VALUE = "private-test-secret"


def _configuration(
    **overrides: object,
) -> FavoritesFtpReadConfiguration:
    values: dict[str, object] = {
        "host": "192.0.2.10",
        "favorites_directory": "/BCDx36HP/favorites_lists",
        "credential": FavoritesFtpReadCredential(
            username="reader",
            password_environment_variable=_SECRET_VARIABLE,
        ),
    }
    values.update(overrides)
    return FavoritesFtpReadConfiguration(
        **values  # type: ignore[arg-type]
    )


def _snapshot_pass(
    *,
    catalog: bytes = b"catalog\r\n",
    documents: dict[str, bytes] | None = None,
    names: tuple[str, ...] | None = None,
) -> tuple[tuple[str, ...], dict[str, bytes]]:
    payloads = (
        {
            "f_000002.hpd": b"second\r\n",
            "f_000001.hpd": b"first\r\n",
        }
        if documents is None
        else dict(documents)
    )
    payloads["f_list.cfg"] = catalog
    listing = (
        (
            "f_000002.hpd",
            "f_list.cfg",
            "ignored.txt",
            "f_000001.hpd",
        )
        if names is None
        else names
    )
    return listing, payloads


class _FakeSession:
    def __init__(
        self,
        passes: tuple[
            tuple[tuple[str, ...], dict[str, bytes]],
            ...,
        ],
        *,
        close_error: BaseException | None = None,
    ) -> None:
        self._passes = passes
        self._pass_index = -1
        self._current_payloads: dict[str, bytes] = {}
        self.close_error = close_error
        self.closed = False
        self.retrievals: list[tuple[str, int]] = []

    def list_names(self) -> tuple[str, ...]:
        self._pass_index += 1
        if self._pass_index >= len(self._passes):
            raise AssertionError(
                "unexpected extra FTP listing pass"
            )
        names, payloads = self._passes[self._pass_index]
        self._current_payloads = payloads
        return names

    def retrieve_file(
        self,
        filename: str,
        *,
        max_bytes: int,
    ) -> bytes:
        self.retrievals.append((filename, max_bytes))
        return self._current_payloads[filename]

    def close(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


class _RecordingFactory:
    def __init__(self, session: FavoritesFtpSession) -> None:
        self.session = session
        self.calls: list[
            tuple[FavoritesFtpReadConfiguration, str]
        ] = []

    def __call__(
        self,
        configuration: FavoritesFtpReadConfiguration,
        password: str,
    ) -> FavoritesFtpSession:
        self.calls.append((configuration, password))
        return self.session


def _source(
    session: FavoritesFtpSession,
    *,
    configuration: FavoritesFtpReadConfiguration | None = None,
    secret_resolver: ftp_storage.FavoritesFtpSecretResolver | None = None,
) -> tuple[FavoritesFtpStorageSource, _RecordingFactory]:
    factory = _RecordingFactory(session)
    source = FavoritesFtpStorageSource(
        configuration=(
            _configuration()
            if configuration is None
            else configuration
        ),
        session_factory=factory,
        secret_resolver=(
            (lambda variable: _SECRET_VALUE)
            if secret_resolver is None
            else secret_resolver
        ),
    )
    return source, factory


def test_ftp_defaults_are_stable() -> None:
    assert FAVORITES_FTP_DEFAULT_PORT == 21
    assert FAVORITES_FTP_DEFAULT_TIMEOUT == 10.0
    assert FAVORITES_FTP_DEFAULT_MAX_LISTING_ENTRIES == 4096
    assert FAVORITES_FTP_DEFAULT_MAX_CATALOG_BYTES == 4 * 1024 * 1024
    assert FAVORITES_FTP_DEFAULT_MAX_DOCUMENT_BYTES == 64 * 1024 * 1024
    assert FAVORITES_FTP_DEFAULT_MAX_SNAPSHOT_BYTES == 256 * 1024 * 1024


def test_ftp_read_configuration_is_immutable_and_secret_free() -> None:
    configuration = _configuration()

    assert configuration.credential.username == "reader"
    assert (
        configuration.credential.password_environment_variable
        == _SECRET_VARIABLE
    )
    assert _SECRET_VALUE not in repr(configuration)

    with pytest.raises(FrozenInstanceError):
        configuration.port = 22  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value", "error"),
    (
        ("host", "", ValueError),
        ("host", " scanner ", ValueError),
        ("host", "ftp://scanner", ValueError),
        ("host", "user@scanner", ValueError),
        ("favorites_directory", "", ValueError),
        (
            "favorites_directory",
            "/BCDx36HP/../favorites_lists",
            ValueError,
        ),
        (
            "favorites_directory",
            "/BCDx36HP//favorites_lists",
            ValueError,
        ),
        (
            "favorites_directory",
            "/BCDx36HP/favorites_lists/",
            ValueError,
        ),
        ("port", 0, ValueError),
        ("port", 65536, ValueError),
        ("port", True, TypeError),
        ("timeout", 0.0, ValueError),
        ("timeout", float("inf"), ValueError),
        ("max_listing_entries", 0, ValueError),
        ("max_catalog_bytes", 0, ValueError),
        ("max_document_bytes", 0, ValueError),
        ("max_snapshot_bytes", 0, ValueError),
    ),
)
def test_ftp_read_configuration_rejects_unsafe_values(
    field: str,
    value: object,
    error: type[Exception],
) -> None:
    with pytest.raises(error):
        _configuration(**{field: value})


@pytest.mark.parametrize(
    ("username", "variable"),
    (
        ("", _SECRET_VARIABLE),
        (" reader ", _SECRET_VARIABLE),
        ("reader\r\nPASS x", _SECRET_VARIABLE),
        ("reader", ""),
        ("reader", " BAD "),
        ("reader", "BAD VAR"),
        ("reader", "BAD=VALUE"),
    ),
)
def test_ftp_read_credential_rejects_unsafe_values(
    username: str,
    variable: str,
) -> None:
    with pytest.raises(ValueError):
        FavoritesFtpReadCredential(
            username=username,
            password_environment_variable=variable,
        )


def test_ftp_source_returns_two_pass_exact_snapshot_in_sorted_order() -> None:
    session = _FakeSession(
        (
            _snapshot_pass(),
            _snapshot_pass(),
        )
    )
    source, factory = _source(session)

    snapshot = source.read_snapshot()

    assert snapshot.catalog_bytes == b"catalog\r\n"
    assert [
        document.filename
        for document in snapshot.documents
    ] == [
        "f_000001.hpd",
        "f_000002.hpd",
    ]
    assert [
        document.content
        for document in snapshot.documents
    ] == [
        b"first\r\n",
        b"second\r\n",
    ]
    assert factory.calls == [
        (source.configuration, _SECRET_VALUE)
    ]
    assert session.closed is True
    assert [
        filename
        for filename, _ in session.retrievals
    ] == [
        "f_list.cfg",
        "f_000001.hpd",
        "f_000002.hpd",
        "f_list.cfg",
        "f_000001.hpd",
        "f_000002.hpd",
    ]


def test_ftp_source_uses_default_environment_secret_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession(
        (
            _snapshot_pass(),
            _snapshot_pass(),
        )
    )
    factory = _RecordingFactory(session)
    monkeypatch.setenv(
        _SECRET_VARIABLE,
        _SECRET_VALUE,
    )

    source = FavoritesFtpStorageSource(
        configuration=_configuration(),
        session_factory=factory,
    )

    source.read_snapshot()

    assert factory.calls[0][1] == _SECRET_VALUE


def test_ftp_source_missing_read_secret_fails_before_session() -> None:
    def missing_secret(variable: str) -> str:
        raise KeyError(variable)

    session = _FakeSession(
        (
            _snapshot_pass(),
            _snapshot_pass(),
        )
    )
    source, factory = _source(
        session,
        secret_resolver=missing_secret,
    )

    with pytest.raises(
        FavoritesFtpStorageError
    ) as captured:
        source.read_snapshot()

    assert (
        captured.value.reason
        is FavoritesFtpStorageErrorReason.CREDENTIAL_UNAVAILABLE
    )
    assert factory.calls == []
    assert _SECRET_VALUE not in str(captured.value)
    assert captured.value.__cause__ is None


@pytest.mark.parametrize(
    "names",
    (
        ("f_list.cfg", "../evil.hpd"),
        ("f_list.cfg", "sub/f_000001.hpd"),
        ("f_list.cfg", "sub\\f_000001.hpd"),
        ("f_list.cfg", "evil\r\nDELE x.hpd"),
        (
            "f_list.cfg",
            "f_000001.hpd",
            "f_000001.hpd",
        ),
        ("f_000001.hpd",),
    ),
)
def test_ftp_source_rejects_unsafe_or_ambiguous_listing(
    names: tuple[str, ...],
) -> None:
    one_pass = _snapshot_pass(
        names=names,
        documents={"f_000001.hpd": b"one"},
    )
    session = _FakeSession((one_pass,))
    source, _ = _source(session)

    with pytest.raises(
        FavoritesFtpStorageError
    ) as captured:
        source.read_snapshot()

    assert (
        captured.value.reason
        is FavoritesFtpStorageErrorReason.UNSAFE_LISTING
    )
    assert session.closed is True


def test_ftp_source_detects_listing_change_between_passes() -> None:
    first = _snapshot_pass(
        names=("f_list.cfg", "f_000001.hpd"),
        documents={"f_000001.hpd": b"one"},
    )
    second = _snapshot_pass(
        names=(
            "f_list.cfg",
            "f_000001.hpd",
            "extra.txt",
        ),
        documents={"f_000001.hpd": b"one"},
    )
    session = _FakeSession((first, second))
    source, _ = _source(session)

    with pytest.raises(
        FavoritesFtpStorageError
    ) as captured:
        source.read_snapshot()

    assert (
        captured.value.reason
        is FavoritesFtpStorageErrorReason.SNAPSHOT_CHANGED
    )
    assert session.closed is True


def test_ftp_source_detects_content_change_between_passes() -> None:
    first = _snapshot_pass(catalog=b"first")
    second = _snapshot_pass(catalog=b"second")
    session = _FakeSession((first, second))
    source, _ = _source(session)

    with pytest.raises(
        FavoritesFtpStorageError
    ) as captured:
        source.read_snapshot()

    assert (
        captured.value.reason
        is FavoritesFtpStorageErrorReason.SNAPSHOT_CHANGED
    )
    assert session.closed is True


def test_ftp_source_enforces_listing_limit() -> None:
    configuration = _configuration(
        max_listing_entries=1
    )
    session = _FakeSession((_snapshot_pass(),))
    source, _ = _source(
        session,
        configuration=configuration,
    )

    with pytest.raises(
        FavoritesFtpStorageError
    ) as captured:
        source.read_snapshot()

    assert (
        captured.value.reason
        is FavoritesFtpStorageErrorReason.LIMIT_EXCEEDED
    )
    assert session.closed is True


def test_ftp_source_enforces_catalog_limit_for_nonconforming_session() -> None:
    configuration = _configuration(
        max_catalog_bytes=3
    )
    session = _FakeSession(
        (_snapshot_pass(catalog=b"four"),)
    )
    source, _ = _source(
        session,
        configuration=configuration,
    )

    with pytest.raises(
        FavoritesFtpStorageError
    ) as captured:
        source.read_snapshot()

    assert (
        captured.value.reason
        is FavoritesFtpStorageErrorReason.LIMIT_EXCEEDED
    )


def test_ftp_source_enforces_document_limit_for_nonconforming_session() -> None:
    configuration = _configuration(
        max_document_bytes=3
    )
    one_pass = _snapshot_pass(
        names=("f_list.cfg", "f_000001.hpd"),
        documents={"f_000001.hpd": b"four"},
    )
    session = _FakeSession((one_pass,))
    source, _ = _source(
        session,
        configuration=configuration,
    )

    with pytest.raises(
        FavoritesFtpStorageError
    ) as captured:
        source.read_snapshot()

    assert (
        captured.value.reason
        is FavoritesFtpStorageErrorReason.LIMIT_EXCEEDED
    )


def test_ftp_source_enforces_complete_snapshot_limit() -> None:
    configuration = _configuration(
        max_catalog_bytes=10,
        max_document_bytes=10,
        max_snapshot_bytes=5,
    )
    one_pass = _snapshot_pass(
        catalog=b"cat",
        documents={"f_000001.hpd": b"doc"},
        names=("f_list.cfg", "f_000001.hpd"),
    )
    session = _FakeSession((one_pass,))
    source, _ = _source(
        session,
        configuration=configuration,
    )

    with pytest.raises(
        FavoritesFtpStorageError
    ) as captured:
        source.read_snapshot()

    assert (
        captured.value.reason
        is FavoritesFtpStorageErrorReason.LIMIT_EXCEEDED
    )


def test_ftp_source_redacts_untyped_listing_failure() -> None:
    class FailingSession(_FakeSession):
        def list_names(self) -> tuple[str, ...]:
            raise RuntimeError(
                f"server leaked {_SECRET_VALUE}"
            )

    session = FailingSession((_snapshot_pass(),))
    source, _ = _source(session)

    with pytest.raises(
        FavoritesFtpStorageError
    ) as captured:
        source.read_snapshot()

    assert (
        captured.value.reason
        is FavoritesFtpStorageErrorReason.LISTING_FAILED
    )
    assert _SECRET_VALUE not in str(captured.value)
    assert captured.value.__cause__ is None
    assert session.closed is True


def test_ftp_source_preserves_primary_failure_when_cleanup_fails() -> None:
    session = _FakeSession(
        (
            _snapshot_pass(
                names=("missing.hpd",)
            ),
        ),
        close_error=RuntimeError(
            "cleanup failure"
        ),
    )
    source, _ = _source(session)

    with pytest.raises(
        FavoritesFtpStorageError
    ) as captured:
        source.read_snapshot()

    assert (
        captured.value.reason
        is FavoritesFtpStorageErrorReason.UNSAFE_LISTING
    )
    assert session.closed is True


def test_ftp_source_surfaces_cleanup_failure_after_success() -> None:
    session = _FakeSession(
        (
            _snapshot_pass(),
            _snapshot_pass(),
        ),
        close_error=FavoritesFtpStorageError(
            FavoritesFtpStorageErrorReason.CLEANUP_FAILED,
            "Could not close the Favorites FTP session cleanly.",
        ),
    )
    source, _ = _source(session)

    with pytest.raises(
        FavoritesFtpStorageError
    ) as captured:
        source.read_snapshot()

    assert (
        captured.value.reason
        is FavoritesFtpStorageErrorReason.CLEANUP_FAILED
    )


class _FakeFtplib:
    def __init__(
        self,
        *,
        login_error: BaseException | None = None,
    ) -> None:
        self.login_error = login_error
        self.calls: list[tuple[object, ...]] = []
        self.payloads = {
            "f_list.cfg": b"catalog",
            "f_000001.hpd": b"document",
        }

    def connect(
        self,
        host: str,
        port: int,
        timeout: float,
    ) -> str:
        self.calls.append(
            ("connect", host, port, timeout)
        )
        return "connected"

    def login(
        self,
        user: str,
        passwd: str,
    ) -> str:
        self.calls.append(
            ("login", user, passwd)
        )
        if self.login_error is not None:
            raise self.login_error
        return "logged in"

    def cwd(self, directory: str) -> str:
        self.calls.append(("cwd", directory))
        return "cwd"

    def nlst(self) -> list[str]:
        self.calls.append(("nlst",))
        return [
            "f_list.cfg",
            "f_000001.hpd",
        ]

    def retrbinary(
        self,
        command: str,
        callback: Callable[[bytes], object],
        blocksize: int = 8192,
        rest: int | None = None,
    ) -> str:
        self.calls.append(
            (
                "retrbinary",
                command,
                blocksize,
                rest,
            )
        )
        filename = command.removeprefix("RETR ")
        callback(self.payloads[filename])
        return "retrieved"

    def quit(self) -> str:
        self.calls.append(("quit",))
        return "bye"

    def close(self) -> None:
        self.calls.append(("close",))


def test_default_ftplib_adapter_uses_only_read_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeFtplib()
    monkeypatch.setattr(
        ftp_storage.ftplib,
        "FTP",
        lambda: cast(ftplib.FTP, fake),
    )

    source = FavoritesFtpStorageSource(
        configuration=_configuration(),
        secret_resolver=lambda variable: _SECRET_VALUE,
    )

    snapshot = source.read_snapshot()

    assert snapshot.catalog_bytes == b"catalog"
    assert snapshot.documents[0].content == b"document"
    assert fake.calls == [
        (
            "connect",
            "192.0.2.10",
            21,
            10.0,
        ),
        (
            "login",
            "reader",
            _SECRET_VALUE,
        ),
        (
            "cwd",
            "/BCDx36HP/favorites_lists",
        ),
        ("nlst",),
        (
            "retrbinary",
            "RETR f_list.cfg",
            64 * 1024,
            None,
        ),
        (
            "retrbinary",
            "RETR f_000001.hpd",
            64 * 1024,
            None,
        ),
        ("nlst",),
        (
            "retrbinary",
            "RETR f_list.cfg",
            64 * 1024,
            None,
        ),
        (
            "retrbinary",
            "RETR f_000001.hpd",
            64 * 1024,
            None,
        ),
        ("quit",),
    ]


def test_default_ftplib_authentication_failure_redacts_server_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeFtplib(
        login_error=ftplib.error_perm(
            f"bad password {_SECRET_VALUE}"
        )
    )
    monkeypatch.setattr(
        ftp_storage.ftplib,
        "FTP",
        lambda: cast(ftplib.FTP, fake),
    )

    source = FavoritesFtpStorageSource(
        configuration=_configuration(),
        secret_resolver=lambda variable: _SECRET_VALUE,
    )

    with pytest.raises(
        FavoritesFtpStorageError
    ) as captured:
        source.read_snapshot()

    assert (
        captured.value.reason
        is FavoritesFtpStorageErrorReason.AUTHENTICATION_FAILED
    )
    assert _SECRET_VALUE not in str(captured.value)
    assert captured.value.__cause__ is None
    assert fake.calls[-1] == ("close",)


def test_ftp_public_symbols_are_package_exports() -> None:
    expected = (
        "FAVORITES_FTP_DEFAULT_MAX_CATALOG_BYTES",
        "FAVORITES_FTP_DEFAULT_MAX_DOCUMENT_BYTES",
        "FAVORITES_FTP_DEFAULT_MAX_LISTING_ENTRIES",
        "FAVORITES_FTP_DEFAULT_MAX_SNAPSHOT_BYTES",
        "FAVORITES_FTP_DEFAULT_PORT",
        "FAVORITES_FTP_DEFAULT_TIMEOUT",
        "FavoritesFtpReadConfiguration",
        "FavoritesFtpReadCredential",
        "FavoritesFtpSecretResolver",
        "FavoritesFtpSession",
        "FavoritesFtpSessionFactory",
        "FavoritesFtpStorageError",
        "FavoritesFtpStorageErrorReason",
        "FavoritesFtpStorageSource",
    )

    for name in expected:
        assert name in sds200.__all__
        assert hasattr(sds200, name)
