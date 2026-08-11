from __future__ import annotations

from pathlib import Path

import pytest

from sds200 import (
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
    FavoritesWorkspace,
    export_favorites_workspace_snapshot,
    project_favorites_storage_snapshot,
)

_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "favorites"


def _round_trip(
    snapshot: FavoritesStorageSnapshot,
) -> tuple[FavoritesStorageSnapshot, FavoritesWorkspace]:
    workspace = project_favorites_storage_snapshot(snapshot)
    exported = export_favorites_workspace_snapshot(workspace)

    assert exported == snapshot
    assert exported.catalog_bytes == workspace.catalog.source.to_bytes()
    assert tuple(
        document.filename
        for document in exported.documents
    ) == tuple(
        document.filename
        for document in workspace.documents
    )
    assert tuple(
        document.content
        for document in exported.documents
    ) == tuple(
        document.hierarchy.source.to_bytes()
        for document in workspace.documents
    )

    return exported, workspace


def test_synthetic_storage_snapshot_round_trips_exactly() -> None:
    catalog = (
        _FIXTURE_ROOT / "synthetic-f_list.cfg"
    ).read_bytes()
    hpd = (
        _FIXTURE_ROOT / "synthetic-favorites.hpd"
    ).read_bytes()
    snapshot = FavoritesStorageSnapshot(
        catalog_bytes=catalog,
        documents=(
            FavoritesStorageDocument(
                filename="f_000001.hpd",
                content=hpd,
            ),
        ),
    )

    exported, _ = _round_trip(snapshot)

    assert exported is not snapshot
    assert exported.documents[0] is not snapshot.documents[0]


def test_unknown_records_extensions_and_physical_lines_round_trip() -> None:
    catalog = (
        b"TargetModel\tBCDx36HP\r\n"
        b"FormatVersion\t1.00\n"
        b"F-List\tSynthetic\tf_000001.hpd\r"
        b"FutureCatalog\talpha\t\tomega\t"
    )
    hpd = (
        b"TargetModel\tBCDx36HP\n"
        b"FormatVersion\t1.00\r\n"
        b"Conventional\t\t\tSystem\r"
        b"FutureHPD\talpha\t\tomega\t"
    )
    snapshot = FavoritesStorageSnapshot(
        catalog_bytes=catalog,
        documents=(
            FavoritesStorageDocument(
                filename="f_000001.hpd",
                content=hpd,
            ),
        ),
    )

    exported, workspace = _round_trip(snapshot)

    assert exported.catalog_bytes == catalog
    assert exported.documents[0].content == hpd
    assert workspace.catalog.source.records[-1].line_ending == b""
    assert (
        workspace.documents[0]
        .hierarchy.source.records[-1]
        .line_ending
        == b""
    )


def test_document_order_and_duplicate_filenames_round_trip() -> None:
    snapshot = FavoritesStorageSnapshot(
        catalog_bytes=(
            b"F-List\tZed\tz.hpd\r\n"
            b"F-List\tDuplicate\tdup.hpd\r\n"
        ),
        documents=(
            FavoritesStorageDocument(
                filename="z.hpd",
                content=b"Future\tz\r\n",
            ),
            FavoritesStorageDocument(
                filename="dup.hpd",
                content=b"Future\tfirst\n",
            ),
            FavoritesStorageDocument(
                filename="dup.hpd",
                content=b"Future\tsecond\r",
            ),
            FavoritesStorageDocument(
                filename="orphan.hpd",
                content=b"Future\torphan",
            ),
        ),
    )

    exported, workspace = _round_trip(snapshot)

    assert [
        document.filename
        for document in exported.documents
    ] == [
        "z.hpd",
        "dup.hpd",
        "dup.hpd",
        "orphan.hpd",
    ]
    assert workspace.duplicate_document_filenames == ("dup.hpd",)
    assert [
        entry.filename
        for entry in workspace.ambiguous_entries
    ] == ["dup.hpd"]
    assert [
        document.filename
        for document in workspace.orphan_documents
    ] == ["orphan.hpd"]


def test_missing_catalog_target_does_not_change_export() -> None:
    snapshot = FavoritesStorageSnapshot(
        catalog_bytes=(
            b"F-List\tMissing\tmissing.hpd\r\n"
        ),
        documents=(),
    )

    _, workspace = _round_trip(snapshot)

    assert [
        entry.filename
        for entry in workspace.missing_entries
    ] == ["missing.hpd"]


def test_orphan_document_does_not_change_export() -> None:
    snapshot = FavoritesStorageSnapshot(
        catalog_bytes=b"",
        documents=(
            FavoritesStorageDocument(
                filename="orphan.hpd",
                content=b"Future\torphan\r\n",
            ),
        ),
    )

    _, workspace = _round_trip(snapshot)

    assert [
        document.filename
        for document in workspace.orphan_documents
    ] == ["orphan.hpd"]


def test_duplicate_catalog_filenames_do_not_change_export() -> None:
    snapshot = FavoritesStorageSnapshot(
        catalog_bytes=(
            b"F-List\tFirst\tdup.hpd\r\n"
            b"F-List\tSecond\tdup.hpd\r\n"
        ),
        documents=(
            FavoritesStorageDocument(
                filename="dup.hpd",
                content=b"Future\tvalue\r\n",
            ),
        ),
    )

    _, workspace = _round_trip(snapshot)

    assert workspace.duplicate_catalog_filenames == ("dup.hpd",)


def test_empty_catalog_and_hpd_sources_round_trip() -> None:
    snapshot = FavoritesStorageSnapshot(
        catalog_bytes=b"",
        documents=(
            FavoritesStorageDocument(
                filename="empty.hpd",
                content=b"",
            ),
        ),
    )

    exported, workspace = _round_trip(snapshot)

    assert exported.catalog_bytes == b""
    assert exported.documents[0].content == b""
    assert workspace.catalog.source.records == ()
    assert workspace.documents[0].hierarchy.source.records == ()


def test_export_uses_workspace_document_order_not_bindings() -> None:
    snapshot = FavoritesStorageSnapshot(
        catalog_bytes=(
            b"F-List\tFirst\ta.hpd\r\n"
            b"F-List\tSecond\tz.hpd\r\n"
        ),
        documents=(
            FavoritesStorageDocument(
                filename="z.hpd",
                content=b"Future\tz\r\n",
            ),
            FavoritesStorageDocument(
                filename="a.hpd",
                content=b"Future\ta\r\n",
            ),
        ),
    )

    exported, workspace = _round_trip(snapshot)

    assert [
        binding.filename
        for binding in workspace.bindings
    ] == ["a.hpd", "z.hpd"]
    assert [
        document.filename
        for document in workspace.documents
    ] == ["z.hpd", "a.hpd"]
    assert [
        document.filename
        for document in exported.documents
    ] == ["z.hpd", "a.hpd"]


def test_export_requires_workspace() -> None:
    with pytest.raises(
        TypeError,
        match="Favorites workspace export requires FavoritesWorkspace",
    ):
        export_favorites_workspace_snapshot(  # type: ignore[arg-type]
            b"workspace"
        )
