"""Pure renderer-neutral Favorites record editing."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .favorites_file import (
    FavoritesSourceFile,
    FavoritesSourceRecord,
)
from .favorites_schema import (
    FavoritesSchemaSeverity,
    validate_favorites_workspace,
)
from .favorites_storage import (
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
    project_favorites_storage_snapshot,
)
from .favorites_workspace import FavoritesWorkspace


class FavoritesRecordSourceKind(StrEnum):
    """Identify the exact source file containing one editable record."""

    CATALOG = "catalog"
    HPD = "hpd"


class FavoritesRecordEditError(ValueError):
    """Report an unsafe, stale, or unsupported Favorites record edit."""


@dataclass(frozen=True, slots=True)
class FavoritesRecordTarget:
    """Address one exact source record with stale-target evidence."""

    source_kind: FavoritesRecordSourceKind
    source_index: int
    record: FavoritesSourceRecord
    document_index: int | None = None
    filename: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(
            self.source_kind,
            FavoritesRecordSourceKind,
        ):
            raise TypeError(
                "Favorites record target source kind must be "
                "FavoritesRecordSourceKind."
            )

        if (
            type(self.source_index) is not int
            or self.source_index < 0
        ):
            raise ValueError(
                "Favorites record target source index must be "
                "a non-negative integer."
            )

        if not isinstance(
            self.record,
            FavoritesSourceRecord,
        ):
            raise TypeError(
                "Favorites record target record must be "
                "FavoritesSourceRecord."
            )

        if self.source_kind is FavoritesRecordSourceKind.CATALOG:
            if self.document_index is not None:
                raise ValueError(
                    "Favorites catalog record target must not have "
                    "a document index."
                )

            if self.filename is not None:
                raise ValueError(
                    "Favorites catalog record target must not have "
                    "an HPD filename."
                )

            return

        if (
            type(self.document_index) is not int
            or self.document_index < 0
        ):
            raise ValueError(
                "Favorites HPD record target document index must be "
                "a non-negative integer."
            )

        if type(self.filename) is not str or not self.filename:
            raise ValueError(
                "Favorites HPD record target filename must be "
                "a non-empty string."
            )


_NAME_FIELD_INDEX = {
    "F-List": 0,
    "Conventional": 2,
    "Trunk": 2,
    "UnitIds": 2,
    "Site": 2,
    "C-Group": 2,
    "T-Group": 2,
    "C-Freq": 2,
    "TGID": 2,
}

_DELETABLE_HPD_COMMANDS = frozenset(
    {
        "C-Freq",
        "TGID",
        "T-Freq",
        "BandPlan_Mot",
        "BandPlan_P25",
    }
)

_CREATABLE_HPD_COMMANDS = _DELETABLE_HPD_COMMANDS

_CREATE_AFTER_COMMANDS = {
    "C-Freq": frozenset(
        {
            "C-Group",
            "C-Freq",
        }
    ),
    "TGID": frozenset(
        {
            "T-Group",
            "TGID",
        }
    ),
    "T-Freq": frozenset(
        {
            "Site",
            "T-Freq",
            "BandPlan_Mot",
            "BandPlan_P25",
        }
    ),
    "BandPlan_Mot": frozenset(
        {
            "Site",
            "T-Freq",
            "BandPlan_Mot",
            "BandPlan_P25",
        }
    ),
    "BandPlan_P25": frozenset(
        {
            "Site",
            "T-Freq",
            "BandPlan_Mot",
            "BandPlan_P25",
        }
    ),
}


def _require_snapshot(
    snapshot: FavoritesStorageSnapshot,
) -> None:
    if not isinstance(
        snapshot,
        FavoritesStorageSnapshot,
    ):
        raise TypeError(
            "Favorites record editing requires "
            "FavoritesStorageSnapshot."
        )


def _require_unambiguous_catalog_entry(
    workspace: FavoritesWorkspace,
    source_index: int,
) -> None:
    entry = next(
        (
            candidate
            for candidate in workspace.catalog.entries
            if candidate.source_index == source_index
        ),
        None,
    )

    if entry is None:
        return

    if entry.filename in workspace.duplicate_catalog_filenames:
        raise FavoritesRecordEditError(
            "Favorites record target belongs to a duplicate catalog "
            "filename and is ambiguous for mutation."
        )

    if entry.filename in workspace.duplicate_document_filenames:
        raise FavoritesRecordEditError(
            "Favorites record target resolves to duplicate HPD "
            "documents and is ambiguous for mutation."
        )

    if entry in workspace.missing_entries:
        raise FavoritesRecordEditError(
            "Favorites record target mapped HPD document is missing."
        )

    if entry in workspace.ambiguous_entries:
        raise FavoritesRecordEditError(
            "Favorites record target mapped HPD document is ambiguous."
        )


def _require_unambiguous_document(
    workspace: FavoritesWorkspace,
    document_index: int,
) -> None:
    document = workspace.documents[document_index]
    filename = document.filename

    if filename in workspace.duplicate_document_filenames:
        raise FavoritesRecordEditError(
            "Favorites record target belongs to duplicate HPD "
            "documents and is ambiguous for mutation."
        )

    if filename in workspace.duplicate_catalog_filenames:
        raise FavoritesRecordEditError(
            "Favorites record target is referenced by duplicate "
            "catalog filenames and is ambiguous for mutation."
        )

    bindings = tuple(
        binding
        for binding in workspace.bindings
        if binding.document is document
    )

    if not bindings:
        raise FavoritesRecordEditError(
            "Favorites record target HPD document is not bound to "
            "a catalog entry."
        )

    if len(bindings) != 1:
        raise FavoritesRecordEditError(
            "Favorites record target HPD document has ambiguous "
            "catalog ownership."
        )


def _select_target(
    snapshot: FavoritesStorageSnapshot,
    source_index: int,
    *,
    document_index: int | None,
) -> tuple[FavoritesRecordTarget, FavoritesSourceFile]:
    _require_snapshot(snapshot)

    if (
        type(source_index) is not int
        or source_index < 0
    ):
        raise ValueError(
            "Favorites record source index must be "
            "a non-negative integer."
        )

    workspace = project_favorites_storage_snapshot(
        snapshot
    )

    if document_index is None:
        source = workspace.catalog.source

        if source_index >= len(source.records):
            raise FavoritesRecordEditError(
                "Favorites catalog source position does not exist."
            )

        _require_unambiguous_catalog_entry(
            workspace,
            source_index,
        )

        return (
            FavoritesRecordTarget(
                source_kind=FavoritesRecordSourceKind.CATALOG,
                source_index=source_index,
                record=source.records[source_index],
            ),
            source,
        )

    if (
        type(document_index) is not int
        or document_index < 0
    ):
        raise ValueError(
            "Favorites record document index must be "
            "a non-negative integer."
        )

    if document_index >= len(workspace.documents):
        raise FavoritesRecordEditError(
            "Favorites HPD document position does not exist."
        )

    _require_unambiguous_document(
        workspace,
        document_index,
    )

    document = workspace.documents[document_index]
    source = document.hierarchy.source

    if source_index >= len(source.records):
        raise FavoritesRecordEditError(
            "Favorites HPD source position does not exist."
        )

    return (
        FavoritesRecordTarget(
            source_kind=FavoritesRecordSourceKind.HPD,
            document_index=document_index,
            filename=document.filename,
            source_index=source_index,
            record=source.records[source_index],
        ),
        source,
    )


def select_favorites_record_target(
    snapshot: FavoritesStorageSnapshot,
    source_index: int,
    *,
    document_index: int | None = None,
) -> FavoritesRecordTarget:
    """Capture one exact unambiguous record position for a later pure edit."""

    target, _ = _select_target(
        snapshot,
        source_index,
        document_index=document_index,
    )
    return target


def _require_current_target(
    snapshot: FavoritesStorageSnapshot,
    target: FavoritesRecordTarget,
) -> FavoritesSourceFile:
    _require_snapshot(snapshot)

    if not isinstance(
        target,
        FavoritesRecordTarget,
    ):
        raise TypeError(
            "Favorites record editing target must be "
            "FavoritesRecordTarget."
        )

    current, source = _select_target(
        snapshot,
        target.source_index,
        document_index=target.document_index,
    )

    if current.source_kind is not target.source_kind:
        raise FavoritesRecordEditError(
            "Favorites record target source kind changed."
        )

    if current.filename != target.filename:
        raise FavoritesRecordEditError(
            "Favorites record target document no longer matches "
            "its exact filename provenance."
        )

    if current.record != target.record:
        raise FavoritesRecordEditError(
            "Favorites record target no longer matches the exact "
            "source record at its recorded position."
        )

    return source


def _snapshot_with_source(
    snapshot: FavoritesStorageSnapshot,
    target: FavoritesRecordTarget,
    source: FavoritesSourceFile,
) -> FavoritesStorageSnapshot:
    if target.source_kind is FavoritesRecordSourceKind.CATALOG:
        return FavoritesStorageSnapshot(
            catalog_bytes=source.to_bytes(),
            documents=snapshot.documents,
        )

    assert target.document_index is not None

    original = snapshot.documents[
        target.document_index
    ]
    documents = list(snapshot.documents)
    documents[target.document_index] = (
        FavoritesStorageDocument(
            filename=original.filename,
            content=source.to_bytes(),
        )
    )

    return FavoritesStorageSnapshot(
        catalog_bytes=snapshot.catalog_bytes,
        documents=tuple(documents),
    )


def _validate_name(
    name: str,
) -> bytes:
    if type(name) is not str:
        raise TypeError(
            "Favorites record name must be a string."
        )

    if (
        len(name) > 64
        or any(
            not 0x20 <= ord(character) <= 0x7E
            for character in name
        )
    ):
        raise FavoritesRecordEditError(
            "Favorites record name must contain only printable "
            "ASCII characters 0x20-0x7E and must not exceed "
            "64 characters."
        )

    return name.encode("ascii")


def _record_with_name(
    record: FavoritesSourceRecord,
    name: str,
) -> FavoritesSourceRecord:
    field_index = _NAME_FIELD_INDEX.get(
        record.command
    )

    if field_index is None:
        raise FavoritesRecordEditError(
            f"Favorites {record.command!r} record does not have "
            "a supported editable name field."
        )

    if field_index >= len(record.fields):
        raise FavoritesRecordEditError(
            f"Favorites {record.command!r} record does not contain "
            "its supported name field."
        )

    encoded_name = _validate_name(name)
    parts = record.content.split(b"\t")
    parts[field_index + 1] = encoded_name

    return FavoritesSourceRecord(
        content=b"\t".join(parts),
        line_ending=record.line_ending,
    )


def _replace_favorites_record_field(
    snapshot: FavoritesStorageSnapshot,
    target: FavoritesRecordTarget,
    field_index: int,
    value: str,
) -> FavoritesStorageSnapshot:
    """Replace one exact positional field without exposing a public index editor."""

    source = _require_current_target(
        snapshot,
        target,
    )

    if type(field_index) is not int or field_index < 0:
        raise ValueError(
            "Favorites record field index must be a non-negative integer."
        )
    if field_index >= len(target.record.fields):
        raise FavoritesRecordEditError(
            "Favorites record field index is outside the exact target record."
        )
    if type(value) is not str:
        raise TypeError("Favorites record field value must be a string.")

    try:
        encoded_value = value.encode("ascii")
    except UnicodeEncodeError:
        raise FavoritesRecordEditError(
            "Favorites record field value must contain only ASCII characters."
        ) from None

    if any(separator in encoded_value for separator in (b"\t", b"\r", b"\n")):
        raise FavoritesRecordEditError(
            "Favorites record field value must not contain a field or line separator."
        )

    parts = target.record.content.split(b"\t")
    parts[field_index + 1] = encoded_value
    replacement = FavoritesSourceRecord(
        content=b"\t".join(parts),
        line_ending=target.record.line_ending,
    )

    records = list(source.records)
    records[target.source_index] = replacement

    intended = _snapshot_with_source(
        snapshot,
        target,
        FavoritesSourceFile(records=tuple(records)),
    )
    project_favorites_storage_snapshot(intended)
    return intended


def rename_favorites_record(
    snapshot: FavoritesStorageSnapshot,
    target: FavoritesRecordTarget,
    name: str,
) -> FavoritesStorageSnapshot:
    """Replace one evidence-backed name field and preserve all other bytes."""

    source = _require_current_target(
        snapshot,
        target,
    )
    replacement = _record_with_name(
        target.record,
        name,
    )

    records = list(source.records)
    records[target.source_index] = replacement

    intended = _snapshot_with_source(
        snapshot,
        target,
        FavoritesSourceFile(
            records=tuple(records)
        ),
    )

    project_favorites_storage_snapshot(
        intended
    )
    return intended


def delete_favorites_record(
    snapshot: FavoritesStorageSnapshot,
    target: FavoritesRecordTarget,
) -> FavoritesStorageSnapshot:
    """Delete one supported HPD leaf record by exact source provenance."""

    source = _require_current_target(
        snapshot,
        target,
    )

    if target.source_kind is not FavoritesRecordSourceKind.HPD:
        raise FavoritesRecordEditError(
            "Catalog record deletion is not supported by the "
            "record-editing layer."
        )

    if target.record.command not in _DELETABLE_HPD_COMMANDS:
        raise FavoritesRecordEditError(
            f"Favorites {target.record.command!r} deletion is not "
            "supported because it can change hierarchy ownership "
            "or unresolved record semantics."
        )

    records = list(source.records)
    del records[target.source_index]

    intended = _snapshot_with_source(
        snapshot,
        target,
        FavoritesSourceFile(
            records=tuple(records)
        ),
    )

    project_favorites_storage_snapshot(
        intended
    )
    return intended



def create_favorites_record_after(
    snapshot: FavoritesStorageSnapshot,
    anchor: FavoritesRecordTarget,
    template: FavoritesSourceRecord,
    *,
    name: str | None = None,
) -> FavoritesStorageSnapshot:
    """Create one supported HPD leaf record from an exact supplied template."""

    source = _require_current_target(
        snapshot,
        anchor,
    )

    if anchor.source_kind is not FavoritesRecordSourceKind.HPD:
        raise FavoritesRecordEditError(
            "Favorites record creation requires an HPD anchor."
        )

    if not isinstance(
        template,
        FavoritesSourceRecord,
    ):
        raise TypeError(
            "Favorites record creation template must be "
            "FavoritesSourceRecord."
        )

    if template.command not in _CREATABLE_HPD_COMMANDS:
        raise FavoritesRecordEditError(
            f"Favorites {template.command!r} creation is not supported "
            "without evidence-backed leaf-record semantics."
        )

    allowed_anchors = _CREATE_AFTER_COMMANDS[
        template.command
    ]

    if anchor.record.command not in allowed_anchors:
        raise FavoritesRecordEditError(
            f"Favorites {template.command!r} record cannot be created "
            f"after {anchor.record.command!r} without changing or "
            "guessing hierarchy ownership."
        )

    if anchor.record.line_ending == b"":
        raise FavoritesRecordEditError(
            "Favorites record creation cannot insert after a source "
            "record that omits its final line ending without rewriting "
            "that untouched anchor."
        )

    insertion_index = anchor.source_index + 1

    if (
        insertion_index < len(source.records)
        and template.line_ending == b""
    ):
        raise FavoritesRecordEditError(
            "Favorites record creation template must include a line "
            "ending when inserted before existing source records."
        )

    replacement = template

    if name is not None:
        replacement = _record_with_name(
            template,
            name,
        )

    records = list(source.records)
    records.insert(
        insertion_index,
        replacement,
    )

    intended = _snapshot_with_source(
        snapshot,
        anchor,
        FavoritesSourceFile(
            records=tuple(records)
        ),
    )

    workspace = project_favorites_storage_snapshot(
        intended
    )
    validation = validate_favorites_workspace(
        workspace
    )

    created_errors = tuple(
        diagnostic
        for diagnostic in validation.diagnostics
        if (
            diagnostic.severity
            is FavoritesSchemaSeverity.ERROR
            and diagnostic.document_index == anchor.document_index
            and diagnostic.source_index == insertion_index
        )
    )

    if created_errors:
        messages = "; ".join(
            diagnostic.message
            for diagnostic in created_errors
        )
        raise FavoritesRecordEditError(
            "Favorites created record is not schema-valid: "
            f"{messages}"
        )

    return intended


__all__ = [
    "FavoritesRecordEditError",
    "FavoritesRecordSourceKind",
    "FavoritesRecordTarget",
    "create_favorites_record_after",
    "delete_favorites_record",
    "rename_favorites_record",
    "select_favorites_record_target",
]
