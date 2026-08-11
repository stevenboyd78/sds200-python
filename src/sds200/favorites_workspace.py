"""Pure in-memory Favorites catalog and HPD hierarchy binding."""

from __future__ import annotations

from dataclasses import dataclass

from .favorites_catalog import FavoritesCatalog, FavoritesCatalogEntry
from .favorites_hierarchy import FavoritesHierarchy


@dataclass(frozen=True, slots=True)
class FavoritesHierarchyDocument:
    """One explicitly named HPD hierarchy supplied to the workspace binder."""

    filename: str
    hierarchy: FavoritesHierarchy

    def __post_init__(self) -> None:
        if not isinstance(self.filename, str):
            raise TypeError(
                "Favorites hierarchy document filename must be str."
            )
        if not isinstance(
            self.hierarchy,
            FavoritesHierarchy,
        ):
            raise TypeError(
                "Favorites hierarchy document requires "
                "FavoritesHierarchy."
            )


@dataclass(frozen=True, slots=True)
class FavoritesWorkspaceBinding:
    """One exact catalog-entry to hierarchy-document binding."""

    entry: FavoritesCatalogEntry
    document: FavoritesHierarchyDocument

    def __post_init__(self) -> None:
        if not isinstance(
            self.entry,
            FavoritesCatalogEntry,
        ):
            raise TypeError(
                "Favorites workspace binding entry must be "
                "FavoritesCatalogEntry."
            )
        if not isinstance(
            self.document,
            FavoritesHierarchyDocument,
        ):
            raise TypeError(
                "Favorites workspace binding document must be "
                "FavoritesHierarchyDocument."
            )
        if self.entry.filename != self.document.filename:
            raise ValueError(
                "Favorites workspace binding requires exact "
                "filename equality."
            )

    @property
    def name(self) -> str:
        """Return the exact Favorites List display name."""

        return self.entry.name

    @property
    def filename(self) -> str:
        """Return the exact bound catalog/document filename."""

        return self.entry.filename

    @property
    def hierarchy(self) -> FavoritesHierarchy:
        """Return the bound renderer-neutral HPD hierarchy."""

        return self.document.hierarchy


@dataclass(frozen=True, slots=True)
class FavoritesWorkspace:
    """Immutable binding result with explicit unresolved diagnostics."""

    catalog: FavoritesCatalog
    documents: tuple[FavoritesHierarchyDocument, ...]
    bindings: tuple[FavoritesWorkspaceBinding, ...]
    missing_entries: tuple[FavoritesCatalogEntry, ...]
    ambiguous_entries: tuple[FavoritesCatalogEntry, ...]
    duplicate_catalog_filenames: tuple[str, ...]
    duplicate_document_filenames: tuple[str, ...]
    orphan_documents: tuple[FavoritesHierarchyDocument, ...]


def _duplicates_in_order(
    values: tuple[str, ...],
) -> tuple[str, ...]:
    counts: dict[str, int] = {}

    for value in values:
        counts[value] = counts.get(value, 0) + 1

    emitted: set[str] = set()
    duplicates: list[str] = []

    for value in values:
        if counts[value] <= 1 or value in emitted:
            continue

        emitted.add(value)
        duplicates.append(value)

    return tuple(duplicates)


def bind_favorites_workspace(
    catalog: FavoritesCatalog,
    documents: tuple[FavoritesHierarchyDocument, ...],
) -> FavoritesWorkspace:
    """Bind catalog entries to supplied HPD hierarchies by exact filename."""

    if not isinstance(catalog, FavoritesCatalog):
        raise TypeError(
            "Favorites workspace catalog must be FavoritesCatalog."
        )
    if type(documents) is not tuple:
        raise TypeError(
            "Favorites workspace documents must be a tuple."
        )
    if any(
        not isinstance(
            document,
            FavoritesHierarchyDocument,
        )
        for document in documents
    ):
        raise TypeError(
            "Favorites workspace documents must contain "
            "FavoritesHierarchyDocument values."
        )

    documents_by_filename: dict[
        str,
        list[FavoritesHierarchyDocument],
    ] = {}

    for document in documents:
        documents_by_filename.setdefault(
            document.filename,
            [],
        ).append(document)

    catalog_filenames = tuple(
        entry.filename
        for entry in catalog.entries
    )
    document_filenames = tuple(
        document.filename
        for document in documents
    )

    bindings: list[FavoritesWorkspaceBinding] = []
    missing_entries: list[FavoritesCatalogEntry] = []
    ambiguous_entries: list[FavoritesCatalogEntry] = []

    for entry in catalog.entries:
        matching_documents = documents_by_filename.get(
            entry.filename
        )

        if matching_documents is None:
            missing_entries.append(entry)
            continue

        if len(matching_documents) != 1:
            ambiguous_entries.append(entry)
            continue

        bindings.append(
            FavoritesWorkspaceBinding(
                entry=entry,
                document=matching_documents[0],
            )
        )

    referenced_filenames = set(catalog_filenames)

    orphan_documents = tuple(
        document
        for document in documents
        if document.filename not in referenced_filenames
    )

    return FavoritesWorkspace(
        catalog=catalog,
        documents=documents,
        bindings=tuple(bindings),
        missing_entries=tuple(missing_entries),
        ambiguous_entries=tuple(ambiguous_entries),
        duplicate_catalog_filenames=_duplicates_in_order(
            catalog_filenames
        ),
        duplicate_document_filenames=_duplicates_in_order(
            document_filenames
        ),
        orphan_documents=orphan_documents,
    )


__all__ = [
    "FavoritesHierarchyDocument",
    "FavoritesWorkspace",
    "FavoritesWorkspaceBinding",
    "bind_favorites_workspace",
]
