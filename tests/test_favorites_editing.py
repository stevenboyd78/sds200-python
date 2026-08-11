from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from sds200 import (
    FavoritesRecordEditError,
    FavoritesRecordSourceKind,
    FavoritesRecordTarget,
    FavoritesSourceRecord,
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
    create_favorites_record_after,
    delete_favorites_record,
    plan_favorites_write,
    rename_favorites_record,
    select_favorites_record_target,
)

_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "favorites"
_CATALOG = (_FIXTURE_ROOT / "synthetic-f_list.cfg").read_bytes()
_HPD = (_FIXTURE_ROOT / "synthetic-favorites.hpd").read_bytes()


def _snapshot(
    *,
    catalog: bytes = _CATALOG,
    documents: tuple[tuple[str, bytes], ...] = (
        (
            "f_000001.hpd",
            _HPD,
        ),
    ),
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


def _f_list(
    name: str,
    filename: str,
    *,
    trailing_extension: bool = False,
) -> bytes:
    fields = [
        name,
        filename,
        *("Off" for _ in range(114)),
    ]

    if trailing_extension:
        fields.append("")

    return (
        "F-List\t"
        + "\t".join(fields)
        + "\r\n"
    ).encode("ascii")


def _minimal_hpd(
    *records: bytes,
) -> bytes:
    return b"".join(
        (
            b"TargetModel\tBCDx36HP\r\n",
            b"FormatVersion\t1.00\r\n",
            *records,
        )
    )


def test_record_source_kind_values_are_stable() -> None:
    assert tuple(
        item.value
        for item in FavoritesRecordSourceKind
    ) == (
        "catalog",
        "hpd",
    )


def test_select_catalog_target_retains_exact_record() -> None:
    snapshot = _snapshot()

    target = select_favorites_record_target(
        snapshot,
        2,
    )

    assert target.source_kind is FavoritesRecordSourceKind.CATALOG
    assert target.source_index == 2
    assert target.document_index is None
    assert target.filename is None
    assert target.record.raw_bytes == (
        snapshot.catalog_bytes.splitlines(
            keepends=True
        )[2]
    )


def test_select_hpd_target_retains_exact_document_provenance() -> None:
    snapshot = _snapshot()

    target = select_favorites_record_target(
        snapshot,
        5,
        document_index=0,
    )

    assert target.source_kind is FavoritesRecordSourceKind.HPD
    assert target.document_index == 0
    assert target.filename == "f_000001.hpd"
    assert target.source_index == 5
    assert target.record.command == "C-Freq"


def test_record_target_is_frozen_and_slot_backed() -> None:
    target = select_favorites_record_target(
        _snapshot(),
        2,
    )

    assert target.__dataclass_params__.frozen is True
    assert not hasattr(target, "__dict__")

    with pytest.raises(FrozenInstanceError):
        target.source_index = 8  # type: ignore[misc]


@pytest.mark.parametrize(
    ("source_index", "document_index", "match"),
    (
        (-1, None, "source index"),
        (True, None, "source index"),
        (0, -1, "document index"),
        (0, True, "document index"),
    ),
)
def test_selection_rejects_invalid_indexes(
    source_index: int,
    document_index: int | None,
    match: str,
) -> None:
    with pytest.raises(
        ValueError,
        match=match,
    ):
        select_favorites_record_target(
            _snapshot(),
            source_index,
            document_index=document_index,
        )


def test_selection_rejects_missing_catalog_mapping() -> None:
    snapshot = _snapshot(
        catalog=(
            b"TargetModel\tBCDx36HP\r\n"
            b"FormatVersion\t1.00\r\n"
            + _f_list(
                "Missing",
                "missing.hpd",
            )
        ),
        documents=(),
    )

    with pytest.raises(
        FavoritesRecordEditError,
        match="mapped HPD document is missing",
    ):
        select_favorites_record_target(
            snapshot,
            2,
        )


def test_selection_rejects_orphan_hpd_document() -> None:
    snapshot = _snapshot(
        catalog=(
            b"TargetModel\tBCDx36HP\r\n"
            b"FormatVersion\t1.00\r\n"
        ),
        documents=(
            (
                "orphan.hpd",
                _minimal_hpd(
                    b"Future\tvalue\r\n"
                ),
            ),
        ),
    )

    with pytest.raises(
        FavoritesRecordEditError,
        match="not bound to a catalog entry",
    ):
        select_favorites_record_target(
            snapshot,
            2,
            document_index=0,
        )


def test_selection_rejects_duplicate_hpd_documents() -> None:
    catalog = (
        b"TargetModel\tBCDx36HP\r\n"
        b"FormatVersion\t1.00\r\n"
        + _f_list(
            "Duplicate",
            "dup.hpd",
        )
    )
    content = _minimal_hpd()

    snapshot = _snapshot(
        catalog=catalog,
        documents=(
            ("dup.hpd", content),
            ("dup.hpd", content),
        ),
    )

    with pytest.raises(
        FavoritesRecordEditError,
        match="duplicate HPD documents",
    ):
        select_favorites_record_target(
            snapshot,
            0,
            document_index=0,
        )


def test_selection_rejects_duplicate_catalog_ownership() -> None:
    catalog = (
        b"TargetModel\tBCDx36HP\r\n"
        b"FormatVersion\t1.00\r\n"
        + _f_list(
            "First",
            "dup.hpd",
        )
        + _f_list(
            "Second",
            "dup.hpd",
        )
    )

    snapshot = _snapshot(
        catalog=catalog,
        documents=(
            (
                "dup.hpd",
                _minimal_hpd(),
            ),
        ),
    )

    with pytest.raises(
        FavoritesRecordEditError,
        match="duplicate catalog",
    ):
        select_favorites_record_target(
            snapshot,
            2,
        )

    with pytest.raises(
        FavoritesRecordEditError,
        match="duplicate catalog",
    ):
        select_favorites_record_target(
            snapshot,
            0,
            document_index=0,
        )


def test_rename_catalog_entry_preserves_trailing_extension() -> None:
    catalog = (
        b"TargetModel\tBCDx36HP\r\n"
        b"FormatVersion\t1.00\r\n"
        + _f_list(
            "Original",
            "one.hpd",
            trailing_extension=True,
        )
    )
    snapshot = _snapshot(
        catalog=catalog,
        documents=(
            (
                "one.hpd",
                _minimal_hpd(),
            ),
        ),
    )
    target = select_favorites_record_target(
        snapshot,
        2,
    )

    assert target.record.field_count == 118
    assert target.record.fields[-1] == ""

    intended = rename_favorites_record(
        snapshot,
        target,
        "Renamed",
    )
    renamed = select_favorites_record_target(
        intended,
        2,
    ).record

    assert renamed.field_count == 118
    assert renamed.fields[0] == "Renamed"
    assert renamed.fields[1:] == target.record.fields[1:]
    assert renamed.line_ending == b"\r\n"


def test_rename_extended_tgid_preserves_unknown_position() -> None:
    snapshot = _snapshot()
    target = select_favorites_record_target(
        snapshot,
        15,
        document_index=0,
    )

    assert target.record.command == "TGID"
    assert target.record.field_count == 18
    assert target.record.fields[3] == "Extension"

    intended = rename_favorites_record(
        snapshot,
        target,
        "Renamed Talkgroup",
    )
    renamed = select_favorites_record_target(
        intended,
        15,
        document_index=0,
    ).record

    assert renamed.field_count == 18
    assert renamed.fields[2] == "Renamed Talkgroup"
    assert renamed.fields[3:] == target.record.fields[3:]
    assert renamed.line_ending == target.record.line_ending


def test_rename_extended_c_freq_preserves_unknown_position() -> None:
    extended = (
        b"C-Freq\t\t\tInput Frequency\tOff\t147740000"
        b"\t147740000\t\t13\tOff\t2\t0\tOff\tAuto"
        b"\tOff\tOn\tOn\tOff\tOff\r\n"
    )
    hpd = _minimal_hpd(
        b"Conventional\t\t\tSystem\r\n",
        b"C-Group\t\t\tDepartment\r\n",
        extended,
    )
    catalog = (
        b"TargetModel\tBCDx36HP\r\n"
        b"FormatVersion\t1.00\r\n"
        + _f_list(
            "Extended",
            "extended.hpd",
        )
    )
    snapshot = _snapshot(
        catalog=catalog,
        documents=(
            ("extended.hpd", hpd),
        ),
    )
    target = select_favorites_record_target(
        snapshot,
        4,
        document_index=0,
    )

    assert target.record.field_count == 19

    intended = rename_favorites_record(
        snapshot,
        target,
        "Renamed Frequency",
    )
    renamed = select_favorites_record_target(
        intended,
        4,
        document_index=0,
    ).record

    assert renamed.field_count == 19
    assert renamed.fields[2] == "Renamed Frequency"
    assert renamed.fields[3:] == target.record.fields[3:]


@pytest.mark.parametrize(
    "name",
    (
        "x" * 65,
        "bad\tname",
        "bad\nname",
        "Café",
    ),
)
def test_rename_rejects_unsupported_name_values(
    name: str,
) -> None:
    snapshot = _snapshot()
    target = select_favorites_record_target(
        snapshot,
        5,
        document_index=0,
    )

    with pytest.raises(
        FavoritesRecordEditError,
        match="printable ASCII",
    ):
        rename_favorites_record(
            snapshot,
            target,
            name,
        )


def test_rename_rejects_record_without_supported_name_field() -> None:
    snapshot = _snapshot()
    target = select_favorites_record_target(
        snapshot,
        9,
        document_index=0,
    )

    assert target.record.command == "T-Freq"

    with pytest.raises(
        FavoritesRecordEditError,
        match="does not have a supported editable name field",
    ):
        rename_favorites_record(
            snapshot,
            target,
            "Name",
        )


def test_target_rejects_stale_exact_record() -> None:
    snapshot = _snapshot()
    target = select_favorites_record_target(
        snapshot,
        5,
        document_index=0,
    )

    changed = _snapshot(
        documents=(
            (
                "f_000001.hpd",
                snapshot.documents[0].content.replace(
                    b"Synthetic Channel",
                    b"Changed Channel",
                    1,
                ),
            ),
        ),
    )

    with pytest.raises(
        FavoritesRecordEditError,
        match="no longer matches the exact source record",
    ):
        rename_favorites_record(
            changed,
            target,
            "Another Name",
        )


def test_target_rejects_document_reordering() -> None:
    first = _minimal_hpd(
        b"Conventional\t\t\tSystem One\r\n"
    )
    second = _minimal_hpd(
        b"Conventional\t\t\tSystem Two\r\n"
    )
    catalog = (
        b"TargetModel\tBCDx36HP\r\n"
        b"FormatVersion\t1.00\r\n"
        + _f_list("One", "one.hpd")
        + _f_list("Two", "two.hpd")
    )
    snapshot = _snapshot(
        catalog=catalog,
        documents=(
            ("one.hpd", first),
            ("two.hpd", second),
        ),
    )
    target = select_favorites_record_target(
        snapshot,
        2,
        document_index=0,
    )
    reordered = _snapshot(
        catalog=catalog,
        documents=(
            ("two.hpd", second),
            ("one.hpd", first),
        ),
    )

    with pytest.raises(
        FavoritesRecordEditError,
        match="exact filename provenance",
    ):
        rename_favorites_record(
            reordered,
            target,
            "Renamed",
        )


def test_delete_conventional_channel_preserves_other_bytes() -> None:
    snapshot = _snapshot()
    target = select_favorites_record_target(
        snapshot,
        5,
        document_index=0,
    )

    intended = delete_favorites_record(
        snapshot,
        target,
    )

    before = snapshot.documents[0].content.splitlines(
        keepends=True
    )
    after = intended.documents[0].content.splitlines(
        keepends=True
    )

    assert after == (
        before[:5]
        + before[6:]
    )
    assert snapshot.documents[0].content == _HPD


def test_delete_extended_tgid_is_exact_leaf_removal() -> None:
    snapshot = _snapshot()
    target = select_favorites_record_target(
        snapshot,
        15,
        document_index=0,
    )

    assert target.record.command == "TGID"
    assert target.record.field_count == 18

    intended = delete_favorites_record(
        snapshot,
        target,
    )

    assert target.record.raw_bytes not in intended.documents[0].content
    assert b"FutureCommand\talpha\t\tomega\r\n" in (
        intended.documents[0].content
    )


@pytest.mark.parametrize(
    "source_index",
    (
        2,
        4,
        6,
        8,
        13,
        16,
        17,
        18,
    ),
)
def test_delete_rejects_parent_or_unresolved_records(
    source_index: int,
) -> None:
    snapshot = _snapshot()
    target = select_favorites_record_target(
        snapshot,
        source_index,
        document_index=0,
    )

    with pytest.raises(
        FavoritesRecordEditError,
        match="hierarchy ownership|unresolved record semantics",
    ):
        delete_favorites_record(
            snapshot,
            target,
        )


def test_delete_rejects_catalog_record() -> None:
    snapshot = _snapshot()
    target = select_favorites_record_target(
        snapshot,
        2,
    )

    with pytest.raises(
        FavoritesRecordEditError,
        match="Catalog record deletion is not supported",
    ):
        delete_favorites_record(
            snapshot,
            target,
        )


def test_edited_snapshot_flows_through_write_planning() -> None:
    snapshot = _snapshot()
    target = select_favorites_record_target(
        snapshot,
        5,
        document_index=0,
    )
    intended = rename_favorites_record(
        snapshot,
        target,
        "Planned Channel",
    )

    plan = plan_favorites_write(
        snapshot,
        intended,
    )

    assert plan.baseline_snapshot is snapshot
    assert plan.intended_snapshot is intended
    assert plan.has_changes is True
    assert plan.is_noop is False
    assert plan.is_blocked is False
    assert plan.intended_validation.is_valid is True


@pytest.mark.parametrize(
    "argument",
    (
        "snapshot",
        "target",
    ),
)
def test_rename_requires_exact_model_types(
    argument: str,
) -> None:
    snapshot = _snapshot()
    target = select_favorites_record_target(
        snapshot,
        2,
    )

    with pytest.raises(TypeError):
        if argument == "snapshot":
            rename_favorites_record(  # type: ignore[arg-type]
                object(),
                target,
                "Name",
            )
        else:
            rename_favorites_record(  # type: ignore[arg-type]
                snapshot,
                object(),
                "Name",
            )


def test_target_constructor_rejects_mismatched_catalog_provenance() -> None:
    record = FavoritesSourceRecord(
        content=b"F-List\tName\tf_000001.hpd",
        line_ending=b"\r\n",
    )

    with pytest.raises(
        ValueError,
        match="must not have a document index",
    ):
        FavoritesRecordTarget(
            source_kind=FavoritesRecordSourceKind.CATALOG,
            source_index=0,
            record=record,
            document_index=0,
        )


def test_target_constructor_rejects_missing_hpd_provenance() -> None:
    record = FavoritesSourceRecord(
        content=b"C-Freq\t\t\tName",
        line_ending=b"\r\n",
    )

    with pytest.raises(
        ValueError,
        match="document index",
    ):
        FavoritesRecordTarget(
            source_kind=FavoritesRecordSourceKind.HPD,
            source_index=0,
            record=record,
        )


def test_create_c_freq_after_group_is_exact_deterministic_insertion() -> None:
    snapshot = _snapshot()
    anchor = select_favorites_record_target(
        snapshot,
        4,
        document_index=0,
    )
    template = select_favorites_record_target(
        snapshot,
        5,
        document_index=0,
    ).record

    intended = create_favorites_record_after(
        snapshot,
        anchor,
        template,
        name="Created Channel",
    )

    before = snapshot.documents[0].content.splitlines(
        keepends=True
    )
    after = intended.documents[0].content.splitlines(
        keepends=True
    )

    assert after[:5] == before[:5]
    assert after[6:] == before[5:]
    assert after[5].startswith(
        b"C-Freq\t\t\tCreated Channel\t"
    )
    assert snapshot.documents[0].content == _HPD

    plan = plan_favorites_write(
        snapshot,
        intended,
    )

    assert plan.has_changes is True
    assert plan.is_blocked is False


def test_create_extended_tgid_preserves_unknown_position() -> None:
    snapshot = _snapshot()
    anchor = select_favorites_record_target(
        snapshot,
        14,
        document_index=0,
    )
    template = select_favorites_record_target(
        snapshot,
        15,
        document_index=0,
    ).record

    intended = create_favorites_record_after(
        snapshot,
        anchor,
        template,
        name="Created Extended TGID",
    )
    created = select_favorites_record_target(
        intended,
        15,
        document_index=0,
    ).record

    assert created.field_count == 18
    assert created.fields[2] == "Created Extended TGID"
    assert created.fields[3:] == template.fields[3:]


def test_create_t_freq_after_site_preserves_exact_template() -> None:
    snapshot = _snapshot()
    anchor = select_favorites_record_target(
        snapshot,
        8,
        document_index=0,
    )
    template = select_favorites_record_target(
        snapshot,
        9,
        document_index=0,
    ).record

    intended = create_favorites_record_after(
        snapshot,
        anchor,
        template,
    )
    created = select_favorites_record_target(
        intended,
        9,
        document_index=0,
    ).record

    assert created == template
    assert created.raw_bytes == template.raw_bytes


def test_create_allows_reviewable_extended_shape_warning() -> None:
    extended = FavoritesSourceRecord(
        content=(
            b"C-Freq\t\t\tExtended Created\tOff\t147740000"
            b"\t147740000\t\t13\tOff\t2\t0\tOff\tAuto"
            b"\tOff\tOn\tOn\tOff\tOff"
        ),
        line_ending=b"\r\n",
    )
    snapshot = _snapshot()
    anchor = select_favorites_record_target(
        snapshot,
        4,
        document_index=0,
    )

    assert extended.field_count == 19

    intended = create_favorites_record_after(
        snapshot,
        anchor,
        extended,
    )
    plan = plan_favorites_write(
        snapshot,
        intended,
    )

    assert plan.intended_validation.is_valid is True
    assert plan.is_blocked is False
    assert any(
        diagnostic.command == "C-Freq"
        and diagnostic.source_index == 5
        for diagnostic in plan.intended_validation.diagnostics
    )


def test_create_rejects_unsafe_hierarchy_anchor() -> None:
    snapshot = _snapshot()
    anchor = select_favorites_record_target(
        snapshot,
        6,
        document_index=0,
    )
    template = select_favorites_record_target(
        snapshot,
        5,
        document_index=0,
    ).record

    with pytest.raises(
        FavoritesRecordEditError,
        match="without changing or guessing hierarchy ownership",
    ):
        create_favorites_record_after(
            snapshot,
            anchor,
            template,
        )


def test_create_rejects_parent_template() -> None:
    snapshot = _snapshot()
    anchor = select_favorites_record_target(
        snapshot,
        5,
        document_index=0,
    )
    template = select_favorites_record_target(
        snapshot,
        2,
        document_index=0,
    ).record

    with pytest.raises(
        FavoritesRecordEditError,
        match="without evidence-backed leaf-record semantics",
    ):
        create_favorites_record_after(
            snapshot,
            anchor,
            template,
        )


def test_create_rejects_schema_error_in_new_record() -> None:
    snapshot = _snapshot()
    anchor = select_favorites_record_target(
        snapshot,
        4,
        document_index=0,
    )
    short = FavoritesSourceRecord(
        content=b"C-Freq\t\t\tShort",
        line_ending=b"\r\n",
    )

    with pytest.raises(
        FavoritesRecordEditError,
        match="created record is not schema-valid",
    ):
        create_favorites_record_after(
            snapshot,
            anchor,
            short,
        )


def test_create_rejects_missing_final_newline_anchor_without_rewrite() -> None:
    catalog = (
        b"TargetModel\tBCDx36HP\r\n"
        b"FormatVersion\t1.00\r\n"
        + _f_list(
            "No newline",
            "nonewline.hpd",
        )
    )
    hpd = (
        b"TargetModel\tBCDx36HP\r\n"
        b"FormatVersion\t1.00\r\n"
        b"Conventional\t\t\tSystem\r\n"
        b"C-Group\t\t\tDepartment"
    )
    snapshot = _snapshot(
        catalog=catalog,
        documents=(
            (
                "nonewline.hpd",
                hpd,
            ),
        ),
    )
    anchor = select_favorites_record_target(
        snapshot,
        3,
        document_index=0,
    )
    template = select_favorites_record_target(
        _snapshot(),
        5,
        document_index=0,
    ).record

    with pytest.raises(
        FavoritesRecordEditError,
        match="omits its final line ending",
    ):
        create_favorites_record_after(
            snapshot,
            anchor,
            template,
        )

    assert snapshot.documents[0].content == hpd


def test_create_requires_template_line_ending_before_existing_record() -> None:
    snapshot = _snapshot()
    anchor = select_favorites_record_target(
        snapshot,
        4,
        document_index=0,
    )
    template = FavoritesSourceRecord(
        content=(
            b"C-Freq\t\t\tCreated\tOff\t155000000\tNFM\t\t2"
            b"\tOff\t2\t0\tOff\tAuto\tOff\tOn\tOff\tOff"
        ),
        line_ending=b"",
    )

    with pytest.raises(
        FavoritesRecordEditError,
        match="template must include a line ending",
    ):
        create_favorites_record_after(
            snapshot,
            anchor,
            template,
        )


def test_create_requires_exact_template_type() -> None:
    snapshot = _snapshot()
    anchor = select_favorites_record_target(
        snapshot,
        4,
        document_index=0,
    )

    with pytest.raises(
        TypeError,
        match="template must be FavoritesSourceRecord",
    ):
        create_favorites_record_after(  # type: ignore[arg-type]
            snapshot,
            anchor,
            object(),
        )
