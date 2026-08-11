from __future__ import annotations

from pathlib import Path

import pytest

from sds200 import (
    FavoritesCopiedTreeStorageError,
    FavoritesCopiedTreeStorageSource,
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


def _favorites_directory(
    tmp_path: Path,
) -> Path:
    directory = (
        tmp_path
        / "BCDx36HP"
        / "favorites_lists"
    )
    directory.mkdir(
        parents=True
    )
    return directory


def _write_catalog(
    directory: Path,
    content: bytes | None = None,
) -> bytes:
    selected = (
        _catalog_bytes()
        if content is None
        else content
    )
    (
        directory / "f_list.cfg"
    ).write_bytes(selected)
    return selected


def _write_hpd(
    directory: Path,
    filename: str = "f_000001.hpd",
    content: bytes | None = None,
) -> bytes:
    selected = (
        _hpd_bytes()
        if content is None
        else content
    )
    (
        directory / filename
    ).write_bytes(selected)
    return selected


def _symlink_or_skip(
    link: Path,
    target: Path,
    *,
    target_is_directory: bool = False,
) -> None:
    try:
        link.symlink_to(
            target,
            target_is_directory=target_is_directory,
        )
    except OSError as error:
        pytest.skip(
            f"symbolic links unavailable: {error}"
        )


def test_reads_exact_copied_tree_snapshot(
    tmp_path: Path,
) -> None:
    directory = _favorites_directory(
        tmp_path
    )
    catalog = _write_catalog(
        directory
    )
    hpd = _write_hpd(
        directory
    )

    source = FavoritesCopiedTreeStorageSource(
        directory
    )
    snapshot = source.read_snapshot()

    assert snapshot.catalog_bytes == catalog
    assert [
        document.filename
        for document in snapshot.documents
    ] == ["f_000001.hpd"]
    assert snapshot.documents[0].content == hpd


def test_copied_tree_projects_through_existing_workspace(
    tmp_path: Path,
) -> None:
    directory = _favorites_directory(
        tmp_path
    )
    catalog = _write_catalog(
        directory
    )
    hpd = _write_hpd(
        directory
    )

    source = FavoritesCopiedTreeStorageSource(
        directory
    )

    workspace = project_favorites_storage_snapshot(
        source.read_snapshot()
    )

    assert workspace.catalog.source.to_bytes() == catalog
    assert len(workspace.bindings) == 1
    assert workspace.missing_entries == ()
    assert workspace.ambiguous_entries == ()
    assert workspace.orphan_documents == ()

    binding = workspace.bindings[0]

    assert binding.name == "Synthetic Favorites"
    assert binding.filename == "f_000001.hpd"
    assert binding.hierarchy.source.to_bytes() == hpd


def test_missing_mapped_hpd_remains_workspace_diagnostic(
    tmp_path: Path,
) -> None:
    directory = _favorites_directory(
        tmp_path
    )
    _write_catalog(
        directory
    )

    workspace = project_favorites_storage_snapshot(
        FavoritesCopiedTreeStorageSource(
            directory
        ).read_snapshot()
    )

    assert workspace.bindings == ()
    assert len(workspace.missing_entries) == 1
    assert workspace.ambiguous_entries == ()
    assert workspace.orphan_documents == ()


def test_orphan_hpd_remains_workspace_diagnostic(
    tmp_path: Path,
) -> None:
    directory = _favorites_directory(
        tmp_path
    )
    _write_catalog(
        directory
    )
    _write_hpd(
        directory
    )
    orphan_bytes = _write_hpd(
        directory,
        "orphan.hpd",
    )

    snapshot = FavoritesCopiedTreeStorageSource(
        directory
    ).read_snapshot()

    assert [
        document.filename
        for document in snapshot.documents
    ] == [
        "f_000001.hpd",
        "orphan.hpd",
    ]
    assert snapshot.documents[1].content == orphan_bytes

    workspace = project_favorites_storage_snapshot(
        snapshot
    )

    assert len(workspace.bindings) == 1
    assert [
        document.filename
        for document in workspace.orphan_documents
    ] == ["orphan.hpd"]


def test_documents_are_sorted_by_exact_filename(
    tmp_path: Path,
) -> None:
    directory = _favorites_directory(
        tmp_path
    )
    _write_catalog(
        directory,
        b"",
    )

    for filename in (
        "z.hpd",
        "a.hpd",
        "A.hpd",
    ):
        _write_hpd(
            directory,
            filename,
            b"",
        )

    snapshot = FavoritesCopiedTreeStorageSource(
        directory
    ).read_snapshot()

    assert [
        document.filename
        for document in snapshot.documents
    ] == [
        "A.hpd",
        "a.hpd",
        "z.hpd",
    ]


def test_uppercase_hpd_extension_is_not_case_folded(
    tmp_path: Path,
) -> None:
    directory = _favorites_directory(
        tmp_path
    )
    _write_catalog(
        directory,
        b"",
    )
    _write_hpd(
        directory,
        "lower.hpd",
        b"",
    )
    (
        directory / "upper.HPD"
    ).write_bytes(b"not selected")

    snapshot = FavoritesCopiedTreeStorageSource(
        directory
    ).read_snapshot()

    assert [
        document.filename
        for document in snapshot.documents
    ] == ["lower.hpd"]


def test_nested_hpd_is_not_discovered(
    tmp_path: Path,
) -> None:
    directory = _favorites_directory(
        tmp_path
    )
    _write_catalog(
        directory,
        b"",
    )

    nested = directory / "nested"
    nested.mkdir()
    (
        nested / "nested.hpd"
    ).write_bytes(b"nested")

    snapshot = FavoritesCopiedTreeStorageSource(
        directory
    ).read_snapshot()

    assert snapshot.documents == ()


def test_unrelated_immediate_files_are_ignored(
    tmp_path: Path,
) -> None:
    directory = _favorites_directory(
        tmp_path
    )
    _write_catalog(
        directory,
        b"",
    )

    (
        directory / "notes.txt"
    ).write_bytes(b"notes")
    (
        directory / "f_list.cfg.bak"
    ).write_bytes(b"backup")

    snapshot = FavoritesCopiedTreeStorageSource(
        directory
    ).read_snapshot()

    assert snapshot.documents == ()


def test_hpd_named_directory_is_not_discovered(
    tmp_path: Path,
) -> None:
    directory = _favorites_directory(
        tmp_path
    )
    _write_catalog(
        directory,
        b"",
    )

    (
        directory / "directory.hpd"
    ).mkdir()

    snapshot = FavoritesCopiedTreeStorageSource(
        directory
    ).read_snapshot()

    assert snapshot.documents == ()


def test_missing_favorites_directory_is_rejected(
    tmp_path: Path,
) -> None:
    directory = (
        tmp_path
        / "missing"
        / "favorites_lists"
    )

    with pytest.raises(
        FavoritesCopiedTreeStorageError,
        match="Favorites directory does not exist",
    ) as captured:
        FavoritesCopiedTreeStorageSource(
            directory
        ).read_snapshot()

    assert captured.value.path == directory


def test_non_directory_root_is_rejected(
    tmp_path: Path,
) -> None:
    path = tmp_path / "favorites_lists"
    path.write_bytes(b"not directory")

    with pytest.raises(
        FavoritesCopiedTreeStorageError,
        match="must be a directory",
    ):
        FavoritesCopiedTreeStorageSource(
            path
        ).read_snapshot()


def test_symbolic_link_root_is_rejected(
    tmp_path: Path,
) -> None:
    real = _favorites_directory(
        tmp_path
    )
    link = tmp_path / "favorites-link"

    _symlink_or_skip(
        link,
        real,
        target_is_directory=True,
    )

    with pytest.raises(
        FavoritesCopiedTreeStorageError,
        match="must not be a symbolic link",
    ):
        FavoritesCopiedTreeStorageSource(
            link
        ).read_snapshot()


def test_missing_catalog_is_rejected(
    tmp_path: Path,
) -> None:
    directory = _favorites_directory(
        tmp_path
    )

    with pytest.raises(
        FavoritesCopiedTreeStorageError,
        match="Favorites catalog file does not exist",
    ):
        FavoritesCopiedTreeStorageSource(
            directory
        ).read_snapshot()


def test_catalog_directory_is_rejected(
    tmp_path: Path,
) -> None:
    directory = _favorites_directory(
        tmp_path
    )
    (
        directory / "f_list.cfg"
    ).mkdir()

    with pytest.raises(
        FavoritesCopiedTreeStorageError,
        match="Favorites catalog file must be a regular file",
    ):
        FavoritesCopiedTreeStorageSource(
            directory
        ).read_snapshot()


def test_catalog_symbolic_link_is_rejected(
    tmp_path: Path,
) -> None:
    directory = _favorites_directory(
        tmp_path
    )
    target = directory / "catalog-real"
    target.write_bytes(
        _catalog_bytes()
    )

    _symlink_or_skip(
        directory / "f_list.cfg",
        target,
    )

    with pytest.raises(
        FavoritesCopiedTreeStorageError,
        match="Favorites catalog file must not be a symbolic link",
    ):
        FavoritesCopiedTreeStorageSource(
            directory
        ).read_snapshot()


def test_hpd_symbolic_link_is_rejected(
    tmp_path: Path,
) -> None:
    directory = _favorites_directory(
        tmp_path
    )
    _write_catalog(
        directory,
        b"",
    )

    target = directory / "real-data"
    target.write_bytes(
        _hpd_bytes()
    )

    _symlink_or_skip(
        directory / "linked.hpd",
        target,
    )

    with pytest.raises(
        FavoritesCopiedTreeStorageError,
        match="Favorites HPD file must not be a symbolic link",
    ):
        FavoritesCopiedTreeStorageSource(
            directory
        ).read_snapshot()


def test_unmanaged_symbolic_link_is_ignored(
    tmp_path: Path,
) -> None:
    directory = _favorites_directory(
        tmp_path
    )
    _write_catalog(
        directory,
        b"",
    )

    target = directory / "real-data"
    target.write_bytes(b"data")

    _symlink_or_skip(
        directory / "ignored.txt",
        target,
    )

    snapshot = FavoritesCopiedTreeStorageSource(
        directory
    ).read_snapshot()

    assert snapshot.documents == ()


def test_source_requires_path_object() -> None:
    with pytest.raises(
        TypeError,
        match="must be pathlib.Path",
    ):
        FavoritesCopiedTreeStorageSource(  # type: ignore[arg-type]
            "favorites_lists"
        )


def test_source_satisfies_storage_protocol(
    tmp_path: Path,
) -> None:
    directory = _favorites_directory(
        tmp_path
    )
    _write_catalog(
        directory,
        b"",
    )

    source = FavoritesCopiedTreeStorageSource(
        directory
    )

    def read(
        storage: FavoritesStorageSource,
    ) -> bytes:
        return storage.read_snapshot().catalog_bytes

    assert read(source) == b""
