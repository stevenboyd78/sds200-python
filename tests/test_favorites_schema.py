from __future__ import annotations

from dataclasses import (
    FrozenInstanceError,
    replace,
)
from pathlib import Path

import pytest

from sds200 import (
    FavoritesCatalog,
    FavoritesCatalogEntry,
    FavoritesHierarchy,
    FavoritesHierarchyDocument,
    FavoritesSchemaDiagnostic,
    FavoritesSchemaRule,
    FavoritesSchemaSeverity,
    FavoritesSchemaSourceKind,
    FavoritesSchemaValidation,
    FavoritesSourceFile,
    FavoritesSourceRecord,
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
    FavoritesWorkspace,
    parse_favorites_file,
    project_favorites_storage_snapshot,
    validate_favorites_workspace,
)

_FIXTURE_ROOT = (
    Path(__file__).parent / "fixtures" / "favorites"
)

_VALID_METADATA = (
    b"TargetModel\tBCDx36HP",
    b"FormatVersion\t1.00",
)

_SHAPE_RULES = frozenset(
    {
        FavoritesSchemaRule.TOO_FEW_FIELDS,
        FavoritesSchemaRule.UNVALIDATED_EXTRA_FIELDS,
    }
)


def _source(
    records: tuple[bytes, ...],
) -> FavoritesSourceFile:
    if not records:
        return parse_favorites_file(b"")

    return parse_favorites_file(
        b"\r\n".join(records) + b"\r\n"
    )


def _workspace(
    *,
    catalog_records: tuple[bytes, ...] = _VALID_METADATA,
    documents: tuple[
        tuple[
            str,
            tuple[bytes, ...],
        ],
        ...,
    ] = (),
) -> FavoritesWorkspace:
    catalog = FavoritesCatalog(
        source=_source(catalog_records),
        metadata_indexes=(),
        entries=(),
        unclassified_indexes=(),
    )

    hierarchy_documents = tuple(
        FavoritesHierarchyDocument(
            filename=filename,
            hierarchy=FavoritesHierarchy(
                source=_source(records),
                metadata_records=(),
                systems=(),
                unclassified_records=(),
            ),
        )
        for filename, records in documents
    )

    return FavoritesWorkspace(
        catalog=catalog,
        documents=hierarchy_documents,
        bindings=(),
        missing_entries=(),
        ambiguous_entries=(),
        duplicate_catalog_filenames=(),
        duplicate_document_filenames=(),
        orphan_documents=(),
    )


def _record_with_count(
    command: str,
    count: int,
    *,
    fields: dict[int, str] | None = None,
) -> bytes:
    assert count >= 1

    values = [
        ""
        for _ in range(count - 1)
    ]

    if fields is not None:
        for index, value in fields.items():
            values[index] = value

    return "\t".join(
        (
            command,
            *values,
        )
    ).encode("ascii")


def _shape_diagnostics(
    validation: FavoritesSchemaValidation,
    command: str,
) -> tuple[FavoritesSchemaDiagnostic, ...]:
    return tuple(
        diagnostic
        for diagnostic in validation.diagnostics
        if (
            diagnostic.command == command
            and diagnostic.rule in _SHAPE_RULES
        )
    )


def test_public_enum_values_are_stable() -> None:
    assert tuple(
        item.value
        for item in FavoritesSchemaSourceKind
    ) == (
        "catalog",
        "hpd",
    )

    assert tuple(
        item.value
        for item in FavoritesSchemaSeverity
    ) == (
        "error",
        "warning",
        "info",
    )

    assert tuple(
        item.value
        for item in FavoritesSchemaRule
    ) == (
        "missing_required_metadata",
        "invalid_target_model",
        "invalid_format_version",
        "unvalidated_format_version",
        "too_few_fields",
        "unvalidated_extra_fields",
        "invalid_name_tag",
        "unsupported_command",
    )


def test_diagnostic_is_frozen_and_slot_backed() -> None:
    record = FavoritesSourceRecord(
        content=b"FormatVersion\t2.00",
        line_ending=b"\r\n",
    )

    diagnostic = FavoritesSchemaDiagnostic(
        rule=FavoritesSchemaRule.UNVALIDATED_FORMAT_VERSION,
        severity=FavoritesSchemaSeverity.WARNING,
        source_kind=FavoritesSchemaSourceKind.HPD,
        document_index=0,
        filename="x.hpd",
        source_index=0,
        command="FormatVersion",
        field_index=0,
        record=record,
        message="Version has not been validated.",
    )

    assert diagnostic.__dataclass_params__.frozen is True
    assert "__slots__" in FavoritesSchemaDiagnostic.__dict__
    assert "__dict__" not in FavoritesSchemaDiagnostic.__dict__

    with pytest.raises(FrozenInstanceError):
        diagnostic.message = "changed"  # type: ignore[misc]


def test_diagnostic_rejects_rule_severity_mismatch() -> None:
    record = FavoritesSourceRecord(
        content=b"FormatVersion\tbad",
        line_ending=b"\r\n",
    )

    with pytest.raises(
        ValueError,
        match="severity does not match",
    ):
        FavoritesSchemaDiagnostic(
            rule=FavoritesSchemaRule.INVALID_FORMAT_VERSION,
            severity=FavoritesSchemaSeverity.WARNING,
            source_kind=FavoritesSchemaSourceKind.CATALOG,
            document_index=None,
            filename=None,
            source_index=0,
            command="FormatVersion",
            field_index=0,
            record=record,
            message="Wrong severity.",
        )


def test_catalog_diagnostic_rejects_document_coordinates() -> None:
    with pytest.raises(
        ValueError,
        match="must not have a document index",
    ):
        FavoritesSchemaDiagnostic(
            rule=FavoritesSchemaRule.MISSING_REQUIRED_METADATA,
            severity=FavoritesSchemaSeverity.ERROR,
            source_kind=FavoritesSchemaSourceKind.CATALOG,
            document_index=0,
            filename=None,
            source_index=None,
            command="TargetModel",
            field_index=None,
            record=None,
            message="Missing.",
        )

    with pytest.raises(
        ValueError,
        match="must not have an HPD filename",
    ):
        FavoritesSchemaDiagnostic(
            rule=FavoritesSchemaRule.MISSING_REQUIRED_METADATA,
            severity=FavoritesSchemaSeverity.ERROR,
            source_kind=FavoritesSchemaSourceKind.CATALOG,
            document_index=None,
            filename="x.hpd",
            source_index=None,
            command="TargetModel",
            field_index=None,
            record=None,
            message="Missing.",
        )


def test_hpd_diagnostic_requires_document_coordinates() -> None:
    with pytest.raises(
        ValueError,
        match="document index",
    ):
        FavoritesSchemaDiagnostic(
            rule=FavoritesSchemaRule.MISSING_REQUIRED_METADATA,
            severity=FavoritesSchemaSeverity.ERROR,
            source_kind=FavoritesSchemaSourceKind.HPD,
            document_index=None,
            filename="x.hpd",
            source_index=None,
            command="TargetModel",
            field_index=None,
            record=None,
            message="Missing.",
        )

    with pytest.raises(
        TypeError,
        match="filename must be str",
    ):
        FavoritesSchemaDiagnostic(
            rule=FavoritesSchemaRule.MISSING_REQUIRED_METADATA,
            severity=FavoritesSchemaSeverity.ERROR,
            source_kind=FavoritesSchemaSourceKind.HPD,
            document_index=0,
            filename=None,
            source_index=None,
            command="TargetModel",
            field_index=None,
            record=None,
            message="Missing.",
        )


def test_diagnostic_rejects_bool_indexes() -> None:
    record = FavoritesSourceRecord(
        content=b"Future",
        line_ending=b"\r\n",
    )

    with pytest.raises(
        ValueError,
        match="document index",
    ):
        FavoritesSchemaDiagnostic(
            rule=FavoritesSchemaRule.UNSUPPORTED_COMMAND,
            severity=FavoritesSchemaSeverity.INFO,
            source_kind=FavoritesSchemaSourceKind.HPD,
            document_index=True,  # type: ignore[arg-type]
            filename="x.hpd",
            source_index=0,
            command="Future",
            field_index=None,
            record=record,
            message="Unsupported.",
        )

    with pytest.raises(
        ValueError,
        match="source index",
    ):
        FavoritesSchemaDiagnostic(
            rule=FavoritesSchemaRule.UNSUPPORTED_COMMAND,
            severity=FavoritesSchemaSeverity.INFO,
            source_kind=FavoritesSchemaSourceKind.CATALOG,
            document_index=None,
            filename=None,
            source_index=True,  # type: ignore[arg-type]
            command="Future",
            field_index=None,
            record=record,
            message="Unsupported.",
        )

    named = FavoritesSourceRecord(
        content=b"F-List\tName",
        line_ending=b"\r\n",
    )

    with pytest.raises(
        ValueError,
        match="field index",
    ):
        FavoritesSchemaDiagnostic(
            rule=FavoritesSchemaRule.INVALID_NAME_TAG,
            severity=FavoritesSchemaSeverity.ERROR,
            source_kind=FavoritesSchemaSourceKind.CATALOG,
            document_index=None,
            filename=None,
            source_index=0,
            command="F-List",
            field_index=True,  # type: ignore[arg-type]
            record=named,
            message="Invalid name.",
        )


def test_source_index_and_record_are_paired() -> None:
    record = FavoritesSourceRecord(
        content=b"Future",
        line_ending=b"\r\n",
    )

    with pytest.raises(
        ValueError,
        match="present or absent together",
    ):
        FavoritesSchemaDiagnostic(
            rule=FavoritesSchemaRule.UNSUPPORTED_COMMAND,
            severity=FavoritesSchemaSeverity.INFO,
            source_kind=FavoritesSchemaSourceKind.CATALOG,
            document_index=None,
            filename=None,
            source_index=None,
            command="Future",
            field_index=None,
            record=record,
            message="Unsupported.",
        )


def test_record_diagnostic_command_must_match_record() -> None:
    record = FavoritesSourceRecord(
        content=b"Future",
        line_ending=b"\r\n",
    )

    with pytest.raises(
        ValueError,
        match="exactly match",
    ):
        FavoritesSchemaDiagnostic(
            rule=FavoritesSchemaRule.UNSUPPORTED_COMMAND,
            severity=FavoritesSchemaSeverity.INFO,
            source_kind=FavoritesSchemaSourceKind.CATALOG,
            document_index=None,
            filename=None,
            source_index=0,
            command="Other",
            field_index=None,
            record=record,
            message="Unsupported.",
        )


def test_missing_metadata_is_file_level_only() -> None:
    record = FavoritesSourceRecord(
        content=b"TargetModel",
        line_ending=b"\r\n",
    )

    with pytest.raises(
        ValueError,
        match="file-level",
    ):
        FavoritesSchemaDiagnostic(
            rule=FavoritesSchemaRule.MISSING_REQUIRED_METADATA,
            severity=FavoritesSchemaSeverity.ERROR,
            source_kind=FavoritesSchemaSourceKind.CATALOG,
            document_index=None,
            filename=None,
            source_index=0,
            command="TargetModel",
            field_index=None,
            record=record,
            message="Missing.",
        )


def test_field_rules_require_record_field_index() -> None:
    record = FavoritesSourceRecord(
        content=b"FormatVersion\tbad",
        line_ending=b"\r\n",
    )

    with pytest.raises(
        ValueError,
        match="field rule requires",
    ):
        FavoritesSchemaDiagnostic(
            rule=FavoritesSchemaRule.INVALID_FORMAT_VERSION,
            severity=FavoritesSchemaSeverity.ERROR,
            source_kind=FavoritesSchemaSourceKind.CATALOG,
            document_index=None,
            filename=None,
            source_index=0,
            command="FormatVersion",
            field_index=None,
            record=record,
            message="Invalid.",
        )

    with pytest.raises(
        ValueError,
        match="non-field rule",
    ):
        FavoritesSchemaDiagnostic(
            rule=FavoritesSchemaRule.UNVALIDATED_EXTRA_FIELDS,
            severity=FavoritesSchemaSeverity.WARNING,
            source_kind=FavoritesSchemaSourceKind.CATALOG,
            document_index=None,
            filename=None,
            source_index=0,
            command="FormatVersion",
            field_index=0,
            record=record,
            message="Extra.",
        )


def test_field_index_addresses_record_fields_not_command() -> None:
    record = FavoritesSourceRecord(
        content=b"C-Freq\tMyId\tParentId\tName",
        line_ending=b"\r\n",
    )

    diagnostic = FavoritesSchemaDiagnostic(
        rule=FavoritesSchemaRule.INVALID_NAME_TAG,
        severity=FavoritesSchemaSeverity.ERROR,
        source_kind=FavoritesSchemaSourceKind.HPD,
        document_index=0,
        filename="x.hpd",
        source_index=0,
        command="C-Freq",
        field_index=2,
        record=record,
        message="Invalid.",
    )

    assert record.fields[diagnostic.field_index] == "Name"

    with pytest.raises(
        ValueError,
        match="address record.fields",
    ):
        replace(
            diagnostic,
            field_index=3,
        )


def test_validation_is_frozen_and_slot_backed() -> None:
    workspace = _workspace()
    validation = FavoritesSchemaValidation(
        workspace=workspace,
        diagnostics=(),
    )

    assert validation.__dataclass_params__.frozen is True
    assert "__slots__" in FavoritesSchemaValidation.__dict__
    assert "__dict__" not in FavoritesSchemaValidation.__dict__

    with pytest.raises(FrozenInstanceError):
        validation.diagnostics = ()  # type: ignore[misc]


def test_validation_requires_workspace_and_exact_tuple() -> None:
    with pytest.raises(
        TypeError,
        match="workspace must be FavoritesWorkspace",
    ):
        FavoritesSchemaValidation(  # type: ignore[arg-type]
            workspace=object(),
            diagnostics=(),
        )

    with pytest.raises(
        TypeError,
        match="diagnostics must be a tuple",
    ):
        FavoritesSchemaValidation(  # type: ignore[arg-type]
            workspace=_workspace(),
            diagnostics=[],
        )


def test_validation_requires_exact_catalog_record_identity() -> None:
    workspace = _workspace(
        catalog_records=(
            b"TargetModel\tBCDx36HP",
            b"FormatVersion\t2.00",
        )
    )

    original = workspace.catalog.source.records[1]

    equal_copy = FavoritesSourceRecord(
        content=original.content,
        line_ending=original.line_ending,
    )

    assert equal_copy == original
    assert equal_copy is not original

    diagnostic = FavoritesSchemaDiagnostic(
        rule=FavoritesSchemaRule.UNVALIDATED_FORMAT_VERSION,
        severity=FavoritesSchemaSeverity.WARNING,
        source_kind=FavoritesSchemaSourceKind.CATALOG,
        document_index=None,
        filename=None,
        source_index=1,
        command="FormatVersion",
        field_index=0,
        record=equal_copy,
        message="Unvalidated version.",
    )

    with pytest.raises(
        ValueError,
        match="exact original source record",
    ):
        FavoritesSchemaValidation(
            workspace=workspace,
            diagnostics=(diagnostic,),
        )


def test_validation_requires_exact_hpd_filename_and_record() -> None:
    workspace = _workspace(
        documents=(
            (
                "one.hpd",
                (
                    b"TargetModel\tBCDx36HP",
                    b"FormatVersion\t2.00",
                ),
            ),
        )
    )

    record = (
        workspace.documents[0]
        .hierarchy.source.records[1]
    )

    diagnostic = FavoritesSchemaDiagnostic(
        rule=FavoritesSchemaRule.UNVALIDATED_FORMAT_VERSION,
        severity=FavoritesSchemaSeverity.WARNING,
        source_kind=FavoritesSchemaSourceKind.HPD,
        document_index=0,
        filename="wrong.hpd",
        source_index=1,
        command="FormatVersion",
        field_index=0,
        record=record,
        message="Unvalidated version.",
    )

    with pytest.raises(
        ValueError,
        match="filename must exactly match",
    ):
        FavoritesSchemaValidation(
            workspace=workspace,
            diagnostics=(diagnostic,),
        )


def test_missing_metadata_diagnostic_must_be_actually_missing() -> None:
    workspace = _workspace()

    diagnostic = FavoritesSchemaDiagnostic(
        rule=FavoritesSchemaRule.MISSING_REQUIRED_METADATA,
        severity=FavoritesSchemaSeverity.ERROR,
        source_kind=FavoritesSchemaSourceKind.CATALOG,
        document_index=None,
        filename=None,
        source_index=None,
        command="TargetModel",
        field_index=None,
        record=None,
        message="Missing.",
    )

    with pytest.raises(
        ValueError,
        match="metadata that is present",
    ):
        FavoritesSchemaValidation(
            workspace=workspace,
            diagnostics=(diagnostic,),
        )


def test_validation_rejects_backward_diagnostic_order() -> None:
    workspace = _workspace(
        catalog_records=(
            b"TargetModel\tBCDx36HP",
            b"FormatVersion\t2.00",
            b"FutureListSetting\tA",
        )
    )

    result = validate_favorites_workspace(
        workspace
    )

    assert len(result.diagnostics) == 2

    with pytest.raises(
        ValueError,
        match="canonical source and rule order",
    ):
        FavoritesSchemaValidation(
            workspace=workspace,
            diagnostics=tuple(
                reversed(result.diagnostics)
            ),
        )


def test_clean_supported_workspace_has_no_diagnostics() -> None:
    workspace = _workspace(
        documents=(
            (
                "clean.hpd",
                _VALID_METADATA,
            ),
        )
    )

    result = validate_favorites_workspace(
        workspace
    )

    assert result.workspace is workspace
    assert result.diagnostics == ()
    assert result.is_valid is True


def test_sanitized_fixture_reports_future_commands_as_info_only() -> None:
    workspace = project_favorites_storage_snapshot(
        FavoritesStorageSnapshot(
            catalog_bytes=(
                _FIXTURE_ROOT
                / "synthetic-f_list.cfg"
            ).read_bytes(),
            documents=(
                FavoritesStorageDocument(
                    filename="f_000001.hpd",
                    content=(
                        _FIXTURE_ROOT
                        / "synthetic-favorites.hpd"
                    ).read_bytes(),
                ),
            ),
        )
    )

    result = validate_favorites_workspace(
        workspace
    )

    assert result.is_valid is True

    assert tuple(
        (
            diagnostic.severity,
            diagnostic.rule,
            diagnostic.source_kind,
            diagnostic.document_index,
            diagnostic.filename,
            diagnostic.source_index,
            diagnostic.command,
        )
        for diagnostic in result.diagnostics
    ) == (
        (
            FavoritesSchemaSeverity.INFO,
            FavoritesSchemaRule.UNSUPPORTED_COMMAND,
            FavoritesSchemaSourceKind.CATALOG,
            None,
            None,
            3,
            "FutureListSetting",
        ),
        (
            FavoritesSchemaSeverity.INFO,
            FavoritesSchemaRule.UNSUPPORTED_COMMAND,
            FavoritesSchemaSourceKind.HPD,
            0,
            "f_000001.hpd",
            18,
            "FutureCommand",
        ),
    )


def test_missing_metadata_order_is_catalog_then_documents() -> None:
    workspace = _workspace(
        catalog_records=(),
        documents=(
            (
                "empty.hpd",
                (),
            ),
        ),
    )

    result = validate_favorites_workspace(
        workspace
    )

    assert result.is_valid is False

    assert tuple(
        (
            diagnostic.source_kind,
            diagnostic.document_index,
            diagnostic.command,
        )
        for diagnostic in result.diagnostics
    ) == (
        (
            FavoritesSchemaSourceKind.CATALOG,
            None,
            "TargetModel",
        ),
        (
            FavoritesSchemaSourceKind.CATALOG,
            None,
            "FormatVersion",
        ),
        (
            FavoritesSchemaSourceKind.HPD,
            0,
            "TargetModel",
        ),
        (
            FavoritesSchemaSourceKind.HPD,
            0,
            "FormatVersion",
        ),
    )


def test_present_but_short_metadata_is_not_reported_missing() -> None:
    workspace = _workspace(
        catalog_records=(
            b"TargetModel",
            b"FormatVersion",
        )
    )

    result = validate_favorites_workspace(
        workspace
    )

    assert tuple(
        diagnostic.rule
        for diagnostic in result.diagnostics
    ) == (
        FavoritesSchemaRule.TOO_FEW_FIELDS,
        FavoritesSchemaRule.TOO_FEW_FIELDS,
    )

    assert all(
        diagnostic.rule
        is not FavoritesSchemaRule.MISSING_REQUIRED_METADATA
        for diagnostic in result.diagnostics
    )


def test_invalid_target_model_is_error() -> None:
    workspace = _workspace(
        catalog_records=(
            b"TargetModel\tOtherModel",
            b"FormatVersion\t1.00",
        )
    )

    result = validate_favorites_workspace(
        workspace
    )

    assert result.is_valid is False

    assert tuple(
        (
            diagnostic.rule,
            diagnostic.severity,
            diagnostic.field_index,
        )
        for diagnostic in result.diagnostics
    ) == (
        (
            FavoritesSchemaRule.INVALID_TARGET_MODEL,
            FavoritesSchemaSeverity.ERROR,
            0,
        ),
    )


@pytest.mark.parametrize(
    (
        "version",
        "expected_rule",
        "expected_valid",
    ),
    [
        (
            "1.00",
            None,
            True,
        ),
        (
            "2.00",
            FavoritesSchemaRule.UNVALIDATED_FORMAT_VERSION,
            True,
        ),
        (
            "0.99",
            FavoritesSchemaRule.UNVALIDATED_FORMAT_VERSION,
            True,
        ),
        (
            "1.0",
            FavoritesSchemaRule.INVALID_FORMAT_VERSION,
            False,
        ),
        (
            "1.000",
            FavoritesSchemaRule.INVALID_FORMAT_VERSION,
            False,
        ),
        (
            "10.00",
            FavoritesSchemaRule.INVALID_FORMAT_VERSION,
            False,
        ),
        (
            "v1.00",
            FavoritesSchemaRule.INVALID_FORMAT_VERSION,
            False,
        ),
        (
            "",
            FavoritesSchemaRule.INVALID_FORMAT_VERSION,
            False,
        ),
    ],
)
def test_format_version_envelope(
    version: str,
    expected_rule: FavoritesSchemaRule | None,
    expected_valid: bool,
) -> None:
    workspace = _workspace(
        catalog_records=(
            b"TargetModel\tBCDx36HP",
            (
                "FormatVersion\t"
                + version
            ).encode("ascii"),
        )
    )

    result = validate_favorites_workspace(
        workspace
    )

    assert result.is_valid is expected_valid

    version_diagnostics = tuple(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.command == "FormatVersion"
    )

    if expected_rule is None:
        assert version_diagnostics == ()
    else:
        assert tuple(
            diagnostic.rule
            for diagnostic in version_diagnostics
        ) == (
            expected_rule,
        )


@pytest.mark.parametrize(
    (
        "source_kind",
        "command",
        "count",
        "expected_rule",
    ),
    [
        (
            FavoritesSchemaSourceKind.CATALOG,
            "F-List",
            116,
            FavoritesSchemaRule.TOO_FEW_FIELDS,
        ),
        (
            FavoritesSchemaSourceKind.CATALOG,
            "F-List",
            117,
            None,
        ),
        (
            FavoritesSchemaSourceKind.CATALOG,
            "F-List",
            118,
            FavoritesSchemaRule.UNVALIDATED_EXTRA_FIELDS,
        ),
        (
            FavoritesSchemaSourceKind.HPD,
            "T-Freq",
            8,
            None,
        ),
        (
            FavoritesSchemaSourceKind.HPD,
            "T-Freq",
            9,
            None,
        ),
        (
            FavoritesSchemaSourceKind.HPD,
            "T-Freq",
            10,
            FavoritesSchemaRule.UNVALIDATED_EXTRA_FIELDS,
        ),
        (
            FavoritesSchemaSourceKind.HPD,
            "BandPlan_P25",
            34,
            None,
        ),
        (
            FavoritesSchemaSourceKind.HPD,
            "BandPlan_P25",
            50,
            None,
        ),
        (
            FavoritesSchemaSourceKind.HPD,
            "BandPlan_P25",
            40,
            FavoritesSchemaRule.UNVALIDATED_EXTRA_FIELDS,
        ),
        (
            FavoritesSchemaSourceKind.HPD,
            "TGID",
            17,
            None,
        ),
        (
            FavoritesSchemaSourceKind.HPD,
            "TGID",
            18,
            None,
        ),
        (
            FavoritesSchemaSourceKind.HPD,
            "TGID",
            19,
            FavoritesSchemaRule.UNVALIDATED_EXTRA_FIELDS,
        ),
        (
            FavoritesSchemaSourceKind.HPD,
            "UnitID",
            1,
            None,
        ),
        (
            FavoritesSchemaSourceKind.HPD,
            "UnitID",
            2,
            FavoritesSchemaRule.UNVALIDATED_EXTRA_FIELDS,
        ),
        (
            FavoritesSchemaSourceKind.HPD,
            "AvoidTgids",
            2,
            FavoritesSchemaRule.TOO_FEW_FIELDS,
        ),
        (
            FavoritesSchemaSourceKind.HPD,
            "AvoidTgids",
            3,
            None,
        ),
        (
            FavoritesSchemaSourceKind.HPD,
            "AvoidTgids",
            18,
            None,
        ),
        (
            FavoritesSchemaSourceKind.HPD,
            "AvoidTgids",
            19,
            FavoritesSchemaRule.UNVALIDATED_EXTRA_FIELDS,
        ),
    ],
)
def test_field_shape_envelope(
    source_kind: FavoritesSchemaSourceKind,
    command: str,
    count: int,
    expected_rule: FavoritesSchemaRule | None,
) -> None:
    record = _record_with_count(
        command,
        count,
    )

    if source_kind is FavoritesSchemaSourceKind.CATALOG:
        workspace = _workspace(
            catalog_records=(
                *_VALID_METADATA,
                record,
            )
        )
    else:
        workspace = _workspace(
            documents=(
                (
                    "shape.hpd",
                    (
                        *_VALID_METADATA,
                        record,
                    ),
                ),
            )
        )

    result = validate_favorites_workspace(
        workspace
    )

    shape_diagnostics = _shape_diagnostics(
        result,
        command,
    )

    if expected_rule is None:
        assert shape_diagnostics == ()
    else:
        assert tuple(
            diagnostic.rule
            for diagnostic in shape_diagnostics
        ) == (
            expected_rule,
        )


def test_observed_scanner_extensions_produce_no_shape_diagnostic() -> None:
    workspace = _workspace(
        documents=(
            (
                "observed.hpd",
                (
                    *_VALID_METADATA,
                    _record_with_count(
                        "T-Freq",
                        9,
                    ),
                    _record_with_count(
                        "BandPlan_P25",
                        50,
                    ),
                    _record_with_count(
                        "TGID",
                        18,
                    ),
                    _record_with_count(
                        "UnitID",
                        1,
                    ),
                ),
            ),
        )
    )

    result = validate_favorites_workspace(
        workspace
    )

    assert tuple(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.command
        in {
            "T-Freq",
            "BandPlan_P25",
            "TGID",
            "UnitID",
        }
    ) == ()


def test_unsupported_command_receives_exactly_one_info() -> None:
    workspace = _workspace(
        catalog_records=(
            *_VALID_METADATA,
            b"FutureListSetting\tA\tB\tC",
        )
    )

    result = validate_favorites_workspace(
        workspace
    )

    assert tuple(
        (
            diagnostic.rule,
            diagnostic.severity,
            diagnostic.command,
        )
        for diagnostic in result.diagnostics
    ) == (
        (
            FavoritesSchemaRule.UNSUPPORTED_COMMAND,
            FavoritesSchemaSeverity.INFO,
            "FutureListSetting",
        ),
    )


@pytest.mark.parametrize(
    (
        "name",
        "expected_invalid",
    ),
    [
        (
            "",
            False,
        ),
        (
            "A" * 64,
            False,
        ),
        (
            "A" * 65,
            True,
        ),
        (
            " Leading and trailing ",
            False,
        ),
        (
            "\x1f",
            True,
        ),
        (
            "\x7f",
            True,
        ),
    ],
)
def test_name_tag_envelope(
    name: str,
    expected_invalid: bool,
) -> None:
    workspace = _workspace(
        catalog_records=(
            *_VALID_METADATA,
            _record_with_count(
                "F-List",
                117,
                fields={
                    0: name,
                },
            ),
        )
    )

    result = validate_favorites_workspace(
        workspace
    )

    diagnostics = tuple(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.rule
        is FavoritesSchemaRule.INVALID_NAME_TAG
    )

    if expected_invalid:
        assert len(diagnostics) == 1
        assert diagnostics[0].field_index == 0
        assert diagnostics[0].record is (
            workspace.catalog.source.records[2]
        )
    else:
        assert diagnostics == ()


def test_duplicate_metadata_has_no_duplicate_specific_diagnostic() -> None:
    workspace = _workspace(
        catalog_records=(
            b"TargetModel\tBCDx36HP",
            b"TargetModel\tBCDx36HP",
            b"FormatVersion\t1.00",
            b"FormatVersion\t1.00",
        )
    )

    result = validate_favorites_workspace(
        workspace
    )

    assert result.diagnostics == ()
    assert result.is_valid is True


def test_duplicate_hpd_filenames_are_disambiguated_by_document_index() -> None:
    records = (
        b"TargetModel\tBCDx36HP",
        b"FormatVersion\t2.00",
    )

    workspace = _workspace(
        documents=(
            (
                "same.hpd",
                records,
            ),
            (
                "same.hpd",
                records,
            ),
        )
    )

    workspace = replace(
        workspace,
        duplicate_document_filenames=(
            "same.hpd",
        ),
    )

    first_record = (
        workspace.documents[0]
        .hierarchy.source.records[1]
    )
    second_record = (
        workspace.documents[1]
        .hierarchy.source.records[1]
    )

    assert first_record == second_record
    assert first_record is not second_record

    result = validate_favorites_workspace(
        workspace
    )

    assert tuple(
        (
            diagnostic.document_index,
            diagnostic.filename,
            diagnostic.source_index,
        )
        for diagnostic in result.diagnostics
    ) == (
        (
            0,
            "same.hpd",
            1,
        ),
        (
            1,
            "same.hpd",
            1,
        ),
    )

    assert result.diagnostics[0].record is first_record
    assert result.diagnostics[1].record is second_record
    assert result.diagnostics[0] != result.diagnostics[1]


def test_workspace_binding_diagnostics_are_not_reclassified() -> None:
    catalog_record = FavoritesSourceRecord(
        content=_record_with_count(
            "F-List",
            117,
            fields={
                0: "List",
                1: "one.hpd",
            },
        ),
        line_ending=b"\r\n",
    )

    catalog_source = FavoritesSourceFile(
        records=(
            FavoritesSourceRecord(
                content=b"TargetModel\tBCDx36HP",
                line_ending=b"\r\n",
            ),
            FavoritesSourceRecord(
                content=b"FormatVersion\t1.00",
                line_ending=b"\r\n",
            ),
            catalog_record,
        )
    )

    entry = FavoritesCatalogEntry(
        source_index=2,
        source=catalog_record,
    )

    document = FavoritesHierarchyDocument(
        filename="one.hpd",
        hierarchy=FavoritesHierarchy(
            source=_source(_VALID_METADATA),
            metadata_records=(),
            systems=(),
            unclassified_records=(),
        ),
    )

    workspace = FavoritesWorkspace(
        catalog=FavoritesCatalog(
            source=catalog_source,
            metadata_indexes=(),
            entries=(entry,),
            unclassified_indexes=(),
        ),
        documents=(document,),
        bindings=(),
        missing_entries=(entry,),
        ambiguous_entries=(entry,),
        duplicate_catalog_filenames=(
            "one.hpd",
        ),
        duplicate_document_filenames=(
            "one.hpd",
        ),
        orphan_documents=(document,),
    )

    result = validate_favorites_workspace(
        workspace
    )

    assert result.workspace is workspace
    assert result.diagnostics == ()


def test_diagnostic_order_is_catalog_document_source_rule_order() -> None:
    workspace = _workspace(
        catalog_records=(
            b"TargetModel\tBCDx36HP",
            b"FormatVersion\t2.00",
            b"FutureListSetting\tA",
        ),
        documents=(
            (
                "first.hpd",
                (
                    b"FormatVersion\t2.00",
                    b"FutureCommand\tA",
                ),
            ),
            (
                "second.hpd",
                (
                    *_VALID_METADATA,
                    b"C-Freq\t\t\t\x1f",
                ),
            ),
        ),
    )

    result = validate_favorites_workspace(
        workspace
    )

    assert tuple(
        (
            diagnostic.source_kind,
            diagnostic.document_index,
            diagnostic.source_index,
            diagnostic.rule,
        )
        for diagnostic in result.diagnostics
    ) == (
        (
            FavoritesSchemaSourceKind.CATALOG,
            None,
            1,
            FavoritesSchemaRule.UNVALIDATED_FORMAT_VERSION,
        ),
        (
            FavoritesSchemaSourceKind.CATALOG,
            None,
            2,
            FavoritesSchemaRule.UNSUPPORTED_COMMAND,
        ),
        (
            FavoritesSchemaSourceKind.HPD,
            0,
            None,
            FavoritesSchemaRule.MISSING_REQUIRED_METADATA,
        ),
        (
            FavoritesSchemaSourceKind.HPD,
            0,
            0,
            FavoritesSchemaRule.UNVALIDATED_FORMAT_VERSION,
        ),
        (
            FavoritesSchemaSourceKind.HPD,
            0,
            1,
            FavoritesSchemaRule.UNSUPPORTED_COMMAND,
        ),
        (
            FavoritesSchemaSourceKind.HPD,
            1,
            2,
            FavoritesSchemaRule.TOO_FEW_FIELDS,
        ),
        (
            FavoritesSchemaSourceKind.HPD,
            1,
            2,
            FavoritesSchemaRule.INVALID_NAME_TAG,
        ),
    )


def test_warning_and_info_do_not_make_validation_invalid() -> None:
    workspace = _workspace(
        catalog_records=(
            b"TargetModel\tBCDx36HP",
            b"FormatVersion\t2.00",
            b"FutureListSetting",
        )
    )

    result = validate_favorites_workspace(
        workspace
    )

    assert tuple(
        diagnostic.severity
        for diagnostic in result.diagnostics
    ) == (
        FavoritesSchemaSeverity.WARNING,
        FavoritesSchemaSeverity.INFO,
    )

    assert result.is_valid is True


def test_error_makes_validation_invalid() -> None:
    result = validate_favorites_workspace(
        _workspace(
            catalog_records=()
        )
    )

    assert result.is_valid is False
    assert all(
        diagnostic.severity
        is FavoritesSchemaSeverity.ERROR
        for diagnostic in result.diagnostics
    )


def test_validation_preserves_workspace_and_source_identity() -> None:
    workspace = _workspace(
        catalog_records=(
            b"TargetModel\tBCDx36HP",
            b"FormatVersion\t2.00",
        ),
        documents=(
            (
                "one.hpd",
                (
                    b"TargetModel\tBCDx36HP",
                    b"FormatVersion\t2.00",
                ),
            ),
        ),
    )

    catalog_bytes = (
        workspace.catalog.source.to_bytes()
    )
    document_bytes = (
        workspace.documents[0]
        .hierarchy.source.to_bytes()
    )

    catalog_records = workspace.catalog.source.records
    document_records = (
        workspace.documents[0]
        .hierarchy.source.records
    )

    result = validate_favorites_workspace(
        workspace
    )

    assert result.workspace is workspace
    assert workspace.catalog.source.to_bytes() == catalog_bytes
    assert (
        workspace.documents[0]
        .hierarchy.source.to_bytes()
        == document_bytes
    )

    assert workspace.catalog.source.records is catalog_records
    assert (
        workspace.documents[0]
        .hierarchy.source.records
        is document_records
    )

    assert result.diagnostics[0].record is catalog_records[1]
    assert result.diagnostics[1].record is document_records[1]


def test_validation_requires_workspace_input() -> None:
    with pytest.raises(
        TypeError,
        match="requires FavoritesWorkspace",
    ):
        validate_favorites_workspace(  # type: ignore[arg-type]
            object()
        )
