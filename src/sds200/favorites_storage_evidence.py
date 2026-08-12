"""Shared exact Favorites storage evidence for verified write workflows."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .favorites_storage import FavoritesStorageSnapshot


@dataclass(frozen=True, slots=True)
class FavoritesTreeEvidence:
    """Immutable identity and complete-tree digest for one Favorites tree."""

    device: int
    inode: int
    sha256: str


class FavoritesTreeEvidenceError(RuntimeError):
    """Report a tree that cannot be captured as safe deterministic evidence."""

    def __init__(
        self,
        path: Path,
        message: str,
    ) -> None:
        self.path = path
        self.message = message
        super().__init__(message)


class _Digest(Protocol):
    def update(
        self,
        data: bytes,
        /,
    ) -> None: ...


def _stat_fingerprint(
    value: os.stat_result,
) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
    )


def _hash_field(
    digest: _Digest,
    value: bytes,
) -> None:
    digest.update(
        len(value).to_bytes(
            8,
            "big",
        )
    )
    digest.update(value)


def _tree_regular_file_digest(
    path: Path,
    initial: os.stat_result,
) -> bytes:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )

    try:
        descriptor = os.open(
            path,
            flags,
        )
    except OSError as error:
        raise FavoritesTreeEvidenceError(
            path,
            f"Could not safely open copied-tree file: {error}",
        ) from error

    try:
        opened = os.fstat(descriptor)

        if not stat.S_ISREG(opened.st_mode):
            raise FavoritesTreeEvidenceError(
                path,
                "Copied-tree file changed to a non-regular file.",
            )

        if (
            opened.st_dev,
            opened.st_ino,
        ) != (
            initial.st_dev,
            initial.st_ino,
        ):
            raise FavoritesTreeEvidenceError(
                path,
                "Copied-tree file changed while being opened.",
            )

        digest = hashlib.sha256()

        try:
            with os.fdopen(
                descriptor,
                "rb",
                closefd=False,
            ) as handle:
                while chunk := handle.read(
                    1024 * 1024
                ):
                    digest.update(chunk)
        except OSError as error:
            raise FavoritesTreeEvidenceError(
                path,
                f"Could not safely read copied-tree file: {error}",
            ) from error

        final = os.fstat(descriptor)

        if (
            _stat_fingerprint(final)
            != _stat_fingerprint(opened)
        ):
            raise FavoritesTreeEvidenceError(
                path,
                "Copied-tree file changed while being read.",
            )

        return digest.digest()
    finally:
        os.close(descriptor)


def favorites_tree_evidence(
    root: Path,
) -> FavoritesTreeEvidence:
    """Capture deterministic complete-tree evidence without following symlinks."""

    if not isinstance(root, Path):
        raise TypeError(
            "Favorites tree evidence root must be pathlib.Path."
        )

    try:
        initial_root = root.lstat()
    except OSError as error:
        raise FavoritesTreeEvidenceError(
            root,
            f"Could not inspect copied-tree root: {error}",
        ) from error

    if stat.S_ISLNK(initial_root.st_mode):
        raise FavoritesTreeEvidenceError(
            root,
            "Copied-tree root must not be a symbolic link.",
        )

    if not stat.S_ISDIR(initial_root.st_mode):
        raise FavoritesTreeEvidenceError(
            root,
            "Copied-tree root must be a directory.",
        )

    digest = hashlib.sha256()

    def visit(
        directory: Path,
        relative: Path,
    ) -> None:
        try:
            initial_directory = directory.lstat()
        except OSError as error:
            raise FavoritesTreeEvidenceError(
                directory,
                f"Could not inspect copied-tree directory: {error}",
            ) from error

        if stat.S_ISLNK(
            initial_directory.st_mode
        ):
            raise FavoritesTreeEvidenceError(
                directory,
                "Copied-tree directories must not be symbolic links.",
            )

        if not stat.S_ISDIR(
            initial_directory.st_mode
        ):
            raise FavoritesTreeEvidenceError(
                directory,
                "Copied-tree directory changed type during traversal.",
            )

        _hash_field(
            digest,
            b"D",
        )
        _hash_field(
            digest,
            os.fsencode(
                relative.as_posix()
            ),
        )
        _hash_field(
            digest,
            (
                initial_directory.st_mode
                & 0o7777
            ).to_bytes(
                4,
                "big",
            ),
        )

        try:
            with os.scandir(
                directory
            ) as handle:
                entries = sorted(
                    handle,
                    key=lambda entry: os.fsencode(
                        entry.name
                    ),
                )
        except OSError as error:
            raise FavoritesTreeEvidenceError(
                directory,
                f"Could not scan copied-tree directory: {error}",
            ) from error

        for entry in entries:
            path = Path(entry.path)
            child_relative = (
                Path(entry.name)
                if relative == Path(".")
                else relative / entry.name
            )

            try:
                observed = path.lstat()
            except OSError as error:
                raise FavoritesTreeEvidenceError(
                    path,
                    f"Could not inspect copied-tree entry: {error}",
                ) from error

            if stat.S_ISLNK(
                observed.st_mode
            ):
                raise FavoritesTreeEvidenceError(
                    path,
                    "Copied-tree write targets must not contain "
                    "symbolic links.",
                )

            if stat.S_ISDIR(
                observed.st_mode
            ):
                visit(
                    path,
                    child_relative,
                )
                continue

            if not stat.S_ISREG(
                observed.st_mode
            ):
                raise FavoritesTreeEvidenceError(
                    path,
                    "Copied-tree write targets may contain only "
                    "regular files and directories.",
                )

            _hash_field(
                digest,
                b"F",
            )
            _hash_field(
                digest,
                os.fsencode(
                    child_relative.as_posix()
                ),
            )
            _hash_field(
                digest,
                (
                    observed.st_mode
                    & 0o7777
                ).to_bytes(
                    4,
                    "big",
                ),
            )
            _hash_field(
                digest,
                _tree_regular_file_digest(
                    path,
                    observed,
                ),
            )

        try:
            final_directory = (
                directory.lstat()
            )
        except OSError as error:
            raise FavoritesTreeEvidenceError(
                directory,
                f"Could not re-inspect copied-tree directory: {error}",
            ) from error

        if (
            final_directory.st_dev,
            final_directory.st_ino,
            final_directory.st_mode,
            final_directory.st_mtime_ns,
        ) != (
            initial_directory.st_dev,
            initial_directory.st_ino,
            initial_directory.st_mode,
            initial_directory.st_mtime_ns,
        ):
            raise FavoritesTreeEvidenceError(
                directory,
                "Copied-tree directory changed during traversal.",
            )

    visit(
        root,
        Path("."),
    )

    try:
        final_root = root.lstat()
    except OSError as error:
        raise FavoritesTreeEvidenceError(
            root,
            f"Could not re-inspect copied-tree root: {error}",
        ) from error

    if (
        final_root.st_dev,
        final_root.st_ino,
        final_root.st_mode,
        final_root.st_mtime_ns,
    ) != (
        initial_root.st_dev,
        initial_root.st_ino,
        initial_root.st_mode,
        initial_root.st_mtime_ns,
    ):
        raise FavoritesTreeEvidenceError(
            root,
            "Copied-tree root changed during traversal.",
        )

    return FavoritesTreeEvidence(
        device=final_root.st_dev,
        inode=final_root.st_ino,
        sha256=digest.hexdigest(),
    )


def favorites_storage_snapshot_sha256(
    snapshot: FavoritesStorageSnapshot,
) -> str:
    """Return the exact deterministic identity for one managed snapshot."""

    if not isinstance(
        snapshot,
        FavoritesStorageSnapshot,
    ):
        raise TypeError(
            "Favorites snapshot identity requires FavoritesStorageSnapshot."
        )

    digest = hashlib.sha256()
    _hash_field(
        digest,
        b"sds200-favorites-storage-snapshot-v1",
    )
    _hash_field(
        digest,
        snapshot.catalog_bytes,
    )

    for document in snapshot.documents:
        _hash_field(
            digest,
            document.filename.encode(
                "utf-8"
            ),
        )
        _hash_field(
            digest,
            document.content,
        )

    return digest.hexdigest()


__all__ = [
    "FavoritesTreeEvidence",
    "FavoritesTreeEvidenceError",
    "favorites_storage_snapshot_sha256",
    "favorites_tree_evidence",
]
