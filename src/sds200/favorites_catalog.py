"""Read-only Favorites List catalog projection over lossless f_list.cfg records."""

from __future__ import annotations

from dataclasses import dataclass

from .favorites_file import FavoritesSourceFile, FavoritesSourceRecord


class FavoritesCatalogError(ValueError):
    """Report a known malformed Favorites catalog record."""

    def __init__(
        self,
        source_index: int,
        command: str,
        message: str,
    ) -> None:
        self.source_index = source_index
        self.command = command
        super().__init__(
            f"Favorites catalog record {source_index + 1} "
            f"({command}): {message}"
        )


@dataclass(frozen=True, slots=True)
class FavoritesCatalogEntry:
    """One F-List record with its exact source position and raw record."""

    source_index: int
    source: FavoritesSourceRecord

    def __post_init__(self) -> None:
        if self.source_index < 0:
            raise ValueError(
                "Favorites catalog source index must be non-negative."
            )
        if not isinstance(
            self.source,
            FavoritesSourceRecord,
        ):
            raise TypeError(
                "Favorites catalog entry source must be "
                "FavoritesSourceRecord."
            )
        if self.source.command != "F-List":
            raise ValueError(
                "Favorites catalog entry source command "
                "must be F-List."
            )
        if len(self.source.fields) < 2:
            raise ValueError(
                "Favorites catalog entry requires "
                "UserName and Filename fields."
            )

    @property
    def name(self) -> str:
        """Return the exact Favorites List display name field."""

        return self.source.fields[0]

    @property
    def filename(self) -> str:
        """Return the exact Favorites List filename field."""

        return self.source.fields[1]


@dataclass(frozen=True, slots=True)
class FavoritesCatalog:
    """Ordered read-only f_list.cfg projection retaining the full raw source."""

    source: FavoritesSourceFile
    metadata_indexes: tuple[int, ...]
    entries: tuple[FavoritesCatalogEntry, ...]
    unclassified_indexes: tuple[int, ...]


def project_favorites_catalog(
    source: FavoritesSourceFile,
) -> FavoritesCatalog:
    """Project f_list.cfg records without normalizing source values."""

    if not isinstance(source, FavoritesSourceFile):
        raise TypeError(
            "Favorites catalog source must be "
            "FavoritesSourceFile."
        )

    metadata_indexes: list[int] = []
    entries: list[FavoritesCatalogEntry] = []
    unclassified_indexes: list[int] = []

    for source_index, record in enumerate(source.records):
        command = record.command

        if command in {
            "TargetModel",
            "FormatVersion",
        }:
            metadata_indexes.append(source_index)
            continue

        if command == "F-List":
            if len(record.fields) < 2:
                raise FavoritesCatalogError(
                    source_index,
                    command,
                    "requires UserName and Filename fields.",
                )

            entries.append(
                FavoritesCatalogEntry(
                    source_index=source_index,
                    source=record,
                )
            )
            continue

        unclassified_indexes.append(source_index)

    return FavoritesCatalog(
        source=source,
        metadata_indexes=tuple(metadata_indexes),
        entries=tuple(entries),
        unclassified_indexes=tuple(
            unclassified_indexes
        ),
    )


__all__ = [
    "FavoritesCatalog",
    "FavoritesCatalogEntry",
    "FavoritesCatalogError",
    "project_favorites_catalog",
]
