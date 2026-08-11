"""Read-only storage source for an offline copied Favorites directory."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .favorites_storage import (
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
)


class FavoritesCopiedTreeStorageError(RuntimeError):
    """Report a copied Favorites directory that cannot be safely read."""

    def __init__(
        self,
        path: Path,
        message: str,
    ) -> None:
        self.path = path
        self.message = message
        super().__init__(
            f"Favorites copied-tree storage error at {path}: {message}"
        )


def _directory_root(path: Path) -> Path:
    try:
        status = path.lstat()
    except FileNotFoundError as error:
        raise FavoritesCopiedTreeStorageError(
            path,
            "Favorites directory does not exist.",
        ) from error
    except OSError as error:
        raise FavoritesCopiedTreeStorageError(
            path,
            f"Could not inspect Favorites directory: {error}",
        ) from error

    if stat.S_ISLNK(status.st_mode):
        raise FavoritesCopiedTreeStorageError(
            path,
            "Favorites directory must not be a symbolic link.",
        )

    if not stat.S_ISDIR(status.st_mode):
        raise FavoritesCopiedTreeStorageError(
            path,
            "Favorites directory must be a directory.",
        )

    try:
        return path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise FavoritesCopiedTreeStorageError(
            path,
            f"Could not resolve Favorites directory: {error}",
        ) from error


def _read_regular_file(
    path: Path,
    *,
    description: str,
) -> bytes:
    try:
        initial = path.lstat()
    except FileNotFoundError as error:
        raise FavoritesCopiedTreeStorageError(
            path,
            f"{description} does not exist.",
        ) from error
    except OSError as error:
        raise FavoritesCopiedTreeStorageError(
            path,
            f"Could not inspect {description}: {error}",
        ) from error

    if stat.S_ISLNK(initial.st_mode):
        raise FavoritesCopiedTreeStorageError(
            path,
            f"{description} must not be a symbolic link.",
        )

    if not stat.S_ISREG(initial.st_mode):
        raise FavoritesCopiedTreeStorageError(
            path,
            f"{description} must be a regular file.",
        )

    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )

    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise FavoritesCopiedTreeStorageError(
            path,
            f"Could not open {description}: {error}",
        ) from error

    try:
        opened = os.fstat(descriptor)

        if not stat.S_ISREG(opened.st_mode):
            raise FavoritesCopiedTreeStorageError(
                path,
                f"{description} changed to a non-regular file.",
            )

        if (
            opened.st_dev,
            opened.st_ino,
        ) != (
            initial.st_dev,
            initial.st_ino,
        ):
            raise FavoritesCopiedTreeStorageError(
                path,
                f"{description} changed while being opened.",
            )

        try:
            with os.fdopen(
                descriptor,
                "rb",
                closefd=False,
            ) as handle:
                content = handle.read()
        except OSError as error:
            raise FavoritesCopiedTreeStorageError(
                path,
                f"Could not read {description}: {error}",
            ) from error

        final = os.fstat(descriptor)

        if (
            final.st_dev,
            final.st_ino,
            final.st_size,
            final.st_mtime_ns,
        ) != (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
        ):
            raise FavoritesCopiedTreeStorageError(
                path,
                f"{description} changed while being read.",
            )

        return content
    finally:
        os.close(descriptor)


@dataclass(frozen=True, slots=True)
class FavoritesCopiedTreeStorageSource:
    """Read one offline copied ``favorites_lists`` directory."""

    favorites_directory: Path

    def __post_init__(self) -> None:
        if not isinstance(
            self.favorites_directory,
            Path,
        ):
            raise TypeError(
                "Favorites copied-tree directory must be pathlib.Path."
            )

    def read_snapshot(self) -> FavoritesStorageSnapshot:
        """Read exact catalog and immediate HPD bytes without mutation."""

        root = _directory_root(
            self.favorites_directory
        )

        catalog_bytes = _read_regular_file(
            root / "f_list.cfg",
            description="Favorites catalog file",
        )

        try:
            with os.scandir(root) as handle:
                entries = sorted(
                    handle,
                    key=lambda entry: entry.name,
                )
        except OSError as error:
            raise FavoritesCopiedTreeStorageError(
                root,
                f"Could not scan Favorites directory: {error}",
            ) from error

        documents: list[
            FavoritesStorageDocument
        ] = []

        for entry in entries:
            if entry.name == "f_list.cfg":
                continue

            if not entry.name.endswith(".hpd"):
                continue

            try:
                if entry.is_symlink():
                    raise FavoritesCopiedTreeStorageError(
                        Path(entry.path),
                        "Favorites HPD file must not be a symbolic link.",
                    )

                if not entry.is_file(
                    follow_symlinks=False
                ):
                    continue
            except OSError as error:
                raise FavoritesCopiedTreeStorageError(
                    Path(entry.path),
                    f"Could not inspect Favorites HPD file: {error}",
                ) from error

            # Validate the exact directory-entry name before reading it.
            FavoritesStorageDocument(
                filename=entry.name,
                content=b"",
            )

            content = _read_regular_file(
                Path(entry.path),
                description="Favorites HPD file",
            )

            documents.append(
                FavoritesStorageDocument(
                    filename=entry.name,
                    content=content,
                )
            )

        return FavoritesStorageSnapshot(
            catalog_bytes=catalog_bytes,
            documents=tuple(documents),
        )


__all__ = [
    "FavoritesCopiedTreeStorageError",
    "FavoritesCopiedTreeStorageSource",
]
