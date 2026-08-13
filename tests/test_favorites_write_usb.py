from __future__ import annotations

import json
import os
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from sds200 import favorites_write_usb as write_usb
from sds200.favorites_storage import (
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
)
from sds200.favorites_storage_evidence import (
    favorites_tree_evidence,
    favorites_unmanaged_tree_sha256,
)
from sds200.favorites_storage_local import FavoritesCopiedTreeStorageSource
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


def test_usb_target_lock_key_binds_mount_and_device_identity(
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
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(_CHANGED_CATALOG),
    )
    first = preflight_favorites_usb_write(
        plan,
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )

    first_key = (
        write_usb._usb_target_lock_key(
            first
        )
    )

    assert len(first_key) == 64
    assert first_key == write_usb._usb_target_lock_key(
        first
    )

    current = mountinfo.read_text(
        encoding="utf-8"
    )
    mountinfo.write_text(
        current.replace(
            "900 1 ",
            "901 1 ",
        ).replace(
            "/dev/test-900",
            "/dev/test-901",
        ),
        encoding="utf-8",
    )
    second = preflight_favorites_usb_write(
        plan,
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )

    assert (
        write_usb._usb_target_lock_key(
            second
        )
        != first_key
    )


def test_usb_operation_id_binds_plan_and_complete_tree(
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
    first = preflight_favorites_usb_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(_CHANGED_CATALOG),
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    first_id = (
        write_usb._usb_operation_id(
            first
        )
    )

    assert len(first_id) == 64
    assert first_id == write_usb._usb_operation_id(
        first
    )

    (
        favorites_directory
        / "unmanaged.bin"
    ).write_bytes(
        b"new unmanaged material"
    )
    second = preflight_favorites_usb_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(_CHANGED_CATALOG),
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )

    assert (
        write_usb._usb_operation_id(
            second
        )
        != first_id
    )


def test_usb_host_operation_paths_are_outside_scanner_storage(
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
    preflight = preflight_favorites_usb_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(_CHANGED_CATALOG),
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )

    paths = write_usb._usb_host_operation_paths(
        preflight,
        host_root,
    )

    assert paths.root_directory == host_root
    assert paths.locks_directory == (
        host_root / "locks"
    )
    assert paths.lock_directory.parent == (
        host_root / "locks"
    )
    assert paths.operations_directory == (
        host_root / "operations"
    )
    assert paths.operation_directory == (
        paths.operations_directory
        / paths.operation_id
    )
    assert paths.backup_directory == (
        paths.operation_directory
        / "backup"
    )
    assert paths.staging_directory == (
        paths.operation_directory
        / "staging"
    )
    assert paths.rollback_manifest_path == (
        paths.operation_directory
        / "rollback.json"
    )
    assert paths.operation_report_path == (
        paths.operation_directory
        / "report.json"
    )
    assert paths.failure_report_path == (
        paths.operation_directory
        / "failure.json"
    )
    assert not host_root.exists()


def test_usb_host_operation_paths_reject_scanner_volume(
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
    preflight = preflight_favorites_usb_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(_CHANGED_CATALOG),
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    unsafe = (
        mount_directory
        / ".sdsctl-state"
    )

    with pytest.raises(
        write_usb._FavoritesUsbWritePreparationError,
        match="outside scanner storage",
    ):
        write_usb._usb_host_operation_paths(
            preflight,
            unsafe,
        )

    assert not unsafe.exists()


def test_usb_host_operation_lock_is_private_and_exclusive(
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
    preflight = preflight_favorites_usb_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(_CHANGED_CATALOG),
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        assert paths.root_directory.is_dir()
        assert paths.locks_directory.is_dir()
        assert paths.lock_directory.is_dir()
        assert (
            paths.root_directory.stat().st_mode
            & 0o777
        ) == 0o700
        assert (
            paths.locks_directory.stat().st_mode
            & 0o777
        ) == 0o700
        assert (
            paths.lock_directory.stat().st_mode
            & 0o777
        ) == 0o700

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="already be active",
        ), write_usb._usb_host_operation_lock(
            preflight,
            host_root,
        ):
            pytest.fail(
                "second USB host operation lock unexpectedly succeeded"
            )

    assert not paths.lock_directory.exists()
    assert paths.root_directory.is_dir()
    assert paths.locks_directory.is_dir()
    assert not paths.operations_directory.exists()


def test_existing_usb_host_operation_lock_fails_closed(
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
    preflight = preflight_favorites_usb_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(_CHANGED_CATALOG),
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )
    paths = write_usb._usb_host_operation_paths(
        preflight,
        host_root,
    )
    paths.lock_directory.mkdir(
        parents=True
    )

    with pytest.raises(
        write_usb._FavoritesUsbWritePreparationError,
        match="already be active",
    ), write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ):
        pytest.fail(
            "existing USB host lock unexpectedly allowed another operation"
        )

    assert paths.lock_directory.is_dir()


def test_usb_host_operation_lock_detects_disappearance(
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
    preflight = preflight_favorites_usb_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(_CHANGED_CATALOG),
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )

    with pytest.raises(
        write_usb._FavoritesUsbWritePreparationError,
        match="disappeared before release",
    ), write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        paths.lock_directory.rmdir()


def test_usb_host_state_root_rejects_symlink(
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
    preflight = preflight_favorites_usb_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(_CHANGED_CATALOG),
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    real_root = tmp_path / "real-host-root"
    real_root.mkdir()
    alias = tmp_path / "host-root-alias"
    _symlink_or_skip(
        alias,
        real_root,
    )

    with pytest.raises(
        write_usb._FavoritesUsbWritePreparationError,
        match="canonical",
    ):
        write_usb._usb_host_operation_paths(
            preflight,
            alias,
        )


def test_verified_usb_host_backup_preserves_complete_tree(
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
    unmanaged = (
        favorites_directory
        / "nested"
        / "offline.bin"
    )
    unmanaged.parent.mkdir()
    unmanaged.write_bytes(
        b"unmanaged"
    )
    preflight = preflight_favorites_usb_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(_CHANGED_CATALOG),
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )
    before = favorites_tree_evidence(
        favorites_directory
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )

    after = favorites_tree_evidence(
        favorites_directory
    )

    assert before == preflight.tree_evidence
    assert after == preflight.tree_evidence
    assert backup.directory == paths.backup_directory
    assert backup.directory.is_dir()
    assert (
        backup.tree_evidence.sha256
        == preflight.tree_evidence.sha256
    )
    assert (
        backup.snapshot
        == preflight.observed_snapshot
    )
    assert (
        backup.directory
        / "nested"
        / "offline.bin"
    ).read_bytes() == b"unmanaged"
    assert (
        paths.operation_directory.stat().st_mode
        & 0o777
    ) == 0o700
    assert backup.directory.exists()
    assert not paths.lock_directory.exists()


def test_usb_host_backup_requires_active_target_lock(
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
    preflight = preflight_favorites_usb_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(_CHANGED_CATALOG),
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    paths = write_usb._usb_host_operation_paths(
        preflight,
        (
            tmp_path
            / "host-state"
            / "favorites-usb-writes"
        ),
    )

    with pytest.raises(
        write_usb._FavoritesUsbWritePreparationError,
        match="required USB host operation lock",
    ):
        write_usb._create_verified_usb_host_backup(
            preflight,
            paths,
        )

    assert not paths.operation_directory.exists()
    assert not paths.backup_directory.exists()


def test_existing_usb_host_operation_workspace_fails_closed(
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
    preflight = preflight_favorites_usb_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(_CHANGED_CATALOG),
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        paths.operation_directory.mkdir(
            parents=True
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="workspace already exists",
        ):
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )

    assert paths.operation_directory.is_dir()


def test_usb_host_backup_detects_source_change_during_copy(
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
    unmanaged = (
        favorites_directory
        / "unmanaged.bin"
    )
    unmanaged.write_bytes(
        b"before"
    )
    preflight = preflight_favorites_usb_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(_CHANGED_CATALOG),
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )
    real_copytree = write_usb.shutil.copytree

    def changing_copytree(
        src: Path,
        dst: Path,
        **kwargs: object,
    ) -> Path:
        result = real_copytree(
            src,
            dst,
            **kwargs,
        )
        unmanaged.write_bytes(
            b"after"
        )
        return result

    monkeypatch.setattr(
        write_usb.shutil,
        "copytree",
        changing_copytree,
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths, pytest.raises(
        write_usb._FavoritesUsbWritePreparationError,
        match="content or structure changed",
    ):
        write_usb._create_verified_usb_host_backup(
            preflight,
            paths,
        )

    assert paths.backup_directory.exists()
    assert unmanaged.read_bytes() == b"after"


def test_usb_host_backup_detects_read_only_transition_during_copy(
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
    preflight = preflight_favorites_usb_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(_CHANGED_CATALOG),
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )
    real_copytree = write_usb.shutil.copytree

    def changing_mount_copytree(
        src: Path,
        dst: Path,
        **kwargs: object,
    ) -> Path:
        result = real_copytree(
            src,
            dst,
            **kwargs,
        )
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
        return result

    monkeypatch.setattr(
        write_usb.shutil,
        "copytree",
        changing_mount_copytree,
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths, pytest.raises(
        write_usb._FavoritesUsbWritePreparationError,
        match="read_only_mount",
    ):
        write_usb._create_verified_usb_host_backup(
            preflight,
            paths,
        )

    assert paths.backup_directory.exists()


def test_usb_host_backup_detects_corrupted_host_copy(
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
    (
        favorites_directory
        / "unmanaged.bin"
    ).write_bytes(
        b"original"
    )
    preflight = preflight_favorites_usb_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(_CHANGED_CATALOG),
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )
    real_copytree = write_usb.shutil.copytree

    def corrupting_copytree(
        src: Path,
        dst: Path,
        **kwargs: object,
    ) -> Path:
        result = real_copytree(
            src,
            dst,
            **kwargs,
        )
        (
            dst
            / "unmanaged.bin"
        ).write_bytes(
            b"corrupt"
        )
        return result

    monkeypatch.setattr(
        write_usb.shutil,
        "copytree",
        corrupting_copytree,
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths, pytest.raises(
        write_usb._FavoritesUsbWritePreparationError,
        match="does not exactly match preflight tree evidence",
    ):
        write_usb._create_verified_usb_host_backup(
            preflight,
            paths,
        )

    assert (
        favorites_directory
        / "unmanaged.bin"
    ).read_bytes() == b"original"
    assert paths.backup_directory.exists()


def test_verified_usb_host_staging_uses_backup_and_preserves_unmanaged_material(
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
    nested = (
        favorites_directory
        / "attachments"
    )
    nested.mkdir()
    nested.chmod(0o750)
    unmanaged = (
        nested
        / "offline.bin"
    )
    unmanaged.write_bytes(
        b"preserve exactly"
    )
    unmanaged.chmod(0o640)

    preflight = preflight_favorites_usb_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(_CHANGED_CATALOG),
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )
    active_before = favorites_tree_evidence(
        favorites_directory
    )
    copy_sources: list[Path] = []
    real_copytree = write_usb.shutil.copytree

    def recording_copytree(
        src: Path,
        dst: Path,
        *args: object,
        **kwargs: object,
    ) -> Path:
        if Path(dst) == paths.staging_directory:
            copy_sources.append(
                Path(src)
            )
        return real_copytree(
            src,
            dst,
            *args,
            **kwargs,
        )

    monkeypatch.setattr(
        write_usb.shutil,
        "copytree",
        recording_copytree,
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        copy_sources.clear()
        prepared = (
            write_usb._create_verified_usb_host_staging(
                preflight,
                paths,
                backup,
            )
        )

    assert copy_sources == [
        backup.directory
    ]
    assert (
        prepared.snapshot
        == preflight.plan.intended_snapshot
    )
    assert (
        prepared.directory
        / "f_list.cfg"
    ).read_bytes() == _CHANGED_CATALOG
    assert (
        prepared.directory
        / "attachments"
        / "offline.bin"
    ).read_bytes() == b"preserve exactly"
    assert (
        (
            prepared.directory
            / "attachments"
        ).stat().st_mode
        & 0o777
    ) == 0o750
    assert (
        (
            prepared.directory
            / "attachments"
            / "offline.bin"
        ).stat().st_mode
        & 0o777
    ) == 0o640
    assert (
        favorites_tree_evidence(
            favorites_directory
        )
        == active_before
    )
    assert (
        favorites_tree_evidence(
            backup.directory
        )
        == backup.tree_evidence
    )


def test_usb_host_staging_refuses_existing_destination(
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
    preflight = preflight_favorites_usb_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(_CHANGED_CATALOG),
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        paths.staging_directory.mkdir()

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="staging destination already exists",
        ):
            write_usb._create_verified_usb_host_staging(
                preflight,
                paths,
                backup,
            )


def test_usb_host_staging_refuses_changed_verified_backup(
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
    preflight = preflight_favorites_usb_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(_CHANGED_CATALOG),
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        (
            backup.directory
            / "unmanaged.bin"
        ).write_bytes(
            b"changed"
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="backup changed after verification",
        ):
            write_usb._create_verified_usb_host_staging(
                preflight,
                paths,
                backup,
            )

    assert not paths.staging_directory.exists()


def test_usb_host_staging_detects_backup_change_during_copy(
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
    preflight = preflight_favorites_usb_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(_CHANGED_CATALOG),
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        real_copytree = write_usb.shutil.copytree

        def changing_copytree(
            src: Path,
            dst: Path,
            **kwargs: object,
        ) -> Path:
            result = real_copytree(
                src,
                dst,
                **kwargs,
            )
            (
                backup.directory
                / "changed.bin"
            ).write_bytes(
                b"changed"
            )
            return result

        monkeypatch.setattr(
            write_usb.shutil,
            "copytree",
            changing_copytree,
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="backup changed after verification",
        ):
            write_usb._create_verified_usb_host_staging(
                preflight,
                paths,
                backup,
            )

    assert paths.staging_directory.exists()


def test_usb_host_staging_readback_rejects_corruption(
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
    preflight = preflight_favorites_usb_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(_CHANGED_CATALOG),
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )
    active_before = favorites_tree_evidence(
        favorites_directory
    )
    real_write = (
        write_usb._write_usb_host_staged_regular_file
    )

    def corrupting_write(
        path: Path,
        content: bytes,
    ) -> None:
        real_write(
            path,
            content,
        )
        if path.name == "f_list.cfg":
            path.write_bytes(
                b"corrupt"
            )

    monkeypatch.setattr(
        write_usb,
        "_write_usb_host_staged_regular_file",
        corrupting_write,
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="exact intended snapshot",
        ):
            write_usb._create_verified_usb_host_staging(
                preflight,
                paths,
                backup,
            )

    assert (
        favorites_tree_evidence(
            favorites_directory
        )
        == active_before
    )
    assert (
        favorites_tree_evidence(
            backup.directory
        )
        == backup.tree_evidence
    )


def test_usb_preactivation_revalidates_active_backup_and_staging(
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
    (
        favorites_directory
        / "unmanaged.bin"
    ).write_bytes(
        b"preserve"
    )
    preflight = preflight_favorites_usb_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(_CHANGED_CATALOG),
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )
    active_before = favorites_tree_evidence(
        favorites_directory
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        prepared = (
            write_usb._create_verified_usb_host_staging(
                preflight,
                paths,
                backup,
            )
        )
        ready = write_usb._require_usb_preactivation_ready(
            preflight,
            paths,
            backup,
            prepared,
        )

    assert (
        ready.active_tree_evidence
        == preflight.tree_evidence
    )
    assert (
        ready.backup_tree_evidence
        == backup.tree_evidence
    )
    assert (
        ready.staging_tree_evidence
        == prepared.tree_evidence
    )
    assert (
        favorites_tree_evidence(
            favorites_directory
        )
        == active_before
    )
    assert (
        FavoritesCopiedTreeStorageSource(
            favorites_directory
        ).read_snapshot()
        == preflight.observed_snapshot
    )


def test_usb_preactivation_refuses_changed_active_tree(
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
    unmanaged = (
        favorites_directory
        / "unmanaged.bin"
    )
    unmanaged.write_bytes(
        b"before"
    )
    preflight = preflight_favorites_usb_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(_CHANGED_CATALOG),
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        prepared = (
            write_usb._create_verified_usb_host_staging(
                preflight,
                paths,
                backup,
            )
        )
        unmanaged.write_bytes(
            b"after"
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="content or structure changed",
        ):
            write_usb._require_usb_preactivation_ready(
                preflight,
                paths,
                backup,
                prepared,
            )

    assert unmanaged.read_bytes() == b"after"


def test_usb_preactivation_refuses_read_only_transition(
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
    preflight = preflight_favorites_usb_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(_CHANGED_CATALOG),
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        prepared = (
            write_usb._create_verified_usb_host_staging(
                preflight,
                paths,
                backup,
            )
        )
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

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="read_only_mount",
        ):
            write_usb._require_usb_preactivation_ready(
                preflight,
                paths,
                backup,
                prepared,
            )


def test_usb_preactivation_refuses_changed_verified_backup(
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
    preflight = preflight_favorites_usb_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(_CHANGED_CATALOG),
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        prepared = (
            write_usb._create_verified_usb_host_staging(
                preflight,
                paths,
                backup,
            )
        )
        (
            backup.directory
            / "changed.bin"
        ).write_bytes(
            b"changed"
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="backup changed after verification",
        ):
            write_usb._require_usb_preactivation_ready(
                preflight,
                paths,
                backup,
                prepared,
            )


def test_usb_preactivation_refuses_changed_verified_staging(
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
    preflight = preflight_favorites_usb_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(_CHANGED_CATALOG),
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        prepared = (
            write_usb._create_verified_usb_host_staging(
                preflight,
                paths,
                backup,
            )
        )
        (
            prepared.directory
            / "changed.bin"
        ).write_bytes(
            b"changed"
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="staging content or structure changed",
        ):
            write_usb._require_usb_preactivation_ready(
                preflight,
                paths,
                backup,
                prepared,
            )


def test_usb_preactivation_active_target_check_is_final(
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
    preflight = preflight_favorites_usb_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(_CHANGED_CATALOG),
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        prepared = (
            write_usb._create_verified_usb_host_staging(
                preflight,
                paths,
                backup,
            )
        )
        calls: list[str] = []
        real_backup = (
            write_usb._require_verified_usb_host_backup_current
        )
        real_staging = (
            write_usb._require_verified_usb_host_staging_current
        )
        real_active = (
            write_usb._require_current_usb_preflight_target
        )

        def checked_backup(
            preflight_value: FavoritesUsbWritePreflight,
            paths_value: object,
            backup_value: object,
        ) -> object:
            calls.append("backup")
            return real_backup(
                preflight_value,
                paths_value,
                backup_value,
            )

        def checked_staging(
            preflight_value: FavoritesUsbWritePreflight,
            paths_value: object,
            prepared_value: object,
        ) -> object:
            calls.append("staging")
            return real_staging(
                preflight_value,
                paths_value,
                prepared_value,
            )

        def checked_active(
            preflight_value: FavoritesUsbWritePreflight,
        ) -> object:
            calls.append("active")
            return real_active(
                preflight_value
            )

        monkeypatch.setattr(
            write_usb,
            "_require_verified_usb_host_backup_current",
            checked_backup,
        )
        monkeypatch.setattr(
            write_usb,
            "_require_verified_usb_host_staging_current",
            checked_staging,
        )
        monkeypatch.setattr(
            write_usb,
            "_require_current_usb_preflight_target",
            checked_active,
        )

        write_usb._require_usb_preactivation_ready(
            preflight,
            paths,
            backup,
            prepared,
        )

    assert calls == [
        "backup",
        "staging",
        "active",
    ]


def test_usb_managed_activation_plan_catalog_only_change(
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
    before = favorites_tree_evidence(
        favorites_directory
    )
    preflight = preflight_favorites_usb_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(_CHANGED_CATALOG),
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )

    activation = (
        write_usb._usb_managed_activation_plan(
            preflight
        )
    )

    assert activation.document_writes == ()
    assert activation.write_catalog is True
    assert activation.document_deletions == ()
    assert not activation.is_noop
    assert (
        favorites_tree_evidence(
            favorites_directory
        )
        == before
    )


def test_usb_managed_activation_plan_orders_hpd_writes_before_catalog_and_deletes(
    tmp_path: Path,
) -> None:
    hpd_old = (
        b"TargetModel\tBCDx36HP\r\n"
        b"FormatVersion\t1.00\r\n"
        b"Department\tOld\r\n"
    )
    hpd_updated = (
        b"TargetModel\tBCDx36HP\r\n"
        b"FormatVersion\t1.00\r\n"
        b"Department\tUpdated\r\n"
    )
    hpd_added = (
        b"TargetModel\tBCDx36HP\r\n"
        b"FormatVersion\t1.00\r\n"
        b"Department\tAdded\r\n"
    )
    (
        mountinfo,
        dev_block,
        mount_directory,
        favorites_directory,
    ) = _usb_write_fixture(
        tmp_path
    )
    (
        favorites_directory
        / "keep.hpd"
    ).write_bytes(
        hpd_old
    )
    (
        favorites_directory
        / "remove.hpd"
    ).write_bytes(
        hpd_old
    )

    baseline = FavoritesStorageSnapshot(
        catalog_bytes=_BASELINE_CATALOG,
        documents=(
            FavoritesStorageDocument(
                filename="keep.hpd",
                content=hpd_old,
            ),
            FavoritesStorageDocument(
                filename="remove.hpd",
                content=hpd_old,
            ),
        ),
    )
    intended = FavoritesStorageSnapshot(
        catalog_bytes=_CHANGED_CATALOG,
        documents=(
            FavoritesStorageDocument(
                filename="keep.hpd",
                content=hpd_updated,
            ),
            FavoritesStorageDocument(
                filename="added.hpd",
                content=hpd_added,
            ),
        ),
    )
    plan = plan_favorites_write(
        baseline,
        intended,
    )
    assert not plan.is_blocked
    preflight = preflight_favorites_usb_write(
        plan,
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )

    activation = (
        write_usb._usb_managed_activation_plan(
            preflight
        )
    )

    assert activation.document_writes == (
        "keep.hpd",
        "added.hpd",
    )
    assert activation.write_catalog is True
    assert activation.document_deletions == (
        "remove.hpd",
    )


def test_usb_managed_activation_plan_does_not_rewrite_unchanged_hpd(
    tmp_path: Path,
) -> None:
    hpd = (
        b"TargetModel\tBCDx36HP\r\n"
        b"FormatVersion\t1.00\r\n"
        b"Department\tStable\r\n"
    )
    (
        mountinfo,
        dev_block,
        mount_directory,
        favorites_directory,
    ) = _usb_write_fixture(
        tmp_path
    )
    (
        favorites_directory
        / "stable.hpd"
    ).write_bytes(
        hpd
    )

    baseline = FavoritesStorageSnapshot(
        catalog_bytes=_BASELINE_CATALOG,
        documents=(
            FavoritesStorageDocument(
                filename="stable.hpd",
                content=hpd,
            ),
        ),
    )
    intended = FavoritesStorageSnapshot(
        catalog_bytes=_CHANGED_CATALOG,
        documents=(
            FavoritesStorageDocument(
                filename="stable.hpd",
                content=hpd,
            ),
        ),
    )
    plan = plan_favorites_write(
        baseline,
        intended,
    )
    assert not plan.is_blocked
    preflight = preflight_favorites_usb_write(
        plan,
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )

    activation = (
        write_usb._usb_managed_activation_plan(
            preflight
        )
    )

    assert activation.document_writes == ()
    assert activation.write_catalog is True
    assert activation.document_deletions == ()


def test_usb_managed_activation_plan_noop_has_no_steps(
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
    preflight = preflight_favorites_usb_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(),
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )

    activation = (
        write_usb._usb_managed_activation_plan(
            preflight
        )
    )

    assert activation.document_writes == ()
    assert activation.write_catalog is False
    assert activation.document_deletions == ()
    assert activation.is_noop


def _prepared_usb_activation_fixture(
    tmp_path: Path,
    *,
    baseline: FavoritesStorageSnapshot,
    intended: FavoritesStorageSnapshot,
) -> tuple[
    FavoritesUsbWritePreflight,
    Path,
    Path,
]:
    (
        mountinfo,
        dev_block,
        mount_directory,
        favorites_directory,
    ) = _usb_write_fixture(
        tmp_path,
        catalog=baseline.catalog_bytes,
    )

    for document in baseline.documents:
        (
            favorites_directory
            / document.filename
        ).write_bytes(
            document.content
        )

    preflight = preflight_favorites_usb_write(
        plan_favorites_write(
            baseline,
            intended,
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )

    return (
        preflight,
        mountinfo,
        favorites_directory,
    )


def test_usb_active_file_replace_creates_new_hpd_from_verified_operation(
    tmp_path: Path,
) -> None:
    hpd = (
        b"TargetModel\tBCDx36HP\r\n"
        b"FormatVersion\t1.00\r\n"
        b"Department\tAdded\r\n"
    )
    baseline = _snapshot()
    intended = FavoritesStorageSnapshot(
        catalog_bytes=_BASELINE_CATALOG,
        documents=(
            FavoritesStorageDocument(
                filename="added.hpd",
                content=hpd,
            ),
        ),
    )
    (
        preflight,
        _,
        favorites_directory,
    ) = _prepared_usb_activation_fixture(
        tmp_path,
        baseline=baseline,
        intended=intended,
    )
    unmanaged = (
        favorites_directory
        / "unmanaged.bin"
    )
    unmanaged.write_bytes(
        b"preserve"
    )

    # The unmanaged file was added after preflight only to prove this primitive
    # does not touch unrelated names; the low-level primitive intentionally does
    # not substitute for the final preactivation gate.
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        write_usb._replace_usb_active_managed_file(
            preflight,
            paths,
            "added.hpd",
            hpd,
        )
        temporary = (
            write_usb._usb_media_temporary_path(
                preflight,
                paths,
            )
        )

    assert (
        favorites_directory
        / "added.hpd"
    ).read_bytes() == hpd
    assert unmanaged.read_bytes() == b"preserve"
    assert not temporary.exists()


def test_usb_active_file_replace_replaces_catalog_and_preserves_mode(
    tmp_path: Path,
) -> None:
    baseline = _snapshot()
    intended = _snapshot(
        _CHANGED_CATALOG
    )
    (
        preflight,
        _,
        favorites_directory,
    ) = _prepared_usb_activation_fixture(
        tmp_path,
        baseline=baseline,
        intended=intended,
    )
    catalog = (
        favorites_directory
        / "f_list.cfg"
    )
    catalog.chmod(
        0o640
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        write_usb._replace_usb_active_managed_file(
            preflight,
            paths,
            "f_list.cfg",
            _CHANGED_CATALOG,
        )

    assert catalog.read_bytes() == _CHANGED_CATALOG
    assert (
        catalog.stat().st_mode
        & 0o777
    ) == 0o640


def test_usb_active_file_replace_refuses_unsupported_filesystem(
    tmp_path: Path,
) -> None:
    baseline = _snapshot()
    intended = _snapshot(
        _CHANGED_CATALOG
    )
    (
        preflight,
        mountinfo,
        favorites_directory,
    ) = _prepared_usb_activation_fixture(
        tmp_path,
        baseline=baseline,
        intended=intended,
    )
    mountinfo.write_text(
        mountinfo.read_text(
            encoding="utf-8"
        ).replace(
            " - vfat ",
            " - ext4 ",
        ),
        encoding="utf-8",
    )
    unsupported = preflight_favorites_usb_write(
        preflight.plan,
        preflight.requested_path,
        mountinfo,
        sys_dev_block_directory=(
            preflight.sys_dev_block_directory
        ),
    )
    before = (
        favorites_directory
        / "f_list.cfg"
    ).read_bytes()
    host_root = (
        tmp_path
        / "host-state-unsupported"
        / "favorites-usb-writes"
    )

    with (
        write_usb._usb_host_operation_lock(
            unsupported,
            host_root,
        ) as paths,
        pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="does not support mounted filesystem",
        ),
    ):
        write_usb._replace_usb_active_managed_file(
            unsupported,
            paths,
            "f_list.cfg",
            _CHANGED_CATALOG,
        )

    assert (
        favorites_directory
        / "f_list.cfg"
    ).read_bytes() == before


def test_usb_active_file_replace_refuses_existing_temp_artifact(
    tmp_path: Path,
) -> None:
    baseline = _snapshot()
    intended = _snapshot(
        _CHANGED_CATALOG
    )
    (
        preflight,
        _,
        favorites_directory,
    ) = _prepared_usb_activation_fixture(
        tmp_path,
        baseline=baseline,
        intended=intended,
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        temporary = (
            write_usb._usb_media_temporary_path(
                preflight,
                paths,
            )
        )
        temporary.write_bytes(
            b"existing unmanaged collision"
        )

        with pytest.raises(
            write_usb._FavoritesUsbMediaMutationError,
        ) as raised:
            write_usb._replace_usb_active_managed_file(
                preflight,
                paths,
                "f_list.cfg",
                _CHANGED_CATALOG,
            )

    assert raised.value.mutation_started is False
    assert temporary.read_bytes() == b"existing unmanaged collision"
    assert (
        favorites_directory
        / "f_list.cfg"
    ).read_bytes() == _BASELINE_CATALOG


def test_usb_active_file_replace_refuses_symlink_race_without_writing(
    tmp_path: Path,
) -> None:
    hpd = (
        b"TargetModel\tBCDx36HP\r\n"
        b"FormatVersion\t1.00\r\n"
    )
    baseline = _snapshot()
    intended = FavoritesStorageSnapshot(
        catalog_bytes=_BASELINE_CATALOG,
        documents=(
            FavoritesStorageDocument(
                filename="added.hpd",
                content=hpd,
            ),
        ),
    )
    (
        preflight,
        _,
        favorites_directory,
    ) = _prepared_usb_activation_fixture(
        tmp_path,
        baseline=baseline,
        intended=intended,
    )
    outside = tmp_path / "outside.hpd"
    outside.write_bytes(
        b"outside"
    )
    target = (
        favorites_directory
        / "added.hpd"
    )
    _symlink_or_skip(
        target,
        outside,
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )

    with (
        write_usb._usb_host_operation_lock(
            preflight,
            host_root,
        ) as paths,
        pytest.raises(
            write_usb._FavoritesUsbMediaMutationError,
            match="must not be a symbolic link",
        ) as raised,
    ):
        write_usb._replace_usb_active_managed_file(
            preflight,
            paths,
            "added.hpd",
            hpd,
        )

    assert raised.value.mutation_started is False
    assert outside.read_bytes() == b"outside"
    assert target.is_symlink()


def test_usb_active_file_replace_surfaces_post_replace_readback_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _snapshot()
    intended = _snapshot(
        _CHANGED_CATALOG
    )
    (
        preflight,
        _,
        favorites_directory,
    ) = _prepared_usb_activation_fixture(
        tmp_path,
        baseline=baseline,
        intended=intended,
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )
    real_read = (
        write_usb._read_usb_activation_regular_file
    )
    calls = 0

    def failing_second_read(
        path: Path,
    ) -> bytes:
        nonlocal calls
        calls += 1
        content = real_read(
            path
        )
        if calls == 2:
            return b"corrupted-readback"
        return content

    monkeypatch.setattr(
        write_usb,
        "_read_usb_activation_regular_file",
        failing_second_read,
    )

    with (
        write_usb._usb_host_operation_lock(
            preflight,
            host_root,
        ) as paths,
        pytest.raises(
            write_usb._FavoritesUsbMediaMutationError,
            match="failed exact readback",
        ) as raised,
    ):
        write_usb._replace_usb_active_managed_file(
            preflight,
            paths,
            "f_list.cfg",
            _CHANGED_CATALOG,
        )

    assert raised.value.mutation_started is True
    assert (
        favorites_directory
        / "f_list.cfg"
    ).read_bytes() == _CHANGED_CATALOG

    assert raised.value.recovery_artifact is None


def test_usb_active_hpd_delete_removes_exact_file_and_preserves_unmanaged(
    tmp_path: Path,
) -> None:
    hpd = (
        b"TargetModel\tBCDx36HP\r\n"
        b"FormatVersion\t1.00\r\n"
        b"Department\tRemove\r\n"
    )
    baseline = FavoritesStorageSnapshot(
        catalog_bytes=_BASELINE_CATALOG,
        documents=(
            FavoritesStorageDocument(
                filename="remove.hpd",
                content=hpd,
            ),
        ),
    )
    intended = _snapshot()
    (
        preflight,
        _,
        favorites_directory,
    ) = _prepared_usb_activation_fixture(
        tmp_path,
        baseline=baseline,
        intended=intended,
    )
    unmanaged = (
        favorites_directory
        / "unmanaged.bin"
    )
    unmanaged.write_bytes(
        b"preserve"
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        write_usb._delete_usb_active_managed_hpd(
            preflight,
            paths,
            "remove.hpd",
            hpd,
        )
        temporary = (
            write_usb._usb_media_temporary_path(
                preflight,
                paths,
            )
        )

    assert not (
        favorites_directory
        / "remove.hpd"
    ).exists()
    assert unmanaged.read_bytes() == b"preserve"
    assert not temporary.exists()


def test_usb_active_hpd_delete_refuses_unexpected_content_without_mutation(
    tmp_path: Path,
) -> None:
    hpd = (
        b"TargetModel\tBCDx36HP\r\n"
        b"FormatVersion\t1.00\r\n"
    )
    baseline = FavoritesStorageSnapshot(
        catalog_bytes=_BASELINE_CATALOG,
        documents=(
            FavoritesStorageDocument(
                filename="remove.hpd",
                content=hpd,
            ),
        ),
    )
    intended = _snapshot()
    (
        preflight,
        _,
        favorites_directory,
    ) = _prepared_usb_activation_fixture(
        tmp_path,
        baseline=baseline,
        intended=intended,
    )
    target = (
        favorites_directory
        / "remove.hpd"
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )

    with (
        write_usb._usb_host_operation_lock(
            preflight,
            host_root,
        ) as paths,
        pytest.raises(
            write_usb._FavoritesUsbMediaMutationError,
            match="expected exact content",
        ) as raised,
    ):
        write_usb._delete_usb_active_managed_hpd(
            preflight,
            paths,
            "remove.hpd",
            b"different",
        )

    assert raised.value.mutation_started is False
    assert target.read_bytes() == hpd


def test_usb_active_hpd_delete_refuses_symlink_without_mutation(
    tmp_path: Path,
) -> None:
    baseline = _snapshot()
    intended = _snapshot()
    (
        preflight,
        _,
        favorites_directory,
    ) = _prepared_usb_activation_fixture(
        tmp_path,
        baseline=baseline,
        intended=intended,
    )
    outside = tmp_path / "outside.hpd"
    outside.write_bytes(
        b"outside"
    )
    target = (
        favorites_directory
        / "remove.hpd"
    )
    _symlink_or_skip(
        target,
        outside,
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )

    with (
        write_usb._usb_host_operation_lock(
            preflight,
            host_root,
        ) as paths,
        pytest.raises(
            write_usb._FavoritesUsbMediaMutationError,
            match="must not be a symbolic link",
        ) as raised,
    ):
        write_usb._delete_usb_active_managed_hpd(
            preflight,
            paths,
            "remove.hpd",
            b"outside",
        )

    assert raised.value.mutation_started is False
    assert target.is_symlink()
    assert outside.read_bytes() == b"outside"


def test_usb_active_hpd_delete_refuses_existing_temp_artifact(
    tmp_path: Path,
) -> None:
    hpd = (
        b"TargetModel\tBCDx36HP\r\n"
        b"FormatVersion\t1.00\r\n"
    )
    baseline = FavoritesStorageSnapshot(
        catalog_bytes=_BASELINE_CATALOG,
        documents=(
            FavoritesStorageDocument(
                filename="remove.hpd",
                content=hpd,
            ),
        ),
    )
    intended = _snapshot()
    (
        preflight,
        _,
        favorites_directory,
    ) = _prepared_usb_activation_fixture(
        tmp_path,
        baseline=baseline,
        intended=intended,
    )
    target = (
        favorites_directory
        / "remove.hpd"
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        temporary = (
            write_usb._usb_media_temporary_path(
                preflight,
                paths,
            )
        )
        temporary.write_bytes(
            b"collision"
        )

        with pytest.raises(
            write_usb._FavoritesUsbMediaMutationError,
            match="temporary artifact already exists",
        ) as raised:
            write_usb._delete_usb_active_managed_hpd(
                preflight,
                paths,
                "remove.hpd",
                hpd,
            )

    assert raised.value.mutation_started is False
    assert target.read_bytes() == hpd
    assert temporary.read_bytes() == b"collision"


def test_usb_active_hpd_delete_restores_when_post_move_verification_disagrees(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hpd = (
        b"TargetModel\tBCDx36HP\r\n"
        b"FormatVersion\t1.00\r\n"
    )
    baseline = FavoritesStorageSnapshot(
        catalog_bytes=_BASELINE_CATALOG,
        documents=(
            FavoritesStorageDocument(
                filename="remove.hpd",
                content=hpd,
            ),
        ),
    )
    intended = _snapshot()
    (
        preflight,
        _,
        favorites_directory,
    ) = _prepared_usb_activation_fixture(
        tmp_path,
        baseline=baseline,
        intended=intended,
    )
    target = (
        favorites_directory
        / "remove.hpd"
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )
    real_read = (
        write_usb._read_usb_activation_regular_file
    )
    calls = 0

    def disagree_after_move(
        path: Path,
    ) -> bytes:
        nonlocal calls
        calls += 1
        content = real_read(
            path
        )
        if calls == 2:
            return b"unexpected"
        return content

    monkeypatch.setattr(
        write_usb,
        "_read_usb_activation_regular_file",
        disagree_after_move,
    )

    with (
        write_usb._usb_host_operation_lock(
            preflight,
            host_root,
        ) as paths,
        pytest.raises(
            write_usb._FavoritesUsbMediaMutationError,
            match="post-move verification",
        ) as raised,
    ):
        write_usb._delete_usb_active_managed_hpd(
            preflight,
            paths,
            "remove.hpd",
            hpd,
        )

    assert raised.value.mutation_started is True
    assert target.read_bytes() == hpd

    assert raised.value.recovery_artifact is None


def test_usb_active_hpd_delete_retains_bounded_artifact_when_unlink_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hpd = (
        b"TargetModel\tBCDx36HP\r\n"
        b"FormatVersion\t1.00\r\n"
    )
    baseline = FavoritesStorageSnapshot(
        catalog_bytes=_BASELINE_CATALOG,
        documents=(
            FavoritesStorageDocument(
                filename="remove.hpd",
                content=hpd,
            ),
        ),
    )
    intended = _snapshot()
    (
        preflight,
        _,
        favorites_directory,
    ) = _prepared_usb_activation_fixture(
        tmp_path,
        baseline=baseline,
        intended=intended,
    )
    target = (
        favorites_directory
        / "remove.hpd"
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )
    real_unlink = Path.unlink
    bounded: Path | None = None

    def failing_bounded_unlink(
        path: Path,
        *args: object,
        **kwargs: object,
    ) -> None:
        if (
            path.name.startswith(
                write_usb._USB_MEDIA_TEMP_PREFIX
            )
            and path.parent == favorites_directory
        ):
            raise OSError(
                "injected bounded-artifact unlink failure"
            )
        real_unlink(
            path,
            *args,
            **kwargs,
        )

    monkeypatch.setattr(
        Path,
        "unlink",
        failing_bounded_unlink,
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        bounded = (
            write_usb._usb_media_temporary_path(
                preflight,
                paths,
            )
        )
        with pytest.raises(
            write_usb._FavoritesUsbMediaMutationError,
            match="finalize active HPD deletion",
        ) as raised:
            write_usb._delete_usb_active_managed_hpd(
                preflight,
                paths,
                "remove.hpd",
                hpd,
            )

    assert raised.value.mutation_started is True
    assert not target.exists()
    assert bounded is not None
    assert bounded.read_bytes() == hpd

    artifact = raised.value.recovery_artifact
    assert artifact is not None
    assert artifact.path == bounded
    assert artifact.managed_filename == "remove.hpd"
    assert artifact.content_sha256 == (
        write_usb._usb_media_content_sha256(
            hpd
        )
    )


def test_usb_preflight_retains_exact_unmanaged_tree_identity(
    tmp_path: Path,
) -> None:
    (
        mountinfo,
        dev_block,
        mount_directory,
        favorites_directory,
    ) = _usb_write_fixture(tmp_path)
    nested = favorites_directory / "attachments"
    nested.mkdir()
    (nested / "offline.bin").write_bytes(b"preserve")
    (favorites_directory / "ONE.HPD").write_bytes(b"case-variant-unmanaged")

    preflight = preflight_favorites_usb_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(_CHANGED_CATALOG),
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )

    assert preflight.unmanaged_sha256 == favorites_unmanaged_tree_sha256(
        favorites_directory
    )
    assert len(preflight.unmanaged_sha256) == 64


def test_usb_preflight_refuses_unstable_unmanaged_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        mountinfo,
        dev_block,
        mount_directory,
        _,
    ) = _usb_write_fixture(tmp_path)
    real_unmanaged = write_usb.favorites_unmanaged_tree_sha256
    calls = 0

    def unstable_unmanaged(root: Path) -> str:
        nonlocal calls
        digest = real_unmanaged(root)
        calls += 1
        if calls == 2:
            return "0" * 64 if digest != "0" * 64 else "1" * 64
        return digest

    monkeypatch.setattr(
        write_usb,
        "favorites_unmanaged_tree_sha256",
        unstable_unmanaged,
    )

    with pytest.raises(
        FavoritesUsbWritePreflightError,
        match="unmanaged content or structure changed",
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

    assert raised.value.reason is FavoritesUsbWritePreflightReason.TARGET_STALE


def test_usb_preparation_binds_unmanaged_identity_across_active_backup_and_stage(
    tmp_path: Path,
) -> None:
    (
        mountinfo,
        dev_block,
        mount_directory,
        favorites_directory,
    ) = _usb_write_fixture(tmp_path)
    nested = favorites_directory / "attachments"
    nested.mkdir()
    (nested / "offline.bin").write_bytes(b"preserve")
    (favorites_directory / "ONE.HPD").write_bytes(b"case-variant-unmanaged")

    preflight = preflight_favorites_usb_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(_CHANGED_CATALOG),
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = tmp_path / "host-state" / "favorites-usb-writes"

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = write_usb._create_verified_usb_host_backup(
            preflight,
            paths,
        )
        prepared = write_usb._create_verified_usb_host_staging(
            preflight,
            paths,
            backup,
        )
        ready = write_usb._require_usb_preactivation_ready(
            preflight,
            paths,
            backup,
            prepared,
        )

    expected = preflight.unmanaged_sha256
    assert backup.unmanaged_sha256 == expected
    assert prepared.unmanaged_sha256 == expected
    assert ready.unmanaged_sha256 == expected
    assert favorites_unmanaged_tree_sha256(favorites_directory) == expected
    assert favorites_unmanaged_tree_sha256(backup.directory) == expected
    assert favorites_unmanaged_tree_sha256(prepared.directory) == expected


def test_usb_verified_backup_revalidation_checks_unmanaged_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        mountinfo,
        dev_block,
        mount_directory,
        _,
    ) = _usb_write_fixture(tmp_path)
    preflight = preflight_favorites_usb_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(_CHANGED_CATALOG),
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = tmp_path / "host-state" / "favorites-usb-writes"

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = write_usb._create_verified_usb_host_backup(
            preflight,
            paths,
        )
        real_unmanaged = write_usb.favorites_unmanaged_tree_sha256

        def mismatching_backup_digest(root: Path) -> str:
            digest = real_unmanaged(root)
            if root == backup.directory:
                return "0" * 64 if digest != "0" * 64 else "1" * 64
            return digest

        monkeypatch.setattr(
            write_usb,
            "favorites_unmanaged_tree_sha256",
            mismatching_backup_digest,
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="backup unmanaged tree identity changed",
        ):
            write_usb._require_verified_usb_host_backup_current(
                preflight,
                paths,
                backup,
            )


def test_usb_verified_staging_revalidation_checks_unmanaged_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        mountinfo,
        dev_block,
        mount_directory,
        _,
    ) = _usb_write_fixture(tmp_path)
    preflight = preflight_favorites_usb_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(_CHANGED_CATALOG),
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = tmp_path / "host-state" / "favorites-usb-writes"

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = write_usb._create_verified_usb_host_backup(
            preflight,
            paths,
        )
        prepared = write_usb._create_verified_usb_host_staging(
            preflight,
            paths,
            backup,
        )
        real_unmanaged = write_usb.favorites_unmanaged_tree_sha256

        def mismatching_stage_digest(root: Path) -> str:
            digest = real_unmanaged(root)
            if root == prepared.directory:
                return "0" * 64 if digest != "0" * 64 else "1" * 64
            return digest

        monkeypatch.setattr(
            write_usb,
            "favorites_unmanaged_tree_sha256",
            mismatching_stage_digest,
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="staging unmanaged tree identity changed",
        ):
            write_usb._require_verified_usb_host_staging_current(
                preflight,
                paths,
                prepared,
            )


def test_usb_recovery_target_accepts_managed_divergence_and_owned_temp(
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
    unmanaged = (
        favorites_directory
        / "offline.bin"
    )
    unmanaged.write_bytes(
        b"preserve"
    )

    preflight = preflight_favorites_usb_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(
                _CHANGED_CATALOG
            ),
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )

        (
            favorites_directory
            / "f_list.cfg"
        ).write_bytes(
            b"partially-activated-managed-catalog"
        )

        temporary = (
            write_usb._usb_media_temporary_path(
                preflight,
                paths,
            )
        )
        temporary.write_bytes(
            b"operation-owned-displaced-managed-content"
        )

        def forbidden_public_qualification(
            *args: object,
            **kwargs: object,
        ) -> object:
            raise AssertionError(
                "Post-mutation recovery must not call "
                "public storage qualification."
            )

        monkeypatch.setattr(
            write_usb,
            "qualify_favorites_usb_storage_path",
            forbidden_public_qualification,
        )

        evidence = (
            write_usb._require_usb_recovery_target_ready(
                preflight,
                paths,
                backup,
            )
        )

    assert (
        evidence.mount
        == preflight.qualification.mount
    )
    assert (
        evidence.block_device
        == preflight.qualification.block_device
    )
    assert (
        evidence.mount_directory
        == preflight.qualification.mount_directory
    )
    assert (
        evidence.favorites_directory
        == preflight.qualification.favorites_directory
    )
    assert (
        evidence.unmanaged_sha256
        == preflight.unmanaged_sha256
    )
    assert (
        evidence.temporary_path
        == temporary
    )
    assert temporary.read_bytes() == (
        b"operation-owned-displaced-managed-content"
    )
    assert unmanaged.read_bytes() == b"preserve"


def test_usb_recovery_target_refuses_unexpected_unmanaged_change(
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
    unmanaged = (
        favorites_directory
        / "offline.bin"
    )
    unmanaged.write_bytes(
        b"before"
    )

    preflight = preflight_favorites_usb_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(
                _CHANGED_CATALOG
            ),
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        unmanaged.write_bytes(
            b"after"
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="unmanaged changes beyond",
        ):
            write_usb._require_usb_recovery_target_ready(
                preflight,
                paths,
                backup,
            )


def test_usb_recovery_target_refuses_symlink_at_owned_temp_name(
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

    preflight = preflight_favorites_usb_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(
                _CHANGED_CATALOG
            ),
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )
    outside = (
        tmp_path
        / "outside.bin"
    )
    outside.write_bytes(
        b"outside"
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        temporary = (
            write_usb._usb_media_temporary_path(
                preflight,
                paths,
            )
        )
        temporary.symlink_to(
            outside
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="symbolic links",
        ):
            write_usb._require_usb_recovery_target_ready(
                preflight,
                paths,
                backup,
            )

    assert outside.read_bytes() == b"outside"


def test_usb_recovery_target_refuses_read_only_transition(
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

    preflight = preflight_favorites_usb_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(
                _CHANGED_CATALOG
            ),
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )

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

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="no longer writable",
        ):
            write_usb._require_usb_recovery_target_ready(
                preflight,
                paths,
                backup,
            )


def test_usb_recovery_target_refuses_block_device_evidence_change(
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

    preflight = preflight_favorites_usb_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(
                _CHANGED_CATALOG
            ),
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )
    real_reader = (
        write_usb.read_linux_block_device_evidence
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )

        def changed_block_device(
            device_major: int,
            device_minor: int,
            *,
            sys_dev_block_directory: Path,
        ) -> object:
            evidence = real_reader(
                device_major,
                device_minor,
                sys_dev_block_directory=(
                    sys_dev_block_directory
                ),
            )
            return (
                write_usb.LinuxBlockDeviceEvidence(
                    device_major=(
                        evidence.device_major
                    ),
                    device_minor=(
                        evidence.device_minor
                    ),
                    sysfs_path=(
                        evidence.sysfs_path
                    ),
                    device_name=(
                        evidence.device_name
                    ),
                    usb_ancestor_path=(
                        evidence.usb_ancestor_path
                    ),
                    removable=(
                        not evidence.removable
                        if evidence.removable
                        is not None
                        else True
                    ),
                )
            )

        monkeypatch.setattr(
            write_usb,
            "read_linux_block_device_evidence",
            changed_block_device,
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="block-device evidence changed",
        ):
            write_usb._require_usb_recovery_target_ready(
                preflight,
                paths,
                backup,
            )


def test_usb_recovery_target_revalidates_verified_backup(
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

    preflight = preflight_favorites_usb_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(
                _CHANGED_CATALOG
            ),
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        (
            backup.directory
            / "changed.bin"
        ).write_bytes(
            b"changed"
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="backup changed after verification",
        ):
            write_usb._require_usb_recovery_target_ready(
                preflight,
                paths,
                backup,
            )


def test_usb_recovery_plan_restores_baseline_and_removes_only_introduced_hpd() -> None:
    baseline = write_usb.FavoritesStorageSnapshot(
        catalog_bytes=b"baseline-catalog",
        documents=(
            write_usb.FavoritesStorageDocument(
                filename="zeta.hpd",
                content=b"baseline-zeta",
            ),
            write_usb.FavoritesStorageDocument(
                filename="alpha.hpd",
                content=b"baseline-alpha",
            ),
            write_usb.FavoritesStorageDocument(
                filename="updated.hpd",
                content=b"baseline-updated",
            ),
        ),
    )
    intended = write_usb.FavoritesStorageSnapshot(
        catalog_bytes=b"intended-catalog",
        documents=(
            write_usb.FavoritesStorageDocument(
                filename="updated.hpd",
                content=b"intended-updated",
            ),
            write_usb.FavoritesStorageDocument(
                filename="added.hpd",
                content=b"intended-added",
            ),
            write_usb.FavoritesStorageDocument(
                filename="zeta.hpd",
                content=b"baseline-zeta",
            ),
        ),
    )

    recovery = write_usb._build_usb_recovery_plan(
        baseline,
        intended,
    )

    assert tuple(
        document.filename
        for document in recovery.restore_documents
    ) == (
        "alpha.hpd",
        "updated.hpd",
        "zeta.hpd",
    )
    assert tuple(
        document.content
        for document in recovery.restore_documents
    ) == (
        b"baseline-alpha",
        b"baseline-updated",
        b"baseline-zeta",
    )
    assert (
        recovery.restore_catalog_bytes
        == b"baseline-catalog"
    )
    assert recovery.remove_documents == (
        write_usb.FavoritesStorageDocument(
            filename="added.hpd",
            content=b"intended-added",
        ),
    )


def test_usb_recovery_plan_never_removes_updated_or_baseline_deleted_hpd() -> None:
    baseline = write_usb.FavoritesStorageSnapshot(
        catalog_bytes=b"baseline",
        documents=(
            write_usb.FavoritesStorageDocument(
                filename="updated.hpd",
                content=b"old",
            ),
            write_usb.FavoritesStorageDocument(
                filename="deleted-by-intended.hpd",
                content=b"restore-me",
            ),
        ),
    )
    intended = write_usb.FavoritesStorageSnapshot(
        catalog_bytes=b"intended",
        documents=(
            write_usb.FavoritesStorageDocument(
                filename="updated.hpd",
                content=b"new",
            ),
        ),
    )

    recovery = write_usb._build_usb_recovery_plan(
        baseline,
        intended,
    )

    assert {
        document.filename
        for document in recovery.restore_documents
    } == {
        "updated.hpd",
        "deleted-by-intended.hpd",
    }
    assert recovery.remove_documents == ()


def test_usb_recovery_plan_removal_retains_exact_intended_content() -> None:
    baseline = write_usb.FavoritesStorageSnapshot(
        catalog_bytes=b"baseline",
        documents=(),
    )
    intended = write_usb.FavoritesStorageSnapshot(
        catalog_bytes=b"intended",
        documents=(
            write_usb.FavoritesStorageDocument(
                filename="new.hpd",
                content=b"exact-intended-bytes",
            ),
        ),
    )

    recovery = write_usb._build_usb_recovery_plan(
        baseline,
        intended,
    )

    assert recovery.remove_documents == (
        write_usb.FavoritesStorageDocument(
            filename="new.hpd",
            content=b"exact-intended-bytes",
        ),
    )


def test_usb_recovery_plan_rejects_unsupported_document_name() -> None:
    baseline = write_usb.FavoritesStorageSnapshot(
        catalog_bytes=b"baseline",
        documents=(
            write_usb.FavoritesStorageDocument(
                filename="notes.txt",
                content=b"unsupported",
            ),
        ),
    )
    intended = write_usb.FavoritesStorageSnapshot(
        catalog_bytes=b"intended",
        documents=(),
    )

    with pytest.raises(
        ValueError,
        match="lowercase-.hpd",
    ):
        write_usb._build_usb_recovery_plan(
            baseline,
            intended,
        )


def test_usb_recovery_plan_binds_verified_backup_baseline_without_media_mutation(
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
    preflight = preflight_favorites_usb_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(
                _CHANGED_CATALOG
            ),
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = write_usb._create_verified_usb_host_backup(
            preflight,
            paths,
        )
        before = favorites_tree_evidence(
            favorites_directory
        )

        recovery = write_usb._usb_recovery_plan(
            preflight,
            backup,
        )

        after = favorites_tree_evidence(
            favorites_directory
        )

    assert (
        recovery.restore_catalog_bytes
        == preflight.plan.baseline_snapshot.catalog_bytes
    )
    assert recovery.restore_documents == tuple(
        sorted(
            preflight.plan.baseline_snapshot.documents,
            key=lambda document: document.filename.encode(
                "utf-8"
            ),
        )
    )
    assert recovery.remove_documents == ()
    assert after == before


def test_usb_recovery_plan_rejects_backup_snapshot_not_exact_baseline(
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
    preflight = preflight_favorites_usb_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(
                _CHANGED_CATALOG
            ),
        ),
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = write_usb._create_verified_usb_host_backup(
            preflight,
            paths,
        )
        mismatched = write_usb._FavoritesUsbVerifiedBackup(
            directory=backup.directory,
            tree_evidence=backup.tree_evidence,
            unmanaged_sha256=backup.unmanaged_sha256,
            snapshot=preflight.plan.intended_snapshot,
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="exact write-plan baseline",
        ):
            write_usb._usb_recovery_plan(
                preflight,
                mismatched,
            )


def test_usb_media_recovery_artifact_rejects_catalog_identity(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        ValueError,
        match="lowercase-.hpd",
    ):
        write_usb._FavoritesUsbMediaRecoveryArtifact(
            path=(
                tmp_path.resolve()
                / ".sds200-usb-write-owned.tmp"
            ),
            managed_filename="f_list.cfg",
            content_sha256="0" * 64,
        )


def test_usb_media_recovery_artifact_rejects_invalid_digest(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        ValueError,
        match="SHA-256",
    ):
        write_usb._FavoritesUsbMediaRecoveryArtifact(
            path=(
                tmp_path.resolve()
                / ".sds200-usb-write-owned.tmp"
            ),
            managed_filename="owned.hpd",
            content_sha256="not-a-digest",
        )


def test_usb_media_mutation_error_rejects_invalid_recovery_artifact(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        TypeError,
        match="recovery artifact",
    ):
        write_usb._FavoritesUsbMediaMutationError(
            tmp_path.resolve(),
            "failure",
            mutation_started=True,
            recovery_artifact=object(),  # type: ignore[arg-type]
        )


def _usb_recovery_artifact_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[
    write_usb.FavoritesUsbWritePreflight,
    write_usb._FavoritesUsbHostOperationPaths,
    write_usb._FavoritesUsbVerifiedBackup,
    write_usb._FavoritesUsbMediaRecoveryArtifact,
    Path,
    bytes,
    object,
]:
    hpd = (
        b"TargetModel\tBCDx36HP\r\n"
        b"FormatVersion\t1.00\r\n"
        b"Department\tRecovery artifact\r\n"
    )
    baseline = FavoritesStorageSnapshot(
        catalog_bytes=_BASELINE_CATALOG,
        documents=(
            FavoritesStorageDocument(
                filename="remove.hpd",
                content=hpd,
            ),
        ),
    )
    intended = _snapshot()
    (
        preflight,
        _,
        favorites_directory,
    ) = _prepared_usb_activation_fixture(
        tmp_path,
        baseline=baseline,
        intended=intended,
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )

    lock = write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    )
    paths = lock.__enter__()
    backup = write_usb._create_verified_usb_host_backup(
        preflight,
        paths,
    )

    temporary = (
        write_usb._usb_media_temporary_path(
            preflight,
            paths,
        )
    )
    real_unlink = Path.unlink

    def failing_owned_unlink(
        path: Path,
        *args: object,
        **kwargs: object,
    ) -> None:
        if path == temporary:
            raise OSError(
                "injected surviving recovery artifact"
            )
        real_unlink(
            path,
            *args,
            **kwargs,
        )

    monkeypatch.setattr(
        Path,
        "unlink",
        failing_owned_unlink,
    )

    with pytest.raises(
        write_usb._FavoritesUsbMediaMutationError,
        match="finalize active HPD deletion",
    ) as raised:
        write_usb._delete_usb_active_managed_hpd(
            preflight,
            paths,
            "remove.hpd",
            hpd,
        )

    artifact = raised.value.recovery_artifact
    assert artifact is not None
    assert not (
        favorites_directory
        / "remove.hpd"
    ).exists()
    assert temporary.read_bytes() == hpd

    return (
        preflight,
        paths,
        backup,
        artifact,
        temporary,
        hpd,
        lock,
    )


def test_usb_recovery_artifact_binding_accepts_exact_baseline_deletion_survivor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        backup,
        artifact,
        temporary,
        hpd,
        lock,
    ) = _usb_recovery_artifact_fixture(
        tmp_path,
        monkeypatch,
    )

    try:
        verified = (
            write_usb._require_current_usb_recovery_artifact(
                preflight,
                paths,
                backup,
                artifact,
            )
        )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )

    assert verified.artifact == artifact
    assert (
        verified.target_evidence.temporary_path
        == temporary
    )
    assert verified.baseline_document == (
        FavoritesStorageDocument(
            filename="remove.hpd",
            content=hpd,
        )
    )
    assert verified.device == temporary.stat().st_dev
    assert verified.inode == temporary.stat().st_ino
    assert verified.size == len(hpd)
    assert temporary.read_bytes() == hpd


def test_usb_recovery_artifact_binding_refuses_wrong_operation_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        backup,
        artifact,
        temporary,
        _,
        lock,
    ) = _usb_recovery_artifact_fixture(
        tmp_path,
        monkeypatch,
    )
    wrong = write_usb._FavoritesUsbMediaRecoveryArtifact(
        path=temporary.with_name(
            ".sds200-usb-write-wrong.tmp"
        ),
        managed_filename=artifact.managed_filename,
        content_sha256=artifact.content_sha256,
    )

    try:
        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="exact operation-owned temporary path",
        ):
            write_usb._require_current_usb_recovery_artifact(
                preflight,
                paths,
                backup,
                wrong,
            )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )


def test_usb_recovery_artifact_binding_refuses_non_deletion_phase_filename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        backup,
        artifact,
        temporary,
        _,
        lock,
    ) = _usb_recovery_artifact_fixture(
        tmp_path,
        monkeypatch,
    )
    wrong = write_usb._FavoritesUsbMediaRecoveryArtifact(
        path=temporary,
        managed_filename="other.hpd",
        content_sha256=artifact.content_sha256,
    )

    try:
        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="does not identify a baseline HPD",
        ):
            write_usb._require_current_usb_recovery_artifact(
                preflight,
                paths,
                backup,
                wrong,
            )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )


def test_usb_recovery_artifact_binding_refuses_provenance_hash_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        backup,
        artifact,
        temporary,
        _,
        lock,
    ) = _usb_recovery_artifact_fixture(
        tmp_path,
        monkeypatch,
    )
    wrong = write_usb._FavoritesUsbMediaRecoveryArtifact(
        path=temporary,
        managed_filename=artifact.managed_filename,
        content_sha256="0" * 64,
    )

    try:
        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="provenance SHA-256",
        ):
            write_usb._require_current_usb_recovery_artifact(
                preflight,
                paths,
                backup,
                wrong,
            )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )


def test_usb_recovery_artifact_binding_refuses_current_content_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        backup,
        artifact,
        temporary,
        _,
        lock,
    ) = _usb_recovery_artifact_fixture(
        tmp_path,
        monkeypatch,
    )
    temporary.write_bytes(
        b"changed after provenance"
    )

    try:
        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="exact baseline HPD",
        ):
            write_usb._require_current_usb_recovery_artifact(
                preflight,
                paths,
                backup,
                artifact,
            )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )


def test_usb_recovery_artifact_binding_refuses_symlink_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        backup,
        artifact,
        temporary,
        hpd,
        lock,
    ) = _usb_recovery_artifact_fixture(
        tmp_path,
        monkeypatch,
    )
    # The fixture intentionally leaves Path.unlink monkeypatched so the
    # verified deletion artifact survives. Bypass that injected failure only
    # for this test setup before replacing the exact temp path with a symlink.
    write_usb.os.unlink(
        temporary
    )
    outside = tmp_path / "outside-recovery-artifact"
    outside.write_bytes(
        hpd
    )
    _symlink_or_skip(
        temporary,
        outside,
    )

    try:
        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="symbolic link",
        ):
            write_usb._require_current_usb_recovery_artifact(
                preflight,
                paths,
                backup,
                artifact,
            )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )

    assert outside.read_bytes() == hpd


def test_usb_recovery_artifact_binding_revalidates_backup_and_target_after_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        backup,
        artifact,
        _,
        _,
        lock,
    ) = _usb_recovery_artifact_fixture(
        tmp_path,
        monkeypatch,
    )

    real_backup = (
        write_usb._require_verified_usb_host_backup_current
    )
    real_target = (
        write_usb._require_usb_recovery_target_ready
    )
    real_read = (
        write_usb._read_usb_activation_regular_file
    )
    calls: list[str] = []

    def tracked_backup(
        current_preflight: write_usb.FavoritesUsbWritePreflight,
        current_paths: write_usb._FavoritesUsbHostOperationPaths,
        current_backup: write_usb._FavoritesUsbVerifiedBackup,
    ) -> object:
        calls.append(
            "backup"
        )
        return real_backup(
            current_preflight,
            current_paths,
            current_backup,
        )

    def tracked_target(
        current_preflight: write_usb.FavoritesUsbWritePreflight,
        current_paths: write_usb._FavoritesUsbHostOperationPaths,
        current_backup: write_usb._FavoritesUsbVerifiedBackup,
    ) -> write_usb._FavoritesUsbRecoveryTargetEvidence:
        calls.append(
            "target"
        )
        return real_target(
            current_preflight,
            current_paths,
            current_backup,
        )

    def tracked_read(
        path: Path,
    ) -> bytes:
        calls.append(
            "read"
        )
        return real_read(
            path
        )

    monkeypatch.setattr(
        write_usb,
        "_require_verified_usb_host_backup_current",
        tracked_backup,
    )
    monkeypatch.setattr(
        write_usb,
        "_require_usb_recovery_target_ready",
        tracked_target,
    )
    monkeypatch.setattr(
        write_usb,
        "_read_usb_activation_regular_file",
        tracked_read,
    )

    try:
        write_usb._require_current_usb_recovery_artifact(
            preflight,
            paths,
            backup,
            artifact,
        )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )

    assert calls.count("target") == 2
    assert calls.count("backup") >= 6
    assert calls.count("read") == 2

    first_read = calls.index("read")
    second_target = calls.index(
        "target",
        calls.index("target") + 1,
    )
    second_read = calls.index(
        "read",
        first_read + 1,
    )

    assert first_read < second_target < second_read
    assert "backup" in calls[
        first_read + 1:
        second_target
    ]


_GUARDED_BASELINE_HPD = (
    b"TargetModel\tBCDx36HP\r\n"
    b"FormatVersion\t1.00\r\n"
    b"Department\tGuarded baseline\r\n"
)
_GUARDED_INTENDED_HPD = (
    b"TargetModel\tBCDx36HP\r\n"
    b"FormatVersion\t1.00\r\n"
    b"Department\tGuarded intended\r\n"
)


def _usb_guarded_restore_fixture(
    tmp_path: Path,
    *,
    baseline_content: bytes,
    intended_content: bytes | None,
) -> tuple[
    write_usb.FavoritesUsbWritePreflight,
    Path,
    Path,
]:
    baseline = FavoritesStorageSnapshot(
        catalog_bytes=_BASELINE_CATALOG,
        documents=(
            FavoritesStorageDocument(
                filename="restore.hpd",
                content=baseline_content,
            ),
        ),
    )
    if intended_content is None:
        intended = _snapshot(
            _CHANGED_CATALOG
        )
    else:
        intended = FavoritesStorageSnapshot(
            catalog_bytes=_CHANGED_CATALOG,
            documents=(
                FavoritesStorageDocument(
                    filename="restore.hpd",
                    content=intended_content,
                ),
            ),
        )

    (
        preflight,
        _,
        favorites_directory,
    ) = _prepared_usb_activation_fixture(
        tmp_path,
        baseline=baseline,
        intended=intended,
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )

    return (
        preflight,
        favorites_directory,
        host_root,
    )


def test_usb_guarded_recovery_restore_creates_absent_target_without_consuming_temp(
    tmp_path: Path,
) -> None:
    baseline_content = _GUARDED_BASELINE_HPD
    (
        preflight,
        favorites_directory,
        host_root,
    ) = _usb_guarded_restore_fixture(
        tmp_path,
        baseline_content=baseline_content,
        intended_content=None,
    )
    target = (
        favorites_directory
        / "restore.hpd"
    )
    target.unlink()

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        temporary = (
            write_usb._usb_media_temporary_path(
                preflight,
                paths,
            )
        )
        temporary.write_bytes(
            b"verified surviving deletion artifact"
        )

        write_usb._restore_usb_active_managed_file(
            preflight,
            paths,
            "restore.hpd",
            baseline_content,
            allowed_existing_content=None,
            allow_absent=True,
        )

    assert target.read_bytes() == baseline_content
    assert temporary.read_bytes() == (
        b"verified surviving deletion artifact"
    )


def test_usb_guarded_recovery_restore_refuses_absent_target_when_disallowed(
    tmp_path: Path,
) -> None:
    baseline_content = _GUARDED_BASELINE_HPD
    (
        preflight,
        favorites_directory,
        host_root,
    ) = _usb_guarded_restore_fixture(
        tmp_path,
        baseline_content=baseline_content,
        intended_content=baseline_content,
    )
    target = (
        favorites_directory
        / "restore.hpd"
    )
    target.unlink()

    with (
        write_usb._usb_host_operation_lock(
            preflight,
            host_root,
        ) as paths,
        pytest.raises(
            write_usb._FavoritesUsbMediaMutationError,
            match="absence is not allowed",
        ) as raised,
    ):
        write_usb._restore_usb_active_managed_file(
            preflight,
            paths,
            "restore.hpd",
            baseline_content,
            allowed_existing_content=baseline_content,
            allow_absent=False,
        )

    assert raised.value.mutation_started is False
    assert not target.exists()


def test_usb_guarded_recovery_restore_exact_baseline_is_noop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline_content = _GUARDED_BASELINE_HPD
    intended_content = _GUARDED_INTENDED_HPD
    (
        preflight,
        favorites_directory,
        host_root,
    ) = _usb_guarded_restore_fixture(
        tmp_path,
        baseline_content=baseline_content,
        intended_content=intended_content,
    )
    target = (
        favorites_directory
        / "restore.hpd"
    )

    def forbidden_ftruncate(
        descriptor: int,
        length: int,
    ) -> None:
        raise AssertionError(
            "exact baseline recovery must not truncate"
        )

    monkeypatch.setattr(
        write_usb.os,
        "ftruncate",
        forbidden_ftruncate,
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        write_usb._restore_usb_active_managed_file(
            preflight,
            paths,
            "restore.hpd",
            baseline_content,
            allowed_existing_content=intended_content,
            allow_absent=False,
        )

    assert target.read_bytes() == baseline_content


def test_usb_guarded_recovery_restore_rewrites_exact_allowed_existing_content(
    tmp_path: Path,
) -> None:
    baseline_content = _GUARDED_BASELINE_HPD
    intended_content = _GUARDED_INTENDED_HPD
    (
        preflight,
        favorites_directory,
        host_root,
    ) = _usb_guarded_restore_fixture(
        tmp_path,
        baseline_content=baseline_content,
        intended_content=intended_content,
    )
    target = (
        favorites_directory
        / "restore.hpd"
    )
    target.write_bytes(
        intended_content
    )
    target.chmod(
        0o640
    )
    before_inode = target.stat().st_ino

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        write_usb._restore_usb_active_managed_file(
            preflight,
            paths,
            "restore.hpd",
            baseline_content,
            allowed_existing_content=intended_content,
            allow_absent=False,
        )

    assert target.read_bytes() == baseline_content
    assert target.stat().st_ino == before_inode
    assert (
        target.stat().st_mode
        & 0o777
    ) == 0o640


def test_usb_guarded_recovery_restore_refuses_unknown_existing_content(
    tmp_path: Path,
) -> None:
    baseline_content = _GUARDED_BASELINE_HPD
    intended_content = _GUARDED_INTENDED_HPD
    unknown = b"external concurrent content"
    (
        preflight,
        favorites_directory,
        host_root,
    ) = _usb_guarded_restore_fixture(
        tmp_path,
        baseline_content=baseline_content,
        intended_content=intended_content,
    )
    target = (
        favorites_directory
        / "restore.hpd"
    )
    target.write_bytes(
        unknown
    )

    with (
        write_usb._usb_host_operation_lock(
            preflight,
            host_root,
        ) as paths,
        pytest.raises(
            write_usb._FavoritesUsbMediaMutationError,
            match="explicitly allowed operation-known state",
        ) as raised,
    ):
        write_usb._restore_usb_active_managed_file(
            preflight,
            paths,
            "restore.hpd",
            baseline_content,
            allowed_existing_content=intended_content,
            allow_absent=False,
        )

    assert raised.value.mutation_started is False
    assert target.read_bytes() == unknown


def test_usb_guarded_recovery_restore_refuses_symlink_without_mutation(
    tmp_path: Path,
) -> None:
    baseline_content = _GUARDED_BASELINE_HPD
    intended_content = _GUARDED_INTENDED_HPD
    (
        preflight,
        favorites_directory,
        host_root,
    ) = _usb_guarded_restore_fixture(
        tmp_path,
        baseline_content=baseline_content,
        intended_content=intended_content,
    )
    target = (
        favorites_directory
        / "restore.hpd"
    )
    target.unlink()
    outside = tmp_path / "outside-guarded-restore"
    outside.write_bytes(
        intended_content
    )
    _symlink_or_skip(
        target,
        outside,
    )

    with (
        write_usb._usb_host_operation_lock(
            preflight,
            host_root,
        ) as paths,
        pytest.raises(
            write_usb._FavoritesUsbMediaMutationError,
            match="symbolic link",
        ) as raised,
    ):
        write_usb._restore_usb_active_managed_file(
            preflight,
            paths,
            "restore.hpd",
            baseline_content,
            allowed_existing_content=intended_content,
            allow_absent=False,
        )

    assert raised.value.mutation_started is False
    assert target.is_symlink()
    assert outside.read_bytes() == intended_content


def test_usb_guarded_recovery_restore_refuses_concurrent_path_replacement_before_truncate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline_content = _GUARDED_BASELINE_HPD
    intended_content = _GUARDED_INTENDED_HPD
    concurrent = b"concurrent replacement"
    (
        preflight,
        favorites_directory,
        host_root,
    ) = _usb_guarded_restore_fixture(
        tmp_path,
        baseline_content=baseline_content,
        intended_content=intended_content,
    )
    target = (
        favorites_directory
        / "restore.hpd"
    )
    target.write_bytes(
        intended_content
    )

    real_open = write_usb.os.open
    replaced = False

    def replacing_open(
        path: str | bytes | Path,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal replaced
        if (
            Path(path) == target
            and flags & write_usb.os.O_RDWR
            == write_usb.os.O_RDWR
            and not replaced
        ):
            replaced = True
            target.unlink()
            target.write_bytes(
                concurrent
            )

        if dir_fd is None:
            return real_open(
                path,
                flags,
                mode,
            )
        return real_open(
            path,
            flags,
            mode,
            dir_fd=dir_fd,
        )

    monkeypatch.setattr(
        write_usb.os,
        "open",
        replacing_open,
    )

    with (
        write_usb._usb_host_operation_lock(
            preflight,
            host_root,
        ) as paths,
        pytest.raises(
            write_usb._FavoritesUsbMediaMutationError,
            match="changed while being opened",
        ) as raised,
    ):
        write_usb._restore_usb_active_managed_file(
            preflight,
            paths,
            "restore.hpd",
            baseline_content,
            allowed_existing_content=intended_content,
            allow_absent=False,
        )

    assert replaced
    assert raised.value.mutation_started is False
    assert target.read_bytes() == concurrent


def test_usb_guarded_recovery_restore_refuses_same_inode_content_change_before_truncate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline_content = _GUARDED_BASELINE_HPD
    intended_content = _GUARDED_INTENDED_HPD
    concurrent = b"same inode changed content"
    (
        preflight,
        favorites_directory,
        host_root,
    ) = _usb_guarded_restore_fixture(
        tmp_path,
        baseline_content=baseline_content,
        intended_content=intended_content,
    )
    target = (
        favorites_directory
        / "restore.hpd"
    )
    target.write_bytes(
        intended_content
    )
    original_inode = target.stat().st_ino

    real_open = write_usb.os.open
    changed = False

    def changing_open(
        path: str | bytes | Path,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal changed
        if (
            Path(path) == target
            and flags & write_usb.os.O_RDWR
            == write_usb.os.O_RDWR
            and not changed
        ):
            changed = True
            target.write_bytes(
                concurrent
            )
            assert target.stat().st_ino == original_inode

        if dir_fd is None:
            return real_open(
                path,
                flags,
                mode,
            )
        return real_open(
            path,
            flags,
            mode,
            dir_fd=dir_fd,
        )

    monkeypatch.setattr(
        write_usb.os,
        "open",
        changing_open,
    )

    with (
        write_usb._usb_host_operation_lock(
            preflight,
            host_root,
        ) as paths,
        pytest.raises(
            write_usb._FavoritesUsbMediaMutationError,
            match="changed while being opened|no longer contains",
        ) as raised,
    ):
        write_usb._restore_usb_active_managed_file(
            preflight,
            paths,
            "restore.hpd",
            baseline_content,
            allowed_existing_content=intended_content,
            allow_absent=False,
        )

    assert changed
    assert raised.value.mutation_started is False
    assert target.stat().st_ino == original_inode
    assert target.read_bytes() == concurrent


def test_usb_guarded_recovery_restore_refuses_concurrent_creator_for_absent_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline_content = _GUARDED_BASELINE_HPD
    concurrent = b"concurrent creator"
    (
        preflight,
        favorites_directory,
        host_root,
    ) = _usb_guarded_restore_fixture(
        tmp_path,
        baseline_content=baseline_content,
        intended_content=None,
    )
    target = (
        favorites_directory
        / "restore.hpd"
    )
    target.unlink()

    real_open = write_usb.os.open
    created = False

    def racing_open(
        path: str | bytes | Path,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal created
        if (
            Path(path) == target
            and flags & write_usb.os.O_CREAT
            and flags & write_usb.os.O_EXCL
            and not created
        ):
            created = True
            target.write_bytes(
                concurrent
            )

        if dir_fd is None:
            return real_open(
                path,
                flags,
                mode,
            )
        return real_open(
            path,
            flags,
            mode,
            dir_fd=dir_fd,
        )

    monkeypatch.setattr(
        write_usb.os,
        "open",
        racing_open,
    )

    with (
        write_usb._usb_host_operation_lock(
            preflight,
            host_root,
        ) as paths,
        pytest.raises(
            write_usb._FavoritesUsbMediaMutationError,
            match="exclusively create",
        ) as raised,
    ):
        write_usb._restore_usb_active_managed_file(
            preflight,
            paths,
            "restore.hpd",
            baseline_content,
            allowed_existing_content=None,
            allow_absent=True,
        )

    assert created
    assert raised.value.mutation_started is False
    assert target.read_bytes() == concurrent


def test_usb_guarded_recovery_restore_marks_truncate_failure_as_mutation_started(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline_content = _GUARDED_BASELINE_HPD
    intended_content = _GUARDED_INTENDED_HPD
    (
        preflight,
        favorites_directory,
        host_root,
    ) = _usb_guarded_restore_fixture(
        tmp_path,
        baseline_content=baseline_content,
        intended_content=intended_content,
    )
    target = (
        favorites_directory
        / "restore.hpd"
    )
    target.write_bytes(
        intended_content
    )

    real_ftruncate = write_usb.os.ftruncate

    def failing_ftruncate(
        descriptor: int,
        length: int,
    ) -> None:
        real_ftruncate(
            descriptor,
            length,
        )
        raise OSError(
            "injected post-truncate failure"
        )

    monkeypatch.setattr(
        write_usb.os,
        "ftruncate",
        failing_ftruncate,
    )

    with (
        write_usb._usb_host_operation_lock(
            preflight,
            host_root,
        ) as paths,
        pytest.raises(
            write_usb._FavoritesUsbMediaMutationError,
            match="truncate guarded USB recovery target",
        ) as raised,
    ):
        write_usb._restore_usb_active_managed_file(
            preflight,
            paths,
            "restore.hpd",
            baseline_content,
            allowed_existing_content=intended_content,
            allow_absent=False,
        )

    assert raised.value.mutation_started is True
    assert target.read_bytes() == b""


def _usb_verified_recovery_cleanup_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[
    write_usb.FavoritesUsbWritePreflight,
    write_usb._FavoritesUsbHostOperationPaths,
    write_usb._FavoritesUsbVerifiedBackup,
    write_usb._FavoritesUsbVerifiedRecoveryArtifact,
    Path,
    Path,
    bytes,
    object,
]:
    (
        preflight,
        paths,
        backup,
        artifact,
        temporary,
        hpd,
        lock,
    ) = _usb_recovery_artifact_fixture(
        tmp_path,
        monkeypatch,
    )

    target = (
        preflight.qualification.favorites_directory
        / artifact.managed_filename
    )
    write_usb._restore_usb_active_managed_file(
        preflight,
        paths,
        artifact.managed_filename,
        hpd,
        allowed_existing_content=None,
        allow_absent=True,
    )
    verified = (
        write_usb._require_current_usb_recovery_artifact(
            preflight,
            paths,
            backup,
            artifact,
        )
    )

    assert target.read_bytes() == hpd
    assert temporary.read_bytes() == hpd

    return (
        preflight,
        paths,
        backup,
        verified,
        target,
        temporary,
        hpd,
        lock,
    )


def test_usb_verified_recovery_artifact_cleanup_removes_exact_artifact_after_baseline_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        backup,
        verified,
        target,
        temporary,
        hpd,
        lock,
    ) = _usb_verified_recovery_cleanup_fixture(
        tmp_path,
        monkeypatch,
    )
    target_before = target.stat()

    try:
        write_usb._cleanup_verified_usb_recovery_artifact(
            preflight,
            paths,
            backup,
            verified,
        )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )

    assert target.read_bytes() == hpd
    assert target.stat().st_ino == target_before.st_ino
    assert not write_usb.os.path.lexists(
        temporary
    )


def test_usb_verified_recovery_artifact_cleanup_refuses_missing_baseline_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        backup,
        verified,
        target,
        temporary,
        hpd,
        lock,
    ) = _usb_verified_recovery_cleanup_fixture(
        tmp_path,
        monkeypatch,
    )
    write_usb.os.unlink(
        target
    )

    try:
        with pytest.raises(
            write_usb._FavoritesUsbMediaMutationError,
            match="restored baseline HPD",
        ) as raised:
            write_usb._cleanup_verified_usb_recovery_artifact(
                preflight,
                paths,
                backup,
                verified,
            )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )

    assert raised.value.mutation_started is False
    assert not target.exists()
    assert temporary.read_bytes() == hpd


def test_usb_verified_recovery_artifact_cleanup_refuses_nonbaseline_target_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        backup,
        verified,
        target,
        temporary,
        hpd,
        lock,
    ) = _usb_verified_recovery_cleanup_fixture(
        tmp_path,
        monkeypatch,
    )
    target.write_bytes(
        b"external target bytes"
    )

    try:
        with pytest.raises(
            write_usb._FavoritesUsbMediaMutationError,
            match="baseline HPD is restored exactly",
        ) as raised:
            write_usb._cleanup_verified_usb_recovery_artifact(
                preflight,
                paths,
                backup,
                verified,
            )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )

    assert raised.value.mutation_started is False
    assert target.read_bytes() == b"external target bytes"
    assert temporary.read_bytes() == hpd


def test_usb_verified_recovery_artifact_cleanup_refuses_replaced_artifact_inode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        backup,
        verified,
        target,
        temporary,
        hpd,
        lock,
    ) = _usb_verified_recovery_cleanup_fixture(
        tmp_path,
        monkeypatch,
    )
    old_inode = temporary.stat().st_ino
    write_usb.os.unlink(
        temporary
    )
    temporary.write_bytes(
        hpd
    )
    new_inode = temporary.stat().st_ino

    try:
        with pytest.raises(
            write_usb._FavoritesUsbMediaMutationError,
            match="previously verified artifact identity",
        ) as raised:
            write_usb._cleanup_verified_usb_recovery_artifact(
                preflight,
                paths,
                backup,
                verified,
            )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )

    assert old_inode != new_inode
    assert raised.value.mutation_started is False
    assert target.read_bytes() == hpd
    assert temporary.read_bytes() == hpd


def test_usb_verified_recovery_artifact_cleanup_refuses_artifact_content_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        backup,
        verified,
        target,
        temporary,
        hpd,
        lock,
    ) = _usb_verified_recovery_cleanup_fixture(
        tmp_path,
        monkeypatch,
    )
    replacement = b"changed recovery artifact"
    temporary.write_bytes(
        replacement
    )

    try:
        with pytest.raises(
            write_usb._FavoritesUsbMediaMutationError,
            match="revalidate verified USB recovery artifact",
        ) as raised:
            write_usb._cleanup_verified_usb_recovery_artifact(
                preflight,
                paths,
                backup,
                verified,
            )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )

    assert raised.value.mutation_started is False
    assert target.read_bytes() == hpd
    assert temporary.read_bytes() == replacement


def test_usb_verified_recovery_artifact_cleanup_refuses_symlink_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        backup,
        verified,
        target,
        temporary,
        hpd,
        lock,
    ) = _usb_verified_recovery_cleanup_fixture(
        tmp_path,
        monkeypatch,
    )
    write_usb.os.unlink(
        temporary
    )
    outside = tmp_path / "outside-cleanup-artifact"
    outside.write_bytes(
        hpd
    )
    _symlink_or_skip(
        temporary,
        outside,
    )

    try:
        with pytest.raises(
            write_usb._FavoritesUsbMediaMutationError,
            match="revalidate verified USB recovery artifact",
        ) as raised:
            write_usb._cleanup_verified_usb_recovery_artifact(
                preflight,
                paths,
                backup,
                verified,
            )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )

    assert raised.value.mutation_started is False
    assert target.read_bytes() == hpd
    assert temporary.is_symlink()
    assert outside.read_bytes() == hpd


def test_usb_verified_recovery_artifact_cleanup_marks_unlink_failure_as_mutation_started(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        backup,
        verified,
        target,
        temporary,
        hpd,
        lock,
    ) = _usb_verified_recovery_cleanup_fixture(
        tmp_path,
        monkeypatch,
    )
    real_unlink = write_usb.os.unlink

    def failing_unlink(
        path: str | bytes | Path,
        *,
        dir_fd: int | None = None,
    ) -> None:
        if Path(path) == temporary:
            raise OSError(
                "injected cleanup unlink failure"
            )
        if dir_fd is None:
            real_unlink(
                path
            )
        else:
            real_unlink(
                path,
                dir_fd=dir_fd,
            )

    monkeypatch.setattr(
        write_usb.os,
        "unlink",
        failing_unlink,
    )

    try:
        with pytest.raises(
            write_usb._FavoritesUsbMediaMutationError,
            match="clean verified USB recovery artifact",
        ) as raised:
            write_usb._cleanup_verified_usb_recovery_artifact(
                preflight,
                paths,
                backup,
                verified,
            )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )

    assert raised.value.mutation_started is True
    assert target.read_bytes() == hpd
    assert temporary.read_bytes() == hpd


def test_usb_verified_recovery_artifact_cleanup_does_not_delete_postunlink_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        backup,
        verified,
        target,
        temporary,
        hpd,
        lock,
    ) = _usb_verified_recovery_cleanup_fixture(
        tmp_path,
        monkeypatch,
    )
    replacement = b"post-unlink replacement"
    real_unlink = write_usb.os.unlink
    replaced = False

    def replacing_unlink(
        path: str | bytes | Path,
        *,
        dir_fd: int | None = None,
    ) -> None:
        nonlocal replaced
        if Path(path) == temporary and not replaced:
            replaced = True
            if dir_fd is None:
                real_unlink(
                    path
                )
            else:
                real_unlink(
                    path,
                    dir_fd=dir_fd,
                )
            temporary.write_bytes(
                replacement
            )
            return

        if dir_fd is None:
            real_unlink(
                path
            )
        else:
            real_unlink(
                path,
                dir_fd=dir_fd,
            )

    monkeypatch.setattr(
        write_usb.os,
        "unlink",
        replacing_unlink,
    )

    try:
        with pytest.raises(
            write_usb._FavoritesUsbMediaMutationError,
            match="replacement may have appeared",
        ) as raised:
            write_usb._cleanup_verified_usb_recovery_artifact(
                preflight,
                paths,
                backup,
                verified,
            )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )

    assert replaced
    assert raised.value.mutation_started is True
    assert target.read_bytes() == hpd
    assert temporary.read_bytes() == replacement


def _usb_rollback_manifest_test_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[
    write_usb.FavoritesUsbWritePreflight,
    write_usb._FavoritesUsbHostOperationPaths,
    object,
]:
    (
        preflight,
        paths,
        _backup,
        _artifact,
        _temporary,
        _hpd,
        lock,
    ) = _usb_recovery_artifact_fixture(
        tmp_path,
        monkeypatch,
    )

    assert not write_usb.os.path.lexists(
        paths.rollback_manifest_path
    )
    assert not write_usb.os.path.lexists(
        write_usb._usb_rollback_manifest_temporary_path(
            paths
        )
    )

    return (
        preflight,
        paths,
        lock,
    )


def _usb_rollback_manifest_for_test(
    preflight: write_usb.FavoritesUsbWritePreflight,
    paths: write_usb._FavoritesUsbHostOperationPaths,
    *,
    revision: int,
    phase: write_usb._FavoritesUsbRollbackPhase,
    bounded_artifact_present: bool = False,
) -> write_usb._FavoritesUsbRollbackManifest:
    return write_usb._usb_rollback_manifest(
        preflight,
        paths,
        revision=revision,
        phase=phase,
        bounded_artifact_present=(
            bounded_artifact_present
        ),
    )


def test_usb_rollback_manifest_initial_prepared_write_is_canonical_and_exact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        lock,
    ) = _usb_rollback_manifest_test_fixture(
        tmp_path,
        monkeypatch,
    )
    manifest = (
        _usb_rollback_manifest_for_test(
            preflight,
            paths,
            revision=1,
            phase=(
                write_usb._FavoritesUsbRollbackPhase.PREPARED
            ),
        )
    )

    try:
        write_usb._write_usb_rollback_manifest(
            preflight,
            paths,
            manifest,
        )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )

    content = (
        paths.rollback_manifest_path.read_bytes()
    )
    assert content.endswith(
        b"\n"
    )
    assert (
        content
        == write_usb._usb_rollback_manifest_bytes(
            manifest
        )
    )
    assert (
        write_usb._read_usb_rollback_manifest(
            paths.rollback_manifest_path
        )
        == manifest
    )

    payload = json.loads(
        content
    )
    assert payload["schema"] == (
        "sds200.favorites-usb.rollback"
    )
    assert payload["version"] == 1
    assert payload["revision"] == 1
    assert payload["phase"] == "prepared"
    assert (
        payload["media_mutation_started"]
        is False
    )
    assert (
        payload["recovery_required"]
        is False
    )
    assert (
        payload["recovery_attempted"]
        is False
    )
    assert (
        payload["recovery_completed"]
        is False
    )
    assert payload["backup_retained"] is True


def test_usb_rollback_manifest_serializes_identity_evidence_without_raw_programming_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        lock,
    ) = _usb_rollback_manifest_test_fixture(
        tmp_path,
        monkeypatch,
    )
    manifest = (
        _usb_rollback_manifest_for_test(
            preflight,
            paths,
            revision=1,
            phase=(
                write_usb._FavoritesUsbRollbackPhase.PREPARED
            ),
            bounded_artifact_present=True,
        )
    )

    try:
        write_usb._write_usb_rollback_manifest(
            preflight,
            paths,
            manifest,
        )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )

    payload = json.loads(
        paths.rollback_manifest_path.read_text(
            encoding="utf-8"
        )
    )

    assert set(payload) == {
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
    assert set(
        payload["identity"]
    ) == {
        "baseline_snapshot_sha256",
        "intended_snapshot_sha256",
        "baseline_tree_sha256",
    }
    assert set(
        payload["host"]
    ) == {
        "backup_directory",
        "staging_directory",
    }

    serialized = (
        paths.rollback_manifest_path.read_text(
            encoding="utf-8"
        )
    )
    for forbidden_key in (
        '"catalog_bytes"',
        '"content"',
        '"documents"',
        '"filename"',
        '"managed_filename"',
        '"records"',
        '"baseline_snapshot"',
        '"intended_snapshot"',
        '"message"',
        '"diagnostic"',
    ):
        assert forbidden_key not in serialized


def test_usb_rollback_manifest_allows_only_exact_revisioned_phase_transition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        lock,
    ) = _usb_rollback_manifest_test_fixture(
        tmp_path,
        monkeypatch,
    )
    prepared = (
        _usb_rollback_manifest_for_test(
            preflight,
            paths,
            revision=1,
            phase=(
                write_usb._FavoritesUsbRollbackPhase.PREPARED
            ),
        )
    )
    started = (
        _usb_rollback_manifest_for_test(
            preflight,
            paths,
            revision=2,
            phase=(
                write_usb._FavoritesUsbRollbackPhase.MUTATION_STARTED
            ),
            bounded_artifact_present=True,
        )
    )

    try:
        write_usb._write_usb_rollback_manifest(
            preflight,
            paths,
            prepared,
        )
        write_usb._write_usb_rollback_manifest(
            preflight,
            paths,
            started,
        )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )

    assert (
        write_usb._read_usb_rollback_manifest(
            paths.rollback_manifest_path
        )
        == started
    )
    assert started.media_mutation_started
    assert not started.recovery_required
    assert started.bounded_artifact_present


@pytest.mark.parametrize(
    ("revision", "phase"),
    [
        (
            2,
            write_usb._FavoritesUsbRollbackPhase.PREPARED,
        ),
        (
            1,
            write_usb._FavoritesUsbRollbackPhase.MUTATION_STARTED,
        ),
        (
            1,
            write_usb._FavoritesUsbRollbackPhase.RECOVERY_REQUIRED,
        ),
    ],
)
def test_usb_rollback_manifest_refuses_invalid_initial_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    revision: int,
    phase: write_usb._FavoritesUsbRollbackPhase,
) -> None:
    (
        preflight,
        paths,
        lock,
    ) = _usb_rollback_manifest_test_fixture(
        tmp_path,
        monkeypatch,
    )
    manifest = (
        _usb_rollback_manifest_for_test(
            preflight,
            paths,
            revision=revision,
            phase=phase,
        )
    )

    try:
        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="Initial .* PREPARED .* revision 1",
        ):
            write_usb._write_usb_rollback_manifest(
                preflight,
                paths,
                manifest,
            )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )

    assert not write_usb.os.path.lexists(
        paths.rollback_manifest_path
    )


def test_usb_rollback_manifest_refuses_skipped_revision_without_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        lock,
    ) = _usb_rollback_manifest_test_fixture(
        tmp_path,
        monkeypatch,
    )
    prepared = (
        _usb_rollback_manifest_for_test(
            preflight,
            paths,
            revision=1,
            phase=(
                write_usb._FavoritesUsbRollbackPhase.PREPARED
            ),
        )
    )
    skipped = (
        _usb_rollback_manifest_for_test(
            preflight,
            paths,
            revision=3,
            phase=(
                write_usb._FavoritesUsbRollbackPhase.MUTATION_STARTED
            ),
        )
    )

    try:
        write_usb._write_usb_rollback_manifest(
            preflight,
            paths,
            prepared,
        )
        before = (
            paths.rollback_manifest_path.read_bytes()
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="revision must advance by exactly one",
        ):
            write_usb._write_usb_rollback_manifest(
                preflight,
                paths,
                skipped,
            )

        assert (
            paths.rollback_manifest_path.read_bytes()
            == before
        )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )


def test_usb_rollback_manifest_refuses_disallowed_phase_transition_without_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        lock,
    ) = _usb_rollback_manifest_test_fixture(
        tmp_path,
        monkeypatch,
    )
    prepared = (
        _usb_rollback_manifest_for_test(
            preflight,
            paths,
            revision=1,
            phase=(
                write_usb._FavoritesUsbRollbackPhase.PREPARED
            ),
        )
    )
    invalid = (
        _usb_rollback_manifest_for_test(
            preflight,
            paths,
            revision=2,
            phase=(
                write_usb._FavoritesUsbRollbackPhase.RECOVERY_REQUIRED
            ),
        )
    )

    try:
        write_usb._write_usb_rollback_manifest(
            preflight,
            paths,
            prepared,
        )
        before = (
            paths.rollback_manifest_path.read_bytes()
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="phase transition is not allowed",
        ):
            write_usb._write_usb_rollback_manifest(
                preflight,
                paths,
                invalid,
            )

        assert (
            paths.rollback_manifest_path.read_bytes()
            == before
        )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )


def test_usb_rollback_manifest_refuses_lifecycle_flag_disagreement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        lock,
    ) = _usb_rollback_manifest_test_fixture(
        tmp_path,
        monkeypatch,
    )
    manifest = (
        _usb_rollback_manifest_for_test(
            preflight,
            paths,
            revision=1,
            phase=(
                write_usb._FavoritesUsbRollbackPhase.PREPARED
            ),
        )
    )
    payload = manifest.as_dict()
    payload[
        "media_mutation_started"
    ] = True
    content = (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode(
        "utf-8"
    )

    try:
        with pytest.raises(
            ValueError,
            match="lifecycle flags do not match phase",
        ):
            write_usb._parse_usb_rollback_manifest_bytes(
                content
            )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )


def test_usb_rollback_manifest_refuses_unknown_or_noncanonical_existing_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        lock,
    ) = _usb_rollback_manifest_test_fixture(
        tmp_path,
        monkeypatch,
    )
    manifest = (
        _usb_rollback_manifest_for_test(
            preflight,
            paths,
            revision=2,
            phase=(
                write_usb._FavoritesUsbRollbackPhase.MUTATION_STARTED
            ),
        )
    )
    paths.rollback_manifest_path.write_text(
        '{"schema":"future"}\n',
        encoding="utf-8",
    )
    before = (
        paths.rollback_manifest_path.read_bytes()
    )

    try:
        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="Could not parse durable .* rollback manifest",
        ):
            write_usb._write_usb_rollback_manifest(
                preflight,
                paths,
                manifest,
            )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )

    assert (
        paths.rollback_manifest_path.read_bytes()
        == before
    )


def test_usb_rollback_manifest_refuses_symlink_manifest_without_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        lock,
    ) = _usb_rollback_manifest_test_fixture(
        tmp_path,
        monkeypatch,
    )
    outside = (
        tmp_path
        / "outside-rollback.json"
    )
    outside.write_text(
        '{"outside":true}\n',
        encoding="utf-8",
    )
    _symlink_or_skip(
        paths.rollback_manifest_path,
        outside,
    )
    manifest = (
        _usb_rollback_manifest_for_test(
            preflight,
            paths,
            revision=2,
            phase=(
                write_usb._FavoritesUsbRollbackPhase.MUTATION_STARTED
            ),
        )
    )

    try:
        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="must not be a symbolic link",
        ):
            write_usb._write_usb_rollback_manifest(
                preflight,
                paths,
                manifest,
            )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )

    assert paths.rollback_manifest_path.is_symlink()
    assert outside.read_text(
        encoding="utf-8"
    ) == '{"outside":true}\n'


def test_usb_rollback_manifest_refuses_unknown_temporary_collision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        lock,
    ) = _usb_rollback_manifest_test_fixture(
        tmp_path,
        monkeypatch,
    )
    temporary = (
        write_usb._usb_rollback_manifest_temporary_path(
            paths
        )
    )
    temporary.write_bytes(
        b"unknown-host-temp"
    )
    manifest = (
        _usb_rollback_manifest_for_test(
            preflight,
            paths,
            revision=1,
            phase=(
                write_usb._FavoritesUsbRollbackPhase.PREPARED
            ),
        )
    )

    try:
        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="temporary path already exists",
        ):
            write_usb._write_usb_rollback_manifest(
                preflight,
                paths,
                manifest,
            )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )

    assert temporary.read_bytes() == (
        b"unknown-host-temp"
    )
    assert not write_usb.os.path.lexists(
        paths.rollback_manifest_path
    )


def test_usb_rollback_manifest_prepublication_failure_cleans_only_owned_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        lock,
    ) = _usb_rollback_manifest_test_fixture(
        tmp_path,
        monkeypatch,
    )
    manifest = (
        _usb_rollback_manifest_for_test(
            preflight,
            paths,
            revision=1,
            phase=(
                write_usb._FavoritesUsbRollbackPhase.PREPARED
            ),
        )
    )
    temporary = (
        write_usb._usb_rollback_manifest_temporary_path(
            paths
        )
    )
    real_fsync = write_usb.os.fsync
    failed = False

    def failing_fsync(
        descriptor: int,
    ) -> None:
        nonlocal failed
        if not failed:
            failed = True
            raise OSError(
                "injected rollback temp fsync failure"
            )
        real_fsync(
            descriptor
        )

    monkeypatch.setattr(
        write_usb.os,
        "fsync",
        failing_fsync,
    )

    try:
        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="rollback temporary file",
        ):
            write_usb._write_usb_rollback_manifest(
                preflight,
                paths,
                manifest,
            )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )

    assert failed
    assert not write_usb.os.path.lexists(
        temporary
    )
    assert not write_usb.os.path.lexists(
        paths.rollback_manifest_path
    )


def test_usb_rollback_manifest_published_record_is_fsynced_and_exactly_read_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        lock,
    ) = _usb_rollback_manifest_test_fixture(
        tmp_path,
        monkeypatch,
    )
    manifest = (
        _usb_rollback_manifest_for_test(
            preflight,
            paths,
            revision=1,
            phase=(
                write_usb._FavoritesUsbRollbackPhase.PREPARED
            ),
        )
    )
    fsync_calls: list[int] = []
    real_fsync = write_usb.os.fsync

    def recording_fsync(
        descriptor: int,
    ) -> None:
        fsync_calls.append(
            descriptor
        )
        real_fsync(
            descriptor
        )

    monkeypatch.setattr(
        write_usb.os,
        "fsync",
        recording_fsync,
    )

    try:
        write_usb._write_usb_rollback_manifest(
            preflight,
            paths,
            manifest,
        )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )

    assert len(fsync_calls) >= 2
    assert (
        write_usb._read_usb_rollback_manifest(
            paths.rollback_manifest_path
        )
        == manifest
    )


def test_usb_rollback_manifest_terminal_completed_state_cannot_regress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        lock,
    ) = _usb_rollback_manifest_test_fixture(
        tmp_path,
        monkeypatch,
    )
    prepared = (
        _usb_rollback_manifest_for_test(
            preflight,
            paths,
            revision=1,
            phase=(
                write_usb._FavoritesUsbRollbackPhase.PREPARED
            ),
        )
    )
    started = (
        _usb_rollback_manifest_for_test(
            preflight,
            paths,
            revision=2,
            phase=(
                write_usb._FavoritesUsbRollbackPhase.MUTATION_STARTED
            ),
        )
    )
    completed = (
        _usb_rollback_manifest_for_test(
            preflight,
            paths,
            revision=3,
            phase=(
                write_usb._FavoritesUsbRollbackPhase.COMPLETED
            ),
        )
    )
    regressed = (
        _usb_rollback_manifest_for_test(
            preflight,
            paths,
            revision=4,
            phase=(
                write_usb._FavoritesUsbRollbackPhase.RECOVERY_REQUIRED
            ),
        )
    )

    try:
        for manifest in (
            prepared,
            started,
            completed,
        ):
            write_usb._write_usb_rollback_manifest(
                preflight,
                paths,
                manifest,
            )

        before = (
            paths.rollback_manifest_path.read_bytes()
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="phase transition is not allowed",
        ):
            write_usb._write_usb_rollback_manifest(
                preflight,
                paths,
                regressed,
            )

        assert (
            paths.rollback_manifest_path.read_bytes()
            == before
        )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )


def _usb_operation_report_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[
    write_usb.FavoritesUsbWritePreflight,
    write_usb._FavoritesUsbHostOperationPaths,
    object,
]:
    (
        preflight,
        paths,
        lock,
    ) = _usb_rollback_manifest_test_fixture(
        tmp_path,
        monkeypatch,
    )

    return (
        preflight,
        paths,
        lock,
    )


def _usb_write_rollback_sequence(
    preflight: write_usb.FavoritesUsbWritePreflight,
    paths: write_usb._FavoritesUsbHostOperationPaths,
    phases: tuple[
        write_usb._FavoritesUsbRollbackPhase,
        ...,
    ],
    *,
    final_artifact_present: bool = False,
) -> write_usb._FavoritesUsbRollbackManifest:
    final: write_usb._FavoritesUsbRollbackManifest | None = None

    for revision, phase in enumerate(
        phases,
        start=1,
    ):
        final = write_usb._usb_rollback_manifest(
            preflight,
            paths,
            revision=revision,
            phase=phase,
            bounded_artifact_present=(
                final_artifact_present
                if revision == len(phases)
                else False
            ),
        )
        write_usb._write_usb_rollback_manifest(
            preflight,
            paths,
            final,
        )

    assert final is not None
    return final


def _usb_success_operation_report(
    preflight: write_usb.FavoritesUsbWritePreflight,
    paths: write_usb._FavoritesUsbHostOperationPaths,
    rollback: write_usb._FavoritesUsbRollbackManifest,
) -> write_usb._FavoritesUsbOperationReport:
    return write_usb._usb_operation_report(
        preflight,
        paths,
        rollback,
        backup_verification=(
            write_usb._FavoritesUsbVerificationOutcome.VERIFIED
        ),
        staging_verification=(
            write_usb._FavoritesUsbVerificationOutcome.VERIFIED
        ),
        preactivation_verification=(
            write_usb._FavoritesUsbVerificationOutcome.VERIFIED
        ),
        postactivation_verification=(
            write_usb._FavoritesUsbVerificationOutcome.VERIFIED
        ),
        unmanaged_preservation=(
            write_usb._FavoritesUsbVerificationOutcome.VERIFIED
        ),
        activation_outcome=(
            write_usb._FavoritesUsbActivationOutcome.COMPLETED
        ),
        recovery_outcome=(
            write_usb._FavoritesUsbRecoveryOutcome.NOT_REQUIRED
        ),
        active_snapshot_sha256=(
            rollback.intended_snapshot_sha256
        ),
        failure_code=None,
    )


def test_usb_operation_report_success_is_canonical_private_and_correlated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        lock,
    ) = _usb_operation_report_fixture(
        tmp_path,
        monkeypatch,
    )

    try:
        rollback = _usb_write_rollback_sequence(
            preflight,
            paths,
            (
                write_usb._FavoritesUsbRollbackPhase.PREPARED,
                write_usb._FavoritesUsbRollbackPhase.MUTATION_STARTED,
                write_usb._FavoritesUsbRollbackPhase.COMPLETED,
            ),
        )
        report = _usb_success_operation_report(
            preflight,
            paths,
            rollback,
        )

        written = write_usb._write_usb_operation_report(
            preflight,
            paths,
            rollback,
            report,
        )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )

    assert written == paths.operation_report_path
    assert written.is_file()
    assert not write_usb.os.path.lexists(
        paths.failure_report_path
    )

    content = written.read_bytes()
    assert (
        content
        == write_usb._usb_operation_report_bytes(
            report
        )
    )
    assert (
        write_usb._read_usb_operation_report(
            written
        )
        == report
    )

    payload = json.loads(
        content
    )
    assert payload["schema"] == (
        "sds200.favorites-usb.operation-report"
    )
    assert payload["version"] == 1
    assert payload["operation_id"] == rollback.operation_id
    assert payload["rollback_revision"] == rollback.revision
    assert payload["rollback_phase"] == "completed"
    assert payload["activation_outcome"] == "completed"
    assert payload["recovery_outcome"] == "not_required"
    assert payload["failure_code"] is None
    assert payload["backup_retained"] is True

    serialized = content.decode(
        "utf-8"
    )
    for forbidden_key in (
        '"catalog_bytes"',
        '"content"',
        '"documents"',
        '"filename"',
        '"managed_filename"',
        '"records"',
        '"baseline_snapshot"',
        '"intended_snapshot"',
        '"message"',
        '"diagnostic"',
        '"traceback"',
    ):
        assert forbidden_key not in serialized


def test_usb_operation_failure_report_preactivation_uses_failure_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        lock,
    ) = _usb_operation_report_fixture(
        tmp_path,
        monkeypatch,
    )

    try:
        rollback = _usb_write_rollback_sequence(
            preflight,
            paths,
            (
                write_usb._FavoritesUsbRollbackPhase.PREPARED,
            ),
        )
        report = write_usb._usb_operation_report(
            preflight,
            paths,
            rollback,
            backup_verification=(
                write_usb._FavoritesUsbVerificationOutcome.VERIFIED
            ),
            staging_verification=(
                write_usb._FavoritesUsbVerificationOutcome.VERIFIED
            ),
            preactivation_verification=(
                write_usb._FavoritesUsbVerificationOutcome.FAILED
            ),
            postactivation_verification=(
                write_usb._FavoritesUsbVerificationOutcome.NOT_ATTEMPTED
            ),
            unmanaged_preservation=(
                write_usb._FavoritesUsbVerificationOutcome.FAILED
            ),
            activation_outcome=(
                write_usb._FavoritesUsbActivationOutcome.NOT_STARTED
            ),
            recovery_outcome=(
                write_usb._FavoritesUsbRecoveryOutcome.NOT_REQUIRED
            ),
            active_snapshot_sha256=None,
            failure_code=(
                write_usb._FavoritesUsbFailureCode.PREACTIVATION_FAILED
            ),
        )

        written = write_usb._write_usb_operation_report(
            preflight,
            paths,
            rollback,
            report,
        )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )

    assert written == paths.failure_report_path
    assert written.is_file()
    assert not write_usb.os.path.lexists(
        paths.operation_report_path
    )
    assert (
        write_usb._read_usb_operation_report(
            written
        )
        == report
    )


def test_usb_operation_failure_report_recovered_correlates_to_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        lock,
    ) = _usb_operation_report_fixture(
        tmp_path,
        monkeypatch,
    )

    try:
        rollback = _usb_write_rollback_sequence(
            preflight,
            paths,
            (
                write_usb._FavoritesUsbRollbackPhase.PREPARED,
                write_usb._FavoritesUsbRollbackPhase.MUTATION_STARTED,
                write_usb._FavoritesUsbRollbackPhase.RECOVERY_REQUIRED,
                write_usb._FavoritesUsbRollbackPhase.RECOVERY_IN_PROGRESS,
                write_usb._FavoritesUsbRollbackPhase.RECOVERED,
            ),
        )
        report = write_usb._usb_operation_report(
            preflight,
            paths,
            rollback,
            backup_verification=(
                write_usb._FavoritesUsbVerificationOutcome.VERIFIED
            ),
            staging_verification=(
                write_usb._FavoritesUsbVerificationOutcome.VERIFIED
            ),
            preactivation_verification=(
                write_usb._FavoritesUsbVerificationOutcome.VERIFIED
            ),
            postactivation_verification=(
                write_usb._FavoritesUsbVerificationOutcome.FAILED
            ),
            unmanaged_preservation=(
                write_usb._FavoritesUsbVerificationOutcome.VERIFIED
            ),
            activation_outcome=(
                write_usb._FavoritesUsbActivationOutcome.FAILED_AFTER_MUTATION
            ),
            recovery_outcome=(
                write_usb._FavoritesUsbRecoveryOutcome.RECOVERED
            ),
            active_snapshot_sha256=(
                rollback.baseline_snapshot_sha256
            ),
            failure_code=(
                write_usb._FavoritesUsbFailureCode.POSTACTIVATION_VERIFICATION_FAILED
            ),
        )

        written = write_usb._write_usb_operation_report(
            preflight,
            paths,
            rollback,
            report,
        )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )

    assert written == paths.failure_report_path
    payload = json.loads(
        written.read_text(
            encoding="utf-8"
        )
    )
    assert payload["rollback_phase"] == "recovered"
    assert payload["recovery_outcome"] == "recovered"
    assert (
        payload["identity"]["active_snapshot_sha256"]
        == rollback.baseline_snapshot_sha256
    )


def test_usb_operation_failure_report_incomplete_preserves_artifact_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        lock,
    ) = _usb_operation_report_fixture(
        tmp_path,
        monkeypatch,
    )

    try:
        rollback = _usb_write_rollback_sequence(
            preflight,
            paths,
            (
                write_usb._FavoritesUsbRollbackPhase.PREPARED,
                write_usb._FavoritesUsbRollbackPhase.MUTATION_STARTED,
                write_usb._FavoritesUsbRollbackPhase.RECOVERY_REQUIRED,
                write_usb._FavoritesUsbRollbackPhase.RECOVERY_IN_PROGRESS,
                write_usb._FavoritesUsbRollbackPhase.RECOVERY_INCOMPLETE,
            ),
            final_artifact_present=True,
        )
        report = write_usb._usb_operation_report(
            preflight,
            paths,
            rollback,
            backup_verification=(
                write_usb._FavoritesUsbVerificationOutcome.VERIFIED
            ),
            staging_verification=(
                write_usb._FavoritesUsbVerificationOutcome.VERIFIED
            ),
            preactivation_verification=(
                write_usb._FavoritesUsbVerificationOutcome.VERIFIED
            ),
            postactivation_verification=(
                write_usb._FavoritesUsbVerificationOutcome.FAILED
            ),
            unmanaged_preservation=(
                write_usb._FavoritesUsbVerificationOutcome.FAILED
            ),
            activation_outcome=(
                write_usb._FavoritesUsbActivationOutcome.FAILED_AFTER_MUTATION
            ),
            recovery_outcome=(
                write_usb._FavoritesUsbRecoveryOutcome.INCOMPLETE
            ),
            active_snapshot_sha256=None,
            failure_code=(
                write_usb._FavoritesUsbFailureCode.RECOVERY_INCOMPLETE
            ),
        )

        written = write_usb._write_usb_operation_report(
            preflight,
            paths,
            rollback,
            report,
        )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )

    payload = json.loads(
        written.read_text(
            encoding="utf-8"
        )
    )
    assert payload["rollback_phase"] == "recovery_incomplete"
    assert payload["bounded_artifact_present"] is True
    assert payload["backup_retained"] is True


@pytest.mark.parametrize(
    (
        "phase",
        "activation",
        "recovery",
        "active_identity",
        "failure_code",
    ),
    [
        (
            write_usb._FavoritesUsbRollbackPhase.COMPLETED,
            write_usb._FavoritesUsbActivationOutcome.COMPLETED,
            write_usb._FavoritesUsbRecoveryOutcome.NOT_REQUIRED,
            None,
            None,
        ),
        (
            write_usb._FavoritesUsbRollbackPhase.RECOVERED,
            write_usb._FavoritesUsbActivationOutcome.FAILED_AFTER_MUTATION,
            write_usb._FavoritesUsbRecoveryOutcome.RECOVERED,
            "intended",
            write_usb._FavoritesUsbFailureCode.ACTIVATION_FAILED_AFTER_MUTATION,
        ),
    ],
)
def test_usb_operation_report_refuses_invalid_terminal_active_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: write_usb._FavoritesUsbRollbackPhase,
    activation: write_usb._FavoritesUsbActivationOutcome,
    recovery: write_usb._FavoritesUsbRecoveryOutcome,
    active_identity: str | None,
    failure_code: write_usb._FavoritesUsbFailureCode | None,
) -> None:
    (
        preflight,
        paths,
        lock,
    ) = _usb_operation_report_fixture(
        tmp_path,
        monkeypatch,
    )

    try:
        if phase is write_usb._FavoritesUsbRollbackPhase.COMPLETED:
            phases = (
                write_usb._FavoritesUsbRollbackPhase.PREPARED,
                write_usb._FavoritesUsbRollbackPhase.MUTATION_STARTED,
                write_usb._FavoritesUsbRollbackPhase.COMPLETED,
            )
        else:
            phases = (
                write_usb._FavoritesUsbRollbackPhase.PREPARED,
                write_usb._FavoritesUsbRollbackPhase.MUTATION_STARTED,
                write_usb._FavoritesUsbRollbackPhase.RECOVERY_REQUIRED,
                write_usb._FavoritesUsbRollbackPhase.RECOVERY_IN_PROGRESS,
                write_usb._FavoritesUsbRollbackPhase.RECOVERED,
            )

        rollback = _usb_write_rollback_sequence(
            preflight,
            paths,
            phases,
        )

        if active_identity == "intended":
            observed_identity = rollback.intended_snapshot_sha256
        else:
            observed_identity = None

        with pytest.raises(
            ValueError,
            match="active snapshot identity",
        ):
            write_usb._usb_operation_report(
                preflight,
                paths,
                rollback,
                backup_verification=(
                    write_usb._FavoritesUsbVerificationOutcome.VERIFIED
                ),
                staging_verification=(
                    write_usb._FavoritesUsbVerificationOutcome.VERIFIED
                ),
                preactivation_verification=(
                    write_usb._FavoritesUsbVerificationOutcome.VERIFIED
                ),
                postactivation_verification=(
                    write_usb._FavoritesUsbVerificationOutcome.VERIFIED
                    if phase
                    is write_usb._FavoritesUsbRollbackPhase.COMPLETED
                    else write_usb._FavoritesUsbVerificationOutcome.FAILED
                ),
                unmanaged_preservation=(
                    write_usb._FavoritesUsbVerificationOutcome.VERIFIED
                ),
                activation_outcome=activation,
                recovery_outcome=recovery,
                active_snapshot_sha256=observed_identity,
                failure_code=failure_code,
            )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )


def test_usb_operation_report_refuses_nonreportable_rollback_phase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        lock,
    ) = _usb_operation_report_fixture(
        tmp_path,
        monkeypatch,
    )

    try:
        rollback = _usb_write_rollback_sequence(
            preflight,
            paths,
            (
                write_usb._FavoritesUsbRollbackPhase.PREPARED,
                write_usb._FavoritesUsbRollbackPhase.MUTATION_STARTED,
            ),
        )

        with pytest.raises(
            ValueError,
            match="reportable rollback phase",
        ):
            write_usb._usb_operation_report(
                preflight,
                paths,
                rollback,
                backup_verification=(
                    write_usb._FavoritesUsbVerificationOutcome.VERIFIED
                ),
                staging_verification=(
                    write_usb._FavoritesUsbVerificationOutcome.VERIFIED
                ),
                preactivation_verification=(
                    write_usb._FavoritesUsbVerificationOutcome.VERIFIED
                ),
                postactivation_verification=(
                    write_usb._FavoritesUsbVerificationOutcome.NOT_ATTEMPTED
                ),
                unmanaged_preservation=(
                    write_usb._FavoritesUsbVerificationOutcome.VERIFIED
                ),
                activation_outcome=(
                    write_usb._FavoritesUsbActivationOutcome.FAILED_AFTER_MUTATION
                ),
                recovery_outcome=(
                    write_usb._FavoritesUsbRecoveryOutcome.NOT_ATTEMPTED
                ),
                active_snapshot_sha256=None,
                failure_code=(
                    write_usb._FavoritesUsbFailureCode.RECOVERY_NOT_ATTEMPTED
                ),
            )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )


def test_usb_operation_report_refuses_mismatched_rollback_correlation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        lock,
    ) = _usb_operation_report_fixture(
        tmp_path,
        monkeypatch,
    )

    try:
        rollback = _usb_write_rollback_sequence(
            preflight,
            paths,
            (
                write_usb._FavoritesUsbRollbackPhase.PREPARED,
                write_usb._FavoritesUsbRollbackPhase.MUTATION_STARTED,
                write_usb._FavoritesUsbRollbackPhase.COMPLETED,
            ),
        )
        report = _usb_success_operation_report(
            preflight,
            paths,
            rollback,
        )
        mismatched = write_usb._usb_rollback_manifest(
            preflight,
            paths,
            revision=rollback.revision,
            phase=rollback.phase,
            bounded_artifact_present=True,
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="does not correlate",
        ):
            write_usb._write_usb_operation_report(
                preflight,
                paths,
                mismatched,
                report,
            )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )


def test_usb_operation_report_refuses_existing_final_report_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        lock,
    ) = _usb_operation_report_fixture(
        tmp_path,
        monkeypatch,
    )

    try:
        rollback = _usb_write_rollback_sequence(
            preflight,
            paths,
            (
                write_usb._FavoritesUsbRollbackPhase.PREPARED,
                write_usb._FavoritesUsbRollbackPhase.MUTATION_STARTED,
                write_usb._FavoritesUsbRollbackPhase.COMPLETED,
            ),
        )
        report = _usb_success_operation_report(
            preflight,
            paths,
            rollback,
        )
        paths.failure_report_path.write_bytes(
            b"existing-final-report"
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="final operation report path already exists",
        ):
            write_usb._write_usb_operation_report(
                preflight,
                paths,
                rollback,
                report,
            )

        assert paths.failure_report_path.read_bytes() == (
            b"existing-final-report"
        )
        assert not write_usb.os.path.lexists(
            paths.operation_report_path
        )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )


def test_usb_operation_report_refuses_unknown_temp_collision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        lock,
    ) = _usb_operation_report_fixture(
        tmp_path,
        monkeypatch,
    )

    try:
        rollback = _usb_write_rollback_sequence(
            preflight,
            paths,
            (
                write_usb._FavoritesUsbRollbackPhase.PREPARED,
                write_usb._FavoritesUsbRollbackPhase.MUTATION_STARTED,
                write_usb._FavoritesUsbRollbackPhase.COMPLETED,
            ),
        )
        report = _usb_success_operation_report(
            preflight,
            paths,
            rollback,
        )
        success_temp, _failure_temp = (
            write_usb._usb_operation_report_temporary_paths(
                paths
            )
        )
        success_temp.write_bytes(
            b"unknown-temp"
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="temporary path already exists",
        ):
            write_usb._write_usb_operation_report(
                preflight,
                paths,
                rollback,
                report,
            )

        assert success_temp.read_bytes() == (
            b"unknown-temp"
        )
        assert not write_usb.os.path.lexists(
            paths.operation_report_path
        )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )


def test_usb_operation_report_refuses_symlink_final_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        lock,
    ) = _usb_operation_report_fixture(
        tmp_path,
        monkeypatch,
    )
    outside = (
        tmp_path
        / "outside-report.json"
    )
    outside.write_bytes(
        b"outside"
    )

    try:
        rollback = _usb_write_rollback_sequence(
            preflight,
            paths,
            (
                write_usb._FavoritesUsbRollbackPhase.PREPARED,
                write_usb._FavoritesUsbRollbackPhase.MUTATION_STARTED,
                write_usb._FavoritesUsbRollbackPhase.COMPLETED,
            ),
        )
        report = _usb_success_operation_report(
            preflight,
            paths,
            rollback,
        )
        _symlink_or_skip(
            paths.operation_report_path,
            outside,
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="final operation report path already exists",
        ):
            write_usb._write_usb_operation_report(
                preflight,
                paths,
                rollback,
                report,
            )

        assert paths.operation_report_path.is_symlink()
        assert outside.read_bytes() == b"outside"
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )


def test_usb_operation_report_prepublication_failure_cleans_only_owned_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        lock,
    ) = _usb_operation_report_fixture(
        tmp_path,
        monkeypatch,
    )

    try:
        rollback = _usb_write_rollback_sequence(
            preflight,
            paths,
            (
                write_usb._FavoritesUsbRollbackPhase.PREPARED,
                write_usb._FavoritesUsbRollbackPhase.MUTATION_STARTED,
                write_usb._FavoritesUsbRollbackPhase.COMPLETED,
            ),
        )
        report = _usb_success_operation_report(
            preflight,
            paths,
            rollback,
        )
        success_temp, _failure_temp = (
            write_usb._usb_operation_report_temporary_paths(
                paths
            )
        )

        real_fsync = write_usb.os.fsync
        failed = False

        def failing_fsync(
            descriptor: int,
        ) -> None:
            nonlocal failed
            if not failed:
                failed = True
                raise OSError(
                    "injected operation-report temp fsync failure"
                )
            real_fsync(
                descriptor
            )

        monkeypatch.setattr(
            write_usb.os,
            "fsync",
            failing_fsync,
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="operation report temporary file",
        ):
            write_usb._write_usb_operation_report(
                preflight,
                paths,
                rollback,
                report,
            )

        assert failed
        assert not write_usb.os.path.lexists(
            success_temp
        )
        assert not write_usb.os.path.lexists(
            paths.operation_report_path
        )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )


def test_usb_operation_report_revalidates_rollback_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        lock,
    ) = _usb_operation_report_fixture(
        tmp_path,
        monkeypatch,
    )

    try:
        rollback = _usb_write_rollback_sequence(
            preflight,
            paths,
            (
                write_usb._FavoritesUsbRollbackPhase.PREPARED,
                write_usb._FavoritesUsbRollbackPhase.MUTATION_STARTED,
                write_usb._FavoritesUsbRollbackPhase.COMPLETED,
            ),
        )
        report = _usb_success_operation_report(
            preflight,
            paths,
            rollback,
        )

        real_read = write_usb._read_usb_host_durable_regular_file
        report_temp_reads = 0

        def changing_read(
            path: Path,
        ) -> bytes:
            nonlocal report_temp_reads
            content = real_read(
                path
            )
            success_temp, _failure_temp = (
                write_usb._usb_operation_report_temporary_paths(
                    paths
                )
            )
            if path == success_temp:
                report_temp_reads += 1
                if report_temp_reads == 1:
                    replacement = write_usb._usb_rollback_manifest(
                        preflight,
                        paths,
                        revision=rollback.revision,
                        phase=rollback.phase,
                        bounded_artifact_present=True,
                    )
                    paths.rollback_manifest_path.write_bytes(
                        write_usb._usb_rollback_manifest_bytes(
                            replacement
                        )
                    )
            return content

        monkeypatch.setattr(
            write_usb,
            "_read_usb_host_durable_regular_file",
            changing_read,
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="rollback manifest changed immediately before",
        ):
            write_usb._write_usb_operation_report(
                preflight,
                paths,
                rollback,
                report,
            )

        assert not write_usb.os.path.lexists(
            paths.operation_report_path
        )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )


def test_usb_operation_report_final_publication_fsyncs_and_reads_exactly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        lock,
    ) = _usb_operation_report_fixture(
        tmp_path,
        monkeypatch,
    )

    try:
        rollback = _usb_write_rollback_sequence(
            preflight,
            paths,
            (
                write_usb._FavoritesUsbRollbackPhase.PREPARED,
                write_usb._FavoritesUsbRollbackPhase.MUTATION_STARTED,
                write_usb._FavoritesUsbRollbackPhase.COMPLETED,
            ),
        )
        report = _usb_success_operation_report(
            preflight,
            paths,
            rollback,
        )

        fsync_calls: list[int] = []
        real_fsync = write_usb.os.fsync

        def recording_fsync(
            descriptor: int,
        ) -> None:
            fsync_calls.append(
                descriptor
            )
            real_fsync(
                descriptor
            )

        monkeypatch.setattr(
            write_usb.os,
            "fsync",
            recording_fsync,
        )

        written = write_usb._write_usb_operation_report(
            preflight,
            paths,
            rollback,
            report,
        )

        assert len(fsync_calls) >= 2
        assert (
            write_usb._read_usb_operation_report(
                written
            )
            == report
        )
        assert (
            write_usb._read_usb_rollback_manifest(
                paths.rollback_manifest_path
            )
            == rollback
        )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )


def test_usb_operation_report_parser_refuses_duplicate_or_noncanonical_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        lock,
    ) = _usb_operation_report_fixture(
        tmp_path,
        monkeypatch,
    )

    try:
        rollback = _usb_write_rollback_sequence(
            preflight,
            paths,
            (
                write_usb._FavoritesUsbRollbackPhase.PREPARED,
                write_usb._FavoritesUsbRollbackPhase.MUTATION_STARTED,
                write_usb._FavoritesUsbRollbackPhase.COMPLETED,
            ),
        )
        report = _usb_success_operation_report(
            preflight,
            paths,
            rollback,
        )
        canonical = write_usb._usb_operation_report_bytes(
            report
        )
        payload = json.loads(
            canonical
        )

        pretty = (
            json.dumps(
                payload,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode(
            "utf-8"
        )

        with pytest.raises(
            ValueError,
            match="not in canonical form",
        ):
            write_usb._parse_usb_operation_report_bytes(
                pretty
            )

        duplicate = canonical.replace(
            b'{"activation_outcome":',
            b'{"activation_outcome":"completed","activation_outcome":',
            1,
        )
        with pytest.raises(
            ValueError,
            match="not valid strict JSON",
        ):
            write_usb._parse_usb_operation_report_bytes(
                duplicate
            )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )


_RECOVERY_REMOVE_INTENDED_HPD = (
    b"TargetModel\tBCDx36HP\r\n"
    b"FormatVersion\t1.00\r\n"
    b"Department\tRecovery introduced\r\n"
)


def _usb_recovery_removal_fixture(
    tmp_path: Path,
) -> tuple[
    write_usb.FavoritesUsbWritePreflight,
    Path,
    Path,
    FavoritesStorageDocument,
]:
    baseline = FavoritesStorageSnapshot(
        catalog_bytes=_BASELINE_CATALOG,
        documents=(),
    )
    intended_document = (
        FavoritesStorageDocument(
            filename="added.hpd",
            content=_RECOVERY_REMOVE_INTENDED_HPD,
        )
    )
    intended = FavoritesStorageSnapshot(
        catalog_bytes=_CHANGED_CATALOG,
        documents=(
            intended_document,
        ),
    )

    (
        preflight,
        _prepared,
        favorites_directory,
    ) = _prepared_usb_activation_fixture(
        tmp_path,
        baseline=baseline,
        intended=intended,
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )

    return (
        preflight,
        favorites_directory,
        host_root,
        intended_document,
    )


def test_usb_recovery_removal_removes_exact_intended_only_hpd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        document,
    ) = _usb_recovery_removal_fixture(
        tmp_path
    )
    target = (
        favorites_directory
        / document.filename
    )

    forbidden_binder_called = False

    def forbidden_baseline_binder(
        *_args: object,
        **_kwargs: object,
    ) -> object:
        nonlocal forbidden_binder_called
        forbidden_binder_called = True
        raise AssertionError(
            "intended-only removal must not use the baseline artifact binder"
        )

    monkeypatch.setattr(
        write_usb,
        "_require_current_usb_recovery_artifact",
        forbidden_baseline_binder,
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        target.write_bytes(
            document.content
        )
        temporary = (
            write_usb._usb_media_temporary_path(
                preflight,
                paths,
            )
        )

        write_usb._remove_usb_recovery_intended_only_hpd(
            preflight,
            paths,
            backup,
            document,
        )

        assert not write_usb.os.path.lexists(
            target
        )
        assert not write_usb.os.path.lexists(
            temporary
        )

    assert forbidden_binder_called is False


def test_usb_recovery_removal_absent_target_is_noop(
    tmp_path: Path,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        document,
    ) = _usb_recovery_removal_fixture(
        tmp_path
    )
    target = (
        favorites_directory
        / document.filename
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        temporary = (
            write_usb._usb_media_temporary_path(
                preflight,
                paths,
            )
        )

        assert not write_usb.os.path.lexists(
            target
        )
        write_usb._remove_usb_recovery_intended_only_hpd(
            preflight,
            paths,
            backup,
            document,
        )
        assert not write_usb.os.path.lexists(
            target
        )
        assert not write_usb.os.path.lexists(
            temporary
        )


def test_usb_recovery_removal_refuses_document_not_exactly_in_bound_plan(
    tmp_path: Path,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        document,
    ) = _usb_recovery_removal_fixture(
        tmp_path
    )
    target = (
        favorites_directory
        / document.filename
    )
    wrong = FavoritesStorageDocument(
        filename=document.filename,
        content=b"wrong intended content",
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        target.write_bytes(
            document.content
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="exact intended-only HPD",
        ):
            write_usb._remove_usb_recovery_intended_only_hpd(
                preflight,
                paths,
                backup,
                wrong,
            )

        assert (
            target.read_bytes()
            == document.content
        )


def test_usb_recovery_removal_refuses_unknown_existing_content(
    tmp_path: Path,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        document,
    ) = _usb_recovery_removal_fixture(
        tmp_path
    )
    target = (
        favorites_directory
        / document.filename
    )
    unknown = b"external concurrent content"

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        target.write_bytes(
            unknown
        )

        with pytest.raises(
            write_usb._FavoritesUsbMediaMutationError,
            match="exact intended-only HPD content",
        ) as raised:
            write_usb._remove_usb_recovery_intended_only_hpd(
                preflight,
                paths,
                backup,
                document,
            )

        assert raised.value.mutation_started is False
        assert target.read_bytes() == unknown


def test_usb_recovery_removal_refuses_symlink_without_mutation(
    tmp_path: Path,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        document,
    ) = _usb_recovery_removal_fixture(
        tmp_path
    )
    target = (
        favorites_directory
        / document.filename
    )
    outside = (
        tmp_path
        / "outside-recovery-removal.hpd"
    )
    outside.write_bytes(
        document.content
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        _symlink_or_skip(
            target,
            outside,
        )

        temporary = (
            write_usb._usb_media_temporary_path(
                preflight,
                paths,
            )
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="symbolic links",
        ):
            write_usb._remove_usb_recovery_intended_only_hpd(
                preflight,
                paths,
                backup,
                document,
            )

        assert target.is_symlink()
        assert outside.read_bytes() == document.content
        assert not write_usb.os.path.lexists(
            temporary
        )


def test_usb_recovery_removal_refuses_existing_media_temp(
    tmp_path: Path,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        document,
    ) = _usb_recovery_removal_fixture(
        tmp_path
    )
    target = (
        favorites_directory
        / document.filename
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        target.write_bytes(
            document.content
        )
        temporary = (
            write_usb._usb_media_temporary_path(
                preflight,
                paths,
            )
        )
        temporary.write_bytes(
            b"surviving baseline recovery artifact"
        )

        with pytest.raises(
            write_usb._FavoritesUsbMediaMutationError,
            match="temporary path to be absent",
        ) as raised:
            write_usb._remove_usb_recovery_intended_only_hpd(
                preflight,
                paths,
                backup,
                document,
            )

        assert raised.value.mutation_started is False
        assert target.read_bytes() == document.content
        assert temporary.read_bytes() == (
            b"surviving baseline recovery artifact"
        )


def test_usb_recovery_removal_refuses_target_change_during_final_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        document,
    ) = _usb_recovery_removal_fixture(
        tmp_path
    )
    target = (
        favorites_directory
        / document.filename
    )
    concurrent = b"same-name concurrent replacement"

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        target.write_bytes(
            document.content
        )

        real_ready = (
            write_usb._require_usb_recovery_target_ready
        )
        calls = 0

        def changing_ready(
            current_preflight: write_usb.FavoritesUsbWritePreflight,
            current_paths: write_usb._FavoritesUsbHostOperationPaths,
            current_backup: write_usb._FavoritesUsbVerifiedBackup,
        ) -> write_usb._FavoritesUsbRecoveryTargetEvidence:
            nonlocal calls
            result = real_ready(
                current_preflight,
                current_paths,
                current_backup,
            )
            calls += 1
            if calls == 2:
                target.write_bytes(
                    concurrent
                )
            return result

        monkeypatch.setattr(
            write_usb,
            "_require_usb_recovery_target_ready",
            changing_ready,
        )

        with pytest.raises(
            write_usb._FavoritesUsbMediaMutationError,
            match="changed immediately before mutation|changed during final",
        ) as raised:
            write_usb._remove_usb_recovery_intended_only_hpd(
                preflight,
                paths,
                backup,
                document,
            )

        assert raised.value.mutation_started is False
        assert target.read_bytes() == concurrent


def test_usb_recovery_removal_raced_displacement_never_overwrites_concurrent_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        document,
    ) = _usb_recovery_removal_fixture(
        tmp_path
    )
    target = (
        favorites_directory
        / document.filename
    )
    concurrent = b"raced target content"

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        target.write_bytes(
            document.content
        )
        temporary = (
            write_usb._usb_media_temporary_path(
                preflight,
                paths,
            )
        )
        real_replace = write_usb.os.replace
        injected = False

        def replacing_replace(
            source: str | bytes | Path,
            destination: str | bytes | Path,
        ) -> None:
            nonlocal injected
            if (
                Path(source) == target
                and Path(destination) == temporary
                and not injected
            ):
                injected = True
                target.write_bytes(
                    concurrent
                )
            real_replace(
                source,
                destination,
            )

        monkeypatch.setattr(
            write_usb.os,
            "replace",
            replacing_replace,
        )

        with pytest.raises(
            write_usb._FavoritesUsbMediaMutationError,
            match="failed exact intended-content verification",
        ) as raised:
            write_usb._remove_usb_recovery_intended_only_hpd(
                preflight,
                paths,
                backup,
                document,
            )

        assert injected
        assert raised.value.mutation_started is True
        assert raised.value.recovery_artifact is None
        assert target.read_bytes() == concurrent
        assert temporary.read_bytes() == concurrent


def test_usb_recovery_removal_preserves_exact_artifact_if_concurrent_target_appears(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        document,
    ) = _usb_recovery_removal_fixture(
        tmp_path
    )
    target = (
        favorites_directory
        / document.filename
    )
    concurrent = b"post-displacement concurrent target"

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        target.write_bytes(
            document.content
        )
        temporary = (
            write_usb._usb_media_temporary_path(
                preflight,
                paths,
            )
        )
        real_read = (
            write_usb._read_usb_activation_regular_file
        )
        injected = False

        def creating_read(
            path: Path,
        ) -> bytes:
            nonlocal injected
            content = real_read(
                path
            )
            if (
                path == temporary
                and not injected
            ):
                injected = True
                target.write_bytes(
                    concurrent
                )
            return content

        monkeypatch.setattr(
            write_usb,
            "_read_usb_activation_regular_file",
            creating_read,
        )

        with pytest.raises(
            write_usb._FavoritesUsbMediaMutationError,
            match="concurrent target appeared",
        ) as raised:
            write_usb._remove_usb_recovery_intended_only_hpd(
                preflight,
                paths,
                backup,
                document,
            )

        artifact = raised.value.recovery_artifact
        assert injected
        assert raised.value.mutation_started is True
        assert artifact is not None
        assert artifact.path == temporary
        assert artifact.managed_filename == document.filename
        assert artifact.content_sha256 == (
            write_usb._usb_media_content_sha256(
                document.content
            )
        )
        assert target.read_bytes() == concurrent
        assert temporary.read_bytes() == document.content


def test_usb_recovery_removal_unlink_failure_surfaces_exact_intended_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        document,
    ) = _usb_recovery_removal_fixture(
        tmp_path
    )
    target = (
        favorites_directory
        / document.filename
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        target.write_bytes(
            document.content
        )
        temporary = (
            write_usb._usb_media_temporary_path(
                preflight,
                paths,
            )
        )
        real_unlink = Path.unlink

        def failing_unlink(
            path: Path,
            *args: object,
            **kwargs: object,
        ) -> None:
            if path == temporary:
                raise OSError(
                    "injected recovery-removal unlink failure"
                )
            real_unlink(
                path,
                *args,
                **kwargs,
            )

        monkeypatch.setattr(
            Path,
            "unlink",
            failing_unlink,
        )

        with pytest.raises(
            write_usb._FavoritesUsbMediaMutationError,
            match="finalize intended-only USB recovery removal",
        ) as raised:
            write_usb._remove_usb_recovery_intended_only_hpd(
                preflight,
                paths,
                backup,
                document,
            )

        artifact = raised.value.recovery_artifact
        assert raised.value.mutation_started is True
        assert artifact is not None
        assert artifact.path == temporary
        assert artifact.managed_filename == document.filename
        assert artifact.content_sha256 == (
            write_usb._usb_media_content_sha256(
                document.content
            )
        )
        assert not write_usb.os.path.lexists(
            target
        )
        assert temporary.read_bytes() == document.content


def test_usb_recovery_removal_does_not_delete_postunlink_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        document,
    ) = _usb_recovery_removal_fixture(
        tmp_path
    )
    target = (
        favorites_directory
        / document.filename
    )
    postunlink = b"post-unlink replacement"

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        target.write_bytes(
            document.content
        )
        temporary = (
            write_usb._usb_media_temporary_path(
                preflight,
                paths,
            )
        )
        real_unlink = Path.unlink
        injected = False

        def replacing_unlink(
            path: Path,
            *args: object,
            **kwargs: object,
        ) -> None:
            nonlocal injected
            real_unlink(
                path,
                *args,
                **kwargs,
            )
            if (
                path == temporary
                and not injected
            ):
                injected = True
                target.write_bytes(
                    postunlink
                )

        monkeypatch.setattr(
            Path,
            "unlink",
            replacing_unlink,
        )

        with pytest.raises(
            write_usb._FavoritesUsbMediaMutationError,
            match="target appeared after intended-only recovery removal",
        ) as raised:
            write_usb._remove_usb_recovery_intended_only_hpd(
                preflight,
                paths,
                backup,
                document,
            )

        assert injected
        assert raised.value.mutation_started is True
        assert raised.value.recovery_artifact is None
        assert target.read_bytes() == postunlink
        assert not write_usb.os.path.lexists(
            temporary
        )


def _usb_surviving_recovery_removal_artifact(
    preflight: write_usb.FavoritesUsbWritePreflight,
    paths: write_usb._FavoritesUsbHostOperationPaths,
    backup: write_usb._FavoritesUsbVerifiedBackup,
    favorites_directory: Path,
    document: FavoritesStorageDocument,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[
    write_usb._FavoritesUsbMediaRecoveryArtifact,
    Path,
    Path,
]:
    target = (
        favorites_directory
        / document.filename
    )
    target.write_bytes(
        document.content
    )
    temporary = (
        write_usb._usb_media_temporary_path(
            preflight,
            paths,
        )
    )

    real_unlink = Path.unlink

    def failing_unlink(
        path: Path,
        *args: object,
        **kwargs: object,
    ) -> None:
        if path == temporary:
            raise OSError(
                "injected surviving recovery-removal artifact"
            )
        real_unlink(
            path,
            *args,
            **kwargs,
        )

    monkeypatch.setattr(
        Path,
        "unlink",
        failing_unlink,
    )

    try:
        with pytest.raises(
            write_usb._FavoritesUsbMediaMutationError,
            match="finalize intended-only USB recovery removal",
        ) as raised:
            write_usb._remove_usb_recovery_intended_only_hpd(
                preflight,
                paths,
                backup,
                document,
            )
    finally:
        monkeypatch.setattr(
            Path,
            "unlink",
            real_unlink,
        )

    artifact = raised.value.recovery_artifact
    assert raised.value.mutation_started is True
    assert artifact is not None
    assert artifact.path == temporary
    assert artifact.managed_filename == document.filename
    assert artifact.content_sha256 == (
        write_usb._usb_media_content_sha256(
            document.content
        )
    )
    assert not write_usb.os.path.lexists(
        target
    )
    assert temporary.read_bytes() == document.content

    return (
        artifact,
        target,
        temporary,
    )


def test_usb_recovery_removal_artifact_binds_exact_intended_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        document,
    ) = _usb_recovery_removal_fixture(
        tmp_path
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        (
            artifact,
            target,
            temporary,
        ) = _usb_surviving_recovery_removal_artifact(
            preflight,
            paths,
            backup,
            favorites_directory,
            document,
            monkeypatch,
        )

        verified = (
            write_usb._require_current_usb_recovery_removal_artifact(
                preflight,
                paths,
                backup,
                artifact,
            )
        )

        assert verified.artifact == artifact
        assert verified.intended_document == document
        assert (
            verified.target_evidence.temporary_path
            == temporary
        )
        assert (
            verified.device,
            verified.inode,
            verified.size,
            verified.modified_ns,
            verified.mode,
        ) == write_usb._usb_recovery_artifact_fingerprint(
            temporary.lstat()
        )
        assert not write_usb.os.path.lexists(
            target
        )


def test_usb_recovery_removal_artifact_refuses_target_reappearance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        document,
    ) = _usb_recovery_removal_fixture(
        tmp_path
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        (
            artifact,
            target,
            temporary,
        ) = _usb_surviving_recovery_removal_artifact(
            preflight,
            paths,
            backup,
            favorites_directory,
            document,
            monkeypatch,
        )
        concurrent = b"concurrent managed target"
        target.write_bytes(
            concurrent
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="managed target exists",
        ):
            write_usb._require_current_usb_recovery_removal_artifact(
                preflight,
                paths,
                backup,
                artifact,
            )

        assert target.read_bytes() == concurrent
        assert temporary.read_bytes() == document.content


def test_usb_recovery_removal_artifact_refuses_wrong_provenance_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        document,
    ) = _usb_recovery_removal_fixture(
        tmp_path
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        (
            artifact,
            _target,
            temporary,
        ) = _usb_surviving_recovery_removal_artifact(
            preflight,
            paths,
            backup,
            favorites_directory,
            document,
            monkeypatch,
        )
        wrong = write_usb._FavoritesUsbMediaRecoveryArtifact(
            path=artifact.path,
            managed_filename=artifact.managed_filename,
            content_sha256="0" * 64,
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="provenance SHA-256",
        ):
            write_usb._require_current_usb_recovery_removal_artifact(
                preflight,
                paths,
                backup,
                wrong,
            )

        assert temporary.read_bytes() == document.content


def test_usb_recovery_removal_artifact_refuses_non_removal_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        document,
    ) = _usb_recovery_removal_fixture(
        tmp_path
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        (
            artifact,
            _target,
            temporary,
        ) = _usb_surviving_recovery_removal_artifact(
            preflight,
            paths,
            backup,
            favorites_directory,
            document,
            monkeypatch,
        )
        unrelated = write_usb._FavoritesUsbMediaRecoveryArtifact(
            path=artifact.path,
            managed_filename="other.hpd",
            content_sha256=artifact.content_sha256,
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="bound recovery plan",
        ):
            write_usb._require_current_usb_recovery_removal_artifact(
                preflight,
                paths,
                backup,
                unrelated,
            )

        assert temporary.read_bytes() == document.content


def test_usb_recovery_removal_artifact_refuses_changed_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        document,
    ) = _usb_recovery_removal_fixture(
        tmp_path
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        (
            artifact,
            target,
            temporary,
        ) = _usb_surviving_recovery_removal_artifact(
            preflight,
            paths,
            backup,
            favorites_directory,
            document,
            monkeypatch,
        )
        changed = b"changed recovery-removal artifact"
        temporary.write_bytes(
            changed
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="exact intended-only HPD",
        ):
            write_usb._require_current_usb_recovery_removal_artifact(
                preflight,
                paths,
                backup,
                artifact,
            )

        assert not write_usb.os.path.lexists(
            target
        )
        assert temporary.read_bytes() == changed


def test_usb_recovery_removal_artifact_cleanup_succeeds_with_absent_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        document,
    ) = _usb_recovery_removal_fixture(
        tmp_path
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        (
            artifact,
            target,
            temporary,
        ) = _usb_surviving_recovery_removal_artifact(
            preflight,
            paths,
            backup,
            favorites_directory,
            document,
            monkeypatch,
        )
        verified = (
            write_usb._require_current_usb_recovery_removal_artifact(
                preflight,
                paths,
                backup,
                artifact,
            )
        )

        write_usb._cleanup_verified_usb_recovery_removal_artifact(
            preflight,
            paths,
            backup,
            verified,
        )

        assert not write_usb.os.path.lexists(
            target
        )
        assert not write_usb.os.path.lexists(
            temporary
        )


def test_usb_recovery_removal_artifact_cleanup_refuses_target_reappearance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        document,
    ) = _usb_recovery_removal_fixture(
        tmp_path
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        (
            artifact,
            target,
            temporary,
        ) = _usb_surviving_recovery_removal_artifact(
            preflight,
            paths,
            backup,
            favorites_directory,
            document,
            monkeypatch,
        )
        verified = (
            write_usb._require_current_usb_recovery_removal_artifact(
                preflight,
                paths,
                backup,
                artifact,
            )
        )
        concurrent = b"cleanup concurrent target"
        target.write_bytes(
            concurrent
        )

        with pytest.raises(
            write_usb._FavoritesUsbMediaMutationError,
            match="revalidate verified USB recovery-removal artifact|managed target exists",
        ) as raised:
            write_usb._cleanup_verified_usb_recovery_removal_artifact(
                preflight,
                paths,
                backup,
                verified,
            )

        assert raised.value.mutation_started is False
        assert target.read_bytes() == concurrent
        assert temporary.read_bytes() == document.content


def test_usb_recovery_removal_artifact_cleanup_refuses_changed_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        document,
    ) = _usb_recovery_removal_fixture(
        tmp_path
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        (
            artifact,
            target,
            temporary,
        ) = _usb_surviving_recovery_removal_artifact(
            preflight,
            paths,
            backup,
            favorites_directory,
            document,
            monkeypatch,
        )
        verified = (
            write_usb._require_current_usb_recovery_removal_artifact(
                preflight,
                paths,
                backup,
                artifact,
            )
        )
        changed = b"changed before cleanup"
        temporary.write_bytes(
            changed
        )

        with pytest.raises(
            write_usb._FavoritesUsbMediaMutationError,
            match="revalidate verified USB recovery-removal artifact",
        ) as raised:
            write_usb._cleanup_verified_usb_recovery_removal_artifact(
                preflight,
                paths,
                backup,
                verified,
            )

        assert raised.value.mutation_started is False
        assert not write_usb.os.path.lexists(
            target
        )
        assert temporary.read_bytes() == changed


def test_usb_recovery_removal_artifact_cleanup_does_not_delete_postunlink_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        document,
    ) = _usb_recovery_removal_fixture(
        tmp_path
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        (
            artifact,
            target,
            temporary,
        ) = _usb_surviving_recovery_removal_artifact(
            preflight,
            paths,
            backup,
            favorites_directory,
            document,
            monkeypatch,
        )
        verified = (
            write_usb._require_current_usb_recovery_removal_artifact(
                preflight,
                paths,
                backup,
                artifact,
            )
        )

        real_unlink = write_usb.os.unlink
        calls = 0
        replacement = b"post-unlink artifact replacement"

        def replacing_unlink(
            path: str | bytes | Path,
            *args: object,
            **kwargs: object,
        ) -> None:
            nonlocal calls
            calls += 1
            real_unlink(
                path,
                *args,
                **kwargs,
            )
            Path(path).write_bytes(
                replacement
            )

        monkeypatch.setattr(
            write_usb.os,
            "unlink",
            replacing_unlink,
        )

        with pytest.raises(
            write_usb._FavoritesUsbMediaMutationError,
            match="temporary path absent",
        ) as raised:
            write_usb._cleanup_verified_usb_recovery_removal_artifact(
                preflight,
                paths,
                backup,
                verified,
            )

        assert calls == 1
        assert raised.value.mutation_started is True
        assert not write_usb.os.path.lexists(
            target
        )
        assert temporary.read_bytes() == replacement


def test_usb_recovery_removal_artifact_cleanup_preserves_postunlink_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        document,
    ) = _usb_recovery_removal_fixture(
        tmp_path
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        (
            artifact,
            target,
            temporary,
        ) = _usb_surviving_recovery_removal_artifact(
            preflight,
            paths,
            backup,
            favorites_directory,
            document,
            monkeypatch,
        )
        verified = (
            write_usb._require_current_usb_recovery_removal_artifact(
                preflight,
                paths,
                backup,
                artifact,
            )
        )

        real_unlink = write_usb.os.unlink
        calls = 0
        concurrent = b"post-unlink managed target"

        def target_creating_unlink(
            path: str | bytes | Path,
            *args: object,
            **kwargs: object,
        ) -> None:
            nonlocal calls
            calls += 1
            real_unlink(
                path,
                *args,
                **kwargs,
            )
            target.write_bytes(
                concurrent
            )

        monkeypatch.setattr(
            write_usb.os,
            "unlink",
            target_creating_unlink,
        )

        with pytest.raises(
            write_usb._FavoritesUsbMediaMutationError,
            match="managed target appeared after",
        ) as raised:
            write_usb._cleanup_verified_usb_recovery_removal_artifact(
                preflight,
                paths,
                backup,
                verified,
            )

        assert calls == 1
        assert raised.value.mutation_started is True
        assert target.read_bytes() == concurrent
        assert not write_usb.os.path.lexists(
            temporary
        )


_RECOVERY_ORCHESTRATION_BASELINE_KEEP = (
    b"TargetModel\tBCDx36HP\r\n"
    b"FormatVersion\t1.00\r\n"
    b"Department\tBaseline keep\r\n"
)
_RECOVERY_ORCHESTRATION_INTENDED_KEEP = (
    b"TargetModel\tBCDx36HP\r\n"
    b"FormatVersion\t1.00\r\n"
    b"Department\tIntended keep\r\n"
)
_RECOVERY_ORCHESTRATION_REMOVED = (
    b"TargetModel\tBCDx36HP\r\n"
    b"FormatVersion\t1.00\r\n"
    b"Department\tBaseline removed\r\n"
)
_RECOVERY_ORCHESTRATION_ADDED = (
    b"TargetModel\tBCDx36HP\r\n"
    b"FormatVersion\t1.00\r\n"
    b"Department\tIntended added\r\n"
)


def _usb_recovery_orchestration_fixture(
    tmp_path: Path,
) -> tuple[
    write_usb.FavoritesUsbWritePreflight,
    Path,
    Path,
    FavoritesStorageSnapshot,
    FavoritesStorageSnapshot,
]:
    baseline = FavoritesStorageSnapshot(
        catalog_bytes=_BASELINE_CATALOG,
        documents=(
            FavoritesStorageDocument(
                filename="keep.hpd",
                content=_RECOVERY_ORCHESTRATION_BASELINE_KEEP,
            ),
            FavoritesStorageDocument(
                filename="removed.hpd",
                content=_RECOVERY_ORCHESTRATION_REMOVED,
            ),
        ),
    )
    intended = FavoritesStorageSnapshot(
        catalog_bytes=_CHANGED_CATALOG,
        documents=(
            FavoritesStorageDocument(
                filename="added.hpd",
                content=_RECOVERY_ORCHESTRATION_ADDED,
            ),
            FavoritesStorageDocument(
                filename="keep.hpd",
                content=_RECOVERY_ORCHESTRATION_INTENDED_KEEP,
            ),
        ),
    )

    (
        preflight,
        _prepared,
        favorites_directory,
    ) = _prepared_usb_activation_fixture(
        tmp_path,
        baseline=baseline,
        intended=intended,
    )
    host_root = (
        tmp_path
        / "host-state"
        / "favorites-usb-writes"
    )
    return (
        preflight,
        favorites_directory,
        host_root,
        baseline,
        intended,
    )


def _set_usb_recovery_fixture_to_intended_state(
    favorites_directory: Path,
    intended: FavoritesStorageSnapshot,
) -> None:
    removed = (
        favorites_directory
        / "removed.hpd"
    )
    if write_usb.os.path.lexists(
        removed
    ):
        removed.unlink()

    (
        favorites_directory
        / "f_list.cfg"
    ).write_bytes(
        intended.catalog_bytes
    )
    for document in intended.documents:
        (
            favorites_directory
            / document.filename
        ).write_bytes(
            document.content
        )


def test_usb_recovery_orchestrator_restores_exact_baseline(
    tmp_path: Path,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        baseline,
        intended,
    ) = _usb_recovery_orchestration_fixture(
        tmp_path
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        _set_usb_recovery_fixture_to_intended_state(
            favorites_directory,
            intended,
        )

        recovered = (
            write_usb._recover_usb_active_managed_state(
                preflight,
                paths,
                backup,
            )
        )

        assert recovered.snapshot == baseline
        assert recovered.snapshot_sha256 == (
            write_usb.favorites_storage_snapshot_sha256(
                baseline
            )
        )
        assert recovered.unmanaged_sha256 == (
            preflight.unmanaged_sha256
        )
        assert (
            write_usb._read_usb_recovery_managed_snapshot(
                favorites_directory
            )
            == baseline
        )
        assert not write_usb.os.path.lexists(
            write_usb._usb_media_temporary_path(
                preflight,
                paths,
            )
        )


def test_usb_recovery_orchestrator_binds_baseline_artifact_before_restore_and_cleans_after_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        baseline,
        intended,
    ) = _usb_recovery_orchestration_fixture(
        tmp_path
    )
    baseline_documents = {
        document.filename: document
        for document in baseline.documents
    }

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )

        (
            favorites_directory
            / "keep.hpd"
        ).write_bytes(
            _RECOVERY_ORCHESTRATION_INTENDED_KEEP
        )
        (
            favorites_directory
            / "added.hpd"
        ).write_bytes(
            _RECOVERY_ORCHESTRATION_ADDED
        )
        (
            favorites_directory
            / "f_list.cfg"
        ).write_bytes(
            intended.catalog_bytes
        )

        removed = (
            favorites_directory
            / "removed.hpd"
        )
        temporary = (
            write_usb._usb_media_temporary_path(
                preflight,
                paths,
            )
        )
        write_usb.os.replace(
            removed,
            temporary,
        )
        artifact = (
            write_usb._FavoritesUsbMediaRecoveryArtifact(
                path=temporary,
                managed_filename="removed.hpd",
                content_sha256=(
                    write_usb._usb_media_content_sha256(
                        baseline_documents[
                            "removed.hpd"
                        ].content
                    )
                ),
            )
        )

        events: list[str] = []
        real_bind = (
            write_usb._require_current_usb_recovery_artifact
        )
        real_restore = (
            write_usb._restore_usb_active_managed_file
        )
        real_cleanup = (
            write_usb._cleanup_verified_usb_recovery_artifact
        )
        real_remove = (
            write_usb._remove_usb_recovery_intended_only_hpd
        )

        def recording_bind(
            current_preflight: write_usb.FavoritesUsbWritePreflight,
            current_paths: write_usb._FavoritesUsbHostOperationPaths,
            current_backup: write_usb._FavoritesUsbVerifiedBackup,
            current_artifact: write_usb._FavoritesUsbMediaRecoveryArtifact,
        ) -> write_usb._FavoritesUsbVerifiedRecoveryArtifact:
            events.append(
                "bind:baseline-artifact"
            )
            return real_bind(
                current_preflight,
                current_paths,
                current_backup,
                current_artifact,
            )

        def recording_restore(
            current_preflight: write_usb.FavoritesUsbWritePreflight,
            current_paths: write_usb._FavoritesUsbHostOperationPaths,
            filename: str,
            baseline_content: bytes,
            *,
            allowed_existing_content: bytes | None,
            allow_absent: bool,
        ) -> None:
            events.append(
                f"restore:{filename}"
            )
            real_restore(
                current_preflight,
                current_paths,
                filename,
                baseline_content,
                allowed_existing_content=allowed_existing_content,
                allow_absent=allow_absent,
            )

        def recording_cleanup(
            current_preflight: write_usb.FavoritesUsbWritePreflight,
            current_paths: write_usb._FavoritesUsbHostOperationPaths,
            current_backup: write_usb._FavoritesUsbVerifiedBackup,
            verified_artifact: write_usb._FavoritesUsbVerifiedRecoveryArtifact,
        ) -> None:
            events.append(
                "cleanup:baseline-artifact"
            )
            real_cleanup(
                current_preflight,
                current_paths,
                current_backup,
                verified_artifact,
            )

        def recording_remove(
            current_preflight: write_usb.FavoritesUsbWritePreflight,
            current_paths: write_usb._FavoritesUsbHostOperationPaths,
            current_backup: write_usb._FavoritesUsbVerifiedBackup,
            document: FavoritesStorageDocument,
        ) -> None:
            events.append(
                f"remove:{document.filename}"
            )
            real_remove(
                current_preflight,
                current_paths,
                current_backup,
                document,
            )

        monkeypatch.setattr(
            write_usb,
            "_require_current_usb_recovery_artifact",
            recording_bind,
        )
        monkeypatch.setattr(
            write_usb,
            "_restore_usb_active_managed_file",
            recording_restore,
        )
        monkeypatch.setattr(
            write_usb,
            "_cleanup_verified_usb_recovery_artifact",
            recording_cleanup,
        )
        monkeypatch.setattr(
            write_usb,
            "_remove_usb_recovery_intended_only_hpd",
            recording_remove,
        )

        recovered = (
            write_usb._recover_usb_active_managed_state(
                preflight,
                paths,
                backup,
                activation_artifact=artifact,
            )
        )

        assert recovered.snapshot == baseline
        assert events == [
            "bind:baseline-artifact",
            "restore:keep.hpd",
            "restore:removed.hpd",
            "restore:f_list.cfg",
            "cleanup:baseline-artifact",
            "bind:baseline-artifact",
            "bind:baseline-artifact",
            "remove:added.hpd",
        ]
        assert not write_usb.os.path.lexists(
            temporary
        )


def test_usb_recovery_orchestrator_reconciles_intended_only_cleanup_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        baseline,
        intended,
    ) = _usb_recovery_orchestration_fixture(
        tmp_path
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        _set_usb_recovery_fixture_to_intended_state(
            favorites_directory,
            intended,
        )
        temporary = (
            write_usb._usb_media_temporary_path(
                preflight,
                paths,
            )
        )

        real_unlink = Path.unlink
        injected = False

        def failing_once(
            path: Path,
            *args: object,
            **kwargs: object,
        ) -> None:
            nonlocal injected
            if (
                path == temporary
                and not injected
            ):
                injected = True
                raise OSError(
                    "injected intended-only cleanup survivor"
                )
            real_unlink(
                path,
                *args,
                **kwargs,
            )

        monkeypatch.setattr(
            Path,
            "unlink",
            failing_once,
        )

        recovered = (
            write_usb._recover_usb_active_managed_state(
                preflight,
                paths,
                backup,
            )
        )

        assert injected
        assert recovered.snapshot == baseline
        assert not write_usb.os.path.lexists(
            favorites_directory
            / "added.hpd"
        )
        assert not write_usb.os.path.lexists(
            temporary
        )


def test_usb_recovery_orchestrator_refuses_unknown_same_name_content_without_overwrite(
    tmp_path: Path,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        _baseline,
        intended,
    ) = _usb_recovery_orchestration_fixture(
        tmp_path
    )
    unknown = b"unknown concurrent managed content"

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        _set_usb_recovery_fixture_to_intended_state(
            favorites_directory,
            intended,
        )
        (
            favorites_directory
            / "keep.hpd"
        ).write_bytes(
            unknown
        )

        with pytest.raises(
            write_usb._FavoritesUsbMediaMutationError,
            match="explicitly allowed operation-known state",
        ) as raised:
            write_usb._recover_usb_active_managed_state(
                preflight,
                paths,
                backup,
            )

        assert raised.value.mutation_started is False
        assert (
            favorites_directory
            / "keep.hpd"
        ).read_bytes() == unknown


def test_usb_recovery_orchestrator_never_deletes_unknown_extra_managed_hpd(
    tmp_path: Path,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        _baseline,
        intended,
    ) = _usb_recovery_orchestration_fixture(
        tmp_path
    )
    unknown = b"unknown extra managed HPD"

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        _set_usb_recovery_fixture_to_intended_state(
            favorites_directory,
            intended,
        )
        extra = (
            favorites_directory
            / "external.hpd"
        )
        extra.write_bytes(
            unknown
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="does not exactly match",
        ):
            write_usb._recover_usb_active_managed_state(
                preflight,
                paths,
                backup,
            )

        assert extra.read_bytes() == unknown


def test_usb_recovery_orchestrator_refuses_unmanaged_change_before_managed_mutation(
    tmp_path: Path,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        baseline,
        _intended,
    ) = _usb_recovery_orchestration_fixture(
        tmp_path
    )
    unmanaged = (
        favorites_directory
        / "external-notes.txt"
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        unmanaged.write_bytes(
            b"unexpected unmanaged content"
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="unmanaged changes",
        ):
            write_usb._recover_usb_active_managed_state(
                preflight,
                paths,
                backup,
            )

        assert (
            write_usb._read_usb_recovery_managed_snapshot(
                favorites_directory
            )
            == baseline
        )
        assert unmanaged.read_bytes() == (
            b"unexpected unmanaged content"
        )


def test_usb_recovery_orchestrator_detects_managed_change_between_final_readbacks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        _baseline,
        intended,
    ) = _usb_recovery_orchestration_fixture(
        tmp_path
    )
    raced = b"managed content changed between final readbacks"

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        _set_usb_recovery_fixture_to_intended_state(
            favorites_directory,
            intended,
        )

        real_read = (
            write_usb._read_usb_recovery_managed_snapshot
        )
        calls = 0

        def racing_read(
            path: Path,
        ) -> FavoritesStorageSnapshot:
            nonlocal calls
            snapshot = real_read(
                path
            )
            calls += 1
            if calls == 1:
                (
                    favorites_directory
                    / "keep.hpd"
                ).write_bytes(
                    raced
                )
            return snapshot

        monkeypatch.setattr(
            write_usb,
            "_read_usb_recovery_managed_snapshot",
            racing_read,
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="changed during final exact baseline readback",
        ):
            write_usb._recover_usb_active_managed_state(
                preflight,
                paths,
                backup,
            )

        assert calls == 2
        assert (
            favorites_directory
            / "keep.hpd"
        ).read_bytes() == raced


def test_usb_recovery_orchestrator_refuses_unowned_bounded_temp_before_mutation(
    tmp_path: Path,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        baseline,
        _intended,
    ) = _usb_recovery_orchestration_fixture(
        tmp_path
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        temporary = (
            write_usb._usb_media_temporary_path(
                preflight,
                paths,
            )
        )
        temporary.write_bytes(
            b"unowned bounded temp content"
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="without verified activation-artifact provenance",
        ):
            write_usb._recover_usb_active_managed_state(
                preflight,
                paths,
                backup,
            )

        assert temporary.read_bytes() == (
            b"unowned bounded temp content"
        )
        assert (
            write_usb._read_usb_recovery_managed_snapshot(
                favorites_directory
            )
            == baseline
        )


def _usb_activation_orchestration_prepared(
    tmp_path: Path,
) -> tuple[
    write_usb.FavoritesUsbWritePreflight,
    Path,
    Path,
    FavoritesStorageSnapshot,
    FavoritesStorageSnapshot,
]:
    return _usb_recovery_orchestration_fixture(
        tmp_path
    )


def test_usb_activation_orchestrator_restores_exact_intended_state(
    tmp_path: Path,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        _baseline,
        intended,
    ) = _usb_activation_orchestration_prepared(
        tmp_path
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        prepared = (
            write_usb._create_verified_usb_host_staging(
                preflight,
                paths,
                backup,
            )
        )
        preactivation = (
            write_usb._require_usb_preactivation_ready(
                preflight,
                paths,
                backup,
                prepared,
            )
        )

        activated = (
            write_usb._activate_usb_managed_state(
                preflight,
                paths,
                backup,
                prepared,
                preactivation,
            )
        )

        assert activated.snapshot == intended
        assert activated.snapshot_sha256 == (
            write_usb.favorites_storage_snapshot_sha256(
                intended
            )
        )
        assert activated.unmanaged_sha256 == (
            preflight.unmanaged_sha256
        )
        assert (
            write_usb._read_usb_recovery_managed_snapshot(
                favorites_directory
            )
            == intended
        )
        assert not write_usb.os.path.lexists(
            write_usb._usb_media_temporary_path(
                preflight,
                paths,
            )
        )


def test_usb_activation_orchestrator_orders_hpd_writes_catalog_then_deletions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        _favorites_directory,
        host_root,
        _baseline,
        intended,
    ) = _usb_activation_orchestration_prepared(
        tmp_path
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        prepared = (
            write_usb._create_verified_usb_host_staging(
                preflight,
                paths,
                backup,
            )
        )
        preactivation = (
            write_usb._require_usb_preactivation_ready(
                preflight,
                paths,
                backup,
                prepared,
            )
        )

        events: list[str] = []
        real_write = (
            write_usb._write_usb_activation_managed_file_exact_state
        )
        real_delete = (
            write_usb._delete_usb_active_managed_hpd
        )

        def recording_write(
            current_preflight: write_usb.FavoritesUsbWritePreflight,
            current_paths: write_usb._FavoritesUsbHostOperationPaths,
            filename: str,
            intended_content: bytes,
            *,
            baseline_content: bytes | None,
        ) -> None:
            events.append(
                f"write:{filename}"
            )
            real_write(
                current_preflight,
                current_paths,
                filename,
                intended_content,
                baseline_content=baseline_content,
            )

        def recording_delete(
            current_preflight: write_usb.FavoritesUsbWritePreflight,
            current_paths: write_usb._FavoritesUsbHostOperationPaths,
            filename: str,
            expected_content: bytes,
        ) -> None:
            events.append(
                f"delete:{filename}"
            )
            real_delete(
                current_preflight,
                current_paths,
                filename,
                expected_content,
            )

        monkeypatch.setattr(
            write_usb,
            "_write_usb_activation_managed_file_exact_state",
            recording_write,
        )
        monkeypatch.setattr(
            write_usb,
            "_delete_usb_active_managed_hpd",
            recording_delete,
        )

        activated = (
            write_usb._activate_usb_managed_state(
                preflight,
                paths,
                backup,
                prepared,
                preactivation,
            )
        )

        assert activated.snapshot == intended
        assert events == [
            "write:added.hpd",
            "write:keep.hpd",
            "write:f_list.cfg",
            "delete:removed.hpd",
        ]


def test_usb_activation_orchestrator_refuses_unknown_first_write_state_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        baseline,
        _intended,
    ) = _usb_activation_orchestration_prepared(
        tmp_path
    )
    unknown = b"unknown concurrent activation HPD"

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        prepared = (
            write_usb._create_verified_usb_host_staging(
                preflight,
                paths,
                backup,
            )
        )
        preactivation = (
            write_usb._require_usb_preactivation_ready(
                preflight,
                paths,
                backup,
                prepared,
            )
        )

        real_preactivation = (
            write_usb._require_usb_preactivation_ready
        )
        raced = False

        def racing_preactivation(
            current_preflight: write_usb.FavoritesUsbWritePreflight,
            current_paths: write_usb._FavoritesUsbHostOperationPaths,
            current_backup: write_usb._FavoritesUsbVerifiedBackup,
            current_prepared: write_usb._FavoritesUsbPreparedStage,
        ) -> write_usb._FavoritesUsbPreactivationEvidence:
            nonlocal raced
            evidence = real_preactivation(
                current_preflight,
                current_paths,
                current_backup,
                current_prepared,
            )
            if not raced:
                raced = True
                (
                    favorites_directory
                    / "added.hpd"
                ).write_bytes(
                    unknown
                )
            return evidence

        monkeypatch.setattr(
            write_usb,
            "_require_usb_preactivation_ready",
            racing_preactivation,
        )

        with pytest.raises(
            write_usb._FavoritesUsbActivationExecutionError,
            match="explicitly allowed operation-known state",
        ) as raised:
            write_usb._activate_usb_managed_state(
                preflight,
                paths,
                backup,
                prepared,
                preactivation,
            )

        assert raised.value.stage is (
            write_usb._FavoritesUsbActivationFailureStage
            .MUTATION_EXECUTION
        )
        assert raised.value.mutation_started is False
        assert raised.value.recovery_artifact is None
        assert (
            favorites_directory
            / "added.hpd"
        ).read_bytes() == unknown
        assert (
            favorites_directory
            / "keep.hpd"
        ).read_bytes() == next(
            document.content
            for document in baseline.documents
            if document.filename == "keep.hpd"
        )


def test_usb_activation_orchestrator_aggregates_prior_successful_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        baseline,
        intended,
    ) = _usb_activation_orchestration_prepared(
        tmp_path
    )
    intended_documents = {
        document.filename: document
        for document in intended.documents
    }
    baseline_documents = {
        document.filename: document
        for document in baseline.documents
    }

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        prepared = (
            write_usb._create_verified_usb_host_staging(
                preflight,
                paths,
                backup,
            )
        )
        preactivation = (
            write_usb._require_usb_preactivation_ready(
                preflight,
                paths,
                backup,
                prepared,
            )
        )

        real_write = (
            write_usb._write_usb_activation_managed_file_exact_state
        )
        calls = 0

        def failing_second_write(
            current_preflight: write_usb.FavoritesUsbWritePreflight,
            current_paths: write_usb._FavoritesUsbHostOperationPaths,
            filename: str,
            intended_content: bytes,
            *,
            baseline_content: bytes | None,
        ) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise write_usb._FavoritesUsbMediaMutationError(
                    favorites_directory
                    / filename,
                    "injected later activation refusal before local mutation",
                    mutation_started=False,
                )
            real_write(
                current_preflight,
                current_paths,
                filename,
                intended_content,
                baseline_content=baseline_content,
            )

        monkeypatch.setattr(
            write_usb,
            "_write_usb_activation_managed_file_exact_state",
            failing_second_write,
        )

        with pytest.raises(
            write_usb._FavoritesUsbActivationExecutionError,
            match="injected later activation refusal",
        ) as raised:
            write_usb._activate_usb_managed_state(
                preflight,
                paths,
                backup,
                prepared,
                preactivation,
            )

        assert calls == 2
        assert raised.value.stage is (
            write_usb._FavoritesUsbActivationFailureStage
            .MUTATION_EXECUTION
        )
        assert raised.value.mutation_started is True
        assert raised.value.recovery_artifact is None
        assert (
            favorites_directory
            / "added.hpd"
        ).read_bytes() == (
            intended_documents[
                "added.hpd"
            ].content
        )
        assert (
            favorites_directory
            / "keep.hpd"
        ).read_bytes() == (
            baseline_documents[
                "keep.hpd"
            ].content
        )


def test_usb_activation_orchestrator_preserves_deletion_artifact_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        baseline,
        _intended,
    ) = _usb_activation_orchestration_prepared(
        tmp_path
    )
    baseline_documents = {
        document.filename: document
        for document in baseline.documents
    }

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        prepared = (
            write_usb._create_verified_usb_host_staging(
                preflight,
                paths,
                backup,
            )
        )
        preactivation = (
            write_usb._require_usb_preactivation_ready(
                preflight,
                paths,
                backup,
                prepared,
            )
        )

        temporary = (
            write_usb._usb_media_temporary_path(
                preflight,
                paths,
            )
        )
        real_unlink = Path.unlink

        def failing_bounded_unlink(
            path: Path,
            *args: object,
            **kwargs: object,
        ) -> None:
            if path == temporary:
                raise OSError(
                    "injected activation deletion artifact survivor"
                )
            real_unlink(
                path,
                *args,
                **kwargs,
            )

        monkeypatch.setattr(
            Path,
            "unlink",
            failing_bounded_unlink,
        )

        with pytest.raises(
            write_usb._FavoritesUsbActivationExecutionError,
            match="finalize active HPD deletion",
        ) as raised:
            write_usb._activate_usb_managed_state(
                preflight,
                paths,
                backup,
                prepared,
                preactivation,
            )

        artifact = raised.value.recovery_artifact
        assert raised.value.stage is (
            write_usb._FavoritesUsbActivationFailureStage
            .MUTATION_EXECUTION
        )
        assert raised.value.mutation_started is True
        assert artifact is not None
        assert artifact.path == temporary
        assert artifact.managed_filename == "removed.hpd"
        assert artifact.content_sha256 == (
            write_usb._usb_media_content_sha256(
                baseline_documents[
                    "removed.hpd"
                ].content
            )
        )
        assert not write_usb.os.path.lexists(
            favorites_directory
            / "removed.hpd"
        )
        assert temporary.read_bytes() == (
            baseline_documents[
                "removed.hpd"
            ].content
        )


def test_usb_activation_orchestrator_classifies_postactivation_readback_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        _baseline,
        intended,
    ) = _usb_activation_orchestration_prepared(
        tmp_path
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        prepared = (
            write_usb._create_verified_usb_host_staging(
                preflight,
                paths,
                backup,
            )
        )
        preactivation = (
            write_usb._require_usb_preactivation_ready(
                preflight,
                paths,
                backup,
                prepared,
            )
        )

        def failing_postactivation_read(
            path: Path,
        ) -> FavoritesStorageSnapshot:
            raise write_usb._FavoritesUsbWritePreparationError(
                path,
                "injected postactivation managed readback failure",
            )

        monkeypatch.setattr(
            write_usb,
            "_read_usb_activation_managed_snapshot",
            failing_postactivation_read,
        )

        with pytest.raises(
            write_usb._FavoritesUsbActivationExecutionError,
            match="postactivation_verification",
        ) as raised:
            write_usb._activate_usb_managed_state(
                preflight,
                paths,
                backup,
                prepared,
                preactivation,
            )

        assert raised.value.stage is (
            write_usb._FavoritesUsbActivationFailureStage
            .POSTACTIVATION_VERIFICATION
        )
        assert raised.value.mutation_started is True
        assert raised.value.recovery_artifact is None
        assert (
            write_usb._read_usb_recovery_managed_snapshot(
                favorites_directory
            )
            == intended
        )


def test_usb_activation_orchestrator_refuses_noop_without_media_mutation(
    tmp_path: Path,
) -> None:
    baseline = _snapshot()
    (
        preflight,
        _mountinfo,
        favorites_directory,
    ) = _prepared_usb_activation_fixture(
        tmp_path,
        baseline=baseline,
        intended=baseline,
    )
    host_root = (
        tmp_path
        / "host-state-noop"
        / "favorites-usb-writes"
    )
    before = favorites_tree_evidence(
        favorites_directory
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        prepared = (
            write_usb._create_verified_usb_host_staging(
                preflight,
                paths,
                backup,
            )
        )
        preactivation = (
            write_usb._require_usb_preactivation_ready(
                preflight,
                paths,
                backup,
                prepared,
            )
        )

        with pytest.raises(
            write_usb._FavoritesUsbActivationExecutionError,
            match="No-op Favorites USB write plan",
        ) as raised:
            write_usb._activate_usb_managed_state(
                preflight,
                paths,
                backup,
                prepared,
                preactivation,
            )

        assert raised.value.stage is (
            write_usb._FavoritesUsbActivationFailureStage
            .MUTATION_EXECUTION
        )
        assert raised.value.mutation_started is False
        assert raised.value.recovery_artifact is None

    assert favorites_tree_evidence(
        favorites_directory
    ) == before


def test_usb_activation_mutation_barrier_runs_once_before_first_media_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        _favorites_directory,
        host_root,
        _baseline,
        intended,
    ) = _usb_activation_orchestration_prepared(
        tmp_path
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        prepared = (
            write_usb._create_verified_usb_host_staging(
                preflight,
                paths,
                backup,
            )
        )
        preactivation = (
            write_usb._require_usb_preactivation_ready(
                preflight,
                paths,
                backup,
                prepared,
            )
        )

        events: list[str] = []
        real_write = (
            write_usb._write_usb_activation_managed_file_exact_state
        )
        real_delete = (
            write_usb._delete_usb_active_managed_hpd
        )

        def mutation_start() -> None:
            events.append(
                "barrier"
            )

        def recording_write(
            current_preflight: write_usb.FavoritesUsbWritePreflight,
            current_paths: write_usb._FavoritesUsbHostOperationPaths,
            filename: str,
            intended_content: bytes,
            *,
            baseline_content: bytes | None,
        ) -> None:
            events.append(
                f"write:{filename}"
            )
            real_write(
                current_preflight,
                current_paths,
                filename,
                intended_content,
                baseline_content=baseline_content,
            )

        def recording_delete(
            current_preflight: write_usb.FavoritesUsbWritePreflight,
            current_paths: write_usb._FavoritesUsbHostOperationPaths,
            filename: str,
            expected_content: bytes,
        ) -> None:
            events.append(
                f"delete:{filename}"
            )
            real_delete(
                current_preflight,
                current_paths,
                filename,
                expected_content,
            )

        monkeypatch.setattr(
            write_usb,
            "_write_usb_activation_managed_file_exact_state",
            recording_write,
        )
        monkeypatch.setattr(
            write_usb,
            "_delete_usb_active_managed_hpd",
            recording_delete,
        )

        activated = (
            write_usb._activate_usb_managed_state(
                preflight,
                paths,
                backup,
                prepared,
                preactivation,
                mutation_start=mutation_start,
            )
        )

        assert activated.snapshot == intended
        assert events == [
            "barrier",
            "write:added.hpd",
            "write:keep.hpd",
            "write:f_list.cfg",
            "delete:removed.hpd",
        ]


def test_usb_activation_mutation_barrier_failure_prevents_first_media_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        _baseline,
        _intended,
    ) = _usb_activation_orchestration_prepared(
        tmp_path
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        prepared = (
            write_usb._create_verified_usb_host_staging(
                preflight,
                paths,
                backup,
            )
        )
        preactivation = (
            write_usb._require_usb_preactivation_ready(
                preflight,
                paths,
                backup,
                prepared,
            )
        )
        before = favorites_tree_evidence(
            favorites_directory
        )
        media_called = False

        def failing_barrier() -> None:
            raise write_usb._FavoritesUsbWritePreparationError(
                paths.rollback_manifest_path,
                "injected durable mutation-start barrier failure",
            )

        def forbidden_write(
            current_preflight: write_usb.FavoritesUsbWritePreflight,
            current_paths: write_usb._FavoritesUsbHostOperationPaths,
            filename: str,
            intended_content: bytes,
            *,
            baseline_content: bytes | None,
        ) -> None:
            nonlocal media_called
            media_called = True
            raise AssertionError(
                "media write must not run after barrier failure"
            )

        monkeypatch.setattr(
            write_usb,
            "_write_usb_activation_managed_file_exact_state",
            forbidden_write,
        )

        with pytest.raises(
            write_usb._FavoritesUsbActivationExecutionError,
            match="injected durable mutation-start barrier failure",
        ) as raised:
            write_usb._activate_usb_managed_state(
                preflight,
                paths,
                backup,
                prepared,
                preactivation,
                mutation_start=failing_barrier,
            )

        assert raised.value.stage is (
            write_usb._FavoritesUsbActivationFailureStage
            .MUTATION_EXECUTION
        )
        assert raised.value.mutation_started is False
        assert raised.value.recovery_artifact is None
        assert not media_called
        assert favorites_tree_evidence(
            favorites_directory
        ) == before


def test_usb_activation_mutation_barrier_covers_catalog_only_first_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        _favorites_directory,
        host_root,
        _baseline,
        _intended,
    ) = _usb_activation_orchestration_prepared(
        tmp_path
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        prepared = (
            write_usb._create_verified_usb_host_staging(
                preflight,
                paths,
                backup,
            )
        )
        preactivation = (
            write_usb._require_usb_preactivation_ready(
                preflight,
                paths,
                backup,
                prepared,
            )
        )
        events: list[str] = []

        monkeypatch.setattr(
            write_usb,
            "_usb_managed_activation_plan",
            lambda _current_preflight: (
                write_usb._FavoritesUsbManagedActivationPlan(
                    document_writes=(),
                    write_catalog=True,
                    document_deletions=(),
                )
            ),
        )

        def mutation_start() -> None:
            events.append(
                "barrier"
            )

        def refusing_catalog(
            current_preflight: write_usb.FavoritesUsbWritePreflight,
            current_paths: write_usb._FavoritesUsbHostOperationPaths,
            filename: str,
            intended_content: bytes,
            *,
            baseline_content: bytes | None,
        ) -> None:
            events.append(
                f"write:{filename}"
            )
            raise write_usb._FavoritesUsbMediaMutationError(
                current_preflight.qualification.favorites_directory
                / filename,
                "injected catalog refusal after durable barrier",
                mutation_started=False,
            )

        monkeypatch.setattr(
            write_usb,
            "_write_usb_activation_managed_file_exact_state",
            refusing_catalog,
        )

        with pytest.raises(
            write_usb._FavoritesUsbActivationExecutionError,
            match="injected catalog refusal after durable barrier",
        ) as raised:
            write_usb._activate_usb_managed_state(
                preflight,
                paths,
                backup,
                prepared,
                preactivation,
                mutation_start=mutation_start,
            )

        assert events == [
            "barrier",
            "write:f_list.cfg",
        ]
        assert raised.value.mutation_started is True
        assert raised.value.stage is (
            write_usb._FavoritesUsbActivationFailureStage
            .MUTATION_EXECUTION
        )


def test_usb_activation_mutation_barrier_covers_deletion_only_first_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        _favorites_directory,
        host_root,
        _baseline,
        _intended,
    ) = _usb_activation_orchestration_prepared(
        tmp_path
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        prepared = (
            write_usb._create_verified_usb_host_staging(
                preflight,
                paths,
                backup,
            )
        )
        preactivation = (
            write_usb._require_usb_preactivation_ready(
                preflight,
                paths,
                backup,
                prepared,
            )
        )
        events: list[str] = []

        monkeypatch.setattr(
            write_usb,
            "_usb_managed_activation_plan",
            lambda _current_preflight: (
                write_usb._FavoritesUsbManagedActivationPlan(
                    document_writes=(),
                    write_catalog=False,
                    document_deletions=(
                        "removed.hpd",
                    ),
                )
            ),
        )

        def mutation_start() -> None:
            events.append(
                "barrier"
            )

        def refusing_delete(
            current_preflight: write_usb.FavoritesUsbWritePreflight,
            current_paths: write_usb._FavoritesUsbHostOperationPaths,
            filename: str,
            expected_content: bytes,
        ) -> None:
            events.append(
                f"delete:{filename}"
            )
            raise write_usb._FavoritesUsbMediaMutationError(
                current_preflight.qualification.favorites_directory
                / filename,
                "injected deletion refusal after durable barrier",
                mutation_started=False,
            )

        monkeypatch.setattr(
            write_usb,
            "_delete_usb_active_managed_hpd",
            refusing_delete,
        )

        with pytest.raises(
            write_usb._FavoritesUsbActivationExecutionError,
            match="injected deletion refusal after durable barrier",
        ) as raised:
            write_usb._activate_usb_managed_state(
                preflight,
                paths,
                backup,
                prepared,
                preactivation,
                mutation_start=mutation_start,
            )

        assert events == [
            "barrier",
            "delete:removed.hpd",
        ]
        assert raised.value.mutation_started is True
        assert raised.value.stage is (
            write_usb._FavoritesUsbActivationFailureStage
            .MUTATION_EXECUTION
        )


def test_usb_activation_mutation_barrier_is_not_called_for_noop(
    tmp_path: Path,
) -> None:
    baseline = _snapshot()
    (
        preflight,
        _mountinfo,
        _favorites_directory,
    ) = _prepared_usb_activation_fixture(
        tmp_path,
        baseline=baseline,
        intended=baseline,
    )
    host_root = (
        tmp_path
        / "host-state-noop-barrier"
        / "favorites-usb-writes"
    )
    calls = 0

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = (
            write_usb._create_verified_usb_host_backup(
                preflight,
                paths,
            )
        )
        prepared = (
            write_usb._create_verified_usb_host_staging(
                preflight,
                paths,
                backup,
            )
        )
        preactivation = (
            write_usb._require_usb_preactivation_ready(
                preflight,
                paths,
                backup,
                prepared,
            )
        )

        def mutation_start() -> None:
            nonlocal calls
            calls += 1

        with pytest.raises(
            write_usb._FavoritesUsbActivationExecutionError,
            match="No-op Favorites USB write plan",
        ) as raised:
            write_usb._activate_usb_managed_state(
                preflight,
                paths,
                backup,
                prepared,
                preactivation,
                mutation_start=mutation_start,
            )

        assert raised.value.mutation_started is False
        assert calls == 0


def test_usb_rollback_write_failure_reconciliation_detects_published_proposed_state(
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
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(_CHANGED_CATALOG),
    )
    preflight = preflight_favorites_usb_write(
        plan,
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state-postpublish-reconcile"
        / "favorites-usb-writes"
    )
    before = favorites_tree_evidence(
        favorites_directory
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        write_usb._create_verified_usb_host_backup(
            preflight,
            paths,
        )

        current = write_usb._usb_rollback_manifest(
            preflight,
            paths,
            revision=1,
            phase=write_usb._FavoritesUsbRollbackPhase.PREPARED,
            bounded_artifact_present=False,
        )
        write_usb._write_usb_rollback_manifest(
            preflight,
            paths,
            current,
        )

        proposed = write_usb._usb_rollback_manifest(
            preflight,
            paths,
            revision=2,
            phase=write_usb._FavoritesUsbRollbackPhase.MUTATION_STARTED,
            bounded_artifact_present=False,
        )

        def failing_directory_sync(path: Path) -> None:
            raise write_usb._FavoritesUsbWritePreparationError(
                path,
                "injected post-publication directory fsync failure",
            )

        monkeypatch.setattr(
            write_usb,
            "_fsync_usb_host_directory",
            failing_directory_sync,
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="injected post-publication directory fsync failure",
        ):
            write_usb._write_usb_rollback_manifest(
                preflight,
                paths,
                proposed,
            )

        state = (
            write_usb._reconcile_usb_rollback_manifest_write_failure(
                preflight,
                paths,
                current,
                proposed,
            )
        )

        assert state is (
            write_usb._FavoritesUsbRollbackWriteFailureState.PROPOSED
        )
        assert (
            write_usb._read_usb_rollback_manifest(
                paths.rollback_manifest_path
            )
            == proposed
        )
        assert favorites_tree_evidence(
            favorites_directory
        ) == before


def test_usb_rollback_write_failure_reconciliation_detects_prior_state(
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
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(_CHANGED_CATALOG),
    )
    preflight = preflight_favorites_usb_write(
        plan,
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state-prepublish-reconcile"
        / "favorites-usb-writes"
    )
    before = favorites_tree_evidence(
        favorites_directory
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        write_usb._create_verified_usb_host_backup(
            preflight,
            paths,
        )

        current = write_usb._usb_rollback_manifest(
            preflight,
            paths,
            revision=1,
            phase=write_usb._FavoritesUsbRollbackPhase.PREPARED,
            bounded_artifact_present=False,
        )
        write_usb._write_usb_rollback_manifest(
            preflight,
            paths,
            current,
        )

        proposed = write_usb._usb_rollback_manifest(
            preflight,
            paths,
            revision=2,
            phase=write_usb._FavoritesUsbRollbackPhase.MUTATION_STARTED,
            bounded_artifact_present=False,
        )

        real_fsync = write_usb.os.fsync
        injected = False

        def failing_first_fsync(descriptor: int) -> None:
            nonlocal injected

            if not injected:
                injected = True
                raise OSError(
                    "injected prepublication temporary-file fsync failure"
                )
            real_fsync(
                descriptor
            )

        monkeypatch.setattr(
            write_usb.os,
            "fsync",
            failing_first_fsync,
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="Could not synchronize Favorites USB rollback temporary file",
        ):
            write_usb._write_usb_rollback_manifest(
                preflight,
                paths,
                proposed,
            )

        state = (
            write_usb._reconcile_usb_rollback_manifest_write_failure(
                preflight,
                paths,
                current,
                proposed,
            )
        )

        assert state is (
            write_usb._FavoritesUsbRollbackWriteFailureState.CURRENT
        )
        assert (
            write_usb._read_usb_rollback_manifest(
                paths.rollback_manifest_path
            )
            == current
        )
        assert favorites_tree_evidence(
            favorites_directory
        ) == before


def test_usb_rollback_write_failure_reconciliation_refuses_missing_prior_state(
    tmp_path: Path,
) -> None:
    (
        mountinfo,
        dev_block,
        mount_directory,
        _favorites_directory,
    ) = _usb_write_fixture(
        tmp_path
    )
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(_CHANGED_CATALOG),
    )
    preflight = preflight_favorites_usb_write(
        plan,
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state-missing-reconcile"
        / "favorites-usb-writes"
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        write_usb._create_verified_usb_host_backup(
            preflight,
            paths,
        )

        current = write_usb._usb_rollback_manifest(
            preflight,
            paths,
            revision=1,
            phase=write_usb._FavoritesUsbRollbackPhase.PREPARED,
            bounded_artifact_present=False,
        )
        write_usb._write_usb_rollback_manifest(
            preflight,
            paths,
            current,
        )

        proposed = write_usb._usb_rollback_manifest(
            preflight,
            paths,
            revision=2,
            phase=write_usb._FavoritesUsbRollbackPhase.MUTATION_STARTED,
            bounded_artifact_present=False,
        )

        paths.rollback_manifest_path.unlink()

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
        ):
            write_usb._reconcile_usb_rollback_manifest_write_failure(
                preflight,
                paths,
                current,
                proposed,
            )


def test_usb_rollback_write_failure_reconciliation_refuses_unrelated_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        mountinfo,
        dev_block,
        mount_directory,
        _favorites_directory,
    ) = _usb_write_fixture(
        tmp_path
    )
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(_CHANGED_CATALOG),
    )
    preflight = preflight_favorites_usb_write(
        plan,
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state-unrelated-reconcile"
        / "favorites-usb-writes"
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        write_usb._create_verified_usb_host_backup(
            preflight,
            paths,
        )

        current = write_usb._usb_rollback_manifest(
            preflight,
            paths,
            revision=1,
            phase=write_usb._FavoritesUsbRollbackPhase.PREPARED,
            bounded_artifact_present=False,
        )
        write_usb._write_usb_rollback_manifest(
            preflight,
            paths,
            current,
        )

        proposed = write_usb._usb_rollback_manifest(
            preflight,
            paths,
            revision=2,
            phase=write_usb._FavoritesUsbRollbackPhase.MUTATION_STARTED,
            bounded_artifact_present=False,
        )
        unrelated = write_usb._usb_rollback_manifest(
            preflight,
            paths,
            revision=99,
            phase=write_usb._FavoritesUsbRollbackPhase.RECOVERY_INCOMPLETE,
            bounded_artifact_present=True,
        )

        monkeypatch.setattr(
            write_usb,
            "_read_usb_rollback_manifest",
            lambda _path: unrelated,
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="matches neither the exact prior nor proposed operation state",
        ):
            write_usb._reconcile_usb_rollback_manifest_write_failure(
                preflight,
                paths,
                current,
                proposed,
            )


def test_usb_rollback_write_failure_reconciliation_handles_initial_publish_absence(
    tmp_path: Path,
) -> None:
    (
        mountinfo,
        dev_block,
        mount_directory,
        _favorites_directory,
    ) = _usb_write_fixture(
        tmp_path
    )
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(_CHANGED_CATALOG),
    )
    preflight = preflight_favorites_usb_write(
        plan,
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state-initial-reconcile"
        / "favorites-usb-writes"
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        write_usb._create_verified_usb_host_backup(
            preflight,
            paths,
        )

        proposed = write_usb._usb_rollback_manifest(
            preflight,
            paths,
            revision=1,
            phase=write_usb._FavoritesUsbRollbackPhase.PREPARED,
            bounded_artifact_present=False,
        )

        state = (
            write_usb._reconcile_usb_rollback_manifest_write_failure(
                preflight,
                paths,
                None,
                proposed,
            )
        )

        assert state is (
            write_usb._FavoritesUsbRollbackWriteFailureState.CURRENT
        )


def _usb_completed_report_for_reconciliation(
    preflight: write_usb.FavoritesUsbWritePreflight,
    paths: write_usb._FavoritesUsbHostOperationPaths,
) -> tuple[
    write_usb._FavoritesUsbRollbackManifest,
    write_usb._FavoritesUsbOperationReport,
]:
    prepared = write_usb._usb_rollback_manifest(
        preflight,
        paths,
        revision=1,
        phase=write_usb._FavoritesUsbRollbackPhase.PREPARED,
        bounded_artifact_present=False,
    )
    write_usb._write_usb_rollback_manifest(
        preflight,
        paths,
        prepared,
    )

    mutation_started = write_usb._usb_rollback_manifest(
        preflight,
        paths,
        revision=2,
        phase=write_usb._FavoritesUsbRollbackPhase.MUTATION_STARTED,
        bounded_artifact_present=False,
    )
    write_usb._write_usb_rollback_manifest(
        preflight,
        paths,
        mutation_started,
    )

    completed = write_usb._usb_rollback_manifest(
        preflight,
        paths,
        revision=3,
        phase=write_usb._FavoritesUsbRollbackPhase.COMPLETED,
        bounded_artifact_present=False,
    )
    write_usb._write_usb_rollback_manifest(
        preflight,
        paths,
        completed,
    )

    report = write_usb._usb_operation_report(
        preflight,
        paths,
        completed,
        backup_verification=(
            write_usb._FavoritesUsbVerificationOutcome.VERIFIED
        ),
        staging_verification=(
            write_usb._FavoritesUsbVerificationOutcome.VERIFIED
        ),
        preactivation_verification=(
            write_usb._FavoritesUsbVerificationOutcome.VERIFIED
        ),
        postactivation_verification=(
            write_usb._FavoritesUsbVerificationOutcome.VERIFIED
        ),
        unmanaged_preservation=(
            write_usb._FavoritesUsbVerificationOutcome.VERIFIED
        ),
        activation_outcome=(
            write_usb._FavoritesUsbActivationOutcome.COMPLETED
        ),
        recovery_outcome=(
            write_usb._FavoritesUsbRecoveryOutcome.NOT_REQUIRED
        ),
        active_snapshot_sha256=(
            completed.intended_snapshot_sha256
        ),
        failure_code=None,
    )

    return completed, report


def test_usb_operation_report_write_failure_reconciliation_detects_published_proposed_report(
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
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(_CHANGED_CATALOG),
    )
    preflight = preflight_favorites_usb_write(
        plan,
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state-report-postpublish"
        / "favorites-usb-writes"
    )
    before = favorites_tree_evidence(
        favorites_directory
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = write_usb._create_verified_usb_host_backup(
            preflight,
            paths,
        )
        write_usb._create_verified_usb_host_staging(
            preflight,
            paths,
            backup,
        )
        rollback, report = _usb_completed_report_for_reconciliation(
            preflight,
            paths,
        )

        def failing_directory_sync(path: Path) -> None:
            raise write_usb._FavoritesUsbWritePreparationError(
                path,
                "injected post-publication report directory fsync failure",
            )

        monkeypatch.setattr(
            write_usb,
            "_fsync_usb_host_directory",
            failing_directory_sync,
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="injected post-publication report directory fsync failure",
        ):
            write_usb._write_usb_operation_report(
                preflight,
                paths,
                rollback,
                report,
            )

        state = (
            write_usb._reconcile_usb_operation_report_write_failure(
                preflight,
                paths,
                rollback,
                report,
            )
        )

        assert state is (
            write_usb._FavoritesUsbOperationReportWriteFailureState.PROPOSED
        )
        assert (
            write_usb._read_usb_operation_report(
                paths.operation_report_path
            )
            == report
        )
        assert not paths.failure_report_path.exists()
        assert favorites_tree_evidence(
            favorites_directory
        ) == before


def test_usb_operation_report_write_failure_reconciliation_detects_absent_report(
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
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(_CHANGED_CATALOG),
    )
    preflight = preflight_favorites_usb_write(
        plan,
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state-report-prepublish"
        / "favorites-usb-writes"
    )
    before = favorites_tree_evidence(
        favorites_directory
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = write_usb._create_verified_usb_host_backup(
            preflight,
            paths,
        )
        write_usb._create_verified_usb_host_staging(
            preflight,
            paths,
            backup,
        )
        rollback, report = _usb_completed_report_for_reconciliation(
            preflight,
            paths,
        )

        real_fsync = write_usb.os.fsync
        injected = False

        def failing_first_fsync(descriptor: int) -> None:
            nonlocal injected

            if not injected:
                injected = True
                raise OSError(
                    "injected prepublication report temporary-file fsync failure"
                )

            real_fsync(
                descriptor
            )

        monkeypatch.setattr(
            write_usb.os,
            "fsync",
            failing_first_fsync,
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="Could not synchronize Favorites USB operation report temporary file",
        ):
            write_usb._write_usb_operation_report(
                preflight,
                paths,
                rollback,
                report,
            )

        state = (
            write_usb._reconcile_usb_operation_report_write_failure(
                preflight,
                paths,
                rollback,
                report,
            )
        )

        assert state is (
            write_usb._FavoritesUsbOperationReportWriteFailureState.ABSENT
        )
        assert not paths.operation_report_path.exists()
        assert not paths.failure_report_path.exists()
        assert favorites_tree_evidence(
            favorites_directory
        ) == before


def test_usb_operation_report_write_failure_reconciliation_refuses_alternate_report(
    tmp_path: Path,
) -> None:
    (
        mountinfo,
        dev_block,
        mount_directory,
        _favorites_directory,
    ) = _usb_write_fixture(
        tmp_path
    )
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(_CHANGED_CATALOG),
    )
    preflight = preflight_favorites_usb_write(
        plan,
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state-report-alternate"
        / "favorites-usb-writes"
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = write_usb._create_verified_usb_host_backup(
            preflight,
            paths,
        )
        write_usb._create_verified_usb_host_staging(
            preflight,
            paths,
            backup,
        )
        rollback, report = _usb_completed_report_for_reconciliation(
            preflight,
            paths,
        )

        paths.failure_report_path.write_bytes(
            b"unexpected alternate report"
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="alternate final report path",
        ):
            write_usb._reconcile_usb_operation_report_write_failure(
                preflight,
                paths,
                rollback,
                report,
            )


def test_usb_operation_report_write_failure_reconciliation_refuses_wrong_proposed_report(
    tmp_path: Path,
) -> None:
    (
        mountinfo,
        dev_block,
        mount_directory,
        _favorites_directory,
    ) = _usb_write_fixture(
        tmp_path
    )
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(_CHANGED_CATALOG),
    )
    preflight = preflight_favorites_usb_write(
        plan,
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state-report-wrong"
        / "favorites-usb-writes"
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = write_usb._create_verified_usb_host_backup(
            preflight,
            paths,
        )
        write_usb._create_verified_usb_host_staging(
            preflight,
            paths,
            backup,
        )
        rollback, report = _usb_completed_report_for_reconciliation(
            preflight,
            paths,
        )

        wrong_rollback = write_usb._usb_rollback_manifest(
            preflight,
            paths,
            revision=1,
            phase=write_usb._FavoritesUsbRollbackPhase.PREPARED,
            bounded_artifact_present=False,
        )
        wrong_report = write_usb._usb_operation_report(
            preflight,
            paths,
            wrong_rollback,
            backup_verification=(
                write_usb._FavoritesUsbVerificationOutcome.VERIFIED
            ),
            staging_verification=(
                write_usb._FavoritesUsbVerificationOutcome.VERIFIED
            ),
            preactivation_verification=(
                write_usb._FavoritesUsbVerificationOutcome.VERIFIED
            ),
            postactivation_verification=(
                write_usb._FavoritesUsbVerificationOutcome.NOT_ATTEMPTED
            ),
            unmanaged_preservation=(
                write_usb._FavoritesUsbVerificationOutcome.NOT_ATTEMPTED
            ),
            activation_outcome=(
                write_usb._FavoritesUsbActivationOutcome.FAILED_BEFORE_MUTATION
            ),
            recovery_outcome=(
                write_usb._FavoritesUsbRecoveryOutcome.NOT_REQUIRED
            ),
            active_snapshot_sha256=None,
            failure_code=(
                write_usb._FavoritesUsbFailureCode.ACTIVATION_FAILED_BEFORE_MUTATION
            ),
        )

        paths.operation_report_path.write_bytes(
            write_usb._usb_operation_report_bytes(
                wrong_report
            )
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="does not match the exact proposed report",
        ):
            write_usb._reconcile_usb_operation_report_write_failure(
                preflight,
                paths,
                rollback,
                report,
            )

def test_usb_operation_report_write_failure_reconciliation_refuses_rollback_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        mountinfo,
        dev_block,
        mount_directory,
        _favorites_directory,
    ) = _usb_write_fixture(
        tmp_path
    )
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(_CHANGED_CATALOG),
    )
    preflight = preflight_favorites_usb_write(
        plan,
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state-report-rollback-change"
        / "favorites-usb-writes"
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = write_usb._create_verified_usb_host_backup(
            preflight,
            paths,
        )
        write_usb._create_verified_usb_host_staging(
            preflight,
            paths,
            backup,
        )
        rollback, report = _usb_completed_report_for_reconciliation(
            preflight,
            paths,
        )

        unrelated = write_usb._usb_rollback_manifest(
            preflight,
            paths,
            revision=99,
            phase=write_usb._FavoritesUsbRollbackPhase.RECOVERY_INCOMPLETE,
            bounded_artifact_present=True,
        )

        monkeypatch.setattr(
            write_usb,
            "_read_usb_rollback_manifest",
            lambda _path: unrelated,
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="rollback manifest changed",
        ):
            write_usb._reconcile_usb_operation_report_write_failure(
                preflight,
                paths,
                rollback,
                report,
            )


def test_usb_operation_report_write_failure_reconciliation_refuses_surviving_temp(
    tmp_path: Path,
) -> None:
    (
        mountinfo,
        dev_block,
        mount_directory,
        _favorites_directory,
    ) = _usb_write_fixture(
        tmp_path
    )
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(_CHANGED_CATALOG),
    )
    preflight = preflight_favorites_usb_write(
        plan,
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state-report-temp"
        / "favorites-usb-writes"
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = write_usb._create_verified_usb_host_backup(
            preflight,
            paths,
        )
        write_usb._create_verified_usb_host_staging(
            preflight,
            paths,
            backup,
        )
        rollback, report = _usb_completed_report_for_reconciliation(
            preflight,
            paths,
        )

        success_temp, _failure_temp = (
            write_usb._usb_operation_report_temporary_paths(
                paths
            )
        )
        success_temp.write_bytes(
            b"surviving report temporary"
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="surviving temporary report path",
        ):
            write_usb._reconcile_usb_operation_report_write_failure(
                preflight,
                paths,
                rollback,
                report,
            )



def test_usb_recovery_bounded_artifact_observer_reports_absent_for_clean_target(
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
    preflight = preflight_favorites_usb_write(
        plan,
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state-bounded-observer-absent"
        / "favorites-usb-writes"
    )
    before = favorites_tree_evidence(
        favorites_directory
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = write_usb._create_verified_usb_host_backup(
            preflight,
            paths,
        )

        assert (
            write_usb._observe_usb_recovery_bounded_artifact_present(
                preflight,
                paths,
                backup,
            )
            is False
        )

    assert favorites_tree_evidence(
        favorites_directory
    ) == before


def test_usb_recovery_bounded_artifact_observer_reports_stable_regular_temp(
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
    preflight = preflight_favorites_usb_write(
        plan,
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state-bounded-observer-present"
        / "favorites-usb-writes"
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = write_usb._create_verified_usb_host_backup(
            preflight,
            paths,
        )
        temporary = write_usb._usb_media_temporary_path(
            preflight,
            paths,
        )
        content = b"operation-bounded temporary content"
        temporary.write_bytes(
            content
        )

        assert (
            write_usb._observe_usb_recovery_bounded_artifact_present(
                preflight,
                paths,
                backup,
            )
            is True
        )
        assert temporary.read_bytes() == content

    assert (
        favorites_directory
        / temporary.name
    ).read_bytes() == content


def test_usb_recovery_bounded_artifact_observer_reports_absent_after_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        paths,
        backup,
        verified,
        target,
        temporary,
        hpd,
        lock,
    ) = _usb_verified_recovery_cleanup_fixture(
        tmp_path,
        monkeypatch,
    )

    try:
        assert (
            write_usb._observe_usb_recovery_bounded_artifact_present(
                preflight,
                paths,
                backup,
            )
            is True
        )

        write_usb._cleanup_verified_usb_recovery_artifact(
            preflight,
            paths,
            backup,
            verified,
        )

        assert (
            write_usb._observe_usb_recovery_bounded_artifact_present(
                preflight,
                paths,
                backup,
            )
            is False
        )
    finally:
        lock.__exit__(
            None,
            None,
            None,
        )

    assert target.read_bytes() == hpd
    assert not write_usb.os.path.lexists(
        temporary
    )


def test_usb_recovery_bounded_artifact_observer_refuses_absent_to_present_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        mountinfo,
        dev_block,
        mount_directory,
        _favorites_directory,
    ) = _usb_write_fixture(
        tmp_path
    )
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(_CHANGED_CATALOG),
    )
    preflight = preflight_favorites_usb_write(
        plan,
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state-bounded-observer-absence-race"
        / "favorites-usb-writes"
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = write_usb._create_verified_usb_host_backup(
            preflight,
            paths,
        )
        temporary = write_usb._usb_media_temporary_path(
            preflight,
            paths,
        )
        real_match = (
            write_usb._require_usb_recovery_target_matches
        )
        changed = False

        def racing_match(
            current_preflight: write_usb.FavoritesUsbWritePreflight,
            current_paths: write_usb._FavoritesUsbHostOperationPaths,
            current_backup: write_usb._FavoritesUsbVerifiedBackup,
            expected: write_usb._FavoritesUsbRecoveryTargetEvidence,
            *,
            stage: str,
        ) -> write_usb._FavoritesUsbRecoveryTargetEvidence:
            nonlocal changed

            observed = real_match(
                current_preflight,
                current_paths,
                current_backup,
                expected,
                stage=stage,
            )
            if not changed:
                changed = True
                temporary.write_bytes(
                    b"appeared during absence observation"
                )
            return observed

        monkeypatch.setattr(
            write_usb,
            "_require_usb_recovery_target_matches",
            racing_match,
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="appeared while observing its absence",
        ):
            write_usb._observe_usb_recovery_bounded_artifact_present(
                preflight,
                paths,
                backup,
            )


def test_usb_recovery_bounded_artifact_observer_refuses_changed_present_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        mountinfo,
        dev_block,
        mount_directory,
        _favorites_directory,
    ) = _usb_write_fixture(
        tmp_path
    )
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(_CHANGED_CATALOG),
    )
    preflight = preflight_favorites_usb_write(
        plan,
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state-bounded-observer-presence-race"
        / "favorites-usb-writes"
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        backup = write_usb._create_verified_usb_host_backup(
            preflight,
            paths,
        )
        temporary = write_usb._usb_media_temporary_path(
            preflight,
            paths,
        )
        temporary.write_bytes(
            b"initial"
        )
        real_match = (
            write_usb._require_usb_recovery_target_matches
        )
        changed = False

        def racing_match(
            current_preflight: write_usb.FavoritesUsbWritePreflight,
            current_paths: write_usb._FavoritesUsbHostOperationPaths,
            current_backup: write_usb._FavoritesUsbVerifiedBackup,
            expected: write_usb._FavoritesUsbRecoveryTargetEvidence,
            *,
            stage: str,
        ) -> write_usb._FavoritesUsbRecoveryTargetEvidence:
            nonlocal changed

            observed = real_match(
                current_preflight,
                current_paths,
                current_backup,
                expected,
                stage=stage,
            )
            if not changed:
                changed = True
                temporary.write_bytes(
                    b"changed while present observation"
                )
            return observed

        monkeypatch.setattr(
            write_usb,
            "_require_usb_recovery_target_matches",
            racing_match,
        )

        with pytest.raises(
            write_usb._FavoritesUsbWritePreparationError,
            match="changed while observing its presence",
        ):
            write_usb._observe_usb_recovery_bounded_artifact_present(
                preflight,
                paths,
                backup,
            )


def test_usb_durable_workflow_completes_exact_intended_state(
    tmp_path: Path,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        _baseline,
        intended,
    ) = _usb_activation_orchestration_prepared(
        tmp_path
    )

    report = write_usb._execute_usb_write_workflow(
        preflight,
        host_root,
    )

    assert report.is_success
    assert report.rollback_manifest.phase is (
        write_usb._FavoritesUsbRollbackPhase.COMPLETED
    )
    assert report.activation_outcome is (
        write_usb._FavoritesUsbActivationOutcome.COMPLETED
    )
    assert report.recovery_outcome is (
        write_usb._FavoritesUsbRecoveryOutcome.NOT_REQUIRED
    )
    assert report.failure_code is None
    assert report.active_snapshot_sha256 == (
        write_usb.favorites_storage_snapshot_sha256(
            intended
        )
    )
    assert (
        write_usb._read_usb_recovery_managed_snapshot(
            favorites_directory
        )
        == intended
    )
    assert report.rollback_manifest.backup_directory.is_dir()
    assert report.rollback_manifest.staging_directory.is_dir()
    assert not report.rollback_manifest.bounded_artifact_present


def test_usb_durable_workflow_refuses_noop_before_host_operation_creation(
    tmp_path: Path,
) -> None:
    baseline = _snapshot()
    (
        preflight,
        _prepared,
        favorites_directory,
    ) = _prepared_usb_activation_fixture(
        tmp_path,
        baseline=baseline,
        intended=baseline,
    )
    host_root = (
        tmp_path
        / "durable-workflow-noop-host"
    )
    before = favorites_tree_evidence(
        favorites_directory
    )

    with pytest.raises(
        write_usb._FavoritesUsbWritePreparationError,
        match="No-op Favorites USB write plan",
    ):
        write_usb._execute_usb_write_workflow(
            preflight,
            host_root,
        )

    assert not host_root.exists()
    assert favorites_tree_evidence(
        favorites_directory
    ) == before


def test_usb_durable_workflow_reports_preactivation_failure_from_prepared(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        baseline,
        _intended,
    ) = _usb_activation_orchestration_prepared(
        tmp_path
    )

    def failing_preactivation(
        current_preflight: write_usb.FavoritesUsbWritePreflight,
        current_paths: write_usb._FavoritesUsbHostOperationPaths,
        current_backup: write_usb._FavoritesUsbVerifiedBackup,
        current_stage: write_usb._FavoritesUsbPreparedStage,
    ) -> write_usb._FavoritesUsbPreactivationEvidence:
        del current_preflight
        del current_backup
        del current_stage
        raise write_usb._FavoritesUsbWritePreparationError(
            current_paths.operation_directory,
            "injected final preactivation failure",
        )

    monkeypatch.setattr(
        write_usb,
        "_require_usb_preactivation_ready",
        failing_preactivation,
    )

    report = write_usb._execute_usb_write_workflow(
        preflight,
        host_root,
    )

    assert report.rollback_manifest.phase is (
        write_usb._FavoritesUsbRollbackPhase.PREPARED
    )
    assert report.activation_outcome is (
        write_usb._FavoritesUsbActivationOutcome.NOT_STARTED
    )
    assert report.failure_code is (
        write_usb._FavoritesUsbFailureCode.PREACTIVATION_FAILED
    )
    assert report.preactivation_verification is (
        write_usb._FavoritesUsbVerificationOutcome.FAILED
    )
    assert report.active_snapshot_sha256 is None
    assert (
        write_usb._read_usb_recovery_managed_snapshot(
            favorites_directory
        )
        == baseline
    )


def test_usb_durable_workflow_mutation_start_current_prevents_media(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        baseline,
        _intended,
    ) = _usb_activation_orchestration_prepared(
        tmp_path
    )
    real_write = write_usb._write_usb_rollback_manifest

    def failing_mutation_start(
        current_preflight: write_usb.FavoritesUsbWritePreflight,
        current_paths: write_usb._FavoritesUsbHostOperationPaths,
        manifest: write_usb._FavoritesUsbRollbackManifest,
    ) -> None:
        if (
            manifest.phase
            is write_usb._FavoritesUsbRollbackPhase.MUTATION_STARTED
        ):
            raise write_usb._FavoritesUsbWritePreparationError(
                current_paths.rollback_manifest_path,
                "injected prepublication mutation-start failure",
            )
        real_write(
            current_preflight,
            current_paths,
            manifest,
        )

    monkeypatch.setattr(
        write_usb,
        "_write_usb_rollback_manifest",
        failing_mutation_start,
    )

    report = write_usb._execute_usb_write_workflow(
        preflight,
        host_root,
    )

    assert report.rollback_manifest.phase is (
        write_usb._FavoritesUsbRollbackPhase.PREPARED
    )
    assert report.failure_code is (
        write_usb._FavoritesUsbFailureCode
        .ACTIVATION_FAILED_BEFORE_MUTATION
    )
    assert (
        write_usb._read_usb_recovery_managed_snapshot(
            favorites_directory
        )
        == baseline
    )


def test_usb_durable_workflow_mutation_start_proposed_recovers_without_media_primitive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        baseline,
        _intended,
    ) = _usb_activation_orchestration_prepared(
        tmp_path
    )
    real_write = write_usb._write_usb_rollback_manifest
    injected = False

    def publish_then_fail_mutation_start(
        current_preflight: write_usb.FavoritesUsbWritePreflight,
        current_paths: write_usb._FavoritesUsbHostOperationPaths,
        manifest: write_usb._FavoritesUsbRollbackManifest,
    ) -> None:
        nonlocal injected

        if (
            manifest.phase
            is write_usb._FavoritesUsbRollbackPhase.MUTATION_STARTED
            and not injected
        ):
            injected = True
            real_write(
                current_preflight,
                current_paths,
                manifest,
            )
            raise write_usb._FavoritesUsbWritePreparationError(
                current_paths.rollback_manifest_path,
                "injected postpublication mutation-start failure",
            )

        real_write(
            current_preflight,
            current_paths,
            manifest,
        )

    monkeypatch.setattr(
        write_usb,
        "_write_usb_rollback_manifest",
        publish_then_fail_mutation_start,
    )

    report = write_usb._execute_usb_write_workflow(
        preflight,
        host_root,
    )

    assert injected
    assert report.rollback_manifest.phase is (
        write_usb._FavoritesUsbRollbackPhase.RECOVERED
    )
    assert report.failure_code is (
        write_usb._FavoritesUsbFailureCode
        .ACTIVATION_FAILED_AFTER_MUTATION
    )
    assert (
        write_usb._read_usb_recovery_managed_snapshot(
            favorites_directory
        )
        == baseline
    )


def test_usb_durable_workflow_recovers_postactivation_verification_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        baseline,
        _intended,
    ) = _usb_activation_orchestration_prepared(
        tmp_path
    )

    def failing_postactivation_read(
        path: Path,
    ) -> FavoritesStorageSnapshot:
        raise write_usb._FavoritesUsbWritePreparationError(
            path,
            "injected durable-workflow postactivation readback failure",
        )

    monkeypatch.setattr(
        write_usb,
        "_read_usb_activation_managed_snapshot",
        failing_postactivation_read,
    )

    report = write_usb._execute_usb_write_workflow(
        preflight,
        host_root,
    )

    assert report.rollback_manifest.phase is (
        write_usb._FavoritesUsbRollbackPhase.RECOVERED
    )
    assert report.failure_code is (
        write_usb._FavoritesUsbFailureCode
        .POSTACTIVATION_VERIFICATION_FAILED
    )
    assert report.postactivation_verification is (
        write_usb._FavoritesUsbVerificationOutcome.FAILED
    )
    assert report.unmanaged_preservation is (
        write_usb._FavoritesUsbVerificationOutcome.VERIFIED
    )
    assert (
        write_usb._read_usb_recovery_managed_snapshot(
            favorites_directory
        )
        == baseline
    )


def test_usb_durable_workflow_reobserves_absent_artifact_after_partial_recovery_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        baseline,
        _intended,
    ) = _usb_activation_orchestration_prepared(
        tmp_path
    )
    baseline_documents = {
        document.filename: document
        for document in baseline.documents
    }

    def activation_with_artifact(
        current_preflight: write_usb.FavoritesUsbWritePreflight,
        current_paths: write_usb._FavoritesUsbHostOperationPaths,
        current_backup: write_usb._FavoritesUsbVerifiedBackup,
        current_stage: write_usb._FavoritesUsbPreparedStage,
        current_preactivation: write_usb._FavoritesUsbPreactivationEvidence,
        *,
        mutation_start: object = None,
    ) -> write_usb._FavoritesUsbActivatedState:
        del current_backup
        del current_stage
        del current_preactivation

        assert callable(
            mutation_start
        )
        mutation_start()

        target = (
            current_preflight.qualification.favorites_directory
            / "removed.hpd"
        )
        temporary = write_usb._usb_media_temporary_path(
            current_preflight,
            current_paths,
        )
        write_usb.os.replace(
            target,
            temporary,
        )
        artifact = write_usb._FavoritesUsbMediaRecoveryArtifact(
            path=temporary,
            managed_filename="removed.hpd",
            content_sha256=(
                write_usb._usb_media_content_sha256(
                    baseline_documents[
                        "removed.hpd"
                    ].content
                )
            ),
        )
        raise write_usb._FavoritesUsbActivationExecutionError(
            temporary,
            "injected activation artifact before recovery",
            stage=(
                write_usb._FavoritesUsbActivationFailureStage
                .MUTATION_EXECUTION
            ),
            mutation_started=True,
            recovery_artifact=artifact,
        )

    def failing_recovery_read(
        path: Path,
    ) -> FavoritesStorageSnapshot:
        raise write_usb._FavoritesUsbWritePreparationError(
            path,
            "injected recovery readback failure after artifact cleanup",
        )

    monkeypatch.setattr(
        write_usb,
        "_activate_usb_managed_state",
        activation_with_artifact,
    )
    monkeypatch.setattr(
        write_usb,
        "_read_usb_recovery_managed_snapshot",
        failing_recovery_read,
    )

    report = write_usb._execute_usb_write_workflow(
        preflight,
        host_root,
    )

    assert report.rollback_manifest.phase is (
        write_usb._FavoritesUsbRollbackPhase.RECOVERY_INCOMPLETE
    )
    assert report.failure_code is (
        write_usb._FavoritesUsbFailureCode.RECOVERY_INCOMPLETE
    )
    assert report.rollback_manifest.bounded_artifact_present is False
    temporary = (
        report.rollback_manifest.favorites_directory
        / (
            write_usb._USB_MEDIA_TEMP_PREFIX
            + report.rollback_manifest.operation_id[:16]
            + ".tmp"
        )
    )
    assert not write_usb.os.path.lexists(
        temporary
    )
    assert (
        favorites_directory
        / "removed.hpd"
    ).read_bytes() == (
        baseline_documents[
            "removed.hpd"
        ].content
    )


def test_usb_durable_workflow_final_report_postpublication_failure_returns_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        _favorites_directory,
        host_root,
        _baseline,
        _intended,
    ) = _usb_activation_orchestration_prepared(
        tmp_path
    )
    real_write = write_usb._write_usb_operation_report
    injected = False

    def publish_then_fail_report(
        current_preflight: write_usb.FavoritesUsbWritePreflight,
        current_paths: write_usb._FavoritesUsbHostOperationPaths,
        rollback: write_usb._FavoritesUsbRollbackManifest,
        report: write_usb._FavoritesUsbOperationReport,
    ) -> Path:
        nonlocal injected

        destination = real_write(
            current_preflight,
            current_paths,
            rollback,
            report,
        )
        if not injected:
            injected = True
            raise write_usb._FavoritesUsbWritePreparationError(
                destination,
                "injected final report postpublication failure",
            )
        return destination

    monkeypatch.setattr(
        write_usb,
        "_write_usb_operation_report",
        publish_then_fail_report,
    )

    report = write_usb._execute_usb_write_workflow(
        preflight,
        host_root,
    )

    assert injected
    assert report.is_success
    assert report.rollback_manifest.phase is (
        write_usb._FavoritesUsbRollbackPhase.COMPLETED
    )


def test_usb_durable_workflow_final_report_absent_failure_is_not_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        _baseline,
        intended,
    ) = _usb_activation_orchestration_prepared(
        tmp_path
    )
    calls = 0

    def failing_report_write(
        current_preflight: write_usb.FavoritesUsbWritePreflight,
        current_paths: write_usb._FavoritesUsbHostOperationPaths,
        rollback: write_usb._FavoritesUsbRollbackManifest,
        report: write_usb._FavoritesUsbOperationReport,
    ) -> Path:
        nonlocal calls
        del current_preflight
        del rollback
        del report
        calls += 1
        raise write_usb._FavoritesUsbWritePreparationError(
            current_paths.operation_report_path,
            "injected final report prepublication failure",
        )

    monkeypatch.setattr(
        write_usb,
        "_write_usb_operation_report",
        failing_report_write,
    )

    with pytest.raises(
        write_usb._FavoritesUsbWritePreparationError,
        match="injected final report prepublication failure",
    ):
        write_usb._execute_usb_write_workflow(
            preflight,
            host_root,
        )

    assert calls == 1
    assert (
        write_usb._read_usb_recovery_managed_snapshot(
            favorites_directory
        )
        == intended
    )


def test_usb_durable_workflow_initial_prepared_writer_failure_never_activates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        baseline,
        _intended,
    ) = _usb_activation_orchestration_prepared(
        tmp_path
    )
    real_write = write_usb._write_usb_rollback_manifest
    activation_called = False

    def publish_prepared_then_fail(
        current_preflight: write_usb.FavoritesUsbWritePreflight,
        current_paths: write_usb._FavoritesUsbHostOperationPaths,
        manifest: write_usb._FavoritesUsbRollbackManifest,
    ) -> None:
        if manifest.phase is write_usb._FavoritesUsbRollbackPhase.PREPARED:
            real_write(
                current_preflight,
                current_paths,
                manifest,
            )
            raise write_usb._FavoritesUsbWritePreparationError(
                current_paths.rollback_manifest_path,
                "injected PREPARED postpublication failure",
            )
        real_write(
            current_preflight,
            current_paths,
            manifest,
        )

    def forbidden_activation(
        *args: object,
        **kwargs: object,
    ) -> write_usb._FavoritesUsbActivatedState:
        nonlocal activation_called
        activation_called = True
        raise AssertionError(
            "activation must not run after PREPARED writer failure"
        )

    monkeypatch.setattr(
        write_usb,
        "_write_usb_rollback_manifest",
        publish_prepared_then_fail,
    )
    monkeypatch.setattr(
        write_usb,
        "_activate_usb_managed_state",
        forbidden_activation,
    )

    with pytest.raises(
        write_usb._FavoritesUsbWritePreparationError,
        match="injected PREPARED postpublication failure",
    ):
        write_usb._execute_usb_write_workflow(
            preflight,
            host_root,
        )

    assert activation_called is False
    assert (
        write_usb._read_usb_recovery_managed_snapshot(
            favorites_directory
        )
        == baseline
    )


def test_usb_durable_workflow_completed_postpublication_failure_reconciles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        favorites_directory,
        host_root,
        _baseline,
        intended,
    ) = _usb_activation_orchestration_prepared(
        tmp_path
    )
    real_write = write_usb._write_usb_rollback_manifest
    injected = False

    def publish_completed_then_fail(
        current_preflight: write_usb.FavoritesUsbWritePreflight,
        current_paths: write_usb._FavoritesUsbHostOperationPaths,
        manifest: write_usb._FavoritesUsbRollbackManifest,
    ) -> None:
        nonlocal injected

        if (
            manifest.phase is write_usb._FavoritesUsbRollbackPhase.COMPLETED
            and not injected
        ):
            injected = True
            real_write(
                current_preflight,
                current_paths,
                manifest,
            )
            raise write_usb._FavoritesUsbWritePreparationError(
                current_paths.rollback_manifest_path,
                "injected COMPLETED postpublication failure",
            )

        real_write(
            current_preflight,
            current_paths,
            manifest,
        )

    monkeypatch.setattr(
        write_usb,
        "_write_usb_rollback_manifest",
        publish_completed_then_fail,
    )

    report = write_usb._execute_usb_write_workflow(
        preflight,
        host_root,
    )

    assert injected
    assert report.is_success
    assert report.rollback_manifest.phase is (
        write_usb._FavoritesUsbRollbackPhase.COMPLETED
    )
    assert (
        write_usb._read_usb_recovery_managed_snapshot(
            favorites_directory
        )
        == intended
    )


def test_usb_rollback_transition_publisher_retries_exact_current_once(
    tmp_path: Path,
) -> None:
    (
        mountinfo,
        dev_block,
        mount_directory,
        _favorites_directory,
    ) = _usb_write_fixture(
        tmp_path
    )
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(_CHANGED_CATALOG),
    )
    preflight = preflight_favorites_usb_write(
        plan,
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state-transition-retry-once"
        / "favorites-usb-writes"
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        write_usb._create_verified_usb_host_backup(
            preflight,
            paths,
        )
        current = write_usb._usb_rollback_manifest(
            preflight,
            paths,
            revision=1,
            phase=write_usb._FavoritesUsbRollbackPhase.PREPARED,
            bounded_artifact_present=False,
        )
        write_usb._write_usb_rollback_manifest(
            preflight,
            paths,
            current,
        )

        real_write = write_usb._write_usb_rollback_manifest
        calls = 0

        def fail_first_current(
            current_preflight: write_usb.FavoritesUsbWritePreflight,
            current_paths: write_usb._FavoritesUsbHostOperationPaths,
            manifest: write_usb._FavoritesUsbRollbackManifest,
        ) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise write_usb._FavoritesUsbWritePreparationError(
                    current_paths.rollback_manifest_path,
                    "injected exact-CURRENT transition failure",
                )
            real_write(
                current_preflight,
                current_paths,
                manifest,
            )

        original = write_usb._write_usb_rollback_manifest
        write_usb._write_usb_rollback_manifest = fail_first_current
        try:
            proposed = write_usb._publish_usb_rollback_transition(
                preflight,
                paths,
                current,
                phase=write_usb._FavoritesUsbRollbackPhase.MUTATION_STARTED,
                bounded_artifact_present=False,
            )
        finally:
            write_usb._write_usb_rollback_manifest = original

        assert calls == 2
        assert proposed.phase is (
            write_usb._FavoritesUsbRollbackPhase.MUTATION_STARTED
        )
        assert (
            write_usb._read_usb_rollback_manifest(
                paths.rollback_manifest_path
            )
            == proposed
        )


def test_usb_rollback_transition_publisher_stops_after_two_exact_current_failures(
    tmp_path: Path,
) -> None:
    (
        mountinfo,
        dev_block,
        mount_directory,
        _favorites_directory,
    ) = _usb_write_fixture(
        tmp_path
    )
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(_CHANGED_CATALOG),
    )
    preflight = preflight_favorites_usb_write(
        plan,
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "host-state-transition-retry-bounded"
        / "favorites-usb-writes"
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        write_usb._create_verified_usb_host_backup(
            preflight,
            paths,
        )
        current = write_usb._usb_rollback_manifest(
            preflight,
            paths,
            revision=1,
            phase=write_usb._FavoritesUsbRollbackPhase.PREPARED,
            bounded_artifact_present=False,
        )
        write_usb._write_usb_rollback_manifest(
            preflight,
            paths,
            current,
        )

        calls = 0

        def always_current(
            _current_preflight: write_usb.FavoritesUsbWritePreflight,
            current_paths: write_usb._FavoritesUsbHostOperationPaths,
            _manifest: write_usb._FavoritesUsbRollbackManifest,
        ) -> None:
            nonlocal calls
            calls += 1
            raise write_usb._FavoritesUsbWritePreparationError(
                current_paths.rollback_manifest_path,
                "injected persistent exact-CURRENT transition failure",
            )

        original = write_usb._write_usb_rollback_manifest
        write_usb._write_usb_rollback_manifest = always_current
        try:
            with pytest.raises(
                write_usb._FavoritesUsbWritePreparationError,
                match="persistent exact-CURRENT",
            ):
                write_usb._publish_usb_rollback_transition(
                    preflight,
                    paths,
                    current,
                    phase=(
                        write_usb._FavoritesUsbRollbackPhase.MUTATION_STARTED
                    ),
                    bounded_artifact_present=False,
                )
        finally:
            write_usb._write_usb_rollback_manifest = original

        assert calls == 2
        assert (
            write_usb._read_usb_rollback_manifest(
                paths.rollback_manifest_path
            )
            == current
        )


def _run_usb_durable_workflow_with_single_transition_current(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    failed_phase: write_usb._FavoritesUsbRollbackPhase,
    force_recovery: bool,
    force_incomplete_recovery: bool = False,
) -> tuple[
    write_usb._FavoritesUsbOperationReport,
    FavoritesStorageSnapshot,
    FavoritesStorageSnapshot,
    Path,
    int,
]:
    (
        preflight,
        favorites_directory,
        host_root,
        baseline,
        intended,
    ) = _usb_activation_orchestration_prepared(
        tmp_path
    )

    real_write = write_usb._write_usb_rollback_manifest
    failed_calls = 0

    def fail_first_target_phase(
        current_preflight: write_usb.FavoritesUsbWritePreflight,
        current_paths: write_usb._FavoritesUsbHostOperationPaths,
        manifest: write_usb._FavoritesUsbRollbackManifest,
    ) -> None:
        nonlocal failed_calls

        if (
            manifest.phase is failed_phase
            and failed_calls == 0
        ):
            failed_calls += 1
            raise write_usb._FavoritesUsbWritePreparationError(
                current_paths.rollback_manifest_path,
                "injected workflow transition exact-CURRENT failure",
            )

        real_write(
            current_preflight,
            current_paths,
            manifest,
        )

    monkeypatch.setattr(
        write_usb,
        "_write_usb_rollback_manifest",
        fail_first_target_phase,
    )

    if force_recovery:
        def failing_postactivation_read(
            path: Path,
        ) -> FavoritesStorageSnapshot:
            raise write_usb._FavoritesUsbWritePreparationError(
                path,
                "injected transition-retry recovery path",
            )

        monkeypatch.setattr(
            write_usb,
            "_read_usb_activation_managed_snapshot",
            failing_postactivation_read,
        )

    if force_incomplete_recovery:
        def failing_recovery(
            _current_preflight: write_usb.FavoritesUsbWritePreflight,
            current_paths: write_usb._FavoritesUsbHostOperationPaths,
            _current_backup: write_usb._FavoritesUsbVerifiedBackup,
            *,
            activation_artifact: (
                write_usb._FavoritesUsbMediaRecoveryArtifact | None
            ) = None,
        ) -> write_usb._FavoritesUsbRecoveredState:
            del activation_artifact
            raise write_usb._FavoritesUsbWritePreparationError(
                current_paths.operation_directory,
                "injected incomplete recovery after transition retry",
            )

        monkeypatch.setattr(
            write_usb,
            "_recover_usb_active_managed_state",
            failing_recovery,
        )

    report = write_usb._execute_usb_write_workflow(
        preflight,
        host_root,
    )

    return (
        report,
        baseline,
        intended,
        favorites_directory,
        failed_calls,
    )


def test_usb_durable_workflow_retries_recovery_required_current(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        report,
        baseline,
        _intended,
        favorites_directory,
        failed_calls,
    ) = _run_usb_durable_workflow_with_single_transition_current(
        tmp_path,
        monkeypatch,
        failed_phase=(
            write_usb._FavoritesUsbRollbackPhase.RECOVERY_REQUIRED
        ),
        force_recovery=True,
    )

    assert failed_calls == 1
    assert report.rollback_manifest.phase is (
        write_usb._FavoritesUsbRollbackPhase.RECOVERED
    )
    assert (
        write_usb._read_usb_recovery_managed_snapshot(
            favorites_directory
        )
        == baseline
    )


def test_usb_durable_workflow_retries_recovery_in_progress_current(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        report,
        baseline,
        _intended,
        favorites_directory,
        failed_calls,
    ) = _run_usb_durable_workflow_with_single_transition_current(
        tmp_path,
        monkeypatch,
        failed_phase=(
            write_usb._FavoritesUsbRollbackPhase.RECOVERY_IN_PROGRESS
        ),
        force_recovery=True,
    )

    assert failed_calls == 1
    assert report.rollback_manifest.phase is (
        write_usb._FavoritesUsbRollbackPhase.RECOVERED
    )
    assert (
        write_usb._read_usb_recovery_managed_snapshot(
            favorites_directory
        )
        == baseline
    )


def test_usb_durable_workflow_retries_recovered_current(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        report,
        baseline,
        _intended,
        favorites_directory,
        failed_calls,
    ) = _run_usb_durable_workflow_with_single_transition_current(
        tmp_path,
        monkeypatch,
        failed_phase=write_usb._FavoritesUsbRollbackPhase.RECOVERED,
        force_recovery=True,
    )

    assert failed_calls == 1
    assert report.rollback_manifest.phase is (
        write_usb._FavoritesUsbRollbackPhase.RECOVERED
    )
    assert (
        write_usb._read_usb_recovery_managed_snapshot(
            favorites_directory
        )
        == baseline
    )


def test_usb_durable_workflow_retries_recovery_incomplete_current(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        report,
        _baseline,
        _intended,
        _favorites_directory,
        failed_calls,
    ) = _run_usb_durable_workflow_with_single_transition_current(
        tmp_path,
        monkeypatch,
        failed_phase=(
            write_usb._FavoritesUsbRollbackPhase.RECOVERY_INCOMPLETE
        ),
        force_recovery=True,
        force_incomplete_recovery=True,
    )

    assert failed_calls == 1
    assert report.rollback_manifest.phase is (
        write_usb._FavoritesUsbRollbackPhase.RECOVERY_INCOMPLETE
    )
    assert report.failure_code is (
        write_usb._FavoritesUsbFailureCode.RECOVERY_INCOMPLETE
    )


def test_usb_durable_workflow_retries_completed_current(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        report,
        _baseline,
        intended,
        favorites_directory,
        failed_calls,
    ) = _run_usb_durable_workflow_with_single_transition_current(
        tmp_path,
        monkeypatch,
        failed_phase=write_usb._FavoritesUsbRollbackPhase.COMPLETED,
        force_recovery=False,
    )

    assert failed_calls == 1
    assert report.is_success
    assert report.rollback_manifest.phase is (
        write_usb._FavoritesUsbRollbackPhase.COMPLETED
    )
    assert (
        write_usb._read_usb_recovery_managed_snapshot(
            favorites_directory
        )
        == intended
    )


def test_usb_public_execution_status_values_are_stable() -> None:
    assert {
        status.value
        for status in write_usb.FavoritesUsbWriteExecutionStatus
    } == {
        "noop",
        "completed",
    }


def test_usb_public_executor_noop_creates_no_host_operation(
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
    host_root = (
        tmp_path
        / "public-noop-host"
        / "favorites-usb-writes"
    )
    baseline = _snapshot()
    plan = plan_favorites_write(
        baseline,
        baseline,
    )

    result = write_usb.execute_favorites_usb_write(
        plan,
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
        host_state_directory=host_root,
    )

    assert result.status is (
        write_usb.FavoritesUsbWriteExecutionStatus.NOOP
    )
    assert result.target_directory == favorites_directory
    assert result.operation_id is None
    assert result.backup_directory is None
    assert result.staging_directory is None
    assert result.rollback_manifest_path is None
    assert result.operation_report_path is None
    assert not host_root.exists()
    assert (
        write_usb._read_usb_recovery_managed_snapshot(
            favorites_directory
        )
        == baseline
    )


def test_usb_public_executor_completes_verified_changed_plan(
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
    host_root = (
        tmp_path
        / "public-completed-host"
        / "favorites-usb-writes"
    )
    baseline = _snapshot()
    intended = _snapshot(
        _CHANGED_CATALOG
    )
    plan = plan_favorites_write(
        baseline,
        intended,
    )

    result = write_usb.execute_favorites_usb_write(
        plan,
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
        host_state_directory=host_root,
    )

    assert result.status is (
        write_usb.FavoritesUsbWriteExecutionStatus.COMPLETED
    )
    assert result.target_directory == favorites_directory
    assert result.operation_id is not None
    assert result.backup_directory is not None
    assert result.backup_directory.is_dir()
    assert result.staging_directory is not None
    assert result.staging_directory.is_dir()
    assert result.rollback_manifest_path is not None
    assert result.rollback_manifest_path.is_file()
    assert result.operation_report_path is not None
    assert result.operation_report_path.is_file()
    assert (
        write_usb._read_usb_recovery_managed_snapshot(
            favorites_directory
        )
        == intended
    )


def test_usb_public_executor_surfaces_durable_failure_report(
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
    host_root = (
        tmp_path
        / "public-failure-host"
        / "favorites-usb-writes"
    )
    baseline = _snapshot()
    plan = plan_favorites_write(
        baseline,
        _snapshot(
            _CHANGED_CATALOG
        ),
    )

    def failing_preactivation(
        current_preflight: write_usb.FavoritesUsbWritePreflight,
        current_paths: write_usb._FavoritesUsbHostOperationPaths,
        current_backup: write_usb._FavoritesUsbVerifiedBackup,
        current_stage: write_usb._FavoritesUsbPreparedStage,
    ) -> write_usb._FavoritesUsbPreactivationEvidence:
        del current_preflight
        del current_backup
        del current_stage
        raise write_usb._FavoritesUsbWritePreparationError(
            current_paths.operation_directory,
            "injected public preactivation failure",
        )

    monkeypatch.setattr(
        write_usb,
        "_require_usb_preactivation_ready",
        failing_preactivation,
    )

    with pytest.raises(
        write_usb.FavoritesUsbWriteExecutionError,
        match="preactivation_failed",
    ) as raised:
        write_usb.execute_favorites_usb_write(
            plan,
            mount_directory,
            mountinfo,
            sys_dev_block_directory=dev_block,
            host_state_directory=host_root,
        )

    assert raised.value.operation_id is not None
    assert raised.value.report_path is not None
    assert raised.value.report_path.name == "failure.json"
    assert raised.value.report_path.is_file()
    assert raised.value.recovery_status == "not_required"
    assert (
        write_usb._read_usb_recovery_managed_snapshot(
            favorites_directory
        )
        == baseline
    )


def test_usb_public_executor_wraps_unreportable_durable_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        mountinfo,
        dev_block,
        mount_directory,
        _favorites_directory,
    ) = _usb_write_fixture(
        tmp_path
    )
    host_root = (
        tmp_path
        / "public-unreportable-host"
        / "favorites-usb-writes"
    )
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(
            _CHANGED_CATALOG
        ),
    )

    def failing_workflow(
        preflight: write_usb.FavoritesUsbWritePreflight,
        current_host_root: Path,
    ) -> write_usb._FavoritesUsbOperationReport:
        del preflight
        raise write_usb._FavoritesUsbWritePreparationError(
            current_host_root,
            "injected unreportable durable failure",
        )

    monkeypatch.setattr(
        write_usb,
        "_execute_usb_write_workflow",
        failing_workflow,
    )

    with pytest.raises(
        write_usb.FavoritesUsbWriteExecutionError,
        match="could not complete",
    ) as raised:
        write_usb.execute_favorites_usb_write(
            plan,
            mount_directory,
            mountinfo,
            sys_dev_block_directory=dev_block,
            host_state_directory=host_root,
        )

    assert raised.value.operation_id is not None
    assert raised.value.report_path is None
    assert raised.value.recovery_status is None


def test_usb_public_executor_propagates_blocked_preflight_before_target_access(
    tmp_path: Path,
) -> None:
    baseline = _snapshot()
    blocked = plan_favorites_write(
        baseline,
        FavoritesStorageSnapshot(
            catalog_bytes=b"",
            documents=(),
        ),
    )
    assert blocked.is_blocked
    missing = (
        tmp_path
        / "missing-scanner-target"
    )
    host_root = (
        tmp_path
        / "public-blocked-host"
        / "favorites-usb-writes"
    )

    with pytest.raises(
        write_usb.FavoritesUsbWritePreflightError,
    ) as raised:
        write_usb.execute_favorites_usb_write(
            blocked,
            missing,
            host_state_directory=host_root,
        )

    assert raised.value.reason is (
        write_usb.FavoritesUsbWritePreflightReason.BLOCKED_PLAN
    )
    assert not missing.exists()
    assert not host_root.exists()


def test_usb_public_executor_requires_absolute_host_state_directory(
    tmp_path: Path,
) -> None:
    (
        _mountinfo,
        _dev_block,
        mount_directory,
        _favorites_directory,
    ) = _usb_write_fixture(
        tmp_path
    )

    with pytest.raises(
        ValueError,
        match="host-state directory must be absolute",
    ):
        write_usb.execute_favorites_usb_write(
            plan_favorites_write(
                _snapshot(),
                _snapshot(),
            ),
            mount_directory,
            host_state_directory=Path(
                "relative-host-state"
            ),
        )


def test_usb_public_execution_symbols_are_package_exports() -> None:
    import sds200

    assert sds200.FavoritesUsbWriteExecutionStatus is (
        write_usb.FavoritesUsbWriteExecutionStatus
    )
    assert sds200.FavoritesUsbWriteExecutionError is (
        write_usb.FavoritesUsbWriteExecutionError
    )
    assert sds200.FavoritesUsbWriteExecutionResult is (
        write_usb.FavoritesUsbWriteExecutionResult
    )
    assert sds200.execute_favorites_usb_write is (
        write_usb.execute_favorites_usb_write
    )


def test_usb_failure_matrix_public_qualification_failure(
    tmp_path: Path,
) -> None:
    (
        mountinfo,
        dev_block,
        mount_directory,
        _favorites_directory,
    ) = _usb_write_fixture(
        tmp_path
    )
    mountinfo.write_text(
        mountinfo.read_text(
            encoding="utf-8"
        ).replace(
            " rw ",
            " ro ",
        ).replace(
            " rw\n",
            " ro\n",
        ),
        encoding="utf-8",
    )
    host_root = (
        tmp_path
        / "failure-matrix-qualification"
        / "favorites-usb-writes"
    )

    with pytest.raises(
        write_usb.FavoritesUsbWritePreflightError,
    ) as raised:
        write_usb.execute_favorites_usb_write(
            plan_favorites_write(
                _snapshot(),
                _snapshot(_CHANGED_CATALOG),
            ),
            mount_directory,
            mountinfo,
            sys_dev_block_directory=dev_block,
            host_state_directory=host_root,
        )

    assert raised.value.reason is (
        write_usb.FavoritesUsbWritePreflightReason.QUALIFICATION_FAILED
    )
    assert raised.value.qualification_reason is (
        FavoritesUsbStorageQualificationReason.READ_ONLY_MOUNT
    )
    assert not host_root.exists()


def test_usb_failure_matrix_public_exclusivity_failure(
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
    preflight = write_usb.preflight_favorites_usb_write(
        plan,
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )
    host_root = (
        tmp_path
        / "failure-matrix-exclusivity"
        / "favorites-usb-writes"
    )
    before = (
        write_usb._read_usb_recovery_managed_snapshot(
            favorites_directory
        )
    )

    with write_usb._usb_host_operation_lock(
        preflight,
        host_root,
    ) as paths:
        with pytest.raises(
            write_usb.FavoritesUsbWriteExecutionError,
            match="could not complete",
        ) as raised:
            write_usb.execute_favorites_usb_write(
                plan,
                mount_directory,
                mountinfo,
                sys_dev_block_directory=dev_block,
                host_state_directory=host_root,
            )

        assert raised.value.operation_id is not None
        assert raised.value.report_path is None
        assert raised.value.recovery_status is None
        assert (
            write_usb._read_usb_recovery_managed_snapshot(
                favorites_directory
            )
            == before
        )
        assert not paths.operation_directory.exists()

    assert not paths.lock_directory.exists()



def test_usb_failure_matrix_public_backup_failure(
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
    host_root = (
        tmp_path
        / "failure-matrix-backup"
        / "favorites-usb-writes"
    )
    before = (
        write_usb._read_usb_recovery_managed_snapshot(
            favorites_directory
        )
    )

    def fail_backup(
        _preflight: write_usb.FavoritesUsbWritePreflight,
        paths: write_usb._FavoritesUsbHostOperationPaths,
    ) -> write_usb._FavoritesUsbVerifiedBackup:
        raise write_usb._FavoritesUsbWritePreparationError(
            paths.backup_directory,
            "injected deterministic backup failure",
        )

    monkeypatch.setattr(
        write_usb,
        "_create_verified_usb_host_backup",
        fail_backup,
    )

    with pytest.raises(
        write_usb.FavoritesUsbWriteExecutionError,
        match="could not complete",
    ) as raised:
        write_usb.execute_favorites_usb_write(
            plan_favorites_write(
                _snapshot(),
                _snapshot(_CHANGED_CATALOG),
            ),
            mount_directory,
            mountinfo,
            sys_dev_block_directory=dev_block,
            host_state_directory=host_root,
        )

    assert raised.value.operation_id is not None
    assert raised.value.report_path is None
    assert raised.value.recovery_status is None
    assert (
        write_usb._read_usb_recovery_managed_snapshot(
            favorites_directory
        )
        == before
    )
    assert tuple(
        host_root.rglob(
            "rollback.json"
        )
    ) == ()


def test_usb_failure_matrix_public_staging_failure(
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
    host_root = (
        tmp_path
        / "failure-matrix-staging"
        / "favorites-usb-writes"
    )
    before = (
        write_usb._read_usb_recovery_managed_snapshot(
            favorites_directory
        )
    )

    def fail_staging(
        _preflight: write_usb.FavoritesUsbWritePreflight,
        paths: write_usb._FavoritesUsbHostOperationPaths,
        _backup: write_usb._FavoritesUsbVerifiedBackup,
    ) -> write_usb._FavoritesUsbPreparedStage:
        raise write_usb._FavoritesUsbWritePreparationError(
            paths.staging_directory,
            "injected deterministic staging failure",
        )

    monkeypatch.setattr(
        write_usb,
        "_create_verified_usb_host_staging",
        fail_staging,
    )

    with pytest.raises(
        write_usb.FavoritesUsbWriteExecutionError,
        match="could not complete",
    ) as raised:
        write_usb.execute_favorites_usb_write(
            plan_favorites_write(
                _snapshot(),
                _snapshot(_CHANGED_CATALOG),
            ),
            mount_directory,
            mountinfo,
            sys_dev_block_directory=dev_block,
            host_state_directory=host_root,
        )

    assert raised.value.operation_id is not None
    assert raised.value.report_path is None
    assert raised.value.recovery_status is None
    assert (
        write_usb._read_usb_recovery_managed_snapshot(
            favorites_directory
        )
        == before
    )
    assert tuple(
        host_root.rglob(
            "rollback.json"
        )
    ) == ()


def test_usb_failure_matrix_workflow_read_only_at_final_preactivation(
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
    host_root = (
        tmp_path
        / "failure-matrix-read-only"
        / "favorites-usb-writes"
    )
    before = (
        write_usb._read_usb_recovery_managed_snapshot(
            favorites_directory
        )
    )
    real_staging = write_usb._create_verified_usb_host_staging

    def stage_then_remount_read_only(
        preflight: write_usb.FavoritesUsbWritePreflight,
        paths: write_usb._FavoritesUsbHostOperationPaths,
        backup: write_usb._FavoritesUsbVerifiedBackup,
    ) -> write_usb._FavoritesUsbPreparedStage:
        prepared = real_staging(
            preflight,
            paths,
            backup,
        )
        mountinfo.write_text(
            mountinfo.read_text(
                encoding="utf-8"
            ).replace(
                " rw ",
                " ro ",
            ).replace(
                " rw\n",
                " ro\n",
            ),
            encoding="utf-8",
        )
        return prepared

    monkeypatch.setattr(
        write_usb,
        "_create_verified_usb_host_staging",
        stage_then_remount_read_only,
    )

    with pytest.raises(
        write_usb.FavoritesUsbWriteExecutionError,
        match="preactivation_failed",
    ) as raised:
        write_usb.execute_favorites_usb_write(
            plan_favorites_write(
                _snapshot(),
                _snapshot(_CHANGED_CATALOG),
            ),
            mount_directory,
            mountinfo,
            sys_dev_block_directory=dev_block,
            host_state_directory=host_root,
        )

    assert raised.value.report_path is not None
    assert raised.value.report_path.name == "failure.json"
    assert raised.value.report_path.is_file()
    assert raised.value.recovery_status == "not_required"
    assert (
        write_usb._read_usb_recovery_managed_snapshot(
            favorites_directory
        )
        == before
    )


def test_usb_failure_matrix_workflow_device_removed_at_final_preactivation(
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
    host_root = (
        tmp_path
        / "failure-matrix-device-removal"
        / "favorites-usb-writes"
    )
    before = (
        write_usb._read_usb_recovery_managed_snapshot(
            favorites_directory
        )
    )
    status = mount_directory.stat()
    device_link = (
        dev_block
        / f"{os.major(status.st_dev)}:{os.minor(status.st_dev)}"
    )
    assert device_link.is_symlink()

    real_staging = write_usb._create_verified_usb_host_staging

    def stage_then_remove_device(
        preflight: write_usb.FavoritesUsbWritePreflight,
        paths: write_usb._FavoritesUsbHostOperationPaths,
        backup: write_usb._FavoritesUsbVerifiedBackup,
    ) -> write_usb._FavoritesUsbPreparedStage:
        prepared = real_staging(
            preflight,
            paths,
            backup,
        )
        device_link.unlink()
        return prepared

    monkeypatch.setattr(
        write_usb,
        "_create_verified_usb_host_staging",
        stage_then_remove_device,
    )

    with pytest.raises(
        write_usb.FavoritesUsbWriteExecutionError,
        match="preactivation_failed",
    ) as raised:
        write_usb.execute_favorites_usb_write(
            plan_favorites_write(
                _snapshot(),
                _snapshot(_CHANGED_CATALOG),
            ),
            mount_directory,
            mountinfo,
            sys_dev_block_directory=dev_block,
            host_state_directory=host_root,
        )

    assert raised.value.report_path is not None
    assert raised.value.report_path.name == "failure.json"
    assert raised.value.report_path.is_file()
    assert raised.value.recovery_status == "not_required"
    assert (
        write_usb._read_usb_recovery_managed_snapshot(
            favorites_directory
        )
        == before
    )



def test_usb_failure_matrix_public_unsupported_filesystem(
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
    mountinfo.write_text(
        mountinfo.read_text(
            encoding="utf-8"
        ).replace(
            " - vfat ",
            " - ext4 ",
        ),
        encoding="utf-8",
    )
    host_root = (
        tmp_path
        / "failure-matrix-unsupported-fs"
        / "favorites-usb-writes"
    )
    before = (
        write_usb._read_usb_recovery_managed_snapshot(
            favorites_directory
        )
    )

    with pytest.raises(
        write_usb.FavoritesUsbWriteExecutionError,
        match="activation_failed_before_mutation",
    ) as raised:
        write_usb.execute_favorites_usb_write(
            plan_favorites_write(
                _snapshot(),
                _snapshot(_CHANGED_CATALOG),
            ),
            mount_directory,
            mountinfo,
            sys_dev_block_directory=dev_block,
            host_state_directory=host_root,
        )

    assert raised.value.report_path is not None
    assert raised.value.report_path.name == "failure.json"
    assert raised.value.report_path.is_file()
    assert raised.value.recovery_status == "not_required"
    assert (
        write_usb._read_usb_recovery_managed_snapshot(
            favorites_directory
        )
        == before
    )
