from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from sds200 import (
    FavoritesStorageDocument,
    FavoritesStorageFilenameError,
    FavoritesStorageSnapshot,
    FavoritesStorageSource,
    project_favorites_storage_snapshot,
)

_FIXTURE_ROOT = (
    Path(__file__).parent / "fixtures" / "favorites"
)


def _catalog_bytes() -> bytes:
    return (
        _FIXTURE_ROOT / "synthetic-f_list.cfg"
    ).read_bytes()


def _hpd_bytes() -> bytes:
    return (
        _FIXTURE_ROOT / "synthetic-favorites.hpd"
    ).read_bytes()


def _snapshot(
    *documents: FavoritesStorageDocument,
    catalog_bytes: bytes | None = None,
) -> FavoritesStorageSnapshot:
    return FavoritesStorageSnapshot(
        catalog_bytes=(
            _catalog_bytes()
            if catalog_bytes is None
            else catalog_bytes
        ),
        documents=documents,
    )


def _document(
    filename: str = "f_000001.hpd",
    content: bytes | None = None,
) -> FavoritesStorageDocument:
    return FavoritesStorageDocument(
        filename=filename,
        content=(
            _hpd_bytes()
            if content is None
            else content
        ),
    )


def test_projects_synthetic_storage_snapshot_end_to_end() -> None:
    catalog_bytes = _catalog_bytes()
    hpd_bytes = _hpd_bytes()
    document = FavoritesStorageDocument(
        filename="f_000001.hpd",
        content=hpd_bytes,
    )
    snapshot = FavoritesStorageSnapshot(
        catalog_bytes=catalog_bytes,
        documents=(document,),
    )

    workspace = project_favorites_storage_snapshot(
        snapshot
    )

    assert workspace.catalog.source.to_bytes() == catalog_bytes
    assert len(workspace.bindings) == 1
    assert workspace.missing_entries == ()
    assert workspace.ambiguous_entries == ()
    assert workspace.orphan_documents == ()

    binding = workspace.bindings[0]

    assert binding.name == "Synthetic Favorites"
    assert binding.filename == "f_000001.hpd"
    assert binding.document.filename == document.filename
    assert binding.hierarchy.source.to_bytes() == hpd_bytes
    assert len(binding.hierarchy.systems) == 2


def test_missing_mapped_document_uses_workspace_diagnostic() -> None:
    workspace = project_favorites_storage_snapshot(
        _snapshot()
    )

    assert workspace.bindings == ()
    assert workspace.missing_entries == (
        workspace.catalog.entries[0],
    )
    assert workspace.ambiguous_entries == ()
    assert workspace.orphan_documents == ()


def test_orphan_document_uses_workspace_diagnostic() -> None:
    mapped = _document()
    orphan = _document("orphan.hpd")

    workspace = project_favorites_storage_snapshot(
        _snapshot(
            mapped,
            orphan,
        )
    )

    assert len(workspace.bindings) == 1
    assert workspace.missing_entries == ()
    assert workspace.ambiguous_entries == ()
    assert [
        document.filename
        for document in workspace.orphan_documents
    ] == ["orphan.hpd"]


def test_duplicate_documents_remain_ambiguous() -> None:
    first = _document()
    second = _document()

    workspace = project_favorites_storage_snapshot(
        _snapshot(
            first,
            second,
        )
    )

    assert workspace.bindings == ()
    assert workspace.missing_entries == ()
    assert workspace.ambiguous_entries == (
        workspace.catalog.entries[0],
    )
    assert workspace.duplicate_document_filenames == (
        "f_000001.hpd",
    )


def test_document_order_remains_source_order() -> None:
    first = _document("z.hpd")
    second = _document("a.hpd")

    snapshot = _snapshot(
        first,
        second,
    )
    workspace = project_favorites_storage_snapshot(
        snapshot
    )

    assert [
        document.filename
        for document in snapshot.documents
    ] == [
        "z.hpd",
        "a.hpd",
    ]
    assert [
        document.filename
        for document in workspace.documents
    ] == [
        "z.hpd",
        "a.hpd",
    ]


@pytest.mark.parametrize(
    "filename",
    (
        "",
        ".",
        "..",
        "../f_000001.hpd",
        "sub/f_000001.hpd",
        "/tmp/f_000001.hpd",
        r"..\f_000001.hpd",
        r"sub\f_000001.hpd",
        r"C:\scanner\f_000001.hpd",
        r"C:f_000001.hpd",
        "\x00bad.hpd",
    ),
)
def test_storage_document_rejects_unsafe_filename(
    filename: str,
) -> None:
    with pytest.raises(
        FavoritesStorageFilenameError,
        match="one exact non-traversing file name",
    ):
        FavoritesStorageDocument(
            filename=filename,
            content=b"",
        )


@pytest.mark.parametrize(
    "filename",
    (
        "../f_000001.hpd",
        "sub/f_000001.hpd",
        "/tmp/f_000001.hpd",
        r"..\f_000001.hpd",
        r"sub\f_000001.hpd",
        r"C:\scanner\f_000001.hpd",
        r"C:f_000001.hpd",
    ),
)
def test_catalog_rejects_unsafe_storage_filename(
    filename: str,
) -> None:
    catalog_bytes = (
        b"F-List\tUnsafe\t"
        + filename.encode("ascii")
        + b"\r\n"
    )

    with pytest.raises(
        FavoritesStorageFilenameError,
        match="Favorites catalog record 1 filename",
    ) as captured:
        project_favorites_storage_snapshot(
            _snapshot(
                catalog_bytes=catalog_bytes,
            )
        )

    assert captured.value.filename == filename
    assert captured.value.source_index == 0


def test_catalog_filename_is_not_trimmed() -> None:
    document = _document(
        " f_000001.hpd "
    )
    catalog_bytes = (
        b"F-List\tExact\t f_000001.hpd \r\n"
    )

    workspace = project_favorites_storage_snapshot(
        _snapshot(
            document,
            catalog_bytes=catalog_bytes,
        )
    )

    assert len(workspace.bindings) == 1
    assert workspace.bindings[0].filename == (
        " f_000001.hpd "
    )


def test_catalog_filename_is_not_case_folded() -> None:
    document = _document(
        "F_000001.HPD"
    )

    workspace = project_favorites_storage_snapshot(
        _snapshot(document)
    )

    assert workspace.bindings == ()
    assert workspace.missing_entries == (
        workspace.catalog.entries[0],
    )
    assert workspace.orphan_documents == (
        workspace.documents[0],
    )


def test_unknown_catalog_and_hpd_records_remain_lossless() -> None:
    catalog_bytes = (
        b"TargetModel\tBCDx36HP\r\n"
        b"FormatVersion\t1.00\r\n"
        b"F-List\tSynthetic\tf_000001.hpd\r\n"
        b"FutureCatalog\talpha\t\r\n"
    )
    hpd_bytes = (
        b"TargetModel\tBCDx36HP\r\n"
        b"FormatVersion\t1.00\r\n"
        b"Conventional\t\t\tSystem\r\n"
        b"FutureHPD\talpha\t\tomega\t\r\n"
    )

    workspace = project_favorites_storage_snapshot(
        FavoritesStorageSnapshot(
            catalog_bytes=catalog_bytes,
            documents=(
                FavoritesStorageDocument(
                    filename="f_000001.hpd",
                    content=hpd_bytes,
                ),
            ),
        )
    )

    assert workspace.catalog.source.to_bytes() == catalog_bytes
    assert (
        workspace.bindings[0]
        .hierarchy.source.to_bytes()
        == hpd_bytes
    )

    assert [
        workspace.catalog.source.records[
            index
        ].command
        for index in workspace.catalog.unclassified_indexes
    ] == ["FutureCatalog"]

    assert [
        reference.record.command
        for reference in (
            workspace.bindings[0]
            .hierarchy.unclassified_records
        )
    ] == ["FutureHPD"]


def test_storage_models_are_immutable() -> None:
    document = _document()
    snapshot = _snapshot(document)

    with pytest.raises(FrozenInstanceError):
        document.filename = "changed.hpd"  # type: ignore[misc]

    with pytest.raises(FrozenInstanceError):
        snapshot.documents = ()  # type: ignore[misc]


def test_storage_document_requires_exact_types() -> None:
    with pytest.raises(
        TypeError,
        match="filename must be a string",
    ):
        FavoritesStorageDocument(  # type: ignore[arg-type]
            filename=123,
            content=b"",
        )

    with pytest.raises(
        TypeError,
        match="content must be bytes",
    ):
        FavoritesStorageDocument(  # type: ignore[arg-type]
            filename="f_000001.hpd",
            content="not bytes",
        )


def test_storage_snapshot_requires_exact_types() -> None:
    document = _document()

    with pytest.raises(
        TypeError,
        match="catalog content must be bytes",
    ):
        FavoritesStorageSnapshot(  # type: ignore[arg-type]
            catalog_bytes="not bytes",
            documents=(),
        )

    with pytest.raises(
        TypeError,
        match="documents must be a tuple",
    ):
        FavoritesStorageSnapshot(  # type: ignore[arg-type]
            catalog_bytes=b"",
            documents=[document],
        )

    with pytest.raises(
        TypeError,
        match="must contain FavoritesStorageDocument",
    ):
        FavoritesStorageSnapshot(  # type: ignore[arg-type]
            catalog_bytes=b"",
            documents=(b"not document",),
        )


def test_storage_projection_requires_snapshot() -> None:
    with pytest.raises(
        TypeError,
        match="requires FavoritesStorageSnapshot",
    ):
        project_favorites_storage_snapshot(  # type: ignore[arg-type]
            b"snapshot"
        )


def test_storage_source_protocol_shape() -> None:
    snapshot = _snapshot()

    class SyntheticStorageSource:
        def read_snapshot(
            self,
        ) -> FavoritesStorageSnapshot:
            return snapshot

    def read(
        source: FavoritesStorageSource,
    ) -> FavoritesStorageSnapshot:
        return source.read_snapshot()

    assert read(
        SyntheticStorageSource()
    ) is snapshot
