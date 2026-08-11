from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from sds200 import (
    FavoritesCatalog,
    FavoritesHierarchy,
    FavoritesHierarchyDocument,
    FavoritesWorkspaceBinding,
    bind_favorites_workspace,
    parse_favorites_file,
    project_favorites_catalog,
    project_favorites_hierarchy,
)

_FIXTURE_ROOT = (
    Path(__file__).parent / "fixtures" / "favorites"
)


def _fixture_catalog() -> FavoritesCatalog:
    return project_favorites_catalog(
        parse_favorites_file(
            (
                _FIXTURE_ROOT
                / "synthetic-f_list.cfg"
            ).read_bytes()
        )
    )


def _fixture_hierarchy() -> FavoritesHierarchy:
    return project_favorites_hierarchy(
        parse_favorites_file(
            (
                _FIXTURE_ROOT
                / "synthetic-favorites.hpd"
            ).read_bytes()
        )
    )


def _document(
    filename: str,
) -> FavoritesHierarchyDocument:
    return FavoritesHierarchyDocument(
        filename=filename,
        hierarchy=_fixture_hierarchy(),
    )


def test_binds_synthetic_catalog_to_hierarchy() -> None:
    catalog = _fixture_catalog()
    document = _document("f_000001.hpd")

    workspace = bind_favorites_workspace(
        catalog,
        (document,),
    )

    assert workspace.catalog is catalog
    assert workspace.documents == (document,)
    assert len(workspace.bindings) == 1
    assert workspace.missing_entries == ()
    assert workspace.ambiguous_entries == ()
    assert workspace.duplicate_catalog_filenames == ()
    assert workspace.duplicate_document_filenames == ()
    assert workspace.orphan_documents == ()

    binding = workspace.bindings[0]

    assert binding.entry is catalog.entries[0]
    assert binding.document is document
    assert binding.name == "Synthetic Favorites"
    assert binding.filename == "f_000001.hpd"
    assert binding.hierarchy is document.hierarchy
    assert len(binding.hierarchy.systems) == 2


def test_missing_catalog_target_is_explicit() -> None:
    catalog = _fixture_catalog()

    workspace = bind_favorites_workspace(
        catalog,
        (),
    )

    assert workspace.bindings == ()
    assert workspace.missing_entries == (
        catalog.entries[0],
    )
    assert workspace.ambiguous_entries == ()
    assert workspace.orphan_documents == ()


def test_duplicate_catalog_filename_is_reported_without_dropping_entries() -> None:
    catalog = project_favorites_catalog(
        parse_favorites_file(
            b"F-List\tFirst\tf_000001.hpd\r\n"
            b"F-List\tSecond\tf_000001.hpd\r\n"
        )
    )
    document = _document("f_000001.hpd")

    workspace = bind_favorites_workspace(
        catalog,
        (document,),
    )

    assert workspace.duplicate_catalog_filenames == (
        "f_000001.hpd",
    )
    assert [
        binding.name
        for binding in workspace.bindings
    ] == [
        "First",
        "Second",
    ]
    assert all(
        binding.document is document
        for binding in workspace.bindings
    )
    assert workspace.missing_entries == ()
    assert workspace.ambiguous_entries == ()


def test_duplicate_documents_make_matching_entry_ambiguous() -> None:
    catalog = _fixture_catalog()
    first = _document("f_000001.hpd")
    second = _document("f_000001.hpd")

    workspace = bind_favorites_workspace(
        catalog,
        (
            first,
            second,
        ),
    )

    assert workspace.bindings == ()
    assert workspace.missing_entries == ()
    assert workspace.ambiguous_entries == (
        catalog.entries[0],
    )
    assert workspace.duplicate_document_filenames == (
        "f_000001.hpd",
    )
    assert workspace.documents == (
        first,
        second,
    )
    assert workspace.orphan_documents == ()


def test_orphan_documents_are_preserved_in_supplied_order() -> None:
    catalog = _fixture_catalog()
    bound = _document("f_000001.hpd")
    orphan_one = _document("f_900001.hpd")
    orphan_two = _document("f_900002.hpd")

    workspace = bind_favorites_workspace(
        catalog,
        (
            orphan_one,
            bound,
            orphan_two,
        ),
    )

    assert len(workspace.bindings) == 1
    assert workspace.orphan_documents == (
        orphan_one,
        orphan_two,
    )


def test_filename_matching_is_exact_and_not_case_folded() -> None:
    catalog = _fixture_catalog()
    document = _document("F_000001.HPD")

    workspace = bind_favorites_workspace(
        catalog,
        (document,),
    )

    assert workspace.bindings == ()
    assert workspace.missing_entries == (
        catalog.entries[0],
    )
    assert workspace.orphan_documents == (
        document,
    )


def test_filename_matching_does_not_trim_whitespace() -> None:
    catalog = _fixture_catalog()
    document = _document(" f_000001.hpd ")

    workspace = bind_favorites_workspace(
        catalog,
        (document,),
    )

    assert workspace.bindings == ()
    assert workspace.missing_entries == (
        catalog.entries[0],
    )
    assert workspace.orphan_documents == (
        document,
    )


def test_every_catalog_entry_has_exactly_one_workspace_state() -> None:
    catalog = project_favorites_catalog(
        parse_favorites_file(
            b"F-List\tBound\tbound.hpd\r\n"
            b"F-List\tMissing\tmissing.hpd\r\n"
            b"F-List\tAmbiguous\tambiguous.hpd\r\n"
        )
    )

    bound = _document("bound.hpd")
    ambiguous_one = _document("ambiguous.hpd")
    ambiguous_two = _document("ambiguous.hpd")
    orphan = _document("orphan.hpd")

    workspace = bind_favorites_workspace(
        catalog,
        (
            bound,
            ambiguous_one,
            orphan,
            ambiguous_two,
        ),
    )

    entry_indexes = [
        *(
            binding.entry.source_index
            for binding in workspace.bindings
        ),
        *(
            entry.source_index
            for entry in workspace.missing_entries
        ),
        *(
            entry.source_index
            for entry in workspace.ambiguous_entries
        ),
    ]

    assert sorted(entry_indexes) == [
        entry.source_index
        for entry in catalog.entries
    ]
    assert len(entry_indexes) == len(
        set(entry_indexes)
    )

    assert workspace.duplicate_document_filenames == (
        "ambiguous.hpd",
    )
    assert workspace.orphan_documents == (
        orphan,
    )


def test_duplicate_diagnostics_preserve_first_filename_order() -> None:
    catalog = project_favorites_catalog(
        parse_favorites_file(
            b"F-List\tA1\ta.hpd\r\n"
            b"F-List\tB1\tb.hpd\r\n"
            b"F-List\tA2\ta.hpd\r\n"
            b"F-List\tC1\tc.hpd\r\n"
            b"F-List\tB2\tb.hpd\r\n"
        )
    )

    workspace = bind_favorites_workspace(
        catalog,
        (
            _document("z.hpd"),
            _document("y.hpd"),
            _document("z.hpd"),
            _document("y.hpd"),
        ),
    )

    assert workspace.duplicate_catalog_filenames == (
        "a.hpd",
        "b.hpd",
    )
    assert workspace.duplicate_document_filenames == (
        "z.hpd",
        "y.hpd",
    )


def test_binding_rejects_nonmatching_filenames() -> None:
    catalog = _fixture_catalog()
    document = _document("other.hpd")

    with pytest.raises(
        ValueError,
        match="requires exact filename equality",
    ):
        FavoritesWorkspaceBinding(
            entry=catalog.entries[0],
            document=document,
        )


def test_workspace_models_are_immutable() -> None:
    catalog = _fixture_catalog()
    document = _document("f_000001.hpd")

    workspace = bind_favorites_workspace(
        catalog,
        (document,),
    )
    binding = workspace.bindings[0]

    with pytest.raises(FrozenInstanceError):
        workspace.bindings = ()  # type: ignore[misc]

    with pytest.raises(FrozenInstanceError):
        binding.document = document  # type: ignore[misc]

    with pytest.raises(FrozenInstanceError):
        document.filename = "changed.hpd"  # type: ignore[misc]


def test_workspace_requires_catalog_and_immutable_document_tuple() -> None:
    catalog = _fixture_catalog()
    document = _document("f_000001.hpd")

    with pytest.raises(
        TypeError,
        match="catalog must be FavoritesCatalog",
    ):
        bind_favorites_workspace(  # type: ignore[arg-type]
            b"catalog",
            (),
        )

    with pytest.raises(
        TypeError,
        match="documents must be a tuple",
    ):
        bind_favorites_workspace(  # type: ignore[arg-type]
            catalog,
            [document],
        )
