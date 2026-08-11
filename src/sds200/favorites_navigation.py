"""Renderer-neutral navigation over a resolved Favorites workspace."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .favorites_catalog import FavoritesCatalogEntry
from .favorites_hierarchy import (
    FavoritesConventionalChannel,
    FavoritesConventionalDepartment,
    FavoritesConventionalSystem,
    FavoritesTrunkChannel,
    FavoritesTrunkDepartment,
    FavoritesTrunkSite,
    FavoritesTrunkSystem,
)
from .favorites_workspace import (
    FavoritesWorkspace,
    FavoritesWorkspaceBinding,
)

_NavigationHierarchySource = (
    FavoritesConventionalSystem
    | FavoritesTrunkSystem
    | FavoritesConventionalDepartment
    | FavoritesTrunkDepartment
    | FavoritesTrunkSite
    | FavoritesConventionalChannel
    | FavoritesTrunkChannel
)

_NavigationSource = (
    FavoritesCatalogEntry
    | _NavigationHierarchySource
)


class FavoritesNavigationKind(StrEnum):
    """Identify one renderer-neutral Favorites navigation level."""

    FAVORITES_LIST = "favorites_list"
    CONVENTIONAL_SYSTEM = "conventional_system"
    TRUNK_SYSTEM = "trunk_system"
    CONVENTIONAL_DEPARTMENT = "conventional_department"
    TRUNK_DEPARTMENT = "trunk_department"
    TRUNK_SITE = "trunk_site"
    CONVENTIONAL_CHANNEL = "conventional_channel"
    TRUNK_CHANNEL = "trunk_channel"


@dataclass(frozen=True, slots=True)
class FavoritesNavigationPath:
    """Address one navigation node by immutable source positions."""

    indexes: tuple[int, ...]

    def __post_init__(self) -> None:
        if type(self.indexes) is not tuple:
            raise TypeError(
                "Favorites navigation path indexes must be a tuple."
            )

        if not self.indexes:
            raise ValueError(
                "Favorites navigation path must not be empty."
            )

        if any(
            type(index) is not int
            or index < 0
            for index in self.indexes
        ):
            raise ValueError(
                "Favorites navigation path indexes must be "
                "non-negative integers."
            )

    @property
    def parent(self) -> FavoritesNavigationPath | None:
        """Return the containing path, or ``None`` for a root."""

        if len(self.indexes) == 1:
            return None

        return FavoritesNavigationPath(
            self.indexes[:-1]
        )

    def child(
        self,
        source_index: int,
    ) -> FavoritesNavigationPath:
        """Append one immutable source position."""

        return FavoritesNavigationPath(
            (
                *self.indexes,
                source_index,
            )
        )


def _source_index(
    source: _NavigationSource,
) -> int:
    if isinstance(
        source,
        FavoritesCatalogEntry,
    ):
        return source.source_index

    return source.source.source_index


def _source_name(
    source: _NavigationSource,
) -> str | None:
    return source.name


_KIND_SOURCE_TYPES: dict[
    FavoritesNavigationKind,
    type[object],
] = {
    FavoritesNavigationKind.FAVORITES_LIST: FavoritesCatalogEntry,
    FavoritesNavigationKind.CONVENTIONAL_SYSTEM: FavoritesConventionalSystem,
    FavoritesNavigationKind.TRUNK_SYSTEM: FavoritesTrunkSystem,
    FavoritesNavigationKind.CONVENTIONAL_DEPARTMENT:
        FavoritesConventionalDepartment,
    FavoritesNavigationKind.TRUNK_DEPARTMENT: FavoritesTrunkDepartment,
    FavoritesNavigationKind.TRUNK_SITE: FavoritesTrunkSite,
    FavoritesNavigationKind.CONVENTIONAL_CHANNEL:
        FavoritesConventionalChannel,
    FavoritesNavigationKind.TRUNK_CHANNEL: FavoritesTrunkChannel,
}


@dataclass(frozen=True, slots=True)
class FavoritesNavigationNode:
    """One immutable source-backed navigation node."""

    path: FavoritesNavigationPath
    kind: FavoritesNavigationKind
    name: str | None
    source: _NavigationSource
    children: tuple[FavoritesNavigationNode, ...]

    def __post_init__(self) -> None:
        if not isinstance(
            self.path,
            FavoritesNavigationPath,
        ):
            raise TypeError(
                "Favorites navigation node path must be "
                "FavoritesNavigationPath."
            )

        if not isinstance(
            self.kind,
            FavoritesNavigationKind,
        ):
            raise TypeError(
                "Favorites navigation node kind must be "
                "FavoritesNavigationKind."
            )

        if (
            self.name is not None
            and not isinstance(
                self.name,
                str,
            )
        ):
            raise TypeError(
                "Favorites navigation node name must be str or None."
            )

        expected_source_type = _KIND_SOURCE_TYPES[
            self.kind
        ]

        if not isinstance(
            self.source,
            expected_source_type,
        ):
            raise TypeError(
                "Favorites navigation node source does not "
                f"match kind {self.kind.value}."
            )

        if self.path.indexes[-1] != _source_index(
            self.source
        ):
            raise ValueError(
                "Favorites navigation node path must end at "
                "its source index."
            )

        if self.name != _source_name(
            self.source
        ):
            raise ValueError(
                "Favorites navigation node name must exactly "
                "match its source name."
            )

        if type(self.children) is not tuple:
            raise TypeError(
                "Favorites navigation node children must be a tuple."
            )

        if any(
            not isinstance(
                child,
                FavoritesNavigationNode,
            )
            for child in self.children
        ):
            raise TypeError(
                "Favorites navigation node children must contain "
                "FavoritesNavigationNode values."
            )

        if any(
            child.path.parent != self.path
            for child in self.children
        ):
            raise ValueError(
                "Favorites navigation children must be direct "
                "descendants of the node path."
            )

        child_paths = tuple(
            child.path
            for child in self.children
        )

        if len(child_paths) != len(
            set(child_paths)
        ):
            raise ValueError(
                "Favorites navigation child paths must be unique."
            )

    @property
    def parent_path(
        self,
    ) -> FavoritesNavigationPath | None:
        """Return the node parent without storing redundant state."""

        return self.path.parent

    @property
    def source_index(self) -> int:
        """Return the immutable source position for this node."""

        return _source_index(
            self.source
        )


@dataclass(frozen=True, slots=True)
class FavoritesNavigation:
    """Immutable navigation tree retaining its authoritative workspace."""

    workspace: FavoritesWorkspace
    roots: tuple[FavoritesNavigationNode, ...]

    def __post_init__(self) -> None:
        if not isinstance(
            self.workspace,
            FavoritesWorkspace,
        ):
            raise TypeError(
                "Favorites navigation workspace must be "
                "FavoritesWorkspace."
            )

        if type(self.roots) is not tuple:
            raise TypeError(
                "Favorites navigation roots must be a tuple."
            )

        if any(
            not isinstance(
                root,
                FavoritesNavigationNode,
            )
            for root in self.roots
        ):
            raise TypeError(
                "Favorites navigation roots must contain "
                "FavoritesNavigationNode values."
            )

        if any(
            root.kind
            is not FavoritesNavigationKind.FAVORITES_LIST
            or root.parent_path is not None
            for root in self.roots
        ):
            raise ValueError(
                "Favorites navigation roots must be "
                "Favorites List root nodes."
            )

        expected_entries = tuple(
            binding.entry
            for binding in self.workspace.bindings
        )

        actual_entries = tuple(
            root.source
            for root in self.roots
        )

        if actual_entries != expected_entries:
            raise ValueError(
                "Favorites navigation roots must exactly follow "
                "resolved workspace bindings."
            )


def _channel_node(
    channel: (
        FavoritesConventionalChannel
        | FavoritesTrunkChannel
    ),
    parent_path: FavoritesNavigationPath,
) -> FavoritesNavigationNode:
    if isinstance(
        channel,
        FavoritesConventionalChannel,
    ):
        kind = (
            FavoritesNavigationKind.CONVENTIONAL_CHANNEL
        )
    else:
        kind = FavoritesNavigationKind.TRUNK_CHANNEL

    return FavoritesNavigationNode(
        path=parent_path.child(
            channel.source.source_index
        ),
        kind=kind,
        name=channel.name,
        source=channel,
        children=(),
    )


def _conventional_department_node(
    department: FavoritesConventionalDepartment,
    parent_path: FavoritesNavigationPath,
) -> FavoritesNavigationNode:
    path = parent_path.child(
        department.source.source_index
    )

    return FavoritesNavigationNode(
        path=path,
        kind=FavoritesNavigationKind.CONVENTIONAL_DEPARTMENT,
        name=department.name,
        source=department,
        children=tuple(
            _channel_node(
                channel,
                path,
            )
            for channel in department.channels
        ),
    )


def _trunk_department_node(
    department: FavoritesTrunkDepartment,
    parent_path: FavoritesNavigationPath,
) -> FavoritesNavigationNode:
    path = parent_path.child(
        department.source.source_index
    )

    return FavoritesNavigationNode(
        path=path,
        kind=FavoritesNavigationKind.TRUNK_DEPARTMENT,
        name=department.name,
        source=department,
        children=tuple(
            _channel_node(
                channel,
                path,
            )
            for channel in department.channels
        ),
    )


def _trunk_site_node(
    site: FavoritesTrunkSite,
    parent_path: FavoritesNavigationPath,
) -> FavoritesNavigationNode:
    return FavoritesNavigationNode(
        path=parent_path.child(
            site.source.source_index
        ),
        kind=FavoritesNavigationKind.TRUNK_SITE,
        name=site.name,
        source=site,
        children=(),
    )


def _conventional_system_node(
    system: FavoritesConventionalSystem,
    parent_path: FavoritesNavigationPath,
) -> FavoritesNavigationNode:
    path = parent_path.child(
        system.source.source_index
    )

    return FavoritesNavigationNode(
        path=path,
        kind=FavoritesNavigationKind.CONVENTIONAL_SYSTEM,
        name=system.name,
        source=system,
        children=tuple(
            _conventional_department_node(
                department,
                path,
            )
            for department in system.departments
        ),
    )


def _trunk_system_node(
    system: FavoritesTrunkSystem,
    parent_path: FavoritesNavigationPath,
) -> FavoritesNavigationNode:
    path = parent_path.child(
        system.source.source_index
    )

    mixed_children: list[
        FavoritesTrunkSite
        | FavoritesTrunkDepartment
    ] = [
        *system.sites,
        *system.departments,
    ]

    mixed_children.sort(
        key=lambda child: (
            child.source.source_index
        )
    )

    children: list[
        FavoritesNavigationNode
    ] = []

    for child in mixed_children:
        if isinstance(
            child,
            FavoritesTrunkSite,
        ):
            children.append(
                _trunk_site_node(
                    child,
                    path,
                )
            )
            continue

        children.append(
            _trunk_department_node(
                child,
                path,
            )
        )

    return FavoritesNavigationNode(
        path=path,
        kind=FavoritesNavigationKind.TRUNK_SYSTEM,
        name=system.name,
        source=system,
        children=tuple(children),
    )


def _favorites_list_node(
    binding: FavoritesWorkspaceBinding,
) -> FavoritesNavigationNode:
    path = FavoritesNavigationPath(
        (
            binding.entry.source_index,
        )
    )

    children: list[
        FavoritesNavigationNode
    ] = []

    for system in binding.hierarchy.systems:
        if isinstance(
            system,
            FavoritesConventionalSystem,
        ):
            children.append(
                _conventional_system_node(
                    system,
                    path,
                )
            )
            continue

        children.append(
            _trunk_system_node(
                system,
                path,
            )
        )

    return FavoritesNavigationNode(
        path=path,
        kind=FavoritesNavigationKind.FAVORITES_LIST,
        name=binding.entry.name,
        source=binding.entry,
        children=tuple(children),
    )


def project_favorites_navigation(
    workspace: FavoritesWorkspace,
) -> FavoritesNavigation:
    """Project resolved workspace bindings into immutable navigation."""

    if not isinstance(
        workspace,
        FavoritesWorkspace,
    ):
        raise TypeError(
            "Favorites navigation projection requires "
            "FavoritesWorkspace."
        )

    return FavoritesNavigation(
        workspace=workspace,
        roots=tuple(
            _favorites_list_node(
                binding
            )
            for binding in workspace.bindings
        ),
    )


__all__ = [
    "FavoritesNavigation",
    "FavoritesNavigationKind",
    "FavoritesNavigationNode",
    "FavoritesNavigationPath",
    "project_favorites_navigation",
]
