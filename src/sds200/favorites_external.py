"""Renderer-neutral external Favorites provenance and import previews."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from .favorites_editing import FavoritesRecordTarget


class FavoritesExternalImportError(ValueError):
    """Report invalid or ambiguous external Favorites evidence."""


class FavoritesExternalFieldOwnership(StrEnum):
    """Classify who controls one normalized local field."""

    EXTERNAL = "external"
    LOCAL = "local"
    DETACHED = "detached"


class FavoritesExternalFieldObservationState(StrEnum):
    """Distinguish an observed value from explicit provider absence."""

    VALUE = "value"
    ABSENT = "absent"


class FavoritesExternalRecordObservationState(StrEnum):
    """Classify one provider record observation."""

    ACTIVE = "active"
    REMOVED = "removed"


class FavoritesExternalChangeKind(StrEnum):
    """Classify one previewable external-data effect."""

    ADDED = "added"
    REPLACED = "replaced"
    REMOVED = "removed"
    UNCHANGED = "unchanged"
    LOCAL_ONLY = "local_only"
    CONFLICT = "conflict"


def _validate_text(value: str, *, label: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{label} must be a string.")
    if not value or value.strip() != value:
        raise ValueError(f"{label} must not be empty or padded.")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{label} must not contain control characters.")
    return value


def _validate_tuple(value: object, *, label: str) -> tuple[object, ...]:
    if type(value) is not tuple:
        raise TypeError(f"{label} must be an immutable tuple.")
    return value


@dataclass(frozen=True, slots=True)
class FavoritesExternalSourceIdentity:
    """Opaque provider and dataset identity, separate from scanner identity."""

    provider: str
    dataset: str

    def __post_init__(self) -> None:
        _validate_text(self.provider, label="External Favorites provider")
        _validate_text(self.dataset, label="External Favorites dataset")

    @property
    def sort_key(self) -> tuple[str, str]:
        """Return a deterministic identity sort key."""

        return (self.provider, self.dataset)


@dataclass(frozen=True, slots=True)
class FavoritesExternalRecordIdentity:
    """Opaque provider record identity without scanner provenance semantics."""

    source: FavoritesExternalSourceIdentity
    record_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.source, FavoritesExternalSourceIdentity):
            raise TypeError(
                "External Favorites record identity requires "
                "FavoritesExternalSourceIdentity."
            )
        _validate_text(self.record_id, label="External Favorites record ID")

    @property
    def sort_key(self) -> tuple[str, str, str]:
        """Return a deterministic external record sort key."""

        return (*self.source.sort_key, self.record_id)


@dataclass(frozen=True, slots=True)
class FavoritesExternalObservationEvidence:
    """Record when one opaque provider revision was observed."""

    observed_at: datetime
    revision: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.observed_at, datetime):
            raise TypeError(
                "External Favorites observation time must be a datetime."
            )
        if (
            self.observed_at.tzinfo is None
            or self.observed_at.utcoffset() is None
        ):
            raise ValueError(
                "External Favorites observation time must be timezone-aware."
            )
        if self.revision is not None:
            _validate_text(
                self.revision,
                label="External Favorites revision",
            )


@dataclass(frozen=True, slots=True)
class FavoritesExternalFieldObservation:
    """One normalized provider field value or explicit absence."""

    name: str
    state: FavoritesExternalFieldObservationState
    value: str | None = None

    def __post_init__(self) -> None:
        _validate_text(self.name, label="External Favorites field name")
        if not isinstance(
            self.state,
            FavoritesExternalFieldObservationState,
        ):
            raise TypeError(
                "External Favorites field observation state must be "
                "FavoritesExternalFieldObservationState."
            )

        if self.state is FavoritesExternalFieldObservationState.VALUE:
            if type(self.value) is not str:
                raise TypeError(
                    "External Favorites observed field value must be a string."
                )
            return

        if self.value is not None:
            raise ValueError(
                "Explicitly absent external Favorites fields must not "
                "contain a value."
            )


@dataclass(frozen=True, slots=True)
class FavoritesExternalRecordObservation:
    """One complete normalized provider record observation."""

    identity: FavoritesExternalRecordIdentity
    evidence: FavoritesExternalObservationEvidence
    fields: tuple[FavoritesExternalFieldObservation, ...] = ()
    state: FavoritesExternalRecordObservationState = (
        FavoritesExternalRecordObservationState.ACTIVE
    )

    def __post_init__(self) -> None:
        if not isinstance(self.identity, FavoritesExternalRecordIdentity):
            raise TypeError(
                "External Favorites record observation requires "
                "FavoritesExternalRecordIdentity."
            )
        if not isinstance(
            self.evidence,
            FavoritesExternalObservationEvidence,
        ):
            raise TypeError(
                "External Favorites record observation requires "
                "FavoritesExternalObservationEvidence."
            )
        if not isinstance(
            self.state,
            FavoritesExternalRecordObservationState,
        ):
            raise TypeError(
                "External Favorites record observation state must be "
                "FavoritesExternalRecordObservationState."
            )

        _validate_tuple(
            self.fields,
            label="External Favorites record fields",
        )
        if any(
            not isinstance(field, FavoritesExternalFieldObservation)
            for field in self.fields
        ):
            raise TypeError(
                "External Favorites record fields must contain only "
                "FavoritesExternalFieldObservation values."
            )

        names = tuple(field.name for field in self.fields)
        if len(set(names)) != len(names):
            raise ValueError(
                "External Favorites record observation contains duplicate "
                "field names."
            )

        if (
            self.state is FavoritesExternalRecordObservationState.REMOVED
            and self.fields
        ):
            raise ValueError(
                "Removed external Favorites records must not contain fields."
            )


@dataclass(frozen=True, slots=True)
class FavoritesExternalFieldState:
    """Persist source-neutral ownership for one exact local source field."""

    name: str
    field_index: int
    ownership: FavoritesExternalFieldOwnership
    last_external: FavoritesExternalFieldObservation | None = None

    def __post_init__(self) -> None:
        _validate_text(self.name, label="External Favorites local field name")
        if type(self.field_index) is not int or self.field_index < 0:
            raise ValueError(
                "External Favorites local field index must be "
                "a non-negative integer."
            )
        if not isinstance(
            self.ownership,
            FavoritesExternalFieldOwnership,
        ):
            raise TypeError(
                "External Favorites field ownership must be "
                "FavoritesExternalFieldOwnership."
            )

        if self.ownership is FavoritesExternalFieldOwnership.LOCAL:
            if self.last_external is not None:
                raise ValueError(
                    "Locally owned external Favorites fields must not carry "
                    "external provenance."
                )
            return

        if not isinstance(
            self.last_external,
            FavoritesExternalFieldObservation,
        ):
            raise ValueError(
                "Externally owned or detached Favorites fields require "
                "last-observed external provenance."
            )
        if self.last_external.name != self.name:
            raise ValueError(
                "External Favorites field provenance name must match "
                "the local field name."
            )


@dataclass(frozen=True, slots=True)
class FavoritesExternalRecordState:
    """Source-neutral provenance attached to one exact local record target."""

    target: FavoritesRecordTarget
    fields: tuple[FavoritesExternalFieldState, ...]
    external_identity: FavoritesExternalRecordIdentity | None = None
    last_observation: FavoritesExternalObservationEvidence | None = None
    detached: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.target, FavoritesRecordTarget):
            raise TypeError(
                "External Favorites record state requires FavoritesRecordTarget."
            )
        _validate_tuple(
            self.fields,
            label="External Favorites local field states",
        )
        if any(
            not isinstance(field, FavoritesExternalFieldState)
            for field in self.fields
        ):
            raise TypeError(
                "External Favorites local field states must contain only "
                "FavoritesExternalFieldState values."
            )
        if type(self.detached) is not bool:
            raise TypeError(
                "External Favorites record detached state must be a bool."
            )

        names = tuple(field.name for field in self.fields)
        indexes = tuple(field.field_index for field in self.fields)
        if len(set(names)) != len(names):
            raise ValueError(
                "External Favorites local record contains duplicate field names."
            )
        if len(set(indexes)) != len(indexes):
            raise ValueError(
                "External Favorites local record contains duplicate "
                "source field indexes."
            )
        if any(
            field.field_index >= len(self.target.record.fields)
            for field in self.fields
        ):
            raise ValueError(
                "External Favorites local field index is outside the exact "
                "target source record."
            )

        linked = self.external_identity is not None
        if linked != (self.last_observation is not None):
            raise ValueError(
                "External Favorites record identity and last observation "
                "must be present together."
            )
        if self.external_identity is not None and not isinstance(
            self.external_identity,
            FavoritesExternalRecordIdentity,
        ):
            raise TypeError(
                "External Favorites linked record identity must be "
                "FavoritesExternalRecordIdentity."
            )
        if self.last_observation is not None and not isinstance(
            self.last_observation,
            FavoritesExternalObservationEvidence,
        ):
            raise TypeError(
                "External Favorites linked record observation must be "
                "FavoritesExternalObservationEvidence."
            )

        if not linked:
            if self.detached:
                raise ValueError(
                    "A local-only Favorites record cannot be externally detached."
                )
            if any(
                field.ownership is not FavoritesExternalFieldOwnership.LOCAL
                for field in self.fields
            ):
                raise ValueError(
                    "A local-only Favorites record may contain only "
                    "locally owned fields."
                )
            return

        if self.detached and any(
            field.ownership is FavoritesExternalFieldOwnership.EXTERNAL
            for field in self.fields
        ):
            raise ValueError(
                "A detached external Favorites record cannot retain "
                "externally owned fields."
            )

    def local_value(self, field: FavoritesExternalFieldState) -> str:
        """Return the exact current ASCII value from the captured target."""

        if field not in self.fields:
            raise ValueError(
                "External Favorites field does not belong to this local record."
            )
        return self.target.record.fields[field.field_index]


@dataclass(frozen=True, slots=True)
class FavoritesExternalFieldPreview:
    """Preview one normalized field without applying it."""

    name: str
    kind: FavoritesExternalChangeKind
    ownership: FavoritesExternalFieldOwnership
    local_value: str | None
    external_state: FavoritesExternalFieldObservationState | None
    external_value: str | None

    def __post_init__(self) -> None:
        _validate_text(self.name, label="External Favorites preview field name")
        if not isinstance(self.kind, FavoritesExternalChangeKind):
            raise TypeError(
                "External Favorites preview change kind must be "
                "FavoritesExternalChangeKind."
            )
        if not isinstance(
            self.ownership,
            FavoritesExternalFieldOwnership,
        ):
            raise TypeError(
                "External Favorites preview ownership must be "
                "FavoritesExternalFieldOwnership."
            )
        if self.local_value is not None and type(self.local_value) is not str:
            raise TypeError(
                "External Favorites preview local value must be a string or None."
            )
        if self.external_state is None:
            if self.external_value is not None:
                raise ValueError(
                    "An unobserved external Favorites preview field must not "
                    "contain an external value."
                )
            return

        if not isinstance(
            self.external_state,
            FavoritesExternalFieldObservationState,
        ):
            raise TypeError(
                "External Favorites preview field state must be "
                "FavoritesExternalFieldObservationState or None."
            )
        if (
            self.external_state is FavoritesExternalFieldObservationState.VALUE
            and type(self.external_value) is not str
        ):
            raise TypeError(
                "An external Favorites value preview requires a string value."
            )
        if (
            self.external_state is FavoritesExternalFieldObservationState.ABSENT
            and self.external_value is not None
        ):
            raise ValueError(
                "An explicitly absent external Favorites preview field "
                "must not contain a value."
            )


@dataclass(frozen=True, slots=True)
class FavoritesExternalRecordPreview:
    """Preview one external/local record relationship."""

    kind: FavoritesExternalChangeKind
    target: FavoritesRecordTarget | None
    external_identity: FavoritesExternalRecordIdentity | None
    evidence: FavoritesExternalObservationEvidence | None
    fields: tuple[FavoritesExternalFieldPreview, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.kind, FavoritesExternalChangeKind):
            raise TypeError(
                "External Favorites record preview kind must be "
                "FavoritesExternalChangeKind."
            )
        if self.target is not None and not isinstance(
            self.target,
            FavoritesRecordTarget,
        ):
            raise TypeError(
                "External Favorites record preview target must be "
                "FavoritesRecordTarget or None."
            )
        if self.external_identity is not None and not isinstance(
            self.external_identity,
            FavoritesExternalRecordIdentity,
        ):
            raise TypeError(
                "External Favorites record preview identity must be "
                "FavoritesExternalRecordIdentity or None."
            )
        if self.evidence is not None and not isinstance(
            self.evidence,
            FavoritesExternalObservationEvidence,
        ):
            raise TypeError(
                "External Favorites record preview evidence must be "
                "FavoritesExternalObservationEvidence or None."
            )
        if self.target is None and self.external_identity is None:
            raise ValueError(
                "External Favorites record preview requires a local target "
                "or external identity."
            )
        if (self.external_identity is None) != (self.evidence is None):
            raise ValueError(
                "External Favorites preview identity and evidence must "
                "be present together."
            )

        _validate_tuple(
            self.fields,
            label="External Favorites preview fields",
        )
        if any(
            not isinstance(field, FavoritesExternalFieldPreview)
            for field in self.fields
        ):
            raise TypeError(
                "External Favorites preview fields must contain only "
                "FavoritesExternalFieldPreview values."
            )


@dataclass(frozen=True, slots=True)
class FavoritesExternalImportPreview:
    """Deterministic source-neutral external import/update preview."""

    records: tuple[FavoritesExternalRecordPreview, ...]

    def __post_init__(self) -> None:
        _validate_tuple(
            self.records,
            label="External Favorites preview records",
        )
        if any(
            not isinstance(record, FavoritesExternalRecordPreview)
            for record in self.records
        ):
            raise TypeError(
                "External Favorites preview records must contain only "
                "FavoritesExternalRecordPreview values."
            )

    @property
    def has_conflicts(self) -> bool:
        """Return whether any record preview is blocked by ownership conflict."""

        return any(
            record.kind is FavoritesExternalChangeKind.CONFLICT
            for record in self.records
        )

    @property
    def has_changes(self) -> bool:
        """Return whether the preview contains an actionable external change."""

        return any(
            record.kind
            in {
                FavoritesExternalChangeKind.ADDED,
                FavoritesExternalChangeKind.REPLACED,
                FavoritesExternalChangeKind.REMOVED,
            }
            for record in self.records
        )


class FavoritesExternalSource(Protocol):
    """Narrow fakeable source of normalized external Favorites observations."""

    def read_observations(
        self,
    ) -> tuple[FavoritesExternalRecordObservation, ...]:
        """Return one immutable normalized provider observation set."""
        ...


def _target_sort_key(
    target: FavoritesRecordTarget,
) -> tuple[str, int, str, int, bytes]:
    return (
        target.source_kind.value,
        -1 if target.document_index is None else target.document_index,
        "" if target.filename is None else target.filename,
        target.source_index,
        target.record.raw_bytes,
    )


def _local_only_field_preview(
    record: FavoritesExternalRecordState,
    field: FavoritesExternalFieldState,
) -> FavoritesExternalFieldPreview:
    return FavoritesExternalFieldPreview(
        name=field.name,
        kind=FavoritesExternalChangeKind.LOCAL_ONLY,
        ownership=field.ownership,
        local_value=record.local_value(field),
        external_state=None,
        external_value=None,
    )


def _detached_record_preview(
    local: FavoritesExternalRecordState,
    observation: FavoritesExternalRecordObservation,
) -> FavoritesExternalRecordPreview:
    observed = {field.name: field for field in observation.fields}
    fields: list[FavoritesExternalFieldPreview] = []

    for field in sorted(local.fields, key=lambda candidate: candidate.name):
        external = observed.pop(field.name, None)
        fields.append(
            FavoritesExternalFieldPreview(
                name=field.name,
                kind=FavoritesExternalChangeKind.LOCAL_ONLY,
                ownership=field.ownership,
                local_value=local.local_value(field),
                external_state=None if external is None else external.state,
                external_value=None if external is None else external.value,
            )
        )

    for external in sorted(
        observed.values(),
        key=lambda candidate: candidate.name,
    ):
        fields.append(
            FavoritesExternalFieldPreview(
                name=external.name,
                kind=FavoritesExternalChangeKind.LOCAL_ONLY,
                ownership=FavoritesExternalFieldOwnership.DETACHED,
                local_value=None,
                external_state=external.state,
                external_value=external.value,
            )
        )

    return FavoritesExternalRecordPreview(
        kind=FavoritesExternalChangeKind.LOCAL_ONLY,
        target=local.target,
        external_identity=observation.identity,
        evidence=observation.evidence,
        fields=tuple(fields),
    )


def _removed_record_preview(
    local: FavoritesExternalRecordState,
    observation: FavoritesExternalRecordObservation,
) -> FavoritesExternalRecordPreview:
    fields: list[FavoritesExternalFieldPreview] = []
    conflict = False

    for field in sorted(local.fields, key=lambda candidate: candidate.name):
        local_value = local.local_value(field)
        if field.ownership is FavoritesExternalFieldOwnership.EXTERNAL:
            kind = FavoritesExternalChangeKind.REMOVED
        else:
            kind = FavoritesExternalChangeKind.CONFLICT
            conflict = True
        fields.append(
            FavoritesExternalFieldPreview(
                name=field.name,
                kind=kind,
                ownership=field.ownership,
                local_value=local_value,
                external_state=FavoritesExternalFieldObservationState.ABSENT,
                external_value=None,
            )
        )

    return FavoritesExternalRecordPreview(
        kind=(
            FavoritesExternalChangeKind.CONFLICT
            if conflict
            else FavoritesExternalChangeKind.REMOVED
        ),
        target=local.target,
        external_identity=observation.identity,
        evidence=observation.evidence,
        fields=tuple(fields),
    )


def _active_record_preview(
    local: FavoritesExternalRecordState,
    observation: FavoritesExternalRecordObservation,
) -> FavoritesExternalRecordPreview:
    observed = {field.name: field for field in observation.fields}
    fields: list[FavoritesExternalFieldPreview] = []

    for field in sorted(local.fields, key=lambda candidate: candidate.name):
        local_value = local.local_value(field)
        external = observed.pop(field.name, None)

        if external is None:
            fields.append(_local_only_field_preview(local, field))
            continue

        if field.ownership in {
            FavoritesExternalFieldOwnership.LOCAL,
            FavoritesExternalFieldOwnership.DETACHED,
        }:
            if (
                external.state is FavoritesExternalFieldObservationState.VALUE
                and external.value == local_value
            ):
                kind = FavoritesExternalChangeKind.LOCAL_ONLY
            else:
                kind = FavoritesExternalChangeKind.CONFLICT
        elif external.state is FavoritesExternalFieldObservationState.ABSENT:
            kind = FavoritesExternalChangeKind.REMOVED
        elif external.value == local_value:
            kind = FavoritesExternalChangeKind.UNCHANGED
        else:
            kind = FavoritesExternalChangeKind.REPLACED

        fields.append(
            FavoritesExternalFieldPreview(
                name=field.name,
                kind=kind,
                ownership=field.ownership,
                local_value=local_value,
                external_state=external.state,
                external_value=external.value,
            )
        )

    for external in sorted(
        observed.values(),
        key=lambda candidate: candidate.name,
    ):
        fields.append(
            FavoritesExternalFieldPreview(
                name=external.name,
                kind=(
                    FavoritesExternalChangeKind.ADDED
                    if external.state
                    is FavoritesExternalFieldObservationState.VALUE
                    else FavoritesExternalChangeKind.UNCHANGED
                ),
                ownership=FavoritesExternalFieldOwnership.EXTERNAL,
                local_value=None,
                external_state=external.state,
                external_value=external.value,
            )
        )

    kinds = {field.kind for field in fields}
    if FavoritesExternalChangeKind.CONFLICT in kinds:
        record_kind = FavoritesExternalChangeKind.CONFLICT
    elif kinds.intersection(
        {
            FavoritesExternalChangeKind.ADDED,
            FavoritesExternalChangeKind.REPLACED,
            FavoritesExternalChangeKind.REMOVED,
        }
    ):
        record_kind = FavoritesExternalChangeKind.REPLACED
    else:
        record_kind = FavoritesExternalChangeKind.UNCHANGED

    return FavoritesExternalRecordPreview(
        kind=record_kind,
        target=local.target,
        external_identity=observation.identity,
        evidence=observation.evidence,
        fields=tuple(fields),
    )


def _bound_record_preview(
    local: FavoritesExternalRecordState,
    observation: FavoritesExternalRecordObservation,
) -> FavoritesExternalRecordPreview:
    if local.detached:
        return _detached_record_preview(local, observation)
    if observation.state is FavoritesExternalRecordObservationState.REMOVED:
        return _removed_record_preview(local, observation)
    return _active_record_preview(local, observation)


def _unbound_record_preview(
    observation: FavoritesExternalRecordObservation,
) -> FavoritesExternalRecordPreview:
    fields = tuple(
        FavoritesExternalFieldPreview(
            name=field.name,
            kind=(
                FavoritesExternalChangeKind.ADDED
                if field.state is FavoritesExternalFieldObservationState.VALUE
                else FavoritesExternalChangeKind.UNCHANGED
            ),
            ownership=FavoritesExternalFieldOwnership.EXTERNAL,
            local_value=None,
            external_state=field.state,
            external_value=field.value,
        )
        for field in sorted(
            observation.fields,
            key=lambda candidate: candidate.name,
        )
    )

    return FavoritesExternalRecordPreview(
        kind=(
            FavoritesExternalChangeKind.ADDED
            if observation.state
            is FavoritesExternalRecordObservationState.ACTIVE
            else FavoritesExternalChangeKind.UNCHANGED
        ),
        target=None,
        external_identity=observation.identity,
        evidence=observation.evidence,
        fields=fields,
    )


def _unmatched_local_preview(
    local: FavoritesExternalRecordState,
) -> FavoritesExternalRecordPreview:
    return FavoritesExternalRecordPreview(
        kind=FavoritesExternalChangeKind.LOCAL_ONLY,
        target=local.target,
        external_identity=local.external_identity,
        evidence=local.last_observation,
        fields=tuple(
            _local_only_field_preview(local, field)
            for field in sorted(
                local.fields,
                key=lambda candidate: candidate.name,
            )
        ),
    )


def preview_favorites_external_import(
    local_records: tuple[FavoritesExternalRecordState, ...],
    observations: tuple[FavoritesExternalRecordObservation, ...],
) -> FavoritesExternalImportPreview:
    """Preview normalized provider observations without mutating local data."""

    _validate_tuple(
        local_records,
        label="External Favorites local records",
    )
    _validate_tuple(
        observations,
        label="External Favorites observations",
    )
    if any(
        not isinstance(record, FavoritesExternalRecordState)
        for record in local_records
    ):
        raise TypeError(
            "External Favorites local records must contain only "
            "FavoritesExternalRecordState values."
        )
    if any(
        not isinstance(observation, FavoritesExternalRecordObservation)
        for observation in observations
    ):
        raise TypeError(
            "External Favorites observations must contain only "
            "FavoritesExternalRecordObservation values."
        )

    targets = tuple(record.target for record in local_records)
    if len(set(targets)) != len(targets):
        raise FavoritesExternalImportError(
            "External Favorites local provenance contains duplicate "
            "record targets."
        )

    linked = tuple(
        record.external_identity
        for record in local_records
        if record.external_identity is not None
    )
    if len(set(linked)) != len(linked):
        raise FavoritesExternalImportError(
            "External Favorites local provenance contains duplicate "
            "provider record identities."
        )

    observed_identities = tuple(
        observation.identity for observation in observations
    )
    if len(set(observed_identities)) != len(observed_identities):
        raise FavoritesExternalImportError(
            "External Favorites observations contain duplicate "
            "provider record identities."
        )

    local_by_identity = {
        record.external_identity: record
        for record in local_records
        if record.external_identity is not None
    }
    matched_targets: set[FavoritesRecordTarget] = set()
    previews: list[FavoritesExternalRecordPreview] = []

    for observation in sorted(
        observations,
        key=lambda candidate: candidate.identity.sort_key,
    ):
        local = local_by_identity.get(observation.identity)
        if local is None:
            previews.append(_unbound_record_preview(observation))
            continue

        matched_targets.add(local.target)
        previews.append(_bound_record_preview(local, observation))

    for local in sorted(
        local_records,
        key=lambda candidate: _target_sort_key(candidate.target),
    ):
        if local.target not in matched_targets:
            previews.append(_unmatched_local_preview(local))

    return FavoritesExternalImportPreview(records=tuple(previews))


def preview_favorites_external_source(
    local_records: tuple[FavoritesExternalRecordState, ...],
    source: FavoritesExternalSource,
) -> FavoritesExternalImportPreview:
    """Read one fakeable source and return a redacted deterministic preview."""

    reader = getattr(source, "read_observations", None)
    if not callable(reader):
        raise TypeError(
            "External Favorites source must provide read_observations()."
        )

    try:
        observations = reader()
    except Exception:
        raise FavoritesExternalImportError(
            "Could not read external Favorites observations."
        ) from None

    if type(observations) is not tuple:
        raise FavoritesExternalImportError(
            "External Favorites source returned a non-immutable observation set."
        )

    return preview_favorites_external_import(
        local_records,
        observations,
    )


def detach_favorites_external_field(
    record: FavoritesExternalRecordState,
    field_name: str,
) -> FavoritesExternalRecordState:
    """Detach one externally owned field while preserving its last provenance."""

    if not isinstance(record, FavoritesExternalRecordState):
        raise TypeError(
            "External Favorites detach requires FavoritesExternalRecordState."
        )
    _validate_text(
        field_name,
        label="External Favorites detach field name",
    )

    index = next(
        (
            position
            for position, field in enumerate(record.fields)
            if field.name == field_name
        ),
        None,
    )
    if index is None:
        raise FavoritesExternalImportError(
            "External Favorites detach field does not exist."
        )

    field = record.fields[index]
    if field.ownership is FavoritesExternalFieldOwnership.LOCAL:
        return record
    if field.ownership is FavoritesExternalFieldOwnership.DETACHED:
        return record

    fields = list(record.fields)
    fields[index] = replace(
        field,
        ownership=FavoritesExternalFieldOwnership.DETACHED,
    )
    return replace(record, fields=tuple(fields))


def detach_favorites_external_record(
    record: FavoritesExternalRecordState,
) -> FavoritesExternalRecordState:
    """Detach one linked record while preserving exact local values/provenance."""

    if not isinstance(record, FavoritesExternalRecordState):
        raise TypeError(
            "External Favorites detach requires FavoritesExternalRecordState."
        )
    if record.external_identity is None:
        return record
    if record.detached:
        return record

    fields = tuple(
        (
            replace(
                field,
                ownership=FavoritesExternalFieldOwnership.DETACHED,
            )
            if field.ownership is FavoritesExternalFieldOwnership.EXTERNAL
            else field
        )
        for field in record.fields
    )
    return replace(
        record,
        fields=fields,
        detached=True,
    )


__all__ = [
    "FavoritesExternalChangeKind",
    "FavoritesExternalFieldObservation",
    "FavoritesExternalFieldObservationState",
    "FavoritesExternalFieldOwnership",
    "FavoritesExternalFieldPreview",
    "FavoritesExternalFieldState",
    "FavoritesExternalImportError",
    "FavoritesExternalImportPreview",
    "FavoritesExternalObservationEvidence",
    "FavoritesExternalRecordIdentity",
    "FavoritesExternalRecordObservation",
    "FavoritesExternalRecordObservationState",
    "FavoritesExternalRecordPreview",
    "FavoritesExternalRecordState",
    "FavoritesExternalSource",
    "FavoritesExternalSourceIdentity",
    "detach_favorites_external_field",
    "detach_favorites_external_record",
    "preview_favorites_external_import",
    "preview_favorites_external_source",
]
