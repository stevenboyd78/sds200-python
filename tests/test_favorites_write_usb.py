from __future__ import annotations

import os
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from sds200 import favorites_write_usb as write_usb
from sds200.favorites_storage import FavoritesStorageSnapshot
from sds200.favorites_storage_evidence import favorites_tree_evidence
from sds200.favorites_storage_usb import (
    FavoritesUsbStorageQualificationReason,
)
from sds200.favorites_write_plan import plan_favorites_write
from sds200.favorites_write_usb import (
    FavoritesUsbWritePreflight,
    FavoritesUsbWritePreflightError,
    FavoritesUsbWritePreflightReason,
    preflight_favorites_usb_write,
)

_BASELINE_CATALOG = (
    b"TargetModel\tBCDx36HP\r\n"
    b"FormatVersion\t1.00\r\n"
)
_CHANGED_CATALOG = (
    b"TargetModel\tBCDx36HP\n"
    b"FormatVersion\t1.00\n"
)


def _snapshot(
    catalog: bytes = _BASELINE_CATALOG,
) -> FavoritesStorageSnapshot:
    return FavoritesStorageSnapshot(
        catalog_bytes=catalog,
        documents=(),
    )


def _write_mountinfo(
    path: Path,
    *,
    mount_id: int,
    mount_directory: Path,
    device_major: int,
    device_minor: int,
    writable: bool = True,
) -> None:
    mode = "rw" if writable else "ro"
    path.write_text(
        (
            f"{mount_id} 1 {device_major}:{device_minor} / "
            f"{mount_directory} {mode} - vfat "
            f"/dev/test-{mount_id} {mode}\n"
        ),
        encoding="utf-8",
    )


def _symlink_or_skip(
    link: Path,
    target: Path,
) -> None:
    try:
        link.symlink_to(
            target,
            target_is_directory=target.is_dir(),
        )
    except OSError as error:
        pytest.skip(
            f"symbolic links unavailable: {error}"
        )


def _usb_write_fixture(
    tmp_path: Path,
    *,
    catalog: bytes = _BASELINE_CATALOG,
) -> tuple[Path, Path, Path, Path]:
    sysfs = tmp_path / "sys"
    dev_block = sysfs / "dev" / "block"
    usb_subsystem = sysfs / "bus" / "usb"
    dev_block.mkdir(parents=True)
    usb_subsystem.mkdir(parents=True)

    mount_directory = tmp_path / "scanner"
    favorites_directory = (
        mount_directory
        / "BCDx36HP"
        / "favorites_lists"
    )
    favorites_directory.mkdir(parents=True)
    (
        favorites_directory
        / "f_list.cfg"
    ).write_bytes(catalog)

    status = mount_directory.stat()
    major = os.major(status.st_dev)
    minor = os.minor(status.st_dev)

    mountinfo = tmp_path / "mountinfo"
    _write_mountinfo(
        mountinfo,
        mount_id=900,
        mount_directory=mount_directory,
        device_major=major,
        device_minor=minor,
    )

    usb_device = (
        sysfs
        / "devices"
        / "pci0000:00"
        / "usb9"
        / "9-1"
    )
    partition = (
        usb_device
        / "block"
        / "sdz"
        / "sdz1"
    )
    partition.mkdir(parents=True)
    _symlink_or_skip(
        usb_device / "subsystem",
        usb_subsystem,
    )
    _symlink_or_skip(
        dev_block / f"{major}:{minor}",
        partition,
    )

    return (
        mountinfo,
        dev_block,
        mount_directory,
        favorites_directory,
    )


def test_public_preflight_reason_values_are_stable() -> None:
    assert tuple(
        reason.value
        for reason in FavoritesUsbWritePreflightReason
    ) == (
        "blocked_plan",
        "qualification_failed",
        "target_stale",
        "unsafe_tree",
    )


def test_usb_preflight_retains_exact_current_target_evidence(
    tmp_path: Path,
) -> None:
    (
        mountinfo,
        dev_block,
        mount_directory,
        favorites_directory,
    ) = _usb_write_fixture(
        tmp_path
    )
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(_CHANGED_CATALOG),
    )
    before = tuple(
        sorted(
            str(path.relative_to(tmp_path))
            for path in tmp_path.rglob("*")
        )
    )

    preflight = preflight_favorites_usb_write(
        plan,
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )

    after = tuple(
        sorted(
            str(path.relative_to(tmp_path))
            for path in tmp_path.rglob("*")
        )
    )
    assert isinstance(
        preflight,
        FavoritesUsbWritePreflight,
    )
    assert preflight.plan is plan
    assert preflight.requested_path == mount_directory
    assert preflight.mountinfo_path == mountinfo
    assert (
        preflight.sys_dev_block_directory
        == dev_block
    )
    assert (
        preflight.qualification.mount_directory
        == mount_directory
    )
    assert (
        preflight.qualification.favorites_directory
        == favorites_directory
    )
    assert (
        preflight.observed_snapshot
        == plan.baseline_snapshot
    )
    assert (
        preflight.tree_evidence
        == favorites_tree_evidence(
            favorites_directory
        )
    )
    assert not preflight.is_noop
    assert before == after
    assert not hasattr(
        preflight,
        "__dict__",
    )

    with pytest.raises(
        FrozenInstanceError,
    ):
        preflight.requested_path = Path("/")  # type: ignore[misc]


def test_noop_usb_preflight_is_read_only(
    tmp_path: Path,
) -> None:
    (
        mountinfo,
        dev_block,
        _,
        favorites_directory,
    ) = _usb_write_fixture(
        tmp_path
    )
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(),
    )
    before = tuple(
        sorted(
            str(path.relative_to(tmp_path))
            for path in tmp_path.rglob("*")
        )
    )

    preflight = preflight_favorites_usb_write(
        plan,
        favorites_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )

    after = tuple(
        sorted(
            str(path.relative_to(tmp_path))
            for path in tmp_path.rglob("*")
        )
    )
    assert preflight.is_noop
    assert before == after


def test_blocked_plan_is_refused_before_target_access(
    tmp_path: Path,
) -> None:
    plan = plan_favorites_write(
        _snapshot(),
        FavoritesStorageSnapshot(
            catalog_bytes=b"",
            documents=(),
        ),
    )
    missing = tmp_path / "missing-scanner"
    missing_mountinfo = tmp_path / "missing-mountinfo"
    missing_sysfs = tmp_path / "missing-sysfs"

    with pytest.raises(
        FavoritesUsbWritePreflightError,
    ) as raised:
        preflight_favorites_usb_write(
            plan,
            missing,
            missing_mountinfo,
            sys_dev_block_directory=missing_sysfs,
        )

    assert (
        raised.value.reason
        is FavoritesUsbWritePreflightReason.BLOCKED_PLAN
    )
    assert raised.value.path == missing
    assert raised.value.qualification_reason is None
    assert "intended_schema_error" in raised.value.message
    assert not missing.exists()
    assert not missing_mountinfo.exists()
    assert not missing_sysfs.exists()


def test_initial_usb_qualification_failure_retains_specific_reason(
    tmp_path: Path,
) -> None:
    (
        mountinfo,
        dev_block,
        mount_directory,
        _,
    ) = _usb_write_fixture(
        tmp_path
    )
    mountinfo.write_text(
        mountinfo.read_text(
            encoding="utf-8"
        ).replace(" rw ", " ro ").replace(
            " rw\n",
            " ro\n",
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        FavoritesUsbWritePreflightError,
    ) as raised:
        preflight_favorites_usb_write(
            plan_favorites_write(
                _snapshot(),
                _snapshot(_CHANGED_CATALOG),
            ),
            mount_directory,
            mountinfo,
            sys_dev_block_directory=dev_block,
        )

    assert (
        raised.value.reason
        is FavoritesUsbWritePreflightReason.QUALIFICATION_FAILED
    )
    assert (
        raised.value.qualification_reason
        is FavoritesUsbStorageQualificationReason.READ_ONLY_MOUNT
    )


def test_fresh_managed_snapshot_mismatch_is_stale(
    tmp_path: Path,
) -> None:
    (
        mountinfo,
        dev_block,
        mount_directory,
        _,
    ) = _usb_write_fixture(
        tmp_path,
        catalog=_CHANGED_CATALOG,
    )

    with pytest.raises(
        FavoritesUsbWritePreflightError,
    ) as raised:
        preflight_favorites_usb_write(
            plan_favorites_write(
                _snapshot(),
                _snapshot(_CHANGED_CATALOG),
            ),
            mount_directory,
            mountinfo,
            sys_dev_block_directory=dev_block,
        )

    assert (
        raised.value.reason
        is FavoritesUsbWritePreflightReason.TARGET_STALE
    )
    assert raised.value.qualification_reason is None
    assert "baseline" in raised.value.message


def test_unmanaged_symbolic_link_is_refused_as_unsafe_tree(
    tmp_path: Path,
) -> None:
    (
        mountinfo,
        dev_block,
        mount_directory,
        favorites_directory,
    ) = _usb_write_fixture(
        tmp_path
    )
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    link = favorites_directory / "unmanaged-link"
    _symlink_or_skip(
        link,
        outside,
    )

    with pytest.raises(
        FavoritesUsbWritePreflightError,
    ) as raised:
        preflight_favorites_usb_write(
            plan_favorites_write(
                _snapshot(),
                _snapshot(_CHANGED_CATALOG),
            ),
            mount_directory,
            mountinfo,
            sys_dev_block_directory=dev_block,
        )

    assert (
        raised.value.reason
        is FavoritesUsbWritePreflightReason.UNSAFE_TREE
    )
    assert raised.value.path == link
    assert "symbolic links" in raised.value.message


def test_requalification_detects_mount_becoming_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        mountinfo,
        dev_block,
        mount_directory,
        _,
    ) = _usb_write_fixture(
        tmp_path
    )
    real_tree_evidence = (
        write_usb.favorites_tree_evidence
    )
    calls = 0

    def change_mount_after_first_scan(
        root: Path,
    ) -> object:
        nonlocal calls
        evidence = real_tree_evidence(
            root
        )
        calls += 1
        if calls == 1:
            current = mountinfo.read_text(
                encoding="utf-8"
            )
            mountinfo.write_text(
                current.replace(
                    " rw ",
                    " ro ",
                ).replace(
                    " rw\n",
                    " ro\n",
                ),
                encoding="utf-8",
            )
        return evidence

    monkeypatch.setattr(
        write_usb,
        "favorites_tree_evidence",
        change_mount_after_first_scan,
    )

    with pytest.raises(
        FavoritesUsbWritePreflightError,
    ) as raised:
        preflight_favorites_usb_write(
            plan_favorites_write(
                _snapshot(),
                _snapshot(_CHANGED_CATALOG),
            ),
            mount_directory,
            mountinfo,
            sys_dev_block_directory=dev_block,
        )

    assert calls == 1
    assert (
        raised.value.reason
        is FavoritesUsbWritePreflightReason.TARGET_STALE
    )
    assert (
        raised.value.qualification_reason
        is FavoritesUsbStorageQualificationReason.READ_ONLY_MOUNT
    )


def test_complete_tree_change_during_preflight_is_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        mountinfo,
        dev_block,
        mount_directory,
        favorites_directory,
    ) = _usb_write_fixture(
        tmp_path
    )
    unmanaged = favorites_directory / "offline.bin"
    unmanaged.write_bytes(b"before")
    real_tree_evidence = (
        write_usb.favorites_tree_evidence
    )
    calls = 0

    def change_tree_between_scans(
        root: Path,
    ) -> object:
        nonlocal calls
        evidence = real_tree_evidence(
            root
        )
        calls += 1
        if calls == 1:
            unmanaged.write_bytes(
                b"after"
            )
        return evidence

    monkeypatch.setattr(
        write_usb,
        "favorites_tree_evidence",
        change_tree_between_scans,
    )

    with pytest.raises(
        FavoritesUsbWritePreflightError,
    ) as raised:
        preflight_favorites_usb_write(
            plan_favorites_write(
                _snapshot(),
                _snapshot(_CHANGED_CATALOG),
            ),
            mount_directory,
            mountinfo,
            sys_dev_block_directory=dev_block,
        )

    assert calls == 2
    assert (
        raised.value.reason
        is FavoritesUsbWritePreflightReason.TARGET_STALE
    )
    assert "complete-tree identity changed" in raised.value.message
