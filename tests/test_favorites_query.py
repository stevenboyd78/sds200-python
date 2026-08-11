from __future__ import annotations

from dataclasses import (
    FrozenInstanceError,
    replace,
)
from pathlib import Path

import pytest

from sds200 import (
    FavoritesConventionalSystem,
    FavoritesNavigation,
    FavoritesNavigationKind,
    FavoritesNavigationNode,
    FavoritesNavigationPath,
    FavoritesNavigationQuery,
    FavoritesRecordReference,
    FavoritesSourceRecord,
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
    project_favorites_navigation,
    project_favorites_storage_snapshot,
    query_favorites_navigation,
)

_FIXTURE_ROOT = (
    Path(__file__).parent / "fixtures" / "favorites"
)


def _navigation() -> FavoritesNavigation:
    workspace = project_favorites_storage_snapshot(
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

    return project_favorites_navigation(
        workspace
    )


def _preorder(
    navigation: FavoritesNavigation,
) -> tuple[
    FavoritesNavigationNode,
    ...,
]:
    nodes: list[
        FavoritesNavigationNode
    ] = []

    def visit(
        node: FavoritesNavigationNode,
    ) -> None:
        nodes.append(node)

        for child in node.children:
            visit(child)

    for root in navigation.roots:
        visit(root)

    return tuple(nodes)


def test_query_is_frozen_slot_backed() -> None:
    query = FavoritesNavigationQuery()

    parameters = query.__dataclass_params__

    assert parameters.frozen is True
    assert "__slots__" in FavoritesNavigationQuery.__dict__
    assert "__dict__" not in FavoritesNavigationQuery.__dict__

    with pytest.raises(
        FrozenInstanceError,
    ):
        query.text = "changed"  # type: ignore[misc]


def test_no_predicates_returns_complete_preorder() -> None:
    navigation = _navigation()

    expected = _preorder(
        navigation
    )

    result = query_favorites_navigation(
        navigation,
        FavoritesNavigationQuery(),
    )

    assert result == expected

    assert tuple(
        node.path.indexes
        for node in result
    ) == (
        (2,),
        (2, 2),
        (2, 2, 4),
        (2, 2, 4, 5),
        (2, 6),
        (2, 6, 8),
        (2, 6, 13),
        (2, 6, 13, 14),
        (2, 6, 13, 15),
    )

    for result_node, original_node in zip(
        result,
        expected,
        strict=True,
    ):
        assert result_node is original_node


def test_text_search_is_case_insensitive_substring() -> None:
    navigation = _navigation()

    result = query_favorites_navigation(
        navigation,
        FavoritesNavigationQuery(
            text="DISPATCH",
        ),
    )

    assert tuple(
        node.name
        for node in result
    ) == (
        "Synthetic Dispatch",
    )


def test_text_search_does_not_mutate_exact_names() -> None:
    navigation = _navigation()
    nodes = _preorder(
        navigation
    )

    before = tuple(
        (
            node.name,
            node.source,
        )
        for node in nodes
    )

    result = query_favorites_navigation(
        navigation,
        FavoritesNavigationQuery(
            text="sYnThEtIc",
        ),
    )

    after = tuple(
        (
            node.name,
            node.source,
        )
        for node in nodes
    )

    assert len(result) == len(nodes)
    assert before == after

    assert result[0].name == "Synthetic Favorites"
    assert result[-1].name == "Synthetic Talkgroup"


@pytest.mark.parametrize(
    "text",
    [
        "155000000",
        "P25Standard",
        "1001",
    ],
)
def test_text_search_does_not_search_raw_fields(
    text: str,
) -> None:
    assert query_favorites_navigation(
        _navigation(),
        FavoritesNavigationQuery(
            text=text,
        ),
    ) == ()


def test_explicit_empty_text_is_rejected() -> None:
    with pytest.raises(
        ValueError,
        match="must not be empty",
    ):
        FavoritesNavigationQuery(
            text="",
        )


def test_whitespace_text_is_preserved_as_literal_search() -> None:
    navigation = _navigation()

    query = FavoritesNavigationQuery(
        text=" ",
    )

    assert query.text == " "

    result = query_favorites_navigation(
        navigation,
        query,
    )

    assert len(result) == 9


def test_query_text_requires_string_or_none() -> None:
    with pytest.raises(
        TypeError,
        match="text must be str or None",
    ):
        FavoritesNavigationQuery(  # type: ignore[arg-type]
            text=123,
        )


def test_kind_filter_preserves_navigation_order() -> None:
    navigation = _navigation()

    result = query_favorites_navigation(
        navigation,
        FavoritesNavigationQuery(
            kinds=frozenset(
                {
                    FavoritesNavigationKind.CONVENTIONAL_CHANNEL,
                    FavoritesNavigationKind.TRUNK_CHANNEL,
                }
            ),
        ),
    )

    assert tuple(
        node.kind
        for node in result
    ) == (
        FavoritesNavigationKind.CONVENTIONAL_CHANNEL,
        FavoritesNavigationKind.TRUNK_CHANNEL,
        FavoritesNavigationKind.TRUNK_CHANNEL,
    )

    assert tuple(
        node.name
        for node in result
    ) == (
        "Synthetic Channel",
        "Synthetic Dispatch",
        "Synthetic Talkgroup",
    )


def test_empty_kind_filter_matches_no_nodes() -> None:
    assert query_favorites_navigation(
        _navigation(),
        FavoritesNavigationQuery(
            kinds=frozenset(),
        ),
    ) == ()


def test_none_kind_filter_applies_no_kind_predicate() -> None:
    navigation = _navigation()

    assert query_favorites_navigation(
        navigation,
        FavoritesNavigationQuery(
            kinds=None,
        ),
    ) == _preorder(
        navigation
    )


def test_query_kinds_requires_frozenset_or_none() -> None:
    with pytest.raises(
        TypeError,
        match="kinds must be frozenset or None",
    ):
        FavoritesNavigationQuery(  # type: ignore[arg-type]
            kinds={
                FavoritesNavigationKind.TRUNK_CHANNEL,
            },
        )


def test_query_kinds_requires_navigation_kind_values() -> None:
    with pytest.raises(
        TypeError,
        match="FavoritesNavigationKind values",
    ):
        FavoritesNavigationQuery(  # type: ignore[arg-type]
            kinds=frozenset(
                {
                    "trunk_channel",
                }
            ),
        )


def test_subtree_is_inclusive_and_preordered() -> None:
    navigation = _navigation()

    result = query_favorites_navigation(
        navigation,
        FavoritesNavigationQuery(
            subtree=FavoritesNavigationPath(
                (
                    2,
                    6,
                    13,
                )
            ),
        ),
    )

    assert tuple(
        node.path.indexes
        for node in result
    ) == (
        (2, 6, 13),
        (2, 6, 13, 14),
        (2, 6, 13, 15),
    )


def test_subtree_root_still_must_satisfy_other_predicates() -> None:
    result = query_favorites_navigation(
        _navigation(),
        FavoritesNavigationQuery(
            kinds=frozenset(
                {
                    FavoritesNavigationKind.TRUNK_CHANNEL,
                }
            ),
            subtree=FavoritesNavigationPath(
                (
                    2,
                    6,
                    13,
                )
            ),
        ),
    )

    assert tuple(
        node.name
        for node in result
    ) == (
        "Synthetic Dispatch",
        "Synthetic Talkgroup",
    )


def test_text_kind_and_subtree_combine_with_and() -> None:
    result = query_favorites_navigation(
        _navigation(),
        FavoritesNavigationQuery(
            text="DISPATCH",
            kinds=frozenset(
                {
                    FavoritesNavigationKind.TRUNK_CHANNEL,
                }
            ),
            subtree=FavoritesNavigationPath(
                (
                    2,
                    6,
                    13,
                )
            ),
        ),
    )

    assert tuple(
        node.name
        for node in result
    ) == (
        "Synthetic Dispatch",
    )


def test_valid_subtree_can_have_no_matching_results() -> None:
    result = query_favorites_navigation(
        _navigation(),
        FavoritesNavigationQuery(
            text="not-present",
            subtree=FavoritesNavigationPath(
                (
                    2,
                    6,
                    13,
                )
            ),
        ),
    )

    assert result == ()


def test_missing_subtree_path_is_rejected() -> None:
    missing = FavoritesNavigationPath(
        (
            2,
            6,
            999,
        )
    )

    with pytest.raises(
        ValueError,
        match="not present in navigation",
    ):
        query_favorites_navigation(
            _navigation(),
            FavoritesNavigationQuery(
                subtree=missing,
            ),
        )


def test_query_subtree_requires_navigation_path_or_none() -> None:
    with pytest.raises(
        TypeError,
        match="subtree must be FavoritesNavigationPath or None",
    ):
        FavoritesNavigationQuery(  # type: ignore[arg-type]
            subtree=(2, 6),
        )


def test_duplicate_display_names_remain_distinct() -> None:
    hpd = (
        _FIXTURE_ROOT
        / "synthetic-favorites.hpd"
    ).read_bytes()

    workspace = project_favorites_storage_snapshot(
        FavoritesStorageSnapshot(
            catalog_bytes=(
                b"F-List\tSame List\tfirst.hpd\r\n"
                b"F-List\tSame List\tsecond.hpd\r\n"
            ),
            documents=(
                FavoritesStorageDocument(
                    filename="second.hpd",
                    content=hpd,
                ),
                FavoritesStorageDocument(
                    filename="first.hpd",
                    content=hpd,
                ),
            ),
        )
    )

    navigation = project_favorites_navigation(
        workspace
    )

    result = query_favorites_navigation(
        navigation,
        FavoritesNavigationQuery(
            text="same list",
        ),
    )

    assert tuple(
        node.name
        for node in result
    ) == (
        "Same List",
        "Same List",
    )

    assert tuple(
        node.path.indexes
        for node in result
    ) == (
        (0,),
        (1,),
    )

    assert result[0] is navigation.roots[0]
    assert result[1] is navigation.roots[1]


def test_empty_names_remain_candidates_without_text_search() -> None:
    workspace = project_favorites_storage_snapshot(
        FavoritesStorageSnapshot(
            catalog_bytes=(
                b"F-List\tList\tf.hpd\r\n"
            ),
            documents=(
                FavoritesStorageDocument(
                    filename="f.hpd",
                    content=(
                        b"Conventional\t\t\t\r\n"
                        b"C-Group\t\t\t\r\n"
                        b"C-Freq\t\t\t\r\n"
                    ),
                ),
            ),
        )
    )

    navigation = project_favorites_navigation(
        workspace
    )

    result = query_favorites_navigation(
        navigation,
        FavoritesNavigationQuery(),
    )

    assert tuple(
        node.name
        for node in result
    ) == (
        "List",
        "",
        "",
        "",
    )


def test_absent_name_does_not_match_text_search() -> None:
    navigation = _navigation()
    root = navigation.roots[0]

    source = FavoritesConventionalSystem(
        source=FavoritesRecordReference(
            source_index=3,
            record=FavoritesSourceRecord(
                content=b"Conventional",
                line_ending=b"\r\n",
            ),
        ),
        quick_key_status_records=(),
        departments=(),
        supplemental_records=(),
    )

    unnamed = FavoritesNavigationNode(
        path=root.path.child(3),
        kind=FavoritesNavigationKind.CONVENTIONAL_SYSTEM,
        name=None,
        source=source,
        children=(),
    )

    modified_root = replace(
        root,
        children=(unnamed,),
    )

    modified_navigation = FavoritesNavigation(
        workspace=navigation.workspace,
        roots=(modified_root,),
    )

    assert query_favorites_navigation(
        modified_navigation,
        FavoritesNavigationQuery(),
    ) == (
        modified_root,
        unnamed,
    )

    assert query_favorites_navigation(
        modified_navigation,
        FavoritesNavigationQuery(
            text="synthetic",
        ),
    ) == (
        modified_root,
    )


def test_results_retain_original_node_identity() -> None:
    navigation = _navigation()
    originals = _preorder(
        navigation
    )

    result = query_favorites_navigation(
        navigation,
        FavoritesNavigationQuery(
            text="synthetic",
        ),
    )

    assert len(result) == len(originals)

    for result_node, original_node in zip(
        result,
        originals,
        strict=True,
    ):
        assert result_node is original_node


def test_query_requires_navigation() -> None:
    with pytest.raises(
        TypeError,
        match="requires FavoritesNavigation",
    ):
        query_favorites_navigation(  # type: ignore[arg-type]
            object(),
            FavoritesNavigationQuery(),
        )


def test_query_requires_query_object() -> None:
    with pytest.raises(
        TypeError,
        match="requires FavoritesNavigationQuery",
    ):
        query_favorites_navigation(  # type: ignore[arg-type]
            _navigation(),
            object(),
        )
