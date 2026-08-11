"""Renderer-neutral schema diagnostics over an immutable Favorites workspace."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from .favorites_file import (
    FavoritesSourceFile,
    FavoritesSourceRecord,
)
from .favorites_workspace import FavoritesWorkspace


class FavoritesSchemaSourceKind(StrEnum):
    """Identify which preserved Favorites source produced a diagnostic."""

    CATALOG = "catalog"
    HPD = "hpd"


class FavoritesSchemaSeverity(StrEnum):
    """Classify one stable Favorites schema diagnostic."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class FavoritesSchemaRule(StrEnum):
    """Identify one stable Favorites schema rule."""

    MISSING_REQUIRED_METADATA = "missing_required_metadata"
    INVALID_TARGET_MODEL = "invalid_target_model"
    INVALID_FORMAT_VERSION = "invalid_format_version"
    UNVALIDATED_FORMAT_VERSION = "unvalidated_format_version"
    TOO_FEW_FIELDS = "too_few_fields"
    UNVALIDATED_EXTRA_FIELDS = "unvalidated_extra_fields"
    INVALID_NAME_TAG = "invalid_name_tag"
    UNSUPPORTED_COMMAND = "unsupported_command"


_RULE_SEVERITIES = {
    FavoritesSchemaRule.MISSING_REQUIRED_METADATA: (
        FavoritesSchemaSeverity.ERROR
    ),
    FavoritesSchemaRule.INVALID_TARGET_MODEL: (
        FavoritesSchemaSeverity.ERROR
    ),
    FavoritesSchemaRule.INVALID_FORMAT_VERSION: (
        FavoritesSchemaSeverity.ERROR
    ),
    FavoritesSchemaRule.UNVALIDATED_FORMAT_VERSION: (
        FavoritesSchemaSeverity.WARNING
    ),
    FavoritesSchemaRule.TOO_FEW_FIELDS: FavoritesSchemaSeverity.ERROR,
    FavoritesSchemaRule.UNVALIDATED_EXTRA_FIELDS: (
        FavoritesSchemaSeverity.WARNING
    ),
    FavoritesSchemaRule.INVALID_NAME_TAG: FavoritesSchemaSeverity.ERROR,
    FavoritesSchemaRule.UNSUPPORTED_COMMAND: FavoritesSchemaSeverity.INFO,
}

_FIELD_RULES = frozenset(
    {
        FavoritesSchemaRule.INVALID_TARGET_MODEL,
        FavoritesSchemaRule.INVALID_FORMAT_VERSION,
        FavoritesSchemaRule.UNVALIDATED_FORMAT_VERSION,
        FavoritesSchemaRule.INVALID_NAME_TAG,
    }
)

_REQUIRED_METADATA = (
    "TargetModel",
    "FormatVersion",
)

_REQUIRED_METADATA_ORDER = {
    command: index
    for index, command in enumerate(_REQUIRED_METADATA)
}

_RECORD_RULE_ORDER = {
    FavoritesSchemaRule.TOO_FEW_FIELDS: 0,
    FavoritesSchemaRule.UNVALIDATED_EXTRA_FIELDS: 0,
    FavoritesSchemaRule.UNSUPPORTED_COMMAND: 0,
    FavoritesSchemaRule.INVALID_TARGET_MODEL: 1,
    FavoritesSchemaRule.INVALID_FORMAT_VERSION: 1,
    FavoritesSchemaRule.UNVALIDATED_FORMAT_VERSION: 1,
    FavoritesSchemaRule.INVALID_NAME_TAG: 2,
}


@dataclass(frozen=True, slots=True)
class FavoritesSchemaDiagnostic:
    """One immutable schema diagnostic with exact source provenance."""

    rule: FavoritesSchemaRule
    severity: FavoritesSchemaSeverity
    source_kind: FavoritesSchemaSourceKind
    document_index: int | None
    filename: str | None
    source_index: int | None
    command: str
    field_index: int | None
    record: FavoritesSourceRecord | None
    message: str

    def __post_init__(self) -> None:
        if not isinstance(self.rule, FavoritesSchemaRule):
            raise TypeError(
                "Favorites schema diagnostic rule must be "
                "FavoritesSchemaRule."
            )

        if not isinstance(self.severity, FavoritesSchemaSeverity):
            raise TypeError(
                "Favorites schema diagnostic severity must be "
                "FavoritesSchemaSeverity."
            )

        if self.severity is not _RULE_SEVERITIES[self.rule]:
            raise ValueError(
                "Favorites schema diagnostic severity does not "
                "match its rule."
            )

        if not isinstance(self.source_kind, FavoritesSchemaSourceKind):
            raise TypeError(
                "Favorites schema diagnostic source kind must be "
                "FavoritesSchemaSourceKind."
            )

        if self.source_kind is FavoritesSchemaSourceKind.CATALOG:
            if self.document_index is not None:
                raise ValueError(
                    "Favorites catalog schema diagnostic must not "
                    "have a document index."
                )

            if self.filename is not None:
                raise ValueError(
                    "Favorites catalog schema diagnostic must not "
                    "have an HPD filename."
                )
        else:
            if (
                type(self.document_index) is not int
                or self.document_index < 0
            ):
                raise ValueError(
                    "Favorites HPD schema diagnostic document index "
                    "must be a non-negative integer."
                )

            if not isinstance(self.filename, str):
                raise TypeError(
                    "Favorites HPD schema diagnostic filename must "
                    "be str."
                )

        if not isinstance(self.command, str):
            raise TypeError(
                "Favorites schema diagnostic command must be str."
            )

        if (self.source_index is None) != (self.record is None):
            raise ValueError(
                "Favorites schema diagnostic source index and record "
                "must be present or absent together."
            )

        if self.source_index is not None:
            if (
                type(self.source_index) is not int
                or self.source_index < 0
            ):
                raise ValueError(
                    "Favorites schema diagnostic source index must "
                    "be a non-negative integer."
                )

            if not isinstance(self.record, FavoritesSourceRecord):
                raise TypeError(
                    "Favorites schema diagnostic record must be "
                    "FavoritesSourceRecord."
                )

            if self.command != self.record.command:
                raise ValueError(
                    "Favorites schema diagnostic command must exactly "
                    "match its source record command."
                )

        if self.field_index is not None:
            if self.record is None:
                raise ValueError(
                    "Favorites schema diagnostic field index requires "
                    "a source record."
                )

            if (
                type(self.field_index) is not int
                or self.field_index < 0
                or self.field_index >= len(self.record.fields)
            ):
                raise ValueError(
                    "Favorites schema diagnostic field index must "
                    "address record.fields."
                )

        if self.rule is FavoritesSchemaRule.MISSING_REQUIRED_METADATA:
            if self.command not in _REQUIRED_METADATA:
                raise ValueError(
                    "Missing metadata diagnostic command must be "
                    "TargetModel or FormatVersion."
                )

            if (
                self.source_index is not None
                or self.record is not None
                or self.field_index is not None
            ):
                raise ValueError(
                    "Missing metadata diagnostic must be file-level."
                )
        elif self.record is None:
            raise ValueError(
                "Favorites schema record diagnostic requires an "
                "original source record."
            )

        if self.rule in _FIELD_RULES:
            if self.field_index is None:
                raise ValueError(
                    "Favorites schema field rule requires a field "
                    "index."
                )
        elif self.field_index is not None:
            raise ValueError(
                "Favorites schema non-field rule must not have a "
                "field index."
            )

        if not isinstance(self.message, str):
            raise TypeError(
                "Favorites schema diagnostic message must be str."
            )

        if not self.message:
            raise ValueError(
                "Favorites schema diagnostic message must not be "
                "empty."
            )


def _diagnostic_source(
    workspace: FavoritesWorkspace,
    diagnostic: FavoritesSchemaDiagnostic,
) -> FavoritesSourceFile:
    if diagnostic.source_kind is FavoritesSchemaSourceKind.CATALOG:
        return workspace.catalog.source

    document_index = diagnostic.document_index

    if (
        type(document_index) is not int
        or document_index >= len(workspace.documents)
    ):
        raise ValueError(
            "Favorites schema diagnostic document index is not "
            "present in the validation workspace."
        )

    document = workspace.documents[document_index]

    if diagnostic.filename != document.filename:
        raise ValueError(
            "Favorites schema diagnostic filename must exactly match "
            "its workspace document."
        )

    return document.hierarchy.source


def _diagnostic_order_key(
    diagnostic: FavoritesSchemaDiagnostic,
) -> tuple[int, int, int, int, int]:
    if diagnostic.source_kind is FavoritesSchemaSourceKind.CATALOG:
        source_kind_order = 0
        document_order = 0
    else:
        source_kind_order = 1
        document_order = (
            diagnostic.document_index
            if diagnostic.document_index is not None
            else -1
        )

    if diagnostic.rule is FavoritesSchemaRule.MISSING_REQUIRED_METADATA:
        return (
            source_kind_order,
            document_order,
            0,
            _REQUIRED_METADATA_ORDER[diagnostic.command],
            0,
        )

    source_index = (
        diagnostic.source_index
        if diagnostic.source_index is not None
        else -1
    )

    return (
        source_kind_order,
        document_order,
        1,
        source_index,
        _RECORD_RULE_ORDER[diagnostic.rule],
    )


def _validate_diagnostic_provenance(
    workspace: FavoritesWorkspace,
    diagnostic: FavoritesSchemaDiagnostic,
) -> None:
    source = _diagnostic_source(
        workspace,
        diagnostic,
    )

    if diagnostic.source_index is not None:
        if diagnostic.source_index >= len(source.records):
            raise ValueError(
                "Favorites schema diagnostic source index is not "
                "present in its source file."
            )

        expected_record = source.records[diagnostic.source_index]

        if diagnostic.record is not expected_record:
            raise ValueError(
                "Favorites schema diagnostic record must be the exact "
                "original source record at its source index."
            )

    if (
        diagnostic.rule is FavoritesSchemaRule.MISSING_REQUIRED_METADATA
        and any(
            record.command == diagnostic.command
            for record in source.records
        )
    ):
        raise ValueError(
            "Missing metadata diagnostic cannot reference metadata "
            "that is present in its source."
        )


@dataclass(frozen=True, slots=True)
class FavoritesSchemaValidation:
    """Immutable validation result retaining its authoritative workspace."""

    workspace: FavoritesWorkspace
    diagnostics: tuple[FavoritesSchemaDiagnostic, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.workspace, FavoritesWorkspace):
            raise TypeError(
                "Favorites schema validation workspace must be "
                "FavoritesWorkspace."
            )

        if type(self.diagnostics) is not tuple:
            raise TypeError(
                "Favorites schema validation diagnostics must be a "
                "tuple."
            )

        if any(
            not isinstance(
                diagnostic,
                FavoritesSchemaDiagnostic,
            )
            for diagnostic in self.diagnostics
        ):
            raise TypeError(
                "Favorites schema validation diagnostics must contain "
                "FavoritesSchemaDiagnostic values."
            )

        previous_key: tuple[int, int, int, int, int] | None = None

        for diagnostic in self.diagnostics:
            _validate_diagnostic_provenance(
                self.workspace,
                diagnostic,
            )

            order_key = _diagnostic_order_key(
                diagnostic
            )

            if (
                previous_key is not None
                and order_key < previous_key
            ):
                raise ValueError(
                    "Favorites schema diagnostics must preserve "
                    "canonical source and rule order."
                )

            previous_key = order_key

    @property
    def is_valid(self) -> bool:
        """Return whether validation contains no error diagnostics."""

        return not any(
            diagnostic.severity
            is FavoritesSchemaSeverity.ERROR
            for diagnostic in self.diagnostics
        )


@dataclass(frozen=True, slots=True)
class _FixedShape:
    minimum: int
    accepted_counts: frozenset[int]


@dataclass(frozen=True, slots=True)
class _RangeShape:
    minimum: int
    maximum: int


_Shape = _FixedShape | _RangeShape

_CATALOG_SHAPES: dict[str, _Shape] = {
    "TargetModel": _FixedShape(
        minimum=2,
        accepted_counts=frozenset({2}),
    ),
    "FormatVersion": _FixedShape(
        minimum=2,
        accepted_counts=frozenset({2}),
    ),
    "F-List": _FixedShape(
        minimum=117,
        accepted_counts=frozenset({117}),
    ),
}

_HPD_SHAPES: dict[str, _Shape] = {
    "TargetModel": _FixedShape(
        minimum=2,
        accepted_counts=frozenset({2}),
    ),
    "FormatVersion": _FixedShape(
        minimum=2,
        accepted_counts=frozenset({2}),
    ),
    "Conventional": _FixedShape(
        minimum=15,
        accepted_counts=frozenset({15}),
    ),
    "Trunk": _FixedShape(
        minimum=22,
        accepted_counts=frozenset({22}),
    ),
    "AreaState": _FixedShape(
        minimum=3,
        accepted_counts=frozenset({3}),
    ),
    "AreaCounty": _FixedShape(
        minimum=3,
        accepted_counts=frozenset({3}),
    ),
    "FleetMap": _FixedShape(
        minimum=10,
        accepted_counts=frozenset({10}),
    ),
    "UnitIds": _FixedShape(
        minimum=9,
        accepted_counts=frozenset({9}),
    ),
    "UnitID": _FixedShape(
        minimum=1,
        accepted_counts=frozenset({1}),
    ),
    "Site": _FixedShape(
        minimum=19,
        accepted_counts=frozenset({19}),
    ),
    "Rectangle": _FixedShape(
        minimum=6,
        accepted_counts=frozenset({6}),
    ),
    "BandPlan_Mot": _FixedShape(
        minimum=26,
        accepted_counts=frozenset({26}),
    ),
    "BandPlan_P25": _FixedShape(
        minimum=34,
        accepted_counts=frozenset(
            {
                34,
                50,
            }
        ),
    ),
    "DQKs_Status": _FixedShape(
        minimum=102,
        accepted_counts=frozenset({102}),
    ),
    "C-Group": _FixedShape(
        minimum=11,
        accepted_counts=frozenset({11}),
    ),
    "T-Group": _FixedShape(
        minimum=10,
        accepted_counts=frozenset({10}),
    ),
    "C-Freq": _FixedShape(
        minimum=18,
        accepted_counts=frozenset({18}),
    ),
    "TGID": _FixedShape(
        minimum=17,
        accepted_counts=frozenset(
            {
                17,
                18,
            }
        ),
    ),
    "T-Freq": _FixedShape(
        minimum=8,
        accepted_counts=frozenset(
            {
                8,
                9,
            }
        ),
    ),
    "AvoidTgids": _RangeShape(
        minimum=3,
        maximum=18,
    ),
}

_NAME_FIELDS = {
    FavoritesSchemaSourceKind.CATALOG: {
        "F-List": 0,
    },
    FavoritesSchemaSourceKind.HPD: {
        "Conventional": 2,
        "Trunk": 2,
        "UnitIds": 2,
        "Site": 2,
        "C-Group": 2,
        "T-Group": 2,
        "C-Freq": 2,
        "TGID": 2,
    },
}

_FORMAT_VERSION_PATTERN = re.compile(
    r"[0-9][.][0-9]{2}"
)


def _make_diagnostic(
    *,
    rule: FavoritesSchemaRule,
    source_kind: FavoritesSchemaSourceKind,
    document_index: int | None,
    filename: str | None,
    source_index: int | None,
    command: str,
    field_index: int | None,
    record: FavoritesSourceRecord | None,
    message: str,
) -> FavoritesSchemaDiagnostic:
    return FavoritesSchemaDiagnostic(
        rule=rule,
        severity=_RULE_SEVERITIES[rule],
        source_kind=source_kind,
        document_index=document_index,
        filename=filename,
        source_index=source_index,
        command=command,
        field_index=field_index,
        record=record,
        message=message,
    )


def _shape_diagnostic(
    *,
    shape: _Shape,
    source_kind: FavoritesSchemaSourceKind,
    document_index: int | None,
    filename: str | None,
    source_index: int,
    record: FavoritesSourceRecord,
) -> FavoritesSchemaDiagnostic | None:
    count = record.field_count

    if count < shape.minimum:
        return _make_diagnostic(
            rule=FavoritesSchemaRule.TOO_FEW_FIELDS,
            source_kind=source_kind,
            document_index=document_index,
            filename=filename,
            source_index=source_index,
            command=record.command,
            field_index=None,
            record=record,
            message=(
                f"{record.command} has {count} fields including "
                f"the command; supported minimum is {shape.minimum}."
            ),
        )

    if isinstance(shape, _FixedShape):
        accepted = count in shape.accepted_counts
    else:
        accepted = count <= shape.maximum

    if accepted:
        return None

    return _make_diagnostic(
        rule=FavoritesSchemaRule.UNVALIDATED_EXTRA_FIELDS,
        source_kind=source_kind,
        document_index=document_index,
        filename=filename,
        source_index=source_index,
        command=record.command,
        field_index=None,
        record=record,
        message=(
            f"{record.command} has an unvalidated "
            f"{count}-field shape."
        ),
    )


def _name_is_supported(
    value: str,
) -> bool:
    return (
        len(value) <= 64
        and all(
            0x20 <= ord(character) <= 0x7E
            for character in value
        )
    )


def _validate_source(
    source: FavoritesSourceFile,
    *,
    source_kind: FavoritesSchemaSourceKind,
    document_index: int | None,
    filename: str | None,
) -> tuple[FavoritesSchemaDiagnostic, ...]:
    diagnostics: list[FavoritesSchemaDiagnostic] = []

    present_commands = frozenset(
        record.command
        for record in source.records
    )

    for command in _REQUIRED_METADATA:
        if command in present_commands:
            continue

        diagnostics.append(
            _make_diagnostic(
                rule=FavoritesSchemaRule.MISSING_REQUIRED_METADATA,
                source_kind=source_kind,
                document_index=document_index,
                filename=filename,
                source_index=None,
                command=command,
                field_index=None,
                record=None,
                message=(
                    f"Required {command} metadata record is missing."
                ),
            )
        )

    shapes = (
        _CATALOG_SHAPES
        if source_kind is FavoritesSchemaSourceKind.CATALOG
        else _HPD_SHAPES
    )

    name_fields = _NAME_FIELDS[source_kind]

    for source_index, record in enumerate(source.records):
        shape = shapes.get(record.command)

        if shape is None:
            diagnostics.append(
                _make_diagnostic(
                    rule=FavoritesSchemaRule.UNSUPPORTED_COMMAND,
                    source_kind=source_kind,
                    document_index=document_index,
                    filename=filename,
                    source_index=source_index,
                    command=record.command,
                    field_index=None,
                    record=record,
                    message=(
                        "No Milestone 21.5 schema rule is defined "
                        f"for {record.command!r}."
                    ),
                )
            )
            continue

        shape_diagnostic = _shape_diagnostic(
            shape=shape,
            source_kind=source_kind,
            document_index=document_index,
            filename=filename,
            source_index=source_index,
            record=record,
        )

        if shape_diagnostic is not None:
            diagnostics.append(shape_diagnostic)

        if (
            record.command == "TargetModel"
            and record.fields
            and record.fields[0] != "BCDx36HP"
        ):
            diagnostics.append(
                _make_diagnostic(
                    rule=FavoritesSchemaRule.INVALID_TARGET_MODEL,
                    source_kind=source_kind,
                    document_index=document_index,
                    filename=filename,
                    source_index=source_index,
                    command=record.command,
                    field_index=0,
                    record=record,
                    message=(
                        "TargetModel is not the supported "
                        "BCDx36HP value."
                    ),
                )
            )

        if record.command == "FormatVersion" and record.fields:
            version = record.fields[0]

            if _FORMAT_VERSION_PATTERN.fullmatch(version) is None:
                diagnostics.append(
                    _make_diagnostic(
                        rule=FavoritesSchemaRule.INVALID_FORMAT_VERSION,
                        source_kind=source_kind,
                        document_index=document_index,
                        filename=filename,
                        source_index=source_index,
                        command=record.command,
                        field_index=0,
                        record=record,
                        message=(
                            "FormatVersion is not in supported "
                            "x.xx decimal syntax."
                        ),
                    )
                )
            elif version != "1.00":
                diagnostics.append(
                    _make_diagnostic(
                        rule=(
                            FavoritesSchemaRule.UNVALIDATED_FORMAT_VERSION
                        ),
                        source_kind=source_kind,
                        document_index=document_index,
                        filename=filename,
                        source_index=source_index,
                        command=record.command,
                        field_index=0,
                        record=record,
                        message=(
                            f"FormatVersion {version!r} is not the "
                            "device-validated 1.00 version."
                        ),
                    )
                )

        name_field_index = name_fields.get(
            record.command
        )

        if (
            name_field_index is not None
            and name_field_index < len(record.fields)
        ):
            name = record.fields[name_field_index]

            if not _name_is_supported(name):
                diagnostics.append(
                    _make_diagnostic(
                        rule=FavoritesSchemaRule.INVALID_NAME_TAG,
                        source_kind=source_kind,
                        document_index=document_index,
                        filename=filename,
                        source_index=source_index,
                        command=record.command,
                        field_index=name_field_index,
                        record=record,
                        message=(
                            "Name tag must contain only printable "
                            "ASCII characters 0x20-0x7E and must "
                            "not exceed 64 characters."
                        ),
                    )
                )

    return tuple(diagnostics)


def validate_favorites_workspace(
    workspace: FavoritesWorkspace,
) -> FavoritesSchemaValidation:
    """Validate supported schema rules without changing the workspace."""

    if not isinstance(workspace, FavoritesWorkspace):
        raise TypeError(
            "Favorites schema validation requires FavoritesWorkspace."
        )

    diagnostics: list[FavoritesSchemaDiagnostic] = []

    diagnostics.extend(
        _validate_source(
            workspace.catalog.source,
            source_kind=FavoritesSchemaSourceKind.CATALOG,
            document_index=None,
            filename=None,
        )
    )

    for document_index, document in enumerate(
        workspace.documents
    ):
        diagnostics.extend(
            _validate_source(
                document.hierarchy.source,
                source_kind=FavoritesSchemaSourceKind.HPD,
                document_index=document_index,
                filename=document.filename,
            )
        )

    return FavoritesSchemaValidation(
        workspace=workspace,
        diagnostics=tuple(diagnostics),
    )


__all__ = [
    "FavoritesSchemaDiagnostic",
    "FavoritesSchemaRule",
    "FavoritesSchemaSeverity",
    "FavoritesSchemaSourceKind",
    "FavoritesSchemaValidation",
    "validate_favorites_workspace",
]
