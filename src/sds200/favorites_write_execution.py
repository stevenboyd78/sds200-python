"""Pre-mutation safety contract for verified copied-tree Favorites writes."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Protocol

from .favorites_schema import validate_favorites_workspace
from .favorites_storage import (
    FavoritesStorageSnapshot,
    project_favorites_storage_snapshot,
)
from .favorites_storage_evidence import (
    FavoritesTreeEvidence as _CopiedTreeEvidence,
)
from .favorites_storage_evidence import (
    FavoritesTreeEvidenceError as _CopiedTreeUnsafeError,
)
from .favorites_storage_evidence import (
    favorites_storage_snapshot_sha256 as _favorites_storage_snapshot_sha256,
)
from .favorites_storage_evidence import (
    favorites_tree_evidence as _copied_tree_evidence,
)
from .favorites_storage_local import (
    FavoritesCopiedTreeStorageError,
    FavoritesCopiedTreeStorageSource,
)
from .favorites_write_plan import FavoritesWritePlan


class FavoritesCopiedTreeWritePreflightReason(StrEnum):
    """Classify one copied-tree write refusal before mutation begins."""

    BLOCKED_PLAN = "blocked_plan"
    TARGET_UNAVAILABLE = "target_unavailable"
    TARGET_STALE = "target_stale"
    UNSAFE_TREE = "unsafe_tree"


class _Digest(Protocol):
    def update(
        self,
        data: bytes,
        /,
    ) -> None: ...


def _hash_field(
    digest: _Digest,
    value: bytes,
) -> None:
    digest.update(
        len(value).to_bytes(
            8,
            "big",
        )
    )
    digest.update(value)



class _CopiedTreeWritePreparationError(RuntimeError):
    def __init__(
        self,
        path: Path,
        message: str,
    ) -> None:
        self.path = path
        self.message = message
        super().__init__(
            f"Favorites copied-tree write preparation failed at {path}: {message}"
        )


@contextmanager
def _copied_tree_operation_lock(
    preflight: FavoritesCopiedTreeWritePreflight,
) -> Iterator[None]:
    if not isinstance(
        preflight,
        FavoritesCopiedTreeWritePreflight,
    ):
        raise TypeError(
            "Favorites copied-tree operation lock requires "
            "FavoritesCopiedTreeWritePreflight."
        )

    lock_path = preflight.lock_path

    try:
        lock_path.mkdir(
            mode=0o700,
        )
    except FileExistsError as error:
        raise _CopiedTreeWritePreparationError(
            lock_path,
            "Another Favorites copied-tree write operation may already be active.",
        ) from error
    except OSError as error:
        raise _CopiedTreeWritePreparationError(
            lock_path,
            f"Could not establish the Favorites copied-tree operation lock: {error}",
        ) from error

    try:
        locked = lock_path.lstat()
    except OSError as error:
        raise _CopiedTreeWritePreparationError(
            lock_path,
            f"Could not inspect the Favorites copied-tree operation lock: {error}",
        ) from error

    if not stat.S_ISDIR(
        locked.st_mode
    ):
        raise _CopiedTreeWritePreparationError(
            lock_path,
            "Favorites copied-tree operation lock is not a directory.",
        )

    body_error: BaseException | None = None

    try:
        yield
    except BaseException as error:
        body_error = error
        raise
    finally:
        cleanup_error: _CopiedTreeWritePreparationError | None = None

        try:
            current = lock_path.lstat()
        except FileNotFoundError:
            cleanup_error = _CopiedTreeWritePreparationError(
                lock_path,
                "Favorites copied-tree operation lock disappeared before release.",
            )
        except OSError as error:
            cleanup_error = _CopiedTreeWritePreparationError(
                lock_path,
                f"Could not inspect the operation lock before release: {error}",
            )
        else:
            if (
                current.st_dev,
                current.st_ino,
            ) != (
                locked.st_dev,
                locked.st_ino,
            ):
                cleanup_error = _CopiedTreeWritePreparationError(
                    lock_path,
                    "Favorites copied-tree operation lock changed before release.",
                )
            elif not stat.S_ISDIR(
                current.st_mode
            ):
                cleanup_error = _CopiedTreeWritePreparationError(
                    lock_path,
                    "Favorites copied-tree operation lock changed file type.",
                )
            else:
                try:
                    lock_path.rmdir()
                except OSError as error:
                    cleanup_error = _CopiedTreeWritePreparationError(
                        lock_path,
                        f"Could not release the operation lock: {error}",
                    )

        if (
            cleanup_error is not None
            and body_error is None
        ):
            raise cleanup_error


def _require_current_preflight_target(
    preflight: FavoritesCopiedTreeWritePreflight,
) -> _CopiedTreeEvidence:
    if not isinstance(
        preflight,
        FavoritesCopiedTreeWritePreflight,
    ):
        raise TypeError(
            "Favorites copied-tree target revalidation requires "
            "FavoritesCopiedTreeWritePreflight."
        )

    root = preflight.resolved_directory

    try:
        observed_root = root.lstat()
    except OSError as error:
        raise _CopiedTreeWritePreparationError(
            root,
            f"Could not re-inspect the copied-tree target: {error}",
        ) from error

    if (
        observed_root.st_dev,
        observed_root.st_ino,
    ) != (
        preflight.target_device,
        preflight.target_inode,
    ):
        raise _CopiedTreeWritePreparationError(
            root,
            "Copied-tree target identity changed after preflight.",
        )

    try:
        evidence = _copied_tree_evidence(
            root
        )
    except _CopiedTreeUnsafeError as error:
        raise _CopiedTreeWritePreparationError(
            error.path,
            error.message,
        ) from error

    if evidence.sha256 != preflight.tree_sha256:
        raise _CopiedTreeWritePreparationError(
            root,
            "Copied-tree content or structure changed after preflight.",
        )

    try:
        snapshot = FavoritesCopiedTreeStorageSource(
            root
        ).read_snapshot()
    except FavoritesCopiedTreeStorageError as error:
        raise _CopiedTreeWritePreparationError(
            error.path,
            error.message,
        ) from error

    if (
        snapshot != preflight.observed_snapshot
        or not preflight.plan.matches_baseline_snapshot(
            snapshot
        )
    ):
        raise _CopiedTreeWritePreparationError(
            root,
            "Copied-tree managed snapshot changed after preflight.",
        )

    return evidence


def _create_verified_backup(
    preflight: FavoritesCopiedTreeWritePreflight,
    backup_directory: Path,
) -> _CopiedTreeEvidence:
    if not isinstance(
        preflight,
        FavoritesCopiedTreeWritePreflight,
    ):
        raise TypeError(
            "Favorites copied-tree backup requires "
            "FavoritesCopiedTreeWritePreflight."
        )
    if not isinstance(
        backup_directory,
        Path,
    ):
        raise TypeError(
            "Favorites copied-tree backup directory must be pathlib.Path."
        )
    if not backup_directory.is_absolute():
        raise ValueError(
            "Favorites copied-tree backup directory must be absolute."
        )

    expected_parent = (
        preflight.resolved_directory.parent
    )

    try:
        backup_parent = (
            backup_directory.parent.resolve(
                strict=True
            )
        )
    except (OSError, RuntimeError) as error:
        raise _CopiedTreeWritePreparationError(
            backup_directory.parent,
            f"Could not resolve the backup parent directory: {error}",
        ) from error

    if backup_parent != expected_parent:
        raise _CopiedTreeWritePreparationError(
            backup_directory,
            "Verified copied-tree backup must be a sibling of the active target.",
        )

    _require_current_preflight_target(
        preflight
    )

    if os.path.lexists(
        backup_directory
    ):
        raise _CopiedTreeWritePreparationError(
            backup_directory,
            "Verified copied-tree backup destination already exists.",
        )

    try:
        shutil.copytree(
            preflight.resolved_directory,
            backup_directory,
            copy_function=shutil.copy2,
            symlinks=True,
        )
    except OSError as error:
        raise _CopiedTreeWritePreparationError(
            backup_directory,
            f"Could not create the complete copied-tree backup: {error}",
        ) from error

    _require_current_preflight_target(
        preflight
    )

    try:
        backup_evidence = _copied_tree_evidence(
            backup_directory
        )
    except _CopiedTreeUnsafeError as error:
        raise _CopiedTreeWritePreparationError(
            error.path,
            (
                "Copied-tree backup could not be verified safely: "
                f"{error.message}"
            ),
        ) from error

    if backup_evidence.sha256 != preflight.tree_sha256:
        raise _CopiedTreeWritePreparationError(
            backup_directory,
            "Copied-tree backup does not exactly match the preflight tree evidence.",
        )

    try:
        backup_snapshot = FavoritesCopiedTreeStorageSource(
            backup_directory
        ).read_snapshot()
    except FavoritesCopiedTreeStorageError as error:
        raise _CopiedTreeWritePreparationError(
            error.path,
            (
                "Copied-tree backup managed snapshot could not be verified: "
                f"{error.message}"
            ),
        ) from error

    if backup_snapshot != preflight.observed_snapshot:
        raise _CopiedTreeWritePreparationError(
            backup_directory,
            "Copied-tree backup managed snapshot differs from the preflight target.",
        )

    return backup_evidence


@dataclass(frozen=True, slots=True)
class _CopiedTreePreparedStage:
    directory: Path
    snapshot: FavoritesStorageSnapshot
    device: int
    inode: int
    tree_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(
            self.directory,
            Path,
        ):
            raise TypeError(
                "Favorites copied-tree staging directory must be pathlib.Path."
            )
        if not self.directory.is_absolute():
            raise ValueError(
                "Favorites copied-tree staging directory must be absolute."
            )
        if not isinstance(
            self.snapshot,
            FavoritesStorageSnapshot,
        ):
            raise TypeError(
                "Favorites copied-tree staged snapshot must be "
                "FavoritesStorageSnapshot."
            )
        if type(self.device) is not int:
            raise TypeError(
                "Favorites copied-tree staging device must be an integer."
            )
        if self.device < 0:
            raise ValueError(
                "Favorites copied-tree staging device must be non-negative."
            )
        if type(self.inode) is not int:
            raise TypeError(
                "Favorites copied-tree staging inode must be an integer."
            )
        if self.inode < 0:
            raise ValueError(
                "Favorites copied-tree staging inode must be non-negative."
            )
        if not isinstance(
            self.tree_sha256,
            str,
        ):
            raise TypeError(
                "Favorites copied-tree staging SHA-256 must be a string."
            )
        if (
            len(self.tree_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.tree_sha256
            )
        ):
            raise ValueError(
                "Favorites copied-tree staging SHA-256 must be 64 lowercase "
                "hexadecimal characters."
            )


def _write_staged_regular_file(
    path: Path,
    content: bytes,
) -> None:
    if not isinstance(
        path,
        Path,
    ):
        raise TypeError(
            "Favorites staged write path must be pathlib.Path."
        )
    if not isinstance(
        content,
        bytes,
    ):
        raise TypeError(
            "Favorites staged write content must be bytes."
        )

    try:
        observed = path.lstat()
    except FileNotFoundError:
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        mode = 0o600
    except OSError as error:
        raise _CopiedTreeWritePreparationError(
            path,
            f"Could not inspect staged Favorites file: {error}",
        ) from error
    else:
        if stat.S_ISLNK(
            observed.st_mode
        ):
            raise _CopiedTreeWritePreparationError(
                path,
                "Staged Favorites file must not be a symbolic link.",
            )
        if not stat.S_ISREG(
            observed.st_mode
        ):
            raise _CopiedTreeWritePreparationError(
                path,
                "Staged Favorites file must be a regular file.",
            )
        flags = (
            os.O_WRONLY
            | os.O_TRUNC
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        mode = observed.st_mode & 0o7777

    try:
        descriptor = os.open(
            path,
            flags,
            mode,
        )
    except OSError as error:
        raise _CopiedTreeWritePreparationError(
            path,
            f"Could not open staged Favorites file for writing: {error}",
        ) from error

    try:
        with os.fdopen(
            descriptor,
            "wb",
            closefd=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(
                handle.fileno()
            )
    except OSError as error:
        raise _CopiedTreeWritePreparationError(
            path,
            f"Could not write staged Favorites file: {error}",
        ) from error
    finally:
        os.close(descriptor)


def _create_verified_staging(
    preflight: FavoritesCopiedTreeWritePreflight,
    staging_directory: Path,
) -> _CopiedTreePreparedStage:
    if not isinstance(
        preflight,
        FavoritesCopiedTreeWritePreflight,
    ):
        raise TypeError(
            "Favorites copied-tree staging requires "
            "FavoritesCopiedTreeWritePreflight."
        )
    if not isinstance(
        staging_directory,
        Path,
    ):
        raise TypeError(
            "Favorites copied-tree staging directory must be pathlib.Path."
        )
    if not staging_directory.is_absolute():
        raise ValueError(
            "Favorites copied-tree staging directory must be absolute."
        )

    expected_parent = (
        preflight.resolved_directory.parent
    )

    try:
        staging_parent = (
            staging_directory.parent.resolve(
                strict=True
            )
        )
    except (OSError, RuntimeError) as error:
        raise _CopiedTreeWritePreparationError(
            staging_directory.parent,
            f"Could not resolve the staging parent directory: {error}",
        ) from error

    if staging_parent != expected_parent:
        raise _CopiedTreeWritePreparationError(
            staging_directory,
            "Verified copied-tree staging must be a sibling of the active target.",
        )

    if os.path.lexists(
        staging_directory
    ):
        raise _CopiedTreeWritePreparationError(
            staging_directory,
            "Verified copied-tree staging destination already exists.",
        )

    intended = (
        preflight.plan.intended_snapshot
    )
    intended_names = tuple(
        document.filename
        for document in intended.documents
    )

    unsupported_names = tuple(
        filename
        for filename in intended_names
        if not filename.endswith(".hpd")
    )

    if unsupported_names:
        raise _CopiedTreeWritePreparationError(
            staging_directory,
            (
                "Copied-tree storage can stage only immediate lowercase-.hpd "
                "Favorites documents; unsupported intended filename: "
                f"{unsupported_names[0]!r}."
            ),
        )

    if len(set(intended_names)) != len(
        intended_names
    ):
        raise _CopiedTreeWritePreparationError(
            staging_directory,
            "Copied-tree staging requires unique intended HPD filenames.",
        )

    _require_current_preflight_target(
        preflight
    )

    try:
        shutil.copytree(
            preflight.resolved_directory,
            staging_directory,
            copy_function=shutil.copy2,
            symlinks=True,
        )
    except OSError as error:
        raise _CopiedTreeWritePreparationError(
            staging_directory,
            f"Could not create the complete copied-tree staging tree: {error}",
        ) from error

    _require_current_preflight_target(
        preflight
    )

    try:
        with os.scandir(
            staging_directory
        ) as handle:
            entries = tuple(handle)
    except OSError as error:
        raise _CopiedTreeWritePreparationError(
            staging_directory,
            f"Could not scan copied-tree staging directory: {error}",
        ) from error

    intended_name_set = set(
        intended_names
    )

    for entry in entries:
        if (
            entry.name == "f_list.cfg"
            or not entry.name.endswith(
                ".hpd"
            )
        ):
            continue

        path = Path(entry.path)

        try:
            observed = path.lstat()
        except OSError as error:
            raise _CopiedTreeWritePreparationError(
                path,
                f"Could not inspect staged HPD path: {error}",
            ) from error

        if not stat.S_ISREG(
            observed.st_mode
        ):
            if entry.name in intended_name_set:
                raise _CopiedTreeWritePreparationError(
                    path,
                    "Intended HPD filename collides with non-regular staged material.",
                )
            continue

        if entry.name not in intended_name_set:
            try:
                path.unlink()
            except OSError as error:
                raise _CopiedTreeWritePreparationError(
                    path,
                    f"Could not remove obsolete staged HPD file: {error}",
                ) from error

    _write_staged_regular_file(
        staging_directory
        / "f_list.cfg",
        intended.catalog_bytes,
    )

    for document in intended.documents:
        _write_staged_regular_file(
            staging_directory
            / document.filename,
            document.content,
        )

    try:
        staged_snapshot = (
            FavoritesCopiedTreeStorageSource(
                staging_directory
            ).read_snapshot()
        )
    except FavoritesCopiedTreeStorageError as error:
        raise _CopiedTreeWritePreparationError(
            error.path,
            (
                "Copied-tree staged managed snapshot could not be read back: "
                f"{error.message}"
            ),
        ) from error

    if staged_snapshot != intended:
        raise _CopiedTreeWritePreparationError(
            staging_directory,
            "Copied-tree staged managed snapshot differs from the exact intended snapshot.",
        )

    staged_workspace = (
        project_favorites_storage_snapshot(
            staged_snapshot
        )
    )
    staged_validation = (
        validate_favorites_workspace(
            staged_workspace
        )
    )

    if (
        staged_workspace
        != preflight.plan.intended_workspace
    ):
        raise _CopiedTreeWritePreparationError(
            staging_directory,
            "Copied-tree staged workspace differs from the write-plan intended workspace.",
        )

    if (
        staged_validation
        != preflight.plan.intended_validation
    ):
        raise _CopiedTreeWritePreparationError(
            staging_directory,
            "Copied-tree staged schema evidence differs from the write-plan intended validation.",
        )

    try:
        staging_evidence = (
            _copied_tree_evidence(
                staging_directory
            )
        )
    except _CopiedTreeUnsafeError as error:
        raise _CopiedTreeWritePreparationError(
            error.path,
            (
                "Copied-tree staging could not be verified safely: "
                f"{error.message}"
            ),
        ) from error

    return _CopiedTreePreparedStage(
        directory=staging_directory,
        snapshot=staged_snapshot,
        device=staging_evidence.device,
        inode=staging_evidence.inode,
        tree_sha256=staging_evidence.sha256,
    )


class _CopiedTreeRecoveryStatus(StrEnum):
    NOT_NEEDED = "not_needed"
    RESTORED = "restored"
    INCOMPLETE = "incomplete"


class _CopiedTreeReplacementError(RuntimeError):
    def __init__(
        self,
        path: Path,
        message: str,
        *,
        recovery_status: _CopiedTreeRecoveryStatus,
        recovery_message: str | None = None,
    ) -> None:
        self.path = path
        self.message = message
        self.recovery_status = recovery_status
        self.recovery_message = recovery_message
        detail = (
            f"Favorites copied-tree replacement failed at {path}: {message} "
            f"(recovery={recovery_status.value})"
        )
        if recovery_message is not None:
            detail = f"{detail}: {recovery_message}"
        super().__init__(detail)


@dataclass(frozen=True, slots=True)
class _CopiedTreeReplacementResult:
    active_snapshot: FavoritesStorageSnapshot
    active_tree_sha256: str
    displaced_directory: Path

    def __post_init__(self) -> None:
        if not isinstance(
            self.active_snapshot,
            FavoritesStorageSnapshot,
        ):
            raise TypeError(
                "Favorites copied-tree replacement snapshot must be "
                "FavoritesStorageSnapshot."
            )
        if not isinstance(
            self.active_tree_sha256,
            str,
        ):
            raise TypeError(
                "Favorites copied-tree replacement SHA-256 must be a string."
            )
        if (
            len(self.active_tree_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.active_tree_sha256
            )
        ):
            raise ValueError(
                "Favorites copied-tree replacement SHA-256 must be 64 "
                "lowercase hexadecimal characters."
            )
        if not isinstance(
            self.displaced_directory,
            Path,
        ):
            raise TypeError(
                "Favorites copied-tree displaced directory must be pathlib.Path."
            )
        if not self.displaced_directory.is_absolute():
            raise ValueError(
                "Favorites copied-tree displaced directory must be absolute."
            )


def _verify_verified_backup_current(
    preflight: FavoritesCopiedTreeWritePreflight,
    backup_directory: Path,
) -> _CopiedTreeEvidence:
    if not isinstance(
        backup_directory,
        Path,
    ):
        raise TypeError(
            "Favorites copied-tree backup directory must be pathlib.Path."
        )

    try:
        evidence = _copied_tree_evidence(
            backup_directory
        )
    except _CopiedTreeUnsafeError as error:
        raise _CopiedTreeWritePreparationError(
            error.path,
            (
                "Verified copied-tree backup is no longer safe: "
                f"{error.message}"
            ),
        ) from error

    if evidence.sha256 != preflight.tree_sha256:
        raise _CopiedTreeWritePreparationError(
            backup_directory,
            "Verified copied-tree backup changed after verification.",
        )

    try:
        snapshot = FavoritesCopiedTreeStorageSource(
            backup_directory
        ).read_snapshot()
    except FavoritesCopiedTreeStorageError as error:
        raise _CopiedTreeWritePreparationError(
            error.path,
            (
                "Verified copied-tree backup can no longer be read: "
                f"{error.message}"
            ),
        ) from error

    if snapshot != preflight.observed_snapshot:
        raise _CopiedTreeWritePreparationError(
            backup_directory,
            "Verified copied-tree backup managed snapshot changed.",
        )

    return evidence


def _verify_prepared_stage_current(
    preflight: FavoritesCopiedTreeWritePreflight,
    prepared: _CopiedTreePreparedStage,
) -> _CopiedTreeEvidence:
    if not isinstance(
        prepared,
        _CopiedTreePreparedStage,
    ):
        raise TypeError(
            "Favorites copied-tree replacement requires _CopiedTreePreparedStage."
        )

    try:
        observed = prepared.directory.lstat()
    except OSError as error:
        raise _CopiedTreeWritePreparationError(
            prepared.directory,
            f"Could not inspect verified copied-tree staging: {error}",
        ) from error

    if (
        observed.st_dev,
        observed.st_ino,
    ) != (
        prepared.device,
        prepared.inode,
    ):
        raise _CopiedTreeWritePreparationError(
            prepared.directory,
            "Verified copied-tree staging identity changed.",
        )

    try:
        evidence = _copied_tree_evidence(
            prepared.directory
        )
    except _CopiedTreeUnsafeError as error:
        raise _CopiedTreeWritePreparationError(
            error.path,
            (
                "Verified copied-tree staging is no longer safe: "
                f"{error.message}"
            ),
        ) from error

    if evidence.sha256 != prepared.tree_sha256:
        raise _CopiedTreeWritePreparationError(
            prepared.directory,
            "Verified copied-tree staging content or structure changed.",
        )

    try:
        snapshot = FavoritesCopiedTreeStorageSource(
            prepared.directory
        ).read_snapshot()
    except FavoritesCopiedTreeStorageError as error:
        raise _CopiedTreeWritePreparationError(
            error.path,
            (
                "Verified copied-tree staging can no longer be read: "
                f"{error.message}"
            ),
        ) from error

    if (
        snapshot != prepared.snapshot
        or snapshot != preflight.plan.intended_snapshot
    ):
        raise _CopiedTreeWritePreparationError(
            prepared.directory,
            "Verified copied-tree staging no longer matches the exact intended snapshot.",
        )

    return evidence


def _verify_replacement_active(
    preflight: FavoritesCopiedTreeWritePreflight,
    prepared: _CopiedTreePreparedStage,
) -> _CopiedTreeEvidence:
    root = preflight.resolved_directory

    try:
        observed = root.lstat()
    except OSError as error:
        raise _CopiedTreeWritePreparationError(
            root,
            f"Could not inspect replaced copied-tree target: {error}",
        ) from error

    if (
        observed.st_dev,
        observed.st_ino,
    ) != (
        prepared.device,
        prepared.inode,
    ):
        raise _CopiedTreeWritePreparationError(
            root,
            "Replaced copied-tree target does not retain the verified staging identity.",
        )

    try:
        evidence = _copied_tree_evidence(
            root
        )
    except _CopiedTreeUnsafeError as error:
        raise _CopiedTreeWritePreparationError(
            error.path,
            (
                "Replaced copied-tree target is not safe: "
                f"{error.message}"
            ),
        ) from error

    if evidence.sha256 != prepared.tree_sha256:
        raise _CopiedTreeWritePreparationError(
            root,
            "Replaced copied-tree target differs from verified staging evidence.",
        )

    try:
        snapshot = FavoritesCopiedTreeStorageSource(
            root
        ).read_snapshot()
    except FavoritesCopiedTreeStorageError as error:
        raise _CopiedTreeWritePreparationError(
            error.path,
            (
                "Replaced copied-tree target could not be read back: "
                f"{error.message}"
            ),
        ) from error

    if snapshot != preflight.plan.intended_snapshot:
        raise _CopiedTreeWritePreparationError(
            root,
            "Replaced copied-tree target differs from the exact intended snapshot.",
        )

    return evidence


def _restore_displaced_after_replacement_failure(
    preflight: FavoritesCopiedTreeWritePreflight,
    prepared: _CopiedTreePreparedStage,
    displaced_directory: Path,
) -> tuple[
    _CopiedTreeRecoveryStatus,
    str | None,
]:
    root = preflight.resolved_directory

    try:
        if os.path.lexists(
            root
        ):
            if os.path.lexists(
                prepared.directory
            ):
                return (
                    _CopiedTreeRecoveryStatus.INCOMPLETE,
                    (
                        "Could not move the failed replacement aside because "
                        "the verified staging path is occupied."
                    ),
                )

            os.rename(
                root,
                prepared.directory,
            )

        if not os.path.lexists(
            displaced_directory
        ):
            return (
                _CopiedTreeRecoveryStatus.INCOMPLETE,
                "The displaced pre-operation tree is unavailable for recovery.",
            )

        os.rename(
            displaced_directory,
            root,
        )
        _require_current_preflight_target(
            preflight
        )
    except BaseException as error:
        return (
            _CopiedTreeRecoveryStatus.INCOMPLETE,
            f"Automatic restoration of the displaced tree failed: {error}",
        )

    return (
        _CopiedTreeRecoveryStatus.RESTORED,
        "The exact pre-operation copied tree was restored.",
    )


def _replace_active_with_verified_staging(
    preflight: FavoritesCopiedTreeWritePreflight,
    backup_directory: Path,
    prepared: _CopiedTreePreparedStage,
    displaced_directory: Path,
) -> _CopiedTreeReplacementResult:
    if not isinstance(
        preflight,
        FavoritesCopiedTreeWritePreflight,
    ):
        raise TypeError(
            "Favorites copied-tree replacement requires "
            "FavoritesCopiedTreeWritePreflight."
        )
    if not isinstance(
        displaced_directory,
        Path,
    ):
        raise TypeError(
            "Favorites copied-tree displaced directory must be pathlib.Path."
        )
    if not displaced_directory.is_absolute():
        raise ValueError(
            "Favorites copied-tree displaced directory must be absolute."
        )

    expected_parent = (
        preflight.resolved_directory.parent
    )

    try:
        displaced_parent = (
            displaced_directory.parent.resolve(
                strict=True
            )
        )
    except (OSError, RuntimeError) as error:
        raise _CopiedTreeWritePreparationError(
            displaced_directory.parent,
            f"Could not resolve the displaced-tree parent directory: {error}",
        ) from error

    if displaced_parent != expected_parent:
        raise _CopiedTreeWritePreparationError(
            displaced_directory,
            "Displaced copied-tree recovery path must be a sibling of the active target.",
        )

    if os.path.lexists(
        displaced_directory
    ):
        raise _CopiedTreeWritePreparationError(
            displaced_directory,
            "Displaced copied-tree recovery path already exists.",
        )

    _verify_verified_backup_current(
        preflight,
        backup_directory,
    )
    _verify_prepared_stage_current(
        preflight,
        prepared,
    )

    # This is the second exact stale-baseline check immediately before
    # active replacement.
    _require_current_preflight_target(
        preflight
    )

    mutation_started = False

    try:
        os.rename(
            preflight.resolved_directory,
            displaced_directory,
        )
        mutation_started = True

        os.rename(
            prepared.directory,
            preflight.resolved_directory,
        )

        active_evidence = (
            _verify_replacement_active(
                preflight,
                prepared,
            )
        )
    except BaseException as error:
        if mutation_started:
            (
                recovery_status,
                recovery_message,
            ) = _restore_displaced_after_replacement_failure(
                preflight,
                prepared,
                displaced_directory,
            )
        else:
            recovery_status = (
                _CopiedTreeRecoveryStatus.NOT_NEEDED
            )
            recovery_message = None

        raise _CopiedTreeReplacementError(
            preflight.resolved_directory,
            f"Could not activate verified copied-tree staging: {error}",
            recovery_status=recovery_status,
            recovery_message=recovery_message,
        ) from error

    return _CopiedTreeReplacementResult(
        active_snapshot=preflight.plan.intended_snapshot,
        active_tree_sha256=active_evidence.sha256,
        displaced_directory=displaced_directory,
    )


class _CopiedTreeReplacementOutcome(StrEnum):
    NOT_STARTED = "not_started"
    COMPLETED = "completed"
    FAILED = "failed"


def _require_sha256(
    value: str,
    *,
    label: str,
) -> str:
    if not isinstance(
        value,
        str,
    ):
        raise TypeError(
            f"{label} must be a string."
        )
    if (
        len(value) != 64
        or any(
            character not in "0123456789abcdef"
            for character in value
        )
    ):
        raise ValueError(
            f"{label} must be 64 lowercase hexadecimal characters."
        )
    return value


def _copied_tree_operation_id(
    preflight: FavoritesCopiedTreeWritePreflight,
) -> str:
    if not isinstance(
        preflight,
        FavoritesCopiedTreeWritePreflight,
    ):
        raise TypeError(
            "Favorites copied-tree operation identity requires "
            "FavoritesCopiedTreeWritePreflight."
        )

    digest = hashlib.sha256()
    _hash_field(
        digest,
        b"sds200-favorites-copied-tree-write-operation-v1",
    )
    _hash_field(
        digest,
        os.fsencode(
            str(
                preflight.resolved_directory
            )
        ),
    )
    _hash_field(
        digest,
        _favorites_storage_snapshot_sha256(
            preflight.plan.baseline_snapshot
        ).encode("ascii"),
    )
    _hash_field(
        digest,
        _favorites_storage_snapshot_sha256(
            preflight.plan.intended_snapshot
        ).encode("ascii"),
    )
    _hash_field(
        digest,
        preflight.tree_sha256.encode(
            "ascii"
        ),
    )
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class _CopiedTreeRollbackManifest:
    operation_id: str
    target_directory: Path
    backup_directory: Path
    displaced_directory: Path
    baseline_snapshot_sha256: str
    baseline_tree_sha256: str

    def __post_init__(self) -> None:
        _require_sha256(
            self.operation_id,
            label="Favorites copied-tree operation ID",
        )
        for label, path in (
            (
                "target directory",
                self.target_directory,
            ),
            (
                "backup directory",
                self.backup_directory,
            ),
            (
                "displaced directory",
                self.displaced_directory,
            ),
        ):
            if not isinstance(
                path,
                Path,
            ):
                raise TypeError(
                    f"Favorites copied-tree rollback {label} "
                    "must be pathlib.Path."
                )
            if not path.is_absolute():
                raise ValueError(
                    f"Favorites copied-tree rollback {label} "
                    "must be absolute."
                )

        _require_sha256(
            self.baseline_snapshot_sha256,
            label=(
                "Favorites copied-tree rollback baseline snapshot SHA-256"
            ),
        )
        _require_sha256(
            self.baseline_tree_sha256,
            label=(
                "Favorites copied-tree rollback baseline tree SHA-256"
            ),
        )

    def as_dict(
        self,
    ) -> dict[str, object]:
        return {
            "schema_version": 1,
            "operation_id": self.operation_id,
            "target_directory": str(
                self.target_directory
            ),
            "backup_directory": str(
                self.backup_directory
            ),
            "displaced_directory": str(
                self.displaced_directory
            ),
            "baseline_snapshot_sha256":
                self.baseline_snapshot_sha256,
            "baseline_tree_sha256":
                self.baseline_tree_sha256,
            "restore_instruction": (
                "Use the verified backup or displaced pre-operation tree "
                "only after confirming the active target is not newer."
            ),
        }


@dataclass(frozen=True, slots=True)
class _CopiedTreeOperationReport:
    operation_id: str
    target_directory: Path
    backup_directory: Path
    staging_directory: Path
    displaced_directory: Path
    baseline_snapshot_sha256: str
    intended_snapshot_sha256: str
    baseline_tree_sha256: str
    backup_verified: bool
    staging_verified: bool
    second_baseline_verified: bool
    replacement_outcome: _CopiedTreeReplacementOutcome
    recovery_outcome: _CopiedTreeRecoveryStatus
    recovery_message: str | None = None
    active_snapshot_sha256: str | None = None

    def __post_init__(self) -> None:
        _require_sha256(
            self.operation_id,
            label="Favorites copied-tree operation ID",
        )
        for label, path in (
            (
                "target directory",
                self.target_directory,
            ),
            (
                "backup directory",
                self.backup_directory,
            ),
            (
                "staging directory",
                self.staging_directory,
            ),
            (
                "displaced directory",
                self.displaced_directory,
            ),
        ):
            if not isinstance(
                path,
                Path,
            ):
                raise TypeError(
                    f"Favorites copied-tree operation {label} "
                    "must be pathlib.Path."
                )
            if not path.is_absolute():
                raise ValueError(
                    f"Favorites copied-tree operation {label} must be absolute."
                )

        _require_sha256(
            self.baseline_snapshot_sha256,
            label=(
                "Favorites copied-tree operation baseline snapshot SHA-256"
            ),
        )
        _require_sha256(
            self.intended_snapshot_sha256,
            label=(
                "Favorites copied-tree operation intended snapshot SHA-256"
            ),
        )
        _require_sha256(
            self.baseline_tree_sha256,
            label=(
                "Favorites copied-tree operation baseline tree SHA-256"
            ),
        )

        for label, value in (
            (
                "backup verified",
                self.backup_verified,
            ),
            (
                "staging verified",
                self.staging_verified,
            ),
            (
                "second baseline verified",
                self.second_baseline_verified,
            ),
        ):
            if type(value) is not bool:
                raise TypeError(
                    f"Favorites copied-tree operation {label} must be bool."
                )

        if not isinstance(
            self.replacement_outcome,
            _CopiedTreeReplacementOutcome,
        ):
            raise TypeError(
                "Favorites copied-tree replacement outcome must be "
                "_CopiedTreeReplacementOutcome."
            )

        if not isinstance(
            self.recovery_outcome,
            _CopiedTreeRecoveryStatus,
        ):
            raise TypeError(
                "Favorites copied-tree recovery outcome must be "
                "_CopiedTreeRecoveryStatus."
            )

        if (
            self.recovery_message is not None
            and not isinstance(
                self.recovery_message,
                str,
            )
        ):
            raise TypeError(
                "Favorites copied-tree recovery message must be str or None."
            )

        if self.active_snapshot_sha256 is not None:
            _require_sha256(
                self.active_snapshot_sha256,
                label=(
                    "Favorites copied-tree active snapshot SHA-256"
                ),
            )

    def as_dict(
        self,
    ) -> dict[str, object]:
        return {
            "schema_version": 1,
            "operation_id": self.operation_id,
            "target_directory": str(
                self.target_directory
            ),
            "backup_directory": str(
                self.backup_directory
            ),
            "staging_directory": str(
                self.staging_directory
            ),
            "displaced_directory": str(
                self.displaced_directory
            ),
            "baseline_snapshot_sha256":
                self.baseline_snapshot_sha256,
            "intended_snapshot_sha256":
                self.intended_snapshot_sha256,
            "baseline_tree_sha256":
                self.baseline_tree_sha256,
            "backup_verified":
                self.backup_verified,
            "staging_verified":
                self.staging_verified,
            "second_baseline_verified":
                self.second_baseline_verified,
            "replacement_outcome":
                self.replacement_outcome.value,
            "recovery_outcome":
                self.recovery_outcome.value,
            "recovery_message":
                self.recovery_message,
            "active_snapshot_sha256":
                self.active_snapshot_sha256,
        }


def _write_durable_json(
    path: Path,
    payload: dict[str, object],
) -> None:
    if not isinstance(
        path,
        Path,
    ):
        raise TypeError(
            "Favorites copied-tree durable JSON path must be pathlib.Path."
        )
    if not path.is_absolute():
        raise ValueError(
            "Favorites copied-tree durable JSON path must be absolute."
        )
    if not isinstance(
        payload,
        dict,
    ):
        raise TypeError(
            "Favorites copied-tree durable JSON payload must be dict."
        )

    parent = path.parent

    try:
        parent_status = parent.lstat()
    except OSError as error:
        raise _CopiedTreeWritePreparationError(
            parent,
            f"Could not inspect durable-report parent directory: {error}",
        ) from error

    if stat.S_ISLNK(
        parent_status.st_mode
    ):
        raise _CopiedTreeWritePreparationError(
            parent,
            "Durable-report parent directory must not be a symbolic link.",
        )
    if not stat.S_ISDIR(
        parent_status.st_mode
    ):
        raise _CopiedTreeWritePreparationError(
            parent,
            "Durable-report parent must be a directory.",
        )

    try:
        resolved_parent = parent.resolve(
            strict=True
        )
    except (OSError, RuntimeError) as error:
        raise _CopiedTreeWritePreparationError(
            parent,
            f"Could not resolve durable-report parent directory: {error}",
        ) from error

    if resolved_parent != parent:
        raise _CopiedTreeWritePreparationError(
            parent,
            "Durable-report parent path must be canonical.",
        )

    if os.path.lexists(
        path
    ):
        raise _CopiedTreeWritePreparationError(
            path,
            "Durable Favorites operation record already exists.",
        )

    encoded = (
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")

    temporary: Path | None = None

    try:
        with NamedTemporaryFile(
            mode="wb",
            dir=parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(
                handle.name
            )
            handle.write(encoded)
            handle.flush()
            os.fsync(
                handle.fileno()
            )

        assert temporary is not None

        try:
            os.link(
                temporary,
                path,
                follow_symlinks=False,
            )
        except OSError as error:
            raise _CopiedTreeWritePreparationError(
                path,
                f"Could not publish durable Favorites operation record: {error}",
            ) from error

        try:
            parent_descriptor = os.open(
                parent,
                os.O_RDONLY
                | getattr(
                    os,
                    "O_DIRECTORY",
                    0,
                ),
            )
        except OSError as error:
            raise _CopiedTreeWritePreparationError(
                parent,
                f"Could not open durable-report parent for synchronization: {error}",
            ) from error

        try:
            os.fsync(
                parent_descriptor
            )
        except OSError as error:
            raise _CopiedTreeWritePreparationError(
                parent,
                f"Could not synchronize durable-report parent directory: {error}",
            ) from error
        finally:
            os.close(
                parent_descriptor
            )
    finally:
        if temporary is not None:
            with suppress(FileNotFoundError):
                temporary.unlink()


def _write_rollback_manifest(
    path: Path,
    manifest: _CopiedTreeRollbackManifest,
) -> None:
    if not isinstance(
        manifest,
        _CopiedTreeRollbackManifest,
    ):
        raise TypeError(
            "Favorites copied-tree rollback writer requires "
            "_CopiedTreeRollbackManifest."
        )
    _write_durable_json(
        path,
        manifest.as_dict(),
    )


def _write_operation_report(
    path: Path,
    report: _CopiedTreeOperationReport,
) -> None:
    if not isinstance(
        report,
        _CopiedTreeOperationReport,
    ):
        raise TypeError(
            "Favorites copied-tree report writer requires "
            "_CopiedTreeOperationReport."
        )
    _write_durable_json(
        path,
        report.as_dict(),
    )


class FavoritesCopiedTreeWriteExecutionStatus(StrEnum):
    """Outcome for one public copied-tree Favorites write execution."""

    NOOP = "noop"
    COMPLETED = "completed"


class FavoritesCopiedTreeWriteExecutionError(RuntimeError):
    """Report a failed copied-tree Favorites write execution."""

    def __init__(
        self,
        message: str,
        *,
        operation_id: str | None,
        report_path: Path | None,
        recovery_status: str | None,
    ) -> None:
        if not isinstance(
            message,
            str,
        ):
            raise TypeError(
                "Favorites copied-tree execution error message must be a string."
            )
        if not message:
            raise ValueError(
                "Favorites copied-tree execution error message must not be empty."
            )
        if operation_id is not None:
            _require_sha256(
                operation_id,
                label="Favorites copied-tree execution operation ID",
            )
        if (
            report_path is not None
            and not isinstance(
                report_path,
                Path,
            )
        ):
            raise TypeError(
                "Favorites copied-tree execution report path must be pathlib.Path or None."
            )
        if (
            recovery_status is not None
            and not isinstance(
                recovery_status,
                str,
            )
        ):
            raise TypeError(
                "Favorites copied-tree execution recovery status must be str or None."
            )

        self.operation_id = operation_id
        self.report_path = report_path
        self.recovery_status = recovery_status
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class FavoritesCopiedTreeWriteExecutionResult:
    """Public immutable result for one completed copied-tree write request."""

    status: FavoritesCopiedTreeWriteExecutionStatus
    target_directory: Path
    operation_id: str | None = None
    backup_directory: Path | None = None
    staging_directory: Path | None = None
    displaced_directory: Path | None = None
    rollback_manifest_path: Path | None = None
    operation_report_path: Path | None = None

    def __post_init__(self) -> None:
        if not isinstance(
            self.status,
            FavoritesCopiedTreeWriteExecutionStatus,
        ):
            raise TypeError(
                "Favorites copied-tree execution status must be "
                "FavoritesCopiedTreeWriteExecutionStatus."
            )
        if not isinstance(
            self.target_directory,
            Path,
        ):
            raise TypeError(
                "Favorites copied-tree execution target must be pathlib.Path."
            )
        if not self.target_directory.is_absolute():
            raise ValueError(
                "Favorites copied-tree execution target must be absolute."
            )

        paths = (
            self.backup_directory,
            self.staging_directory,
            self.displaced_directory,
            self.rollback_manifest_path,
            self.operation_report_path,
        )

        for path in paths:
            if path is not None:
                if not isinstance(
                    path,
                    Path,
                ):
                    raise TypeError(
                        "Favorites copied-tree execution artifact paths must be "
                        "pathlib.Path or None."
                    )
                if not path.is_absolute():
                    raise ValueError(
                        "Favorites copied-tree execution artifact paths must be absolute."
                    )

        if (
            self.status
            is FavoritesCopiedTreeWriteExecutionStatus.NOOP
        ):
            if self.operation_id is not None or any(
                path is not None
                for path in paths
            ):
                raise ValueError(
                    "No-op Favorites copied-tree execution must retain no operation "
                    "or artifact paths."
                )
            return

        if self.operation_id is None:
            raise ValueError(
                "Completed Favorites copied-tree execution requires an operation ID."
            )

        _require_sha256(
            self.operation_id,
            label="Favorites copied-tree execution operation ID",
        )

        if any(
            path is None
            for path in paths
        ):
            raise ValueError(
                "Completed Favorites copied-tree execution requires all artifact paths."
            )


@dataclass(frozen=True, slots=True)
class _CopiedTreeOperationPaths:
    operation_id: str
    backup_directory: Path
    staging_directory: Path
    displaced_directory: Path
    rollback_manifest_path: Path
    operation_report_path: Path
    failure_report_path: Path

    def __post_init__(self) -> None:
        _require_sha256(
            self.operation_id,
            label="Favorites copied-tree operation ID",
        )
        for path in (
            self.backup_directory,
            self.staging_directory,
            self.displaced_directory,
            self.rollback_manifest_path,
            self.operation_report_path,
            self.failure_report_path,
        ):
            if not isinstance(
                path,
                Path,
            ):
                raise TypeError(
                    "Favorites copied-tree operation paths must be pathlib.Path."
                )
            if not path.is_absolute():
                raise ValueError(
                    "Favorites copied-tree operation paths must be absolute."
                )


def _copied_tree_operation_paths(
    preflight: FavoritesCopiedTreeWritePreflight,
) -> _CopiedTreeOperationPaths:
    operation_id = (
        _copied_tree_operation_id(
            preflight
        )
    )
    parent = (
        preflight.resolved_directory.parent
    )
    stem = (
        f".{preflight.resolved_directory.name}."
        f"sds200-favorites-{operation_id[:16]}"
    )

    return _CopiedTreeOperationPaths(
        operation_id=operation_id,
        backup_directory=parent / f"{stem}.backup",
        staging_directory=parent / f"{stem}.staging",
        displaced_directory=parent / f"{stem}.displaced",
        rollback_manifest_path=parent / f"{stem}.rollback.json",
        operation_report_path=parent / f"{stem}.report.json",
        failure_report_path=parent / f"{stem}.failure.json",
    )


def _require_operation_paths_available(
    paths: _CopiedTreeOperationPaths,
) -> None:
    for path in (
        paths.backup_directory,
        paths.staging_directory,
        paths.displaced_directory,
        paths.rollback_manifest_path,
        paths.operation_report_path,
        paths.failure_report_path,
    ):
        if os.path.lexists(
            path
        ):
            raise _CopiedTreeWritePreparationError(
                path,
                "Favorites copied-tree operation artifact path already exists.",
            )


def _operation_report(
    preflight: FavoritesCopiedTreeWritePreflight,
    paths: _CopiedTreeOperationPaths,
    *,
    backup_verified: bool,
    staging_verified: bool,
    second_baseline_verified: bool,
    replacement_outcome: _CopiedTreeReplacementOutcome,
    recovery_outcome: _CopiedTreeRecoveryStatus,
    recovery_message: str | None = None,
    active_snapshot: FavoritesStorageSnapshot | None = None,
) -> _CopiedTreeOperationReport:
    return _CopiedTreeOperationReport(
        operation_id=paths.operation_id,
        target_directory=preflight.resolved_directory,
        backup_directory=paths.backup_directory,
        staging_directory=paths.staging_directory,
        displaced_directory=paths.displaced_directory,
        baseline_snapshot_sha256=(
            _favorites_storage_snapshot_sha256(
                preflight.plan.baseline_snapshot
            )
        ),
        intended_snapshot_sha256=(
            _favorites_storage_snapshot_sha256(
                preflight.plan.intended_snapshot
            )
        ),
        baseline_tree_sha256=preflight.tree_sha256,
        backup_verified=backup_verified,
        staging_verified=staging_verified,
        second_baseline_verified=second_baseline_verified,
        replacement_outcome=replacement_outcome,
        recovery_outcome=recovery_outcome,
        recovery_message=recovery_message,
        active_snapshot_sha256=(
            None
            if active_snapshot is None
            else _favorites_storage_snapshot_sha256(
                active_snapshot
            )
        ),
    )


def _persist_failure_report(
    path: Path,
    report: _CopiedTreeOperationReport,
) -> tuple[
    Path | None,
    str | None,
]:
    try:
        _write_operation_report(
            path,
            report,
        )
    except BaseException as error:
        return (
            None,
            f"Could not persist the final operation report: {error}",
        )

    return (
        path,
        None,
    )


class FavoritesCopiedTreeWritePreflightError(RuntimeError):
    """Report one copied-tree write refusal before any mutation."""

    def __init__(
        self,
        reason: FavoritesCopiedTreeWritePreflightReason,
        path: Path,
        message: str,
    ) -> None:
        if not isinstance(
            reason,
            FavoritesCopiedTreeWritePreflightReason,
        ):
            raise TypeError(
                "Favorites copied-tree write preflight reason must be "
                "FavoritesCopiedTreeWritePreflightReason."
            )
        if not isinstance(path, Path):
            raise TypeError(
                "Favorites copied-tree write preflight path must be pathlib.Path."
            )
        if not isinstance(message, str):
            raise TypeError(
                "Favorites copied-tree write preflight message must be a string."
            )
        if not message:
            raise ValueError(
                "Favorites copied-tree write preflight message must not be empty."
            )

        self.reason = reason
        self.path = path
        self.message = message
        super().__init__(
            f"Favorites copied-tree write preflight failed at {path}: {message}"
        )


@dataclass(frozen=True, slots=True)
class FavoritesCopiedTreeWritePreflight:
    """Immutable exact-target evidence retained before write-side effects."""

    plan: FavoritesWritePlan
    requested_directory: Path
    resolved_directory: Path
    observed_snapshot: FavoritesStorageSnapshot
    target_device: int
    target_inode: int
    tree_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.plan, FavoritesWritePlan):
            raise TypeError(
                "Favorites copied-tree write preflight plan must be "
                "FavoritesWritePlan."
            )
        if not isinstance(self.requested_directory, Path):
            raise TypeError(
                "Favorites copied-tree write requested directory must be "
                "pathlib.Path."
            )
        if not isinstance(self.resolved_directory, Path):
            raise TypeError(
                "Favorites copied-tree write resolved directory must be "
                "pathlib.Path."
            )
        if not self.resolved_directory.is_absolute():
            raise ValueError(
                "Favorites copied-tree write resolved directory must be absolute."
            )
        if not isinstance(
            self.observed_snapshot,
            FavoritesStorageSnapshot,
        ):
            raise TypeError(
                "Favorites copied-tree write observed snapshot must be "
                "FavoritesStorageSnapshot."
            )
        if not self.plan.matches_baseline_snapshot(
            self.observed_snapshot
        ):
            raise ValueError(
                "Favorites copied-tree write preflight must retain an exact "
                "baseline-matching target snapshot."
            )
        if type(self.target_device) is not int:
            raise TypeError(
                "Favorites copied-tree write target device must be an integer."
            )
        if self.target_device < 0:
            raise ValueError(
                "Favorites copied-tree write target device must be non-negative."
            )
        if type(self.target_inode) is not int:
            raise TypeError(
                "Favorites copied-tree write target inode must be an integer."
            )
        if self.target_inode < 0:
            raise ValueError(
                "Favorites copied-tree write target inode must be non-negative."
            )
        if not isinstance(
            self.tree_sha256,
            str,
        ):
            raise TypeError(
                "Favorites copied-tree write tree SHA-256 must be a string."
            )
        if (
            len(self.tree_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.tree_sha256
            )
        ):
            raise ValueError(
                "Favorites copied-tree write tree SHA-256 must be 64 "
                "lowercase hexadecimal characters."
            )

    @property
    def is_noop(self) -> bool:
        """Return whether the exact confirmed plan requires no storage change."""

        return self.plan.is_noop

    @property
    def lock_path(self) -> Path:
        """Return the sibling operation-lock path without creating it."""

        return self.resolved_directory.parent / (
            f".{self.resolved_directory.name}.sds200-favorites-write.lock"
        )


def preflight_favorites_copied_tree_write(
    plan: FavoritesWritePlan,
    favorites_directory: Path,
) -> FavoritesCopiedTreeWritePreflight:
    """Validate one exact copied-tree write target without mutating storage."""

    if not isinstance(plan, FavoritesWritePlan):
        raise TypeError(
            "Favorites copied-tree write preflight requires FavoritesWritePlan."
        )
    if not isinstance(favorites_directory, Path):
        raise TypeError(
            "Favorites copied-tree write directory must be pathlib.Path."
        )

    if plan.is_blocked:
        blockers = ", ".join(
            blocker.value
            for blocker in plan.blockers
        )
        raise FavoritesCopiedTreeWritePreflightError(
            FavoritesCopiedTreeWritePreflightReason.BLOCKED_PLAN,
            favorites_directory,
            (
                "Write plan is blocked by deterministic planning evidence: "
                f"{blockers}."
            ),
        )

    source = FavoritesCopiedTreeStorageSource(
        favorites_directory
    )

    try:
        observed = source.read_snapshot()
        resolved = favorites_directory.resolve(
            strict=True
        )
        tree_evidence = _copied_tree_evidence(
            resolved
        )
        observed_after_tree_scan = source.read_snapshot()
    except FavoritesCopiedTreeStorageError as error:
        raise FavoritesCopiedTreeWritePreflightError(
            FavoritesCopiedTreeWritePreflightReason.TARGET_UNAVAILABLE,
            error.path,
            error.message,
        ) from error
    except _CopiedTreeUnsafeError as error:
        raise FavoritesCopiedTreeWritePreflightError(
            FavoritesCopiedTreeWritePreflightReason.UNSAFE_TREE,
            error.path,
            error.message,
        ) from error
    except (OSError, RuntimeError) as error:
        raise FavoritesCopiedTreeWritePreflightError(
            FavoritesCopiedTreeWritePreflightReason.TARGET_UNAVAILABLE,
            favorites_directory,
            f"Could not resolve Favorites copied-tree target: {error}",
        ) from error

    if observed_after_tree_scan != observed:
        raise FavoritesCopiedTreeWritePreflightError(
            FavoritesCopiedTreeWritePreflightReason.TARGET_STALE,
            resolved,
            (
                "Copied-tree managed snapshot changed while target "
                "identity was being established."
            ),
        )

    if not plan.matches_baseline_snapshot(observed):
        raise FavoritesCopiedTreeWritePreflightError(
            FavoritesCopiedTreeWritePreflightReason.TARGET_STALE,
            resolved,
            (
                "Fresh copied-tree snapshot no longer exactly matches the "
                "write-plan baseline."
            ),
        )

    return FavoritesCopiedTreeWritePreflight(
        plan=plan,
        requested_directory=favorites_directory,
        resolved_directory=resolved,
        observed_snapshot=observed,
        target_device=tree_evidence.device,
        target_inode=tree_evidence.inode,
        tree_sha256=tree_evidence.sha256,
    )


def execute_favorites_copied_tree_write(
    plan: FavoritesWritePlan,
    favorites_directory: Path,
) -> FavoritesCopiedTreeWriteExecutionResult:
    """Execute one exact verified write against an offline copied Favorites tree."""

    preflight = (
        preflight_favorites_copied_tree_write(
            plan,
            favorites_directory,
        )
    )

    if preflight.is_noop:
        return FavoritesCopiedTreeWriteExecutionResult(
            status=FavoritesCopiedTreeWriteExecutionStatus.NOOP,
            target_directory=preflight.resolved_directory,
        )

    paths = _copied_tree_operation_paths(
        preflight
    )
    backup_verified = False
    staging_verified = False
    second_baseline_verified = False
    prepared: _CopiedTreePreparedStage | None = None

    with _copied_tree_operation_lock(
        preflight
    ):
        try:
            _require_operation_paths_available(
                paths
            )

            _create_verified_backup(
                preflight,
                paths.backup_directory,
            )
            backup_verified = True

            manifest = _CopiedTreeRollbackManifest(
                operation_id=paths.operation_id,
                target_directory=preflight.resolved_directory,
                backup_directory=paths.backup_directory,
                displaced_directory=paths.displaced_directory,
                baseline_snapshot_sha256=(
                    _favorites_storage_snapshot_sha256(
                        preflight.plan.baseline_snapshot
                    )
                ),
                baseline_tree_sha256=preflight.tree_sha256,
            )
            _write_rollback_manifest(
                paths.rollback_manifest_path,
                manifest,
            )

            prepared = _create_verified_staging(
                preflight,
                paths.staging_directory,
            )
            staging_verified = True

            try:
                replacement = (
                    _replace_active_with_verified_staging(
                        preflight,
                        paths.backup_directory,
                        prepared,
                        paths.displaced_directory,
                    )
                )
            except _CopiedTreeReplacementError as error:
                second_baseline_verified = True
                report = _operation_report(
                    preflight,
                    paths,
                    backup_verified=backup_verified,
                    staging_verified=staging_verified,
                    second_baseline_verified=second_baseline_verified,
                    replacement_outcome=(
                        _CopiedTreeReplacementOutcome.FAILED
                    ),
                    recovery_outcome=error.recovery_status,
                    recovery_message=error.recovery_message,
                )
                (
                    report_path,
                    report_error,
                ) = _persist_failure_report(
                    paths.operation_report_path,
                    report,
                )
                detail = str(
                    error
                )
                if report_error is not None:
                    detail = (
                        f"{detail}; {report_error}"
                    )
                raise FavoritesCopiedTreeWriteExecutionError(
                    detail,
                    operation_id=paths.operation_id,
                    report_path=report_path,
                    recovery_status=error.recovery_status.value,
                ) from error

            second_baseline_verified = True

            completed_report = _operation_report(
                preflight,
                paths,
                backup_verified=True,
                staging_verified=True,
                second_baseline_verified=True,
                replacement_outcome=(
                    _CopiedTreeReplacementOutcome.COMPLETED
                ),
                recovery_outcome=(
                    _CopiedTreeRecoveryStatus.NOT_NEEDED
                ),
                active_snapshot=replacement.active_snapshot,
            )

            try:
                _write_operation_report(
                    paths.operation_report_path,
                    completed_report,
                )
            except BaseException as report_write_error:
                (
                    recovery_status,
                    recovery_message,
                ) = _restore_displaced_after_replacement_failure(
                    preflight,
                    prepared,
                    paths.displaced_directory,
                )

                failure_report = _operation_report(
                    preflight,
                    paths,
                    backup_verified=True,
                    staging_verified=True,
                    second_baseline_verified=True,
                    replacement_outcome=(
                        _CopiedTreeReplacementOutcome.FAILED
                    ),
                    recovery_outcome=recovery_status,
                    recovery_message=(
                        (
                            "Final operation report persistence failed after "
                            f"replacement: {report_write_error}"
                        )
                        if recovery_message is None
                        else (
                            "Final operation report persistence failed after "
                            f"replacement: {report_write_error}; {recovery_message}"
                        )
                    ),
                )
                (
                    failure_path,
                    failure_report_error,
                ) = _persist_failure_report(
                    paths.failure_report_path,
                    failure_report,
                )

                detail = (
                    "Verified copied-tree replacement was rolled back because "
                    f"the final operation report could not be persisted: {report_write_error}"
                )
                if failure_report_error is not None:
                    detail = (
                        f"{detail}; {failure_report_error}"
                    )

                raise FavoritesCopiedTreeWriteExecutionError(
                    detail,
                    operation_id=paths.operation_id,
                    report_path=failure_path,
                    recovery_status=recovery_status.value,
                ) from report_write_error

            return FavoritesCopiedTreeWriteExecutionResult(
                status=FavoritesCopiedTreeWriteExecutionStatus.COMPLETED,
                target_directory=preflight.resolved_directory,
                operation_id=paths.operation_id,
                backup_directory=paths.backup_directory,
                staging_directory=paths.staging_directory,
                displaced_directory=paths.displaced_directory,
                rollback_manifest_path=paths.rollback_manifest_path,
                operation_report_path=paths.operation_report_path,
            )

        except FavoritesCopiedTreeWriteExecutionError:
            raise
        except BaseException as error:
            report = _operation_report(
                preflight,
                paths,
                backup_verified=backup_verified,
                staging_verified=staging_verified,
                second_baseline_verified=second_baseline_verified,
                replacement_outcome=(
                    _CopiedTreeReplacementOutcome.NOT_STARTED
                ),
                recovery_outcome=(
                    _CopiedTreeRecoveryStatus.NOT_NEEDED
                ),
            )

            (
                report_path,
                report_error,
            ) = _persist_failure_report(
                paths.operation_report_path,
                report,
            )

            detail = str(
                error
            )
            if report_error is not None:
                detail = (
                    f"{detail}; {report_error}"
                )

            raise FavoritesCopiedTreeWriteExecutionError(
                detail,
                operation_id=paths.operation_id,
                report_path=report_path,
                recovery_status=(
                    _CopiedTreeRecoveryStatus.NOT_NEEDED.value
                ),
            ) from error


__all__ = [
    "FavoritesCopiedTreeWriteExecutionError",
    "FavoritesCopiedTreeWriteExecutionResult",
    "FavoritesCopiedTreeWriteExecutionStatus",
    "FavoritesCopiedTreeWritePreflight",
    "FavoritesCopiedTreeWritePreflightError",
    "FavoritesCopiedTreeWritePreflightReason",
    "execute_favorites_copied_tree_write",
    "preflight_favorites_copied_tree_write",
]
