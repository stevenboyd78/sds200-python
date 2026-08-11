from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from sds200 import (
    FavoritesCatalog,
    FavoritesCatalogEntry,
    FavoritesCatalogError,
    FavoritesSourceRecord,
    parse_favorites_file,
    project_favorites_catalog,
)

_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "favorites"
    / "synthetic-f_list.cfg"
)


def _fixture_catalog() -> FavoritesCatalog:
    source = parse_favorites_file(
        _FIXTURE.read_bytes()
    )
    return project_favorites_catalog(source)


def test_projects_synthetic_f_list_catalog() -> None:
    catalog = _fixture_catalog()

    assert catalog.metadata_indexes == (0, 1)
    assert catalog.unclassified_indexes == (3,)
    assert len(catalog.entries) == 1

    entry = catalog.entries[0]

    assert entry.source_index == 2
    assert entry.name == "Synthetic Favorites"
    assert entry.filename == "f_000001.hpd"
    assert entry.source.field_count == 117


def test_catalog_retains_complete_lossless_source() -> None:
    data = _FIXTURE.read_bytes()
    source = parse_favorites_file(data)

    catalog = project_favorites_catalog(source)

    assert catalog.source is source
    assert catalog.source.to_bytes() == data
    assert catalog.entries[0].source is source.records[2]


def test_catalog_partitions_every_source_record_once() -> None:
    catalog = _fixture_catalog()

    indexes = [
        *catalog.metadata_indexes,
        *(
            entry.source_index
            for entry in catalog.entries
        ),
        *catalog.unclassified_indexes,
    ]

    assert sorted(indexes) == list(
        range(len(catalog.source.records))
    )
    assert len(indexes) == len(set(indexes))


def test_catalog_preserves_unknown_records() -> None:
    catalog = _fixture_catalog()

    assert [
        catalog.source.records[index].command
        for index in catalog.unclassified_indexes
    ] == ["FutureListSetting"]


def test_catalog_name_and_filename_are_not_normalized() -> None:
    source = parse_favorites_file(
        b"F-List\t  Display Name  \t f_000001.hpd \tOff\r\n"
    )

    catalog = project_favorites_catalog(source)
    entry = catalog.entries[0]

    assert entry.name == "  Display Name  "
    assert entry.filename == " f_000001.hpd "
    assert catalog.source.to_bytes() == source.to_bytes()


def test_catalog_preserves_additional_positions() -> None:
    source = parse_favorites_file(
        b"F-List\tName\tf_000001.hpd\tOff\tOn"
        b"\tFuturePosition\t\r\n"
    )

    catalog = project_favorites_catalog(source)
    entry = catalog.entries[0]

    assert entry.source.fields == (
        "Name",
        "f_000001.hpd",
        "Off",
        "On",
        "FuturePosition",
        "",
    )
    assert entry.source.field_count == 7
    assert catalog.source.to_bytes() == source.to_bytes()


def test_catalog_preserves_entry_order_and_duplicate_filenames() -> None:
    source = parse_favorites_file(
        b"F-List\tFirst\tf_000001.hpd\r\n"
        b"F-List\tSecond\tf_000001.hpd\r\n"
    )

    catalog = project_favorites_catalog(source)

    assert [
        entry.name
        for entry in catalog.entries
    ] == [
        "First",
        "Second",
    ]
    assert [
        entry.filename
        for entry in catalog.entries
    ] == [
        "f_000001.hpd",
        "f_000001.hpd",
    ]


@pytest.mark.parametrize(
    "data",
    (
        b"F-List\r\n",
        b"F-List\tOnly Name\r\n",
    ),
)
def test_known_f_list_requires_name_and_filename(
    data: bytes,
) -> None:
    source = parse_favorites_file(data)

    with pytest.raises(
        FavoritesCatalogError,
        match="requires UserName and Filename fields",
    ) as captured:
        project_favorites_catalog(source)

    assert captured.value.source_index == 0
    assert captured.value.command == "F-List"


def test_catalog_entry_rejects_non_f_list_source() -> None:
    record = FavoritesSourceRecord(
        content=b"Future\tName\tf_000001.hpd",
        line_ending=b"\r\n",
    )

    with pytest.raises(
        ValueError,
        match="source command must be F-List",
    ):
        FavoritesCatalogEntry(
            source_index=0,
            source=record,
        )


def test_catalog_models_are_immutable() -> None:
    catalog = _fixture_catalog()
    entry = catalog.entries[0]

    with pytest.raises(FrozenInstanceError):
        catalog.entries = ()  # type: ignore[misc]

    with pytest.raises(FrozenInstanceError):
        entry.source_index = 5  # type: ignore[misc]


def test_catalog_projection_requires_lossless_source() -> None:
    with pytest.raises(
        TypeError,
        match="Favorites catalog source must be",
    ):
        project_favorites_catalog(  # type: ignore[arg-type]
            b"F-List"
        )
