from __future__ import annotations

from pathlib import Path

import pytest

from sds200.favorites_storage import (
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
)
from sds200.favorites_storage_evidence import (
    FavoritesTreeEvidenceError,
    favorites_storage_snapshot_sha256,
    favorites_tree_evidence,
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
