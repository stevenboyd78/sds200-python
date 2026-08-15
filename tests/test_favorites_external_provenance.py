from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

import sds200
from sds200 import (
    FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_BYTES,
    FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_FIELDS_PER_RECORD,
    FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_RECORDS,
    FAVORITES_EXTERNAL_PROVENANCE_SCHEMA,
    FAVORITES_EXTERNAL_PROVENANCE_VERSION,
    FavoritesExternalFieldBinding,
    FavoritesExternalFieldObservation,
    FavoritesExternalFieldObservationState,
    FavoritesExternalFieldOwnership,
    FavoritesExternalFieldState,
    FavoritesExternalObservationEvidence,
    FavoritesExternalProvenanceError,
    FavoritesExternalRecordIdentity,
    FavoritesExternalRecordObservation,
    FavoritesExternalRecordState,
    FavoritesExternalSourceIdentity,
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
    bind_favorites_external_record,
    deserialize_favorites_external_provenance,
    detach_favorites_external_record,
    select_favorites_record_target,
    serialize_favorites_external_provenance,
)

_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "favorites"


def _snapshot() -> FavoritesStorageSnapshot:
    return FavoritesStorageSnapshot(
        catalog_bytes=(_FIXTURE_ROOT / "synthetic-f_list.cfg").read_bytes(),
        documents=(
            FavoritesStorageDocument(
                filename="f_000001.hpd",
                content=(
                    _FIXTURE_ROOT / "synthetic-favorites.hpd"
                ).read_bytes(),
            ),
        ),
    )


def _identity(
    record_id: str = "channel-101",
) -> FavoritesExternalRecordIdentity:
    return FavoritesExternalRecordIdentity(
        source=FavoritesExternalSourceIdentity(
            provider="synthetic-provider",
            dataset="metro",
        ),
        record_id=record_id,
    )


def _evidence(
    revision: str = "accepted-r1",
) -> FavoritesExternalObservationEvidence:
    return FavoritesExternalObservationEvidence(
        observed_at=datetime(2026, 8, 15, 2, 0, tzinfo=UTC),
        revision=revision,
    )


def _value(
    name: str,
    value: str,
) -> FavoritesExternalFieldObservation:
    return FavoritesExternalFieldObservation(
        name=name,
        state=FavoritesExternalFieldObservationState.VALUE,
        value=value,
    )


def _linked_state(
    snapshot: FavoritesStorageSnapshot,
) -> FavoritesExternalRecordState:
    target = select_favorites_record_target(
        snapshot,
        5,
        document_index=0,
    )
    observation = FavoritesExternalRecordObservation(
        identity=_identity(),
        evidence=_evidence(),
        fields=(
            _value(
                "name",
                target.record.fields[2],
            ),
        ),
    )
    return bind_favorites_external_record(
        target,
        observation,
        (
            FavoritesExternalFieldBinding(
                name="name",
                field_index=2,
                ownership=FavoritesExternalFieldOwnership.EXTERNAL,
            ),
        ),
    )


def _canonical_payload_bytes(
    payload: object,
) -> bytes:
    return (
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def test_provenance_constants_are_stable_public_contract() -> None:
    assert FAVORITES_EXTERNAL_PROVENANCE_SCHEMA == (
        "sds200.favorites-external-provenance"
    )
    assert FAVORITES_EXTERNAL_PROVENANCE_VERSION == 1
    assert FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_BYTES == 1024 * 1024
    assert FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_RECORDS == 4_096
    assert FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_FIELDS_PER_RECORD == 256


def test_linked_provenance_round_trip_rebinds_exact_snapshot_target() -> None:
    snapshot = _snapshot()
    state = _linked_state(snapshot)

    content = serialize_favorites_external_provenance((state,))
    restored = deserialize_favorites_external_provenance(
        content,
        snapshot,
    )

    assert restored == (state,)
    assert restored[0].target is not state.target
    assert restored[0].target == state.target
    assert content.endswith(b"\n")

    payload = json.loads(content)
    record = payload["records"][0]
    target = record["target"]
    assert target == {
        "document_index": 0,
        "filename": "f_000001.hpd",
        "record_sha256": target["record_sha256"],
        "source_index": 5,
        "source_kind": "hpd",
    }
    assert len(target["record_sha256"]) == 64
    assert state.target.record.raw_bytes not in content


def test_detached_provenance_round_trip_preserves_last_external_evidence() -> None:
    snapshot = _snapshot()
    state = detach_favorites_external_record(_linked_state(snapshot))

    content = serialize_favorites_external_provenance((state,))
    restored = deserialize_favorites_external_provenance(
        content,
        snapshot,
    )

    assert restored == (state,)
    assert restored[0].detached is True
    assert restored[0].fields[0].ownership is (
        FavoritesExternalFieldOwnership.DETACHED
    )
    assert restored[0].fields[0].last_external == state.fields[0].last_external


def test_serialization_omits_raw_programming_and_credential_schema_fields() -> None:
    snapshot = _snapshot()
    state = _linked_state(snapshot)

    content = serialize_favorites_external_provenance((state,))
    text = content.decode("utf-8")

    assert state.target.record.raw_bytes not in content
    for forbidden_key in (
        '"application_key"',
        '"app_key"',
        '"cookie"',
        '"password"',
        '"secret"',
        '"token"',
        '"username"',
    ):
        assert forbidden_key not in text


def test_serialization_rejects_local_only_and_duplicate_provenance() -> None:
    snapshot = _snapshot()
    linked = _linked_state(snapshot)
    local_only = FavoritesExternalRecordState(
        target=linked.target,
        fields=(
            FavoritesExternalFieldState(
                name="name",
                field_index=2,
                ownership=FavoritesExternalFieldOwnership.LOCAL,
            ),
        ),
    )

    with pytest.raises(
        FavoritesExternalProvenanceError,
        match="linked or detached",
    ):
        serialize_favorites_external_provenance((local_only,))

    with pytest.raises(
        FavoritesExternalProvenanceError,
        match="duplicate local record targets",
    ):
        serialize_favorites_external_provenance((linked, linked))


def test_deserialization_rejects_changed_exact_record_identity() -> None:
    snapshot = _snapshot()
    state = _linked_state(snapshot)
    content = serialize_favorites_external_provenance((state,))
    changed = FavoritesStorageSnapshot(
        catalog_bytes=snapshot.catalog_bytes,
        documents=(
            FavoritesStorageDocument(
                filename=snapshot.documents[0].filename,
                content=snapshot.documents[0].content.replace(
                    b"Synthetic Channel",
                    b"Locally Changed",
                    1,
                ),
            ),
        ),
    )

    with pytest.raises(
        FavoritesExternalProvenanceError,
        match="record identity no longer matches",
    ):
        deserialize_favorites_external_provenance(
            content,
            changed,
        )


def test_deserialization_rejects_changed_target_position() -> None:
    snapshot = _snapshot()
    state = _linked_state(snapshot)
    payload = json.loads(
        serialize_favorites_external_provenance((state,))
    )
    payload["records"][0]["target"]["source_index"] = 4

    with pytest.raises(
        FavoritesExternalProvenanceError,
        match="record identity no longer matches",
    ):
        deserialize_favorites_external_provenance(
            _canonical_payload_bytes(payload),
            snapshot,
        )


@pytest.mark.parametrize(
    ("key", "value", "message"),
    (
        ("schema", "other.schema", "schema is unsupported"),
        ("version", 2, "version is unsupported"),
    ),
)
def test_deserialization_rejects_unsupported_document_contract(
    key: str,
    value: object,
    message: str,
) -> None:
    snapshot = _snapshot()
    payload = json.loads(
        serialize_favorites_external_provenance(
            (_linked_state(snapshot),)
        )
    )
    payload[key] = value

    with pytest.raises(
        FavoritesExternalProvenanceError,
        match=message,
    ):
        deserialize_favorites_external_provenance(
            _canonical_payload_bytes(payload),
            snapshot,
        )


def test_deserialization_rejects_duplicate_keys_and_noncanonical_bytes() -> None:
    snapshot = _snapshot()
    content = serialize_favorites_external_provenance(
        (_linked_state(snapshot),)
    )

    duplicate = content.replace(
        b'{"records":',
        b'{"records":[],"records":',
        1,
    )
    with pytest.raises(
        FavoritesExternalProvenanceError,
        match="strict JSON",
    ):
        deserialize_favorites_external_provenance(
            duplicate,
            snapshot,
        )

    pretty = (
        json.dumps(
            json.loads(content),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    with pytest.raises(
        FavoritesExternalProvenanceError,
        match="canonical form",
    ):
        deserialize_favorites_external_provenance(
            pretty,
            snapshot,
        )


def test_deserialization_is_bounded_and_requires_exact_input_types() -> None:
    snapshot = _snapshot()
    content = serialize_favorites_external_provenance(
        (_linked_state(snapshot),)
    )

    with pytest.raises(
        FavoritesExternalProvenanceError,
        match="maximum encoded size",
    ):
        deserialize_favorites_external_provenance(
            content,
            snapshot,
            max_bytes=len(content) - 1,
        )

    with pytest.raises(TypeError):
        deserialize_favorites_external_provenance(  # type: ignore[arg-type]
            "not-bytes",
            snapshot,
        )
    with pytest.raises(TypeError):
        deserialize_favorites_external_provenance(  # type: ignore[arg-type]
            content,
            object(),
        )
    with pytest.raises(ValueError, match="positive integer"):
        deserialize_favorites_external_provenance(
            content,
            snapshot,
            max_bytes=0,
        )


def test_deserialization_rejects_invalid_utf8_and_nonfinite_json() -> None:
    snapshot = _snapshot()

    with pytest.raises(
        FavoritesExternalProvenanceError,
        match="valid UTF-8",
    ):
        deserialize_favorites_external_provenance(
            b"\xff",
            snapshot,
        )

    with pytest.raises(
        FavoritesExternalProvenanceError,
        match="strict JSON",
    ):
        deserialize_favorites_external_provenance(
            b'{"records":[],"schema":"sds200.favorites-external-provenance",'
            b'"version":NaN}\n',
            snapshot,
        )



def test_serialization_and_deserialization_share_exact_size_bound() -> None:
    snapshot = _snapshot()
    state = _linked_state(snapshot)
    reference = serialize_favorites_external_provenance((state,))

    assert (
        serialize_favorites_external_provenance(
            (state,),
            max_bytes=len(reference),
        )
        == reference
    )
    assert (
        deserialize_favorites_external_provenance(
            reference,
            snapshot,
            max_bytes=len(reference),
        )
        == (state,)
    )

    with pytest.raises(
        FavoritesExternalProvenanceError,
        match="maximum encoded size",
    ):
        serialize_favorites_external_provenance(
            (state,),
            max_bytes=len(reference) - 1,
        )

    with pytest.raises(
        ValueError,
        match="positive integer",
    ):
        serialize_favorites_external_provenance(
            (state,),
            max_bytes=0,
        )


def test_deserialization_redacts_json_recursion_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()

    def raise_recursion_error(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise RecursionError

    monkeypatch.setattr(json, "loads", raise_recursion_error)

    with pytest.raises(
        FavoritesExternalProvenanceError,
        match="strict JSON",
    ):
        deserialize_favorites_external_provenance(
            b"{}\n",
            snapshot,
        )


def test_deserialization_rejects_non_object_json_root() -> None:
    snapshot = _snapshot()

    with pytest.raises(
        FavoritesExternalProvenanceError,
        match="document must be an object",
    ):
        deserialize_favorites_external_provenance(
            b"[]\n",
            snapshot,
        )


def test_catalog_provenance_round_trip_rebinds_exact_catalog_target() -> None:
    snapshot = _snapshot()
    target = select_favorites_record_target(
        snapshot,
        2,
    )
    state = FavoritesExternalRecordState(
        target=target,
        fields=(),
        external_identity=_identity("catalog-1"),
        last_observation=_evidence("catalog-r1"),
    )

    content = serialize_favorites_external_provenance((state,))
    restored = deserialize_favorites_external_provenance(
        content,
        snapshot,
    )

    assert restored == (state,)
    payload = json.loads(content)
    target_payload = payload["records"][0]["target"]
    assert target_payload["source_kind"] == "catalog"
    assert target_payload["document_index"] is None
    assert target_payload["filename"] is None


def test_serialization_rejects_duplicate_external_identity_across_targets() -> None:
    snapshot = _snapshot()
    first = _linked_state(snapshot)
    second = FavoritesExternalRecordState(
        target=select_favorites_record_target(
            snapshot,
            6,
            document_index=0,
        ),
        fields=(),
        external_identity=first.external_identity,
        last_observation=first.last_observation,
    )

    with pytest.raises(
        FavoritesExternalProvenanceError,
        match="duplicate external record identities",
    ):
        serialize_favorites_external_provenance(
            (first, second)
        )



def test_serialization_and_deserialization_enforce_record_count_bound() -> None:
    snapshot = _snapshot()
    first = _linked_state(snapshot)
    second = FavoritesExternalRecordState(
        target=select_favorites_record_target(
            snapshot,
            6,
            document_index=0,
        ),
        fields=(),
        external_identity=_identity("channel-102"),
        last_observation=_evidence("accepted-r2"),
    )

    content = serialize_favorites_external_provenance(
        (first, second),
        max_records=2,
    )

    with pytest.raises(
        FavoritesExternalProvenanceError,
        match="maximum record count",
    ):
        serialize_favorites_external_provenance(
            (first, second),
            max_records=1,
        )

    with pytest.raises(
        FavoritesExternalProvenanceError,
        match="maximum record count",
    ):
        deserialize_favorites_external_provenance(
            content,
            snapshot,
            max_records=1,
        )


def test_serialization_and_deserialization_enforce_field_count_bound() -> None:
    snapshot = _snapshot()
    linked = _linked_state(snapshot)
    state = FavoritesExternalRecordState(
        target=linked.target,
        fields=(
            *linked.fields,
            FavoritesExternalFieldState(
                name="local-note",
                field_index=1,
                ownership=FavoritesExternalFieldOwnership.LOCAL,
            ),
        ),
        external_identity=linked.external_identity,
        last_observation=linked.last_observation,
    )

    content = serialize_favorites_external_provenance(
        (state,),
        max_fields_per_record=2,
    )

    with pytest.raises(
        FavoritesExternalProvenanceError,
        match="maximum field count",
    ):
        serialize_favorites_external_provenance(
            (state,),
            max_fields_per_record=1,
        )

    with pytest.raises(
        FavoritesExternalProvenanceError,
        match="maximum field count",
    ):
        deserialize_favorites_external_provenance(
            content,
            snapshot,
            max_fields_per_record=1,
        )


@pytest.mark.parametrize(
    ("max_records", "max_fields_per_record"),
    (
        (0, FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_FIELDS_PER_RECORD),
        (True, FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_FIELDS_PER_RECORD),
        (FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_RECORDS, 0),
        (FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_RECORDS, True),
    ),
)
def test_provenance_rejects_invalid_structural_limits(
    max_records: object,
    max_fields_per_record: object,
) -> None:
    snapshot = _snapshot()
    state = _linked_state(snapshot)

    with pytest.raises(ValueError, match="positive integer"):
        serialize_favorites_external_provenance(
            (state,),
            max_records=max_records,  # type: ignore[arg-type]
            max_fields_per_record=max_fields_per_record,  # type: ignore[arg-type]
        )

    content = serialize_favorites_external_provenance((state,))
    with pytest.raises(ValueError, match="positive integer"):
        deserialize_favorites_external_provenance(
            content,
            snapshot,
            max_records=max_records,  # type: ignore[arg-type]
            max_fields_per_record=max_fields_per_record,  # type: ignore[arg-type]
        )


def test_public_provenance_symbols_are_package_exports() -> None:
    expected = (
        "FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_BYTES",
        "FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_FIELDS_PER_RECORD",
        "FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_RECORDS",
        "FAVORITES_EXTERNAL_PROVENANCE_SCHEMA",
        "FAVORITES_EXTERNAL_PROVENANCE_VERSION",
        "FavoritesExternalProvenanceError",
        "deserialize_favorites_external_provenance",
        "serialize_favorites_external_provenance",
    )

    for name in expected:
        assert name in sds200.__all__
        assert hasattr(sds200, name)
