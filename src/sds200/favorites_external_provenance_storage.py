from __future__ import annotations

import errno
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from secrets import token_hex
from threading import Lock
from typing import cast

from .favorites_external import FavoritesExternalRecordState
from .favorites_external_provenance import (
    FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_BYTES,
    FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_FIELDS_PER_RECORD,
    FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_RECORDS,
    deserialize_favorites_external_provenance,
    serialize_favorites_external_provenance,
)
from .favorites_storage import FavoritesStorageSnapshot

_PROVENANCE_DIRECTORY_MODE = 0o700
_PROVENANCE_FILE_MODE = 0o600
_PROVENANCE_LOCK_FILENAME = ".favorites-external-provenance.lock"
_PROVENANCE_TEMPORARY_ATTEMPTS = 16
_PUBLICATION_PROCESS_LOCK = Lock()


class FavoritesExternalProvenanceStorageError(RuntimeError):
    """Report one safe external-provenance host-state filesystem failure."""


@dataclass(frozen=True, slots=True)
class _DurableFileState:
    content: bytes
    device: int
    inode: int
    size: int
    modified_ns: int
    changed_ns: int
    mode: int
    owner: int


def _storage_error(message: str) -> FavoritesExternalProvenanceStorageError:
    return FavoritesExternalProvenanceStorageError(message)


def _require_state_path(path: str | Path) -> Path:
    if not isinstance(path, (str, Path)):
        raise TypeError(
            "External Favorites provenance state path must be str or pathlib.Path."
        )
    if isinstance(path, str) and not path.strip():
        raise ValueError(
            "External Favorites provenance state path must not be empty."
        )
    result = Path(path)
    if not result.is_absolute():
        raise ValueError(
            "External Favorites provenance state path must be absolute."
        )
    if not result.name:
        raise ValueError(
            "External Favorites provenance state path must identify a file."
        )
    return result


def _require_positive_limit(value: int, *, label: str) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{label} must be a positive integer.")


def _effective_uid(observed_uid: int) -> int:
    getter = getattr(os, "geteuid", None)
    if getter is None:
        return observed_uid
    return cast(int, getter())


def _require_private_directory(path: Path, *, create: bool) -> None:
    if create:
        try:
            path.mkdir(mode=_PROVENANCE_DIRECTORY_MODE, parents=True, exist_ok=True)
        except OSError:
            raise _storage_error(
                "Could not create the external Favorites provenance state directory."
            ) from None

    try:
        observed = path.lstat()
    except OSError:
        raise _storage_error(
            "Could not inspect the external Favorites provenance state directory."
        ) from None

    if stat.S_ISLNK(observed.st_mode) or not stat.S_ISDIR(observed.st_mode):
        raise _storage_error("External Favorites provenance state directory is unsafe.")
    if observed.st_uid != _effective_uid(observed.st_uid):
        raise _storage_error(
            "External Favorites provenance state directory has unsafe ownership."
        )

    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise _storage_error(
            "Could not resolve the external Favorites provenance state directory."
        ) from None
    if resolved != path:
        raise _storage_error(
            "External Favorites provenance state directory must be canonical."
        )

    if create:
        try:
            os.chmod(path, _PROVENANCE_DIRECTORY_MODE)
        except OSError:
            raise _storage_error(
                "Could not secure the external Favorites provenance state directory."
            ) from None
        try:
            observed = path.lstat()
        except OSError:
            raise _storage_error(
                "Could not verify the external Favorites provenance state directory."
            ) from None

    if stat.S_IMODE(observed.st_mode) != _PROVENANCE_DIRECTORY_MODE:
        raise _storage_error(
            "External Favorites provenance state directory permissions are unsafe."
        )


def _no_follow_flag() -> int:
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None or no_follow == 0:
        raise _storage_error(
            "External Favorites provenance storage requires no-follow file support."
        )
    return cast(int, no_follow)


def _file_flags(base: int) -> int:
    return (
        base
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | _no_follow_flag()
    )


def _stat_identity(observed: os.stat_result) -> tuple[int, int]:
    return observed.st_dev, observed.st_ino


def _require_private_regular_file(
    observed: os.stat_result,
    *,
    max_bytes: int,
) -> None:
    if stat.S_ISLNK(observed.st_mode) or not stat.S_ISREG(observed.st_mode):
        raise _storage_error("External Favorites provenance state must be a regular file.")
    if observed.st_uid != _effective_uid(observed.st_uid):
        raise _storage_error(
            "External Favorites provenance state file has unsafe ownership."
        )
    if stat.S_IMODE(observed.st_mode) != _PROVENANCE_FILE_MODE:
        raise _storage_error(
            "External Favorites provenance state file permissions are unsafe."
        )
    if observed.st_size <= 0 or observed.st_size > max_bytes:
        raise _storage_error("External Favorites provenance state file size is invalid.")


def _read_durable_regular_file(path: Path, *, max_bytes: int) -> _DurableFileState:
    try:
        initial = path.lstat()
    except OSError:
        raise _storage_error(
            "Could not inspect the external Favorites provenance state file."
        ) from None

    _require_private_regular_file(initial, max_bytes=max_bytes)

    try:
        descriptor = os.open(path, _file_flags(os.O_RDONLY))
    except OSError:
        raise _storage_error(
            "Could not open the external Favorites provenance state file."
        ) from None

    try:
        opened = os.fstat(descriptor)
        _require_private_regular_file(opened, max_bytes=max_bytes)
        if _stat_identity(opened) != _stat_identity(initial):
            raise _storage_error(
                "External Favorites provenance state changed while opening."
            )

        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            try:
                chunk = os.read(descriptor, min(8192, remaining))
            except OSError:
                raise _storage_error(
                    "Could not read the external Favorites provenance state file."
                ) from None
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)

        content = b"".join(chunks)
        if not content or len(content) > max_bytes:
            raise _storage_error(
                "External Favorites provenance state file size is invalid."
            )

        final = os.fstat(descriptor)
        opened_fingerprint = (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
            opened.st_mode,
            opened.st_uid,
        )
        final_fingerprint = (
            final.st_dev,
            final.st_ino,
            final.st_size,
            final.st_mtime_ns,
            final.st_ctime_ns,
            final.st_mode,
            final.st_uid,
        )
        if final_fingerprint != opened_fingerprint or len(content) != final.st_size:
            raise _storage_error(
                "External Favorites provenance state changed while reading."
            )

        try:
            current = path.lstat()
        except OSError:
            raise _storage_error(
                "External Favorites provenance state changed while reading."
            ) from None
        current_fingerprint = (
            current.st_dev,
            current.st_ino,
            current.st_size,
            current.st_mtime_ns,
            current.st_ctime_ns,
            current.st_mode,
            current.st_uid,
        )
        if current_fingerprint != final_fingerprint:
            raise _storage_error(
                "External Favorites provenance state changed while reading."
            )

        return _DurableFileState(
            content=content,
            device=final.st_dev,
            inode=final.st_ino,
            size=final.st_size,
            modified_ns=final.st_mtime_ns,
            changed_ns=final.st_ctime_ns,
            mode=final.st_mode,
            owner=final.st_uid,
        )
    finally:
        os.close(descriptor)


def _read_optional_durable_regular_file(
    path: Path,
    *,
    max_bytes: int,
) -> _DurableFileState | None:
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        raise _storage_error(
            "Could not inspect the external Favorites provenance state file."
        ) from None
    return _read_durable_regular_file(path, max_bytes=max_bytes)


@contextmanager
def _publication_lock(parent: Path) -> Iterator[None]:
    lockf = getattr(os, "lockf", None)
    lock_exclusive = getattr(os, "F_TLOCK", None)
    lock_unlock = getattr(os, "F_ULOCK", None)
    if lockf is None or lock_exclusive is None or lock_unlock is None:
        raise _storage_error(
            "External Favorites provenance publication locking is unavailable."
        )

    if not _PUBLICATION_PROCESS_LOCK.acquire(blocking=False):
        raise _storage_error(
            "External Favorites provenance publication is already active."
        )

    descriptor: int | None = None
    locked = False
    try:
        path = parent / _PROVENANCE_LOCK_FILENAME
        try:
            descriptor = os.open(
                path,
                _file_flags(os.O_RDWR | os.O_CREAT),
                _PROVENANCE_FILE_MODE,
            )
        except OSError:
            raise _storage_error(
                "Could not open the external Favorites provenance publication lock."
            ) from None

        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise _storage_error(
                "External Favorites provenance publication lock is unsafe."
            )
        if opened.st_uid != _effective_uid(opened.st_uid):
            raise _storage_error(
                "External Favorites provenance publication lock has unsafe ownership."
            )
        try:
            os.fchmod(descriptor, _PROVENANCE_FILE_MODE)
        except OSError:
            raise _storage_error(
                "Could not secure the external Favorites provenance publication lock."
            ) from None

        try:
            current = path.lstat()
        except OSError:
            raise _storage_error(
                "Could not verify the external Favorites provenance publication lock."
            ) from None
        if (
            not stat.S_ISREG(current.st_mode)
            or _stat_identity(current) != _stat_identity(opened)
        ):
            raise _storage_error(
                "External Favorites provenance publication lock is unsafe."
            )

        try:
            lockf(descriptor, lock_exclusive, 0)
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EAGAIN}:
                raise _storage_error(
                    "External Favorites provenance publication is already active."
                ) from None
            raise _storage_error(
                "Could not acquire the external Favorites provenance publication lock."
            ) from None
        locked = True
        yield
    finally:
        if descriptor is not None:
            if locked:
                with suppress(OSError):
                    lockf(descriptor, lock_unlock, 0)
            os.close(descriptor)
        _PUBLICATION_PROCESS_LOCK.release()


def _open_temporary_file(target: Path) -> tuple[int, Path, tuple[int, int]]:
    flags = _file_flags(os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    for _ in range(_PROVENANCE_TEMPORARY_ATTEMPTS):
        path = target.parent / f".{target.name}.{token_hex(16)}.tmp"
        try:
            descriptor = os.open(path, flags, _PROVENANCE_FILE_MODE)
        except FileExistsError:
            continue
        except OSError:
            raise _storage_error(
                "Could not create the external Favorites provenance temporary file."
            ) from None

        try:
            os.fchmod(descriptor, _PROVENANCE_FILE_MODE)
            observed = os.fstat(descriptor)
        except OSError:
            os.close(descriptor)
            with suppress(OSError):
                path.unlink()
            raise _storage_error(
                "Could not secure the external Favorites provenance temporary file."
            ) from None

        if (
            not stat.S_ISREG(observed.st_mode)
            or observed.st_uid != _effective_uid(observed.st_uid)
            or stat.S_IMODE(observed.st_mode) != _PROVENANCE_FILE_MODE
        ):
            os.close(descriptor)
            with suppress(OSError):
                path.unlink()
            raise _storage_error(
                "External Favorites provenance temporary file is unsafe."
            )
        return descriptor, path, _stat_identity(observed)

    raise _storage_error(
        "Could not allocate an external Favorites provenance temporary file."
    )


def _write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        try:
            written = os.write(descriptor, content[offset:])
        except OSError:
            raise _storage_error(
                "Could not write the external Favorites provenance temporary file."
            ) from None
        if written <= 0:
            raise _storage_error(
                "External Favorites provenance temporary write did not make progress."
            )
        offset += written


def _fsync_directory(path: Path) -> None:
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if directory_flag is None or directory_flag == 0:
        raise _storage_error(
            "External Favorites provenance storage requires directory synchronization."
        )
    try:
        descriptor = os.open(
            path,
            _file_flags(os.O_RDONLY | directory_flag),
        )
    except OSError:
        raise _storage_error(
            "Could not open the external Favorites provenance state directory for synchronization."
        ) from None
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise _storage_error(
                "External Favorites provenance synchronization target is unsafe."
            )
        try:
            os.fsync(descriptor)
        except OSError:
            raise _storage_error(
                "Could not synchronize the external Favorites provenance state directory."
            ) from None
    finally:
        os.close(descriptor)


def _unlink_matching_temporary(path: Path, identity: tuple[int, int]) -> None:
    try:
        observed = path.lstat()
    except OSError:
        return
    if stat.S_ISREG(observed.st_mode) and _stat_identity(observed) == identity:
        with suppress(OSError):
            path.unlink()


def _require_target_unchanged(
    target: Path,
    expected: _DurableFileState | None,
    *,
    max_bytes: int,
) -> None:
    current = _read_optional_durable_regular_file(target, max_bytes=max_bytes)
    if current != expected:
        raise _storage_error(
            "External Favorites provenance state changed during publication."
        )


_EXPECTED_CURRENT_CONTENT_UNSET = object()


def _publish_content(
    target: Path,
    content: bytes,
    *,
    max_bytes: int,
    expected_current_content: bytes | None | object = _EXPECTED_CURRENT_CONTENT_UNSET,
) -> None:
    initial = _read_optional_durable_regular_file(target, max_bytes=max_bytes)
    if expected_current_content is not _EXPECTED_CURRENT_CONTENT_UNSET:
        observed_content = None if initial is None else initial.content
        if observed_content != expected_current_content:
            raise _storage_error(
                "External Favorites provenance state does not match "
                "the expected current state."
            )
    descriptor, temporary, temporary_identity = _open_temporary_file(target)
    published = False
    try:
        try:
            _write_all(descriptor, content)
            os.fsync(descriptor)
        except OSError:
            raise _storage_error(
                "Could not synchronize the external Favorites provenance temporary file."
            ) from None
        finally:
            os.close(descriptor)

        temporary_state = _read_durable_regular_file(
            temporary,
            max_bytes=max_bytes,
        )
        if (
            temporary_state.content != content
            or (temporary_state.device, temporary_state.inode) != temporary_identity
        ):
            raise _storage_error(
                "External Favorites provenance temporary file failed exact readback."
            )

        _require_target_unchanged(target, initial, max_bytes=max_bytes)
        try:
            os.replace(temporary, target)
        except OSError:
            raise _storage_error(
                "Could not atomically publish external Favorites provenance state."
            ) from None
        published = True

        _fsync_directory(target.parent)
        final = _read_durable_regular_file(target, max_bytes=max_bytes)
        if (
            final.content != content
            or (final.device, final.inode) != temporary_identity
        ):
            raise _storage_error(
                "Published external Favorites provenance state failed exact readback."
            )
    finally:
        if not published:
            _unlink_matching_temporary(temporary, temporary_identity)


def save_favorites_external_provenance(
    records: tuple[FavoritesExternalRecordState, ...],
    path: str | Path,
    *,
    max_bytes: int = FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_BYTES,
    max_records: int = FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_RECORDS,
    max_fields_per_record: int = FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_FIELDS_PER_RECORD,
) -> Path:
    """Durably publish canonical external Favorites provenance to one host file."""

    target = _require_state_path(path)
    content = serialize_favorites_external_provenance(
        records,
        max_bytes=max_bytes,
        max_records=max_records,
        max_fields_per_record=max_fields_per_record,
    )
    _require_private_directory(target.parent, create=True)
    with _publication_lock(target.parent):
        _publish_content(target, content, max_bytes=max_bytes)
    return target


def save_favorites_external_provenance_if_current(
    records: tuple[FavoritesExternalRecordState, ...],
    path: str | Path,
    *,
    expected_current_records: tuple[FavoritesExternalRecordState, ...] | None,
    max_bytes: int = FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_BYTES,
    max_records: int = FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_RECORDS,
    max_fields_per_record: int = FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_FIELDS_PER_RECORD,
) -> Path:
    """Durably publish provenance only when exact current canonical state matches."""

    target = _require_state_path(path)
    content = serialize_favorites_external_provenance(
        records,
        max_bytes=max_bytes,
        max_records=max_records,
        max_fields_per_record=max_fields_per_record,
    )
    expected_content = (
        None
        if expected_current_records is None
        else serialize_favorites_external_provenance(
            expected_current_records,
            max_bytes=max_bytes,
            max_records=max_records,
            max_fields_per_record=max_fields_per_record,
        )
    )
    _require_private_directory(target.parent, create=True)
    with _publication_lock(target.parent):
        _publish_content(
            target,
            content,
            max_bytes=max_bytes,
            expected_current_content=expected_content,
        )
    return target


def load_favorites_external_provenance(
    path: str | Path,
    snapshot: FavoritesStorageSnapshot,
    *,
    max_bytes: int = FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_BYTES,
    max_records: int = FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_RECORDS,
    max_fields_per_record: int = FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_FIELDS_PER_RECORD,
) -> tuple[FavoritesExternalRecordState, ...] | None:
    """Load one explicit provenance state file and rebind it to a fresh snapshot."""

    target = _require_state_path(path)
    if not isinstance(snapshot, FavoritesStorageSnapshot):
        raise TypeError(
            "External Favorites provenance loading requires FavoritesStorageSnapshot."
        )
    _require_positive_limit(
        max_bytes,
        label="External Favorites provenance maximum size",
    )
    _require_positive_limit(
        max_records,
        label="External Favorites provenance maximum record count",
    )
    _require_positive_limit(
        max_fields_per_record,
        label="External Favorites provenance maximum field count",
    )

    try:
        target.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        raise _storage_error(
            "Could not inspect the external Favorites provenance state file."
        ) from None

    _require_private_directory(target.parent, create=False)
    state = _read_durable_regular_file(target, max_bytes=max_bytes)
    return deserialize_favorites_external_provenance(
        state.content,
        snapshot,
        max_bytes=max_bytes,
        max_records=max_records,
        max_fields_per_record=max_fields_per_record,
    )


__all__ = [
    "FavoritesExternalProvenanceStorageError",
    "load_favorites_external_provenance",
    "save_favorites_external_provenance",
    "save_favorites_external_provenance_if_current",
]
