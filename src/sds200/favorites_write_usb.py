"""Pre-mutation safety contract for verified USB Favorites writes."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .favorites_schema import validate_favorites_workspace
from .favorites_storage import (
    FavoritesStorageSnapshot,
    project_favorites_storage_snapshot,
)
from .favorites_storage_evidence import (
    FavoritesTreeEvidence,
    FavoritesTreeEvidenceError,
    favorites_storage_snapshot_sha256,
    favorites_tree_evidence,
)
from .favorites_storage_local import (
    FavoritesCopiedTreeStorageError,
    FavoritesCopiedTreeStorageSource,
)
from .favorites_storage_usb import (
    DEFAULT_LINUX_MOUNTINFO_PATH,
    DEFAULT_LINUX_SYS_DEV_BLOCK_DIRECTORY,
    FavoritesUsbStorageQualification,
    FavoritesUsbStorageQualificationError,
    FavoritesUsbStorageQualificationReason,
    qualify_favorites_usb_storage_path,
)
from .favorites_write_plan import FavoritesWritePlan


class FavoritesUsbWritePreflightReason(StrEnum):
    """Classify one USB write refusal before any storage mutation."""

    BLOCKED_PLAN = "blocked_plan"
    QUALIFICATION_FAILED = "qualification_failed"
    TARGET_STALE = "target_stale"
    UNSAFE_TREE = "unsafe_tree"


class FavoritesUsbWritePreflightError(RuntimeError):
    """Report one USB write refusal before any mutation begins."""

    def __init__(
        self,
        reason: FavoritesUsbWritePreflightReason,
        path: Path,
        message: str,
        *,
        qualification_reason: FavoritesUsbStorageQualificationReason | None = None,
    ) -> None:
        if not isinstance(
            reason,
            FavoritesUsbWritePreflightReason,
        ):
            raise TypeError(
                "Favorites USB write preflight reason must be "
                "FavoritesUsbWritePreflightReason."
            )
        if not isinstance(path, Path):
            raise TypeError(
                "Favorites USB write preflight path must be pathlib.Path."
            )
        if not isinstance(message, str):
            raise TypeError(
                "Favorites USB write preflight message must be a string."
            )
        if not message:
            raise ValueError(
                "Favorites USB write preflight message must not be empty."
            )
        if (
            qualification_reason is not None
            and not isinstance(
                qualification_reason,
                FavoritesUsbStorageQualificationReason,
            )
        ):
            raise TypeError(
                "Favorites USB write qualification reason must be "
                "FavoritesUsbStorageQualificationReason or None."
            )
        if (
            reason
            not in {
                FavoritesUsbWritePreflightReason.QUALIFICATION_FAILED,
                FavoritesUsbWritePreflightReason.TARGET_STALE,
            }
            and qualification_reason is not None
        ):
            raise ValueError(
                "Favorites USB write qualification reason is only valid for "
                "qualification or stale-target failures."
            )

        self.reason = reason
        self.path = path
        self.message = message
        self.qualification_reason = qualification_reason

        detail = (
            ""
            if qualification_reason is None
            else f" [{qualification_reason.value}]"
        )
        super().__init__(
            "Favorites USB write preflight failed "
            f"({reason.value}){detail} at {path}: {message}"
        )


@dataclass(frozen=True, slots=True)
class FavoritesUsbWritePreflight:
    """Immutable exact USB target evidence retained before write-side effects."""

    plan: FavoritesWritePlan
    requested_path: Path
    qualification: FavoritesUsbStorageQualification
    mountinfo_path: Path
    sys_dev_block_directory: Path
    tree_evidence: FavoritesTreeEvidence

    def __post_init__(self) -> None:
        if not isinstance(self.plan, FavoritesWritePlan):
            raise TypeError(
                "Favorites USB write preflight plan must be FavoritesWritePlan."
            )
        if not isinstance(self.requested_path, Path):
            raise TypeError(
                "Favorites USB write requested path must be pathlib.Path."
            )
        if not self.requested_path.is_absolute():
            raise ValueError(
                "Favorites USB write requested path must be absolute."
            )
        if not isinstance(
            self.qualification,
            FavoritesUsbStorageQualification,
        ):
            raise TypeError(
                "Favorites USB write preflight qualification must be "
                "FavoritesUsbStorageQualification."
            )
        for name, value in (
            ("mountinfo path", self.mountinfo_path),
            ("sysfs block directory", self.sys_dev_block_directory),
        ):
            if not isinstance(value, Path):
                raise TypeError(
                    f"Favorites USB write {name} must be pathlib.Path."
                )
            if not value.is_absolute():
                raise ValueError(
                    f"Favorites USB write {name} must be absolute."
                )
        if not isinstance(
            self.tree_evidence,
            FavoritesTreeEvidence,
        ):
            raise TypeError(
                "Favorites USB write tree evidence must be FavoritesTreeEvidence."
            )
        if not self.plan.matches_baseline_snapshot(
            self.qualification.snapshot
        ):
            raise ValueError(
                "Favorites USB write preflight must retain an exact "
                "baseline-matching target snapshot."
            )

        tree_device_number = (
            os.major(self.tree_evidence.device),
            os.minor(self.tree_evidence.device),
        )
        if (
            tree_device_number
            != self.qualification.mount.device_number
        ):
            raise ValueError(
                "Favorites USB write tree evidence must remain on the "
                "qualified mounted device."
            )

    @property
    def observed_snapshot(self) -> FavoritesStorageSnapshot:
        """Return the exact managed snapshot retained by qualification."""

        return self.qualification.snapshot

    @property
    def is_noop(self) -> bool:
        """Return whether the exact confirmed plan requires no storage change."""

        return self.plan.is_noop


def _qualification_preflight_error(
    error: FavoritesUsbStorageQualificationError,
    *,
    stale: bool,
) -> FavoritesUsbWritePreflightError:
    reason = (
        FavoritesUsbWritePreflightReason.TARGET_STALE
        if stale
        else FavoritesUsbWritePreflightReason.QUALIFICATION_FAILED
    )
    return FavoritesUsbWritePreflightError(
        reason,
        error.path,
        error.message,
        qualification_reason=error.reason,
    )


def _require_same_qualification(
    initial: FavoritesUsbStorageQualification,
    current: FavoritesUsbStorageQualification,
) -> None:
    if (
        current.mount != initial.mount
        or current.block_device != initial.block_device
        or current.mount_directory != initial.mount_directory
        or current.favorites_directory != initial.favorites_directory
        or current.snapshot != initial.snapshot
    ):
        raise FavoritesUsbWritePreflightError(
            FavoritesUsbWritePreflightReason.TARGET_STALE,
            initial.favorites_directory,
            (
                "USB target qualification changed while complete-tree "
                "preflight evidence was captured."
            ),
        )


def preflight_favorites_usb_write(
    plan: FavoritesWritePlan,
    path: Path,
    mountinfo_path: Path = DEFAULT_LINUX_MOUNTINFO_PATH,
    *,
    sys_dev_block_directory: Path = DEFAULT_LINUX_SYS_DEV_BLOCK_DIRECTORY,
) -> FavoritesUsbWritePreflight:
    """Capture exact current USB write evidence without mutating any storage."""

    if not isinstance(plan, FavoritesWritePlan):
        raise TypeError(
            "Favorites USB write preflight requires FavoritesWritePlan."
        )
    if not isinstance(path, Path):
        raise TypeError(
            "Favorites USB write preflight path must be pathlib.Path."
        )
    if not path.is_absolute():
        raise ValueError(
            "Favorites USB write preflight path must be absolute."
        )
    if not isinstance(mountinfo_path, Path):
        raise TypeError(
            "Favorites USB write mountinfo path must be pathlib.Path."
        )
    if not mountinfo_path.is_absolute():
        raise ValueError(
            "Favorites USB write mountinfo path must be absolute."
        )
    if not isinstance(
        sys_dev_block_directory,
        Path,
    ):
        raise TypeError(
            "Favorites USB write sysfs block directory must be pathlib.Path."
        )
    if not sys_dev_block_directory.is_absolute():
        raise ValueError(
            "Favorites USB write sysfs block directory must be absolute."
        )

    if plan.is_blocked:
        blockers = ", ".join(
            blocker.value
            for blocker in plan.blockers
        )
        raise FavoritesUsbWritePreflightError(
            FavoritesUsbWritePreflightReason.BLOCKED_PLAN,
            path,
            (
                "Favorites write plan is blocked and cannot enter USB "
                f"preflight: {blockers}."
            ),
        )

    try:
        initial = qualify_favorites_usb_storage_path(
            path,
            mountinfo_path,
            sys_dev_block_directory=sys_dev_block_directory,
        )
    except FavoritesUsbStorageQualificationError as error:
        raise _qualification_preflight_error(
            error,
            stale=False,
        ) from error

    if not plan.matches_baseline_snapshot(
        initial.snapshot
    ):
        raise FavoritesUsbWritePreflightError(
            FavoritesUsbWritePreflightReason.TARGET_STALE,
            initial.favorites_directory,
            (
                "Freshly qualified USB Favorites snapshot does not exactly "
                "match the write-plan baseline."
            ),
        )

    try:
        initial_tree = favorites_tree_evidence(
            initial.favorites_directory
        )
    except FavoritesTreeEvidenceError as error:
        raise FavoritesUsbWritePreflightError(
            FavoritesUsbWritePreflightReason.UNSAFE_TREE,
            error.path,
            error.message,
        ) from error

    try:
        current = qualify_favorites_usb_storage_path(
            path,
            mountinfo_path,
            sys_dev_block_directory=sys_dev_block_directory,
        )
    except FavoritesUsbStorageQualificationError as error:
        raise _qualification_preflight_error(
            error,
            stale=True,
        ) from error

    _require_same_qualification(
        initial,
        current,
    )

    if not plan.matches_baseline_snapshot(
        current.snapshot
    ):
        raise FavoritesUsbWritePreflightError(
            FavoritesUsbWritePreflightReason.TARGET_STALE,
            current.favorites_directory,
            (
                "USB Favorites snapshot stopped matching the write-plan "
                "baseline during preflight."
            ),
        )

    try:
        final_tree = favorites_tree_evidence(
            current.favorites_directory
        )
    except FavoritesTreeEvidenceError as error:
        raise FavoritesUsbWritePreflightError(
            FavoritesUsbWritePreflightReason.UNSAFE_TREE,
            error.path,
            error.message,
        ) from error

    if final_tree != initial_tree:
        raise FavoritesUsbWritePreflightError(
            FavoritesUsbWritePreflightReason.TARGET_STALE,
            current.favorites_directory,
            (
                "USB Favorites complete-tree identity changed while "
                "preflight evidence was captured."
            ),
        )

    return FavoritesUsbWritePreflight(
        plan=plan,
        requested_path=path,
        qualification=current,
        mountinfo_path=mountinfo_path,
        sys_dev_block_directory=sys_dev_block_directory,
        tree_evidence=final_tree,
    )


_USB_WRITE_OPERATION_VERSION = b"sds200-favorites-usb-write-operation-v1"
_USB_WRITE_TARGET_VERSION = b"sds200-favorites-usb-write-target-v1"


class _Digest:
    def __init__(self) -> None:
        self._digest = hashlib.sha256()

    def field(
        self,
        value: bytes,
    ) -> None:
        self._digest.update(
            len(value).to_bytes(
                8,
                "big",
            )
        )
        self._digest.update(value)

    def hexdigest(self) -> str:
        return self._digest.hexdigest()


class _FavoritesUsbWritePreparationError(RuntimeError):
    def __init__(
        self,
        path: Path,
        message: str,
    ) -> None:
        self.path = path
        self.message = message
        super().__init__(
            f"Favorites USB write preparation failed at {path}: {message}"
        )


def _identity_text(
    value: object,
) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bool):
        return b"1" if value else b"0"
    return str(value).encode(
        "utf-8",
        errors="strict",
    )


def _usb_target_lock_key(
    preflight: FavoritesUsbWritePreflight,
) -> str:
    if not isinstance(
        preflight,
        FavoritesUsbWritePreflight,
    ):
        raise TypeError(
            "Favorites USB target lock identity requires "
            "FavoritesUsbWritePreflight."
        )

    qualification = preflight.qualification
    mount = qualification.mount
    block = qualification.block_device
    digest = _Digest()

    digest.field(
        _USB_WRITE_TARGET_VERSION
    )

    for value in (
        qualification.mount_directory,
        qualification.favorites_directory,
        mount.mount_id,
        mount.parent_id,
        mount.device_major,
        mount.device_minor,
        mount.root,
        mount.mount_point,
        ",".join(mount.mount_options),
        ",".join(mount.optional_fields),
        mount.filesystem_type,
        mount.mount_source,
        ",".join(mount.super_options),
        block.device_major,
        block.device_minor,
        block.sysfs_path,
        block.device_name,
        block.usb_ancestor_path,
        block.removable,
    ):
        digest.field(
            _identity_text(value)
        )

    return digest.hexdigest()


def _usb_operation_id(
    preflight: FavoritesUsbWritePreflight,
) -> str:
    if not isinstance(
        preflight,
        FavoritesUsbWritePreflight,
    ):
        raise TypeError(
            "Favorites USB operation identity requires "
            "FavoritesUsbWritePreflight."
        )

    digest = _Digest()
    digest.field(
        _USB_WRITE_OPERATION_VERSION
    )
    digest.field(
        _usb_target_lock_key(
            preflight
        ).encode("ascii")
    )
    digest.field(
        favorites_storage_snapshot_sha256(
            preflight.plan.baseline_snapshot
        ).encode("ascii")
    )
    digest.field(
        favorites_storage_snapshot_sha256(
            preflight.plan.intended_snapshot
        ).encode("ascii")
    )
    digest.field(
        preflight.tree_evidence.sha256.encode(
            "ascii"
        )
    )
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class _FavoritesUsbHostOperationPaths:
    target_lock_key: str
    operation_id: str
    root_directory: Path
    locks_directory: Path
    lock_directory: Path
    operations_directory: Path
    operation_directory: Path
    backup_directory: Path
    staging_directory: Path
    rollback_manifest_path: Path
    operation_report_path: Path
    failure_report_path: Path

    def __post_init__(self) -> None:
        for label, value in (
            ("target lock key", self.target_lock_key),
            ("operation ID", self.operation_id),
        ):
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in value
                )
            ):
                raise ValueError(
                    f"Favorites USB host {label} must be 64 lowercase "
                    "hexadecimal characters."
                )

        for path in (
            self.root_directory,
            self.locks_directory,
            self.lock_directory,
            self.operations_directory,
            self.operation_directory,
            self.backup_directory,
            self.staging_directory,
            self.rollback_manifest_path,
            self.operation_report_path,
            self.failure_report_path,
        ):
            if not isinstance(path, Path):
                raise TypeError(
                    "Favorites USB host operation paths must be pathlib.Path."
                )
            if not path.is_absolute():
                raise ValueError(
                    "Favorites USB host operation paths must be absolute."
                )


def _canonical_host_state_candidate(
    host_state_directory: Path,
    preflight: FavoritesUsbWritePreflight,
) -> Path:
    if not isinstance(
        host_state_directory,
        Path,
    ):
        raise TypeError(
            "Favorites USB host state directory must be pathlib.Path."
        )
    if not host_state_directory.is_absolute():
        raise ValueError(
            "Favorites USB host state directory must be absolute."
        )
    if not isinstance(
        preflight,
        FavoritesUsbWritePreflight,
    ):
        raise TypeError(
            "Favorites USB host state validation requires "
            "FavoritesUsbWritePreflight."
        )

    try:
        canonical = host_state_directory.resolve(
            strict=False
        )
    except (OSError, RuntimeError) as error:
        raise _FavoritesUsbWritePreparationError(
            host_state_directory,
            f"Could not resolve the host state directory: {error}",
        ) from error

    if canonical != host_state_directory:
        raise _FavoritesUsbWritePreparationError(
            host_state_directory,
            "Favorites USB host state directory must be canonical.",
        )

    mount = preflight.qualification.mount_directory
    try:
        canonical.relative_to(
            mount
        )
    except ValueError:
        pass
    else:
        raise _FavoritesUsbWritePreparationError(
            host_state_directory,
            "Favorites USB host state directory must be outside scanner storage.",
        )

    return canonical


def _require_private_host_directory(
    path: Path,
    *,
    create: bool,
) -> None:
    if not isinstance(path, Path):
        raise TypeError(
            "Favorites USB private host directory must be pathlib.Path."
        )
    if not path.is_absolute():
        raise ValueError(
            "Favorites USB private host directory must be absolute."
        )

    if create:
        try:
            path.mkdir(
                mode=0o700,
                parents=True,
                exist_ok=True,
            )
        except OSError as error:
            raise _FavoritesUsbWritePreparationError(
                path,
                f"Could not create the private host directory: {error}",
            ) from error

    try:
        observed = path.lstat()
    except OSError as error:
        raise _FavoritesUsbWritePreparationError(
            path,
            f"Could not inspect the private host directory: {error}",
        ) from error

    if stat.S_ISLNK(
        observed.st_mode
    ):
        raise _FavoritesUsbWritePreparationError(
            path,
            "Private Favorites USB host directory must not be a symbolic link.",
        )
    if not stat.S_ISDIR(
        observed.st_mode
    ):
        raise _FavoritesUsbWritePreparationError(
            path,
            "Private Favorites USB host path must be a directory.",
        )

    effective_uid = getattr(
        os,
        "geteuid",
        lambda: observed.st_uid,
    )()
    if observed.st_uid != effective_uid:
        raise _FavoritesUsbWritePreparationError(
            path,
            "Private Favorites USB host directory is not owned by the current user.",
        )

    try:
        resolved = path.resolve(
            strict=True
        )
    except (OSError, RuntimeError) as error:
        raise _FavoritesUsbWritePreparationError(
            path,
            f"Could not resolve the private host directory: {error}",
        ) from error

    if resolved != path:
        raise _FavoritesUsbWritePreparationError(
            path,
            "Private Favorites USB host directory must be canonical.",
        )

    try:
        os.chmod(
            path,
            0o700,
        )
    except OSError as error:
        raise _FavoritesUsbWritePreparationError(
            path,
            f"Could not secure the private host directory: {error}",
        ) from error


def _usb_host_operation_paths(
    preflight: FavoritesUsbWritePreflight,
    host_state_directory: Path,
) -> _FavoritesUsbHostOperationPaths:
    root = _canonical_host_state_candidate(
        host_state_directory,
        preflight,
    )
    target_lock_key = (
        _usb_target_lock_key(
            preflight
        )
    )
    operation_id = (
        _usb_operation_id(
            preflight
        )
    )
    locks = root / "locks"
    operations = root / "operations"
    operation = (
        operations
        / operation_id
    )

    return _FavoritesUsbHostOperationPaths(
        target_lock_key=target_lock_key,
        operation_id=operation_id,
        root_directory=root,
        locks_directory=locks,
        lock_directory=(
            locks
            / f"{target_lock_key}.lock"
        ),
        operations_directory=operations,
        operation_directory=operation,
        backup_directory=operation / "backup",
        staging_directory=operation / "staging",
        rollback_manifest_path=operation / "rollback.json",
        operation_report_path=operation / "report.json",
        failure_report_path=operation / "failure.json",
    )


@contextmanager
def _usb_host_operation_lock(
    preflight: FavoritesUsbWritePreflight,
    host_state_directory: Path,
) -> Iterator[_FavoritesUsbHostOperationPaths]:
    paths = _usb_host_operation_paths(
        preflight,
        host_state_directory,
    )

    _require_private_host_directory(
        paths.root_directory,
        create=True,
    )
    _require_private_host_directory(
        paths.locks_directory,
        create=True,
    )

    try:
        paths.lock_directory.mkdir(
            mode=0o700,
        )
    except FileExistsError as error:
        raise _FavoritesUsbWritePreparationError(
            paths.lock_directory,
            (
                "Another Favorites USB write operation for this exact "
                "mounted target may already be active."
            ),
        ) from error
    except OSError as error:
        raise _FavoritesUsbWritePreparationError(
            paths.lock_directory,
            f"Could not establish the Favorites USB host operation lock: {error}",
        ) from error

    try:
        os.chmod(
            paths.lock_directory,
            0o700,
        )
        locked = (
            paths.lock_directory.lstat()
        )
    except OSError as error:
        raise _FavoritesUsbWritePreparationError(
            paths.lock_directory,
            f"Could not inspect the Favorites USB host operation lock: {error}",
        ) from error

    if not stat.S_ISDIR(
        locked.st_mode
    ):
        raise _FavoritesUsbWritePreparationError(
            paths.lock_directory,
            "Favorites USB host operation lock is not a directory.",
        )

    body_error: BaseException | None = None

    try:
        yield paths
    except BaseException as error:
        body_error = error
        raise
    finally:
        cleanup_error: _FavoritesUsbWritePreparationError | None = None

        try:
            current = (
                paths.lock_directory.lstat()
            )
        except FileNotFoundError:
            cleanup_error = _FavoritesUsbWritePreparationError(
                paths.lock_directory,
                "Favorites USB host operation lock disappeared before release.",
            )
        except OSError as error:
            cleanup_error = _FavoritesUsbWritePreparationError(
                paths.lock_directory,
                f"Could not inspect the host operation lock before release: {error}",
            )
        else:
            if (
                current.st_dev,
                current.st_ino,
            ) != (
                locked.st_dev,
                locked.st_ino,
            ):
                cleanup_error = _FavoritesUsbWritePreparationError(
                    paths.lock_directory,
                    "Favorites USB host operation lock changed before release.",
                )
            elif not stat.S_ISDIR(
                current.st_mode
            ):
                cleanup_error = _FavoritesUsbWritePreparationError(
                    paths.lock_directory,
                    "Favorites USB host operation lock changed file type.",
                )
            else:
                try:
                    paths.lock_directory.rmdir()
                except OSError as error:
                    cleanup_error = _FavoritesUsbWritePreparationError(
                        paths.lock_directory,
                        f"Could not release the host operation lock: {error}",
                    )

        if (
            cleanup_error is not None
            and body_error is None
        ):
            raise cleanup_error


@dataclass(frozen=True, slots=True)
class _FavoritesUsbVerifiedBackup:
    directory: Path
    tree_evidence: FavoritesTreeEvidence
    snapshot: FavoritesStorageSnapshot

    def __post_init__(self) -> None:
        if not isinstance(
            self.directory,
            Path,
        ):
            raise TypeError(
                "Favorites USB verified backup directory must be pathlib.Path."
            )
        if not self.directory.is_absolute():
            raise ValueError(
                "Favorites USB verified backup directory must be absolute."
            )
        if not isinstance(
            self.tree_evidence,
            FavoritesTreeEvidence,
        ):
            raise TypeError(
                "Favorites USB verified backup tree evidence must be "
                "FavoritesTreeEvidence."
            )
        if not isinstance(
            self.snapshot,
            FavoritesStorageSnapshot,
        ):
            raise TypeError(
                "Favorites USB verified backup snapshot must be "
                "FavoritesStorageSnapshot."
            )


def _require_current_usb_preflight_target(
    preflight: FavoritesUsbWritePreflight,
) -> FavoritesTreeEvidence:
    if not isinstance(
        preflight,
        FavoritesUsbWritePreflight,
    ):
        raise TypeError(
            "Favorites USB target revalidation requires "
            "FavoritesUsbWritePreflight."
        )

    try:
        current = qualify_favorites_usb_storage_path(
            preflight.requested_path,
            preflight.mountinfo_path,
            sys_dev_block_directory=preflight.sys_dev_block_directory,
        )
    except FavoritesUsbStorageQualificationError as error:
        raise _FavoritesUsbWritePreparationError(
            error.path,
            (
                "Could not requalify the current USB Favorites target "
                f"({error.reason.value}): {error.message}"
            ),
        ) from error

    if current != preflight.qualification:
        raise _FavoritesUsbWritePreparationError(
            preflight.qualification.favorites_directory,
            "USB target qualification changed after preflight.",
        )

    if (
        current.snapshot
        != preflight.observed_snapshot
        or not preflight.plan.matches_baseline_snapshot(
            current.snapshot
        )
    ):
        raise _FavoritesUsbWritePreparationError(
            current.favorites_directory,
            "USB managed snapshot changed after preflight.",
        )

    try:
        evidence = favorites_tree_evidence(
            current.favorites_directory
        )
    except FavoritesTreeEvidenceError as error:
        raise _FavoritesUsbWritePreparationError(
            error.path,
            (
                "Could not capture current USB Favorites tree evidence: "
                f"{error.message}"
            ),
        ) from error

    if evidence != preflight.tree_evidence:
        raise _FavoritesUsbWritePreparationError(
            current.favorites_directory,
            "USB Favorites content or structure changed after preflight.",
        )

    return evidence


def _require_host_operation_paths_match_preflight(
    preflight: FavoritesUsbWritePreflight,
    paths: _FavoritesUsbHostOperationPaths,
) -> None:
    if not isinstance(
        paths,
        _FavoritesUsbHostOperationPaths,
    ):
        raise TypeError(
            "Favorites USB host backup paths must be "
            "_FavoritesUsbHostOperationPaths."
        )

    expected = _usb_host_operation_paths(
        preflight,
        paths.root_directory,
    )
    if paths != expected:
        raise _FavoritesUsbWritePreparationError(
            paths.operation_directory,
            "Favorites USB host operation paths do not match the preflight.",
        )


def _require_active_usb_host_lock(
    paths: _FavoritesUsbHostOperationPaths,
) -> None:
    try:
        observed = paths.lock_directory.lstat()
    except OSError as error:
        raise _FavoritesUsbWritePreparationError(
            paths.lock_directory,
            f"Could not inspect the required USB host operation lock: {error}",
        ) from error

    if not stat.S_ISDIR(
        observed.st_mode
    ):
        raise _FavoritesUsbWritePreparationError(
            paths.lock_directory,
            "Required Favorites USB host operation lock is not a directory.",
        )


def _create_verified_usb_host_backup(
    preflight: FavoritesUsbWritePreflight,
    paths: _FavoritesUsbHostOperationPaths,
) -> _FavoritesUsbVerifiedBackup:
    if not isinstance(
        preflight,
        FavoritesUsbWritePreflight,
    ):
        raise TypeError(
            "Favorites USB host backup requires FavoritesUsbWritePreflight."
        )

    _require_host_operation_paths_match_preflight(
        preflight,
        paths,
    )
    _require_active_usb_host_lock(
        paths
    )
    _require_current_usb_preflight_target(
        preflight
    )

    _require_private_host_directory(
        paths.operations_directory,
        create=True,
    )

    if os.path.lexists(
        paths.operation_directory
    ):
        raise _FavoritesUsbWritePreparationError(
            paths.operation_directory,
            "Favorites USB host operation workspace already exists.",
        )

    try:
        paths.operation_directory.mkdir(
            mode=0o700,
        )
    except OSError as error:
        raise _FavoritesUsbWritePreparationError(
            paths.operation_directory,
            f"Could not create the USB host operation workspace: {error}",
        ) from error

    _require_private_host_directory(
        paths.operation_directory,
        create=False,
    )

    if os.path.lexists(
        paths.backup_directory
    ):
        raise _FavoritesUsbWritePreparationError(
            paths.backup_directory,
            "Favorites USB verified host backup destination already exists.",
        )

    try:
        shutil.copytree(
            preflight.qualification.favorites_directory,
            paths.backup_directory,
            copy_function=shutil.copy2,
            symlinks=True,
        )
    except OSError as error:
        raise _FavoritesUsbWritePreparationError(
            paths.backup_directory,
            f"Could not create the complete USB host backup: {error}",
        ) from error

    _require_current_usb_preflight_target(
        preflight
    )

    try:
        backup_evidence = favorites_tree_evidence(
            paths.backup_directory
        )
    except FavoritesTreeEvidenceError as error:
        raise _FavoritesUsbWritePreparationError(
            error.path,
            (
                "USB host backup could not be verified safely: "
                f"{error.message}"
            ),
        ) from error

    if (
        backup_evidence.sha256
        != preflight.tree_evidence.sha256
    ):
        raise _FavoritesUsbWritePreparationError(
            paths.backup_directory,
            "USB host backup does not exactly match preflight tree evidence.",
        )

    try:
        backup_snapshot = FavoritesCopiedTreeStorageSource(
            paths.backup_directory
        ).read_snapshot()
    except FavoritesCopiedTreeStorageError as error:
        raise _FavoritesUsbWritePreparationError(
            error.path,
            (
                "USB host backup managed snapshot could not be verified: "
                f"{error.message}"
            ),
        ) from error

    if (
        backup_snapshot
        != preflight.observed_snapshot
        or not preflight.plan.matches_baseline_snapshot(
            backup_snapshot
        )
    ):
        raise _FavoritesUsbWritePreparationError(
            paths.backup_directory,
            "USB host backup managed snapshot differs from the preflight target.",
        )

    _require_current_usb_preflight_target(
        preflight
    )

    return _FavoritesUsbVerifiedBackup(
        directory=paths.backup_directory,
        tree_evidence=backup_evidence,
        snapshot=backup_snapshot,
    )


@dataclass(frozen=True, slots=True)
class _FavoritesUsbPreparedStage:
    directory: Path
    snapshot: FavoritesStorageSnapshot
    tree_evidence: FavoritesTreeEvidence

    def __post_init__(self) -> None:
        if not isinstance(
            self.directory,
            Path,
        ):
            raise TypeError(
                "Favorites USB prepared staging directory must be pathlib.Path."
            )
        if not self.directory.is_absolute():
            raise ValueError(
                "Favorites USB prepared staging directory must be absolute."
            )
        if not isinstance(
            self.snapshot,
            FavoritesStorageSnapshot,
        ):
            raise TypeError(
                "Favorites USB prepared staging snapshot must be "
                "FavoritesStorageSnapshot."
            )
        if not isinstance(
            self.tree_evidence,
            FavoritesTreeEvidence,
        ):
            raise TypeError(
                "Favorites USB prepared staging tree evidence must be "
                "FavoritesTreeEvidence."
            )


def _require_verified_usb_host_backup_current(
    preflight: FavoritesUsbWritePreflight,
    paths: _FavoritesUsbHostOperationPaths,
    backup: _FavoritesUsbVerifiedBackup,
) -> FavoritesTreeEvidence:
    if not isinstance(
        backup,
        _FavoritesUsbVerifiedBackup,
    ):
        raise TypeError(
            "Favorites USB host staging requires _FavoritesUsbVerifiedBackup."
        )

    _require_host_operation_paths_match_preflight(
        preflight,
        paths,
    )

    if backup.directory != paths.backup_directory:
        raise _FavoritesUsbWritePreparationError(
            backup.directory,
            "Verified USB host backup path does not match the operation workspace.",
        )

    try:
        evidence = favorites_tree_evidence(
            backup.directory
        )
    except FavoritesTreeEvidenceError as error:
        raise _FavoritesUsbWritePreparationError(
            error.path,
            (
                "Verified USB host backup could not be revalidated safely: "
                f"{error.message}"
            ),
        ) from error

    if (
        evidence != backup.tree_evidence
        or evidence.sha256
        != preflight.tree_evidence.sha256
    ):
        raise _FavoritesUsbWritePreparationError(
            backup.directory,
            "Verified USB host backup changed after verification.",
        )

    try:
        snapshot = FavoritesCopiedTreeStorageSource(
            backup.directory
        ).read_snapshot()
    except FavoritesCopiedTreeStorageError as error:
        raise _FavoritesUsbWritePreparationError(
            error.path,
            (
                "Verified USB host backup managed snapshot could not be "
                f"revalidated: {error.message}"
            ),
        ) from error

    if (
        snapshot != backup.snapshot
        or snapshot != preflight.observed_snapshot
        or not preflight.plan.matches_baseline_snapshot(
            snapshot
        )
    ):
        raise _FavoritesUsbWritePreparationError(
            backup.directory,
            "Verified USB host backup managed snapshot changed.",
        )

    return evidence


def _write_usb_host_staged_regular_file(
    path: Path,
    content: bytes,
) -> None:
    if not isinstance(
        path,
        Path,
    ):
        raise TypeError(
            "Favorites USB staged write path must be pathlib.Path."
        )
    if not isinstance(
        content,
        bytes,
    ):
        raise TypeError(
            "Favorites USB staged write content must be bytes."
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
        raise _FavoritesUsbWritePreparationError(
            path,
            f"Could not inspect staged Favorites file: {error}",
        ) from error
    else:
        if stat.S_ISLNK(
            observed.st_mode
        ):
            raise _FavoritesUsbWritePreparationError(
                path,
                "Staged Favorites file must not be a symbolic link.",
            )
        if not stat.S_ISREG(
            observed.st_mode
        ):
            raise _FavoritesUsbWritePreparationError(
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
        raise _FavoritesUsbWritePreparationError(
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
        raise _FavoritesUsbWritePreparationError(
            path,
            f"Could not write staged Favorites file: {error}",
        ) from error
    finally:
        os.close(descriptor)


def _create_verified_usb_host_staging(
    preflight: FavoritesUsbWritePreflight,
    paths: _FavoritesUsbHostOperationPaths,
    backup: _FavoritesUsbVerifiedBackup,
) -> _FavoritesUsbPreparedStage:
    if not isinstance(
        preflight,
        FavoritesUsbWritePreflight,
    ):
        raise TypeError(
            "Favorites USB host staging requires FavoritesUsbWritePreflight."
        )

    _require_host_operation_paths_match_preflight(
        preflight,
        paths,
    )
    _require_active_usb_host_lock(
        paths
    )
    _require_private_host_directory(
        paths.operation_directory,
        create=False,
    )
    _require_verified_usb_host_backup_current(
        preflight,
        paths,
        backup,
    )

    if os.path.lexists(
        paths.staging_directory
    ):
        raise _FavoritesUsbWritePreparationError(
            paths.staging_directory,
            "Verified USB host staging destination already exists.",
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
        raise _FavoritesUsbWritePreparationError(
            paths.staging_directory,
            (
                "USB host staging can write only immediate lowercase-.hpd "
                "Favorites documents; unsupported intended filename: "
                f"{unsupported_names[0]!r}."
            ),
        )

    if len(set(intended_names)) != len(
        intended_names
    ):
        raise _FavoritesUsbWritePreparationError(
            paths.staging_directory,
            "USB host staging requires unique intended HPD filenames.",
        )

    try:
        shutil.copytree(
            backup.directory,
            paths.staging_directory,
            copy_function=shutil.copy2,
            symlinks=True,
        )
    except OSError as error:
        raise _FavoritesUsbWritePreparationError(
            paths.staging_directory,
            f"Could not create the complete USB host staging tree: {error}",
        ) from error

    _require_verified_usb_host_backup_current(
        preflight,
        paths,
        backup,
    )

    try:
        with os.scandir(
            paths.staging_directory
        ) as handle:
            entries = tuple(handle)
    except OSError as error:
        raise _FavoritesUsbWritePreparationError(
            paths.staging_directory,
            f"Could not scan USB host staging directory: {error}",
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
            raise _FavoritesUsbWritePreparationError(
                path,
                f"Could not inspect staged HPD path: {error}",
            ) from error

        if not stat.S_ISREG(
            observed.st_mode
        ):
            if entry.name in intended_name_set:
                raise _FavoritesUsbWritePreparationError(
                    path,
                    "Intended HPD filename collides with non-regular staged material.",
                )
            continue

        if entry.name not in intended_name_set:
            try:
                path.unlink()
            except OSError as error:
                raise _FavoritesUsbWritePreparationError(
                    path,
                    f"Could not remove obsolete staged HPD file: {error}",
                ) from error

    _write_usb_host_staged_regular_file(
        paths.staging_directory
        / "f_list.cfg",
        intended.catalog_bytes,
    )

    for document in intended.documents:
        _write_usb_host_staged_regular_file(
            paths.staging_directory
            / document.filename,
            document.content,
        )

    try:
        staged_snapshot = FavoritesCopiedTreeStorageSource(
            paths.staging_directory
        ).read_snapshot()
    except FavoritesCopiedTreeStorageError as error:
        raise _FavoritesUsbWritePreparationError(
            error.path,
            (
                "USB host staged managed snapshot could not be read back: "
                f"{error.message}"
            ),
        ) from error

    if staged_snapshot != intended:
        raise _FavoritesUsbWritePreparationError(
            paths.staging_directory,
            "USB host staged managed snapshot differs from the exact intended snapshot.",
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
        raise _FavoritesUsbWritePreparationError(
            paths.staging_directory,
            "USB host staged workspace differs from the write-plan intended workspace.",
        )

    if (
        staged_validation
        != preflight.plan.intended_validation
    ):
        raise _FavoritesUsbWritePreparationError(
            paths.staging_directory,
            "USB host staged schema evidence differs from the write-plan intended validation.",
        )

    try:
        staging_evidence = favorites_tree_evidence(
            paths.staging_directory
        )
    except FavoritesTreeEvidenceError as error:
        raise _FavoritesUsbWritePreparationError(
            error.path,
            (
                "USB host staging could not be verified safely: "
                f"{error.message}"
            ),
        ) from error

    _require_verified_usb_host_backup_current(
        preflight,
        paths,
        backup,
    )

    return _FavoritesUsbPreparedStage(
        directory=paths.staging_directory,
        snapshot=staged_snapshot,
        tree_evidence=staging_evidence,
    )


@dataclass(frozen=True, slots=True)
class _FavoritesUsbPreactivationEvidence:
    active_tree_evidence: FavoritesTreeEvidence
    backup_tree_evidence: FavoritesTreeEvidence
    staging_tree_evidence: FavoritesTreeEvidence

    def __post_init__(self) -> None:
        for label, value in (
            ("active tree evidence", self.active_tree_evidence),
            ("backup tree evidence", self.backup_tree_evidence),
            ("staging tree evidence", self.staging_tree_evidence),
        ):
            if not isinstance(
                value,
                FavoritesTreeEvidence,
            ):
                raise TypeError(
                    f"Favorites USB preactivation {label} must be "
                    "FavoritesTreeEvidence."
                )


def _require_verified_usb_host_staging_current(
    preflight: FavoritesUsbWritePreflight,
    paths: _FavoritesUsbHostOperationPaths,
    prepared: _FavoritesUsbPreparedStage,
) -> FavoritesTreeEvidence:
    if not isinstance(
        prepared,
        _FavoritesUsbPreparedStage,
    ):
        raise TypeError(
            "Favorites USB preactivation requires _FavoritesUsbPreparedStage."
        )

    _require_host_operation_paths_match_preflight(
        preflight,
        paths,
    )

    if prepared.directory != paths.staging_directory:
        raise _FavoritesUsbWritePreparationError(
            prepared.directory,
            "Verified USB host staging path does not match the operation workspace.",
        )

    try:
        evidence = favorites_tree_evidence(
            prepared.directory
        )
    except FavoritesTreeEvidenceError as error:
        raise _FavoritesUsbWritePreparationError(
            error.path,
            (
                "Verified USB host staging is no longer safe: "
                f"{error.message}"
            ),
        ) from error

    if evidence != prepared.tree_evidence:
        raise _FavoritesUsbWritePreparationError(
            prepared.directory,
            "Verified USB host staging content or structure changed.",
        )

    try:
        snapshot = FavoritesCopiedTreeStorageSource(
            prepared.directory
        ).read_snapshot()
    except FavoritesCopiedTreeStorageError as error:
        raise _FavoritesUsbWritePreparationError(
            error.path,
            (
                "Verified USB host staging can no longer be read: "
                f"{error.message}"
            ),
        ) from error

    if (
        snapshot != prepared.snapshot
        or snapshot != preflight.plan.intended_snapshot
    ):
        raise _FavoritesUsbWritePreparationError(
            prepared.directory,
            "Verified USB host staging no longer matches the exact intended snapshot.",
        )

    workspace = project_favorites_storage_snapshot(
        snapshot
    )
    validation = validate_favorites_workspace(
        workspace
    )

    if (
        workspace
        != preflight.plan.intended_workspace
    ):
        raise _FavoritesUsbWritePreparationError(
            prepared.directory,
            "Verified USB host staging workspace no longer matches the intended plan.",
        )

    if (
        validation
        != preflight.plan.intended_validation
    ):
        raise _FavoritesUsbWritePreparationError(
            prepared.directory,
            "Verified USB host staging schema evidence no longer matches the intended plan.",
        )

    return evidence


def _require_usb_preactivation_ready(
    preflight: FavoritesUsbWritePreflight,
    paths: _FavoritesUsbHostOperationPaths,
    backup: _FavoritesUsbVerifiedBackup,
    prepared: _FavoritesUsbPreparedStage,
) -> _FavoritesUsbPreactivationEvidence:
    if not isinstance(
        preflight,
        FavoritesUsbWritePreflight,
    ):
        raise TypeError(
            "Favorites USB preactivation requires FavoritesUsbWritePreflight."
        )

    _require_host_operation_paths_match_preflight(
        preflight,
        paths,
    )
    _require_active_usb_host_lock(
        paths
    )
    _require_private_host_directory(
        paths.operation_directory,
        create=False,
    )

    backup_evidence = (
        _require_verified_usb_host_backup_current(
            preflight,
            paths,
            backup,
        )
    )
    staging_evidence = (
        _require_verified_usb_host_staging_current(
            preflight,
            paths,
            prepared,
        )
    )

    # Keep the active USB requalification and complete-tree read as the final
    # preactivation boundary after all host-side recovery/staging evidence has
    # been revalidated.
    active_evidence = (
        _require_current_usb_preflight_target(
            preflight
        )
    )

    return _FavoritesUsbPreactivationEvidence(
        active_tree_evidence=active_evidence,
        backup_tree_evidence=backup_evidence,
        staging_tree_evidence=staging_evidence,
    )


@dataclass(frozen=True, slots=True)
class _FavoritesUsbManagedActivationPlan:
    document_writes: tuple[str, ...]
    write_catalog: bool
    document_deletions: tuple[str, ...]

    def __post_init__(self) -> None:
        for label, names in (
            ("document writes", self.document_writes),
            ("document deletions", self.document_deletions),
        ):
            if type(names) is not tuple:
                raise TypeError(
                    f"Favorites USB managed activation {label} must be a tuple."
                )
            if any(
                not isinstance(name, str)
                or not name.endswith(".hpd")
                or "/" in name
                or "\\" in name
                or "\x00" in name
                for name in names
            ):
                raise ValueError(
                    f"Favorites USB managed activation {label} must contain "
                    "safe immediate lowercase-.hpd filenames."
                )
            if len(set(names)) != len(names):
                raise ValueError(
                    f"Favorites USB managed activation {label} must be unique."
                )

        if type(self.write_catalog) is not bool:
            raise TypeError(
                "Favorites USB managed activation catalog flag must be bool."
            )

        overlap = (
            set(self.document_writes)
            & set(self.document_deletions)
        )
        if overlap:
            raise ValueError(
                "Favorites USB managed activation must not write and delete "
                "the same HPD filename."
            )

    @property
    def is_noop(self) -> bool:
        return (
            not self.document_writes
            and not self.write_catalog
            and not self.document_deletions
        )


def _usb_managed_activation_plan(
    preflight: FavoritesUsbWritePreflight,
) -> _FavoritesUsbManagedActivationPlan:
    if not isinstance(
        preflight,
        FavoritesUsbWritePreflight,
    ):
        raise TypeError(
            "Favorites USB managed activation planning requires "
            "FavoritesUsbWritePreflight."
        )

    baseline = preflight.plan.baseline_snapshot
    intended = preflight.plan.intended_snapshot

    baseline_documents = {
        document.filename: document.content
        for document in baseline.documents
    }
    intended_documents = {
        document.filename: document.content
        for document in intended.documents
    }

    if len(baseline_documents) != len(
        baseline.documents
    ):
        raise _FavoritesUsbWritePreparationError(
            preflight.qualification.favorites_directory,
            "USB managed activation baseline HPD filenames are not unique.",
        )
    if len(intended_documents) != len(
        intended.documents
    ):
        raise _FavoritesUsbWritePreparationError(
            preflight.qualification.favorites_directory,
            "USB managed activation intended HPD filenames are not unique.",
        )

    unsupported = tuple(
        document.filename
        for document in intended.documents
        if not document.filename.endswith(".hpd")
    )
    if unsupported:
        raise _FavoritesUsbWritePreparationError(
            preflight.qualification.favorites_directory,
            (
                "USB managed activation supports only immediate lowercase-.hpd "
                f"documents; unsupported intended filename: {unsupported[0]!r}."
            ),
        )

    # Activation order is intentionally represented as three disjoint phases:
    # 1. create/update intended HPDs while the baseline catalog is still active;
    # 2. replace f_list.cfg only after every intended HPD is present;
    # 3. remove obsolete baseline HPDs only after the intended catalog is active.
    #
    # This avoids a transient catalog reference to a not-yet-created HPD and
    # avoids deleting a baseline HPD while the baseline catalog can still refer
    # to it.
    document_writes = tuple(
        document.filename
        for document in intended.documents
        if baseline_documents.get(
            document.filename
        ) != document.content
    )
    document_deletions = tuple(
        document.filename
        for document in baseline.documents
        if document.filename
        not in intended_documents
    )

    return _FavoritesUsbManagedActivationPlan(
        document_writes=document_writes,
        write_catalog=(
            baseline.catalog_bytes
            != intended.catalog_bytes
        ),
        document_deletions=document_deletions,
    )


_USB_SUPPORTED_ACTIVATION_FILESYSTEMS = frozenset(
    {
        "vfat",
    }
)
_USB_MEDIA_TEMP_PREFIX = ".sds200-usb-write-"


class _FavoritesUsbMediaMutationError(RuntimeError):
    def __init__(
        self,
        path: Path,
        message: str,
        *,
        mutation_started: bool,
    ) -> None:
        if not isinstance(
            path,
            Path,
        ):
            raise TypeError(
                "Favorites USB media-mutation error path must be pathlib.Path."
            )
        if not isinstance(
            message,
            str,
        ):
            raise TypeError(
                "Favorites USB media-mutation error message must be str."
            )
        if type(mutation_started) is not bool:
            raise TypeError(
                "Favorites USB media-mutation started flag must be bool."
            )

        self.path = path
        self.message = message
        self.mutation_started = mutation_started

        super().__init__(
            f"Favorites USB media mutation failed at {path}: {message} "
            f"(mutation_started={str(mutation_started).lower()})"
        )


def _require_usb_activation_filesystem(
    preflight: FavoritesUsbWritePreflight,
) -> str:
    if not isinstance(
        preflight,
        FavoritesUsbWritePreflight,
    ):
        raise TypeError(
            "Favorites USB activation filesystem check requires "
            "FavoritesUsbWritePreflight."
        )

    filesystem_type = (
        preflight.qualification.mount.filesystem_type
    )
    if (
        filesystem_type
        not in _USB_SUPPORTED_ACTIVATION_FILESYSTEMS
    ):
        raise _FavoritesUsbWritePreparationError(
            preflight.qualification.mount_directory,
            (
                "Favorites USB activation does not support mounted filesystem "
                f"type {filesystem_type!r}; supported types: "
                f"{sorted(_USB_SUPPORTED_ACTIVATION_FILESYSTEMS)!r}."
            ),
        )

    return filesystem_type


def _usb_media_temporary_path(
    preflight: FavoritesUsbWritePreflight,
    paths: _FavoritesUsbHostOperationPaths,
) -> Path:
    _require_host_operation_paths_match_preflight(
        preflight,
        paths,
    )

    return (
        preflight.qualification.favorites_directory
        / (
            f"{_USB_MEDIA_TEMP_PREFIX}"
            f"{paths.operation_id[:16]}.tmp"
        )
    )


def _require_usb_managed_activation_filename(
    filename: str,
) -> str:
    if not isinstance(
        filename,
        str,
    ):
        raise TypeError(
            "Favorites USB managed activation filename must be str."
        )

    if filename == "f_list.cfg":
        return filename

    if (
        not filename.endswith(".hpd")
        or not filename
        or filename in {".", ".."}
        or "/" in filename
        or "\\" in filename
        or "\x00" in filename
    ):
        raise ValueError(
            "Favorites USB managed activation filename must be f_list.cfg "
            "or one safe immediate lowercase-.hpd filename."
        )

    return filename


def _read_usb_activation_regular_file(
    path: Path,
) -> bytes:
    if not isinstance(
        path,
        Path,
    ):
        raise TypeError(
            "Favorites USB activation read path must be pathlib.Path."
        )

    try:
        initial = path.lstat()
    except OSError as error:
        raise _FavoritesUsbWritePreparationError(
            path,
            f"Could not inspect USB activation file: {error}",
        ) from error

    if stat.S_ISLNK(
        initial.st_mode
    ):
        raise _FavoritesUsbWritePreparationError(
            path,
            "USB activation file must not be a symbolic link.",
        )
    if not stat.S_ISREG(
        initial.st_mode
    ):
        raise _FavoritesUsbWritePreparationError(
            path,
            "USB activation file must be a regular file.",
        )

    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )

    try:
        descriptor = os.open(
            path,
            flags,
        )
    except OSError as error:
        raise _FavoritesUsbWritePreparationError(
            path,
            f"Could not open USB activation file: {error}",
        ) from error

    try:
        opened = os.fstat(
            descriptor
        )
        if not stat.S_ISREG(
            opened.st_mode
        ):
            raise _FavoritesUsbWritePreparationError(
                path,
                "USB activation file changed to a non-regular file.",
            )
        if (
            opened.st_dev,
            opened.st_ino,
        ) != (
            initial.st_dev,
            initial.st_ino,
        ):
            raise _FavoritesUsbWritePreparationError(
                path,
                "USB activation file changed while being opened.",
            )

        try:
            with os.fdopen(
                descriptor,
                "rb",
                closefd=False,
            ) as handle:
                content = handle.read()
        except OSError as error:
            raise _FavoritesUsbWritePreparationError(
                path,
                f"Could not read USB activation file: {error}",
            ) from error

        final = os.fstat(
            descriptor
        )
        if (
            final.st_dev,
            final.st_ino,
            final.st_size,
            final.st_mtime_ns,
        ) != (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
        ):
            raise _FavoritesUsbWritePreparationError(
                path,
                "USB activation file changed while being read.",
            )

        return content
    finally:
        os.close(
            descriptor
        )


def _replace_usb_active_managed_file(
    preflight: FavoritesUsbWritePreflight,
    paths: _FavoritesUsbHostOperationPaths,
    filename: str,
    content: bytes,
) -> None:
    if not isinstance(
        preflight,
        FavoritesUsbWritePreflight,
    ):
        raise TypeError(
            "Favorites USB active-file replacement requires "
            "FavoritesUsbWritePreflight."
        )
    if not isinstance(
        content,
        bytes,
    ):
        raise TypeError(
            "Favorites USB active-file replacement content must be bytes."
        )

    filename = (
        _require_usb_managed_activation_filename(
            filename
        )
    )
    _require_host_operation_paths_match_preflight(
        preflight,
        paths,
    )
    _require_active_usb_host_lock(
        paths
    )
    _require_usb_activation_filesystem(
        preflight
    )

    root = (
        preflight.qualification.favorites_directory
    )
    target = root / filename
    temporary = _usb_media_temporary_path(
        preflight,
        paths,
    )

    if os.path.lexists(
        temporary
    ):
        raise _FavoritesUsbMediaMutationError(
            temporary,
            "USB activation temporary artifact already exists.",
            mutation_started=False,
        )

    try:
        observed = target.lstat()
    except FileNotFoundError:
        mode = 0o600
    except OSError as error:
        raise _FavoritesUsbMediaMutationError(
            target,
            f"Could not inspect active managed file: {error}",
            mutation_started=False,
        ) from error
    else:
        if stat.S_ISLNK(
            observed.st_mode
        ):
            raise _FavoritesUsbMediaMutationError(
                target,
                "Active managed file must not be a symbolic link.",
                mutation_started=False,
            )
        if not stat.S_ISREG(
            observed.st_mode
        ):
            raise _FavoritesUsbMediaMutationError(
                target,
                "Active managed file must be a regular file.",
                mutation_started=False,
            )
        mode = observed.st_mode & 0o7777

    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )

    descriptor: int | None = None
    temporary_identity: tuple[int, int] | None = None
    mutation_started = False

    try:
        try:
            descriptor = os.open(
                temporary,
                flags,
                mode,
            )
        except OSError as error:
            raise _FavoritesUsbMediaMutationError(
                temporary,
                f"Could not create USB activation temporary file: {error}",
                mutation_started=False,
            ) from error

        opened = os.fstat(
            descriptor
        )
        if not stat.S_ISREG(
            opened.st_mode
        ):
            raise _FavoritesUsbMediaMutationError(
                temporary,
                "USB activation temporary path is not a regular file.",
                mutation_started=False,
            )
        temporary_identity = (
            opened.st_dev,
            opened.st_ino,
        )

        try:
            with os.fdopen(
                descriptor,
                "wb",
                closefd=False,
            ) as handle:
                handle.write(
                    content
                )
                handle.flush()
                os.fsync(
                    handle.fileno()
                )
        except OSError as error:
            raise _FavoritesUsbMediaMutationError(
                temporary,
                f"Could not write USB activation temporary file: {error}",
                mutation_started=False,
            ) from error
        finally:
            os.close(
                descriptor
            )
            descriptor = None

        try:
            temporary_content = (
                _read_usb_activation_regular_file(
                    temporary
                )
            )
        except _FavoritesUsbWritePreparationError as error:
            raise _FavoritesUsbMediaMutationError(
                error.path,
                error.message,
                mutation_started=False,
            ) from error

        if temporary_content != content:
            raise _FavoritesUsbMediaMutationError(
                temporary,
                "USB activation temporary file failed exact readback.",
                mutation_started=False,
            )

        try:
            os.replace(
                temporary,
                target,
            )
        except OSError as error:
            raise _FavoritesUsbMediaMutationError(
                target,
                f"Could not replace active managed file: {error}",
                mutation_started=False,
            ) from error

        mutation_started = True

        try:
            active_content = (
                _read_usb_activation_regular_file(
                    target
                )
            )
        except _FavoritesUsbWritePreparationError as error:
            raise _FavoritesUsbMediaMutationError(
                error.path,
                error.message,
                mutation_started=True,
            ) from error

        if active_content != content:
            raise _FavoritesUsbMediaMutationError(
                target,
                "Replaced active managed file failed exact readback.",
                mutation_started=True,
            )
    finally:
        if descriptor is not None:
            os.close(
                descriptor
            )

        if (
            not mutation_started
            and temporary_identity is not None
        ):
            try:
                current = temporary.lstat()
            except FileNotFoundError:
                pass
            except OSError:
                pass
            else:
                if (
                    stat.S_ISREG(
                        current.st_mode
                    )
                    and (
                        current.st_dev,
                        current.st_ino,
                    )
                    == temporary_identity
                ):
                    with suppress(OSError):
                        temporary.unlink()

__all__ = [
    "FavoritesUsbWritePreflight",
    "FavoritesUsbWritePreflightError",
    "FavoritesUsbWritePreflightReason",
    "preflight_favorites_usb_write",
]
