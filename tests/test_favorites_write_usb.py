from __future__ import annotations

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
