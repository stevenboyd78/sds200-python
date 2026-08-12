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
