from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from sds200 import (
    FavoritesComparisonAmbiguity,
    FavoritesSchemaSeverity,
    FavoritesSchemaValidation,
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
    FavoritesWriteBlocker,
    FavoritesWritePlan,
    plan_favorites_write,
)

_VALID_CATALOG = (
    b"TargetModel\tBCDx36HP\r\n"
    b"FormatVersion\t1.00\r\n"
)

_VALID_HPD = _VALID_CATALOG


def _f_list(
    name: str,
    filename: str,
) -> bytes:
    fields = [
        name,
        filename,
        *("" for _ in range(114)),
    ]

    assert len(fields) == 116

    return "\t".join(
        (
            "F-List",
            *fields,
        )
    ).encode("ascii")


def _catalog(
    *records: bytes,
    metadata: bytes = _VALID_CATALOG,
) -> bytes:
    if not records:
        return metadata

    return (
        metadata
        + b"\r\n".join(records)
        + b"\r\n"
    )


def _snapshot(
    *,
    catalog: bytes = _VALID_CATALOG,
    documents: tuple[tuple[str, bytes], ...] = (),
) -> FavoritesStorageSnapshot:
    return FavoritesStorageSnapshot(
        catalog_bytes=catalog,
        documents=tuple(
            FavoritesStorageDocument(
                filename=filename,
                content=content,
            )
            for filename, content in documents
        ),
    )


def test_public_blocker_values_are_stable() -> None:
    assert tuple(
        blocker.value
        for blocker in FavoritesWriteBlocker
    ) == (
        "comparison_ambiguity",
        "intended_missing_entry",
        "intended_ambiguous_entry",
        "intended_duplicate_catalog_filename",
        "intended_duplicate_document_filename",
        "intended_schema_error",
    )


def test_clean_noop_plan_retains_exact_authoritative_inputs() -> None:
    snapshot = _snapshot()

    plan = plan_favorites_write(
        snapshot,
        snapshot,
    )

    assert plan.baseline_snapshot is snapshot
    assert plan.intended_snapshot is snapshot

    assert (
        plan.comparison.baseline
        is plan.baseline_workspace
    )
    assert (
        plan.comparison.candidate
        is plan.intended_workspace
    )

    assert (
        plan.baseline_validation.workspace
        is plan.baseline_workspace
    )
    assert (
        plan.intended_validation.workspace
        is plan.intended_workspace
    )

    assert plan.comparison.is_equal is True
    assert plan.baseline_validation.is_valid is True
    assert plan.intended_validation.is_valid is True

    assert plan.blockers == ()
    assert plan.has_changes is False
    assert plan.is_noop is True
    assert plan.is_blocked is False


def test_write_plan_is_frozen_and_slot_backed() -> None:
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(),
    )

    assert plan.__dataclass_params__.frozen is True
    assert "__slots__" in FavoritesWritePlan.__dict__
    assert "__dict__" not in FavoritesWritePlan.__dict__

    with pytest.raises(FrozenInstanceError):
        plan.blockers = ()  # type: ignore[misc]


def test_exact_snapshot_difference_drives_change_state() -> None:
    baseline = _snapshot(
        documents=(
            (
                "orphan.hpd",
                (
                    b"TargetModel\tBCDx36HP\r\n"
                    b"FormatVersion\t1.00\r\n"
                    b"FutureCommand\tone\r\n"
                ),
            ),
        ),
    )
    intended = _snapshot(
        documents=(
            (
                "orphan.hpd",
                (
                    b"TargetModel\tBCDx36HP\r\n"
                    b"FormatVersion\t1.00\r\n"
                    b"FutureCommand\tone\n"
                ),
            ),
        ),
    )

    plan = plan_favorites_write(
        baseline,
        intended,
    )

    assert plan.baseline_snapshot is baseline
    assert plan.intended_snapshot is intended
    assert plan.has_changes is True
    assert plan.is_noop is False
    assert plan.is_blocked is False
    assert plan.comparison.has_changes is True

    assert tuple(
        diagnostic.severity
        for diagnostic in plan.intended_validation.diagnostics
    ) == (
        FavoritesSchemaSeverity.INFO,
    )


def test_ambiguity_can_block_an_exact_noop_plan() -> None:
    duplicate = _snapshot(
        catalog=_catalog(
            _f_list(
                "Duplicate",
                "dup.hpd",
            )
        ),
        documents=(
            (
                "dup.hpd",
                _VALID_HPD,
            ),
            (
                "dup.hpd",
                _VALID_HPD,
            ),
        ),
    )

    plan = plan_favorites_write(
        duplicate,
        duplicate,
    )

    assert plan.has_changes is False
    assert plan.is_noop is True
    assert plan.comparison.has_ambiguity is True
    assert plan.comparison.has_changes is False

    assert plan.blockers == (
        FavoritesWriteBlocker.COMPARISON_AMBIGUITY,
        FavoritesWriteBlocker.INTENDED_AMBIGUOUS_ENTRY,
        FavoritesWriteBlocker.INTENDED_DUPLICATE_DOCUMENT_FILENAME,
    )
    assert plan.is_blocked is True


def test_baseline_only_comparison_ambiguity_blocks_plan() -> None:
    baseline = _snapshot(
        catalog=_catalog(
            _f_list(
                "Duplicate",
                "dup.hpd",
            )
        ),
        documents=(
            (
                "dup.hpd",
                _VALID_HPD,
            ),
            (
                "dup.hpd",
                _VALID_HPD,
            ),
        ),
    )
    intended = _snapshot(
        catalog=_catalog(
            _f_list(
                "Duplicate",
                "dup.hpd",
            )
        ),
        documents=(
            (
                "dup.hpd",
                _VALID_HPD,
            ),
        ),
    )

    plan = plan_favorites_write(
        baseline,
        intended,
    )

    assert plan.intended_workspace.ambiguous_entries == ()
    assert (
        plan.intended_workspace.duplicate_document_filenames
        == ()
    )

    assert any(
        isinstance(
            item,
            FavoritesComparisonAmbiguity,
        )
        for item in plan.comparison.items
    )

    assert plan.blockers == (
        FavoritesWriteBlocker.COMPARISON_AMBIGUITY,
    )


def test_missing_intended_catalog_target_is_blocker() -> None:
    intended = _snapshot(
        catalog=_catalog(
            _f_list(
                "Missing",
                "missing.hpd",
            )
        ),
    )

    plan = plan_favorites_write(
        _snapshot(),
        intended,
    )

    assert [
        entry.filename
        for entry in plan.intended_workspace.missing_entries
    ] == [
        "missing.hpd",
    ]

    assert plan.intended_validation.is_valid is True
    assert plan.blockers == (
        FavoritesWriteBlocker.INTENDED_MISSING_ENTRY,
    )


def test_duplicate_intended_catalog_filename_is_blocker() -> None:
    intended = _snapshot(
        catalog=_catalog(
            _f_list(
                "First",
                "dup.hpd",
            ),
            _f_list(
                "Second",
                "dup.hpd",
            ),
        ),
        documents=(
            (
                "dup.hpd",
                _VALID_HPD,
            ),
        ),
    )

    plan = plan_favorites_write(
        _snapshot(),
        intended,
    )

    assert (
        plan.intended_workspace.duplicate_catalog_filenames
        == (
            "dup.hpd",
        )
    )
    assert plan.intended_workspace.ambiguous_entries == ()
    assert plan.intended_validation.is_valid is True

    assert plan.blockers == (
        FavoritesWriteBlocker.INTENDED_DUPLICATE_CATALOG_FILENAME,
    )


def test_duplicate_intended_document_reports_all_related_blockers() -> None:
    intended = _snapshot(
        catalog=_catalog(
            _f_list(
                "Duplicate",
                "dup.hpd",
            )
        ),
        documents=(
            (
                "dup.hpd",
                _VALID_HPD,
            ),
            (
                "dup.hpd",
                _VALID_HPD,
            ),
        ),
    )

    plan = plan_favorites_write(
        _snapshot(),
        intended,
    )

    assert [
        entry.filename
        for entry in plan.intended_workspace.ambiguous_entries
    ] == [
        "dup.hpd",
    ]
    assert (
        plan.intended_workspace.duplicate_document_filenames
        == (
            "dup.hpd",
        )
    )

    assert plan.blockers == (
        FavoritesWriteBlocker.COMPARISON_AMBIGUITY,
        FavoritesWriteBlocker.INTENDED_AMBIGUOUS_ENTRY,
        FavoritesWriteBlocker.INTENDED_DUPLICATE_DOCUMENT_FILENAME,
    )


def test_intended_schema_error_is_blocker() -> None:
    intended = _snapshot(
        catalog=(
            b"TargetModel\tWrongModel\r\n"
            b"FormatVersion\t1.00\r\n"
        )
    )

    plan = plan_favorites_write(
        _snapshot(),
        intended,
    )

    assert plan.intended_validation.is_valid is False
    assert any(
        diagnostic.severity
        is FavoritesSchemaSeverity.ERROR
        for diagnostic in plan.intended_validation.diagnostics
    )

    assert plan.blockers == (
        FavoritesWriteBlocker.INTENDED_SCHEMA_ERROR,
    )


def test_baseline_schema_error_does_not_block_repair() -> None:
    baseline = _snapshot(
        catalog=(
            b"TargetModel\tWrongModel\r\n"
            b"FormatVersion\t1.00\r\n"
        )
    )
    intended = _snapshot()

    plan = plan_favorites_write(
        baseline,
        intended,
    )

    assert plan.baseline_validation.is_valid is False
    assert plan.intended_validation.is_valid is True
    assert plan.has_changes is True
    assert plan.blockers == ()
    assert plan.is_blocked is False


def test_warnings_and_info_remain_reviewable_not_blocking() -> None:
    intended = _snapshot(
        catalog=(
            b"TargetModel\tBCDx36HP\r\n"
            b"FormatVersion\t2.00\r\n"
            b"FutureCatalog\tone\ttwo\r\n"
        )
    )

    plan = plan_favorites_write(
        _snapshot(),
        intended,
    )

    assert plan.intended_validation.is_valid is True
    assert tuple(
        diagnostic.severity
        for diagnostic in plan.intended_validation.diagnostics
    ) == (
        FavoritesSchemaSeverity.WARNING,
        FavoritesSchemaSeverity.INFO,
    )

    assert plan.blockers == ()
    assert plan.is_blocked is False


def test_orphan_document_remains_previewable_not_blocking() -> None:
    intended = _snapshot(
        documents=(
            (
                "orphan.hpd",
                _VALID_HPD,
            ),
        ),
    )

    plan = plan_favorites_write(
        _snapshot(),
        intended,
    )

    assert [
        document.filename
        for document in plan.intended_workspace.orphan_documents
    ] == [
        "orphan.hpd",
    ]

    assert plan.intended_validation.is_valid is True
    assert plan.comparison.has_changes is True
    assert plan.blockers == ()
    assert plan.is_blocked is False


def test_stale_target_precondition_uses_exact_snapshot_equality() -> None:
    baseline = _snapshot(
        documents=(
            (
                "one.hpd",
                b"Future\tone\r\n",
            ),
        ),
    )

    equal_copy = _snapshot(
        documents=(
            (
                "one.hpd",
                b"Future\tone\r\n",
            ),
        ),
    )

    line_ending_change = _snapshot(
        documents=(
            (
                "one.hpd",
                b"Future\tone\n",
            ),
        ),
    )

    plan = plan_favorites_write(
        baseline,
        _snapshot(),
    )

    assert equal_copy == baseline
    assert equal_copy is not baseline

    assert (
        plan.matches_baseline_snapshot(
            baseline
        )
        is True
    )
    assert (
        plan.matches_baseline_snapshot(
            equal_copy
        )
        is True
    )
    assert (
        plan.matches_baseline_snapshot(
            line_ending_change
        )
        is False
    )


def test_stale_target_check_requires_storage_snapshot() -> None:
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(),
    )

    with pytest.raises(
        TypeError,
        match="target check requires FavoritesStorageSnapshot",
    ):
        plan.matches_baseline_snapshot(  # type: ignore[arg-type]
            object()
        )


def test_plan_constructor_rejects_inconsistent_blockers() -> None:
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(),
    )

    with pytest.raises(
        ValueError,
        match="blockers must match",
    ):
        replace(
            plan,
            blockers=(
                FavoritesWriteBlocker.INTENDED_SCHEMA_ERROR,
            ),
        )


def test_plan_constructor_rejects_snapshot_workspace_mismatch() -> None:
    plan = plan_favorites_write(
        _snapshot(),
        _snapshot(),
    )

    changed = _snapshot(
        catalog=(
            b"TargetModel\tBCDx36HP\r\n"
            b"FormatVersion\t2.00\r\n"
        )
    )

    with pytest.raises(
        ValueError,
        match="baseline workspace must preserve",
    ):
        replace(
            plan,
            baseline_snapshot=changed,
        )


@pytest.mark.parametrize(
    "side",
    (
        "baseline",
        "intended",
    ),
)
def test_plan_constructor_rejects_incomplete_schema_evidence(
    side: str,
) -> None:
    invalid = _snapshot(
        catalog=(
            b"TargetModel\tWrongModel\r\n"
            b"FormatVersion\t1.00\r\n"
        )
    )
    valid = _snapshot()

    if side == "baseline":
        plan = plan_favorites_write(
            invalid,
            valid,
        )
        forged = FavoritesSchemaValidation(
            workspace=plan.baseline_workspace,
            diagnostics=(),
        )

        with pytest.raises(
            ValueError,
            match="baseline validation must match",
        ):
            replace(
                plan,
                baseline_validation=forged,
            )
    else:
        plan = plan_favorites_write(
            valid,
            invalid,
        )
        forged = FavoritesSchemaValidation(
            workspace=plan.intended_workspace,
            diagnostics=(),
        )

        with pytest.raises(
            ValueError,
            match="intended validation must match",
        ):
            replace(
                plan,
                intended_validation=forged,
            )


@pytest.mark.parametrize(
    "argument",
    (
        "baseline",
        "intended",
    ),
)
def test_planning_requires_storage_snapshots(
    argument: str,
) -> None:
    snapshot = _snapshot()

    with pytest.raises(
        TypeError,
        match="requires FavoritesStorageSnapshot",
    ):
        if argument == "baseline":
            plan_favorites_write(  # type: ignore[arg-type]
                object(),
                snapshot,
            )
        else:
            plan_favorites_write(  # type: ignore[arg-type]
                snapshot,
                object(),
            )
