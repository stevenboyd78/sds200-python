"""Canonical durable representation for source-neutral external Favorites provenance."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import cast

from .favorites_editing import (
    FavoritesRecordEditError,
    FavoritesRecordSourceKind,
    FavoritesRecordTarget,
    select_favorites_record_target,
)
from .favorites_external import (
    FavoritesExternalFieldObservation,
    FavoritesExternalFieldObservationState,
    FavoritesExternalFieldOwnership,
    FavoritesExternalFieldState,
    FavoritesExternalObservationEvidence,
    FavoritesExternalRecordIdentity,
    FavoritesExternalRecordState,
    FavoritesExternalSourceIdentity,
)
from .favorites_storage import FavoritesStorageSnapshot

FAVORITES_EXTERNAL_PROVENANCE_SCHEMA = "sds200.favorites-external-provenance"
FAVORITES_EXTERNAL_PROVENANCE_VERSION = 1
FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_BYTES = 1024 * 1024
FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_RECORDS = 4_096
FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_FIELDS_PER_RECORD = 256


class FavoritesExternalProvenanceError(ValueError):
    """Report invalid, stale, or unsupported durable external provenance."""


def _reject_duplicate_object(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate object key.")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    del value
    raise ValueError("Unsupported JSON constant.")


def _require_object(value: object, *, label: str) -> dict[str, object]:
    if type(value) is not dict:
        raise FavoritesExternalProvenanceError(f"{label} must be an object.")
    candidate = cast(dict[object, object], value)
    if any(type(key) is not str for key in candidate):
        raise FavoritesExternalProvenanceError(
            f"{label} must contain only string keys."
        )
    return cast(dict[str, object], value)


def _require_array(value: object, *, label: str) -> list[object]:
    if type(value) is not list:
        raise FavoritesExternalProvenanceError(f"{label} must be an array.")
    return cast(list[object], value)


def _require_exact_keys(
    value: dict[str, object],
    expected: frozenset[str],
    *,
    label: str,
) -> None:
    if frozenset(value) != expected:
        raise FavoritesExternalProvenanceError(
            f"{label} must contain exactly the supported keys."
        )


def _require_string(value: object, *, label: str) -> str:
    if type(value) is not str:
        raise FavoritesExternalProvenanceError(f"{label} must be a string.")
    return value


def _require_nullable_string(
    value: object,
    *,
    label: str,
) -> str | None:
    if value is None:
        return None
    return _require_string(value, label=label)


def _require_integer(value: object, *, label: str) -> int:
    if type(value) is not int:
        raise FavoritesExternalProvenanceError(f"{label} must be an integer.")
    return value


def _require_nullable_integer(
    value: object,
    *,
    label: str,
) -> int | None:
    if value is None:
        return None
    return _require_integer(value, label=label)


def _require_boolean(value: object, *, label: str) -> bool:
    if type(value) is not bool:
        raise FavoritesExternalProvenanceError(f"{label} must be a boolean.")
    return value


def _record_sha256(record_bytes: bytes) -> str:
    return hashlib.sha256(record_bytes).hexdigest()


def _require_sha256(value: object, *, label: str) -> str:
    digest = _require_string(value, label=label)
    if (
        len(digest) != 64
        or digest != digest.lower()
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise FavoritesExternalProvenanceError(
            f"{label} must be one canonical lowercase SHA-256 digest."
        )
    return digest


def _evidence_payload(
    evidence: FavoritesExternalObservationEvidence,
) -> dict[str, object]:
    return {
        "observed_at": evidence.observed_at.isoformat(),
        "revision": evidence.revision,
    }


def _field_observation_payload(
    observation: FavoritesExternalFieldObservation,
) -> dict[str, object]:
    return {
        "name": observation.name,
        "state": observation.state.value,
        "value": observation.value,
    }


def _field_state_payload(
    field: FavoritesExternalFieldState,
) -> dict[str, object]:
    return {
        "field_index": field.field_index,
        "last_external": (
            None
            if field.last_external is None
            else _field_observation_payload(field.last_external)
        ),
        "name": field.name,
        "ownership": field.ownership.value,
    }


def _record_state_payload(
    state: FavoritesExternalRecordState,
) -> dict[str, object]:
    identity = state.external_identity
    observation = state.last_observation
    if identity is None or observation is None:
        raise FavoritesExternalProvenanceError(
            "Durable external Favorites provenance requires linked or detached "
            "external record state."
        )

    target = state.target
    return {
        "detached": state.detached,
        "external_identity": {
            "record_id": identity.record_id,
            "source": {
                "dataset": identity.source.dataset,
                "provider": identity.source.provider,
            },
        },
        "fields": [_field_state_payload(field) for field in state.fields],
        "last_observation": _evidence_payload(observation),
        "target": {
            "document_index": target.document_index,
            "filename": target.filename,
            "record_sha256": _record_sha256(target.record.raw_bytes),
            "source_index": target.source_index,
            "source_kind": target.source_kind.value,
        },
    }


def _validate_record_set(
    records: tuple[FavoritesExternalRecordState, ...],
    *,
    max_records: int,
    max_fields_per_record: int,
) -> None:
    if len(records) > max_records:
        raise FavoritesExternalProvenanceError(
            "External Favorites provenance exceeds the maximum record count."
        )
    if any(len(record.fields) > max_fields_per_record for record in records):
        raise FavoritesExternalProvenanceError(
            "External Favorites provenance exceeds the maximum field count "
            "for one record."
        )

    targets = tuple(record.target for record in records)
    if len(set(targets)) != len(targets):
        raise FavoritesExternalProvenanceError(
            "Durable external Favorites provenance contains duplicate local "
            "record targets."
        )

    identities = tuple(record.external_identity for record in records)
    if any(identity is None for identity in identities):
        raise FavoritesExternalProvenanceError(
            "Durable external Favorites provenance requires linked or detached "
            "external record state."
        )
    if len(set(identities)) != len(identities):
        raise FavoritesExternalProvenanceError(
            "Durable external Favorites provenance contains duplicate external "
            "record identities."
        )


def serialize_favorites_external_provenance(
    records: tuple[FavoritesExternalRecordState, ...],
    *,
    max_bytes: int = FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_BYTES,
    max_records: int = FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_RECORDS,
    max_fields_per_record: int = (
        FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_FIELDS_PER_RECORD
    ),
) -> bytes:
    """Serialize linked/detached external provenance as canonical UTF-8 JSON."""

    if type(records) is not tuple:
        raise TypeError(
            "External Favorites provenance serialization requires an immutable tuple."
        )
    if any(
        not isinstance(record, FavoritesExternalRecordState)
        for record in records
    ):
        raise TypeError(
            "External Favorites provenance serialization requires only "
            "FavoritesExternalRecordState values."
        )
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError(
            "External Favorites provenance maximum size must be a positive integer."
        )
    if type(max_records) is not int or max_records <= 0:
        raise ValueError(
            "External Favorites provenance maximum record count must be "
            "a positive integer."
        )
    if type(max_fields_per_record) is not int or max_fields_per_record <= 0:
        raise ValueError(
            "External Favorites provenance maximum field count must be "
            "a positive integer."
        )

    _validate_record_set(
        records,
        max_records=max_records,
        max_fields_per_record=max_fields_per_record,
    )
    payload: dict[str, object] = {
        "records": [_record_state_payload(record) for record in records],
        "schema": FAVORITES_EXTERNAL_PROVENANCE_SCHEMA,
        "version": FAVORITES_EXTERNAL_PROVENANCE_VERSION,
    }
    encoded = (
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    if len(encoded) > max_bytes:
        raise FavoritesExternalProvenanceError(
            "External Favorites provenance exceeds the maximum encoded size."
        )
    return encoded


def _parse_evidence(
    value: object,
) -> FavoritesExternalObservationEvidence:
    payload = _require_object(
        value,
        label="External Favorites provenance observation evidence",
    )
    _require_exact_keys(
        payload,
        frozenset({"observed_at", "revision"}),
        label="External Favorites provenance observation evidence",
    )
    observed_at_text = _require_string(
        payload["observed_at"],
        label="External Favorites provenance observation time",
    )
    revision = _require_nullable_string(
        payload["revision"],
        label="External Favorites provenance observation revision",
    )

    try:
        observed_at = datetime.fromisoformat(observed_at_text)
        return FavoritesExternalObservationEvidence(
            observed_at=observed_at,
            revision=revision,
        )
    except (TypeError, ValueError):
        raise FavoritesExternalProvenanceError(
            "External Favorites provenance contains invalid observation evidence."
        ) from None


def _parse_field_observation(
    value: object,
) -> FavoritesExternalFieldObservation:
    payload = _require_object(
        value,
        label="External Favorites provenance field observation",
    )
    _require_exact_keys(
        payload,
        frozenset({"name", "state", "value"}),
        label="External Favorites provenance field observation",
    )

    name = _require_string(
        payload["name"],
        label="External Favorites provenance observed field name",
    )
    state_text = _require_string(
        payload["state"],
        label="External Favorites provenance observed field state",
    )
    observed_value = _require_nullable_string(
        payload["value"],
        label="External Favorites provenance observed field value",
    )

    try:
        state = FavoritesExternalFieldObservationState(state_text)
        return FavoritesExternalFieldObservation(
            name=name,
            state=state,
            value=observed_value,
        )
    except (TypeError, ValueError):
        raise FavoritesExternalProvenanceError(
            "External Favorites provenance contains an invalid field observation."
        ) from None


def _parse_field_state(
    value: object,
) -> FavoritesExternalFieldState:
    payload = _require_object(
        value,
        label="External Favorites provenance field state",
    )
    _require_exact_keys(
        payload,
        frozenset({"field_index", "last_external", "name", "ownership"}),
        label="External Favorites provenance field state",
    )

    name = _require_string(
        payload["name"],
        label="External Favorites provenance field name",
    )
    field_index = _require_integer(
        payload["field_index"],
        label="External Favorites provenance field index",
    )
    ownership_text = _require_string(
        payload["ownership"],
        label="External Favorites provenance field ownership",
    )
    last_external_value = payload["last_external"]

    try:
        ownership = FavoritesExternalFieldOwnership(ownership_text)
        last_external = (
            None
            if last_external_value is None
            else _parse_field_observation(last_external_value)
        )
        return FavoritesExternalFieldState(
            name=name,
            field_index=field_index,
            ownership=ownership,
            last_external=last_external,
        )
    except FavoritesExternalProvenanceError:
        raise
    except (TypeError, ValueError):
        raise FavoritesExternalProvenanceError(
            "External Favorites provenance contains invalid field state."
        ) from None


def _parse_identity(
    value: object,
) -> FavoritesExternalRecordIdentity:
    payload = _require_object(
        value,
        label="External Favorites provenance record identity",
    )
    _require_exact_keys(
        payload,
        frozenset({"record_id", "source"}),
        label="External Favorites provenance record identity",
    )
    source_payload = _require_object(
        payload["source"],
        label="External Favorites provenance source identity",
    )
    _require_exact_keys(
        source_payload,
        frozenset({"dataset", "provider"}),
        label="External Favorites provenance source identity",
    )

    provider = _require_string(
        source_payload["provider"],
        label="External Favorites provenance provider",
    )
    dataset = _require_string(
        source_payload["dataset"],
        label="External Favorites provenance dataset",
    )
    record_id = _require_string(
        payload["record_id"],
        label="External Favorites provenance record ID",
    )

    try:
        return FavoritesExternalRecordIdentity(
            source=FavoritesExternalSourceIdentity(
                provider=provider,
                dataset=dataset,
            ),
            record_id=record_id,
        )
    except (TypeError, ValueError):
        raise FavoritesExternalProvenanceError(
            "External Favorites provenance contains invalid external identity."
        ) from None


def _parse_target(
    value: object,
    snapshot: FavoritesStorageSnapshot,
) -> FavoritesRecordTarget:
    payload = _require_object(
        value,
        label="External Favorites provenance local target",
    )
    _require_exact_keys(
        payload,
        frozenset(
            {
                "document_index",
                "filename",
                "record_sha256",
                "source_index",
                "source_kind",
            }
        ),
        label="External Favorites provenance local target",
    )

    source_kind_text = _require_string(
        payload["source_kind"],
        label="External Favorites provenance target source kind",
    )
    source_index = _require_integer(
        payload["source_index"],
        label="External Favorites provenance target source index",
    )
    document_index = _require_nullable_integer(
        payload["document_index"],
        label="External Favorites provenance target document index",
    )
    filename = _require_nullable_string(
        payload["filename"],
        label="External Favorites provenance target filename",
    )
    expected_sha256 = _require_sha256(
        payload["record_sha256"],
        label="External Favorites provenance target record SHA-256",
    )

    if source_index < 0 or (document_index is not None and document_index < 0):
        raise FavoritesExternalProvenanceError(
            "External Favorites provenance target indexes must be non-negative."
        )

    try:
        source_kind = FavoritesRecordSourceKind(source_kind_text)
    except ValueError:
        raise FavoritesExternalProvenanceError(
            "External Favorites provenance target source kind is unsupported."
        ) from None

    if source_kind is FavoritesRecordSourceKind.CATALOG:
        if document_index is not None or filename is not None:
            raise FavoritesExternalProvenanceError(
                "External Favorites catalog provenance contains HPD target metadata."
            )
    else:
        if document_index is None or filename is None:
            raise FavoritesExternalProvenanceError(
                "External Favorites HPD provenance requires exact document metadata."
            )

    try:
        target = select_favorites_record_target(
            snapshot,
            source_index,
            document_index=document_index,
        )
    except (FavoritesRecordEditError, TypeError, ValueError):
        raise FavoritesExternalProvenanceError(
            "External Favorites provenance could not rebind its exact local target."
        ) from None

    if (
        target.source_kind is not source_kind
        or target.document_index != document_index
        or target.filename != filename
    ):
        raise FavoritesExternalProvenanceError(
            "External Favorites provenance target location no longer matches."
        )
    if _record_sha256(target.record.raw_bytes) != expected_sha256:
        raise FavoritesExternalProvenanceError(
            "External Favorites provenance target record identity no longer matches."
        )
    return target


def _parse_record_state(
    value: object,
    snapshot: FavoritesStorageSnapshot,
    *,
    max_fields_per_record: int,
) -> FavoritesExternalRecordState:
    payload = _require_object(
        value,
        label="External Favorites provenance record",
    )
    _require_exact_keys(
        payload,
        frozenset(
            {
                "detached",
                "external_identity",
                "fields",
                "last_observation",
                "target",
            }
        ),
        label="External Favorites provenance record",
    )

    target = _parse_target(payload["target"], snapshot)
    identity = _parse_identity(payload["external_identity"])
    observation = _parse_evidence(payload["last_observation"])
    detached = _require_boolean(
        payload["detached"],
        label="External Favorites provenance detached state",
    )
    field_values = _require_array(
        payload["fields"],
        label="External Favorites provenance fields",
    )
    if len(field_values) > max_fields_per_record:
        raise FavoritesExternalProvenanceError(
            "External Favorites provenance exceeds the maximum field count "
            "for one record."
        )
    fields = tuple(_parse_field_state(field) for field in field_values)

    try:
        return FavoritesExternalRecordState(
            target=target,
            fields=fields,
            external_identity=identity,
            last_observation=observation,
            detached=detached,
        )
    except (TypeError, ValueError):
        raise FavoritesExternalProvenanceError(
            "External Favorites provenance record state is internally inconsistent."
        ) from None


def deserialize_favorites_external_provenance(
    content: bytes,
    snapshot: FavoritesStorageSnapshot,
    *,
    max_bytes: int = FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_BYTES,
    max_records: int = FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_RECORDS,
    max_fields_per_record: int = (
        FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_FIELDS_PER_RECORD
    ),
) -> tuple[FavoritesExternalRecordState, ...]:
    """Parse canonical provenance and rebind every record to one fresh snapshot."""

    if type(content) is not bytes:
        raise TypeError("External Favorites provenance content must be bytes.")
    if not isinstance(snapshot, FavoritesStorageSnapshot):
        raise TypeError(
            "External Favorites provenance rebinding requires "
            "FavoritesStorageSnapshot."
        )
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError(
            "External Favorites provenance maximum size must be a positive integer."
        )
    if type(max_records) is not int or max_records <= 0:
        raise ValueError(
            "External Favorites provenance maximum record count must be "
            "a positive integer."
        )
    if type(max_fields_per_record) is not int or max_fields_per_record <= 0:
        raise ValueError(
            "External Favorites provenance maximum field count must be "
            "a positive integer."
        )
    if not content:
        raise FavoritesExternalProvenanceError(
            "External Favorites provenance content must not be empty."
        )
    if len(content) > max_bytes:
        raise FavoritesExternalProvenanceError(
            "External Favorites provenance exceeds the maximum encoded size."
        )

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise FavoritesExternalProvenanceError(
            "External Favorites provenance must be valid UTF-8."
        ) from None

    try:
        parsed: object = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_object,
            parse_constant=_reject_json_constant,
        )
    except (
        json.JSONDecodeError,
        RecursionError,
        ValueError,
    ):
        raise FavoritesExternalProvenanceError(
            "External Favorites provenance is not valid strict JSON."
        ) from None

    root = _require_object(
        parsed,
        label="External Favorites provenance document",
    )
    _require_exact_keys(
        root,
        frozenset({"records", "schema", "version"}),
        label="External Favorites provenance document",
    )

    schema = _require_string(
        root["schema"],
        label="External Favorites provenance schema",
    )
    if schema != FAVORITES_EXTERNAL_PROVENANCE_SCHEMA:
        raise FavoritesExternalProvenanceError(
            "External Favorites provenance schema is unsupported."
        )

    version = _require_integer(
        root["version"],
        label="External Favorites provenance version",
    )
    if version != FAVORITES_EXTERNAL_PROVENANCE_VERSION:
        raise FavoritesExternalProvenanceError(
            "External Favorites provenance version is unsupported."
        )

    record_values = _require_array(
        root["records"],
        label="External Favorites provenance records",
    )
    if len(record_values) > max_records:
        raise FavoritesExternalProvenanceError(
            "External Favorites provenance exceeds the maximum record count."
        )
    records = tuple(
        _parse_record_state(
            value,
            snapshot,
            max_fields_per_record=max_fields_per_record,
        )
        for value in record_values
    )
    _validate_record_set(
        records,
        max_records=max_records,
        max_fields_per_record=max_fields_per_record,
    )

    if (
        serialize_favorites_external_provenance(
            records,
            max_bytes=max_bytes,
            max_records=max_records,
            max_fields_per_record=max_fields_per_record,
        )
        != content
    ):
        raise FavoritesExternalProvenanceError(
            "External Favorites provenance is not in canonical form."
        )

    return records


__all__ = [
    "FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_BYTES",
    "FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_FIELDS_PER_RECORD",
    "FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_RECORDS",
    "FAVORITES_EXTERNAL_PROVENANCE_SCHEMA",
    "FAVORITES_EXTERNAL_PROVENANCE_VERSION",
    "FavoritesExternalProvenanceError",
    "deserialize_favorites_external_provenance",
    "serialize_favorites_external_provenance",
]
