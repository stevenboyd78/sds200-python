from __future__ import annotations

from pathlib import Path

import pytest

import sds200.favorites_storage_evidence as storage_evidence
from sds200.favorites_storage import (
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
)
from sds200.favorites_storage_evidence import (
    FavoritesTreeEvidenceError,
    favorites_storage_snapshot_sha256,
    favorites_tree_evidence,
    favorites_unmanaged_tree_sha256,
)


def _snapshot(
    catalog: bytes = b"TargetModel\\tBCDx36HP\\r\\n",
) -> FavoritesStorageSnapshot:
    return FavoritesStorageSnapshot(
        catalog_bytes=catalog,
        documents=(
            FavoritesStorageDocument(
                filename="f_000001.hpd",
                content=b"Department\\tExample\\r\\n",
            ),
        ),
    )


def test_snapshot_identity_is_stable_and_exact() -> None:
    baseline = _snapshot()
    changed = _snapshot(
        b"TargetModel\\tBCDx36HP\\n"
    )

    baseline_digest = favorites_storage_snapshot_sha256(
        baseline
    )

    assert len(baseline_digest) == 64
    assert baseline_digest == favorites_storage_snapshot_sha256(
        baseline
    )
    assert baseline_digest != favorites_storage_snapshot_sha256(
        changed
    )


def test_tree_evidence_captures_root_identity_and_unmanaged_content(
    tmp_path: Path,
) -> None:
    root = tmp_path / "favorites_lists"
    root.mkdir()
    (root / "f_list.cfg").write_bytes(
        b"TargetModel\\tBCDx36HP\\r\\n"
    )
    unmanaged = root / "offline-notes.bin"
    unmanaged.write_bytes(b"first")

    first = favorites_tree_evidence(root)

    root_status = root.lstat()
    assert first.device == root_status.st_dev
    assert first.inode == root_status.st_ino
    assert len(first.sha256) == 64

    unmanaged.write_bytes(b"second")
    second = favorites_tree_evidence(root)

    assert second.device == first.device
    assert second.inode == first.inode
    assert second.sha256 != first.sha256


def test_tree_evidence_rejects_symbolic_links(
    tmp_path: Path,
) -> None:
    root = tmp_path / "favorites_lists"
    root.mkdir()
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    link = root / "unsafe-link"
    link.symlink_to(outside)

    with pytest.raises(
        FavoritesTreeEvidenceError,
        match="symbolic links",
    ) as raised:
        favorites_tree_evidence(root)

    assert raised.value.path == link


def test_tree_evidence_requires_pathlib_path() -> None:
    with pytest.raises(
        TypeError,
        match="pathlib.Path",
    ):
        favorites_tree_evidence(  # type: ignore[arg-type]
            "."
        )


def _write_unmanaged_tree_fixture(
    root: Path,
) -> None:
    root.mkdir()
    (root / "f_list.cfg").write_bytes(
        b"catalog"
    )
    (root / "one.hpd").write_bytes(
        b"managed"
    )
    (root / "ONE.HPD").write_bytes(
        b"uppercase-unmanaged"
    )
    (root / "one.HPD").write_bytes(
        b"mixed-unmanaged"
    )
    (root / "notes.bin").write_bytes(
        b"notes"
    )
    nested = root / "nested"
    nested.mkdir()
    (nested / "example.hpd").write_bytes(
        b"nested-unmanaged"
    )
    shadow = root / "shadow.hpd"
    shadow.mkdir()
    (shadow / "inside.bin").write_bytes(
        b"directory-name-is-unmanaged"
    )


def test_unmanaged_tree_identity_ignores_root_managed_file_content(
    tmp_path: Path,
) -> None:
    root = tmp_path / "favorites_lists"
    _write_unmanaged_tree_fixture(
        root
    )

    baseline = favorites_unmanaged_tree_sha256(
        root
    )

    (root / "f_list.cfg").write_bytes(
        b"changed-catalog"
    )
    (root / "one.hpd").write_bytes(
        b"changed-managed"
    )

    assert (
        favorites_unmanaged_tree_sha256(
            root
        )
        == baseline
    )


def test_unmanaged_tree_identity_includes_case_variants_nested_hpd_and_temp(
    tmp_path: Path,
) -> None:
    root = tmp_path / "favorites_lists"
    _write_unmanaged_tree_fixture(
        root
    )

    baseline = favorites_unmanaged_tree_sha256(
        root
    )

    (root / "ONE.HPD").write_bytes(
        b"changed-uppercase"
    )
    uppercase_changed = (
        favorites_unmanaged_tree_sha256(
            root
        )
    )
    assert uppercase_changed != baseline

    (root / "ONE.HPD").write_bytes(
        b"uppercase-unmanaged"
    )
    (root / "nested" / "example.hpd").write_bytes(
        b"changed-nested"
    )
    nested_changed = (
        favorites_unmanaged_tree_sha256(
            root
        )
    )
    assert nested_changed != baseline

    (root / "nested" / "example.hpd").write_bytes(
        b"nested-unmanaged"
    )
    temporary = (
        root
        / ".sds200-usb-write-example.tmp"
    )
    temporary.write_bytes(
        b"surviving-artifact"
    )

    assert (
        favorites_unmanaged_tree_sha256(
            root
        )
        != baseline
    )


def test_unmanaged_tree_identity_includes_empty_directories_and_hpd_directories(
    tmp_path: Path,
) -> None:
    root = tmp_path / "favorites_lists"
    _write_unmanaged_tree_fixture(
        root
    )

    baseline = favorites_unmanaged_tree_sha256(
        root
    )

    empty = root / "empty"
    empty.mkdir()
    with_empty = (
        favorites_unmanaged_tree_sha256(
            root
        )
    )
    assert with_empty != baseline

    empty.rmdir()
    (
        root
        / "shadow.hpd"
        / "inside.bin"
    ).write_bytes(
        b"changed-directory-content"
    )

    assert (
        favorites_unmanaged_tree_sha256(
            root
        )
        != baseline
    )


def test_unmanaged_tree_identity_ignores_filesystem_mode_metadata(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_unmanaged_tree_fixture(
        first
    )
    _write_unmanaged_tree_fixture(
        second
    )

    (first / "notes.bin").chmod(
        0o600
    )
    (second / "notes.bin").chmod(
        0o644
    )
    (first / "nested").chmod(
        0o700
    )
    (second / "nested").chmod(
        0o755
    )

    assert (
        favorites_unmanaged_tree_sha256(
            first
        )
        == favorites_unmanaged_tree_sha256(
            second
        )
    )


def test_unmanaged_tree_identity_rejects_symbolic_links(
    tmp_path: Path,
) -> None:
    root = tmp_path / "favorites_lists"
    root.mkdir()
    outside = tmp_path / "outside.bin"
    outside.write_bytes(
        b"outside"
    )
    link = root / "unsafe-link"
    link.symlink_to(
        outside
    )

    with pytest.raises(
        FavoritesTreeEvidenceError,
        match="symbolic links",
    ) as raised:
        favorites_unmanaged_tree_sha256(
            root
        )

    assert raised.value.path == link


def test_unmanaged_tree_identity_detects_regular_file_change_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "favorites_lists"
    root.mkdir()
    (root / "notes.bin").write_bytes(
        b"notes"
    )

    real_fingerprint = (
        storage_evidence._stat_fingerprint
    )
    calls = 0

    def changing_fingerprint(
        value: object,
    ) -> tuple[int, int, int, int, int]:
        nonlocal calls
        calls += 1
        fingerprint = real_fingerprint(
            value  # type: ignore[arg-type]
        )
        if calls == 2:
            return (
                fingerprint[0],
                fingerprint[1],
                fingerprint[2],
                fingerprint[3] + 1,
                fingerprint[4],
            )
        return fingerprint

    monkeypatch.setattr(
        storage_evidence,
        "_stat_fingerprint",
        changing_fingerprint,
    )

    with pytest.raises(
        FavoritesTreeEvidenceError,
        match="changed while being read",
    ):
        favorites_unmanaged_tree_sha256(
            root
        )


def test_unmanaged_tree_identity_requires_pathlib_path() -> None:
    with pytest.raises(
        TypeError,
        match="pathlib.Path",
    ):
        favorites_unmanaged_tree_sha256(  # type: ignore[arg-type]
            "."
        )
