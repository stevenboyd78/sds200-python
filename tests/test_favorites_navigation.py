from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from sds200 import (
    FavoritesCatalogEntry,
    FavoritesNavigation,
    FavoritesNavigationKind,
    FavoritesNavigationNode,
    FavoritesNavigationPath,
    FavoritesRecordReference,
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
    FavoritesTrunkDepartment,
    FavoritesTrunkSite,
    FavoritesTrunkSystem,
    bind_favorites_workspace,
    project_favorites_navigation,
    project_favorites_storage_snapshot,
)

_FIXTURE_ROOT = (
    Path(__file__).parent / "fixtures" / "favorites"
)


def _workspace():
    return project_favorites_storage_snapshot(
        FavoritesStorageSnapshot(
            catalog_bytes=(
                _FIXTURE_ROOT
                / "synthetic-f_list.cfg"
            ).read_bytes(),
            documents=(
                FavoritesStorageDocument(
                    filename="f_000001.hpd",
                    content=(
                        _FIXTURE_ROOT
                        / "synthetic-favorites.hpd"
                    ).read_bytes(),
                ),
            ),
        )
    )


def test_projects_complete_navigation_tree() -> None:
    workspace = _workspace()

    navigation = project_favorites_navigation(
        workspace
    )

    assert navigation.workspace is workspace
    assert len(navigation.roots) == 1

    favorite = navigation.roots[0]

    assert favorite.kind is (
        FavoritesNavigationKind.FAVORITES_LIST
    )
    assert favorite.name == "Synthetic Favorites"
    assert favorite.path == FavoritesNavigationPath(
        (2,)
    )
    assert favorite.parent_path is None
    assert favorite.source is workspace.bindings[0].entry

    conventional, trunk = favorite.children

    assert conventional.kind is (
        FavoritesNavigationKind.CONVENTIONAL_SYSTEM
    )
    assert conventional.name == "Synthetic Conventional"
    assert conventional.path.indexes == (2, 2)
    assert conventional.parent_path == favorite.path

    conventional_department = (
        conventional.children[0]
    )

    assert conventional_department.kind is (
        FavoritesNavigationKind.CONVENTIONAL_DEPARTMENT
    )
    assert (
        conventional_department.path.indexes
        == (2, 2, 4)
    )

    conventional_channel = (
        conventional_department.children[0]
    )

    assert conventional_channel.kind is (
        FavoritesNavigationKind.CONVENTIONAL_CHANNEL
    )
    assert (
        conventional_channel.path.indexes
        == (2, 2, 4, 5)
    )

    assert trunk.kind is (
        FavoritesNavigationKind.TRUNK_SYSTEM
    )
    assert trunk.name == "Synthetic P25"
    assert trunk.path.indexes == (2, 6)

    site, department = trunk.children

    assert site.kind is (
        FavoritesNavigationKind.TRUNK_SITE
    )
    assert site.path.indexes == (2, 6, 8)
    assert site.children == ()

    assert department.kind is (
        FavoritesNavigationKind.TRUNK_DEPARTMENT
    )
    assert department.path.indexes == (
        2,
        6,
        13,
    )

    assert tuple(
        child.kind
        for child in department.children
    ) == (
        FavoritesNavigationKind.TRUNK_CHANNEL,
        FavoritesNavigationKind.TRUNK_CHANNEL,
    )

    assert tuple(
        child.path.indexes
        for child in department.children
    ) == (
        (2, 6, 13, 14),
        (2, 6, 13, 15),
    )


def test_projection_preserves_exact_source_objects() -> None:
    workspace = _workspace()

    navigation = project_favorites_navigation(
        workspace
    )

    binding = workspace.bindings[0]
    favorite = navigation.roots[0]

    assert favorite.source is binding.entry

    for source_system, system_node in zip(
        binding.hierarchy.systems,
        favorite.children,
        strict=True,
    ):
        assert system_node.source is source_system

        if isinstance(
            source_system,
            FavoritesTrunkSystem,
        ):
            expected_children = sorted(
                (
                    *source_system.sites,
                    *source_system.departments,
                ),
                key=lambda child: (
                    child.source.source_index
                ),
            )
        else:
            expected_children = list(
                source_system.departments
            )

        for source_child, child_node in zip(
            expected_children,
            system_node.children,
            strict=True,
        ):
            assert child_node.source is source_child

            for source_channel, channel_node in zip(
                getattr(
                    source_child,
                    "channels",
                    (),
                ),
                child_node.children,
                strict=True,
            ):
                assert channel_node.source is source_channel


def test_projection_preserves_exact_names() -> None:
    catalog = (
        b"F-List\t Favorites List \t"
        b" spaced.hpd \r\n"
    )

    hpd = (
        b"Conventional\t\t\t System \r\n"
        b"C-Group\t\t\t Department \r\n"
        b"C-Freq\t\t\t Channel \r\n"
    )

    workspace = project_favorites_storage_snapshot(
        FavoritesStorageSnapshot(
            catalog_bytes=catalog,
            documents=(
                FavoritesStorageDocument(
                    filename=" spaced.hpd ",
                    content=hpd,
                ),
            ),
        )
    )

    navigation = project_favorites_navigation(
        workspace
    )

    favorite = navigation.roots[0]
    system = favorite.children[0]
    department = system.children[0]
    channel = department.children[0]

    assert favorite.name == " Favorites List "
    assert system.name == " System "
    assert department.name == " Department "
    assert channel.name == " Channel "


def test_navigation_paths_are_unique_and_parent_addressable() -> None:
    navigation = project_favorites_navigation(
        _workspace()
    )

    paths: list[
        FavoritesNavigationPath
    ] = []

    def collect(
        node: FavoritesNavigationNode,
    ) -> None:
        paths.append(node.path)

        for child in node.children:
            assert child.parent_path == node.path
            collect(child)

    for root in navigation.roots:
        assert root.parent_path is None
        collect(root)

    assert len(paths) == len(set(paths))

    assert {
        path.indexes
        for path in paths
    } == {
        (2,),
        (2, 2),
        (2, 2, 4),
        (2, 2, 4, 5),
        (2, 6),
        (2, 6, 8),
        (2, 6, 13),
        (2, 6, 13, 14),
        (2, 6, 13, 15),
    }


def test_unresolved_workspace_entries_do_not_become_roots() -> None:
    catalog = (
        _FIXTURE_ROOT
        / "synthetic-f_list.cfg"
    ).read_bytes()

    hpd = (
        _FIXTURE_ROOT
        / "synthetic-favorites.hpd"
    ).read_bytes()

    missing = project_favorites_storage_snapshot(
        FavoritesStorageSnapshot(
            catalog_bytes=catalog,
            documents=(),
        )
    )

    missing_navigation = (
        project_favorites_navigation(
            missing
        )
    )

    assert missing_navigation.roots == ()
    assert len(missing.missing_entries) == 1

    ambiguous = project_favorites_storage_snapshot(
        FavoritesStorageSnapshot(
            catalog_bytes=catalog,
            documents=(
                FavoritesStorageDocument(
                    filename="f_000001.hpd",
                    content=hpd,
                ),
                FavoritesStorageDocument(
                    filename="f_000001.hpd",
                    content=hpd,
                ),
            ),
        )
    )

    ambiguous_navigation = (
        project_favorites_navigation(
            ambiguous
        )
    )

    assert ambiguous_navigation.roots == ()
    assert len(
        ambiguous.ambiguous_entries
    ) == 1


def test_trunk_sites_and_departments_follow_source_order() -> None:
    workspace = _workspace()
    binding = workspace.bindings[0]

    original_trunk = next(
        system
        for system in binding.hierarchy.systems
        if isinstance(
            system,
            FavoritesTrunkSystem,
        )
    )

    original_site = original_trunk.sites[0]
    original_department = (
        original_trunk.departments[0]
    )

    early_department = FavoritesTrunkDepartment(
        source=FavoritesRecordReference(
            source_index=8,
            record=original_department.source.record,
        ),
        channels=(),
    )

    late_site = FavoritesTrunkSite(
        source=FavoritesRecordReference(
            source_index=13,
            record=original_site.source.record,
        ),
        frequencies=(),
        band_plans=(),
    )

    reordered_trunk = replace(
        original_trunk,
        sites=(late_site,),
        departments=(early_department,),
    )

    hierarchy = replace(
        binding.hierarchy,
        systems=(reordered_trunk,),
    )

    document = replace(
        binding.document,
        hierarchy=hierarchy,
    )

    modified_workspace = bind_favorites_workspace(
        workspace.catalog,
        (document,),
    )

    navigation = project_favorites_navigation(
        modified_workspace
    )

    trunk = navigation.roots[0].children[0]

    assert tuple(
        child.kind
        for child in trunk.children
    ) == (
        FavoritesNavigationKind.TRUNK_DEPARTMENT,
        FavoritesNavigationKind.TRUNK_SITE,
    )

    assert tuple(
        child.source_index
        for child in trunk.children
    ) == (
        8,
        13,
    )


def test_site_remains_navigation_leaf() -> None:
    navigation = project_favorites_navigation(
        _workspace()
    )

    site = next(
        child
        for system in navigation.roots[0].children
        for child in system.children
        if child.kind
        is FavoritesNavigationKind.TRUNK_SITE
    )

    assert site.children == ()

    source = site.source

    assert isinstance(
        source,
        FavoritesTrunkSite,
    )
    assert len(source.frequencies) == 2
    assert len(source.band_plans) == 2


def test_all_eight_navigation_kinds_are_projected() -> None:
    navigation = project_favorites_navigation(
        _workspace()
    )

    kinds: set[
        FavoritesNavigationKind
    ] = set()

    def collect(
        node: FavoritesNavigationNode,
    ) -> None:
        kinds.add(node.kind)

        for child in node.children:
            collect(child)

    for root in navigation.roots:
        collect(root)

    assert kinds == set(
        FavoritesNavigationKind
    )


def test_path_parent_and_child_are_derived() -> None:
    root = FavoritesNavigationPath(
        (3,)
    )

    assert root.parent is None

    system = root.child(7)

    assert system.indexes == (3, 7)
    assert system.parent == root

    channel = system.child(11)

    assert channel.indexes == (
        3,
        7,
        11,
    )
    assert channel.parent == system


@pytest.mark.parametrize(
    "indexes",
    [
        (),
        (-1,),
        (0, -1),
        (True,),
    ],
)
def test_path_rejects_invalid_indexes(
    indexes: tuple[int, ...],
) -> None:
    with pytest.raises(
        ValueError,
    ):
        FavoritesNavigationPath(
            indexes
        )


def test_path_requires_tuple() -> None:
    with pytest.raises(
        TypeError,
        match="indexes must be a tuple",
    ):
        FavoritesNavigationPath(  # type: ignore[arg-type]
            [1]
        )


def test_node_rejects_kind_source_mismatch() -> None:
    entry = _workspace().bindings[0].entry

    with pytest.raises(
        TypeError,
        match="does not match kind",
    ):
        FavoritesNavigationNode(
            path=FavoritesNavigationPath(
                (
                    entry.source_index,
                )
            ),
            kind=FavoritesNavigationKind.TRUNK_SYSTEM,
            name=entry.name,
            source=entry,
            children=(),
        )


def test_node_rejects_normalized_name() -> None:
    entry = _workspace().bindings[0].entry

    with pytest.raises(
        ValueError,
        match="exactly match",
    ):
        FavoritesNavigationNode(
            path=FavoritesNavigationPath(
                (
                    entry.source_index,
                )
            ),
            kind=FavoritesNavigationKind.FAVORITES_LIST,
            name=entry.name.lower(),
            source=entry,
            children=(),
        )


def test_node_rejects_wrong_source_index_path() -> None:
    entry = _workspace().bindings[0].entry

    with pytest.raises(
        ValueError,
        match="end at its source index",
    ):
        FavoritesNavigationNode(
            path=FavoritesNavigationPath(
                (
                    entry.source_index + 1,
                )
            ),
            kind=FavoritesNavigationKind.FAVORITES_LIST,
            name=entry.name,
            source=entry,
            children=(),
        )


def test_node_rejects_non_direct_child() -> None:
    workspace = _workspace()
    entry = workspace.bindings[0].entry

    child_source = (
        workspace.bindings[0]
        .hierarchy.systems[0]
    )

    child = FavoritesNavigationNode(
        path=FavoritesNavigationPath(
            (
                entry.source_index,
                99,
                child_source.source.source_index,
            )
        ),
        kind=FavoritesNavigationKind.CONVENTIONAL_SYSTEM,
        name=child_source.name,
        source=child_source,
        children=(),
    )

    with pytest.raises(
        ValueError,
        match="direct descendants",
    ):
        FavoritesNavigationNode(
            path=FavoritesNavigationPath(
                (
                    entry.source_index,
                )
            ),
            kind=FavoritesNavigationKind.FAVORITES_LIST,
            name=entry.name,
            source=entry,
            children=(child,),
        )


def test_navigation_requires_workspace() -> None:
    with pytest.raises(
        TypeError,
        match="workspace must be FavoritesWorkspace",
    ):
        FavoritesNavigation(  # type: ignore[arg-type]
            workspace=object(),
            roots=(),
        )


def test_projection_requires_workspace() -> None:
    with pytest.raises(
        TypeError,
        match="requires FavoritesWorkspace",
    ):
        project_favorites_navigation(  # type: ignore[arg-type]
            object()
        )


def test_navigation_roots_must_follow_workspace_bindings() -> None:
    workspace = _workspace()

    with pytest.raises(
        ValueError,
        match="exactly follow",
    ):
        FavoritesNavigation(
            workspace=workspace,
            roots=(),
        )


def test_root_source_is_catalog_entry() -> None:
    navigation = project_favorites_navigation(
        _workspace()
    )

    source = navigation.roots[0].source

    assert isinstance(
        source,
        FavoritesCatalogEntry,
    )
