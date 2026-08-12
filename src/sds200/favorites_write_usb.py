"""Pre-mutation safety contract for verified USB Favorites writes."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .favorites_storage import FavoritesStorageSnapshot
from .favorites_storage_evidence import (
    FavoritesTreeEvidence,
    FavoritesTreeEvidenceError,
    favorites_tree_evidence,
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


__all__ = [
    "FavoritesUsbWritePreflight",
    "FavoritesUsbWritePreflightError",
    "FavoritesUsbWritePreflightReason",
    "preflight_favorites_usb_write",
]
