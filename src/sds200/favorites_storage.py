"""Immutable Favorites storage snapshots and pure import/export projection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PureWindowsPath
from typing import Protocol

from .favorites_catalog import FavoritesCatalogEntry, project_favorites_catalog
from .favorites_file import parse_favorites_file
from .favorites_hierarchy import project_favorites_hierarchy
from .favorites_workspace import (
    FavoritesHierarchyDocument,
    FavoritesWorkspace,
    bind_favorites_workspace,
)


class FavoritesStorageFilenameError(ValueError):
    """Reject one filename that cannot safely identify a storage child."""

    def __init__(
        self,
        filename: str,
        *,
        source_index: int | None = None,
    ) -> None:
        self.filename = filename
        self.source_index = source_index

        if source_index is None:
            prefix = "Favorites storage filename"
        else:
            prefix = (
                f"Favorites catalog record {source_index + 1} "
                "filename"
            )

        super().__init__(
            f"{prefix} must be one exact non-traversing "
            f"file name: {filename!r}"
        )


def _validate_storage_filename(
    filename: str,
    *,
    source_index: int | None = None,
) -> str:
    if not isinstance(filename, str):
        raise TypeError(
            "Favorites storage filename must be a string."
        )

    unsafe = (
        not filename
        or filename in {".", ".."}
        or "/" in filename
        or "\\" in filename
        or "\x00" in filename
        or bool(PureWindowsPath(filename).drive)
    )

    if unsafe:
        raise FavoritesStorageFilenameError(
            filename,
            source_index=source_index,
        )

    return filename


@dataclass(frozen=True, slots=True)
class FavoritesStorageDocument:
    """One exact named Favorites document captured from read-only storage."""

    filename: str
    content: bytes

    def __post_init__(self) -> None:
        _validate_storage_filename(self.filename)

        if not isinstance(self.content, bytes):
            raise TypeError(
                "Favorites storage document content must be bytes."
            )


@dataclass(frozen=True, slots=True)
class FavoritesStorageSnapshot:
    """Immutable f_list.cfg bytes and ordered named Favorites documents."""

    catalog_bytes: bytes
    documents: tuple[FavoritesStorageDocument, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.catalog_bytes, bytes):
            raise TypeError(
                "Favorites storage catalog content must be bytes."
            )

        if type(self.documents) is not tuple:
            raise TypeError(
                "Favorites storage documents must be a tuple."
            )

        if any(
            not isinstance(
                document,
                FavoritesStorageDocument,
            )
            for document in self.documents
        ):
            raise TypeError(
                "Favorites storage documents must contain "
                "FavoritesStorageDocument values."
            )


class FavoritesStorageSource(Protocol):
    """Read one immutable Favorites storage snapshot."""

    def read_snapshot(self) -> FavoritesStorageSnapshot:
        """Return exact catalog and named document bytes."""


def _validate_catalog_entry_filename(
    entry: FavoritesCatalogEntry,
) -> None:
    _validate_storage_filename(
        entry.filename,
        source_index=entry.source_index,
    )


def project_favorites_storage_snapshot(
    snapshot: FavoritesStorageSnapshot,
) -> FavoritesWorkspace:
    """Project one immutable storage snapshot through the 21.1 layers."""

    if not isinstance(
        snapshot,
        FavoritesStorageSnapshot,
    ):
        raise TypeError(
            "Favorites storage projection requires "
            "FavoritesStorageSnapshot."
        )

    catalog_source = parse_favorites_file(
        snapshot.catalog_bytes
    )
    catalog = project_favorites_catalog(
        catalog_source
    )

    for entry in catalog.entries:
        _validate_catalog_entry_filename(entry)

    documents = tuple(
        FavoritesHierarchyDocument(
            filename=document.filename,
            hierarchy=project_favorites_hierarchy(
                parse_favorites_file(
                    document.content
                )
            ),
        )
        for document in snapshot.documents
    )

    return bind_favorites_workspace(
        catalog,
        documents,
    )


def export_favorites_workspace_snapshot(
    workspace: FavoritesWorkspace,
) -> FavoritesStorageSnapshot:
    """Export one workspace to its exact preserved storage snapshot."""

    if not isinstance(workspace, FavoritesWorkspace):
        raise TypeError(
            "Favorites workspace export requires FavoritesWorkspace."
        )

    return FavoritesStorageSnapshot(
        catalog_bytes=workspace.catalog.source.to_bytes(),
        documents=tuple(
            FavoritesStorageDocument(
                filename=document.filename,
                content=document.hierarchy.source.to_bytes(),
            )
            for document in workspace.documents
        ),
    )


__all__ = [
    "FavoritesStorageDocument",
    "FavoritesStorageFilenameError",
    "FavoritesStorageSnapshot",
    "FavoritesStorageSource",
    "export_favorites_workspace_snapshot",
    "project_favorites_storage_snapshot",
]
