from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from sds200 import (
    FavoritesComparisonAmbiguity,
    FavoritesComparisonChangeKind,
    FavoritesComparisonDocumentReference,
    FavoritesComparisonRecordChange,
    FavoritesComparisonSource,
    FavoritesComparisonSourceKind,
    FavoritesComparisonSourceState,
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
    FavoritesWorkspaceComparison,
    compare_favorites_workspaces,
    parse_favorites_file,
    project_favorites_storage_snapshot,
)


def _workspace(
    *,
    catalog: bytes = (
        b"TargetModel\tBCDx36HP\r\n"
        b"FormatVersion\t1.00\r\n"
    ),
    documents: tuple[tuple[str, bytes], ...] = (),
):
    return project_favorites_storage_snapshot(
        FavoritesStorageSnapshot(
            catalog_bytes=catalog,
            documents=tuple(
                FavoritesStorageDocument(
                    filename=filename,
                    content=content,
                )
                for filename, content in documents
            ),
        )
    )


def _hpd(
    *records: bytes,
    line_ending: bytes = b"\r\n",
) -> bytes:
    return b"".join(
        record + line_ending
        for record in records
    )


def _catalog_with_entry(
    name: str,
    filename: str,
    *,
    line_ending: bytes = b"\r\n",
) -> bytes:
    return (
        b"TargetModel\tBCDx36HP"
        + line_ending
        + b"FormatVersion\t1.00"
        + line_ending
        + f"F-List\t{name}\t{filename}".encode("ascii")
        + line_ending
    )


def test_identical_workspaces_are_exactly_equal() -> None:
    catalog = _catalog_with_entry("Alpha", "alpha.hpd")
    hpd = _hpd(
        b"TargetModel\tBCDx36HP",
        b"FormatVersion\t1.00",
        b"Mystery\tA\tB",
    )
    baseline = _workspace(
        catalog=catalog,
        documents=(("alpha.hpd", hpd),),
    )
    candidate = _workspace(
        catalog=catalog,
        documents=(("alpha.hpd", hpd),),
    )

    comparison = compare_favorites_workspaces(
        baseline,
        candidate,
    )

    assert comparison.baseline is baseline
    assert comparison.candidate is candidate
    assert comparison.is_comparable is True
    assert comparison.has_ambiguity is False
    assert comparison.has_changes is False
    assert comparison.is_equal is True
    assert len(comparison.items) == 2

    catalog_comparison = comparison.items[0]
    assert isinstance(
        catalog_comparison,
        FavoritesComparisonSource,
    )
    assert (
        catalog_comparison.source_kind
        is FavoritesComparisonSourceKind.CATALOG
    )
    assert (
        catalog_comparison.state
        is FavoritesComparisonSourceState.MATCHED
    )
    assert catalog_comparison.filename is None
    assert catalog_comparison.record_changes == ()
    assert (
        catalog_comparison.baseline_source
        is baseline.catalog.source
    )
    assert (
        catalog_comparison.candidate_source
        is candidate.catalog.source
    )

    hpd_comparison = comparison.items[1]
    assert isinstance(
        hpd_comparison,
        FavoritesComparisonSource,
    )
    assert hpd_comparison.filename == "alpha.hpd"
    assert (
        hpd_comparison.baseline_document is not None
    )
    assert (
        hpd_comparison.baseline_document.document
        is baseline.documents[0]
    )
    assert (
        hpd_comparison.candidate_document is not None
    )
    assert (
        hpd_comparison.candidate_document.document
        is candidate.documents[0]
    )
    assert hpd_comparison.record_changes == ()


def test_catalog_insertion_is_one_exact_added_record() -> None:
    baseline = _workspace()
    candidate = _workspace(
        catalog=_catalog_with_entry(
            "Alpha",
            "alpha.hpd",
        )
    )

    comparison = compare_favorites_workspaces(
        baseline,
        candidate,
    )
    source = comparison.items[0]

    assert isinstance(source, FavoritesComparisonSource)
    assert source.changed is True
    assert len(source.record_changes) == 1

    change = source.record_changes[0]
    assert (
        change.kind
        is FavoritesComparisonChangeKind.ADDED
    )
    assert change.baseline_source_index is None
    assert change.baseline_record is None
    assert change.candidate_source_index == 2
    assert (
        change.candidate_record
        is candidate.catalog.source.records[2]
    )


def test_catalog_removal_is_one_exact_removed_record() -> None:
    baseline = _workspace(
        catalog=_catalog_with_entry(
            "Alpha",
            "alpha.hpd",
        )
    )
    candidate = _workspace()

    comparison = compare_favorites_workspaces(
        baseline,
        candidate,
    )
    source = comparison.items[0]

    assert isinstance(source, FavoritesComparisonSource)
    assert len(source.record_changes) == 1

    change = source.record_changes[0]
    assert (
        change.kind
        is FavoritesComparisonChangeKind.REMOVED
    )
    assert change.baseline_source_index == 2
    assert (
        change.baseline_record
        is baseline.catalog.source.records[2]
    )
    assert change.candidate_source_index is None
    assert change.candidate_record is None


def test_catalog_replacement_is_positional_and_exact() -> None:
    baseline = _workspace(
        catalog=_catalog_with_entry(
            "Alpha",
            "alpha.hpd",
        )
    )
    candidate = _workspace(
        catalog=_catalog_with_entry(
            "Bravo",
            "alpha.hpd",
        )
    )

    source = compare_favorites_workspaces(
        baseline,
        candidate,
    ).items[0]

    assert isinstance(source, FavoritesComparisonSource)
    assert len(source.record_changes) == 1
    change = source.record_changes[0]

    assert (
        change.kind
        is FavoritesComparisonChangeKind.REPLACED
    )
    assert change.baseline_source_index == 2
    assert change.candidate_source_index == 2
    assert (
        change.baseline_record
        is baseline.catalog.source.records[2]
    )
    assert (
        change.candidate_record
        is candidate.catalog.source.records[2]
    )


def test_hpd_insertion_aligns_equal_surrounding_records() -> None:
    baseline = _workspace(
        documents=(
            (
                "alpha.hpd",
                _hpd(
                    b"Unknown\tA",
                    b"Unknown\tC",
                ),
            ),
        )
    )
    candidate = _workspace(
        documents=(
            (
                "alpha.hpd",
                _hpd(
                    b"Unknown\tA",
                    b"Unknown\tB",
                    b"Unknown\tC",
                ),
            ),
        )
    )

    source = compare_favorites_workspaces(
        baseline,
        candidate,
    ).items[1]

    assert isinstance(source, FavoritesComparisonSource)
    assert len(source.record_changes) == 1

    change = source.record_changes[0]
    assert (
        change.kind
        is FavoritesComparisonChangeKind.ADDED
    )
    assert change.baseline_source_index is None
    assert change.candidate_source_index == 1
    assert (
        change.candidate_record
        is candidate.documents[0].hierarchy.source.records[1]
    )


def test_hpd_deletion_aligns_equal_surrounding_records() -> None:
    baseline = _workspace(
        documents=(
            (
                "alpha.hpd",
                _hpd(
                    b"Unknown\tA",
                    b"Unknown\tB",
                    b"Unknown\tC",
                ),
            ),
        )
    )
    candidate = _workspace(
        documents=(
            (
                "alpha.hpd",
                _hpd(
                    b"Unknown\tA",
                    b"Unknown\tC",
                ),
            ),
        )
    )

    source = compare_favorites_workspaces(
        baseline,
        candidate,
    ).items[1]

    assert isinstance(source, FavoritesComparisonSource)
    assert len(source.record_changes) == 1

    change = source.record_changes[0]
    assert (
        change.kind
        is FavoritesComparisonChangeKind.REMOVED
    )
    assert change.baseline_source_index == 1
    assert change.candidate_source_index is None


def test_replace_block_pairs_then_reports_residual_removal() -> None:
    baseline = _workspace(
        documents=(
            (
                "alpha.hpd",
                _hpd(
                    b"Unknown\tA",
                    b"Unknown\tB",
                    b"Unknown\tC",
                    b"Unknown\tD",
                ),
            ),
        )
    )
    candidate = _workspace(
        documents=(
            (
                "alpha.hpd",
                _hpd(
                    b"Unknown\tA",
                    b"Unknown\tX",
                    b"Unknown\tD",
                ),
            ),
        )
    )

    source = compare_favorites_workspaces(
        baseline,
        candidate,
    ).items[1]

    assert isinstance(source, FavoritesComparisonSource)
    assert [
        (
            change.kind,
            change.baseline_source_index,
            change.candidate_source_index,
        )
        for change in source.record_changes
    ] == [
        (
            FavoritesComparisonChangeKind.REPLACED,
            1,
            1,
        ),
        (
            FavoritesComparisonChangeKind.REMOVED,
            2,
            None,
        ),
    ]


def test_replace_block_pairs_then_reports_residual_addition() -> None:
    baseline = _workspace(
        documents=(
            (
                "alpha.hpd",
                _hpd(
                    b"Unknown\tA",
                    b"Unknown\tB",
                    b"Unknown\tD",
                ),
            ),
        )
    )
    candidate = _workspace(
        documents=(
            (
                "alpha.hpd",
                _hpd(
                    b"Unknown\tA",
                    b"Unknown\tX",
                    b"Unknown\tY",
                    b"Unknown\tD",
                ),
            ),
        )
    )

    source = compare_favorites_workspaces(
        baseline,
        candidate,
    ).items[1]

    assert isinstance(source, FavoritesComparisonSource)
    assert [
        (
            change.kind,
            change.baseline_source_index,
            change.candidate_source_index,
        )
        for change in source.record_changes
    ] == [
        (
            FavoritesComparisonChangeKind.REPLACED,
            1,
            1,
        ),
        (
            FavoritesComparisonChangeKind.ADDED,
            None,
            2,
        ),
    ]


def test_line_ending_change_is_an_exact_replacement() -> None:
    baseline = _workspace(
        documents=(
            (
                "alpha.hpd",
                b"Unknown\tA\r\n",
            ),
        )
    )
    candidate = _workspace(
        documents=(
            (
                "alpha.hpd",
                b"Unknown\tA\n",
            ),
        )
    )

    source = compare_favorites_workspaces(
        baseline,
        candidate,
    ).items[1]

    assert isinstance(source, FavoritesComparisonSource)
    assert len(source.record_changes) == 1
    change = source.record_changes[0]

    assert (
        change.kind
        is FavoritesComparisonChangeKind.REPLACED
    )
    assert change.baseline_record is not None
    assert change.candidate_record is not None
    assert change.baseline_record.content == change.candidate_record.content
    assert change.baseline_record.line_ending == b"\r\n"
    assert change.candidate_record.line_ending == b"\n"


def test_unknown_command_and_extra_fields_are_ordinary_source_data() -> None:
    baseline = _workspace(
        documents=(
            (
                "alpha.hpd",
                b"FutureCommand\tone\r\n",
            ),
        )
    )
    candidate = _workspace(
        documents=(
            (
                "alpha.hpd",
                b"FutureCommand\tone\ttwo\r\n",
            ),
        )
    )

    source = compare_favorites_workspaces(
        baseline,
        candidate,
    ).items[1]

    assert isinstance(source, FavoritesComparisonSource)
    assert len(source.record_changes) == 1
    change = source.record_changes[0]

    assert (
        change.kind
        is FavoritesComparisonChangeKind.REPLACED
    )
    assert change.baseline_record is not None
    assert change.candidate_record is not None
    assert change.baseline_record.command == "FutureCommand"
    assert change.candidate_record.command == "FutureCommand"
    assert change.baseline_record.fields == ("one",)
    assert change.candidate_record.fields == ("one", "two")


def test_removed_hpd_reports_every_preserved_record() -> None:
    baseline = _workspace(
        documents=(
            (
                "alpha.hpd",
                _hpd(
                    b"Unknown\tA",
                    b"Unknown\tB",
                ),
            ),
        )
    )
    candidate = _workspace()

    source = compare_favorites_workspaces(
        baseline,
        candidate,
    ).items[1]

    assert isinstance(source, FavoritesComparisonSource)
    assert (
        source.state
        is FavoritesComparisonSourceState.REMOVED
    )
    assert source.candidate_document is None
    assert source.candidate_source is None
    assert [
        change.kind
        for change in source.record_changes
    ] == [
        FavoritesComparisonChangeKind.REMOVED,
        FavoritesComparisonChangeKind.REMOVED,
    ]
    assert [
        change.baseline_source_index
        for change in source.record_changes
    ] == [0, 1]


def test_added_hpd_reports_every_preserved_record() -> None:
    baseline = _workspace()
    candidate = _workspace(
        documents=(
            (
                "alpha.hpd",
                _hpd(
                    b"Unknown\tA",
                    b"Unknown\tB",
                ),
            ),
        )
    )

    source = compare_favorites_workspaces(
        baseline,
        candidate,
    ).items[1]

    assert isinstance(source, FavoritesComparisonSource)
    assert (
        source.state
        is FavoritesComparisonSourceState.ADDED
    )
    assert source.baseline_document is None
    assert source.baseline_source is None
    assert [
        change.kind
        for change in source.record_changes
    ] == [
        FavoritesComparisonChangeKind.ADDED,
        FavoritesComparisonChangeKind.ADDED,
    ]
    assert [
        change.candidate_source_index
        for change in source.record_changes
    ] == [0, 1]


def test_hpd_source_order_is_baseline_then_candidate_only() -> None:
    baseline = _workspace(
        documents=(
            ("bravo.hpd", b"Unknown\tB\r\n"),
            ("alpha.hpd", b"Unknown\tA\r\n"),
        )
    )
    candidate = _workspace(
        documents=(
            ("alpha.hpd", b"Unknown\tA\r\n"),
            ("charlie.hpd", b"Unknown\tC\r\n"),
            ("bravo.hpd", b"Unknown\tB\r\n"),
            ("delta.hpd", b"Unknown\tD\r\n"),
        )
    )

    comparison = compare_favorites_workspaces(
        baseline,
        candidate,
    )

    assert [
        item.filename
        for item in comparison.items[1:]
    ] == [
        "bravo.hpd",
        "alpha.hpd",
        "charlie.hpd",
        "delta.hpd",
    ]


def test_removed_hpd_stays_in_baseline_document_order() -> None:
    baseline = _workspace(
        documents=(
            ("bravo.hpd", b"Unknown\tB\r\n"),
            ("alpha.hpd", b"Unknown\tA\r\n"),
        )
    )
    candidate = _workspace()

    comparison = compare_favorites_workspaces(
        baseline,
        candidate,
    )

    assert [
        item.filename
        for item in comparison.items[1:]
    ] == [
        "bravo.hpd",
        "alpha.hpd",
    ]
    assert all(
        isinstance(item, FavoritesComparisonSource)
        and item.state is FavoritesComparisonSourceState.REMOVED
        for item in comparison.items[1:]
    )


def test_duplicate_baseline_filename_is_explicit_ambiguity() -> None:
    first = b"Unknown\tFirst\r\n"
    second = b"Unknown\tSecond\r\n"
    candidate_bytes = b"Unknown\tCandidate\r\n"
    baseline = _workspace(
        documents=(
            ("alpha.hpd", first),
            ("bravo.hpd", b"Unknown\tB\r\n"),
            ("alpha.hpd", second),
        )
    )
    candidate = _workspace(
        documents=(
            ("alpha.hpd", candidate_bytes),
            ("bravo.hpd", b"Unknown\tB\r\n"),
        )
    )

    comparison = compare_favorites_workspaces(
        baseline,
        candidate,
    )

    ambiguity = comparison.items[1]
    assert isinstance(
        ambiguity,
        FavoritesComparisonAmbiguity,
    )
    assert ambiguity.filename == "alpha.hpd"
    assert [
        reference.document_index
        for reference in ambiguity.baseline_documents
    ] == [0, 2]
    assert [
        reference.document
        for reference in ambiguity.baseline_documents
    ] == [
        baseline.documents[0],
        baseline.documents[2],
    ]
    assert [
        reference.document_index
        for reference in ambiguity.candidate_documents
    ] == [0]
    assert (
        ambiguity.candidate_documents[0].document
        is candidate.documents[0]
    )
    assert comparison.has_ambiguity is True
    assert comparison.is_comparable is False
    assert comparison.is_equal is False


def test_duplicate_candidate_only_filename_is_explicit_ambiguity() -> None:
    baseline = _workspace()
    candidate = _workspace(
        documents=(
            ("alpha.hpd", b"Unknown\tA\r\n"),
            ("alpha.hpd", b"Unknown\tB\r\n"),
        )
    )

    comparison = compare_favorites_workspaces(
        baseline,
        candidate,
    )

    ambiguity = comparison.items[1]
    assert isinstance(
        ambiguity,
        FavoritesComparisonAmbiguity,
    )
    assert ambiguity.baseline_documents == ()
    assert [
        reference.document_index
        for reference in ambiguity.candidate_documents
    ] == [0, 1]
    assert comparison.has_changes is False
    assert comparison.is_equal is False


def test_duplicate_both_sides_are_not_paired_by_position() -> None:
    baseline = _workspace(
        documents=(
            ("alpha.hpd", b"Unknown\tA\r\n"),
            ("alpha.hpd", b"Unknown\tB\r\n"),
        )
    )
    candidate = _workspace(
        documents=(
            ("alpha.hpd", b"Unknown\tA\r\n"),
            ("alpha.hpd", b"Unknown\tB\r\n"),
        )
    )

    comparison = compare_favorites_workspaces(
        baseline,
        candidate,
    )

    assert len(comparison.items) == 2
    ambiguity = comparison.items[1]
    assert isinstance(
        ambiguity,
        FavoritesComparisonAmbiguity,
    )
    assert len(ambiguity.baseline_documents) == 2
    assert len(ambiguity.candidate_documents) == 2
    assert comparison.is_equal is False


def test_ambiguity_uses_first_baseline_position_for_output_order() -> None:
    baseline = _workspace(
        documents=(
            ("alpha.hpd", b"Unknown\tA1\r\n"),
            ("bravo.hpd", b"Unknown\tB\r\n"),
            ("alpha.hpd", b"Unknown\tA2\r\n"),
        )
    )
    candidate = _workspace(
        documents=(
            ("bravo.hpd", b"Unknown\tB\r\n"),
        )
    )

    comparison = compare_favorites_workspaces(
        baseline,
        candidate,
    )

    assert [
        item.filename
        for item in comparison.items[1:]
    ] == [
        "alpha.hpd",
        "bravo.hpd",
    ]
    assert isinstance(
        comparison.items[1],
        FavoritesComparisonAmbiguity,
    )


def test_workspace_binding_diagnostics_are_not_reclassified() -> None:
    catalog = _catalog_with_entry(
        "Missing",
        "missing.hpd",
    )
    baseline = _workspace(catalog=catalog)
    candidate = _workspace(catalog=catalog)

    comparison = compare_favorites_workspaces(
        baseline,
        candidate,
    )

    assert baseline.missing_entries
    assert candidate.missing_entries
    assert comparison.is_equal is True
    assert len(comparison.items) == 1


def test_schema_meaning_does_not_override_exact_source_equality() -> None:
    hpd = b"Unsupported\tvalue\r\n"
    baseline = _workspace(
        documents=(("alpha.hpd", hpd),)
    )
    candidate = _workspace(
        documents=(("alpha.hpd", hpd),)
    )

    comparison = compare_favorites_workspaces(
        baseline,
        candidate,
    )

    assert comparison.is_equal is True


def test_document_reordering_does_not_change_filename_paired_sources() -> None:
    alpha = b"Unknown\tA\r\n"
    bravo = b"Unknown\tB\r\n"
    baseline = _workspace(
        documents=(
            ("alpha.hpd", alpha),
            ("bravo.hpd", bravo),
        )
    )
    candidate = _workspace(
        documents=(
            ("bravo.hpd", bravo),
            ("alpha.hpd", alpha),
        )
    )

    comparison = compare_favorites_workspaces(
        baseline,
        candidate,
    )

    assert comparison.is_equal is True
    assert [
        item.filename
        for item in comparison.items[1:]
    ] == [
        "alpha.hpd",
        "bravo.hpd",
    ]


def test_repeated_identical_records_have_deterministic_alignment() -> None:
    baseline = _workspace(
        documents=(
            (
                "alpha.hpd",
                _hpd(
                    b"Unknown\tA",
                    b"Unknown\tA",
                    b"Unknown\tB",
                ),
            ),
        )
    )
    candidate = _workspace(
        documents=(
            (
                "alpha.hpd",
                _hpd(
                    b"Unknown\tA",
                    b"Unknown\tB",
                ),
            ),
        )
    )

    first = compare_favorites_workspaces(
        baseline,
        candidate,
    )
    second = compare_favorites_workspaces(
        baseline,
        candidate,
    )

    assert first == second
    source = first.items[1]
    assert isinstance(source, FavoritesComparisonSource)
    assert [
        (
            change.kind,
            change.baseline_source_index,
            change.candidate_source_index,
        )
        for change in source.record_changes
    ] == [
        (
            FavoritesComparisonChangeKind.REMOVED,
            0,
            None,
        )
    ]


def test_record_change_is_frozen() -> None:
    record = parse_favorites_file(
        b"Unknown\tA\r\n"
    ).records[0]
    change = FavoritesComparisonRecordChange(
        kind=FavoritesComparisonChangeKind.REMOVED,
        baseline_source_index=0,
        baseline_record=record,
        candidate_source_index=None,
        candidate_record=None,
    )

    with pytest.raises(FrozenInstanceError):
        change.baseline_source_index = 1  # type: ignore[misc]


def test_comparison_is_frozen() -> None:
    comparison = compare_favorites_workspaces(
        _workspace(),
        _workspace(),
    )

    with pytest.raises(FrozenInstanceError):
        comparison.items = ()  # type: ignore[misc]


@pytest.mark.parametrize(
    ("baseline", "candidate", "message"),
    [
        (object(), _workspace(), "baseline"),
        (_workspace(), object(), "candidate"),
    ],
)
def test_compare_requires_workspaces(
    baseline: object,
    candidate: object,
    message: str,
) -> None:
    with pytest.raises(TypeError, match=message):
        compare_favorites_workspaces(  # type: ignore[arg-type]
            baseline,
            candidate,
        )


def test_record_change_rejects_same_raw_bytes_replacement() -> None:
    record = parse_favorites_file(
        b"Unknown\tA\r\n"
    ).records[0]

    with pytest.raises(
        ValueError,
        match="must differ",
    ):
        FavoritesComparisonRecordChange(
            kind=FavoritesComparisonChangeKind.REPLACED,
            baseline_source_index=0,
            baseline_record=record,
            candidate_source_index=0,
            candidate_record=record,
        )


def test_ambiguity_requires_duplicate_filename() -> None:
    workspace = _workspace(
        documents=(
            ("alpha.hpd", b"Unknown\tA\r\n"),
        )
    )
    reference = FavoritesComparisonDocumentReference(
        document_index=0,
        document=workspace.documents[0],
    )

    with pytest.raises(
        ValueError,
        match="duplicate HPD",
    ):
        FavoritesComparisonAmbiguity(
            filename="alpha.hpd",
            baseline_documents=(reference,),
            candidate_documents=(),
        )


def test_comparison_rejects_noncanonical_items() -> None:
    baseline = _workspace(
        documents=(
            ("alpha.hpd", b"Unknown\tA\r\n"),
        )
    )
    candidate = _workspace(
        documents=(
            ("alpha.hpd", b"Unknown\tA\r\n"),
        )
    )
    valid = compare_favorites_workspaces(
        baseline,
        candidate,
    )

    with pytest.raises(
        ValueError,
        match="first item",
    ):
        FavoritesWorkspaceComparison(
            baseline=baseline,
            candidate=candidate,
            items=valid.items[1:],
        )


def test_source_rejects_incomplete_record_change_sequence() -> None:
    baseline = _workspace(
        documents=(
            (
                "alpha.hpd",
                _hpd(
                    b"Unknown\tA",
                    b"Unknown\tB",
                ),
            ),
        )
    )
    candidate = _workspace(
        documents=(
            (
                "alpha.hpd",
                _hpd(
                    b"Unknown\tX",
                    b"Unknown\tY",
                ),
            ),
        )
    )
    valid = compare_favorites_workspaces(
        baseline,
        candidate,
    )
    source = valid.items[1]

    assert isinstance(source, FavoritesComparisonSource)

    with pytest.raises(
        ValueError,
        match="deterministic exact-record diff",
    ):
        FavoritesComparisonSource(
            source_kind=source.source_kind,
            state=source.state,
            filename=source.filename,
            baseline_document=source.baseline_document,
            candidate_document=source.candidate_document,
            baseline_source=source.baseline_source,
            candidate_source=source.candidate_source,
            record_changes=source.record_changes[:1],
        )


def test_source_rejects_copied_record_provenance() -> None:
    baseline = _workspace(
        documents=(
            ("alpha.hpd", b"Unknown\tA\r\n"),
        )
    )
    candidate = _workspace(
        documents=(
            ("alpha.hpd", b"Unknown\tB\r\n"),
        )
    )
    valid = compare_favorites_workspaces(
        baseline,
        candidate,
    )
    source = valid.items[1]

    assert isinstance(source, FavoritesComparisonSource)
    original = source.record_changes[0]
    assert original.baseline_record is not None

    copied_record = parse_favorites_file(
        original.baseline_record.raw_bytes
    ).records[0]
    copied_change = FavoritesComparisonRecordChange(
        kind=original.kind,
        baseline_source_index=original.baseline_source_index,
        baseline_record=copied_record,
        candidate_source_index=original.candidate_source_index,
        candidate_record=original.candidate_record,
    )

    with pytest.raises(
        ValueError,
        match="exact original source record",
    ):
        FavoritesComparisonSource(
            source_kind=source.source_kind,
            state=source.state,
            filename=source.filename,
            baseline_document=source.baseline_document,
            candidate_document=source.candidate_document,
            baseline_source=source.baseline_source,
            candidate_source=source.candidate_source,
            record_changes=(copied_change,),
        )


def test_workspace_comparison_rejects_copied_document_provenance() -> None:
    baseline = _workspace(
        documents=(
            ("alpha.hpd", b"Unknown\tA\r\n"),
        )
    )
    candidate = _workspace(
        documents=(
            ("alpha.hpd", b"Unknown\tA\r\n"),
        )
    )
    valid = compare_favorites_workspaces(
        baseline,
        candidate,
    )
    source = valid.items[1]

    assert isinstance(source, FavoritesComparisonSource)
    assert source.baseline_document is not None

    copied_baseline = _workspace(
        documents=(
            ("alpha.hpd", b"Unknown\tA\r\n"),
        )
    )
    copied_reference = FavoritesComparisonDocumentReference(
        document_index=0,
        document=copied_baseline.documents[0],
    )
    forged_source = FavoritesComparisonSource(
        source_kind=source.source_kind,
        state=source.state,
        filename=source.filename,
        baseline_document=copied_reference,
        candidate_document=source.candidate_document,
        baseline_source=copied_reference.source,
        candidate_source=source.candidate_source,
        record_changes=(),
    )

    with pytest.raises(
        ValueError,
        match="exact original workspace document",
    ):
        FavoritesWorkspaceComparison(
            baseline=baseline,
            candidate=candidate,
            items=(
                valid.items[0],
                forged_source,
            ),
        )
