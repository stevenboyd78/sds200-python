from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from sds200 import (
    FavoritesConventionalSystem,
    FavoritesHierarchy,
    FavoritesHierarchyError,
    FavoritesRecordReference,
    FavoritesTrunkSystem,
    parse_favorites_file,
    project_favorites_hierarchy,
)

_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "favorites"
    / "synthetic-favorites.hpd"
)


def _fixture_hierarchy() -> FavoritesHierarchy:
    source = parse_favorites_file(
        _FIXTURE.read_bytes()
    )
    return project_favorites_hierarchy(source)


def _all_references(
    hierarchy: FavoritesHierarchy,
) -> tuple[FavoritesRecordReference, ...]:
    references: list[FavoritesRecordReference] = [
        *hierarchy.metadata_records,
        *hierarchy.unclassified_records,
    ]

    for system in hierarchy.systems:
        references.append(system.source)
        references.extend(
            system.quick_key_status_records
        )
        references.extend(
            system.supplemental_records
        )

        if isinstance(
            system,
            FavoritesConventionalSystem,
        ):
            for department in system.departments:
                references.append(department.source)
                references.extend(
                    channel.source
                    for channel in department.channels
                )
        else:
            for site in system.sites:
                references.append(site.source)
                references.extend(site.frequencies)
                references.extend(site.band_plans)

            for department in system.departments:
                references.append(department.source)
                references.extend(
                    channel.source
                    for channel in department.channels
                )

    return tuple(references)


def test_projects_conventional_and_trunk_hierarchy() -> None:
    hierarchy = _fixture_hierarchy()

    assert len(hierarchy.systems) == 2

    conventional = hierarchy.systems[0]
    trunk = hierarchy.systems[1]

    assert isinstance(
        conventional,
        FavoritesConventionalSystem,
    )
    assert conventional.name == "Synthetic Conventional"
    assert len(
        conventional.quick_key_status_records
    ) == 1
    assert len(conventional.departments) == 1

    conventional_department = (
        conventional.departments[0]
    )

    assert (
        conventional_department.name
        == "Synthetic Department"
    )
    assert [
        channel.name
        for channel in conventional_department.channels
    ] == ["Synthetic Channel"]

    assert isinstance(trunk, FavoritesTrunkSystem)
    assert trunk.name == "Synthetic P25"
    assert len(trunk.quick_key_status_records) == 1

    assert len(trunk.sites) == 1
    site = trunk.sites[0]

    assert site.name == "Synthetic Site"
    assert [
        reference.record.field_count
        for reference in site.frequencies
    ] == [8, 9]
    assert [
        reference.record.field_count
        for reference in site.band_plans
    ] == [34, 50]

    assert len(trunk.departments) == 1
    trunk_department = trunk.departments[0]

    assert (
        trunk_department.name
        == "Synthetic Talkgroups"
    )
    assert [
        channel.name
        for channel in trunk_department.channels
    ] == [
        "Synthetic Dispatch",
        "Synthetic Talkgroup",
    ]
    assert [
        channel.source.record.field_count
        for channel in trunk_department.channels
    ] == [17, 18]

    assert [
        reference.record.command
        for reference in trunk.supplemental_records
    ] == [
        "UnitID",
        "UnitIds",
    ]

    assert [
        reference.record.command
        for reference in hierarchy.unclassified_records
    ] == ["FutureCommand"]


def test_projection_partitions_every_source_record_once() -> None:
    hierarchy = _fixture_hierarchy()

    references = _all_references(hierarchy)
    indexes = sorted(
        reference.source_index
        for reference in references
    )

    assert indexes == list(
        range(len(hierarchy.source.records))
    )
    assert len(indexes) == len(set(indexes))


def test_projection_does_not_change_lossless_source() -> None:
    data = _FIXTURE.read_bytes()
    source = parse_favorites_file(data)

    hierarchy = project_favorites_hierarchy(source)

    assert hierarchy.source is source
    assert hierarchy.source.to_bytes() == data


def test_names_preserve_exact_source_text() -> None:
    source = parse_favorites_file(
        b"Conventional\t\t\t  System Name  \r\n"
        b"C-Group\t\t\t Department \r\n"
        b"C-Freq\t\t\t  Channel  \r\n"
    )

    hierarchy = project_favorites_hierarchy(source)

    system = hierarchy.systems[0]

    assert isinstance(
        system,
        FavoritesConventionalSystem,
    )
    assert system.name == "  System Name  "
    assert (
        system.departments[0].name
        == " Department "
    )
    assert (
        system.departments[0].channels[0].name
        == "  Channel  "
    )


def test_unknown_record_remains_unclassified() -> None:
    source = parse_favorites_file(
        b"TargetModel\tBCDx36HP\r\n"
        b"FutureCommand\talpha\t\tomega\r\n"
        b"Conventional\t\t\tSystem\r\n"
    )

    hierarchy = project_favorites_hierarchy(source)

    assert [
        reference.record.command
        for reference in hierarchy.metadata_records
    ] == ["TargetModel"]

    assert [
        reference.record.command
        for reference in hierarchy.unclassified_records
    ] == ["FutureCommand"]

    assert hierarchy.source.to_bytes() == (
        source.to_bytes()
    )


@pytest.mark.parametrize(
    ("data", "command", "message"),
    (
        (
            b"Conventional\r\nC-Freq\r\n",
            "C-Freq",
            "requires a current Conventional C-Group",
        ),
        (
            b"Trunk\r\nT-Freq\r\n",
            "T-Freq",
            "requires a current Trunk Site",
        ),
        (
            b"Trunk\r\nTGID\r\n",
            "TGID",
            "requires a current Trunk T-Group",
        ),
        (
            b"Conventional\r\nT-Group\r\n",
            "T-Group",
            "requires a current Trunk system",
        ),
        (
            b"Trunk\r\nC-Group\r\n",
            "C-Group",
            "requires a current Conventional system",
        ),
        (
            b"Conventional\r\nSite\r\n",
            "Site",
            "requires a current Trunk system",
        ),
        (
            b"Trunk\r\nBandPlan_P25\r\n",
            "BandPlan_P25",
            "requires a current Trunk Site",
        ),
    ),
)
def test_known_structural_records_require_valid_context(
    data: bytes,
    command: str,
    message: str,
) -> None:
    source = parse_favorites_file(data)

    with pytest.raises(
        FavoritesHierarchyError,
        match=message,
    ) as captured:
        project_favorites_hierarchy(source)

    assert captured.value.source_index == 1
    assert captured.value.command == command


def test_pre_system_supplemental_record_is_preserved_unclassified() -> None:
    source = parse_favorites_file(
        b"UnitID\r\n"
        b"Trunk\t\t\tSystem\r\n"
    )

    hierarchy = project_favorites_hierarchy(source)

    assert [
        reference.record.command
        for reference in hierarchy.unclassified_records
    ] == ["UnitID"]


def test_multiple_dqks_status_records_are_preserved() -> None:
    source = parse_favorites_file(
        b"Conventional\t\t\tSystem\r\n"
        b"DQKs_Status\t\tOn\r\n"
        b"DQKs_Status\t\tOff\r\n"
    )

    hierarchy = project_favorites_hierarchy(source)
    system = hierarchy.systems[0]

    assert isinstance(
        system,
        FavoritesConventionalSystem,
    )

    assert [
        reference.record.fields
        for reference in system.quick_key_status_records
    ] == [
        ("", "On"),
        ("", "Off"),
    ]


def test_hierarchy_models_are_immutable() -> None:
    hierarchy = _fixture_hierarchy()

    with pytest.raises(FrozenInstanceError):
        hierarchy.systems = ()  # type: ignore[misc]

    system = hierarchy.systems[0]

    with pytest.raises(FrozenInstanceError):
        system.source = system.source  # type: ignore[misc]


def test_projection_requires_lossless_source_file() -> None:
    with pytest.raises(
        TypeError,
        match="Favorites hierarchy source must be",
    ):
        project_favorites_hierarchy(  # type: ignore[arg-type]
            b"Conventional"
        )
