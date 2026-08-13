"""Read-only Favorites storage over bounded trusted-network FTP."""

from __future__ import annotations

import ftplib
import math
import os
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, TypeAlias

from .favorites_storage import (
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
)

FAVORITES_FTP_DEFAULT_PORT = 21
FAVORITES_FTP_DEFAULT_TIMEOUT = 10.0
FAVORITES_FTP_DEFAULT_MAX_LISTING_ENTRIES = 4096
FAVORITES_FTP_DEFAULT_MAX_CATALOG_BYTES = 4 * 1024 * 1024
FAVORITES_FTP_DEFAULT_MAX_DOCUMENT_BYTES = 64 * 1024 * 1024
FAVORITES_FTP_DEFAULT_MAX_SNAPSHOT_BYTES = 256 * 1024 * 1024
_FAVORITES_FTP_TRANSFER_BLOCK_SIZE = 64 * 1024


class FavoritesFtpStorageErrorReason(StrEnum):
    """Stable redacted read-only FTP failure classes."""

    CREDENTIAL_UNAVAILABLE = "credential_unavailable"
    CONNECTION_FAILED = "connection_failed"
    AUTHENTICATION_FAILED = "authentication_failed"
    DIRECTORY_UNAVAILABLE = "directory_unavailable"
    LISTING_FAILED = "listing_failed"
    UNSAFE_LISTING = "unsafe_listing"
    LIMIT_EXCEEDED = "limit_exceeded"
    RETRIEVAL_FAILED = "retrieval_failed"
    SNAPSHOT_CHANGED = "snapshot_changed"
    CLEANUP_FAILED = "cleanup_failed"


class FavoritesFtpStorageError(RuntimeError):
    """Report one redacted read-only Favorites FTP failure."""

    def __init__(
        self,
        reason: FavoritesFtpStorageErrorReason,
        message: str,
    ) -> None:
        if not isinstance(reason, FavoritesFtpStorageErrorReason):
            raise TypeError(
                "Favorites FTP storage error reason must be "
                "FavoritesFtpStorageErrorReason."
            )
        if not isinstance(message, str) or not message:
            raise ValueError(
                "Favorites FTP storage error message must not be empty."
            )

        self.reason = reason
        self.message = message
        super().__init__(message)


def _validate_text(
    value: str,
    *,
    label: str,
) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string.")
    if not value or value.strip() != value:
        raise ValueError(f"{label} must not be empty or padded.")
    if any(
        ord(character) < 32 or ord(character) == 127
        for character in value
    ):
        raise ValueError(f"{label} must not contain control characters.")
    return value


def _validate_host(value: str) -> str:
    host = _validate_text(
        value,
        label="Favorites FTP host",
    )
    if (
        "://" in host
        or "/" in host
        or "\\" in host
        or "@" in host
        or any(character.isspace() for character in host)
    ):
        raise ValueError(
            "Favorites FTP host must be one host name or address."
        )
    return host


def _validate_remote_directory(value: str) -> str:
    directory = _validate_text(
        value,
        label="Favorites FTP directory",
    )
    if "\\" in directory:
        raise ValueError(
            "Favorites FTP directory must use POSIX path components."
        )

    absolute = directory.startswith("/")
    body = directory[1:] if absolute else directory

    if not body or body.endswith("/") or "//" in body:
        raise ValueError(
            "Favorites FTP directory must use non-traversing POSIX "
            "path components."
        )

    components = body.split("/")
    if any(component in {"", ".", ".."} for component in components):
        raise ValueError(
            "Favorites FTP directory must use non-traversing POSIX "
            "path components."
        )

    return directory


def _validate_positive_integer(
    value: int,
    *,
    label: str,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer.")
    if value <= 0:
        raise ValueError(f"{label} must be greater than zero.")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label} must be at most {maximum}.")
    return value


def _validate_timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("Favorites FTP timeout must be a number.")

    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0.0:
        raise ValueError(
            "Favorites FTP timeout must be finite and greater than zero."
        )
    return timeout


@dataclass(frozen=True, slots=True)
class FavoritesFtpReadCredential:
    """Read-only FTP username plus a password secret reference."""

    username: str
    password_environment_variable: str

    def __post_init__(self) -> None:
        _validate_text(
            self.username,
            label="Favorites FTP read username",
        )
        variable = _validate_text(
            self.password_environment_variable,
            label="Favorites FTP read password environment-variable name",
        )
        if "=" in variable or any(
            character.isspace() for character in variable
        ):
            raise ValueError(
                "Favorites FTP read password environment-variable name is invalid."
            )


@dataclass(frozen=True, slots=True)
class FavoritesFtpReadConfiguration:
    """Explicit bounded configuration for read-only Favorites FTP."""

    host: str
    favorites_directory: str
    credential: FavoritesFtpReadCredential
    port: int = FAVORITES_FTP_DEFAULT_PORT
    timeout: float = FAVORITES_FTP_DEFAULT_TIMEOUT
    max_listing_entries: int = FAVORITES_FTP_DEFAULT_MAX_LISTING_ENTRIES
    max_catalog_bytes: int = FAVORITES_FTP_DEFAULT_MAX_CATALOG_BYTES
    max_document_bytes: int = FAVORITES_FTP_DEFAULT_MAX_DOCUMENT_BYTES
    max_snapshot_bytes: int = FAVORITES_FTP_DEFAULT_MAX_SNAPSHOT_BYTES

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "host",
            _validate_host(self.host),
        )
        object.__setattr__(
            self,
            "favorites_directory",
            _validate_remote_directory(self.favorites_directory),
        )

        if not isinstance(self.credential, FavoritesFtpReadCredential):
            raise TypeError(
                "Favorites FTP read configuration requires "
                "FavoritesFtpReadCredential."
            )

        _validate_positive_integer(
            self.port,
            label="Favorites FTP port",
            maximum=65535,
        )
        object.__setattr__(
            self,
            "timeout",
            _validate_timeout(self.timeout),
        )
        _validate_positive_integer(
            self.max_listing_entries,
            label="Favorites FTP maximum listing entries",
        )
        _validate_positive_integer(
            self.max_catalog_bytes,
            label="Favorites FTP maximum catalog bytes",
        )
        _validate_positive_integer(
            self.max_document_bytes,
            label="Favorites FTP maximum document bytes",
        )
        _validate_positive_integer(
            self.max_snapshot_bytes,
            label="Favorites FTP maximum snapshot bytes",
        )


class FavoritesFtpSession(Protocol):
    """Narrow read-only session boundary used by the storage source."""

    def list_names(self) -> tuple[str, ...]:
        """Return immediate names from the current FTP directory."""
        ...

    def retrieve_file(
        self,
        filename: str,
        *,
        max_bytes: int,
    ) -> bytes:
        """Return one exact file while enforcing its byte limit."""
        ...

    def close(self) -> None:
        """Close the session deterministically."""
        ...


FavoritesFtpSessionFactory: TypeAlias = Callable[
    [FavoritesFtpReadConfiguration, str],
    FavoritesFtpSession,
]
FavoritesFtpSecretResolver: TypeAlias = Callable[[str], str]


class _FavoritesFtpTransferLimitExceeded(RuntimeError):
    pass


class _FtplibFavoritesFtpSession:
    def __init__(self, ftp: ftplib.FTP) -> None:
        self._ftp = ftp
        self._closed = False

    def list_names(self) -> tuple[str, ...]:
        try:
            return tuple(self._ftp.nlst())
        except (ftplib.Error, OSError, EOFError, UnicodeError):
            raise FavoritesFtpStorageError(
                FavoritesFtpStorageErrorReason.LISTING_FAILED,
                "Could not list the Favorites FTP directory.",
            ) from None

    def retrieve_file(
        self,
        filename: str,
        *,
        max_bytes: int,
    ) -> bytes:
        content = bytearray()

        def consume(chunk: bytes) -> None:
            if len(content) + len(chunk) > max_bytes:
                raise _FavoritesFtpTransferLimitExceeded
            content.extend(chunk)

        try:
            self._ftp.retrbinary(
                f"RETR {filename}",
                consume,
                blocksize=_FAVORITES_FTP_TRANSFER_BLOCK_SIZE,
            )
        except _FavoritesFtpTransferLimitExceeded:
            raise FavoritesFtpStorageError(
                FavoritesFtpStorageErrorReason.LIMIT_EXCEEDED,
                "Favorites FTP file exceeds its configured byte limit.",
            ) from None
        except (ftplib.Error, OSError, EOFError, UnicodeError):
            raise FavoritesFtpStorageError(
                FavoritesFtpStorageErrorReason.RETRIEVAL_FAILED,
                "Could not retrieve one Favorites FTP file.",
            ) from None

        return bytes(content)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        try:
            self._ftp.quit()
        except (ftplib.Error, OSError, EOFError, UnicodeError):
            with suppress(OSError):
                self._ftp.close()
            raise FavoritesFtpStorageError(
                FavoritesFtpStorageErrorReason.CLEANUP_FAILED,
                "Could not close the Favorites FTP session cleanly.",
            ) from None


def _close_unowned_ftp(ftp: ftplib.FTP) -> None:
    with suppress(OSError):
        ftp.close()


def _ftplib_session_factory(
    configuration: FavoritesFtpReadConfiguration,
    password: str,
) -> FavoritesFtpSession:
    ftp = ftplib.FTP()

    try:
        ftp.connect(
            host=configuration.host,
            port=configuration.port,
            timeout=configuration.timeout,
        )
    except (ftplib.Error, OSError, EOFError, UnicodeError):
        _close_unowned_ftp(ftp)
        raise FavoritesFtpStorageError(
            FavoritesFtpStorageErrorReason.CONNECTION_FAILED,
            "Could not connect to the Favorites FTP endpoint.",
        ) from None

    try:
        ftp.login(
            user=configuration.credential.username,
            passwd=password,
        )
    except (ftplib.Error, OSError, EOFError, UnicodeError):
        _close_unowned_ftp(ftp)
        raise FavoritesFtpStorageError(
            FavoritesFtpStorageErrorReason.AUTHENTICATION_FAILED,
            "Favorites FTP read-only authentication failed.",
        ) from None

    try:
        ftp.cwd(configuration.favorites_directory)
    except (ftplib.Error, OSError, EOFError, UnicodeError):
        _close_unowned_ftp(ftp)
        raise FavoritesFtpStorageError(
            FavoritesFtpStorageErrorReason.DIRECTORY_UNAVAILABLE,
            "Favorites FTP directory is unavailable.",
        ) from None

    return _FtplibFavoritesFtpSession(ftp)


def _environment_secret_resolver(variable: str) -> str:
    value = os.environ.get(variable)
    if value is None:
        raise KeyError(variable)
    return value


def _validate_listing_name(name: str) -> str:
    if not isinstance(name, str):
        raise FavoritesFtpStorageError(
            FavoritesFtpStorageErrorReason.UNSAFE_LISTING,
            "Favorites FTP listing contains a non-text name.",
        )

    if (
        not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or any(
            ord(character) < 32 or ord(character) == 127
            for character in name
        )
    ):
        raise FavoritesFtpStorageError(
            FavoritesFtpStorageErrorReason.UNSAFE_LISTING,
            "Favorites FTP listing contains an unsafe remote name.",
        )

    return name


def _safe_listing(
    session: FavoritesFtpSession,
    configuration: FavoritesFtpReadConfiguration,
) -> tuple[str, ...]:
    try:
        raw_names = session.list_names()
    except FavoritesFtpStorageError:
        raise
    except Exception:
        raise FavoritesFtpStorageError(
            FavoritesFtpStorageErrorReason.LISTING_FAILED,
            "Could not list the Favorites FTP directory.",
        ) from None

    if not isinstance(raw_names, tuple):
        raise FavoritesFtpStorageError(
            FavoritesFtpStorageErrorReason.UNSAFE_LISTING,
            "Favorites FTP listing must be an immutable tuple.",
        )

    if len(raw_names) > configuration.max_listing_entries:
        raise FavoritesFtpStorageError(
            FavoritesFtpStorageErrorReason.LIMIT_EXCEEDED,
            "Favorites FTP listing exceeds its configured entry limit.",
        )

    names = tuple(_validate_listing_name(name) for name in raw_names)

    if len(set(names)) != len(names):
        raise FavoritesFtpStorageError(
            FavoritesFtpStorageErrorReason.UNSAFE_LISTING,
            "Favorites FTP listing contains duplicate remote names.",
        )

    if "f_list.cfg" not in names:
        raise FavoritesFtpStorageError(
            FavoritesFtpStorageErrorReason.UNSAFE_LISTING,
            "Favorites FTP listing does not contain f_list.cfg.",
        )

    return tuple(sorted(names))


def _retrieve_file(
    session: FavoritesFtpSession,
    filename: str,
    *,
    max_bytes: int,
) -> bytes:
    try:
        content = session.retrieve_file(
            filename,
            max_bytes=max_bytes,
        )
    except FavoritesFtpStorageError:
        raise
    except Exception:
        raise FavoritesFtpStorageError(
            FavoritesFtpStorageErrorReason.RETRIEVAL_FAILED,
            "Could not retrieve one Favorites FTP file.",
        ) from None

    if not isinstance(content, bytes):
        raise FavoritesFtpStorageError(
            FavoritesFtpStorageErrorReason.RETRIEVAL_FAILED,
            "Favorites FTP retrieval returned non-byte content.",
        )

    if len(content) > max_bytes:
        raise FavoritesFtpStorageError(
            FavoritesFtpStorageErrorReason.LIMIT_EXCEEDED,
            "Favorites FTP file exceeds its configured byte limit.",
        )

    return content


@dataclass(frozen=True, slots=True)
class _FavoritesFtpSnapshotPass:
    listing: tuple[str, ...]
    snapshot: FavoritesStorageSnapshot


def _read_snapshot_pass(
    session: FavoritesFtpSession,
    configuration: FavoritesFtpReadConfiguration,
) -> _FavoritesFtpSnapshotPass:
    listing = _safe_listing(
        session,
        configuration,
    )

    catalog_bytes = _retrieve_file(
        session,
        "f_list.cfg",
        max_bytes=configuration.max_catalog_bytes,
    )

    total_bytes = len(catalog_bytes)
    if total_bytes > configuration.max_snapshot_bytes:
        raise FavoritesFtpStorageError(
            FavoritesFtpStorageErrorReason.LIMIT_EXCEEDED,
            "Favorites FTP snapshot exceeds its configured byte limit.",
        )

    documents: list[FavoritesStorageDocument] = []

    for filename in listing:
        if not filename.endswith(".hpd"):
            continue

        content = _retrieve_file(
            session,
            filename,
            max_bytes=configuration.max_document_bytes,
        )

        total_bytes += len(content)
        if total_bytes > configuration.max_snapshot_bytes:
            raise FavoritesFtpStorageError(
                FavoritesFtpStorageErrorReason.LIMIT_EXCEEDED,
                "Favorites FTP snapshot exceeds its configured byte limit.",
            )

        documents.append(
            FavoritesStorageDocument(
                filename=filename,
                content=content,
            )
        )

    return _FavoritesFtpSnapshotPass(
        listing=listing,
        snapshot=FavoritesStorageSnapshot(
            catalog_bytes=catalog_bytes,
            documents=tuple(documents),
        ),
    )


@dataclass(frozen=True, slots=True)
class FavoritesFtpStorageSource:
    """Read one exact stable Favorites snapshot through read-only FTP."""

    configuration: FavoritesFtpReadConfiguration
    session_factory: FavoritesFtpSessionFactory = _ftplib_session_factory
    secret_resolver: FavoritesFtpSecretResolver = _environment_secret_resolver

    def __post_init__(self) -> None:
        if not isinstance(
            self.configuration,
            FavoritesFtpReadConfiguration,
        ):
            raise TypeError(
                "Favorites FTP storage source requires "
                "FavoritesFtpReadConfiguration."
            )
        if not callable(self.session_factory):
            raise TypeError(
                "Favorites FTP session factory must be callable."
            )
        if not callable(self.secret_resolver):
            raise TypeError(
                "Favorites FTP secret resolver must be callable."
            )

    def read_snapshot(self) -> FavoritesStorageSnapshot:
        """Return exact two-pass stable read-only FTP evidence."""

        variable = (
            self.configuration
            .credential
            .password_environment_variable
        )

        try:
            password = self.secret_resolver(variable)
        except Exception:
            raise FavoritesFtpStorageError(
                FavoritesFtpStorageErrorReason.CREDENTIAL_UNAVAILABLE,
                "Favorites FTP read password secret is unavailable.",
            ) from None

        if not isinstance(password, str) or not password:
            raise FavoritesFtpStorageError(
                FavoritesFtpStorageErrorReason.CREDENTIAL_UNAVAILABLE,
                "Favorites FTP read password secret is unavailable.",
            )

        try:
            session = self.session_factory(
                self.configuration,
                password,
            )
        except FavoritesFtpStorageError:
            raise
        except Exception:
            raise FavoritesFtpStorageError(
                FavoritesFtpStorageErrorReason.CONNECTION_FAILED,
                "Could not open the Favorites FTP read-only session.",
            ) from None

        body_error: BaseException | None = None

        try:
            first = _read_snapshot_pass(
                session,
                self.configuration,
            )
            second = _read_snapshot_pass(
                session,
                self.configuration,
            )

            if first != second:
                raise FavoritesFtpStorageError(
                    FavoritesFtpStorageErrorReason.SNAPSHOT_CHANGED,
                    "Favorites FTP snapshot changed between verification passes.",
                )

            return second.snapshot
        except BaseException as error:
            body_error = error
            raise
        finally:
            try:
                session.close()
            except FavoritesFtpStorageError:
                if body_error is None:
                    raise
            except Exception:
                if body_error is None:
                    raise FavoritesFtpStorageError(
                        FavoritesFtpStorageErrorReason.CLEANUP_FAILED,
                        "Could not close the Favorites FTP session cleanly.",
                    ) from None


__all__ = [
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
]
