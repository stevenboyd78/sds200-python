from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from sds200 import favorites_write_execution as write_execution
from sds200.favorites_storage import (
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
)
from sds200.favorites_write_execution import (
    FavoritesCopiedTreeWriteExecutionError,
    FavoritesCopiedTreeWriteExecutionStatus,
    FavoritesCopiedTreeWritePreflight,
    FavoritesCopiedTreeWritePreflightError,
    FavoritesCopiedTreeWritePreflightReason,
    execute_favorites_copied_tree_write,
    preflight_favorites_copied_tree_write,
)
from sds200.favorites_write_plan import plan_favorites_write

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


def _write_tree(
    root: Path,
    *,
    catalog: bytes = _BASELINE_CATALOG,
) -> None:
    root.mkdir()
    (root / "f_list.cfg").write_bytes(catalog)


def test_public_preflight_reason_values_are_stable() -> None:
    assert tuple(
        reason.value
        for reason in FavoritesCopiedTreeWritePreflightReason
    ) == (
        "blocked_plan",
        "target_unavailable",
        "target_stale",
        "unsafe_tree",
    )


def test_clean_noop_preflight_retains_exact_target_evidence(
    tmp_path: Path,
) -> None:
    target = tmp_path / "favorites_lists"
    _write_tree(target)
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(),
    )

    preflight = preflight_favorites_copied_tree_write(
        plan,
        target,
    )

    assert preflight.plan is plan
    assert preflight.requested_directory == target
    assert preflight.resolved_directory == target.resolve()
    assert preflight.observed_snapshot == plan.baseline_snapshot
    target_stat = target.lstat()
    assert preflight.target_device == target_stat.st_dev
    assert preflight.target_inode == target_stat.st_ino
    assert len(preflight.tree_sha256) == 64
    assert preflight.tree_sha256 == preflight.tree_sha256.lower()
    assert int(preflight.tree_sha256, 16) >= 0
    assert preflight.is_noop
    assert preflight.lock_path == (
        tmp_path
        / ".favorites_lists.sds200-favorites-write.lock"
    )


def test_noop_preflight_creates_no_storage_artifacts(
    tmp_path: Path,
) -> None:
    target = tmp_path / "favorites_lists"
    _write_tree(target)
    before = tuple(sorted(path.name for path in tmp_path.iterdir()))
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(),
    )

    preflight_favorites_copied_tree_write(
        plan,
        target,
    )

    after = tuple(sorted(path.name for path in tmp_path.iterdir()))
    assert before == ("favorites_lists",)
    assert after == before


def test_changed_plan_preflight_is_still_read_only(
    tmp_path: Path,
) -> None:
    target = tmp_path / "favorites_lists"
    _write_tree(target)
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(_CHANGED_CATALOG),
    )
    assert plan.has_changes
    assert not plan.is_blocked

    preflight = preflight_favorites_copied_tree_write(
        plan,
        target,
    )

    assert not preflight.is_noop
    assert tuple(sorted(path.name for path in tmp_path.iterdir())) == (
        "favorites_lists",
    )
    assert not preflight.lock_path.exists()


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
    assert plan.is_blocked
    missing = tmp_path / "does-not-exist"

    with pytest.raises(
        FavoritesCopiedTreeWritePreflightError,
    ) as raised:
        preflight_favorites_copied_tree_write(
            plan,
            missing,
        )

    assert (
        raised.value.reason
        is FavoritesCopiedTreeWritePreflightReason.BLOCKED_PLAN
    )
    assert raised.value.path == missing
    assert "intended_schema_error" in raised.value.message
    assert not missing.exists()


def test_missing_target_is_reported_as_unavailable(
    tmp_path: Path,
) -> None:
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(_CHANGED_CATALOG),
    )
    missing = tmp_path / "does-not-exist"

    with pytest.raises(
        FavoritesCopiedTreeWritePreflightError,
    ) as raised:
        preflight_favorites_copied_tree_write(
            plan,
            missing,
        )

    assert (
        raised.value.reason
        is FavoritesCopiedTreeWritePreflightReason.TARGET_UNAVAILABLE
    )
    assert raised.value.path == missing


def test_fresh_snapshot_mismatch_is_reported_as_stale(
    tmp_path: Path,
) -> None:
    target = tmp_path / "favorites_lists"
    _write_tree(
        target,
        catalog=_CHANGED_CATALOG,
    )
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(_CHANGED_CATALOG),
    )

    with pytest.raises(
        FavoritesCopiedTreeWritePreflightError,
    ) as raised:
        preflight_favorites_copied_tree_write(
            plan,
            target,
        )

    assert (
        raised.value.reason
        is FavoritesCopiedTreeWritePreflightReason.TARGET_STALE
    )
    assert raised.value.path == target.resolve()
    assert "exactly matches" in raised.value.message


def test_tree_digest_covers_unmanaged_regular_file_content(
    tmp_path: Path,
) -> None:
    target = tmp_path / "favorites_lists"
    _write_tree(target)
    unmanaged = target / "offline-notes.bin"
    unmanaged.write_bytes(b"first")
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(),
    )

    first = preflight_favorites_copied_tree_write(
        plan,
        target,
    )
    unmanaged.write_bytes(b"second")
    second = preflight_favorites_copied_tree_write(
        plan,
        target,
    )

    assert first.observed_snapshot == second.observed_snapshot
    assert first.tree_sha256 != second.tree_sha256


def test_unmanaged_symbolic_link_is_refused_for_write_preflight(
    tmp_path: Path,
) -> None:
    target = tmp_path / "favorites_lists"
    _write_tree(target)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside")
    link = target / "unmanaged-link"
    link.symlink_to(outside)
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(),
    )

    with pytest.raises(
        FavoritesCopiedTreeWritePreflightError,
    ) as raised:
        preflight_favorites_copied_tree_write(
            plan,
            target,
        )

    assert (
        raised.value.reason
        is FavoritesCopiedTreeWritePreflightReason.UNSAFE_TREE
    )
    assert raised.value.path == link
    assert "symbolic links" in raised.value.message


def test_nested_unmanaged_symbolic_link_is_refused_for_write_preflight(
    tmp_path: Path,
) -> None:
    target = tmp_path / "favorites_lists"
    _write_tree(target)
    nested = target / "attachments"
    nested.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside")
    link = nested / "link"
    link.symlink_to(outside)
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(),
    )

    with pytest.raises(
        FavoritesCopiedTreeWritePreflightError,
    ) as raised:
        preflight_favorites_copied_tree_write(
            plan,
            target,
        )

    assert (
        raised.value.reason
        is FavoritesCopiedTreeWritePreflightReason.UNSAFE_TREE
    )
    assert raised.value.path == link


def test_operation_lock_is_exclusive_and_removed_after_success(
    tmp_path: Path,
) -> None:
    target = tmp_path / "favorites_lists"
    _write_tree(target)
    preflight = preflight_favorites_copied_tree_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(_CHANGED_CATALOG),
        ),
        target,
    )

    with write_execution._copied_tree_operation_lock(
        preflight
    ):
        assert preflight.lock_path.is_dir()

        with pytest.raises(
            write_execution._CopiedTreeWritePreparationError,
            match="already be active",
        ), write_execution._copied_tree_operation_lock(
            preflight
        ):
            pytest.fail(
                "second operation lock unexpectedly succeeded"
            )

    assert not preflight.lock_path.exists()


def test_existing_operation_lock_fails_closed(
    tmp_path: Path,
) -> None:
    target = tmp_path / "favorites_lists"
    _write_tree(target)
    preflight = preflight_favorites_copied_tree_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(_CHANGED_CATALOG),
        ),
        target,
    )
    preflight.lock_path.mkdir()

    with pytest.raises(
        write_execution._CopiedTreeWritePreparationError,
        match="already be active",
    ), write_execution._copied_tree_operation_lock(
        preflight
    ):
        pytest.fail(
            "operation lock unexpectedly succeeded"
        )

    assert preflight.lock_path.is_dir()


def test_target_revalidation_detects_unmanaged_change(
    tmp_path: Path,
) -> None:
    target = tmp_path / "favorites_lists"
    _write_tree(target)
    unmanaged = target / "offline-notes.bin"
    unmanaged.write_bytes(b"before")
    preflight = preflight_favorites_copied_tree_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(_CHANGED_CATALOG),
        ),
        target,
    )

    unmanaged.write_bytes(b"after")

    with pytest.raises(
        write_execution._CopiedTreeWritePreparationError,
        match="content or structure changed",
    ):
        write_execution._require_current_preflight_target(
            preflight
        )


def test_target_revalidation_detects_root_replacement(
    tmp_path: Path,
) -> None:
    target = tmp_path / "favorites_lists"
    _write_tree(target)
    preflight = preflight_favorites_copied_tree_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(_CHANGED_CATALOG),
        ),
        target,
    )

    old_target = tmp_path / "old-favorites"
    target.rename(old_target)
    _write_tree(target)

    with pytest.raises(
        write_execution._CopiedTreeWritePreparationError,
        match="identity changed",
    ):
        write_execution._require_current_preflight_target(
            preflight
        )


def test_verified_backup_preserves_complete_tree(
    tmp_path: Path,
) -> None:
    target = tmp_path / "favorites_lists"
    _write_tree(target)
    nested = target / "attachments"
    nested.mkdir()
    nested.chmod(0o750)
    unmanaged = nested / "offline-notes.bin"
    unmanaged.write_bytes(b"preserve exactly")
    unmanaged.chmod(0o640)

    preflight = preflight_favorites_copied_tree_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(_CHANGED_CATALOG),
        ),
        target,
    )
    backup = tmp_path / ".favorites_lists.backup-test"

    with write_execution._copied_tree_operation_lock(
        preflight
    ):
        evidence = write_execution._create_verified_backup(
            preflight,
            backup,
        )

    assert evidence.sha256 == preflight.tree_sha256
    assert (
        write_execution._copied_tree_evidence(
            backup
        ).sha256
        == preflight.tree_sha256
    )
    assert (backup / "f_list.cfg").read_bytes() == _BASELINE_CATALOG
    assert (
        backup
        / "attachments"
        / "offline-notes.bin"
    ).read_bytes() == b"preserve exactly"
    assert (
        (backup / "attachments").stat().st_mode
        & 0o777
    ) == 0o750
    assert (
        (
            backup
            / "attachments"
            / "offline-notes.bin"
        ).stat().st_mode
        & 0o777
    ) == 0o640
    assert (
        write_execution.FavoritesCopiedTreeStorageSource(
            backup
        ).read_snapshot()
        == preflight.observed_snapshot
    )
    assert (
        write_execution._copied_tree_evidence(
            target
        ).sha256
        == preflight.tree_sha256
    )


def test_verified_backup_refuses_existing_destination(
    tmp_path: Path,
) -> None:
    target = tmp_path / "favorites_lists"
    _write_tree(target)
    preflight = preflight_favorites_copied_tree_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(_CHANGED_CATALOG),
        ),
        target,
    )
    backup = tmp_path / ".favorites_lists.backup-test"
    backup.mkdir()

    with write_execution._copied_tree_operation_lock(
        preflight
    ), pytest.raises(
        write_execution._CopiedTreeWritePreparationError,
        match="already exists",
    ):
        write_execution._create_verified_backup(
            preflight,
            backup,
        )


def test_verified_backup_detects_source_change_during_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "favorites_lists"
    _write_tree(target)
    unmanaged = target / "offline-notes.bin"
    unmanaged.write_bytes(b"before")
    preflight = preflight_favorites_copied_tree_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(_CHANGED_CATALOG),
        ),
        target,
    )
    backup = tmp_path / ".favorites_lists.backup-test"
    real_copytree = write_execution.shutil.copytree

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
        unmanaged.write_bytes(b"after")
        return result

    monkeypatch.setattr(
        write_execution.shutil,
        "copytree",
        changing_copytree,
    )

    with write_execution._copied_tree_operation_lock(
        preflight
    ), pytest.raises(
        write_execution._CopiedTreeWritePreparationError,
        match="content or structure changed",
    ):
        write_execution._create_verified_backup(
            preflight,
            backup,
        )

    assert backup.exists()


def test_verified_staging_changes_only_managed_material(
    tmp_path: Path,
) -> None:
    target = tmp_path / "favorites_lists"
    _write_tree(target)
    nested = target / "attachments"
    nested.mkdir()
    unmanaged = nested / "offline-notes.bin"
    unmanaged.write_bytes(b"preserve exactly")

    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(_CHANGED_CATALOG),
    )
    preflight = preflight_favorites_copied_tree_write(
        plan,
        target,
    )
    staging = tmp_path / ".favorites_lists.staging-test"

    with write_execution._copied_tree_operation_lock(
        preflight
    ):
        prepared = (
            write_execution._create_verified_staging(
                preflight,
                staging,
            )
        )

    assert prepared.directory == staging
    assert prepared.snapshot == plan.intended_snapshot
    assert (staging / "f_list.cfg").read_bytes() == _CHANGED_CATALOG
    assert (
        staging
        / "attachments"
        / "offline-notes.bin"
    ).read_bytes() == b"preserve exactly"
    assert (target / "f_list.cfg").read_bytes() == _BASELINE_CATALOG
    assert (
        target
        / "attachments"
        / "offline-notes.bin"
    ).read_bytes() == b"preserve exactly"


def test_verified_staging_reconciles_hpd_add_remove(
    tmp_path: Path,
) -> None:
    hpd = (
        b"TargetModel\tBCDx36HP\r\n"
        b"FormatVersion\t1.00\r\n"
    )
    target = tmp_path / "favorites_lists"
    _write_tree(target)
    (target / "old.hpd").write_bytes(hpd)

    baseline = FavoritesStorageSnapshot(
        catalog_bytes=_BASELINE_CATALOG,
        documents=(
            FavoritesStorageDocument(
                filename="old.hpd",
                content=hpd,
            ),
        ),
    )
    intended = FavoritesStorageSnapshot(
        catalog_bytes=_BASELINE_CATALOG,
        documents=(
            FavoritesStorageDocument(
                filename="new.hpd",
                content=hpd,
            ),
        ),
    )
    plan = plan_favorites_write(
        baseline,
        intended,
    )
    assert not plan.is_blocked
    preflight = preflight_favorites_copied_tree_write(
        plan,
        target,
    )
    staging = tmp_path / ".favorites_lists.staging-test"

    with write_execution._copied_tree_operation_lock(
        preflight
    ):
        prepared = (
            write_execution._create_verified_staging(
                preflight,
                staging,
            )
        )

    assert prepared.snapshot == intended
    assert not (staging / "old.hpd").exists()
    assert (staging / "new.hpd").read_bytes() == hpd
    assert (target / "old.hpd").read_bytes() == hpd
    assert not (target / "new.hpd").exists()


def test_verified_staging_refuses_existing_destination(
    tmp_path: Path,
) -> None:
    target = tmp_path / "favorites_lists"
    _write_tree(target)
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(_CHANGED_CATALOG),
    )
    preflight = preflight_favorites_copied_tree_write(
        plan,
        target,
    )
    staging = tmp_path / ".favorites_lists.staging-test"
    staging.mkdir()

    with (
        write_execution._copied_tree_operation_lock(
            preflight
        ),
        pytest.raises(
            write_execution._CopiedTreeWritePreparationError,
            match="already exists",
        ),
    ):
        write_execution._create_verified_staging(
            preflight,
            staging,
        )


def test_verified_staging_refuses_non_hpd_intended_document(
    tmp_path: Path,
) -> None:
    hpd = (
        b"TargetModel\tBCDx36HP\r\n"
        b"FormatVersion\t1.00\r\n"
    )
    target = tmp_path / "favorites_lists"
    _write_tree(target)
    plan = plan_favorites_write(
        _snapshot(),
        FavoritesStorageSnapshot(
            catalog_bytes=_BASELINE_CATALOG,
            documents=(
                FavoritesStorageDocument(
                    filename="notes.bin",
                    content=hpd,
                ),
            ),
        ),
    )
    assert not plan.is_blocked
    preflight = preflight_favorites_copied_tree_write(
        plan,
        target,
    )
    staging = tmp_path / ".favorites_lists.staging-test"

    with (
        write_execution._copied_tree_operation_lock(
            preflight
        ),
        pytest.raises(
            write_execution._CopiedTreeWritePreparationError,
            match="lowercase-.hpd",
        ),
    ):
        write_execution._create_verified_staging(
            preflight,
            staging,
        )

    assert not staging.exists()


def test_verified_staging_detects_source_change_during_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "favorites_lists"
    _write_tree(target)
    unmanaged = target / "offline-notes.bin"
    unmanaged.write_bytes(b"before")
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(_CHANGED_CATALOG),
    )
    preflight = preflight_favorites_copied_tree_write(
        plan,
        target,
    )
    staging = tmp_path / ".favorites_lists.staging-test"
    real_copytree = write_execution.shutil.copytree

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
        unmanaged.write_bytes(b"after")
        return result

    monkeypatch.setattr(
        write_execution.shutil,
        "copytree",
        changing_copytree,
    )

    with (
        write_execution._copied_tree_operation_lock(
            preflight
        ),
        pytest.raises(
            write_execution._CopiedTreeWritePreparationError,
            match="content or structure changed",
        ),
    ):
        write_execution._create_verified_staging(
            preflight,
            staging,
        )

    assert staging.exists()
    assert unmanaged.read_bytes() == b"after"


def test_verified_staging_readback_rejects_corruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "favorites_lists"
    _write_tree(target)
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(_CHANGED_CATALOG),
    )
    preflight = preflight_favorites_copied_tree_write(
        plan,
        target,
    )
    staging = tmp_path / ".favorites_lists.staging-test"
    real_write = (
        write_execution._write_staged_regular_file
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
                content + b"corrupt"
            )

    monkeypatch.setattr(
        write_execution,
        "_write_staged_regular_file",
        corrupting_write,
    )

    with (
        write_execution._copied_tree_operation_lock(
            preflight
        ),
        pytest.raises(
            write_execution._CopiedTreeWritePreparationError,
            match="exact intended snapshot",
        ),
    ):
        write_execution._create_verified_staging(
            preflight,
            staging,
        )

    assert (target / "f_list.cfg").read_bytes() == _BASELINE_CATALOG


def _prepare_replacement_fixture(
    tmp_path: Path,
) -> tuple[
    FavoritesCopiedTreeWritePreflight,
    Path,
    write_execution._CopiedTreePreparedStage,
    Path,
]:
    target = tmp_path / "favorites_lists"
    _write_tree(target)
    (target / "unmanaged.bin").write_bytes(
        b"preserve"
    )
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(_CHANGED_CATALOG),
    )
    preflight = preflight_favorites_copied_tree_write(
        plan,
        target,
    )
    backup = tmp_path / ".favorites_lists.backup-test"
    staging = tmp_path / ".favorites_lists.staging-test"
    displaced = tmp_path / ".favorites_lists.displaced-test"

    with write_execution._copied_tree_operation_lock(
        preflight
    ):
        write_execution._create_verified_backup(
            preflight,
            backup,
        )
        prepared = (
            write_execution._create_verified_staging(
                preflight,
                staging,
            )
        )

    return (
        preflight,
        backup,
        prepared,
        displaced,
    )


def test_replacement_requires_second_exact_target_check(
    tmp_path: Path,
) -> None:
    (
        preflight,
        backup,
        prepared,
        displaced,
    ) = _prepare_replacement_fixture(
        tmp_path
    )
    target = preflight.resolved_directory
    (target / "unmanaged.bin").write_bytes(
        b"changed after staging"
    )

    with (
        write_execution._copied_tree_operation_lock(
            preflight
        ),
        pytest.raises(
            write_execution._CopiedTreeWritePreparationError,
            match="content or structure changed",
        ),
    ):
        write_execution._replace_active_with_verified_staging(
            preflight,
            backup,
            prepared,
            displaced,
        )

    assert (target / "f_list.cfg").read_bytes() == _BASELINE_CATALOG
    assert prepared.directory.exists()
    assert not displaced.exists()


def test_replacement_activates_verified_stage_and_retains_displaced_tree(
    tmp_path: Path,
) -> None:
    (
        preflight,
        backup,
        prepared,
        displaced,
    ) = _prepare_replacement_fixture(
        tmp_path
    )
    target = preflight.resolved_directory

    with write_execution._copied_tree_operation_lock(
        preflight
    ):
        result = (
            write_execution._replace_active_with_verified_staging(
                preflight,
                backup,
                prepared,
                displaced,
            )
        )

    assert result.active_snapshot == preflight.plan.intended_snapshot
    assert result.active_tree_sha256 == prepared.tree_sha256
    assert result.displaced_directory == displaced
    assert (target / "f_list.cfg").read_bytes() == _CHANGED_CATALOG
    assert (target / "unmanaged.bin").read_bytes() == b"preserve"
    assert not prepared.directory.exists()
    assert (displaced / "f_list.cfg").read_bytes() == _BASELINE_CATALOG
    assert (displaced / "unmanaged.bin").read_bytes() == b"preserve"
    assert (
        write_execution.FavoritesCopiedTreeStorageSource(
            backup
        ).read_snapshot()
        == preflight.observed_snapshot
    )


def test_replacement_refuses_changed_verified_backup(
    tmp_path: Path,
) -> None:
    (
        preflight,
        backup,
        prepared,
        displaced,
    ) = _prepare_replacement_fixture(
        tmp_path
    )
    (backup / "unmanaged.bin").write_bytes(
        b"changed"
    )

    with (
        write_execution._copied_tree_operation_lock(
            preflight
        ),
        pytest.raises(
            write_execution._CopiedTreeWritePreparationError,
            match="backup changed",
        ),
    ):
        write_execution._replace_active_with_verified_staging(
            preflight,
            backup,
            prepared,
            displaced,
        )

    assert (
        preflight.resolved_directory
        / "f_list.cfg"
    ).read_bytes() == _BASELINE_CATALOG
    assert prepared.directory.exists()
    assert not displaced.exists()


def test_replacement_refuses_changed_verified_stage(
    tmp_path: Path,
) -> None:
    (
        preflight,
        backup,
        prepared,
        displaced,
    ) = _prepare_replacement_fixture(
        tmp_path
    )
    (
        prepared.directory
        / "unmanaged.bin"
    ).write_bytes(
        b"changed"
    )

    with (
        write_execution._copied_tree_operation_lock(
            preflight
        ),
        pytest.raises(
            write_execution._CopiedTreeWritePreparationError,
            match="staging content or structure changed",
        ),
    ):
        write_execution._replace_active_with_verified_staging(
            preflight,
            backup,
            prepared,
            displaced,
        )

    assert (
        preflight.resolved_directory
        / "f_list.cfg"
    ).read_bytes() == _BASELINE_CATALOG
    assert not displaced.exists()


def test_replacement_rename_failure_restores_exact_active_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        backup,
        prepared,
        displaced,
    ) = _prepare_replacement_fixture(
        tmp_path
    )
    target = preflight.resolved_directory
    real_rename = write_execution.os.rename
    calls = 0

    def failing_rename(
        src: Path,
        dst: Path,
    ) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError(
                "injected stage activation failure"
            )
        real_rename(
            src,
            dst,
        )

    monkeypatch.setattr(
        write_execution.os,
        "rename",
        failing_rename,
    )

    with (
        write_execution._copied_tree_operation_lock(
            preflight
        ),
        pytest.raises(
            write_execution._CopiedTreeReplacementError,
        ) as raised,
    ):
        write_execution._replace_active_with_verified_staging(
            preflight,
            backup,
            prepared,
            displaced,
        )

    assert (
        raised.value.recovery_status
        is write_execution._CopiedTreeRecoveryStatus.RESTORED
    )
    assert (target / "f_list.cfg").read_bytes() == _BASELINE_CATALOG
    assert (target / "unmanaged.bin").read_bytes() == b"preserve"
    assert prepared.directory.exists()
    assert not displaced.exists()
    assert (
        write_execution._copied_tree_evidence(
            target
        ).sha256
        == preflight.tree_sha256
    )


def test_replacement_readback_failure_restores_exact_active_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        backup,
        prepared,
        displaced,
    ) = _prepare_replacement_fixture(
        tmp_path
    )
    target = preflight.resolved_directory
    real_verify = (
        write_execution._verify_replacement_active
    )

    def failing_verify(
        preflight_arg: FavoritesCopiedTreeWritePreflight,
        prepared_arg: write_execution._CopiedTreePreparedStage,
    ) -> write_execution._CopiedTreeEvidence:
        real_verify(
            preflight_arg,
            prepared_arg,
        )
        raise write_execution._CopiedTreeWritePreparationError(
            target,
            "injected active readback failure",
        )

    monkeypatch.setattr(
        write_execution,
        "_verify_replacement_active",
        failing_verify,
    )

    with (
        write_execution._copied_tree_operation_lock(
            preflight
        ),
        pytest.raises(
            write_execution._CopiedTreeReplacementError,
        ) as raised,
    ):
        write_execution._replace_active_with_verified_staging(
            preflight,
            backup,
            prepared,
            displaced,
        )

    assert (
        raised.value.recovery_status
        is write_execution._CopiedTreeRecoveryStatus.RESTORED
    )
    assert (target / "f_list.cfg").read_bytes() == _BASELINE_CATALOG
    assert prepared.directory.exists()
    assert not displaced.exists()


def test_replacement_surfaces_incomplete_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        preflight,
        backup,
        prepared,
        displaced,
    ) = _prepare_replacement_fixture(
        tmp_path
    )
    real_rename = write_execution.os.rename
    calls = 0

    def failing_rename(
        src: Path,
        dst: Path,
    ) -> None:
        nonlocal calls
        calls += 1
        if calls in {2, 3}:
            raise OSError(
                "injected activation and recovery failure"
            )
        real_rename(
            src,
            dst,
        )

    monkeypatch.setattr(
        write_execution.os,
        "rename",
        failing_rename,
    )

    with (
        write_execution._copied_tree_operation_lock(
            preflight
        ),
        pytest.raises(
            write_execution._CopiedTreeReplacementError,
        ) as raised,
    ):
        write_execution._replace_active_with_verified_staging(
            preflight,
            backup,
            prepared,
            displaced,
        )

    assert (
        raised.value.recovery_status
        is write_execution._CopiedTreeRecoveryStatus.INCOMPLETE
    )
    assert raised.value.recovery_message is not None
    assert backup.exists()
    assert displaced.exists()


def test_snapshot_identity_is_stable_and_exact(
    tmp_path: Path,
) -> None:
    del tmp_path
    baseline = _snapshot()
    changed = _snapshot(
        _CHANGED_CATALOG
    )

    baseline_digest = (
        write_execution._favorites_storage_snapshot_sha256(
            baseline
        )
    )

    assert len(baseline_digest) == 64
    assert baseline_digest == (
        write_execution._favorites_storage_snapshot_sha256(
            baseline
        )
    )
    assert baseline_digest != (
        write_execution._favorites_storage_snapshot_sha256(
            changed
        )
    )


def test_operation_id_binds_target_plan_and_tree(
    tmp_path: Path,
) -> None:
    target = tmp_path / "favorites_lists"
    _write_tree(target)
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(_CHANGED_CATALOG),
    )
    first = preflight_favorites_copied_tree_write(
        plan,
        target,
    )
    first_id = (
        write_execution._copied_tree_operation_id(
            first
        )
    )

    assert len(first_id) == 64
    assert first_id == (
        write_execution._copied_tree_operation_id(
            first
        )
    )

    (target / "unmanaged.bin").write_bytes(
        b"new unmanaged material"
    )
    second = preflight_favorites_copied_tree_write(
        plan,
        target,
    )

    assert first_id != (
        write_execution._copied_tree_operation_id(
            second
        )
    )


def test_rollback_manifest_contains_only_identity_and_paths(
    tmp_path: Path,
) -> None:
    target = tmp_path / "favorites_lists"
    _write_tree(target)
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(_CHANGED_CATALOG),
    )
    preflight = preflight_favorites_copied_tree_write(
        plan,
        target,
    )
    operation_id = (
        write_execution._copied_tree_operation_id(
            preflight
        )
    )
    manifest = write_execution._CopiedTreeRollbackManifest(
        operation_id=operation_id,
        target_directory=target.resolve(),
        backup_directory=(
            tmp_path
            / ".favorites_lists.backup-test"
        ),
        displaced_directory=(
            tmp_path
            / ".favorites_lists.displaced-test"
        ),
        baseline_snapshot_sha256=(
            write_execution._favorites_storage_snapshot_sha256(
                plan.baseline_snapshot
            )
        ),
        baseline_tree_sha256=preflight.tree_sha256,
    )

    payload = manifest.as_dict()
    rendered = json.dumps(
        payload,
        sort_keys=True,
    )

    assert payload["operation_id"] == operation_id
    assert payload["target_directory"] == str(
        target.resolve()
    )
    assert "restore_instruction" in payload
    assert "TargetModel" not in rendered
    assert "BCDx36HP" not in rendered
    assert "FormatVersion" not in rendered


def test_operation_report_serialization_omits_programming_contents(
    tmp_path: Path,
) -> None:
    target = tmp_path / "favorites_lists"
    _write_tree(target)
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(_CHANGED_CATALOG),
    )
    preflight = preflight_favorites_copied_tree_write(
        plan,
        target,
    )
    operation_id = (
        write_execution._copied_tree_operation_id(
            preflight
        )
    )
    report = write_execution._CopiedTreeOperationReport(
        operation_id=operation_id,
        target_directory=target.resolve(),
        backup_directory=(
            tmp_path
            / ".favorites_lists.backup-test"
        ),
        staging_directory=(
            tmp_path
            / ".favorites_lists.staging-test"
        ),
        displaced_directory=(
            tmp_path
            / ".favorites_lists.displaced-test"
        ),
        baseline_snapshot_sha256=(
            write_execution._favorites_storage_snapshot_sha256(
                plan.baseline_snapshot
            )
        ),
        intended_snapshot_sha256=(
            write_execution._favorites_storage_snapshot_sha256(
                plan.intended_snapshot
            )
        ),
        baseline_tree_sha256=preflight.tree_sha256,
        backup_verified=True,
        staging_verified=True,
        second_baseline_verified=True,
        replacement_outcome=(
            write_execution._CopiedTreeReplacementOutcome.COMPLETED
        ),
        recovery_outcome=(
            write_execution._CopiedTreeRecoveryStatus.NOT_NEEDED
        ),
        active_snapshot_sha256=(
            write_execution._favorites_storage_snapshot_sha256(
                plan.intended_snapshot
            )
        ),
    )

    payload = report.as_dict()
    rendered = json.dumps(
        payload,
        sort_keys=True,
    )

    assert payload["replacement_outcome"] == "completed"
    assert payload["recovery_outcome"] == "not_needed"
    assert payload["backup_verified"] is True
    assert payload["staging_verified"] is True
    assert payload["second_baseline_verified"] is True
    assert "TargetModel" not in rendered
    assert "BCDx36HP" not in rendered
    assert "FormatVersion" not in rendered


def test_durable_operation_records_are_no_clobber_json(
    tmp_path: Path,
) -> None:
    target = tmp_path / "favorites_lists"
    _write_tree(target)
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(_CHANGED_CATALOG),
    )
    preflight = preflight_favorites_copied_tree_write(
        plan,
        target,
    )
    operation_id = (
        write_execution._copied_tree_operation_id(
            preflight
        )
    )
    manifest_path = (
        tmp_path
        / ".favorites_lists.rollback-test.json"
    )
    report_path = (
        tmp_path
        / ".favorites_lists.report-test.json"
    )
    manifest = write_execution._CopiedTreeRollbackManifest(
        operation_id=operation_id,
        target_directory=target.resolve(),
        backup_directory=(
            tmp_path
            / ".favorites_lists.backup-test"
        ),
        displaced_directory=(
            tmp_path
            / ".favorites_lists.displaced-test"
        ),
        baseline_snapshot_sha256=(
            write_execution._favorites_storage_snapshot_sha256(
                plan.baseline_snapshot
            )
        ),
        baseline_tree_sha256=preflight.tree_sha256,
    )
    report = write_execution._CopiedTreeOperationReport(
        operation_id=operation_id,
        target_directory=target.resolve(),
        backup_directory=(
            tmp_path
            / ".favorites_lists.backup-test"
        ),
        staging_directory=(
            tmp_path
            / ".favorites_lists.staging-test"
        ),
        displaced_directory=(
            tmp_path
            / ".favorites_lists.displaced-test"
        ),
        baseline_snapshot_sha256=(
            write_execution._favorites_storage_snapshot_sha256(
                plan.baseline_snapshot
            )
        ),
        intended_snapshot_sha256=(
            write_execution._favorites_storage_snapshot_sha256(
                plan.intended_snapshot
            )
        ),
        baseline_tree_sha256=preflight.tree_sha256,
        backup_verified=True,
        staging_verified=True,
        second_baseline_verified=True,
        replacement_outcome=(
            write_execution._CopiedTreeReplacementOutcome.COMPLETED
        ),
        recovery_outcome=(
            write_execution._CopiedTreeRecoveryStatus.NOT_NEEDED
        ),
        active_snapshot_sha256=(
            write_execution._favorites_storage_snapshot_sha256(
                plan.intended_snapshot
            )
        ),
    )

    write_execution._write_rollback_manifest(
        manifest_path,
        manifest,
    )
    write_execution._write_operation_report(
        report_path,
        report,
    )

    manifest_payload = json.loads(
        manifest_path.read_text(
            encoding="utf-8"
        )
    )
    report_payload = json.loads(
        report_path.read_text(
            encoding="utf-8"
        )
    )

    assert manifest_payload == manifest.as_dict()
    assert report_payload == report.as_dict()

    with pytest.raises(
        write_execution._CopiedTreeWritePreparationError,
        match="already exists",
    ):
        write_execution._write_operation_report(
            report_path,
            report,
        )


def test_public_executor_noop_is_side_effect_free(
    tmp_path: Path,
) -> None:
    target = tmp_path / "favorites_lists"
    _write_tree(target)
    before = tuple(
        sorted(
            path.name
            for path in tmp_path.iterdir()
        )
    )

    result = execute_favorites_copied_tree_write(
        plan_favorites_write(
            _snapshot(),
            _snapshot(),
        ),
        target,
    )

    assert (
        result.status
        is FavoritesCopiedTreeWriteExecutionStatus.NOOP
    )
    assert result.target_directory == target.resolve()
    assert result.operation_id is None
    assert result.backup_directory is None
    assert result.staging_directory is None
    assert result.displaced_directory is None
    assert result.rollback_manifest_path is None
    assert result.operation_report_path is None
    assert tuple(
        sorted(
            path.name
            for path in tmp_path.iterdir()
        )
    ) == before


def test_public_executor_completes_verified_copied_tree_write(
    tmp_path: Path,
) -> None:
    target = tmp_path / "favorites_lists"
    _write_tree(target)
    (target / "unmanaged.bin").write_bytes(
        b"preserve"
    )
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(_CHANGED_CATALOG),
    )

    result = execute_favorites_copied_tree_write(
        plan,
        target,
    )

    assert (
        result.status
        is FavoritesCopiedTreeWriteExecutionStatus.COMPLETED
    )
    assert result.operation_id is not None
    assert result.backup_directory is not None
    assert result.staging_directory is not None
    assert result.displaced_directory is not None
    assert result.rollback_manifest_path is not None
    assert result.operation_report_path is not None

    assert (target / "f_list.cfg").read_bytes() == _CHANGED_CATALOG
    assert (target / "unmanaged.bin").read_bytes() == b"preserve"
    assert result.backup_directory.is_dir()
    assert result.displaced_directory.is_dir()
    assert not result.staging_directory.exists()
    assert result.rollback_manifest_path.is_file()
    assert result.operation_report_path.is_file()

    manifest = json.loads(
        result.rollback_manifest_path.read_text(
            encoding="utf-8"
        )
    )
    report = json.loads(
        result.operation_report_path.read_text(
            encoding="utf-8"
        )
    )

    assert manifest["operation_id"] == result.operation_id
    assert report["operation_id"] == result.operation_id
    assert report["backup_verified"] is True
    assert report["staging_verified"] is True
    assert report["second_baseline_verified"] is True
    assert report["replacement_outcome"] == "completed"
    assert report["recovery_outcome"] == "not_needed"

    rendered = json.dumps(
        {
            "manifest": manifest,
            "report": report,
        },
        sort_keys=True,
    )
    assert "TargetModel" not in rendered
    assert "BCDx36HP" not in rendered
    assert "FormatVersion" not in rendered


def test_public_executor_second_stale_check_aborts_before_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "favorites_lists"
    _write_tree(target)
    unmanaged = target / "unmanaged.bin"
    unmanaged.write_bytes(b"before")
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(_CHANGED_CATALOG),
    )
    real_stage = (
        write_execution._create_verified_staging
    )

    def stage_then_change(
        preflight: FavoritesCopiedTreeWritePreflight,
        staging_directory: Path,
    ) -> write_execution._CopiedTreePreparedStage:
        prepared = real_stage(
            preflight,
            staging_directory,
        )
        unmanaged.write_bytes(
            b"changed after staging"
        )
        return prepared

    monkeypatch.setattr(
        write_execution,
        "_create_verified_staging",
        stage_then_change,
    )

    with pytest.raises(
        FavoritesCopiedTreeWriteExecutionError,
    ) as raised:
        execute_favorites_copied_tree_write(
            plan,
            target,
        )

    assert raised.value.operation_id is not None
    assert raised.value.report_path is not None
    report = json.loads(
        raised.value.report_path.read_text(
            encoding="utf-8"
        )
    )
    assert report["backup_verified"] is True
    assert report["staging_verified"] is True
    assert report["second_baseline_verified"] is False
    assert report["replacement_outcome"] == "not_started"
    assert report["recovery_outcome"] == "not_needed"
    assert (target / "f_list.cfg").read_bytes() == _BASELINE_CATALOG


def test_public_executor_reports_recovered_activation_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "favorites_lists"
    _write_tree(target)
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(_CHANGED_CATALOG),
    )
    real_rename = write_execution.os.rename
    calls = 0

    def failing_rename(
        src: Path,
        dst: Path,
    ) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError(
                "injected stage activation failure"
            )
        real_rename(
            src,
            dst,
        )

    monkeypatch.setattr(
        write_execution.os,
        "rename",
        failing_rename,
    )

    with pytest.raises(
        FavoritesCopiedTreeWriteExecutionError,
    ) as raised:
        execute_favorites_copied_tree_write(
            plan,
            target,
        )

    assert raised.value.recovery_status == "restored"
    assert raised.value.report_path is not None
    report = json.loads(
        raised.value.report_path.read_text(
            encoding="utf-8"
        )
    )
    assert report["backup_verified"] is True
    assert report["staging_verified"] is True
    assert report["second_baseline_verified"] is True
    assert report["replacement_outcome"] == "failed"
    assert report["recovery_outcome"] == "restored"
    assert (target / "f_list.cfg").read_bytes() == _BASELINE_CATALOG


def test_public_executor_report_failure_rolls_back_and_uses_failure_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "favorites_lists"
    _write_tree(target)
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(_CHANGED_CATALOG),
    )
    real_writer = (
        write_execution._write_operation_report
    )
    calls = 0

    def failing_once(
        path: Path,
        report: write_execution._CopiedTreeOperationReport,
    ) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise write_execution._CopiedTreeWritePreparationError(
                path,
                "injected final report failure",
            )
        real_writer(
            path,
            report,
        )

    monkeypatch.setattr(
        write_execution,
        "_write_operation_report",
        failing_once,
    )

    with pytest.raises(
        FavoritesCopiedTreeWriteExecutionError,
    ) as raised:
        execute_favorites_copied_tree_write(
            plan,
            target,
        )

    assert raised.value.recovery_status == "restored"
    assert raised.value.report_path is not None
    assert raised.value.report_path.name.endswith(
        ".failure.json"
    )
    report = json.loads(
        raised.value.report_path.read_text(
            encoding="utf-8"
        )
    )
    assert report["replacement_outcome"] == "failed"
    assert report["recovery_outcome"] == "restored"
    assert (target / "f_list.cfg").read_bytes() == _BASELINE_CATALOG


def test_public_executor_refuses_existing_operation_artifact_before_backup(
    tmp_path: Path,
) -> None:
    target = tmp_path / "favorites_lists"
    _write_tree(target)
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(_CHANGED_CATALOG),
    )
    preflight = preflight_favorites_copied_tree_write(
        plan,
        target,
    )
    paths = write_execution._copied_tree_operation_paths(
        preflight
    )
    paths.operation_report_path.write_text(
        "{}\n",
        encoding="utf-8",
    )

    with pytest.raises(
        FavoritesCopiedTreeWriteExecutionError,
    ) as raised:
        execute_favorites_copied_tree_write(
            plan,
            target,
        )

    assert raised.value.operation_id == paths.operation_id
    assert (target / "f_list.cfg").read_bytes() == _BASELINE_CATALOG
    assert not paths.backup_directory.exists()
    assert not paths.staging_directory.exists()
    assert not paths.displaced_directory.exists()
    assert not paths.rollback_manifest_path.exists()


def test_preflight_result_is_frozen_and_slot_backed(
    tmp_path: Path,
) -> None:
    target = tmp_path / "favorites_lists"
    _write_tree(target)
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(),
    )
    preflight = preflight_favorites_copied_tree_write(
        plan,
        target,
    )

    assert not hasattr(preflight, "__dict__")
    with pytest.raises(FrozenInstanceError):
        preflight.resolved_directory = tmp_path  # type: ignore[misc]


def test_preflight_constructor_rejects_nonmatching_snapshot(
    tmp_path: Path,
) -> None:
    target = tmp_path / "favorites_lists"
    _write_tree(target)
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(),
    )

    with pytest.raises(
        ValueError,
        match="baseline-matching",
    ):
        FavoritesCopiedTreeWritePreflight(
            plan=plan,
            requested_directory=target,
            resolved_directory=target.resolve(),
            observed_snapshot=_snapshot(_CHANGED_CATALOG),
            target_device=target.lstat().st_dev,
            target_inode=target.lstat().st_ino,
            tree_sha256="0" * 64,
        )


@pytest.mark.parametrize(
    ("plan", "directory", "message"),
    (
        (
            object(),
            Path("favorites_lists"),
            "requires FavoritesWritePlan",
        ),
        (
            plan_favorites_write(
                _snapshot(),
                _snapshot(),
            ),
            "favorites_lists",
            "must be pathlib.Path",
        ),
    ),
)
def test_preflight_requires_exact_contract_types(
    plan: object,
    directory: object,
    message: str,
) -> None:
    with pytest.raises(
        TypeError,
        match=message,
    ):
        preflight_favorites_copied_tree_write(  # type: ignore[arg-type]
            plan,
            directory,
        )


def test_preflight_error_validates_constructor_contract(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        TypeError,
        match="reason",
    ):
        FavoritesCopiedTreeWritePreflightError(  # type: ignore[arg-type]
            "target_stale",
            tmp_path,
            "message",
        )

    with pytest.raises(
        TypeError,
        match="path",
    ):
        FavoritesCopiedTreeWritePreflightError(  # type: ignore[arg-type]
            FavoritesCopiedTreeWritePreflightReason.TARGET_STALE,
            "favorites_lists",
            "message",
        )

    with pytest.raises(
        ValueError,
        match="must not be empty",
    ):
        FavoritesCopiedTreeWritePreflightError(
            FavoritesCopiedTreeWritePreflightReason.TARGET_STALE,
            tmp_path,
            "",
        )
