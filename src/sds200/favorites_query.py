"""Renderer-neutral search and filtering over Favorites navigation."""

from __future__ import annotations

from dataclasses import dataclass

from .favorites_navigation import (
    FavoritesNavigation,
    FavoritesNavigationKind,
    FavoritesNavigationNode,
    FavoritesNavigationPath,
)


@dataclass(frozen=True, slots=True)
class FavoritesNavigationQuery:
    """Describe pure predicates over immutable Favorites navigation."""

    text: str | None = None
    kinds: frozenset[
        FavoritesNavigationKind
    ] | None = None
    subtree: FavoritesNavigationPath | None = None

    def __post_init__(self) -> None:
        if (
            self.text is not None
            and not isinstance(
                self.text,
                str,
            )
        ):
            raise TypeError(
                "Favorites navigation query text must be "
                "str or None."
            )

        if self.text == "":
            raise ValueError(
                "Favorites navigation query text must not "
                "be empty."
            )

        if (
            self.kinds is not None
            and type(self.kinds) is not frozenset
        ):
            raise TypeError(
                "Favorites navigation query kinds must be "
                "frozenset or None."
            )

        if (
            self.kinds is not None
            and any(
                not isinstance(
                    kind,
                    FavoritesNavigationKind,
                )
                for kind in self.kinds
            )
        ):
            raise TypeError(
                "Favorites navigation query kinds must "
                "contain FavoritesNavigationKind values."
            )

        if (
            self.subtree is not None
            and not isinstance(
                self.subtree,
                FavoritesNavigationPath,
            )
        ):
            raise TypeError(
                "Favorites navigation query subtree must be "
                "FavoritesNavigationPath or None."
            )


def _append_preorder(
    node: FavoritesNavigationNode,
    nodes: list[FavoritesNavigationNode],
) -> None:
    nodes.append(node)

    for child in node.children:
        _append_preorder(
            child,
            nodes,
        )


def _preorder(
    roots: tuple[
        FavoritesNavigationNode,
        ...,
    ],
) -> tuple[
    FavoritesNavigationNode,
    ...,
]:
    nodes: list[
        FavoritesNavigationNode
    ] = []

    for root in roots:
        _append_preorder(
            root,
            nodes,
        )

    return tuple(nodes)


def _resolve_subtree(
    navigation: FavoritesNavigation,
    path: FavoritesNavigationPath,
) -> FavoritesNavigationNode:
    for node in _preorder(
        navigation.roots
    ):
        if node.path == path:
            return node

    raise ValueError(
        "Favorites navigation query subtree path is not "
        f"present in navigation: {path.indexes!r}."
    )


def query_favorites_navigation(
    navigation: FavoritesNavigation,
    query: FavoritesNavigationQuery,
) -> tuple[
    FavoritesNavigationNode,
    ...,
]:
    """Return original matching nodes in authoritative navigation preorder."""

    if not isinstance(
        navigation,
        FavoritesNavigation,
    ):
        raise TypeError(
            "Favorites navigation query requires "
            "FavoritesNavigation."
        )

    if not isinstance(
        query,
        FavoritesNavigationQuery,
    ):
        raise TypeError(
            "Favorites navigation query requires "
            "FavoritesNavigationQuery."
        )

    if query.subtree is None:
        candidates = _preorder(
            navigation.roots
        )
    else:
        subtree_root = _resolve_subtree(
            navigation,
            query.subtree,
        )

        candidates = _preorder(
            (subtree_root,)
        )

    folded_text = (
        query.text.casefold()
        if query.text is not None
        else None
    )

    matches: list[
        FavoritesNavigationNode
    ] = []

    for node in candidates:
        if (
            folded_text is not None
            and (
                node.name is None
                or folded_text
                not in node.name.casefold()
            )
        ):
            continue

        if (
            query.kinds is not None
            and node.kind not in query.kinds
        ):
            continue

        matches.append(node)

    return tuple(matches)


__all__ = [
    "FavoritesNavigationQuery",
    "query_favorites_navigation",
]
