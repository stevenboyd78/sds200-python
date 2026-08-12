"""Pre-mutation safety contract for verified USB Favorites writes."""

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
from typing import cast

from .favorites_schema import validate_favorites_workspace
from .favorites_storage import (
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
    project_favorites_storage_snapshot,
)
from .favorites_storage_evidence import (
    FavoritesTreeEvidence,
    FavoritesTreeEvidenceError,
    _favorites_unmanaged_tree_sha256,
    favorites_storage_snapshot_sha256,
    favorites_tree_evidence,
    favorites_unmanaged_tree_sha256,
)
from .favorites_storage_local import (
    FavoritesCopiedTreeStorageError,
    FavoritesCopiedTreeStorageSource,
)
from .favorites_storage_usb import (
    DEFAULT_LINUX_MOUNTINFO_PATH,
    DEFAULT_LINUX_SYS_DEV_BLOCK_DIRECTORY,
    FAVORITES_USB_STORAGE_RELATIVE_DIRECTORY,
    FavoritesUsbStorageQualification,
    FavoritesUsbStorageQualificationError,
    FavoritesUsbStorageQualificationReason,
    LinuxBlockDeviceError,
    LinuxBlockDeviceEvidence,
    LinuxMountInfoEntry,
    LinuxMountInfoError,
    qualify_favorites_usb_storage_path,
    read_linux_block_device_evidence,
    read_linux_mountinfo,
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


def _validate_unmanaged_sha256(
    value: str,
    *,
    label: str,
) -> None:
    if not isinstance(
        value,
        str,
    ):
        raise TypeError(
            f"{label} must be str."
        )

    if (
        len(value) != 64
        or value != value.lower()
        or any(
            character
            not in "0123456789abcdef"
            for character in value
        )
    ):
        raise ValueError(
            f"{label} must be one canonical lowercase SHA-256 hex digest."
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
    unmanaged_sha256: str

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
        _validate_unmanaged_sha256(
            self.unmanaged_sha256,
            label="Favorites USB write unmanaged tree SHA-256",
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
        initial_unmanaged_sha256 = favorites_unmanaged_tree_sha256(
            initial.favorites_directory
        )
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
        final_unmanaged_sha256 = favorites_unmanaged_tree_sha256(
            current.favorites_directory
        )
        final_tree = favorites_tree_evidence(
            current.favorites_directory
        )
    except FavoritesTreeEvidenceError as error:
        raise FavoritesUsbWritePreflightError(
            FavoritesUsbWritePreflightReason.UNSAFE_TREE,
            error.path,
            error.message,
        ) from error

    # Preserve the established complete-tree stale failure when both evidence
    # forms change during preflight.
    if final_tree != initial_tree:
        raise FavoritesUsbWritePreflightError(
            FavoritesUsbWritePreflightReason.TARGET_STALE,
            current.favorites_directory,
            (
                "USB Favorites complete-tree identity changed while "
                "preflight evidence was captured."
            ),
        )

    if final_unmanaged_sha256 != initial_unmanaged_sha256:
        raise FavoritesUsbWritePreflightError(
            FavoritesUsbWritePreflightReason.TARGET_STALE,
            current.favorites_directory,
            (
                "USB Favorites unmanaged content or structure changed while "
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
        unmanaged_sha256=final_unmanaged_sha256,
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


_USB_ROLLBACK_MANIFEST_SCHEMA = "sds200.favorites-usb.rollback"
_USB_ROLLBACK_MANIFEST_VERSION = 1
_USB_ROLLBACK_MANIFEST_MAX_BYTES = 64 * 1024


class _FavoritesUsbRollbackPhase(StrEnum):
    PREPARED = "prepared"
    MUTATION_STARTED = "mutation_started"
    RECOVERY_REQUIRED = "recovery_required"
    RECOVERY_IN_PROGRESS = "recovery_in_progress"
    RECOVERED = "recovered"
    RECOVERY_INCOMPLETE = "recovery_incomplete"
    COMPLETED = "completed"


_USB_ROLLBACK_PHASE_STATE: dict[
    _FavoritesUsbRollbackPhase,
    tuple[bool, bool, bool, bool],
] = {
    _FavoritesUsbRollbackPhase.PREPARED: (
        False,
        False,
        False,
        False,
    ),
    _FavoritesUsbRollbackPhase.MUTATION_STARTED: (
        True,
        False,
        False,
        False,
    ),
    _FavoritesUsbRollbackPhase.RECOVERY_REQUIRED: (
        True,
        True,
        False,
        False,
    ),
    _FavoritesUsbRollbackPhase.RECOVERY_IN_PROGRESS: (
        True,
        True,
        True,
        False,
    ),
    _FavoritesUsbRollbackPhase.RECOVERED: (
        True,
        False,
        True,
        True,
    ),
    _FavoritesUsbRollbackPhase.RECOVERY_INCOMPLETE: (
        True,
        True,
        True,
        False,
    ),
    _FavoritesUsbRollbackPhase.COMPLETED: (
        True,
        False,
        False,
        False,
    ),
}


_USB_ROLLBACK_ALLOWED_TRANSITIONS = frozenset(
    {
        (
            _FavoritesUsbRollbackPhase.PREPARED,
            _FavoritesUsbRollbackPhase.MUTATION_STARTED,
        ),
        (
            _FavoritesUsbRollbackPhase.MUTATION_STARTED,
            _FavoritesUsbRollbackPhase.COMPLETED,
        ),
        (
            _FavoritesUsbRollbackPhase.MUTATION_STARTED,
            _FavoritesUsbRollbackPhase.RECOVERY_REQUIRED,
        ),
        (
            _FavoritesUsbRollbackPhase.RECOVERY_REQUIRED,
            _FavoritesUsbRollbackPhase.RECOVERY_IN_PROGRESS,
        ),
        (
            _FavoritesUsbRollbackPhase.RECOVERY_IN_PROGRESS,
            _FavoritesUsbRollbackPhase.RECOVERED,
        ),
        (
            _FavoritesUsbRollbackPhase.RECOVERY_IN_PROGRESS,
            _FavoritesUsbRollbackPhase.RECOVERY_INCOMPLETE,
        ),
    }
)


def _require_usb_rollback_absolute_path(
    value: Path,
    *,
    label: str,
) -> None:
    if not isinstance(
        value,
        Path,
    ):
        raise TypeError(
            f"{label} must be pathlib.Path."
        )
    if not value.is_absolute():
        raise ValueError(
            f"{label} must be absolute."
        )


@dataclass(frozen=True, slots=True)
class _FavoritesUsbRollbackManifest:
    revision: int
    operation_id: str
    target_lock_key: str
    mount_directory: Path
    favorites_directory: Path
    mount_id: int
    parent_id: int
    device_major: int
    device_minor: int
    filesystem_type: str
    mount_source: str
    block_device_name: str
    block_sysfs_path: Path
    usb_ancestor_path: Path | None
    removable: bool | None
    backup_directory: Path
    staging_directory: Path
    baseline_snapshot_sha256: str
    intended_snapshot_sha256: str
    baseline_tree_sha256: str
    phase: _FavoritesUsbRollbackPhase
    bounded_artifact_present: bool

    def __post_init__(self) -> None:
        if (
            type(self.revision) is not int
            or self.revision <= 0
        ):
            raise ValueError(
                "Favorites USB rollback manifest revision must be a "
                "positive integer."
            )

        for digest_label, digest_value in (
            (
                "Favorites USB rollback operation ID",
                self.operation_id,
            ),
            (
                "Favorites USB rollback target lock key",
                self.target_lock_key,
            ),
            (
                "Favorites USB rollback baseline snapshot SHA-256",
                self.baseline_snapshot_sha256,
            ),
            (
                "Favorites USB rollback intended snapshot SHA-256",
                self.intended_snapshot_sha256,
            ),
            (
                "Favorites USB rollback baseline tree SHA-256",
                self.baseline_tree_sha256,
            ),
        ):
            _validate_unmanaged_sha256(
                digest_value,
                label=digest_label,
            )

        for path_label, path_value in (
            (
                "Favorites USB rollback mount directory",
                self.mount_directory,
            ),
            (
                "Favorites USB rollback Favorites directory",
                self.favorites_directory,
            ),
            (
                "Favorites USB rollback block sysfs path",
                self.block_sysfs_path,
            ),
            (
                "Favorites USB rollback backup directory",
                self.backup_directory,
            ),
            (
                "Favorites USB rollback staging directory",
                self.staging_directory,
            ),
        ):
            _require_usb_rollback_absolute_path(
                path_value,
                label=path_label,
            )

        if self.usb_ancestor_path is not None:
            _require_usb_rollback_absolute_path(
                self.usb_ancestor_path,
                label=(
                    "Favorites USB rollback USB ancestor path"
                ),
            )

        for mount_label, mount_value in (
            (
                "Favorites USB rollback mount ID",
                self.mount_id,
            ),
            (
                "Favorites USB rollback parent mount ID",
                self.parent_id,
            ),
        ):
            if (
                type(mount_value) is not int
                or mount_value <= 0
            ):
                raise ValueError(
                    f"{mount_label} must be a positive integer."
                )

        for device_label, device_value in (
            (
                "Favorites USB rollback device major",
                self.device_major,
            ),
            (
                "Favorites USB rollback device minor",
                self.device_minor,
            ),
        ):
            if (
                type(device_value) is not int
                or device_value < 0
            ):
                raise ValueError(
                    f"{device_label} must be a non-negative integer."
                )

        for text_label, text_value in (
            (
                "Favorites USB rollback filesystem type",
                self.filesystem_type,
            ),
            (
                "Favorites USB rollback mount source",
                self.mount_source,
            ),
            (
                "Favorites USB rollback block device name",
                self.block_device_name,
            ),
        ):
            if (
                not isinstance(
                    text_value,
                    str,
                )
                or not text_value
            ):
                raise ValueError(
                    f"{text_label} must be a non-empty string."
                )

        if (
            self.removable is not None
            and type(self.removable) is not bool
        ):
            raise TypeError(
                "Favorites USB rollback removable evidence must be bool "
                "or None."
            )
        if not isinstance(
            self.phase,
            _FavoritesUsbRollbackPhase,
        ):
            raise TypeError(
                "Favorites USB rollback phase must be "
                "_FavoritesUsbRollbackPhase."
            )
        if type(
            self.bounded_artifact_present
        ) is not bool:
            raise TypeError(
                "Favorites USB rollback bounded-artifact flag must be bool."
            )

    @property
    def media_mutation_started(
        self,
    ) -> bool:
        return _USB_ROLLBACK_PHASE_STATE[
            self.phase
        ][0]

    @property
    def recovery_required(
        self,
    ) -> bool:
        return _USB_ROLLBACK_PHASE_STATE[
            self.phase
        ][1]

    @property
    def recovery_attempted(
        self,
    ) -> bool:
        return _USB_ROLLBACK_PHASE_STATE[
            self.phase
        ][2]

    @property
    def recovery_completed(
        self,
    ) -> bool:
        return _USB_ROLLBACK_PHASE_STATE[
            self.phase
        ][3]

    @property
    def backup_retained(
        self,
    ) -> bool:
        return True

    def as_dict(
        self,
    ) -> dict[str, object]:
        return {
            "schema": _USB_ROLLBACK_MANIFEST_SCHEMA,
            "version": _USB_ROLLBACK_MANIFEST_VERSION,
            "revision": self.revision,
            "operation_id": self.operation_id,
            "target_lock_key": self.target_lock_key,
            "target": {
                "mount_directory": str(
                    self.mount_directory
                ),
                "favorites_directory": str(
                    self.favorites_directory
                ),
                "mount_id": self.mount_id,
                "parent_id": self.parent_id,
                "device_major": self.device_major,
                "device_minor": self.device_minor,
                "filesystem_type": self.filesystem_type,
                "mount_source": self.mount_source,
                "block_device_name": self.block_device_name,
                "block_sysfs_path": str(
                    self.block_sysfs_path
                ),
                "usb_ancestor_path": (
                    None
                    if self.usb_ancestor_path is None
                    else str(
                        self.usb_ancestor_path
                    )
                ),
                "removable": self.removable,
            },
            "host": {
                "backup_directory": str(
                    self.backup_directory
                ),
                "staging_directory": str(
                    self.staging_directory
                ),
            },
            "identity": {
                "baseline_snapshot_sha256":
                    self.baseline_snapshot_sha256,
                "intended_snapshot_sha256":
                    self.intended_snapshot_sha256,
                "baseline_tree_sha256":
                    self.baseline_tree_sha256,
            },
            "phase": self.phase.value,
            "media_mutation_started":
                self.media_mutation_started,
            "recovery_required":
                self.recovery_required,
            "recovery_attempted":
                self.recovery_attempted,
            "recovery_completed":
                self.recovery_completed,
            "backup_retained":
                self.backup_retained,
            "bounded_artifact_present":
                self.bounded_artifact_present,
        }


def _usb_rollback_manifest(
    preflight: FavoritesUsbWritePreflight,
    paths: _FavoritesUsbHostOperationPaths,
    *,
    revision: int,
    phase: _FavoritesUsbRollbackPhase,
    bounded_artifact_present: bool,
) -> _FavoritesUsbRollbackManifest:
    if not isinstance(
        preflight,
        FavoritesUsbWritePreflight,
    ):
        raise TypeError(
            "Favorites USB rollback manifest construction requires "
            "FavoritesUsbWritePreflight."
        )

    _require_host_operation_paths_match_preflight(
        preflight,
        paths,
    )

    mount = (
        preflight.qualification.mount
    )
    block = (
        preflight.qualification.block_device
    )

    return _FavoritesUsbRollbackManifest(
        revision=revision,
        operation_id=paths.operation_id,
        target_lock_key=paths.target_lock_key,
        mount_directory=(
            preflight.qualification.mount_directory
        ),
        favorites_directory=(
            preflight.qualification.favorites_directory
        ),
        mount_id=mount.mount_id,
        parent_id=mount.parent_id,
        device_major=mount.device_major,
        device_minor=mount.device_minor,
        filesystem_type=mount.filesystem_type,
        mount_source=mount.mount_source,
        block_device_name=block.device_name,
        block_sysfs_path=block.sysfs_path,
        usb_ancestor_path=block.usb_ancestor_path,
        removable=block.removable,
        backup_directory=paths.backup_directory,
        staging_directory=paths.staging_directory,
        baseline_snapshot_sha256=(
            favorites_storage_snapshot_sha256(
                preflight.plan.baseline_snapshot
            )
        ),
        intended_snapshot_sha256=(
            favorites_storage_snapshot_sha256(
                preflight.plan.intended_snapshot
            )
        ),
        baseline_tree_sha256=(
            preflight.tree_evidence.sha256
        ),
        phase=phase,
        bounded_artifact_present=(
            bounded_artifact_present
        ),
    )


def _require_usb_rollback_manifest_matches_preflight(
    preflight: FavoritesUsbWritePreflight,
    paths: _FavoritesUsbHostOperationPaths,
    manifest: _FavoritesUsbRollbackManifest,
) -> None:
    if not isinstance(
        manifest,
        _FavoritesUsbRollbackManifest,
    ):
        raise TypeError(
            "Favorites USB rollback manifest verification requires "
            "_FavoritesUsbRollbackManifest."
        )

    expected = _usb_rollback_manifest(
        preflight,
        paths,
        revision=manifest.revision,
        phase=manifest.phase,
        bounded_artifact_present=(
            manifest.bounded_artifact_present
        ),
    )

    if manifest != expected:
        raise _FavoritesUsbWritePreparationError(
            paths.rollback_manifest_path,
            "Favorites USB rollback manifest identity/evidence does not "
            "match this exact preflight operation.",
        )


def _require_usb_rollback_manifest_transition(
    current: _FavoritesUsbRollbackManifest | None,
    proposed: _FavoritesUsbRollbackManifest,
    *,
    path: Path,
) -> None:
    if current is not None and not isinstance(
        current,
        _FavoritesUsbRollbackManifest,
    ):
        raise TypeError(
            "Current Favorites USB rollback manifest must be "
            "_FavoritesUsbRollbackManifest or None."
        )
    if not isinstance(
        proposed,
        _FavoritesUsbRollbackManifest,
    ):
        raise TypeError(
            "Proposed Favorites USB rollback manifest must be "
            "_FavoritesUsbRollbackManifest."
        )
    if not isinstance(
        path,
        Path,
    ):
        raise TypeError(
            "Favorites USB rollback transition path must be pathlib.Path."
        )

    if current is None:
        if (
            proposed.revision != 1
            or proposed.phase
            is not _FavoritesUsbRollbackPhase.PREPARED
        ):
            raise _FavoritesUsbWritePreparationError(
                path,
                "Initial Favorites USB rollback manifest must be PREPARED "
                "at revision 1.",
            )
        return

    if (
        proposed.operation_id
        != current.operation_id
        or proposed.target_lock_key
        != current.target_lock_key
        or proposed.mount_directory
        != current.mount_directory
        or proposed.favorites_directory
        != current.favorites_directory
        or proposed.mount_id
        != current.mount_id
        or proposed.parent_id
        != current.parent_id
        or proposed.device_major
        != current.device_major
        or proposed.device_minor
        != current.device_minor
        or proposed.filesystem_type
        != current.filesystem_type
        or proposed.mount_source
        != current.mount_source
        or proposed.block_device_name
        != current.block_device_name
        or proposed.block_sysfs_path
        != current.block_sysfs_path
        or proposed.usb_ancestor_path
        != current.usb_ancestor_path
        or proposed.removable
        != current.removable
        or proposed.backup_directory
        != current.backup_directory
        or proposed.staging_directory
        != current.staging_directory
        or proposed.baseline_snapshot_sha256
        != current.baseline_snapshot_sha256
        or proposed.intended_snapshot_sha256
        != current.intended_snapshot_sha256
        or proposed.baseline_tree_sha256
        != current.baseline_tree_sha256
    ):
        raise _FavoritesUsbWritePreparationError(
            path,
            "Favorites USB rollback manifest update cannot change durable "
            "operation identity/evidence.",
        )

    if proposed.revision != current.revision + 1:
        raise _FavoritesUsbWritePreparationError(
            path,
            "Favorites USB rollback manifest revision must advance by "
            "exactly one.",
        )

    if (
        current.phase,
        proposed.phase,
    ) not in _USB_ROLLBACK_ALLOWED_TRANSITIONS:
        raise _FavoritesUsbWritePreparationError(
            path,
            "Favorites USB rollback manifest phase transition is not "
            f"allowed: {current.phase.value!r} -> {proposed.phase.value!r}.",
        )


def _usb_rollback_require_object(
    value: object,
    *,
    label: str,
) -> dict[str, object]:
    if not isinstance(
        value,
        dict,
    ):
        raise ValueError(
            f"{label} must be a JSON object."
        )

    if any(
        not isinstance(
            key,
            str,
        )
        for key in value
    ):
        raise ValueError(
            f"{label} keys must be strings."
        )

    return cast(
        dict[str, object],
        value,
    )


def _usb_rollback_require_exact_keys(
    value: dict[str, object],
    expected: frozenset[str],
    *,
    label: str,
) -> None:
    observed = frozenset(
        value
    )
    if observed != expected:
        missing = sorted(
            expected - observed
        )
        extra = sorted(
            observed - expected
        )
        raise ValueError(
            f"{label} has unsupported fields; "
            f"missing={missing!r}, extra={extra!r}."
        )


def _usb_rollback_require_string(
    value: object,
    *,
    label: str,
) -> str:
    if (
        not isinstance(
            value,
            str,
        )
        or not value
    ):
        raise ValueError(
            f"{label} must be a non-empty string."
        )
    return value


def _usb_rollback_require_integer(
    value: object,
    *,
    label: str,
    positive: bool,
) -> int:
    if type(value) is not int:
        raise ValueError(
            f"{label} must be an integer."
        )
    checked = value
    if (
        positive
        and checked <= 0
    ):
        raise ValueError(
            f"{label} must be positive."
        )
    if (
        not positive
        and checked < 0
    ):
        raise ValueError(
            f"{label} must be non-negative."
        )
    return checked


def _usb_rollback_require_boolean(
    value: object,
    *,
    label: str,
) -> bool:
    if type(value) is not bool:
        raise ValueError(
            f"{label} must be boolean."
        )
    return value


def _usb_rollback_reject_duplicate_object(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(
                "Favorites USB rollback JSON contains duplicate "
                f"object key {key!r}."
            )
        result[key] = value
    return result


def _usb_rollback_manifest_from_payload(
    payload: object,
) -> _FavoritesUsbRollbackManifest:
    root = _usb_rollback_require_object(
        payload,
        label="Favorites USB rollback manifest",
    )
    _usb_rollback_require_exact_keys(
        root,
        frozenset(
            {
                "schema",
                "version",
                "revision",
                "operation_id",
                "target_lock_key",
                "target",
                "host",
                "identity",
                "phase",
                "media_mutation_started",
                "recovery_required",
                "recovery_attempted",
                "recovery_completed",
                "backup_retained",
                "bounded_artifact_present",
            }
        ),
        label="Favorites USB rollback manifest",
    )

    if (
        root["schema"]
        != _USB_ROLLBACK_MANIFEST_SCHEMA
    ):
        raise ValueError(
            "Favorites USB rollback manifest schema is unsupported."
        )
    if (
        type(root["version"]) is not int
        or root["version"]
        != _USB_ROLLBACK_MANIFEST_VERSION
    ):
        raise ValueError(
            "Favorites USB rollback manifest version is unsupported."
        )

    target = _usb_rollback_require_object(
        root["target"],
        label="Favorites USB rollback target",
    )
    _usb_rollback_require_exact_keys(
        target,
        frozenset(
            {
                "mount_directory",
                "favorites_directory",
                "mount_id",
                "parent_id",
                "device_major",
                "device_minor",
                "filesystem_type",
                "mount_source",
                "block_device_name",
                "block_sysfs_path",
                "usb_ancestor_path",
                "removable",
            }
        ),
        label="Favorites USB rollback target",
    )

    host = _usb_rollback_require_object(
        root["host"],
        label="Favorites USB rollback host",
    )
    _usb_rollback_require_exact_keys(
        host,
        frozenset(
            {
                "backup_directory",
                "staging_directory",
            }
        ),
        label="Favorites USB rollback host",
    )

    identity = _usb_rollback_require_object(
        root["identity"],
        label="Favorites USB rollback identity",
    )
    _usb_rollback_require_exact_keys(
        identity,
        frozenset(
            {
                "baseline_snapshot_sha256",
                "intended_snapshot_sha256",
                "baseline_tree_sha256",
            }
        ),
        label="Favorites USB rollback identity",
    )

    phase_text = _usb_rollback_require_string(
        root["phase"],
        label="Favorites USB rollback phase",
    )
    try:
        phase = _FavoritesUsbRollbackPhase(
            phase_text
        )
    except ValueError as error:
        raise ValueError(
            "Favorites USB rollback manifest phase is unsupported."
        ) from error

    removable_value = target[
        "removable"
    ]
    if (
        removable_value is not None
        and type(removable_value) is not bool
    ):
        raise ValueError(
            "Favorites USB rollback removable evidence must be boolean "
            "or null."
        )

    usb_ancestor_value = target[
        "usb_ancestor_path"
    ]
    if (
        usb_ancestor_value is not None
        and not isinstance(
            usb_ancestor_value,
            str,
        )
    ):
        raise ValueError(
            "Favorites USB rollback USB ancestor path must be string "
            "or null."
        )

    manifest = _FavoritesUsbRollbackManifest(
        revision=_usb_rollback_require_integer(
            root["revision"],
            label="Favorites USB rollback revision",
            positive=True,
        ),
        operation_id=_usb_rollback_require_string(
            root["operation_id"],
            label="Favorites USB rollback operation ID",
        ),
        target_lock_key=_usb_rollback_require_string(
            root["target_lock_key"],
            label="Favorites USB rollback target lock key",
        ),
        mount_directory=Path(
            _usb_rollback_require_string(
                target["mount_directory"],
                label=(
                    "Favorites USB rollback mount directory"
                ),
            )
        ),
        favorites_directory=Path(
            _usb_rollback_require_string(
                target["favorites_directory"],
                label=(
                    "Favorites USB rollback Favorites directory"
                ),
            )
        ),
        mount_id=_usb_rollback_require_integer(
            target["mount_id"],
            label="Favorites USB rollback mount ID",
            positive=True,
        ),
        parent_id=_usb_rollback_require_integer(
            target["parent_id"],
            label=(
                "Favorites USB rollback parent mount ID"
            ),
            positive=True,
        ),
        device_major=_usb_rollback_require_integer(
            target["device_major"],
            label=(
                "Favorites USB rollback device major"
            ),
            positive=False,
        ),
        device_minor=_usb_rollback_require_integer(
            target["device_minor"],
            label=(
                "Favorites USB rollback device minor"
            ),
            positive=False,
        ),
        filesystem_type=_usb_rollback_require_string(
            target["filesystem_type"],
            label=(
                "Favorites USB rollback filesystem type"
            ),
        ),
        mount_source=_usb_rollback_require_string(
            target["mount_source"],
            label="Favorites USB rollback mount source",
        ),
        block_device_name=_usb_rollback_require_string(
            target["block_device_name"],
            label=(
                "Favorites USB rollback block device name"
            ),
        ),
        block_sysfs_path=Path(
            _usb_rollback_require_string(
                target["block_sysfs_path"],
                label=(
                    "Favorites USB rollback block sysfs path"
                ),
            )
        ),
        usb_ancestor_path=(
            None
            if usb_ancestor_value is None
            else Path(
                usb_ancestor_value
            )
        ),
        removable=removable_value,
        backup_directory=Path(
            _usb_rollback_require_string(
                host["backup_directory"],
                label=(
                    "Favorites USB rollback backup directory"
                ),
            )
        ),
        staging_directory=Path(
            _usb_rollback_require_string(
                host["staging_directory"],
                label=(
                    "Favorites USB rollback staging directory"
                ),
            )
        ),
        baseline_snapshot_sha256=(
            _usb_rollback_require_string(
                identity[
                    "baseline_snapshot_sha256"
                ],
                label=(
                    "Favorites USB rollback baseline snapshot SHA-256"
                ),
            )
        ),
        intended_snapshot_sha256=(
            _usb_rollback_require_string(
                identity[
                    "intended_snapshot_sha256"
                ],
                label=(
                    "Favorites USB rollback intended snapshot SHA-256"
                ),
            )
        ),
        baseline_tree_sha256=(
            _usb_rollback_require_string(
                identity[
                    "baseline_tree_sha256"
                ],
                label=(
                    "Favorites USB rollback baseline tree SHA-256"
                ),
            )
        ),
        phase=phase,
        bounded_artifact_present=(
            _usb_rollback_require_boolean(
                root[
                    "bounded_artifact_present"
                ],
                label=(
                    "Favorites USB rollback bounded-artifact flag"
                ),
            )
        ),
    )

    expected_flags = {
        "media_mutation_started":
            manifest.media_mutation_started,
        "recovery_required":
            manifest.recovery_required,
        "recovery_attempted":
            manifest.recovery_attempted,
        "recovery_completed":
            manifest.recovery_completed,
        "backup_retained":
            manifest.backup_retained,
    }

    for key, expected in expected_flags.items():
        observed = _usb_rollback_require_boolean(
            root[key],
            label=(
                "Favorites USB rollback "
                f"{key.replace('_', ' ')}"
            ),
        )
        if observed is not expected:
            raise ValueError(
                "Favorites USB rollback manifest lifecycle flags do not "
                f"match phase {manifest.phase.value!r}."
            )

    return manifest


def _usb_rollback_manifest_bytes(
    manifest: _FavoritesUsbRollbackManifest,
) -> bytes:
    if not isinstance(
        manifest,
        _FavoritesUsbRollbackManifest,
    ):
        raise TypeError(
            "Favorites USB rollback serialization requires "
            "_FavoritesUsbRollbackManifest."
        )

    return (
        json.dumps(
            manifest.as_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode(
        "utf-8"
    )


def _parse_usb_rollback_manifest_bytes(
    content: bytes,
) -> _FavoritesUsbRollbackManifest:
    if not isinstance(
        content,
        bytes,
    ):
        raise TypeError(
            "Favorites USB rollback manifest content must be bytes."
        )
    if (
        not content
        or len(content)
        > _USB_ROLLBACK_MANIFEST_MAX_BYTES
    ):
        raise ValueError(
            "Favorites USB rollback manifest size is invalid."
        )

    try:
        text = content.decode(
            "utf-8",
        )
    except UnicodeDecodeError as error:
        raise ValueError(
            "Favorites USB rollback manifest must be valid UTF-8."
        ) from error

    try:
        payload = json.loads(
            text,
            object_pairs_hook=(
                _usb_rollback_reject_duplicate_object
            ),
        )
    except (
        json.JSONDecodeError,
        ValueError,
    ) as error:
        raise ValueError(
            "Favorites USB rollback manifest is not valid strict JSON."
        ) from error

    manifest = (
        _usb_rollback_manifest_from_payload(
            payload
        )
    )

    if (
        _usb_rollback_manifest_bytes(
            manifest
        )
        != content
    ):
        raise ValueError(
            "Favorites USB rollback manifest is not in canonical form."
        )

    return manifest


def _read_usb_host_durable_regular_file(
    path: Path,
) -> bytes:
    if not isinstance(
        path,
        Path,
    ):
        raise TypeError(
            "Favorites USB durable host read path must be pathlib.Path."
        )
    if not path.is_absolute():
        raise ValueError(
            "Favorites USB durable host read path must be absolute."
        )

    no_follow = getattr(
        os,
        "O_NOFOLLOW",
        None,
    )
    if (
        no_follow is None
        or no_follow == 0
    ):
        raise _FavoritesUsbWritePreparationError(
            path,
            "Durable Favorites USB host records require O_NOFOLLOW support.",
        )

    try:
        initial = path.lstat()
    except OSError as error:
        raise _FavoritesUsbWritePreparationError(
            path,
            f"Could not inspect durable Favorites USB host record: {error}",
        ) from error

    if stat.S_ISLNK(
        initial.st_mode
    ):
        raise _FavoritesUsbWritePreparationError(
            path,
            "Durable Favorites USB host record must not be a symbolic link.",
        )
    if not stat.S_ISREG(
        initial.st_mode
    ):
        raise _FavoritesUsbWritePreparationError(
            path,
            "Durable Favorites USB host record must be a regular file.",
        )
    if (
        initial.st_size <= 0
        or initial.st_size
        > _USB_ROLLBACK_MANIFEST_MAX_BYTES
    ):
        raise _FavoritesUsbWritePreparationError(
            path,
            "Durable Favorites USB host record size is invalid.",
        )

    flags = (
        os.O_RDONLY
        | getattr(
            os,
            "O_BINARY",
            0,
        )
        | no_follow
    )

    try:
        descriptor = os.open(
            path,
            flags,
        )
    except OSError as error:
        raise _FavoritesUsbWritePreparationError(
            path,
            f"Could not open durable Favorites USB host record: {error}",
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
                "Durable Favorites USB host record changed to a "
                "non-regular file while opening.",
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
                "Durable Favorites USB host record changed while opening.",
            )
        if (
            opened.st_size <= 0
            or opened.st_size
            > _USB_ROLLBACK_MANIFEST_MAX_BYTES
        ):
            raise _FavoritesUsbWritePreparationError(
                path,
                "Durable Favorites USB host record size changed outside "
                "the allowed bound.",
            )

        chunks: list[bytes] = []
        remaining = (
            _USB_ROLLBACK_MANIFEST_MAX_BYTES
            + 1
        )
        while remaining > 0:
            try:
                chunk = os.read(
                    descriptor,
                    min(
                        8192,
                        remaining,
                    ),
                )
            except OSError as error:
                raise _FavoritesUsbWritePreparationError(
                    path,
                    "Could not read durable Favorites USB host record: "
                    f"{error}",
                ) from error
            if not chunk:
                break
            chunks.append(
                chunk
            )
            remaining -= len(
                chunk
            )

        content = b"".join(
            chunks
        )
        if len(
            content
        ) > _USB_ROLLBACK_MANIFEST_MAX_BYTES:
            raise _FavoritesUsbWritePreparationError(
                path,
                "Durable Favorites USB host record exceeds the maximum "
                "allowed size.",
            )

        final = os.fstat(
            descriptor
        )
        if (
            final.st_dev,
            final.st_ino,
            final.st_size,
            final.st_mtime_ns,
            final.st_mode,
        ) != (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_mode,
        ):
            raise _FavoritesUsbWritePreparationError(
                path,
                "Durable Favorites USB host record changed while reading.",
            )

        return content
    finally:
        os.close(
            descriptor
        )


def _read_usb_rollback_manifest(
    path: Path,
) -> _FavoritesUsbRollbackManifest:
    try:
        content = (
            _read_usb_host_durable_regular_file(
                path
            )
        )
        return (
            _parse_usb_rollback_manifest_bytes(
                content
            )
        )
    except _FavoritesUsbWritePreparationError:
        raise
    except (
        TypeError,
        ValueError,
    ) as error:
        raise _FavoritesUsbWritePreparationError(
            path,
            f"Could not parse durable Favorites USB rollback manifest: {error}",
        ) from error


def _usb_rollback_manifest_temporary_path(
    paths: _FavoritesUsbHostOperationPaths,
) -> Path:
    if not isinstance(
        paths,
        _FavoritesUsbHostOperationPaths,
    ):
        raise TypeError(
            "Favorites USB rollback temporary path requires "
            "_FavoritesUsbHostOperationPaths."
        )

    return (
        paths.operation_directory
        / ".rollback.json.tmp"
    )


def _fsync_usb_host_directory(
    path: Path,
) -> None:
    if not isinstance(
        path,
        Path,
    ):
        raise TypeError(
            "Favorites USB host directory synchronization requires "
            "pathlib.Path."
        )

    directory_flag = getattr(
        os,
        "O_DIRECTORY",
        None,
    )
    no_follow = getattr(
        os,
        "O_NOFOLLOW",
        None,
    )
    if (
        directory_flag is None
        or directory_flag == 0
        or no_follow is None
        or no_follow == 0
    ):
        raise _FavoritesUsbWritePreparationError(
            path,
            "Durable Favorites USB host records require O_DIRECTORY and "
            "O_NOFOLLOW support.",
        )

    flags = (
        os.O_RDONLY
        | directory_flag
        | no_follow
    )
    try:
        descriptor = os.open(
            path,
            flags,
        )
    except OSError as error:
        raise _FavoritesUsbWritePreparationError(
            path,
            f"Could not open durable-record directory for synchronization: "
            f"{error}",
        ) from error

    try:
        observed = os.fstat(
            descriptor
        )
        if not stat.S_ISDIR(
            observed.st_mode
        ):
            raise _FavoritesUsbWritePreparationError(
                path,
                "Durable-record synchronization target is not a directory.",
            )
        try:
            os.fsync(
                descriptor
            )
        except OSError as error:
            raise _FavoritesUsbWritePreparationError(
                path,
                "Could not synchronize durable Favorites USB host directory: "
                f"{error}",
            ) from error
    finally:
        os.close(
            descriptor
        )


def _write_usb_rollback_manifest(
    preflight: FavoritesUsbWritePreflight,
    paths: _FavoritesUsbHostOperationPaths,
    manifest: _FavoritesUsbRollbackManifest,
) -> None:
    if not isinstance(
        preflight,
        FavoritesUsbWritePreflight,
    ):
        raise TypeError(
            "Favorites USB rollback writer requires "
            "FavoritesUsbWritePreflight."
        )
    if not isinstance(
        manifest,
        _FavoritesUsbRollbackManifest,
    ):
        raise TypeError(
            "Favorites USB rollback writer requires "
            "_FavoritesUsbRollbackManifest."
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
    _require_usb_rollback_manifest_matches_preflight(
        preflight,
        paths,
        manifest,
    )

    manifest_path = (
        paths.rollback_manifest_path
    )
    temporary = (
        _usb_rollback_manifest_temporary_path(
            paths
        )
    )

    current: _FavoritesUsbRollbackManifest | None
    try:
        manifest_path.lstat()
    except FileNotFoundError:
        current = None
    except OSError as error:
        raise _FavoritesUsbWritePreparationError(
            manifest_path,
            f"Could not inspect current Favorites USB rollback manifest: "
            f"{error}",
        ) from error
    else:
        current = (
            _read_usb_rollback_manifest(
                manifest_path
            )
        )
        _require_usb_rollback_manifest_matches_preflight(
            preflight,
            paths,
            current,
        )

    _require_usb_rollback_manifest_transition(
        current,
        manifest,
        path=manifest_path,
    )

    if os.path.lexists(
        temporary
    ):
        raise _FavoritesUsbWritePreparationError(
            temporary,
            "Favorites USB rollback manifest temporary path already exists.",
        )

    no_follow = getattr(
        os,
        "O_NOFOLLOW",
        None,
    )
    if (
        no_follow is None
        or no_follow == 0
    ):
        raise _FavoritesUsbWritePreparationError(
            temporary,
            "Durable Favorites USB rollback writes require O_NOFOLLOW "
            "support.",
        )

    content = (
        _usb_rollback_manifest_bytes(
            manifest
        )
    )
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(
            os,
            "O_BINARY",
            0,
        )
        | no_follow
    )

    descriptor: int | None = None
    temporary_identity: tuple[int, int] | None = None
    published = False

    try:
        try:
            descriptor = os.open(
                temporary,
                flags,
                0o600,
            )
        except OSError as error:
            raise _FavoritesUsbWritePreparationError(
                temporary,
                "Could not exclusively create Favorites USB rollback "
                f"temporary file: {error}",
            ) from error

        opened = os.fstat(
            descriptor
        )
        if not stat.S_ISREG(
            opened.st_mode
        ):
            raise _FavoritesUsbWritePreparationError(
                temporary,
                "Favorites USB rollback temporary path is not a regular file.",
            )

        temporary_identity = (
            opened.st_dev,
            opened.st_ino,
        )

        offset = 0
        while offset < len(
            content
        ):
            try:
                written = os.write(
                    descriptor,
                    content[offset:],
                )
            except OSError as error:
                raise _FavoritesUsbWritePreparationError(
                    temporary,
                    f"Could not write Favorites USB rollback temporary "
                    f"file: {error}",
                ) from error

            if written <= 0:
                raise _FavoritesUsbWritePreparationError(
                    temporary,
                    "Favorites USB rollback temporary write returned zero "
                    "bytes.",
                )
            offset += written

        try:
            os.fsync(
                descriptor
            )
        except OSError as error:
            raise _FavoritesUsbWritePreparationError(
                temporary,
                f"Could not synchronize Favorites USB rollback temporary "
                f"file: {error}",
            ) from error

        os.close(
            descriptor
        )
        descriptor = None

        temporary_content = (
            _read_usb_host_durable_regular_file(
                temporary
            )
        )
        if temporary_content != content:
            raise _FavoritesUsbWritePreparationError(
                temporary,
                "Favorites USB rollback temporary file failed exact "
                "readback.",
            )

        temporary_manifest = (
            _parse_usb_rollback_manifest_bytes(
                temporary_content
            )
        )
        if temporary_manifest != manifest:
            raise _FavoritesUsbWritePreparationError(
                temporary,
                "Favorites USB rollback temporary manifest failed exact "
                "schema readback.",
            )

        # Revalidate the currently published manifest immediately before
        # replacement so an unexpected host-side change is never silently
        # overwritten.
        if current is None:
            if os.path.lexists(
                manifest_path
            ):
                raise _FavoritesUsbWritePreparationError(
                    manifest_path,
                    "Favorites USB rollback manifest appeared before its "
                    "initial publish.",
                )
        else:
            observed_current = (
                _read_usb_rollback_manifest(
                    manifest_path
                )
            )
            if observed_current != current:
                raise _FavoritesUsbWritePreparationError(
                    manifest_path,
                    "Favorites USB rollback manifest changed before guarded "
                    "update publication.",
                )

        try:
            os.replace(
                temporary,
                manifest_path,
            )
        except OSError as error:
            raise _FavoritesUsbWritePreparationError(
                manifest_path,
                f"Could not atomically publish Favorites USB rollback "
                f"manifest: {error}",
            ) from error

        published = True

        _fsync_usb_host_directory(
            paths.operation_directory
        )

        final_manifest = (
            _read_usb_rollback_manifest(
                manifest_path
            )
        )
        if final_manifest != manifest:
            raise _FavoritesUsbWritePreparationError(
                manifest_path,
                "Published Favorites USB rollback manifest failed exact "
                "post-fsync readback.",
            )
    finally:
        if descriptor is not None:
            os.close(
                descriptor
            )

        if (
            not published
            and temporary_identity is not None
        ):
            try:
                observed = temporary.lstat()
            except (
                FileNotFoundError,
                OSError,
            ):
                pass
            else:
                if (
                    stat.S_ISREG(
                        observed.st_mode
                    )
                    and (
                        observed.st_dev,
                        observed.st_ino,
                    )
                    == temporary_identity
                ):
                    with suppress(
                        OSError
                    ):
                        temporary.unlink()


_USB_OPERATION_REPORT_SCHEMA = "sds200.favorites-usb.operation-report"
_USB_OPERATION_REPORT_VERSION = 1
_USB_OPERATION_REPORT_MAX_BYTES = _USB_ROLLBACK_MANIFEST_MAX_BYTES


class _FavoritesUsbVerificationOutcome(StrEnum):
    NOT_ATTEMPTED = "not_attempted"
    VERIFIED = "verified"
    FAILED = "failed"


class _FavoritesUsbActivationOutcome(StrEnum):
    NOT_STARTED = "not_started"
    COMPLETED = "completed"
    FAILED_BEFORE_MUTATION = "failed_before_mutation"
    FAILED_AFTER_MUTATION = "failed_after_mutation"


class _FavoritesUsbRecoveryOutcome(StrEnum):
    NOT_REQUIRED = "not_required"
    NOT_ATTEMPTED = "not_attempted"
    RECOVERED = "recovered"
    INCOMPLETE = "incomplete"


class _FavoritesUsbFailureCode(StrEnum):
    PREACTIVATION_FAILED = "preactivation_failed"
    ACTIVATION_FAILED_BEFORE_MUTATION = (
        "activation_failed_before_mutation"
    )
    ACTIVATION_FAILED_AFTER_MUTATION = (
        "activation_failed_after_mutation"
    )
    POSTACTIVATION_VERIFICATION_FAILED = (
        "postactivation_verification_failed"
    )
    RECOVERY_NOT_ATTEMPTED = "recovery_not_attempted"
    RECOVERY_INCOMPLETE = "recovery_incomplete"


@dataclass(frozen=True, slots=True)
class _FavoritesUsbOperationReport:
    rollback_manifest: _FavoritesUsbRollbackManifest
    backup_verification: _FavoritesUsbVerificationOutcome
    staging_verification: _FavoritesUsbVerificationOutcome
    preactivation_verification: _FavoritesUsbVerificationOutcome
    postactivation_verification: _FavoritesUsbVerificationOutcome
    unmanaged_preservation: _FavoritesUsbVerificationOutcome
    activation_outcome: _FavoritesUsbActivationOutcome
    recovery_outcome: _FavoritesUsbRecoveryOutcome
    active_snapshot_sha256: str | None
    failure_code: _FavoritesUsbFailureCode | None

    def __post_init__(self) -> None:
        if not isinstance(
            self.rollback_manifest,
            _FavoritesUsbRollbackManifest,
        ):
            raise TypeError(
                "Favorites USB operation report requires "
                "_FavoritesUsbRollbackManifest."
            )

        for verification_label, verification_value in (
            (
                "backup verification",
                self.backup_verification,
            ),
            (
                "staging verification",
                self.staging_verification,
            ),
            (
                "preactivation verification",
                self.preactivation_verification,
            ),
            (
                "postactivation verification",
                self.postactivation_verification,
            ),
            (
                "unmanaged preservation",
                self.unmanaged_preservation,
            ),
        ):
            if not isinstance(
                verification_value,
                _FavoritesUsbVerificationOutcome,
            ):
                raise TypeError(
                    "Favorites USB operation "
                    f"{verification_label} must be "
                    "_FavoritesUsbVerificationOutcome."
                )

        if not isinstance(
            self.activation_outcome,
            _FavoritesUsbActivationOutcome,
        ):
            raise TypeError(
                "Favorites USB operation activation outcome must be "
                "_FavoritesUsbActivationOutcome."
            )
        if not isinstance(
            self.recovery_outcome,
            _FavoritesUsbRecoveryOutcome,
        ):
            raise TypeError(
                "Favorites USB operation recovery outcome must be "
                "_FavoritesUsbRecoveryOutcome."
            )

        if self.active_snapshot_sha256 is not None:
            _validate_unmanaged_sha256(
                self.active_snapshot_sha256,
                label=(
                    "Favorites USB operation active snapshot SHA-256"
                ),
            )

        if (
            self.failure_code is not None
            and not isinstance(
                self.failure_code,
                _FavoritesUsbFailureCode,
            )
        ):
            raise TypeError(
                "Favorites USB operation failure code must be "
                "_FavoritesUsbFailureCode or None."
            )

        _require_usb_operation_report_consistent(
            self
        )

    @property
    def is_success(
        self,
    ) -> bool:
        return (
            self.rollback_manifest.phase
            is _FavoritesUsbRollbackPhase.COMPLETED
        )

    def as_dict(
        self,
    ) -> dict[str, object]:
        rollback = (
            self.rollback_manifest.as_dict()
        )
        identity = cast(
            dict[str, object],
            rollback["identity"],
        )

        return {
            "schema": _USB_OPERATION_REPORT_SCHEMA,
            "version": _USB_OPERATION_REPORT_VERSION,
            "operation_id":
                self.rollback_manifest.operation_id,
            "target_lock_key":
                self.rollback_manifest.target_lock_key,
            "target": rollback["target"],
            "host": rollback["host"],
            "identity": {
                "baseline_snapshot_sha256":
                    identity[
                        "baseline_snapshot_sha256"
                    ],
                "intended_snapshot_sha256":
                    identity[
                        "intended_snapshot_sha256"
                    ],
                "baseline_tree_sha256":
                    identity[
                        "baseline_tree_sha256"
                    ],
                "active_snapshot_sha256":
                    self.active_snapshot_sha256,
            },
            "verification": {
                "backup":
                    self.backup_verification.value,
                "staging":
                    self.staging_verification.value,
                "preactivation":
                    self.preactivation_verification.value,
                "postactivation":
                    self.postactivation_verification.value,
                "unmanaged_preserved":
                    self.unmanaged_preservation.value,
            },
            "activation_outcome":
                self.activation_outcome.value,
            "recovery_outcome":
                self.recovery_outcome.value,
            "rollback_revision":
                self.rollback_manifest.revision,
            "rollback_phase":
                self.rollback_manifest.phase.value,
            "bounded_artifact_present":
                self.rollback_manifest.bounded_artifact_present,
            "backup_retained":
                self.rollback_manifest.backup_retained,
            "failure_code": (
                None
                if self.failure_code is None
                else self.failure_code.value
            ),
        }


def _require_usb_operation_report_consistent(
    report: _FavoritesUsbOperationReport,
) -> None:
    if not isinstance(
        report,
        _FavoritesUsbOperationReport,
    ):
        raise TypeError(
            "Favorites USB operation consistency requires "
            "_FavoritesUsbOperationReport."
        )

    rollback = report.rollback_manifest
    phase = rollback.phase

    if (
        report.backup_verification
        is not _FavoritesUsbVerificationOutcome.VERIFIED
        or report.staging_verification
        is not _FavoritesUsbVerificationOutcome.VERIFIED
    ):
        raise ValueError(
            "Favorites USB operation report requires verified host backup "
            "and staging evidence."
        )

    if phase is _FavoritesUsbRollbackPhase.PREPARED:
        if report.activation_outcome not in {
            _FavoritesUsbActivationOutcome.NOT_STARTED,
            _FavoritesUsbActivationOutcome.FAILED_BEFORE_MUTATION,
        }:
            raise ValueError(
                "PREPARED rollback report requires activation not started "
                "or failed before mutation."
            )
        if (
            report.recovery_outcome
            is not _FavoritesUsbRecoveryOutcome.NOT_REQUIRED
        ):
            raise ValueError(
                "PREPARED rollback report cannot require or claim recovery."
            )
        if rollback.bounded_artifact_present:
            raise ValueError(
                "PREPARED rollback report cannot claim a media recovery "
                "artifact."
            )
        if report.active_snapshot_sha256 is not None:
            raise ValueError(
                "PREPARED rollback report must not claim a final active "
                "snapshot identity."
            )
        if (
            report.activation_outcome
            is _FavoritesUsbActivationOutcome.NOT_STARTED
        ):
            if (
                report.preactivation_verification
                is not _FavoritesUsbVerificationOutcome.FAILED
                or report.failure_code
                is not _FavoritesUsbFailureCode.PREACTIVATION_FAILED
            ):
                raise ValueError(
                    "Activation-not-started report requires failed "
                    "preactivation verification and PREACTIVATION_FAILED."
                )
        else:
            if (
                report.preactivation_verification
                is not _FavoritesUsbVerificationOutcome.VERIFIED
                or report.failure_code
                is not _FavoritesUsbFailureCode.ACTIVATION_FAILED_BEFORE_MUTATION
            ):
                raise ValueError(
                    "Before-mutation activation failure requires verified "
                    "preactivation and ACTIVATION_FAILED_BEFORE_MUTATION."
                )
        if (
            report.postactivation_verification
            is not _FavoritesUsbVerificationOutcome.NOT_ATTEMPTED
        ):
            raise ValueError(
                "No-mutation failure report cannot claim postactivation "
                "verification."
            )

    elif phase is _FavoritesUsbRollbackPhase.COMPLETED:
        if (
            report.activation_outcome
            is not _FavoritesUsbActivationOutcome.COMPLETED
            or report.recovery_outcome
            is not _FavoritesUsbRecoveryOutcome.NOT_REQUIRED
        ):
            raise ValueError(
                "COMPLETED rollback report requires completed activation "
                "with recovery not required."
            )
        if (
            report.preactivation_verification
            is not _FavoritesUsbVerificationOutcome.VERIFIED
            or report.postactivation_verification
            is not _FavoritesUsbVerificationOutcome.VERIFIED
            or report.unmanaged_preservation
            is not _FavoritesUsbVerificationOutcome.VERIFIED
        ):
            raise ValueError(
                "Completed Favorites USB operation requires verified "
                "preactivation, postactivation, and unmanaged preservation."
            )
        if (
            report.active_snapshot_sha256
            != rollback.intended_snapshot_sha256
        ):
            raise ValueError(
                "Completed Favorites USB operation active snapshot identity "
                "must equal the intended snapshot identity."
            )
        if report.failure_code is not None:
            raise ValueError(
                "Completed Favorites USB operation cannot carry a failure "
                "code."
            )
        if rollback.bounded_artifact_present:
            raise ValueError(
                "Completed Favorites USB operation cannot retain a bounded "
                "recovery artifact."
            )

    elif phase is _FavoritesUsbRollbackPhase.RECOVERY_REQUIRED:
        if (
            report.activation_outcome
            is not _FavoritesUsbActivationOutcome.FAILED_AFTER_MUTATION
            or report.recovery_outcome
            is not _FavoritesUsbRecoveryOutcome.NOT_ATTEMPTED
            or report.failure_code
            is not _FavoritesUsbFailureCode.RECOVERY_NOT_ATTEMPTED
        ):
            raise ValueError(
                "RECOVERY_REQUIRED report requires failed-after-mutation "
                "activation, recovery not attempted, and "
                "RECOVERY_NOT_ATTEMPTED."
            )
        if (
            report.preactivation_verification
            is not _FavoritesUsbVerificationOutcome.VERIFIED
        ):
            raise ValueError(
                "Post-mutation failure report requires verified "
                "preactivation."
            )

    elif phase is _FavoritesUsbRollbackPhase.RECOVERED:
        if (
            report.activation_outcome
            is not _FavoritesUsbActivationOutcome.FAILED_AFTER_MUTATION
            or report.recovery_outcome
            is not _FavoritesUsbRecoveryOutcome.RECOVERED
        ):
            raise ValueError(
                "RECOVERED rollback report requires failed-after-mutation "
                "activation followed by recovered rollback."
            )
        if report.failure_code not in {
            _FavoritesUsbFailureCode.ACTIVATION_FAILED_AFTER_MUTATION,
            _FavoritesUsbFailureCode.POSTACTIVATION_VERIFICATION_FAILED,
        }:
            raise ValueError(
                "Recovered operation report requires an activation or "
                "postactivation verification failure code."
            )
        if (
            report.preactivation_verification
            is not _FavoritesUsbVerificationOutcome.VERIFIED
            or report.unmanaged_preservation
            is not _FavoritesUsbVerificationOutcome.VERIFIED
        ):
            raise ValueError(
                "Recovered Favorites USB operation requires verified "
                "preactivation and unmanaged preservation."
            )
        if (
            report.active_snapshot_sha256
            != rollback.baseline_snapshot_sha256
        ):
            raise ValueError(
                "Recovered Favorites USB operation active snapshot identity "
                "must equal the baseline snapshot identity."
            )
        if rollback.bounded_artifact_present:
            raise ValueError(
                "Recovered Favorites USB operation cannot retain a bounded "
                "recovery artifact."
            )

    elif phase is _FavoritesUsbRollbackPhase.RECOVERY_INCOMPLETE:
        if (
            report.activation_outcome
            is not _FavoritesUsbActivationOutcome.FAILED_AFTER_MUTATION
            or report.recovery_outcome
            is not _FavoritesUsbRecoveryOutcome.INCOMPLETE
            or report.failure_code
            is not _FavoritesUsbFailureCode.RECOVERY_INCOMPLETE
        ):
            raise ValueError(
                "RECOVERY_INCOMPLETE report requires failed-after-mutation "
                "activation, incomplete recovery, and RECOVERY_INCOMPLETE."
            )
        if (
            report.preactivation_verification
            is not _FavoritesUsbVerificationOutcome.VERIFIED
        ):
            raise ValueError(
                "Incomplete recovery report requires verified preactivation."
            )

    else:
        raise ValueError(
            "Favorites USB operation report requires a reportable rollback "
            f"phase, not {phase.value!r}."
        )

    if (
        phase is not _FavoritesUsbRollbackPhase.COMPLETED
        and report.failure_code is None
    ):
        raise ValueError(
            "Failed Favorites USB operation report requires a predefined "
            "failure code."
        )


def _usb_operation_report(
    preflight: FavoritesUsbWritePreflight,
    paths: _FavoritesUsbHostOperationPaths,
    rollback_manifest: _FavoritesUsbRollbackManifest,
    *,
    backup_verification: _FavoritesUsbVerificationOutcome,
    staging_verification: _FavoritesUsbVerificationOutcome,
    preactivation_verification: _FavoritesUsbVerificationOutcome,
    postactivation_verification: _FavoritesUsbVerificationOutcome,
    unmanaged_preservation: _FavoritesUsbVerificationOutcome,
    activation_outcome: _FavoritesUsbActivationOutcome,
    recovery_outcome: _FavoritesUsbRecoveryOutcome,
    active_snapshot_sha256: str | None,
    failure_code: _FavoritesUsbFailureCode | None,
) -> _FavoritesUsbOperationReport:
    if not isinstance(
        preflight,
        FavoritesUsbWritePreflight,
    ):
        raise TypeError(
            "Favorites USB operation report construction requires "
            "FavoritesUsbWritePreflight."
        )

    _require_host_operation_paths_match_preflight(
        preflight,
        paths,
    )
    _require_usb_rollback_manifest_matches_preflight(
        preflight,
        paths,
        rollback_manifest,
    )

    return _FavoritesUsbOperationReport(
        rollback_manifest=rollback_manifest,
        backup_verification=backup_verification,
        staging_verification=staging_verification,
        preactivation_verification=preactivation_verification,
        postactivation_verification=postactivation_verification,
        unmanaged_preservation=unmanaged_preservation,
        activation_outcome=activation_outcome,
        recovery_outcome=recovery_outcome,
        active_snapshot_sha256=active_snapshot_sha256,
        failure_code=failure_code,
    )


def _usb_operation_report_rollback_manifest(
    report: _FavoritesUsbOperationReport,
) -> _FavoritesUsbRollbackManifest:
    if not isinstance(
        report,
        _FavoritesUsbOperationReport,
    ):
        raise TypeError(
            "Favorites USB operation report rollback extraction requires "
            "_FavoritesUsbOperationReport."
        )
    return report.rollback_manifest


def _require_usb_operation_report_matches(
    preflight: FavoritesUsbWritePreflight,
    paths: _FavoritesUsbHostOperationPaths,
    rollback_manifest: _FavoritesUsbRollbackManifest,
    report: _FavoritesUsbOperationReport,
) -> None:
    if not isinstance(
        report,
        _FavoritesUsbOperationReport,
    ):
        raise TypeError(
            "Favorites USB operation report correlation requires "
            "_FavoritesUsbOperationReport."
        )

    _require_host_operation_paths_match_preflight(
        preflight,
        paths,
    )
    _require_usb_rollback_manifest_matches_preflight(
        preflight,
        paths,
        rollback_manifest,
    )

    if (
        _usb_operation_report_rollback_manifest(
            report
        )
        != rollback_manifest
    ):
        raise _FavoritesUsbWritePreparationError(
            paths.operation_directory,
            "Favorites USB operation report does not correlate to the exact "
            "final rollback manifest.",
        )


def _usb_operation_report_require_enum(
    enum_type: type[
        _FavoritesUsbVerificationOutcome
        | _FavoritesUsbActivationOutcome
        | _FavoritesUsbRecoveryOutcome
        | _FavoritesUsbFailureCode
    ],
    value: object,
    *,
    label: str,
) -> (
    _FavoritesUsbVerificationOutcome
    | _FavoritesUsbActivationOutcome
    | _FavoritesUsbRecoveryOutcome
    | _FavoritesUsbFailureCode
):
    text = _usb_rollback_require_string(
        value,
        label=label,
    )
    try:
        return enum_type(
            text
        )
    except ValueError as error:
        raise ValueError(
            f"{label} is unsupported."
        ) from error


def _usb_operation_report_from_payload(
    payload: object,
) -> _FavoritesUsbOperationReport:
    root = _usb_rollback_require_object(
        payload,
        label="Favorites USB operation report",
    )
    _usb_rollback_require_exact_keys(
        root,
        frozenset(
            {
                "schema",
                "version",
                "operation_id",
                "target_lock_key",
                "target",
                "host",
                "identity",
                "verification",
                "activation_outcome",
                "recovery_outcome",
                "rollback_revision",
                "rollback_phase",
                "bounded_artifact_present",
                "backup_retained",
                "failure_code",
            }
        ),
        label="Favorites USB operation report",
    )

    if root["schema"] != _USB_OPERATION_REPORT_SCHEMA:
        raise ValueError(
            "Favorites USB operation report schema is unsupported."
        )
    if (
        type(root["version"]) is not int
        or root["version"]
        != _USB_OPERATION_REPORT_VERSION
    ):
        raise ValueError(
            "Favorites USB operation report version is unsupported."
        )

    identity = _usb_rollback_require_object(
        root["identity"],
        label="Favorites USB operation report identity",
    )
    _usb_rollback_require_exact_keys(
        identity,
        frozenset(
            {
                "baseline_snapshot_sha256",
                "intended_snapshot_sha256",
                "baseline_tree_sha256",
                "active_snapshot_sha256",
            }
        ),
        label="Favorites USB operation report identity",
    )

    verification = _usb_rollback_require_object(
        root["verification"],
        label="Favorites USB operation report verification",
    )
    _usb_rollback_require_exact_keys(
        verification,
        frozenset(
            {
                "backup",
                "staging",
                "preactivation",
                "postactivation",
                "unmanaged_preserved",
            }
        ),
        label="Favorites USB operation report verification",
    )

    rollback_phase_text = _usb_rollback_require_string(
        root["rollback_phase"],
        label="Favorites USB operation report rollback phase",
    )
    try:
        rollback_phase = _FavoritesUsbRollbackPhase(
            rollback_phase_text
        )
    except ValueError as error:
        raise ValueError(
            "Favorites USB operation report rollback phase is unsupported."
        ) from error

    rollback_state = (
        _USB_ROLLBACK_PHASE_STATE[
            rollback_phase
        ]
    )

    rollback_payload: dict[str, object] = {
        "schema": _USB_ROLLBACK_MANIFEST_SCHEMA,
        "version": _USB_ROLLBACK_MANIFEST_VERSION,
        "revision": root["rollback_revision"],
        "operation_id": root["operation_id"],
        "target_lock_key": root["target_lock_key"],
        "target": root["target"],
        "host": root["host"],
        "identity": {
            "baseline_snapshot_sha256":
                identity[
                    "baseline_snapshot_sha256"
                ],
            "intended_snapshot_sha256":
                identity[
                    "intended_snapshot_sha256"
                ],
            "baseline_tree_sha256":
                identity[
                    "baseline_tree_sha256"
                ],
        },
        "phase": rollback_phase.value,
        "media_mutation_started":
            rollback_state[0],
        "recovery_required":
            rollback_state[1],
        "recovery_attempted":
            rollback_state[2],
        "recovery_completed":
            rollback_state[3],
        "backup_retained":
            root["backup_retained"],
        "bounded_artifact_present":
            root["bounded_artifact_present"],
    }

    rollback_manifest = (
        _usb_rollback_manifest_from_payload(
            rollback_payload
        )
    )

    active_snapshot_value = identity[
        "active_snapshot_sha256"
    ]
    active_snapshot_sha256: str | None
    if active_snapshot_value is not None:
        active_snapshot_sha256 = (
            _usb_rollback_require_string(
                active_snapshot_value,
                label=(
                    "Favorites USB operation report active snapshot SHA-256"
                ),
            )
        )
    else:
        active_snapshot_sha256 = None

    failure_code_value = root[
        "failure_code"
    ]
    failure_code: _FavoritesUsbFailureCode | None
    if failure_code_value is None:
        failure_code = None
    else:
        parsed_failure = (
            _usb_operation_report_require_enum(
                _FavoritesUsbFailureCode,
                failure_code_value,
                label=(
                    "Favorites USB operation report failure code"
                ),
            )
        )
        if not isinstance(
            parsed_failure,
            _FavoritesUsbFailureCode,
        ):
            raise AssertionError(
                "USB failure-code parser returned wrong enum."
            )
        failure_code = parsed_failure

    parsed_backup = _usb_operation_report_require_enum(
        _FavoritesUsbVerificationOutcome,
        verification["backup"],
        label=(
            "Favorites USB operation report backup verification"
        ),
    )
    parsed_staging = _usb_operation_report_require_enum(
        _FavoritesUsbVerificationOutcome,
        verification["staging"],
        label=(
            "Favorites USB operation report staging verification"
        ),
    )
    parsed_preactivation = (
        _usb_operation_report_require_enum(
            _FavoritesUsbVerificationOutcome,
            verification["preactivation"],
            label=(
                "Favorites USB operation report preactivation verification"
            ),
        )
    )
    parsed_postactivation = (
        _usb_operation_report_require_enum(
            _FavoritesUsbVerificationOutcome,
            verification["postactivation"],
            label=(
                "Favorites USB operation report postactivation verification"
            ),
        )
    )
    parsed_unmanaged = _usb_operation_report_require_enum(
        _FavoritesUsbVerificationOutcome,
        verification["unmanaged_preserved"],
        label=(
            "Favorites USB operation report unmanaged preservation"
        ),
    )
    parsed_activation = _usb_operation_report_require_enum(
        _FavoritesUsbActivationOutcome,
        root["activation_outcome"],
        label=(
            "Favorites USB operation report activation outcome"
        ),
    )
    parsed_recovery = _usb_operation_report_require_enum(
        _FavoritesUsbRecoveryOutcome,
        root["recovery_outcome"],
        label=(
            "Favorites USB operation report recovery outcome"
        ),
    )

    for parsed_verification in (
        parsed_backup,
        parsed_staging,
        parsed_preactivation,
        parsed_postactivation,
        parsed_unmanaged,
    ):
        if not isinstance(
            parsed_verification,
            _FavoritesUsbVerificationOutcome,
        ):
            raise AssertionError(
                "USB verification parser returned wrong enum."
            )

    if not isinstance(
        parsed_backup,
        _FavoritesUsbVerificationOutcome,
    ):
        raise AssertionError(
            "USB backup verification parser returned wrong enum."
        )
    if not isinstance(
        parsed_staging,
        _FavoritesUsbVerificationOutcome,
    ):
        raise AssertionError(
            "USB staging verification parser returned wrong enum."
        )
    if not isinstance(
        parsed_preactivation,
        _FavoritesUsbVerificationOutcome,
    ):
        raise AssertionError(
            "USB preactivation verification parser returned wrong enum."
        )
    if not isinstance(
        parsed_postactivation,
        _FavoritesUsbVerificationOutcome,
    ):
        raise AssertionError(
            "USB postactivation verification parser returned wrong enum."
        )
    if not isinstance(
        parsed_unmanaged,
        _FavoritesUsbVerificationOutcome,
    ):
        raise AssertionError(
            "USB unmanaged verification parser returned wrong enum."
        )
    if not isinstance(
        parsed_activation,
        _FavoritesUsbActivationOutcome,
    ):
        raise AssertionError(
            "USB activation parser returned wrong enum."
        )
    if not isinstance(
        parsed_recovery,
        _FavoritesUsbRecoveryOutcome,
    ):
        raise AssertionError(
            "USB recovery parser returned wrong enum."
        )

    return _FavoritesUsbOperationReport(
        rollback_manifest=rollback_manifest,
        backup_verification=parsed_backup,
        staging_verification=parsed_staging,
        preactivation_verification=parsed_preactivation,
        postactivation_verification=parsed_postactivation,
        unmanaged_preservation=parsed_unmanaged,
        activation_outcome=parsed_activation,
        recovery_outcome=parsed_recovery,
        active_snapshot_sha256=active_snapshot_sha256,
        failure_code=failure_code,
    )


def _usb_operation_report_bytes(
    report: _FavoritesUsbOperationReport,
) -> bytes:
    if not isinstance(
        report,
        _FavoritesUsbOperationReport,
    ):
        raise TypeError(
            "Favorites USB operation report serialization requires "
            "_FavoritesUsbOperationReport."
        )

    return (
        json.dumps(
            report.as_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode(
        "utf-8"
    )


def _parse_usb_operation_report_bytes(
    content: bytes,
) -> _FavoritesUsbOperationReport:
    if not isinstance(
        content,
        bytes,
    ):
        raise TypeError(
            "Favorites USB operation report content must be bytes."
        )
    if (
        not content
        or len(content)
        > _USB_OPERATION_REPORT_MAX_BYTES
    ):
        raise ValueError(
            "Favorites USB operation report size is invalid."
        )

    try:
        text = content.decode(
            "utf-8"
        )
    except UnicodeDecodeError as error:
        raise ValueError(
            "Favorites USB operation report must be valid UTF-8."
        ) from error

    try:
        payload = json.loads(
            text,
            object_pairs_hook=(
                _usb_rollback_reject_duplicate_object
            ),
        )
    except (
        json.JSONDecodeError,
        ValueError,
    ) as error:
        raise ValueError(
            "Favorites USB operation report is not valid strict JSON."
        ) from error

    report = _usb_operation_report_from_payload(
        payload
    )

    if (
        _usb_operation_report_bytes(
            report
        )
        != content
    ):
        raise ValueError(
            "Favorites USB operation report is not in canonical form."
        )

    return report


def _read_usb_operation_report(
    path: Path,
) -> _FavoritesUsbOperationReport:
    try:
        content = (
            _read_usb_host_durable_regular_file(
                path
            )
        )
        if len(
            content
        ) > _USB_OPERATION_REPORT_MAX_BYTES:
            raise ValueError(
                "Favorites USB operation report exceeds the maximum "
                "allowed size."
            )
        return (
            _parse_usb_operation_report_bytes(
                content
            )
        )
    except _FavoritesUsbWritePreparationError:
        raise
    except (
        TypeError,
        ValueError,
    ) as error:
        raise _FavoritesUsbWritePreparationError(
            path,
            f"Could not parse durable Favorites USB operation report: {error}",
        ) from error


def _usb_operation_report_destination(
    paths: _FavoritesUsbHostOperationPaths,
    report: _FavoritesUsbOperationReport,
) -> Path:
    if not isinstance(
        paths,
        _FavoritesUsbHostOperationPaths,
    ):
        raise TypeError(
            "Favorites USB operation report destination requires "
            "_FavoritesUsbHostOperationPaths."
        )
    if not isinstance(
        report,
        _FavoritesUsbOperationReport,
    ):
        raise TypeError(
            "Favorites USB operation report destination requires "
            "_FavoritesUsbOperationReport."
        )

    return (
        paths.operation_report_path
        if report.is_success
        else paths.failure_report_path
    )


def _usb_operation_report_temporary_paths(
    paths: _FavoritesUsbHostOperationPaths,
) -> tuple[Path, Path]:
    if not isinstance(
        paths,
        _FavoritesUsbHostOperationPaths,
    ):
        raise TypeError(
            "Favorites USB operation report temporary paths require "
            "_FavoritesUsbHostOperationPaths."
        )

    return (
        paths.operation_directory
        / ".report.json.tmp",
        paths.operation_directory
        / ".failure.json.tmp",
    )


def _write_usb_operation_report(
    preflight: FavoritesUsbWritePreflight,
    paths: _FavoritesUsbHostOperationPaths,
    rollback_manifest: _FavoritesUsbRollbackManifest,
    report: _FavoritesUsbOperationReport,
) -> Path:
    if not isinstance(
        preflight,
        FavoritesUsbWritePreflight,
    ):
        raise TypeError(
            "Favorites USB operation report writer requires "
            "FavoritesUsbWritePreflight."
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
    _require_usb_operation_report_matches(
        preflight,
        paths,
        rollback_manifest,
        report,
    )

    destination = (
        _usb_operation_report_destination(
            paths,
            report,
        )
    )
    success_temp, failure_temp = (
        _usb_operation_report_temporary_paths(
            paths
        )
    )
    temporary = (
        success_temp
        if report.is_success
        else failure_temp
    )

    for final_path in (
        paths.operation_report_path,
        paths.failure_report_path,
    ):
        if os.path.lexists(
            final_path
        ):
            raise _FavoritesUsbWritePreparationError(
                final_path,
                "Favorites USB final operation report path already exists.",
            )

    for temp_path in (
        success_temp,
        failure_temp,
    ):
        if os.path.lexists(
            temp_path
        ):
            raise _FavoritesUsbWritePreparationError(
                temp_path,
                "Favorites USB operation report temporary path already exists.",
            )

    current_rollback = (
        _read_usb_rollback_manifest(
            paths.rollback_manifest_path
        )
    )
    if current_rollback != rollback_manifest:
        raise _FavoritesUsbWritePreparationError(
            paths.rollback_manifest_path,
            "Favorites USB rollback manifest changed before final report "
            "publication.",
        )

    no_follow = getattr(
        os,
        "O_NOFOLLOW",
        None,
    )
    if (
        no_follow is None
        or no_follow == 0
    ):
        raise _FavoritesUsbWritePreparationError(
            temporary,
            "Durable Favorites USB operation reports require O_NOFOLLOW "
            "support.",
        )

    content = (
        _usb_operation_report_bytes(
            report
        )
    )

    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(
            os,
            "O_BINARY",
            0,
        )
        | no_follow
    )

    descriptor: int | None = None
    temporary_identity: tuple[int, int] | None = None
    published = False

    try:
        try:
            descriptor = os.open(
                temporary,
                flags,
                0o600,
            )
        except OSError as error:
            raise _FavoritesUsbWritePreparationError(
                temporary,
                "Could not exclusively create Favorites USB operation "
                f"report temporary file: {error}",
            ) from error

        opened = os.fstat(
            descriptor
        )
        if not stat.S_ISREG(
            opened.st_mode
        ):
            raise _FavoritesUsbWritePreparationError(
                temporary,
                "Favorites USB operation report temporary path is not a "
                "regular file.",
            )

        temporary_identity = (
            opened.st_dev,
            opened.st_ino,
        )

        offset = 0
        while offset < len(
            content
        ):
            try:
                written = os.write(
                    descriptor,
                    content[offset:],
                )
            except OSError as error:
                raise _FavoritesUsbWritePreparationError(
                    temporary,
                    "Could not write Favorites USB operation report "
                    f"temporary file: {error}",
                ) from error

            if written <= 0:
                raise _FavoritesUsbWritePreparationError(
                    temporary,
                    "Favorites USB operation report temporary write returned "
                    "zero bytes.",
                )
            offset += written

        try:
            os.fsync(
                descriptor
            )
        except OSError as error:
            raise _FavoritesUsbWritePreparationError(
                temporary,
                "Could not synchronize Favorites USB operation report "
                f"temporary file: {error}",
            ) from error

        os.close(
            descriptor
        )
        descriptor = None

        temporary_content = (
            _read_usb_host_durable_regular_file(
                temporary
            )
        )
        if temporary_content != content:
            raise _FavoritesUsbWritePreparationError(
                temporary,
                "Favorites USB operation report temporary file failed exact "
                "readback.",
            )

        temporary_report = (
            _parse_usb_operation_report_bytes(
                temporary_content
            )
        )
        if temporary_report != report:
            raise _FavoritesUsbWritePreparationError(
                temporary,
                "Favorites USB operation report temporary schema failed "
                "exact readback.",
            )

        observed_rollback = (
            _read_usb_rollback_manifest(
                paths.rollback_manifest_path
            )
        )
        if observed_rollback != rollback_manifest:
            raise _FavoritesUsbWritePreparationError(
                paths.rollback_manifest_path,
                "Favorites USB rollback manifest changed immediately before "
                "final report publication.",
            )

        for final_path in (
            paths.operation_report_path,
            paths.failure_report_path,
        ):
            if os.path.lexists(
                final_path
            ):
                raise _FavoritesUsbWritePreparationError(
                    final_path,
                    "Favorites USB final operation report path appeared "
                    "before publication.",
                )

        try:
            os.replace(
                temporary,
                destination,
            )
        except OSError as error:
            raise _FavoritesUsbWritePreparationError(
                destination,
                "Could not atomically publish Favorites USB operation "
                f"report: {error}",
            ) from error

        published = True

        _fsync_usb_host_directory(
            paths.operation_directory
        )

        final_report = (
            _read_usb_operation_report(
                destination
            )
        )
        if final_report != report:
            raise _FavoritesUsbWritePreparationError(
                destination,
                "Published Favorites USB operation report failed exact "
                "post-fsync readback.",
            )

        final_rollback = (
            _read_usb_rollback_manifest(
                paths.rollback_manifest_path
            )
        )
        if final_rollback != rollback_manifest:
            raise _FavoritesUsbWritePreparationError(
                paths.rollback_manifest_path,
                "Favorites USB rollback manifest changed during final "
                "operation-report publication.",
            )

        alternate = (
            paths.failure_report_path
            if report.is_success
            else paths.operation_report_path
        )
        if os.path.lexists(
            alternate
        ):
            raise _FavoritesUsbWritePreparationError(
                alternate,
                "Favorites USB operation produced more than one final "
                "report path.",
            )

        return destination
    finally:
        if descriptor is not None:
            os.close(
                descriptor
            )

        if (
            not published
            and temporary_identity is not None
        ):
            try:
                observed = temporary.lstat()
            except (
                FileNotFoundError,
                OSError,
            ):
                pass
            else:
                if (
                    stat.S_ISREG(
                        observed.st_mode
                    )
                    and (
                        observed.st_dev,
                        observed.st_ino,
                    )
                    == temporary_identity
                ):
                    with suppress(
                        OSError
                    ):
                        temporary.unlink()


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
    unmanaged_sha256: str
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
        _validate_unmanaged_sha256(
            self.unmanaged_sha256,
            label="Favorites USB verified backup unmanaged tree SHA-256",
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
        unmanaged_sha256 = favorites_unmanaged_tree_sha256(
            current.favorites_directory
        )
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

    if unmanaged_sha256 != preflight.unmanaged_sha256:
        raise _FavoritesUsbWritePreparationError(
            current.favorites_directory,
            "USB Favorites unmanaged content or structure changed after preflight.",
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
        backup_unmanaged_sha256 = favorites_unmanaged_tree_sha256(
            paths.backup_directory
        )
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

    if backup_unmanaged_sha256 != preflight.unmanaged_sha256:
        raise _FavoritesUsbWritePreparationError(
            paths.backup_directory,
            "USB host backup does not preserve the exact unmanaged tree identity.",
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
        unmanaged_sha256=backup_unmanaged_sha256,
        snapshot=backup_snapshot,
    )


@dataclass(frozen=True, slots=True)
class _FavoritesUsbPreparedStage:
    directory: Path
    snapshot: FavoritesStorageSnapshot
    tree_evidence: FavoritesTreeEvidence
    unmanaged_sha256: str

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
        _validate_unmanaged_sha256(
            self.unmanaged_sha256,
            label="Favorites USB prepared staging unmanaged tree SHA-256",
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
        unmanaged_sha256 = favorites_unmanaged_tree_sha256(
            backup.directory
        )
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

    if (
        unmanaged_sha256 != backup.unmanaged_sha256
        or unmanaged_sha256 != preflight.unmanaged_sha256
    ):
        raise _FavoritesUsbWritePreparationError(
            backup.directory,
            "Verified USB host backup unmanaged tree identity changed.",
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
        staging_unmanaged_sha256 = favorites_unmanaged_tree_sha256(
            paths.staging_directory
        )
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

    if (
        staging_unmanaged_sha256 != preflight.unmanaged_sha256
        or staging_unmanaged_sha256 != backup.unmanaged_sha256
    ):
        raise _FavoritesUsbWritePreparationError(
            paths.staging_directory,
            "USB host staging does not preserve the exact unmanaged tree identity.",
        )

    _require_verified_usb_host_backup_current(
        preflight,
        paths,
        backup,
    )

    return _FavoritesUsbPreparedStage(
        directory=paths.staging_directory,
        snapshot=staged_snapshot,
        tree_evidence=staging_evidence,
        unmanaged_sha256=staging_unmanaged_sha256,
    )


@dataclass(frozen=True, slots=True)
class _FavoritesUsbPreactivationEvidence:
    active_tree_evidence: FavoritesTreeEvidence
    backup_tree_evidence: FavoritesTreeEvidence
    staging_tree_evidence: FavoritesTreeEvidence
    unmanaged_sha256: str

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
        _validate_unmanaged_sha256(
            self.unmanaged_sha256,
            label="Favorites USB preactivation unmanaged tree SHA-256",
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
        unmanaged_sha256 = favorites_unmanaged_tree_sha256(
            prepared.directory
        )
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

    if (
        unmanaged_sha256 != prepared.unmanaged_sha256
        or unmanaged_sha256 != preflight.unmanaged_sha256
    ):
        raise _FavoritesUsbWritePreparationError(
            prepared.directory,
            "Verified USB host staging unmanaged tree identity changed.",
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
        unmanaged_sha256=preflight.unmanaged_sha256,
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


@dataclass(frozen=True, slots=True)
class _FavoritesUsbRecoveryPlan:
    restore_documents: tuple[FavoritesStorageDocument, ...]
    restore_catalog_bytes: bytes
    remove_documents: tuple[FavoritesStorageDocument, ...]

    def __post_init__(self) -> None:
        if type(self.restore_documents) is not tuple:
            raise TypeError(
                "Favorites USB recovery restore documents must be a tuple."
            )
        if type(self.remove_documents) is not tuple:
            raise TypeError(
                "Favorites USB recovery removal documents must be a tuple."
            )
        if not isinstance(
            self.restore_catalog_bytes,
            bytes,
        ):
            raise TypeError(
                "Favorites USB recovery catalog content must be bytes."
            )

        for label, documents in (
            (
                "restore documents",
                self.restore_documents,
            ),
            (
                "removal documents",
                self.remove_documents,
            ),
        ):
            if any(
                not isinstance(
                    document,
                    FavoritesStorageDocument,
                )
                for document in documents
            ):
                raise TypeError(
                    f"Favorites USB recovery {label} must contain "
                    "FavoritesStorageDocument values."
                )

            names = tuple(
                document.filename
                for document in documents
            )
            if len(
                set(names)
            ) != len(names):
                raise ValueError(
                    f"Favorites USB recovery {label} must have unique filenames."
                )
            if names != tuple(
                sorted(
                    names,
                    key=lambda value: value.encode(
                        "utf-8"
                    ),
                )
            ):
                raise ValueError(
                    f"Favorites USB recovery {label} must be deterministically "
                    "ordered by filename."
                )
            for name in names:
                checked = (
                    _require_usb_managed_activation_filename(
                        name
                    )
                )
                if checked == "f_list.cfg":
                    raise ValueError(
                        f"Favorites USB recovery {label} may contain only "
                        "lowercase-.hpd documents."
                    )

        restore_names = {
            document.filename
            for document in self.restore_documents
        }
        removal_names = {
            document.filename
            for document in self.remove_documents
        }
        overlap = (
            restore_names
            & removal_names
        )
        if overlap:
            raise ValueError(
                "Favorites USB recovery must not restore and remove "
                "the same HPD filename."
            )


def _build_usb_recovery_plan(
    baseline: FavoritesStorageSnapshot,
    intended: FavoritesStorageSnapshot,
) -> _FavoritesUsbRecoveryPlan:
    if not isinstance(
        baseline,
        FavoritesStorageSnapshot,
    ):
        raise TypeError(
            "Favorites USB recovery baseline must be FavoritesStorageSnapshot."
        )
    if not isinstance(
        intended,
        FavoritesStorageSnapshot,
    ):
        raise TypeError(
            "Favorites USB recovery intended value must be "
            "FavoritesStorageSnapshot."
        )

    baseline_documents: dict[
        str,
        FavoritesStorageDocument,
    ] = {}
    for document in baseline.documents:
        checked = (
            _require_usb_managed_activation_filename(
                document.filename
            )
        )
        if checked == "f_list.cfg":
            raise _FavoritesUsbWritePreparationError(
                Path(document.filename),
                "USB recovery baseline documents must be lowercase-.hpd files.",
            )
        if checked in baseline_documents:
            raise _FavoritesUsbWritePreparationError(
                Path(document.filename),
                "USB recovery baseline HPD filenames are not unique.",
            )
        baseline_documents[
            checked
        ] = document

    intended_documents: dict[
        str,
        FavoritesStorageDocument,
    ] = {}
    for document in intended.documents:
        checked = (
            _require_usb_managed_activation_filename(
                document.filename
            )
        )
        if checked == "f_list.cfg":
            raise _FavoritesUsbWritePreparationError(
                Path(document.filename),
                "USB recovery intended documents must be lowercase-.hpd files.",
            )
        if checked in intended_documents:
            raise _FavoritesUsbWritePreparationError(
                Path(document.filename),
                "USB recovery intended HPD filenames are not unique.",
            )
        intended_documents[
            checked
        ] = document

    restore_documents = tuple(
        baseline_documents[name]
        for name in sorted(
            baseline_documents,
            key=lambda value: value.encode(
                "utf-8"
            ),
        )
    )

    introduced_names = (
        set(intended_documents)
        - set(baseline_documents)
    )
    remove_documents = tuple(
        intended_documents[name]
        for name in sorted(
            introduced_names,
            key=lambda value: value.encode(
                "utf-8"
            ),
        )
    )

    return _FavoritesUsbRecoveryPlan(
        restore_documents=restore_documents,
        restore_catalog_bytes=baseline.catalog_bytes,
        remove_documents=remove_documents,
    )


def _usb_recovery_plan(
    preflight: FavoritesUsbWritePreflight,
    backup: _FavoritesUsbVerifiedBackup,
) -> _FavoritesUsbRecoveryPlan:
    if not isinstance(
        preflight,
        FavoritesUsbWritePreflight,
    ):
        raise TypeError(
            "Favorites USB recovery planning requires "
            "FavoritesUsbWritePreflight."
        )
    if not isinstance(
        backup,
        _FavoritesUsbVerifiedBackup,
    ):
        raise TypeError(
            "Favorites USB recovery planning requires "
            "_FavoritesUsbVerifiedBackup."
        )

    if preflight.plan.is_blocked:
        raise _FavoritesUsbWritePreparationError(
            preflight.qualification.favorites_directory,
            "Blocked Favorites write plan cannot produce a USB recovery plan.",
        )

    baseline = (
        preflight.plan.baseline_snapshot
    )
    intended = (
        preflight.plan.intended_snapshot
    )

    if (
        backup.snapshot
        != baseline
        or backup.snapshot
        != preflight.observed_snapshot
    ):
        raise _FavoritesUsbWritePreparationError(
            backup.directory,
            "Verified USB host backup does not contain the exact "
            "write-plan baseline snapshot.",
        )

    if (
        backup.unmanaged_sha256
        != preflight.unmanaged_sha256
    ):
        raise _FavoritesUsbWritePreparationError(
            backup.directory,
            "Verified USB host backup unmanaged identity does not "
            "match the preflight baseline.",
        )

    return _build_usb_recovery_plan(
        backup.snapshot,
        intended,
    )


_USB_SUPPORTED_ACTIVATION_FILESYSTEMS = frozenset(
    {
        "vfat",
    }
)
_USB_MEDIA_TEMP_PREFIX = ".sds200-usb-write-"


@dataclass(frozen=True, slots=True)
class _FavoritesUsbMediaRecoveryArtifact:
    path: Path
    managed_filename: str
    content_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(
            self.path,
            Path,
        ):
            raise TypeError(
                "Favorites USB media recovery artifact path must be pathlib.Path."
            )
        if not self.path.is_absolute():
            raise ValueError(
                "Favorites USB media recovery artifact path must be absolute."
            )

        checked_filename = (
            _require_usb_managed_activation_filename(
                self.managed_filename
            )
        )
        if checked_filename == "f_list.cfg":
            raise ValueError(
                "Favorites USB media recovery artifact may identify only "
                "a lowercase-.hpd managed filename."
            )

        _validate_unmanaged_sha256(
            self.content_sha256,
            label="Favorites USB media recovery artifact content SHA-256",
        )


def _usb_media_content_sha256(
    content: bytes,
) -> str:
    if not isinstance(
        content,
        bytes,
    ):
        raise TypeError(
            "Favorites USB media recovery artifact content must be bytes."
        )

    return hashlib.sha256(
        content
    ).hexdigest()


class _FavoritesUsbMediaMutationError(RuntimeError):
    def __init__(
        self,
        path: Path,
        message: str,
        *,
        mutation_started: bool,
        recovery_artifact: _FavoritesUsbMediaRecoveryArtifact | None = None,
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

        if (
            recovery_artifact is not None
            and not isinstance(
                recovery_artifact,
                _FavoritesUsbMediaRecoveryArtifact,
            )
        ):
            raise TypeError(
                "Favorites USB media-mutation recovery artifact must be "
                "_FavoritesUsbMediaRecoveryArtifact or None."
            )

        self.path = path
        self.message = message
        self.recovery_artifact = recovery_artifact
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


@dataclass(frozen=True, slots=True)
class _FavoritesUsbRecoveryTargetEvidence:
    mount: LinuxMountInfoEntry
    block_device: LinuxBlockDeviceEvidence
    mount_directory: Path
    favorites_directory: Path
    unmanaged_sha256: str
    temporary_path: Path

    def __post_init__(self) -> None:
        if not isinstance(
            self.mount,
            LinuxMountInfoEntry,
        ):
            raise TypeError(
                "Favorites USB recovery target mount must be "
                "LinuxMountInfoEntry."
            )
        if not isinstance(
            self.block_device,
            LinuxBlockDeviceEvidence,
        ):
            raise TypeError(
                "Favorites USB recovery target block device must be "
                "LinuxBlockDeviceEvidence."
            )

        for label, value in (
            (
                "mount directory",
                self.mount_directory,
            ),
            (
                "Favorites directory",
                self.favorites_directory,
            ),
            (
                "temporary path",
                self.temporary_path,
            ),
        ):
            if not isinstance(
                value,
                Path,
            ):
                raise TypeError(
                    "Favorites USB recovery target "
                    f"{label} must be pathlib.Path."
                )
            if not value.is_absolute():
                raise ValueError(
                    "Favorites USB recovery target "
                    f"{label} must be absolute."
                )

        _validate_unmanaged_sha256(
            self.unmanaged_sha256,
            label=(
                "Favorites USB recovery target unmanaged "
                "tree SHA-256"
            ),
        )

        if (
            self.mount.device_number
            != self.block_device.device_number
        ):
            raise ValueError(
                "Favorites USB recovery target mount and "
                "block-device identities must match."
            )
        if not self.block_device.is_usb:
            raise ValueError(
                "Favorites USB recovery target requires "
                "proven USB ancestry."
            )
        if not self.mount.is_writable:
            raise ValueError(
                "Favorites USB recovery target requires "
                "a writable mount."
            )
        if (
            self.temporary_path.parent
            != self.favorites_directory
        ):
            raise ValueError(
                "Favorites USB recovery temporary path must "
                "remain directly inside the Favorites directory."
            )
        if (
            not self.temporary_path.name.startswith(
                _USB_MEDIA_TEMP_PREFIX
            )
            or not self.temporary_path.name.endswith(
                ".tmp"
            )
        ):
            raise ValueError(
                "Favorites USB recovery temporary path must use "
                "the bounded operation-owned media filename."
            )


def _usb_recovery_directory_status(
    path: Path,
    *,
    description: str,
) -> os.stat_result:
    try:
        observed = path.lstat()
    except OSError as error:
        raise _FavoritesUsbWritePreparationError(
            path,
            f"Could not inspect {description}: {error}",
        ) from error

    if stat.S_ISLNK(
        observed.st_mode
    ):
        raise _FavoritesUsbWritePreparationError(
            path,
            f"{description} must not be a symbolic link.",
        )
    if not stat.S_ISDIR(
        observed.st_mode
    ):
        raise _FavoritesUsbWritePreparationError(
            path,
            f"{description} must be a directory.",
        )

    return observed


def _usb_recovery_filesystem_device_number(
    observed: os.stat_result,
) -> tuple[int, int]:
    return (
        os.major(
            observed.st_dev
        ),
        os.minor(
            observed.st_dev
        ),
    )


def _usb_recovery_matching_mount(
    preflight: FavoritesUsbWritePreflight,
) -> tuple[
    LinuxMountInfoEntry,
    Path,
]:
    try:
        requested_status = (
            preflight.requested_path.lstat()
        )
    except OSError as error:
        raise _FavoritesUsbWritePreparationError(
            preflight.requested_path,
            (
                "Could not inspect original USB recovery "
                f"requested path: {error}"
            ),
        ) from error

    if stat.S_ISLNK(
        requested_status.st_mode
    ):
        raise _FavoritesUsbWritePreparationError(
            preflight.requested_path,
            (
                "Original USB recovery requested path must "
                "not be a symbolic link."
            ),
        )
    if not stat.S_ISDIR(
        requested_status.st_mode
    ):
        raise _FavoritesUsbWritePreparationError(
            preflight.requested_path,
            (
                "Original USB recovery requested path must "
                "remain a directory."
            ),
        )

    try:
        resolved_requested = (
            preflight.requested_path.resolve(
                strict=True
            )
        )
    except (OSError, RuntimeError) as error:
        raise _FavoritesUsbWritePreparationError(
            preflight.requested_path,
            (
                "Could not resolve original USB recovery "
                f"requested path: {error}"
            ),
        ) from error

    try:
        mount_entries = read_linux_mountinfo(
            preflight.mountinfo_path
        )
    except LinuxMountInfoError as error:
        raise _FavoritesUsbWritePreparationError(
            preflight.mountinfo_path,
            (
                "Could not read current USB recovery mount "
                f"evidence: {error}"
            ),
        ) from error

    matching_mounts: list[
        LinuxMountInfoEntry
    ] = []

    for mount in mount_entries:
        try:
            mount_root = (
                mount.mount_point.resolve(
                    strict=True
                )
            )
        except (OSError, RuntimeError):
            continue

        favorites_path = (
            mount_root
            / FAVORITES_USB_STORAGE_RELATIVE_DIRECTORY
        )
        try:
            resolved_favorites_path = (
                favorites_path.resolve(
                    strict=True
                )
            )
        except (OSError, RuntimeError):
            resolved_favorites_path = (
                favorites_path
            )

        if resolved_requested in (
            mount_root,
            resolved_favorites_path,
        ):
            matching_mounts.append(
                mount
            )

    if len(
        matching_mounts
    ) != 1:
        raise _FavoritesUsbWritePreparationError(
            preflight.requested_path,
            (
                "Original USB recovery requested path no "
                "longer identifies exactly one mounted target."
            ),
        )

    return (
        matching_mounts[0],
        resolved_requested,
    )


def _require_usb_recovery_target_ready(
    preflight: FavoritesUsbWritePreflight,
    paths: _FavoritesUsbHostOperationPaths,
    backup: _FavoritesUsbVerifiedBackup,
) -> _FavoritesUsbRecoveryTargetEvidence:
    # Re-establish the same physical USB target after managed mutation.

    if not isinstance(
        preflight,
        FavoritesUsbWritePreflight,
    ):
        raise TypeError(
            "Favorites USB recovery target requires "
            "FavoritesUsbWritePreflight."
        )
    if not isinstance(
        backup,
        _FavoritesUsbVerifiedBackup,
    ):
        raise TypeError(
            "Favorites USB recovery target requires "
            "_FavoritesUsbVerifiedBackup."
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

    expected = (
        preflight.qualification
    )
    (
        current_mount,
        resolved_requested,
    ) = _usb_recovery_matching_mount(
        preflight
    )

    if not current_mount.is_writable:
        raise _FavoritesUsbWritePreparationError(
            current_mount.mount_point,
            "USB recovery target is no longer writable.",
        )
    if current_mount != expected.mount:
        raise _FavoritesUsbWritePreparationError(
            current_mount.mount_point,
            (
                "USB recovery mount evidence changed from "
                "the original preflight."
            ),
        )

    try:
        current_block_device = (
            read_linux_block_device_evidence(
                current_mount.device_major,
                current_mount.device_minor,
                sys_dev_block_directory=(
                    preflight.sys_dev_block_directory
                ),
            )
        )
    except LinuxBlockDeviceError as error:
        raise _FavoritesUsbWritePreparationError(
            expected.block_device.sysfs_path,
            (
                "Could not read current USB recovery "
                f"block-device evidence: {error}"
            ),
        ) from error

    if (
        current_block_device
        != expected.block_device
    ):
        raise _FavoritesUsbWritePreparationError(
            current_block_device.sysfs_path,
            (
                "USB recovery block-device evidence changed "
                "from the original preflight."
            ),
        )
    if not current_block_device.is_usb:
        raise _FavoritesUsbWritePreparationError(
            current_block_device.sysfs_path,
            (
                "USB recovery target no longer has proven "
                "USB ancestry."
            ),
        )

    _require_usb_activation_filesystem(
        preflight
    )

    mount_status = (
        _usb_recovery_directory_status(
            current_mount.mount_point,
            description=(
                "USB recovery mounted scanner storage root"
            ),
        )
    )
    if (
        _usb_recovery_filesystem_device_number(
            mount_status
        )
        != current_mount.device_number
    ):
        raise _FavoritesUsbWritePreparationError(
            current_mount.mount_point,
            (
                "USB recovery mounted root no longer matches "
                "mountinfo device identity."
            ),
        )

    try:
        resolved_mount = (
            current_mount.mount_point.resolve(
                strict=True
            )
        )
    except (OSError, RuntimeError) as error:
        raise _FavoritesUsbWritePreparationError(
            current_mount.mount_point,
            (
                "Could not resolve USB recovery mounted "
                f"root: {error}"
            ),
        ) from error

    if (
        resolved_mount
        != expected.mount_directory
    ):
        raise _FavoritesUsbWritePreparationError(
            resolved_mount,
            (
                "USB recovery canonical mount path changed "
                "from preflight."
            ),
        )

    bcd_directory = (
        resolved_mount
        / "BCDx36HP"
    )
    _usb_recovery_directory_status(
        bcd_directory,
        description=(
            "USB recovery BCDx36HP directory"
        ),
    )

    favorites_directory = (
        resolved_mount
        / FAVORITES_USB_STORAGE_RELATIVE_DIRECTORY
    )
    favorites_status = (
        _usb_recovery_directory_status(
            favorites_directory,
            description=(
                "USB recovery Favorites directory"
            ),
        )
    )
    if (
        _usb_recovery_filesystem_device_number(
            favorites_status
        )
        != current_mount.device_number
    ):
        raise _FavoritesUsbWritePreparationError(
            favorites_directory,
            (
                "USB recovery Favorites directory no longer "
                "matches mountinfo device identity."
            ),
        )

    try:
        resolved_favorites = (
            favorites_directory.resolve(
                strict=True
            )
        )
    except (OSError, RuntimeError) as error:
        raise _FavoritesUsbWritePreparationError(
            favorites_directory,
            (
                "Could not resolve USB recovery Favorites "
                f"directory: {error}"
            ),
        ) from error

    if (
        resolved_favorites
        != expected.favorites_directory
    ):
        raise _FavoritesUsbWritePreparationError(
            resolved_favorites,
            (
                "USB recovery canonical Favorites path changed "
                "from preflight."
            ),
        )

    if resolved_requested not in (
        resolved_mount,
        resolved_favorites,
    ):
        raise _FavoritesUsbWritePreparationError(
            preflight.requested_path,
            (
                "Original USB recovery requested path no longer "
                "identifies the qualified mounted target."
            ),
        )

    requested_status = (
        _usb_recovery_directory_status(
            preflight.requested_path,
            description=(
                "original USB recovery requested path"
            ),
        )
    )
    if (
        _usb_recovery_filesystem_device_number(
            requested_status
        )
        != current_mount.device_number
    ):
        raise _FavoritesUsbWritePreparationError(
            preflight.requested_path,
            (
                "Original USB recovery requested path changed "
                "device identity."
            ),
        )

    temporary_path = (
        _usb_media_temporary_path(
            preflight,
            paths,
        )
    )

    try:
        unmanaged_sha256 = (
            _favorites_unmanaged_tree_sha256(
                resolved_favorites,
                excluded_root_regular_filename=(
                    temporary_path.name
                ),
            )
        )
    except FavoritesTreeEvidenceError as error:
        raise _FavoritesUsbWritePreparationError(
            error.path,
            (
                "Could not safely verify USB recovery "
                "unmanaged preservation evidence: "
                f"{error.message}"
            ),
        ) from error

    if (
        unmanaged_sha256
        != preflight.unmanaged_sha256
    ):
        raise _FavoritesUsbWritePreparationError(
            resolved_favorites,
            (
                "USB recovery target has unmanaged changes "
                "beyond the exact operation-owned temporary file."
            ),
        )

    _require_verified_usb_host_backup_current(
        preflight,
        paths,
        backup,
    )

    (
        final_mount,
        final_resolved_requested,
    ) = _usb_recovery_matching_mount(
        preflight
    )
    if (
        final_mount
        != current_mount
        or final_resolved_requested
        != resolved_requested
    ):
        raise _FavoritesUsbWritePreparationError(
            expected.mount_directory,
            (
                "USB recovery mount evidence changed during "
                "target verification."
            ),
        )

    try:
        final_block_device = (
            read_linux_block_device_evidence(
                current_mount.device_major,
                current_mount.device_minor,
                sys_dev_block_directory=(
                    preflight.sys_dev_block_directory
                ),
            )
        )
    except LinuxBlockDeviceError as error:
        raise _FavoritesUsbWritePreparationError(
            expected.block_device.sysfs_path,
            (
                "Could not re-read USB recovery "
                f"block-device evidence: {error}"
            ),
        ) from error

    if (
        final_block_device
        != current_block_device
    ):
        raise _FavoritesUsbWritePreparationError(
            final_block_device.sysfs_path,
            (
                "USB recovery block-device evidence changed "
                "during target verification."
            ),
        )

    final_mount_status = (
        _usb_recovery_directory_status(
            resolved_mount,
            description=(
                "USB recovery mounted scanner storage root"
            ),
        )
    )
    final_favorites_status = (
        _usb_recovery_directory_status(
            resolved_favorites,
            description=(
                "USB recovery Favorites directory"
            ),
        )
    )

    if (
        final_mount_status.st_dev,
        final_mount_status.st_ino,
        final_mount_status.st_mode,
    ) != (
        mount_status.st_dev,
        mount_status.st_ino,
        mount_status.st_mode,
    ):
        raise _FavoritesUsbWritePreparationError(
            resolved_mount,
            (
                "USB recovery mounted root changed during "
                "target verification."
            ),
        )

    if (
        final_favorites_status.st_dev,
        final_favorites_status.st_ino,
        final_favorites_status.st_mode,
    ) != (
        favorites_status.st_dev,
        favorites_status.st_ino,
        favorites_status.st_mode,
    ):
        raise _FavoritesUsbWritePreparationError(
            resolved_favorites,
            (
                "USB recovery Favorites directory changed "
                "during target verification."
            ),
        )

    return _FavoritesUsbRecoveryTargetEvidence(
        mount=current_mount,
        block_device=current_block_device,
        mount_directory=resolved_mount,
        favorites_directory=resolved_favorites,
        unmanaged_sha256=unmanaged_sha256,
        temporary_path=temporary_path,
    )


@dataclass(frozen=True, slots=True)
class _FavoritesUsbVerifiedRecoveryArtifact:
    artifact: _FavoritesUsbMediaRecoveryArtifact
    target_evidence: _FavoritesUsbRecoveryTargetEvidence
    baseline_document: FavoritesStorageDocument
    device: int
    inode: int
    size: int
    modified_ns: int
    mode: int

    def __post_init__(self) -> None:
        if not isinstance(
            self.artifact,
            _FavoritesUsbMediaRecoveryArtifact,
        ):
            raise TypeError(
                "Verified Favorites USB recovery artifact requires "
                "_FavoritesUsbMediaRecoveryArtifact."
            )
        if not isinstance(
            self.target_evidence,
            _FavoritesUsbRecoveryTargetEvidence,
        ):
            raise TypeError(
                "Verified Favorites USB recovery artifact target evidence must "
                "be _FavoritesUsbRecoveryTargetEvidence."
            )
        if not isinstance(
            self.baseline_document,
            FavoritesStorageDocument,
        ):
            raise TypeError(
                "Verified Favorites USB recovery artifact baseline document "
                "must be FavoritesStorageDocument."
            )

        if (
            self.artifact.path
            != self.target_evidence.temporary_path
        ):
            raise ValueError(
                "Verified Favorites USB recovery artifact path must match the "
                "exact operation temporary path."
            )
        if (
            self.artifact.managed_filename
            != self.baseline_document.filename
        ):
            raise ValueError(
                "Verified Favorites USB recovery artifact filename must match "
                "the exact baseline document."
            )
        if (
            self.artifact.content_sha256
            != _usb_media_content_sha256(
                self.baseline_document.content
            )
        ):
            raise ValueError(
                "Verified Favorites USB recovery artifact SHA-256 must match "
                "the exact baseline document."
            )

        for label, value in (
            ("device", self.device),
            ("inode", self.inode),
            ("size", self.size),
            ("modified nanoseconds", self.modified_ns),
            ("mode", self.mode),
        ):
            if not isinstance(
                value,
                int,
            ):
                raise TypeError(
                    f"Verified Favorites USB recovery artifact {label} "
                    "must be int."
                )
            if value < 0:
                raise ValueError(
                    f"Verified Favorites USB recovery artifact {label} "
                    "must be non-negative."
                )


def _usb_recovery_artifact_fingerprint(
    observed: os.stat_result,
) -> tuple[int, int, int, int, int]:
    return (
        observed.st_dev,
        observed.st_ino,
        observed.st_size,
        observed.st_mtime_ns,
        observed.st_mode,
    )


def _require_current_usb_recovery_artifact(
    preflight: FavoritesUsbWritePreflight,
    paths: _FavoritesUsbHostOperationPaths,
    backup: _FavoritesUsbVerifiedBackup,
    artifact: _FavoritesUsbMediaRecoveryArtifact,
) -> _FavoritesUsbVerifiedRecoveryArtifact:
    if not isinstance(
        preflight,
        FavoritesUsbWritePreflight,
    ):
        raise TypeError(
            "Favorites USB recovery-artifact verification requires "
            "FavoritesUsbWritePreflight."
        )
    if not isinstance(
        backup,
        _FavoritesUsbVerifiedBackup,
    ):
        raise TypeError(
            "Favorites USB recovery-artifact verification requires "
            "_FavoritesUsbVerifiedBackup."
        )
    if not isinstance(
        artifact,
        _FavoritesUsbMediaRecoveryArtifact,
    ):
        raise TypeError(
            "Favorites USB recovery-artifact verification requires "
            "_FavoritesUsbMediaRecoveryArtifact."
        )

    _require_host_operation_paths_match_preflight(
        preflight,
        paths,
    )
    _require_active_usb_host_lock(
        paths
    )
    _require_verified_usb_host_backup_current(
        preflight,
        paths,
        backup,
    )

    recovery_plan = _usb_recovery_plan(
        preflight,
        backup,
    )
    target_evidence = (
        _require_usb_recovery_target_ready(
            preflight,
            paths,
            backup,
        )
    )

    if artifact.path != target_evidence.temporary_path:
        raise _FavoritesUsbWritePreparationError(
            artifact.path,
            "USB recovery artifact path does not match the exact "
            "operation-owned temporary path.",
        )

    baseline_documents = {
        document.filename: document
        for document in recovery_plan.restore_documents
    }
    baseline_document = baseline_documents.get(
        artifact.managed_filename
    )
    if baseline_document is None:
        raise _FavoritesUsbWritePreparationError(
            artifact.path,
            "USB recovery artifact does not identify a baseline HPD.",
        )

    intended_names = {
        document.filename
        for document in preflight.plan.intended_snapshot.documents
    }
    if artifact.managed_filename in intended_names:
        raise _FavoritesUsbWritePreparationError(
            artifact.path,
            "USB recovery artifact filename is still present in the intended "
            "snapshot and therefore is not deletion-phase ownership evidence.",
        )

    activation_plan = (
        _usb_managed_activation_plan(
            preflight
        )
    )
    if (
        artifact.managed_filename
        not in activation_plan.document_deletions
    ):
        raise _FavoritesUsbWritePreparationError(
            artifact.path,
            "USB recovery artifact filename is not an operation deletion "
            "candidate.",
        )

    baseline_sha256 = (
        _usb_media_content_sha256(
            baseline_document.content
        )
    )
    if artifact.content_sha256 != baseline_sha256:
        raise _FavoritesUsbWritePreparationError(
            artifact.path,
            "USB recovery artifact provenance SHA-256 does not match the "
            "exact baseline HPD.",
        )

    try:
        initial = artifact.path.lstat()
    except OSError as error:
        raise _FavoritesUsbWritePreparationError(
            artifact.path,
            f"Could not inspect the current USB recovery artifact: {error}",
        ) from error

    try:
        current_content = (
            _read_usb_activation_regular_file(
                artifact.path
            )
        )
    except _FavoritesUsbWritePreparationError as error:
        raise _FavoritesUsbWritePreparationError(
            error.path,
            "Could not safely read the current USB recovery artifact: "
            f"{error.message}",
        ) from error

    try:
        observed = artifact.path.lstat()
    except OSError as error:
        raise _FavoritesUsbWritePreparationError(
            artifact.path,
            "Could not re-inspect the current USB recovery artifact: "
            f"{error}",
        ) from error

    initial_fingerprint = (
        _usb_recovery_artifact_fingerprint(
            initial
        )
    )
    observed_fingerprint = (
        _usb_recovery_artifact_fingerprint(
            observed
        )
    )
    if observed_fingerprint != initial_fingerprint:
        raise _FavoritesUsbWritePreparationError(
            artifact.path,
            "USB recovery artifact changed during exact-content verification.",
        )

    if current_content != baseline_document.content:
        raise _FavoritesUsbWritePreparationError(
            artifact.path,
            "Current USB recovery artifact bytes do not match the exact "
            "baseline HPD.",
        )
    if (
        _usb_media_content_sha256(
            current_content
        )
        != artifact.content_sha256
    ):
        raise _FavoritesUsbWritePreparationError(
            artifact.path,
            "Current USB recovery artifact SHA-256 does not match its "
            "verified provenance.",
        )

    _require_verified_usb_host_backup_current(
        preflight,
        paths,
        backup,
    )
    final_target_evidence = (
        _require_usb_recovery_target_ready(
            preflight,
            paths,
            backup,
        )
    )
    if final_target_evidence != target_evidence:
        raise _FavoritesUsbWritePreparationError(
            artifact.path,
            "USB recovery target evidence changed while binding the "
            "surviving artifact.",
        )

    try:
        final_before = artifact.path.lstat()
    except OSError as error:
        raise _FavoritesUsbWritePreparationError(
            artifact.path,
            "Could not re-inspect the USB recovery artifact after target "
            f"revalidation: {error}",
        ) from error

    if (
        _usb_recovery_artifact_fingerprint(
            final_before
        )
        != observed_fingerprint
    ):
        raise _FavoritesUsbWritePreparationError(
            artifact.path,
            "USB recovery artifact identity changed during target "
            "revalidation.",
        )

    try:
        final_content = (
            _read_usb_activation_regular_file(
                artifact.path
            )
        )
    except _FavoritesUsbWritePreparationError as error:
        raise _FavoritesUsbWritePreparationError(
            error.path,
            "Could not safely re-read the USB recovery artifact after target "
            f"revalidation: {error.message}",
        ) from error

    try:
        final_after = artifact.path.lstat()
    except OSError as error:
        raise _FavoritesUsbWritePreparationError(
            artifact.path,
            "Could not complete USB recovery artifact stability "
            f"verification: {error}",
        ) from error

    final_fingerprint = (
        _usb_recovery_artifact_fingerprint(
            final_after
        )
    )
    if (
        final_fingerprint
        != observed_fingerprint
        or _usb_recovery_artifact_fingerprint(
            final_before
        )
        != final_fingerprint
    ):
        raise _FavoritesUsbWritePreparationError(
            artifact.path,
            "USB recovery artifact changed during final exact-content "
            "verification.",
        )

    if (
        final_content != baseline_document.content
        or _usb_media_content_sha256(
            final_content
        )
        != artifact.content_sha256
    ):
        raise _FavoritesUsbWritePreparationError(
            artifact.path,
            "USB recovery artifact no longer matches the exact verified "
            "baseline HPD.",
        )

    return _FavoritesUsbVerifiedRecoveryArtifact(
        artifact=artifact,
        target_evidence=final_target_evidence,
        baseline_document=baseline_document,
        device=final_after.st_dev,
        inode=final_after.st_ino,
        size=final_after.st_size,
        modified_ns=final_after.st_mtime_ns,
        mode=final_after.st_mode,
    )


def _cleanup_verified_usb_recovery_artifact(
    preflight: FavoritesUsbWritePreflight,
    paths: _FavoritesUsbHostOperationPaths,
    backup: _FavoritesUsbVerifiedBackup,
    verified_artifact: _FavoritesUsbVerifiedRecoveryArtifact,
) -> None:
    if not isinstance(
        preflight,
        FavoritesUsbWritePreflight,
    ):
        raise TypeError(
            "Favorites USB recovery-artifact cleanup requires "
            "FavoritesUsbWritePreflight."
        )
    if not isinstance(
        backup,
        _FavoritesUsbVerifiedBackup,
    ):
        raise TypeError(
            "Favorites USB recovery-artifact cleanup requires "
            "_FavoritesUsbVerifiedBackup."
        )
    if not isinstance(
        verified_artifact,
        _FavoritesUsbVerifiedRecoveryArtifact,
    ):
        raise TypeError(
            "Favorites USB recovery-artifact cleanup requires "
            "_FavoritesUsbVerifiedRecoveryArtifact."
        )

    _require_host_operation_paths_match_preflight(
        preflight,
        paths,
    )
    _require_active_usb_host_lock(
        paths
    )
    _require_verified_usb_host_backup_current(
        preflight,
        paths,
        backup,
    )

    try:
        current_artifact = (
            _require_current_usb_recovery_artifact(
                preflight,
                paths,
                backup,
                verified_artifact.artifact,
            )
        )
    except _FavoritesUsbWritePreparationError as error:
        raise _FavoritesUsbMediaMutationError(
            error.path,
            "Could not revalidate verified USB recovery artifact before "
            f"cleanup: {error.message}",
            mutation_started=False,
        ) from error

    if current_artifact != verified_artifact:
        raise _FavoritesUsbMediaMutationError(
            verified_artifact.artifact.path,
            "Current USB recovery artifact no longer matches the exact "
            "previously verified artifact identity.",
            mutation_started=False,
        )

    current_target = (
        _require_usb_recovery_target_ready(
            preflight,
            paths,
            backup,
        )
    )
    if (
        current_target
        != verified_artifact.target_evidence
    ):
        raise _FavoritesUsbMediaMutationError(
            verified_artifact.artifact.path,
            "USB recovery target evidence changed before artifact cleanup.",
            mutation_started=False,
        )

    target = (
        current_target.favorites_directory
        / verified_artifact.baseline_document.filename
    )

    try:
        target_before = target.lstat()
    except OSError as error:
        raise _FavoritesUsbMediaMutationError(
            target,
            "Could not inspect restored baseline HPD before recovery-artifact "
            f"cleanup: {error}",
            mutation_started=False,
        ) from error

    if stat.S_ISLNK(
        target_before.st_mode
    ):
        raise _FavoritesUsbMediaMutationError(
            target,
            "Restored baseline HPD must not be a symbolic link before "
            "recovery-artifact cleanup.",
            mutation_started=False,
        )
    if not stat.S_ISREG(
        target_before.st_mode
    ):
        raise _FavoritesUsbMediaMutationError(
            target,
            "Restored baseline HPD must be a regular file before "
            "recovery-artifact cleanup.",
            mutation_started=False,
        )

    try:
        target_content = (
            _read_usb_activation_regular_file(
                target
            )
        )
    except _FavoritesUsbWritePreparationError as error:
        raise _FavoritesUsbMediaMutationError(
            error.path,
            "Could not safely verify restored baseline HPD before "
            f"recovery-artifact cleanup: {error.message}",
            mutation_started=False,
        ) from error

    try:
        target_after = target.lstat()
    except OSError as error:
        raise _FavoritesUsbMediaMutationError(
            target,
            "Could not re-inspect restored baseline HPD before "
            f"recovery-artifact cleanup: {error}",
            mutation_started=False,
        ) from error

    if (
        _usb_recovery_artifact_fingerprint(
            target_after
        )
        != _usb_recovery_artifact_fingerprint(
            target_before
        )
    ):
        raise _FavoritesUsbMediaMutationError(
            target,
            "Restored baseline HPD changed during recovery-artifact cleanup "
            "precondition verification.",
            mutation_started=False,
        )

    if (
        target_content
        != verified_artifact.baseline_document.content
    ):
        raise _FavoritesUsbMediaMutationError(
            target,
            "Recovery artifact cannot be cleaned until its baseline HPD is "
            "restored exactly.",
            mutation_started=False,
        )

    _require_verified_usb_host_backup_current(
        preflight,
        paths,
        backup,
    )
    final_target = (
        _require_usb_recovery_target_ready(
            preflight,
            paths,
            backup,
        )
    )
    if (
        final_target
        != verified_artifact.target_evidence
    ):
        raise _FavoritesUsbMediaMutationError(
            verified_artifact.artifact.path,
            "USB recovery target evidence changed during artifact cleanup "
            "verification.",
            mutation_started=False,
        )

    try:
        rebound_artifact = (
            _require_current_usb_recovery_artifact(
                preflight,
                paths,
                backup,
                verified_artifact.artifact,
            )
        )
    except _FavoritesUsbWritePreparationError as error:
        raise _FavoritesUsbMediaMutationError(
            error.path,
            "Could not complete verified USB recovery-artifact revalidation "
            f"before cleanup: {error.message}",
            mutation_started=False,
        ) from error

    if rebound_artifact != verified_artifact:
        raise _FavoritesUsbMediaMutationError(
            verified_artifact.artifact.path,
            "USB recovery artifact identity changed during cleanup "
            "precondition verification.",
            mutation_started=False,
        )

    # Recheck the restored baseline HPD after all artifact/physical-target
    # revalidation. Cleanup never removes the rollback artifact unless the
    # managed target is still the exact baseline bytes.
    try:
        final_target_before = target.lstat()
    except OSError as error:
        raise _FavoritesUsbMediaMutationError(
            target,
            "Could not inspect restored baseline HPD immediately before "
            f"artifact cleanup: {error}",
            mutation_started=False,
        ) from error

    try:
        final_target_content = (
            _read_usb_activation_regular_file(
                target
            )
        )
    except _FavoritesUsbWritePreparationError as error:
        raise _FavoritesUsbMediaMutationError(
            error.path,
            "Could not safely re-read restored baseline HPD immediately "
            f"before artifact cleanup: {error.message}",
            mutation_started=False,
        ) from error

    try:
        final_target_after = target.lstat()
    except OSError as error:
        raise _FavoritesUsbMediaMutationError(
            target,
            "Could not complete restored baseline HPD revalidation before "
            f"artifact cleanup: {error}",
            mutation_started=False,
        ) from error

    if (
        _usb_recovery_artifact_fingerprint(
            final_target_after
        )
        != _usb_recovery_artifact_fingerprint(
            final_target_before
        )
        or final_target_content
        != verified_artifact.baseline_document.content
    ):
        raise _FavoritesUsbMediaMutationError(
            target,
            "Restored baseline HPD is no longer exact and stable immediately "
            "before recovery-artifact cleanup.",
            mutation_started=False,
        )

    artifact_path = (
        verified_artifact.artifact.path
    )
    expected_fingerprint = (
        verified_artifact.device,
        verified_artifact.inode,
        verified_artifact.size,
        verified_artifact.modified_ns,
        verified_artifact.mode,
    )

    try:
        artifact_before = artifact_path.lstat()
    except OSError as error:
        raise _FavoritesUsbMediaMutationError(
            artifact_path,
            "Could not inspect verified USB recovery artifact immediately "
            f"before cleanup: {error}",
            mutation_started=False,
        ) from error

    if (
        _usb_recovery_artifact_fingerprint(
            artifact_before
        )
        != expected_fingerprint
    ):
        raise _FavoritesUsbMediaMutationError(
            artifact_path,
            "USB recovery artifact no longer has the exact verified "
            "fingerprint immediately before cleanup.",
            mutation_started=False,
        )

    try:
        artifact_content = (
            _read_usb_activation_regular_file(
                artifact_path
            )
        )
    except _FavoritesUsbWritePreparationError as error:
        raise _FavoritesUsbMediaMutationError(
            error.path,
            "Could not safely re-read verified USB recovery artifact "
            f"immediately before cleanup: {error.message}",
            mutation_started=False,
        ) from error

    try:
        artifact_after = artifact_path.lstat()
    except OSError as error:
        raise _FavoritesUsbMediaMutationError(
            artifact_path,
            "Could not complete verified USB recovery-artifact stability "
            f"check before cleanup: {error}",
            mutation_started=False,
        ) from error

    if (
        _usb_recovery_artifact_fingerprint(
            artifact_after
        )
        != expected_fingerprint
        or _usb_recovery_artifact_fingerprint(
            artifact_before
        )
        != expected_fingerprint
    ):
        raise _FavoritesUsbMediaMutationError(
            artifact_path,
            "USB recovery artifact identity changed during the immediate "
            "pre-cleanup readback.",
            mutation_started=False,
        )

    if (
        artifact_content
        != verified_artifact.baseline_document.content
        or _usb_media_content_sha256(
            artifact_content
        )
        != verified_artifact.artifact.content_sha256
    ):
        raise _FavoritesUsbMediaMutationError(
            artifact_path,
            "USB recovery artifact no longer contains the exact verified "
            "baseline HPD bytes immediately before cleanup.",
            mutation_started=False,
        )

    # POSIX pathname unlink has no atomic "unlink only if this inode still
    # occupies this name" operation. The host lock excludes cooperating
    # sds200 writers, and the exact path/fingerprint/content are revalidated
    # immediately above, but arbitrary external media mutation can still race
    # this syscall. Treat the unlink attempt itself as recovery mutation and
    # never issue a second delete if the path is recreated afterward.
    try:
        os.unlink(
            artifact_path
        )
    except OSError as error:
        raise _FavoritesUsbMediaMutationError(
            artifact_path,
            f"Could not clean verified USB recovery artifact: {error}",
            mutation_started=True,
        ) from error

    if os.path.lexists(
        artifact_path
    ):
        raise _FavoritesUsbMediaMutationError(
            artifact_path,
            "Verified USB recovery-artifact cleanup did not leave the exact "
            "temporary path absent; a replacement may have appeared.",
            mutation_started=True,
        )


@dataclass(frozen=True, slots=True)
class _FavoritesUsbVerifiedRecoveryRemovalArtifact:
    artifact: _FavoritesUsbMediaRecoveryArtifact
    target_evidence: _FavoritesUsbRecoveryTargetEvidence
    intended_document: FavoritesStorageDocument
    device: int
    inode: int
    size: int
    modified_ns: int
    mode: int

    def __post_init__(self) -> None:
        if not isinstance(
            self.artifact,
            _FavoritesUsbMediaRecoveryArtifact,
        ):
            raise TypeError(
                "Verified Favorites USB recovery-removal artifact requires "
                "_FavoritesUsbMediaRecoveryArtifact."
            )
        if not isinstance(
            self.target_evidence,
            _FavoritesUsbRecoveryTargetEvidence,
        ):
            raise TypeError(
                "Verified Favorites USB recovery-removal artifact target "
                "evidence must be _FavoritesUsbRecoveryTargetEvidence."
            )
        if not isinstance(
            self.intended_document,
            FavoritesStorageDocument,
        ):
            raise TypeError(
                "Verified Favorites USB recovery-removal artifact intended "
                "document must be FavoritesStorageDocument."
            )

        if (
            self.artifact.path
            != self.target_evidence.temporary_path
        ):
            raise ValueError(
                "Verified Favorites USB recovery-removal artifact path must "
                "match the exact operation temporary path."
            )
        if (
            self.artifact.managed_filename
            != self.intended_document.filename
        ):
            raise ValueError(
                "Verified Favorites USB recovery-removal artifact filename "
                "must match the exact intended-only document."
            )
        if (
            self.artifact.content_sha256
            != _usb_media_content_sha256(
                self.intended_document.content
            )
        ):
            raise ValueError(
                "Verified Favorites USB recovery-removal artifact SHA-256 "
                "must match the exact intended-only document."
            )

        for label, value in (
            ("device", self.device),
            ("inode", self.inode),
            ("size", self.size),
            ("modified nanoseconds", self.modified_ns),
            ("mode", self.mode),
        ):
            if not isinstance(
                value,
                int,
            ):
                raise TypeError(
                    "Verified Favorites USB recovery-removal artifact "
                    f"{label} must be int."
                )
            if value < 0:
                raise ValueError(
                    "Verified Favorites USB recovery-removal artifact "
                    f"{label} must be non-negative."
                )


def _require_current_usb_recovery_removal_artifact(
    preflight: FavoritesUsbWritePreflight,
    paths: _FavoritesUsbHostOperationPaths,
    backup: _FavoritesUsbVerifiedBackup,
    artifact: _FavoritesUsbMediaRecoveryArtifact,
) -> _FavoritesUsbVerifiedRecoveryRemovalArtifact:
    if not isinstance(
        preflight,
        FavoritesUsbWritePreflight,
    ):
        raise TypeError(
            "Favorites USB recovery-removal artifact verification requires "
            "FavoritesUsbWritePreflight."
        )
    if not isinstance(
        backup,
        _FavoritesUsbVerifiedBackup,
    ):
        raise TypeError(
            "Favorites USB recovery-removal artifact verification requires "
            "_FavoritesUsbVerifiedBackup."
        )
    if not isinstance(
        artifact,
        _FavoritesUsbMediaRecoveryArtifact,
    ):
        raise TypeError(
            "Favorites USB recovery-removal artifact verification requires "
            "_FavoritesUsbMediaRecoveryArtifact."
        )

    _require_host_operation_paths_match_preflight(
        preflight,
        paths,
    )
    _require_active_usb_host_lock(
        paths
    )
    _require_verified_usb_host_backup_current(
        preflight,
        paths,
        backup,
    )

    recovery_plan = _usb_recovery_plan(
        preflight,
        backup,
    )
    target_evidence = (
        _require_usb_recovery_target_ready(
            preflight,
            paths,
            backup,
        )
    )

    if artifact.path != target_evidence.temporary_path:
        raise _FavoritesUsbWritePreparationError(
            artifact.path,
            "USB recovery-removal artifact path does not match the exact "
            "operation-owned temporary path.",
        )

    removal_documents = {
        document.filename: document
        for document in recovery_plan.remove_documents
    }
    intended_document = removal_documents.get(
        artifact.managed_filename
    )
    if intended_document is None:
        raise _FavoritesUsbWritePreparationError(
            artifact.path,
            "USB recovery-removal artifact does not identify an exact "
            "intended-only HPD from the bound recovery plan.",
        )

    intended_sha256 = (
        _usb_media_content_sha256(
            intended_document.content
        )
    )
    if artifact.content_sha256 != intended_sha256:
        raise _FavoritesUsbWritePreparationError(
            artifact.path,
            "USB recovery-removal artifact provenance SHA-256 does not match "
            "the exact intended-only HPD.",
        )

    target = (
        target_evidence.favorites_directory
        / intended_document.filename
    )
    if os.path.lexists(
        target
    ):
        raise _FavoritesUsbWritePreparationError(
            target,
            "USB recovery-removal artifact cannot be bound while its "
            "intended-only managed target exists.",
        )

    try:
        initial = artifact.path.lstat()
    except OSError as error:
        raise _FavoritesUsbWritePreparationError(
            artifact.path,
            "Could not inspect the current USB recovery-removal artifact: "
            f"{error}",
        ) from error

    try:
        current_content = (
            _read_usb_activation_regular_file(
                artifact.path
            )
        )
    except _FavoritesUsbWritePreparationError as error:
        raise _FavoritesUsbWritePreparationError(
            error.path,
            "Could not safely read the current USB recovery-removal artifact: "
            f"{error.message}",
        ) from error

    try:
        observed = artifact.path.lstat()
    except OSError as error:
        raise _FavoritesUsbWritePreparationError(
            artifact.path,
            "Could not re-inspect the current USB recovery-removal artifact: "
            f"{error}",
        ) from error

    initial_fingerprint = (
        _usb_recovery_artifact_fingerprint(
            initial
        )
    )
    observed_fingerprint = (
        _usb_recovery_artifact_fingerprint(
            observed
        )
    )
    if observed_fingerprint != initial_fingerprint:
        raise _FavoritesUsbWritePreparationError(
            artifact.path,
            "USB recovery-removal artifact changed during exact-content "
            "verification.",
        )

    if current_content != intended_document.content:
        raise _FavoritesUsbWritePreparationError(
            artifact.path,
            "Current USB recovery-removal artifact bytes do not match the "
            "exact intended-only HPD.",
        )
    if (
        _usb_media_content_sha256(
            current_content
        )
        != artifact.content_sha256
    ):
        raise _FavoritesUsbWritePreparationError(
            artifact.path,
            "Current USB recovery-removal artifact SHA-256 does not match "
            "its verified provenance.",
        )

    if os.path.lexists(
        target
    ):
        raise _FavoritesUsbWritePreparationError(
            target,
            "Intended-only managed target appeared while binding the "
            "recovery-removal artifact.",
        )

    _require_verified_usb_host_backup_current(
        preflight,
        paths,
        backup,
    )
    final_target_evidence = (
        _require_usb_recovery_target_ready(
            preflight,
            paths,
            backup,
        )
    )
    if final_target_evidence != target_evidence:
        raise _FavoritesUsbWritePreparationError(
            artifact.path,
            "USB recovery target evidence changed while binding the "
            "recovery-removal artifact.",
        )

    if os.path.lexists(
        target
    ):
        raise _FavoritesUsbWritePreparationError(
            target,
            "Intended-only managed target appeared during recovery-removal "
            "artifact target revalidation.",
        )

    try:
        final_before = artifact.path.lstat()
    except OSError as error:
        raise _FavoritesUsbWritePreparationError(
            artifact.path,
            "Could not re-inspect the USB recovery-removal artifact after "
            f"target revalidation: {error}",
        ) from error

    if (
        _usb_recovery_artifact_fingerprint(
            final_before
        )
        != observed_fingerprint
    ):
        raise _FavoritesUsbWritePreparationError(
            artifact.path,
            "USB recovery-removal artifact identity changed during target "
            "revalidation.",
        )

    try:
        final_content = (
            _read_usb_activation_regular_file(
                artifact.path
            )
        )
    except _FavoritesUsbWritePreparationError as error:
        raise _FavoritesUsbWritePreparationError(
            error.path,
            "Could not safely re-read the USB recovery-removal artifact "
            f"after target revalidation: {error.message}",
        ) from error

    try:
        final_after = artifact.path.lstat()
    except OSError as error:
        raise _FavoritesUsbWritePreparationError(
            artifact.path,
            "Could not complete USB recovery-removal artifact stability "
            f"verification: {error}",
        ) from error

    final_fingerprint = (
        _usb_recovery_artifact_fingerprint(
            final_after
        )
    )
    if (
        final_fingerprint
        != observed_fingerprint
        or _usb_recovery_artifact_fingerprint(
            final_before
        )
        != final_fingerprint
    ):
        raise _FavoritesUsbWritePreparationError(
            artifact.path,
            "USB recovery-removal artifact changed during final exact-content "
            "verification.",
        )

    if (
        final_content != intended_document.content
        or _usb_media_content_sha256(
            final_content
        )
        != artifact.content_sha256
    ):
        raise _FavoritesUsbWritePreparationError(
            artifact.path,
            "USB recovery-removal artifact no longer matches the exact "
            "verified intended-only HPD.",
        )

    if os.path.lexists(
        target
    ):
        raise _FavoritesUsbWritePreparationError(
            target,
            "Intended-only managed target appeared during final "
            "recovery-removal artifact verification.",
        )

    return _FavoritesUsbVerifiedRecoveryRemovalArtifact(
        artifact=artifact,
        target_evidence=final_target_evidence,
        intended_document=intended_document,
        device=final_after.st_dev,
        inode=final_after.st_ino,
        size=final_after.st_size,
        modified_ns=final_after.st_mtime_ns,
        mode=final_after.st_mode,
    )


def _cleanup_verified_usb_recovery_removal_artifact(
    preflight: FavoritesUsbWritePreflight,
    paths: _FavoritesUsbHostOperationPaths,
    backup: _FavoritesUsbVerifiedBackup,
    verified_artifact: _FavoritesUsbVerifiedRecoveryRemovalArtifact,
) -> None:
    if not isinstance(
        preflight,
        FavoritesUsbWritePreflight,
    ):
        raise TypeError(
            "Favorites USB recovery-removal artifact cleanup requires "
            "FavoritesUsbWritePreflight."
        )
    if not isinstance(
        backup,
        _FavoritesUsbVerifiedBackup,
    ):
        raise TypeError(
            "Favorites USB recovery-removal artifact cleanup requires "
            "_FavoritesUsbVerifiedBackup."
        )
    if not isinstance(
        verified_artifact,
        _FavoritesUsbVerifiedRecoveryRemovalArtifact,
    ):
        raise TypeError(
            "Favorites USB recovery-removal artifact cleanup requires "
            "_FavoritesUsbVerifiedRecoveryRemovalArtifact."
        )

    _require_host_operation_paths_match_preflight(
        preflight,
        paths,
    )
    _require_active_usb_host_lock(
        paths
    )
    _require_verified_usb_host_backup_current(
        preflight,
        paths,
        backup,
    )

    try:
        current_artifact = (
            _require_current_usb_recovery_removal_artifact(
                preflight,
                paths,
                backup,
                verified_artifact.artifact,
            )
        )
    except _FavoritesUsbWritePreparationError as error:
        raise _FavoritesUsbMediaMutationError(
            error.path,
            "Could not revalidate verified USB recovery-removal artifact "
            f"before cleanup: {error.message}",
            mutation_started=False,
        ) from error

    if current_artifact != verified_artifact:
        raise _FavoritesUsbMediaMutationError(
            verified_artifact.artifact.path,
            "Current USB recovery-removal artifact no longer matches the "
            "exact previously verified artifact identity.",
            mutation_started=False,
        )

    target = (
        verified_artifact.target_evidence.favorites_directory
        / verified_artifact.intended_document.filename
    )
    if os.path.lexists(
        target
    ):
        raise _FavoritesUsbMediaMutationError(
            target,
            "Verified USB recovery-removal artifact cannot be cleaned while "
            "its intended-only managed target exists.",
            mutation_started=False,
        )

    current_target = (
        _require_usb_recovery_target_ready(
            preflight,
            paths,
            backup,
        )
    )
    if (
        current_target
        != verified_artifact.target_evidence
    ):
        raise _FavoritesUsbMediaMutationError(
            verified_artifact.artifact.path,
            "USB recovery target evidence changed before recovery-removal "
            "artifact cleanup.",
            mutation_started=False,
        )

    if os.path.lexists(
        target
    ):
        raise _FavoritesUsbMediaMutationError(
            target,
            "Intended-only managed target appeared during recovery-removal "
            "artifact cleanup verification.",
            mutation_started=False,
        )

    try:
        rebound_artifact = (
            _require_current_usb_recovery_removal_artifact(
                preflight,
                paths,
                backup,
                verified_artifact.artifact,
            )
        )
    except _FavoritesUsbWritePreparationError as error:
        raise _FavoritesUsbMediaMutationError(
            error.path,
            "Could not complete verified USB recovery-removal artifact "
            f"revalidation before cleanup: {error.message}",
            mutation_started=False,
        ) from error

    if rebound_artifact != verified_artifact:
        raise _FavoritesUsbMediaMutationError(
            verified_artifact.artifact.path,
            "USB recovery-removal artifact identity changed during cleanup "
            "precondition verification.",
            mutation_started=False,
        )

    if os.path.lexists(
        target
    ):
        raise _FavoritesUsbMediaMutationError(
            target,
            "Intended-only managed target appeared immediately before "
            "recovery-removal artifact cleanup.",
            mutation_started=False,
        )

    artifact_path = (
        verified_artifact.artifact.path
    )
    expected_fingerprint = (
        verified_artifact.device,
        verified_artifact.inode,
        verified_artifact.size,
        verified_artifact.modified_ns,
        verified_artifact.mode,
    )

    try:
        artifact_before = artifact_path.lstat()
    except OSError as error:
        raise _FavoritesUsbMediaMutationError(
            artifact_path,
            "Could not inspect verified USB recovery-removal artifact "
            f"immediately before cleanup: {error}",
            mutation_started=False,
        ) from error

    if (
        _usb_recovery_artifact_fingerprint(
            artifact_before
        )
        != expected_fingerprint
    ):
        raise _FavoritesUsbMediaMutationError(
            artifact_path,
            "USB recovery-removal artifact no longer has the exact verified "
            "fingerprint immediately before cleanup.",
            mutation_started=False,
        )

    try:
        artifact_content = (
            _read_usb_activation_regular_file(
                artifact_path
            )
        )
    except _FavoritesUsbWritePreparationError as error:
        raise _FavoritesUsbMediaMutationError(
            error.path,
            "Could not safely re-read verified USB recovery-removal artifact "
            f"immediately before cleanup: {error.message}",
            mutation_started=False,
        ) from error

    try:
        artifact_after = artifact_path.lstat()
    except OSError as error:
        raise _FavoritesUsbMediaMutationError(
            artifact_path,
            "Could not complete verified USB recovery-removal artifact "
            f"stability check before cleanup: {error}",
            mutation_started=False,
        ) from error

    if (
        _usb_recovery_artifact_fingerprint(
            artifact_after
        )
        != expected_fingerprint
        or _usb_recovery_artifact_fingerprint(
            artifact_before
        )
        != expected_fingerprint
    ):
        raise _FavoritesUsbMediaMutationError(
            artifact_path,
            "USB recovery-removal artifact identity changed during the "
            "immediate pre-cleanup readback.",
            mutation_started=False,
        )

    if (
        artifact_content
        != verified_artifact.intended_document.content
        or _usb_media_content_sha256(
            artifact_content
        )
        != verified_artifact.artifact.content_sha256
    ):
        raise _FavoritesUsbMediaMutationError(
            artifact_path,
            "USB recovery-removal artifact no longer contains the exact "
            "verified intended-only HPD bytes immediately before cleanup.",
            mutation_started=False,
        )

    if os.path.lexists(
        target
    ):
        raise _FavoritesUsbMediaMutationError(
            target,
            "Intended-only managed target appeared at the immediate "
            "recovery-removal artifact cleanup boundary.",
            mutation_started=False,
        )

    # POSIX pathname unlink has no atomic "unlink only if this inode still
    # occupies this name" operation. The host lock excludes cooperating
    # sds200 writers, and the exact path/fingerprint/content are revalidated
    # immediately above, but arbitrary external media mutation can still race
    # this syscall. Treat the unlink attempt itself as recovery mutation and
    # never issue a second delete if the path is recreated afterward.
    try:
        os.unlink(
            artifact_path
        )
    except OSError as error:
        raise _FavoritesUsbMediaMutationError(
            artifact_path,
            "Could not clean verified USB recovery-removal artifact: "
            f"{error}",
            mutation_started=True,
        ) from error

    if os.path.lexists(
        artifact_path
    ):
        raise _FavoritesUsbMediaMutationError(
            artifact_path,
            "Verified USB recovery-removal artifact cleanup did not leave "
            "the exact temporary path absent; a replacement may have "
            "appeared.",
            mutation_started=True,
        )

    if os.path.lexists(
        target
    ):
        raise _FavoritesUsbMediaMutationError(
            target,
            "Intended-only managed target appeared after recovery-removal "
            "artifact cleanup; it was preserved.",
            mutation_started=True,
        )

    try:
        final_target = (
            _require_usb_recovery_target_ready(
                preflight,
                paths,
                backup,
            )
        )
    except _FavoritesUsbWritePreparationError as error:
        raise _FavoritesUsbMediaMutationError(
            error.path,
            "Could not revalidate USB recovery target after recovery-removal "
            f"artifact cleanup: {error.message}",
            mutation_started=True,
        ) from error

    if (
        final_target
        != verified_artifact.target_evidence
    ):
        raise _FavoritesUsbMediaMutationError(
            target,
            "USB recovery target evidence changed after recovery-removal "
            "artifact cleanup.",
            mutation_started=True,
        )

    if os.path.lexists(
        target
    ):
        raise _FavoritesUsbMediaMutationError(
            target,
            "Intended-only managed target appeared during final "
            "recovery-removal artifact verification; it was preserved.",
            mutation_started=True,
        )

    if os.path.lexists(
        artifact_path
    ):
        raise _FavoritesUsbMediaMutationError(
            artifact_path,
            "Recovery-removal artifact path reappeared during final target "
            "verification; it was not deleted a second time.",
            mutation_started=True,
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


def _read_usb_recovery_descriptor_content(
    descriptor: int,
    path: Path,
) -> bytes:
    if not isinstance(
        descriptor,
        int,
    ):
        raise TypeError(
            "Favorites USB recovery descriptor must be int."
        )
    if not isinstance(
        path,
        Path,
    ):
        raise TypeError(
            "Favorites USB recovery descriptor path must be pathlib.Path."
        )

    try:
        os.lseek(
            descriptor,
            0,
            os.SEEK_SET,
        )
        chunks: list[bytes] = []
        while True:
            chunk = os.read(
                descriptor,
                1024 * 1024,
            )
            if not chunk:
                break
            chunks.append(
                chunk
            )
    except OSError as error:
        raise _FavoritesUsbWritePreparationError(
            path,
            f"Could not read guarded USB recovery descriptor: {error}",
        ) from error

    return b"".join(
        chunks
    )


def _write_usb_recovery_descriptor_content(
    descriptor: int,
    path: Path,
    content: bytes,
) -> None:
    if not isinstance(
        descriptor,
        int,
    ):
        raise TypeError(
            "Favorites USB recovery descriptor must be int."
        )
    if not isinstance(
        path,
        Path,
    ):
        raise TypeError(
            "Favorites USB recovery descriptor path must be pathlib.Path."
        )
    if not isinstance(
        content,
        bytes,
    ):
        raise TypeError(
            "Favorites USB recovery descriptor content must be bytes."
        )

    try:
        os.lseek(
            descriptor,
            0,
            os.SEEK_SET,
        )
        offset = 0
        while offset < len(content):
            written = os.write(
                descriptor,
                content[offset:],
            )
            if written <= 0:
                raise OSError(
                    "zero-byte guarded USB recovery write"
                )
            offset += written

        os.fsync(
            descriptor
        )
    except OSError as error:
        raise _FavoritesUsbMediaMutationError(
            path,
            f"Could not write guarded USB recovery content: {error}",
            mutation_started=True,
        ) from error


def _restore_usb_active_managed_file(
    preflight: FavoritesUsbWritePreflight,
    paths: _FavoritesUsbHostOperationPaths,
    filename: str,
    baseline_content: bytes,
    *,
    allowed_existing_content: bytes | None,
    allow_absent: bool,
) -> None:
    if not isinstance(
        preflight,
        FavoritesUsbWritePreflight,
    ):
        raise TypeError(
            "Favorites USB guarded recovery restore requires "
            "FavoritesUsbWritePreflight."
        )
    if not isinstance(
        baseline_content,
        bytes,
    ):
        raise TypeError(
            "Favorites USB guarded recovery baseline content must be bytes."
        )
    if (
        allowed_existing_content is not None
        and not isinstance(
            allowed_existing_content,
            bytes,
        )
    ):
        raise TypeError(
            "Favorites USB guarded recovery allowed existing content must be "
            "bytes or None."
        )
    if type(allow_absent) is not bool:
        raise TypeError(
            "Favorites USB guarded recovery absent-target flag must be bool."
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

    no_follow = getattr(
        os,
        "O_NOFOLLOW",
        None,
    )
    if (
        not isinstance(
            no_follow,
            int,
        )
        or no_follow == 0
    ):
        raise _FavoritesUsbWritePreparationError(
            preflight.qualification.favorites_directory,
            "Guarded USB recovery requires O_NOFOLLOW support.",
        )

    root = (
        preflight.qualification.favorites_directory
    )
    target = root / filename
    binary = getattr(
        os,
        "O_BINARY",
        0,
    )

    try:
        initial = target.lstat()
    except FileNotFoundError:
        if not allow_absent:
            raise _FavoritesUsbMediaMutationError(
                target,
                "Guarded USB recovery target is absent but absence is not "
                "allowed for this restore.",
                mutation_started=False,
            ) from None

        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | no_follow
            | binary
        )
        try:
            descriptor = os.open(
                target,
                flags,
                0o600,
            )
        except OSError as error:
            raise _FavoritesUsbMediaMutationError(
                target,
                "Could not exclusively create absent guarded USB recovery "
                f"target: {error}",
                mutation_started=False,
            ) from error

        mutation_started = True
        opened: os.stat_result | None = None
        try:
            try:
                opened = os.fstat(
                    descriptor
                )
            except OSError as error:
                raise _FavoritesUsbMediaMutationError(
                    target,
                    "Could not inspect newly created guarded USB recovery "
                    f"target: {error}",
                    mutation_started=True,
                ) from error

            if not stat.S_ISREG(
                opened.st_mode
            ):
                raise _FavoritesUsbMediaMutationError(
                    target,
                    "New guarded USB recovery target is not a regular file.",
                    mutation_started=True,
                )

            try:
                current_path = target.lstat()
            except OSError as error:
                raise _FavoritesUsbMediaMutationError(
                    target,
                    "Could not re-inspect newly created guarded USB recovery "
                    f"target: {error}",
                    mutation_started=True,
                ) from error

            if (
                current_path.st_dev,
                current_path.st_ino,
            ) != (
                opened.st_dev,
                opened.st_ino,
            ):
                raise _FavoritesUsbMediaMutationError(
                    target,
                    "New guarded USB recovery target pathname changed before "
                    "content write.",
                    mutation_started=True,
                )

            _write_usb_recovery_descriptor_content(
                descriptor,
                target,
                baseline_content,
            )

            try:
                written = os.fstat(
                    descriptor
                )
            except OSError as error:
                raise _FavoritesUsbMediaMutationError(
                    target,
                    "Could not inspect newly restored USB recovery target: "
                    f"{error}",
                    mutation_started=True,
                ) from error

            if (
                not stat.S_ISREG(
                    written.st_mode
                )
                or (
                    written.st_dev,
                    written.st_ino,
                )
                != (
                    opened.st_dev,
                    opened.st_ino,
                )
            ):
                raise _FavoritesUsbMediaMutationError(
                    target,
                    "New guarded USB recovery target identity changed during "
                    "content write.",
                    mutation_started=True,
                )
        finally:
            with suppress(OSError):
                os.close(
                    descriptor
                )

        assert mutation_started
        assert opened is not None
        expected_identity = (
            opened.st_dev,
            opened.st_ino,
        )
    except OSError as error:
        raise _FavoritesUsbMediaMutationError(
            target,
            f"Could not inspect guarded USB recovery target: {error}",
            mutation_started=False,
        ) from error
    else:
        if stat.S_ISLNK(
            initial.st_mode
        ):
            raise _FavoritesUsbMediaMutationError(
                target,
                "Guarded USB recovery target must not be a symbolic link.",
                mutation_started=False,
            )
        if not stat.S_ISREG(
            initial.st_mode
        ):
            raise _FavoritesUsbMediaMutationError(
                target,
                "Guarded USB recovery target must be a regular file.",
                mutation_started=False,
            )

        try:
            observed_content = (
                _read_usb_activation_regular_file(
                    target
                )
            )
        except _FavoritesUsbWritePreparationError as error:
            raise _FavoritesUsbMediaMutationError(
                error.path,
                error.message,
                mutation_started=False,
            ) from error

        try:
            observed = target.lstat()
        except OSError as error:
            raise _FavoritesUsbMediaMutationError(
                target,
                "Could not re-inspect guarded USB recovery target before "
                f"state classification: {error}",
                mutation_started=False,
            ) from error

        if (
            _usb_recovery_artifact_fingerprint(
                observed
            )
            != _usb_recovery_artifact_fingerprint(
                initial
            )
        ):
            raise _FavoritesUsbMediaMutationError(
                target,
                "Guarded USB recovery target changed during state "
                "classification.",
                mutation_started=False,
            )

        if observed_content == baseline_content:
            return

        if (
            allowed_existing_content is None
            or observed_content
            != allowed_existing_content
        ):
            raise _FavoritesUsbMediaMutationError(
                target,
                "Guarded USB recovery target does not contain an explicitly "
                "allowed operation-known state.",
                mutation_started=False,
            )

        flags = (
            os.O_RDWR
            | no_follow
            | binary
        )
        try:
            descriptor = os.open(
                target,
                flags,
            )
        except OSError as error:
            raise _FavoritesUsbMediaMutationError(
                target,
                "Could not open guarded USB recovery target without "
                f"truncation: {error}",
                mutation_started=False,
            ) from error

        mutation_started = False
        opened = None
        try:
            try:
                opened = os.fstat(
                    descriptor
                )
            except OSError as error:
                raise _FavoritesUsbMediaMutationError(
                    target,
                    "Could not inspect opened guarded USB recovery target: "
                    f"{error}",
                    mutation_started=False,
                ) from error

            if not stat.S_ISREG(
                opened.st_mode
            ):
                raise _FavoritesUsbMediaMutationError(
                    target,
                    "Opened guarded USB recovery target is not a regular file.",
                    mutation_started=False,
                )

            if (
                _usb_recovery_artifact_fingerprint(
                    opened
                )
                != _usb_recovery_artifact_fingerprint(
                    observed
                )
            ):
                raise _FavoritesUsbMediaMutationError(
                    target,
                    "Guarded USB recovery target changed while being opened "
                    "for restoration.",
                    mutation_started=False,
                )

            try:
                descriptor_content = (
                    _read_usb_recovery_descriptor_content(
                        descriptor,
                        target,
                    )
                )
            except _FavoritesUsbWritePreparationError as error:
                raise _FavoritesUsbMediaMutationError(
                    error.path,
                    error.message,
                    mutation_started=False,
                ) from error

            try:
                verified_opened = os.fstat(
                    descriptor
                )
            except OSError as error:
                raise _FavoritesUsbMediaMutationError(
                    target,
                    "Could not re-inspect opened guarded USB recovery target "
                    f"before mutation: {error}",
                    mutation_started=False,
                ) from error

            if (
                _usb_recovery_artifact_fingerprint(
                    verified_opened
                )
                != _usb_recovery_artifact_fingerprint(
                    opened
                )
                or descriptor_content
                != allowed_existing_content
            ):
                raise _FavoritesUsbMediaMutationError(
                    target,
                    "Opened guarded USB recovery target no longer contains "
                    "the exact allowed operation-known state.",
                    mutation_started=False,
                )

            mutation_started = True
            try:
                os.ftruncate(
                    descriptor,
                    0,
                )
            except OSError as error:
                raise _FavoritesUsbMediaMutationError(
                    target,
                    f"Could not truncate guarded USB recovery target: {error}",
                    mutation_started=True,
                ) from error

            _write_usb_recovery_descriptor_content(
                descriptor,
                target,
                baseline_content,
            )

            try:
                written = os.fstat(
                    descriptor
                )
            except OSError as error:
                raise _FavoritesUsbMediaMutationError(
                    target,
                    "Could not inspect guarded USB recovery target after "
                    f"rewrite: {error}",
                    mutation_started=True,
                ) from error

            if (
                not stat.S_ISREG(
                    written.st_mode
                )
                or (
                    written.st_dev,
                    written.st_ino,
                )
                != (
                    opened.st_dev,
                    opened.st_ino,
                )
            ):
                raise _FavoritesUsbMediaMutationError(
                    target,
                    "Guarded USB recovery file identity changed during "
                    "in-place rewrite.",
                    mutation_started=True,
                )
        finally:
            with suppress(OSError):
                os.close(
                    descriptor
                )

        assert mutation_started
        assert opened is not None
        expected_identity = (
            opened.st_dev,
            opened.st_ino,
        )

    try:
        final_before = target.lstat()
    except OSError as error:
        raise _FavoritesUsbMediaMutationError(
            target,
            "Could not inspect guarded USB recovery target for final "
            f"readback: {error}",
            mutation_started=True,
        ) from error

    if (
        final_before.st_dev,
        final_before.st_ino,
    ) != expected_identity:
        raise _FavoritesUsbMediaMutationError(
            target,
            "Guarded USB recovery target pathname no longer identifies "
            "the restored file.",
            mutation_started=True,
        )

    try:
        final_content = (
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

    try:
        final_after = target.lstat()
    except OSError as error:
        raise _FavoritesUsbMediaMutationError(
            target,
            "Could not complete guarded USB recovery target readback: "
            f"{error}",
            mutation_started=True,
        ) from error

    if (
        _usb_recovery_artifact_fingerprint(
            final_after
        )
        != _usb_recovery_artifact_fingerprint(
            final_before
        )
        or (
            final_after.st_dev,
            final_after.st_ino,
        )
        != expected_identity
    ):
        raise _FavoritesUsbMediaMutationError(
            target,
            "Guarded USB recovery target changed during final exact "
            "readback.",
            mutation_started=True,
        )

    if final_content != baseline_content:
        raise _FavoritesUsbMediaMutationError(
            target,
            "Guarded USB recovery target failed exact baseline readback.",
            mutation_started=True,
        )


def _restore_displaced_usb_recovery_removal_content(
    target: Path,
    content: bytes,
) -> bool:
    # Best-effort exclusive restoration after a raced recovery displacement.
    #
    # The caller already mutated media by moving an unexpected file to the
    # operation-owned temporary path. This helper never overwrites a target
    # that appeared concurrently. It returns True only when it exclusively
    # recreated the absent target and verified the exact displaced bytes.
    if not isinstance(
        target,
        Path,
    ):
        raise TypeError(
            "Favorites USB recovery-removal restoration target must be "
            "pathlib.Path."
        )
    if not isinstance(
        content,
        bytes,
    ):
        raise TypeError(
            "Favorites USB recovery-removal restoration content must be bytes."
        )

    no_follow = getattr(
        os,
        "O_NOFOLLOW",
        None,
    )
    if (
        not isinstance(
            no_follow,
            int,
        )
        or no_follow == 0
    ):
        return False

    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | no_follow
        | getattr(
            os,
            "O_BINARY",
            0,
        )
    )

    descriptor: int | None = None
    created_identity: tuple[int, int] | None = None

    try:
        try:
            descriptor = os.open(
                target,
                flags,
                0o600,
            )
        except OSError:
            return False

        opened = os.fstat(
            descriptor
        )
        if not stat.S_ISREG(
            opened.st_mode
        ):
            return False

        created_identity = (
            opened.st_dev,
            opened.st_ino,
        )

        _write_usb_recovery_descriptor_content(
            descriptor,
            target,
            content,
        )
    except _FavoritesUsbMediaMutationError:
        return False
    finally:
        if descriptor is not None:
            os.close(
                descriptor
            )

    if created_identity is None:
        return False

    try:
        before = target.lstat()
    except OSError:
        return False

    if (
        before.st_dev,
        before.st_ino,
    ) != created_identity:
        return False

    try:
        restored_content = (
            _read_usb_activation_regular_file(
                target
            )
        )
    except _FavoritesUsbWritePreparationError:
        return False

    try:
        after = target.lstat()
    except OSError:
        return False

    return (
        (
            after.st_dev,
            after.st_ino,
        )
        == created_identity
        and _usb_recovery_artifact_fingerprint(
            after
        )
        == _usb_recovery_artifact_fingerprint(
            before
        )
        and restored_content == content
    )


def _remove_usb_recovery_intended_only_hpd(
    preflight: FavoritesUsbWritePreflight,
    paths: _FavoritesUsbHostOperationPaths,
    backup: _FavoritesUsbVerifiedBackup,
    document: FavoritesStorageDocument,
) -> None:
    if not isinstance(
        preflight,
        FavoritesUsbWritePreflight,
    ):
        raise TypeError(
            "Favorites USB recovery removal requires "
            "FavoritesUsbWritePreflight."
        )
    if not isinstance(
        backup,
        _FavoritesUsbVerifiedBackup,
    ):
        raise TypeError(
            "Favorites USB recovery removal requires "
            "_FavoritesUsbVerifiedBackup."
        )
    if not isinstance(
        document,
        FavoritesStorageDocument,
    ):
        raise TypeError(
            "Favorites USB recovery removal requires "
            "FavoritesStorageDocument."
        )

    filename = (
        _require_usb_managed_activation_filename(
            document.filename
        )
    )
    if filename == "f_list.cfg":
        raise ValueError(
            "Favorites USB recovery removal cannot remove f_list.cfg."
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
    _require_verified_usb_host_backup_current(
        preflight,
        paths,
        backup,
    )

    recovery_plan = _usb_recovery_plan(
        preflight,
        backup,
    )
    removal_matches = tuple(
        candidate
        for candidate in recovery_plan.remove_documents
        if candidate.filename == filename
    )
    if (
        len(removal_matches) != 1
        or removal_matches[0] != document
    ):
        raise _FavoritesUsbWritePreparationError(
            preflight.qualification.favorites_directory
            / filename,
            "USB recovery removal document is not the exact intended-only "
            "HPD from the bound recovery plan.",
        )

    intended_document = removal_matches[0]
    target_evidence = (
        _require_usb_recovery_target_ready(
            preflight,
            paths,
            backup,
        )
    )
    target = (
        target_evidence.favorites_directory
        / filename
    )
    temporary = (
        target_evidence.temporary_path
    )

    if os.path.lexists(
        temporary
    ):
        raise _FavoritesUsbMediaMutationError(
            temporary,
            "USB recovery removal requires the operation-owned media "
            "temporary path to be absent.",
            mutation_started=False,
        )

    try:
        initial = target.lstat()
    except FileNotFoundError:
        try:
            final_target_evidence = (
                _require_usb_recovery_target_ready(
                    preflight,
                    paths,
                    backup,
                )
            )
        except _FavoritesUsbWritePreparationError as error:
            raise _FavoritesUsbMediaMutationError(
                error.path,
                error.message,
                mutation_started=False,
            ) from error

        if (
            final_target_evidence
            != target_evidence
        ):
            raise _FavoritesUsbMediaMutationError(
                target,
                "USB recovery target evidence changed while confirming an "
                "already-absent intended-only HPD.",
                mutation_started=False,
            ) from None

        if os.path.lexists(
            temporary
        ):
            raise _FavoritesUsbMediaMutationError(
                temporary,
                "USB recovery media temporary artifact appeared while "
                "confirming an already-absent intended-only HPD.",
                mutation_started=False,
            ) from None

        try:
            target.lstat()
        except FileNotFoundError:
            return
        except OSError as error:
            raise _FavoritesUsbMediaMutationError(
                target,
                "Could not re-inspect absent intended-only HPD during "
                f"recovery removal: {error}",
                mutation_started=False,
            ) from error

        raise _FavoritesUsbMediaMutationError(
            target,
            "Intended-only HPD appeared while recovery removal was "
            "confirming the baseline-absent state.",
            mutation_started=False,
        ) from None
    except OSError as error:
        raise _FavoritesUsbMediaMutationError(
            target,
            f"Could not inspect intended-only HPD before recovery removal: {error}",
            mutation_started=False,
        ) from error

    if stat.S_ISLNK(
        initial.st_mode
    ):
        raise _FavoritesUsbMediaMutationError(
            target,
            "Recovery removal target must not be a symbolic link.",
            mutation_started=False,
        )
    if not stat.S_ISREG(
        initial.st_mode
    ):
        raise _FavoritesUsbMediaMutationError(
            target,
            "Recovery removal target must be a regular file.",
            mutation_started=False,
        )

    try:
        observed_content = (
            _read_usb_activation_regular_file(
                target
            )
        )
    except _FavoritesUsbWritePreparationError as error:
        raise _FavoritesUsbMediaMutationError(
            error.path,
            error.message,
            mutation_started=False,
        ) from error

    if (
        observed_content
        != intended_document.content
    ):
        raise _FavoritesUsbMediaMutationError(
            target,
            "Recovery removal target does not contain the exact intended-only "
            "HPD content.",
            mutation_started=False,
        )

    try:
        observed = target.lstat()
    except OSError as error:
        raise _FavoritesUsbMediaMutationError(
            target,
            "Could not re-inspect intended-only HPD before recovery removal: "
            f"{error}",
            mutation_started=False,
        ) from error

    initial_fingerprint = (
        _usb_recovery_artifact_fingerprint(
            initial
        )
    )
    if (
        _usb_recovery_artifact_fingerprint(
            observed
        )
        != initial_fingerprint
    ):
        raise _FavoritesUsbMediaMutationError(
            target,
            "Recovery removal target changed before final pre-mutation "
            "verification.",
            mutation_started=False,
        )

    try:
        current_target_evidence = (
            _require_usb_recovery_target_ready(
                preflight,
                paths,
                backup,
            )
        )
    except _FavoritesUsbWritePreparationError as error:
        raise _FavoritesUsbMediaMutationError(
            error.path,
            error.message,
            mutation_started=False,
        ) from error

    if (
        current_target_evidence
        != target_evidence
    ):
        raise _FavoritesUsbMediaMutationError(
            target,
            "USB recovery target evidence changed before intended-only HPD "
            "removal.",
            mutation_started=False,
        )

    if os.path.lexists(
        temporary
    ):
        raise _FavoritesUsbMediaMutationError(
            temporary,
            "USB recovery media temporary artifact appeared before "
            "intended-only HPD removal.",
            mutation_started=False,
        )

    try:
        final_before = target.lstat()
    except OSError as error:
        raise _FavoritesUsbMediaMutationError(
            target,
            "Could not complete intended-only HPD pre-removal identity "
            f"verification: {error}",
            mutation_started=False,
        ) from error

    if (
        _usb_recovery_artifact_fingerprint(
            final_before
        )
        != initial_fingerprint
    ):
        raise _FavoritesUsbMediaMutationError(
            target,
            "Recovery removal target changed immediately before mutation.",
            mutation_started=False,
        )

    try:
        final_content = (
            _read_usb_activation_regular_file(
                target
            )
        )
    except _FavoritesUsbWritePreparationError as error:
        raise _FavoritesUsbMediaMutationError(
            error.path,
            error.message,
            mutation_started=False,
        ) from error

    try:
        final_after = target.lstat()
    except OSError as error:
        raise _FavoritesUsbMediaMutationError(
            target,
            "Could not finish intended-only HPD exact pre-removal readback: "
            f"{error}",
            mutation_started=False,
        ) from error

    if (
        _usb_recovery_artifact_fingerprint(
            final_after
        )
        != initial_fingerprint
        or final_content
        != intended_document.content
    ):
        raise _FavoritesUsbMediaMutationError(
            target,
            "Recovery removal target changed during final exact "
            "pre-mutation readback.",
            mutation_started=False,
        )

    if os.path.lexists(
        temporary
    ):
        raise _FavoritesUsbMediaMutationError(
            temporary,
            "USB recovery media temporary artifact appeared immediately "
            "before intended-only HPD removal.",
            mutation_started=False,
        )

    try:
        os.replace(
            target,
            temporary,
        )
    except OSError as error:
        raise _FavoritesUsbMediaMutationError(
            target,
            "Could not move intended-only HPD into the bounded recovery "
            f"removal artifact: {error}",
            mutation_started=False,
        ) from error

    mutation_started = True

    try:
        try:
            displaced_before = (
                temporary.lstat()
            )
        except OSError as error:
            raise _FavoritesUsbMediaMutationError(
                temporary,
                "Could not inspect displaced intended-only HPD after recovery "
                f"removal began: {error}",
                mutation_started=True,
            ) from error

        if (
            stat.S_ISLNK(
                displaced_before.st_mode
            )
            or not stat.S_ISREG(
                displaced_before.st_mode
            )
        ):
            raise _FavoritesUsbMediaMutationError(
                temporary,
                "Displaced recovery-removal artifact must remain a regular "
                "non-symlink file.",
                mutation_started=True,
            )

        try:
            displaced_content = (
                _read_usb_activation_regular_file(
                    temporary
                )
            )
        except _FavoritesUsbWritePreparationError as error:
            raise _FavoritesUsbMediaMutationError(
                error.path,
                error.message,
                mutation_started=True,
            ) from error

        try:
            displaced_after = (
                temporary.lstat()
            )
        except OSError as error:
            raise _FavoritesUsbMediaMutationError(
                temporary,
                "Could not complete displaced intended-only HPD verification: "
                f"{error}",
                mutation_started=True,
            ) from error

        displaced_fingerprint = (
            _usb_recovery_artifact_fingerprint(
                displaced_after
            )
        )
        if (
            displaced_fingerprint
            != _usb_recovery_artifact_fingerprint(
                displaced_before
            )
        ):
            raise _FavoritesUsbMediaMutationError(
                temporary,
                "Displaced recovery-removal artifact changed during exact "
                "verification.",
                mutation_started=True,
            )

        if (
            displaced_content
            != intended_document.content
        ):
            # A pathname race displaced content that was not the verified
            # intended-only HPD. Never use os.replace() to restore it because
            # that could overwrite a target created concurrently. Instead make
            # one best-effort O_EXCL recreation only while the target name is
            # absent. Preserve the displaced temporary file regardless so the
            # higher-level recovery path cannot mistake this for completion.
            if not os.path.lexists(
                target
            ):
                _restore_displaced_usb_recovery_removal_content(
                    target,
                    displaced_content,
                )

            raise _FavoritesUsbMediaMutationError(
                temporary,
                "Displaced recovery-removal HPD failed exact intended-content "
                "verification.",
                mutation_started=True,
            )

        recovery_artifact = (
            _FavoritesUsbMediaRecoveryArtifact(
                path=temporary,
                managed_filename=filename,
                content_sha256=(
                    _usb_media_content_sha256(
                        intended_document.content
                    )
                ),
            )
        )

        if os.path.lexists(
            target
        ):
            raise _FavoritesUsbMediaMutationError(
                target,
                "A concurrent target appeared after the exact intended-only "
                "HPD was displaced; preserving the verified removal artifact.",
                mutation_started=True,
                recovery_artifact=recovery_artifact,
            )

        try:
            post_displacement_evidence = (
                _require_usb_recovery_target_ready(
                    preflight,
                    paths,
                    backup,
                )
            )
        except _FavoritesUsbWritePreparationError as error:
            raise _FavoritesUsbMediaMutationError(
                error.path,
                error.message,
                mutation_started=True,
                recovery_artifact=recovery_artifact,
            ) from error

        if (
            post_displacement_evidence
            != target_evidence
        ):
            raise _FavoritesUsbMediaMutationError(
                temporary,
                "USB recovery target evidence changed after intended-only "
                "HPD displacement.",
                mutation_started=True,
                recovery_artifact=recovery_artifact,
            )

        if os.path.lexists(
            target
        ):
            raise _FavoritesUsbMediaMutationError(
                target,
                "A concurrent target appeared during recovery-target "
                "revalidation; preserving the verified removal artifact.",
                mutation_started=True,
                recovery_artifact=recovery_artifact,
            )

        try:
            artifact_before_unlink = (
                temporary.lstat()
            )
        except OSError as error:
            raise _FavoritesUsbMediaMutationError(
                temporary,
                "Could not re-inspect the verified recovery-removal artifact "
                f"before cleanup: {error}",
                mutation_started=True,
                recovery_artifact=recovery_artifact,
            ) from error

        if (
            _usb_recovery_artifact_fingerprint(
                artifact_before_unlink
            )
            != displaced_fingerprint
        ):
            raise _FavoritesUsbMediaMutationError(
                temporary,
                "Verified recovery-removal artifact changed before cleanup.",
                mutation_started=True,
                recovery_artifact=recovery_artifact,
            )

        try:
            temporary.unlink()
        except OSError as error:
            raise _FavoritesUsbMediaMutationError(
                temporary,
                "Could not finalize intended-only USB recovery removal: "
                f"{error}",
                mutation_started=True,
                recovery_artifact=recovery_artifact,
            ) from error

        if os.path.lexists(
            temporary
        ):
            raise _FavoritesUsbMediaMutationError(
                temporary,
                "A path appeared at the recovery-removal artifact name after "
                "cleanup; it was not deleted a second time.",
                mutation_started=True,
            )

        if os.path.lexists(
            target
        ):
            raise _FavoritesUsbMediaMutationError(
                target,
                "A target appeared after intended-only recovery removal; it "
                "was preserved.",
                mutation_started=True,
            )

        try:
            final_target_evidence = (
                _require_usb_recovery_target_ready(
                    preflight,
                    paths,
                    backup,
                )
            )
        except _FavoritesUsbWritePreparationError as error:
            raise _FavoritesUsbMediaMutationError(
                error.path,
                error.message,
                mutation_started=True,
            ) from error

        if (
            final_target_evidence
            != target_evidence
        ):
            raise _FavoritesUsbMediaMutationError(
                target,
                "USB recovery target evidence changed while finalizing "
                "intended-only HPD removal.",
                mutation_started=True,
            )

        if (
            os.path.lexists(
                target
            )
            or os.path.lexists(
                temporary
            )
        ):
            raise _FavoritesUsbMediaMutationError(
                target,
                "Intended-only recovery removal did not finish with both the "
                "target and bounded temporary path absent.",
                mutation_started=True,
            )
    except BaseException:
        # Once target -> temporary succeeds, media mutation has begun. Any
        # exact intended artifact that survives is carried on the mutation
        # error; unknown raced content is deliberately left untouched for a
        # higher-level incomplete-recovery decision.
        raise

    assert mutation_started


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


def _delete_usb_active_managed_hpd(
    preflight: FavoritesUsbWritePreflight,
    paths: _FavoritesUsbHostOperationPaths,
    filename: str,
    expected_content: bytes,
) -> None:
    if not isinstance(
        preflight,
        FavoritesUsbWritePreflight,
    ):
        raise TypeError(
            "Favorites USB active HPD deletion requires "
            "FavoritesUsbWritePreflight."
        )
    if not isinstance(
        expected_content,
        bytes,
    ):
        raise TypeError(
            "Favorites USB active HPD deletion expected content must be bytes."
        )

    filename = (
        _require_usb_managed_activation_filename(
            filename
        )
    )
    if filename == "f_list.cfg":
        raise ValueError(
            "Favorites USB active HPD deletion cannot delete f_list.cfg."
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
            "USB HPD deletion temporary artifact already exists.",
            mutation_started=False,
        )

    try:
        initial = target.lstat()
    except OSError as error:
        raise _FavoritesUsbMediaMutationError(
            target,
            f"Could not inspect active HPD before deletion: {error}",
            mutation_started=False,
        ) from error

    if stat.S_ISLNK(
        initial.st_mode
    ):
        raise _FavoritesUsbMediaMutationError(
            target,
            "Active HPD deletion target must not be a symbolic link.",
            mutation_started=False,
        )
    if not stat.S_ISREG(
        initial.st_mode
    ):
        raise _FavoritesUsbMediaMutationError(
            target,
            "Active HPD deletion target must be a regular file.",
            mutation_started=False,
        )

    try:
        observed_content = (
            _read_usb_activation_regular_file(
                target
            )
        )
    except _FavoritesUsbWritePreparationError as error:
        raise _FavoritesUsbMediaMutationError(
            error.path,
            error.message,
            mutation_started=False,
        ) from error

    if observed_content != expected_content:
        raise _FavoritesUsbMediaMutationError(
            target,
            "Active HPD deletion target no longer has the expected exact content.",
            mutation_started=False,
        )

    try:
        current = target.lstat()
    except OSError as error:
        raise _FavoritesUsbMediaMutationError(
            target,
            f"Could not re-inspect active HPD before deletion: {error}",
            mutation_started=False,
        ) from error

    if (
        current.st_dev,
        current.st_ino,
        current.st_size,
        current.st_mtime_ns,
    ) != (
        initial.st_dev,
        initial.st_ino,
        initial.st_size,
        initial.st_mtime_ns,
    ):
        raise _FavoritesUsbMediaMutationError(
            target,
            "Active HPD deletion target changed before mutation.",
            mutation_started=False,
        )

    try:
        os.replace(
            target,
            temporary,
        )
    except OSError as error:
        raise _FavoritesUsbMediaMutationError(
            target,
            f"Could not move active HPD into the bounded deletion artifact: {error}",
            mutation_started=False,
        ) from error

    mutation_started = True

    try:
        try:
            displaced_content = (
                _read_usb_activation_regular_file(
                    temporary
                )
            )
        except _FavoritesUsbWritePreparationError as error:
            raise _FavoritesUsbMediaMutationError(
                error.path,
                error.message,
                mutation_started=True,
            ) from error

        if displaced_content != expected_content:
            # The path changed between verification and the file-level replace.
            # Restore only when the original target name is still absent; never
            # overwrite a concurrent replacement supplied by another writer.
            if not os.path.lexists(
                target
            ):
                with suppress(OSError):
                    os.replace(
                        temporary,
                        target,
                    )
            raise _FavoritesUsbMediaMutationError(
                temporary,
                "Displaced HPD failed exact post-move verification.",
                mutation_started=True,
            )

        recovery_artifact = (
            _FavoritesUsbMediaRecoveryArtifact(
                path=temporary,
                managed_filename=filename,
                content_sha256=(
                    _usb_media_content_sha256(
                        expected_content
                    )
                ),
            )
        )

        try:
            temporary.unlink()
        except OSError as error:
            raise _FavoritesUsbMediaMutationError(
                temporary,
                f"Could not finalize active HPD deletion: {error}",
                mutation_started=True,
                recovery_artifact=recovery_artifact,
            ) from error
    except BaseException:
        # A successful move means active media has changed even when a later
        # verification or cleanup step fails. Leave any surviving bounded
        # artifact intact for the higher-level recovery path.
        raise

    assert mutation_started

__all__ = [
    "FavoritesUsbWritePreflight",
    "FavoritesUsbWritePreflightError",
    "FavoritesUsbWritePreflightReason",
    "preflight_favorites_usb_write",
]
