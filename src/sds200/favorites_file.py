"""Lossless read-only parsing for SDS100/200 Favorites source files."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

_ALLOWED_LINE_ENDINGS = frozenset({b"", b"\r", b"\n", b"\r\n"})


class FavoritesFileParseError(ValueError):
    """Report a structural or encoding failure at one physical source line."""

    def __init__(self, line_number: int, message: str) -> None:
        self.line_number = line_number
        super().__init__(f"Favorites source line {line_number}: {message}")


@dataclass(frozen=True, slots=True)
class FavoritesSourceRecord:
    """One immutable source line with exact bytes and positional ASCII fields."""

    content: bytes
    line_ending: bytes
    command: str = field(init=False)
    fields: tuple[str, ...] = field(init=False)

    def __post_init__(self) -> None:
        if type(self.content) is not bytes:
            raise TypeError("Favorites source record content must be bytes.")
        if type(self.line_ending) is not bytes:
            raise TypeError("Favorites source line ending must be bytes.")
        if self.line_ending not in _ALLOWED_LINE_ENDINGS:
            raise ValueError("Favorites source line ending is invalid.")
        if b"\r" in self.content or b"\n" in self.content:
            raise ValueError(
                "Favorites source record content must not contain line endings."
            )

        try:
            decoded = tuple(
                part.decode("ascii")
                for part in self.content.split(b"\t")
            )
        except UnicodeDecodeError as error:
            raise ValueError(
                "Favorites source record must contain only ASCII bytes."
            ) from error

        object.__setattr__(self, "command", decoded[0])
        object.__setattr__(self, "fields", decoded[1:])

    @property
    def field_count(self) -> int:
        """Return the total positional field count including the command."""

        return 1 + len(self.fields)

    @property
    def raw_bytes(self) -> bytes:
        """Return this physical line exactly as parsed."""

        return self.content + self.line_ending


@dataclass(frozen=True, slots=True)
class FavoritesSourceFile:
    """Ordered immutable Favorites records with exact byte reconstruction."""

    records: tuple[FavoritesSourceRecord, ...]

    def __post_init__(self) -> None:
        if type(self.records) is not tuple:
            raise TypeError("Favorites source records must be a tuple.")
        if any(
            not isinstance(record, FavoritesSourceRecord)
            for record in self.records
        ):
            raise TypeError(
                "Favorites source file contains an invalid record."
            )
        if any(
            record.line_ending == b""
            for record in self.records[:-1]
        ):
            raise ValueError(
                "Only the final Favorites source record may omit a line ending."
            )

    def to_bytes(self) -> bytes:
        """Reconstruct the complete source bytes without normalization."""

        return b"".join(
            record.raw_bytes
            for record in self.records
        )


def parse_favorites_file(data: bytes) -> FavoritesSourceFile:
    """Parse one ASCII Favorites source file without applying semantics."""

    if type(data) is not bytes:
        raise TypeError("Favorites source file data must be bytes.")

    records: list[FavoritesSourceRecord] = []

    for line_number, (content, line_ending) in enumerate(
        _split_source_lines(data),
        start=1,
    ):
        try:
            record = FavoritesSourceRecord(
                content=content,
                line_ending=line_ending,
            )
        except ValueError as error:
            raise FavoritesFileParseError(
                line_number,
                str(error),
            ) from error

        records.append(record)

    return FavoritesSourceFile(records=tuple(records))


def _split_source_lines(
    data: bytes,
) -> Iterator[tuple[bytes, bytes]]:
    start = 0
    index = 0
    length = len(data)

    while index < length:
        value = data[index]

        if value == 13:
            content = data[start:index]

            if index + 1 < length and data[index + 1] == 10:
                yield content, b"\r\n"
                index += 2
            else:
                yield content, b"\r"
                index += 1

            start = index
            continue

        if value == 10:
            yield data[start:index], b"\n"
            index += 1
            start = index
            continue

        index += 1

    if start < length:
        yield data[start:], b""


__all__ = [
    "FavoritesFileParseError",
    "FavoritesSourceFile",
    "FavoritesSourceRecord",
    "parse_favorites_file",
]
