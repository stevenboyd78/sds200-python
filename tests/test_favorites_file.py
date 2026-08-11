from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from sds200 import (
    FavoritesFileParseError,
    FavoritesSourceFile,
    FavoritesSourceRecord,
    parse_favorites_file,
)

_FIXTURE_DIRECTORY = (
    Path(__file__).parent / "fixtures" / "favorites"
)


def _records_for(
    source: FavoritesSourceFile,
    command: str,
) -> tuple[FavoritesSourceRecord, ...]:
    return tuple(
        record
        for record in source.records
        if record.command == command
    )


@pytest.mark.parametrize(
    "filename",
    (
        "synthetic-f_list.cfg",
        "synthetic-favorites.hpd",
    ),
)
def test_synthetic_favorites_fixture_round_trips_exactly(
    filename: str,
) -> None:
    data = (_FIXTURE_DIRECTORY / filename).read_bytes()

    source = parse_favorites_file(data)

    assert source.to_bytes() == data
    assert source.records
    assert all(
        record.line_ending == b"\r\n"
        for record in source.records
    )


def test_f_list_fixture_preserves_full_positional_record() -> None:
    data = (
        _FIXTURE_DIRECTORY / "synthetic-f_list.cfg"
    ).read_bytes()

    source = parse_favorites_file(data)

    favorites = _records_for(source, "F-List")

    assert len(favorites) == 1
    assert favorites[0].field_count == 117
    assert favorites[0].fields[:2] == (
        "Synthetic Favorites",
        "f_000001.hpd",
    )


def test_hpd_fixture_preserves_blank_ids_and_extension_shapes() -> None:
    data = (
        _FIXTURE_DIRECTORY / "synthetic-favorites.hpd"
    ).read_bytes()

    source = parse_favorites_file(data)

    conventional = _records_for(source, "Conventional")
    trunk = _records_for(source, "Trunk")
    t_freq = _records_for(source, "T-Freq")
    p25_band_plan = _records_for(source, "BandPlan_P25")
    tgid = _records_for(source, "TGID")
    unit_id = _records_for(source, "UnitID")

    assert conventional[0].fields[:2] == ("", "")
    assert trunk[0].fields[:2] == ("", "")

    assert [
        record.field_count
        for record in t_freq
    ] == [8, 9]
    assert t_freq[1].fields[-1] == "Any"

    assert [
        record.field_count
        for record in p25_band_plan
    ] == [34, 50]

    assert [
        record.field_count
        for record in tgid
    ] == [17, 18]
    assert tgid[1].fields[3] == "Extension"

    assert len(unit_id) == 1
    assert unit_id[0].fields == ()


def test_unknown_commands_and_empty_fields_are_not_normalized() -> None:
    data = (
        b"futureCommand\talpha\t\tomega\t\r\n"
        b"UnitID\r\n"
    )

    source = parse_favorites_file(data)

    first = source.records[0]

    assert first.command == "futureCommand"
    assert first.fields == (
        "alpha",
        "",
        "omega",
        "",
    )
    assert first.field_count == 5

    assert source.records[1].command == "UnitID"
    assert source.records[1].fields == ()
    assert source.to_bytes() == data


def test_mixed_line_endings_and_missing_final_newline_round_trip() -> None:
    data = (
        b"TargetModel\tBCDx36HP\r\n"
        b"Future\talpha\n"
        b"UnitID\r"
        b"Last\tvalue\t"
    )

    source = parse_favorites_file(data)

    assert tuple(
        record.line_ending
        for record in source.records
    ) == (
        b"\r\n",
        b"\n",
        b"\r",
        b"",
    )

    assert source.records[-1].fields == (
        "value",
        "",
    )
    assert source.to_bytes() == data


def test_empty_file_round_trips() -> None:
    source = parse_favorites_file(b"")

    assert source.records == ()
    assert source.to_bytes() == b""


def test_non_ascii_source_reports_physical_line_number() -> None:
    data = (
        b"TargetModel\tBCDx36HP\r\n"
        b"C-Group\t\xff\r\n"
    )

    with pytest.raises(
        FavoritesFileParseError,
        match="Favorites source line 2",
    ) as captured:
        parse_favorites_file(data)

    assert captured.value.line_number == 2


def test_parse_requires_bytes() -> None:
    with pytest.raises(
        TypeError,
        match="Favorites source file data must be bytes",
    ):
        parse_favorites_file(  # type: ignore[arg-type]
            "TargetModel\tBCDx36HP"
        )


def test_source_models_are_immutable() -> None:
    source = parse_favorites_file(
        b"TargetModel\tBCDx36HP\r\n"
    )
    record = source.records[0]

    with pytest.raises(FrozenInstanceError):
        record.content = b"changed"  # type: ignore[misc]

    with pytest.raises(FrozenInstanceError):
        source.records = ()  # type: ignore[misc]


def test_only_final_record_may_omit_line_ending() -> None:
    first = FavoritesSourceRecord(
        content=b"First",
        line_ending=b"",
    )
    second = FavoritesSourceRecord(
        content=b"Second",
        line_ending=b"\r\n",
    )

    with pytest.raises(
        ValueError,
        match="Only the final Favorites source record",
    ):
        FavoritesSourceFile(
            records=(first, second)
        )
