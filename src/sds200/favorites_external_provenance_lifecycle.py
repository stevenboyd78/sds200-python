"Startup restoration lifecycle for durable external Favorites provenance."

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .favorites_external import FavoritesExternalRecordState
from .favorites_external_provenance import (
    FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_BYTES,
    FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_FIELDS_PER_RECORD,
    FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_RECORDS,
)
from .favorites_external_provenance_acceptance import (
    FavoritesExternalNameAcceptanceDurableResult,
)
from .favorites_external_provenance_storage import (
    load_favorites_external_provenance,
)
from .favorites_storage import FavoritesStorageSnapshot, FavoritesStorageSource


class FavoritesExternalProvenanceLifecycleState(StrEnum):
    "Classify one external Favorites provenance lifecycle."

    IDLE = "idle"
    ACTIVE = "active"
    FAILED = "failed"
    CLOSED = "closed"


class FavoritesExternalProvenanceLifecycleAdvanceError(ValueError):
    """Report stale or mismatched evidence during lifecycle advancement."""


@dataclass(frozen=True, slots=True)
class FavoritesExternalProvenanceLifecycleSnapshot:
    "Immutable evidence from one external Favorites provenance lifecycle."

    state: FavoritesExternalProvenanceLifecycleState
    provenance_path: Path
    favorites_snapshot: FavoritesStorageSnapshot | None
    provenance_records: tuple[FavoritesExternalRecordState, ...] | None
    last_error: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.state, FavoritesExternalProvenanceLifecycleState):
            raise TypeError(
                "External Favorites provenance lifecycle state must be "
                "FavoritesExternalProvenanceLifecycleState."
            )
        if not isinstance(self.provenance_path, Path):
            raise TypeError(
                "External Favorites provenance lifecycle path must be pathlib.Path."
            )
        if not self.provenance_path.is_absolute() or not self.provenance_path.name:
            raise ValueError(
                "External Favorites provenance lifecycle path must identify "
                "an absolute file."
            )
        if self.favorites_snapshot is not None and not isinstance(
            self.favorites_snapshot,
            FavoritesStorageSnapshot,
        ):
            raise TypeError(
                "External Favorites provenance lifecycle Favorites evidence must be "
                "FavoritesStorageSnapshot or None."
            )
        if (
            self.provenance_records is not None
            and type(self.provenance_records) is not tuple
        ):
            raise TypeError(
                "External Favorites provenance lifecycle records must be "
                "an immutable tuple or None."
            )
        if self.provenance_records is not None and any(
            not isinstance(record, FavoritesExternalRecordState)
            for record in self.provenance_records
        ):
            raise TypeError(
                "External Favorites provenance lifecycle records must contain only "
                "FavoritesExternalRecordState values."
            )
        if self.last_error is not None and type(self.last_error) is not str:
            raise TypeError(
                "External Favorites provenance lifecycle error must be a string or None."
            )
        if self.last_error is not None and not self.last_error.strip():
            raise ValueError(
                "External Favorites provenance lifecycle error must not be empty."
            )

        if self.state is FavoritesExternalProvenanceLifecycleState.IDLE:
            if (
                self.favorites_snapshot is not None
                or self.provenance_records is not None
                or self.last_error is not None
            ):
                raise ValueError(
                    "Idle external Favorites provenance lifecycle state must not "
                    "contain restoration or failure evidence."
                )
            return

        if self.state is FavoritesExternalProvenanceLifecycleState.ACTIVE:
            if self.favorites_snapshot is None:
                raise ValueError(
                    "Active external Favorites provenance lifecycle state requires "
                    "fresh Favorites snapshot evidence."
                )
            if self.last_error is not None:
                raise ValueError(
                    "Active external Favorites provenance lifecycle state must not "
                    "contain failure evidence."
                )
            return

        if self.state is FavoritesExternalProvenanceLifecycleState.FAILED:
            if self.favorites_snapshot is not None or self.provenance_records is not None:
                raise ValueError(
                    "Failed external Favorites provenance lifecycle state must not "
                    "retain partial restoration evidence."
                )
            if self.last_error is None:
                raise ValueError(
                    "Failed external Favorites provenance lifecycle state requires "
                    "redacted failure evidence."
                )
            return

        if self.favorites_snapshot is None:
            if self.provenance_records is not None:
                raise ValueError(
                    "Closed external Favorites provenance lifecycle state must not "
                    "contain provenance without fresh Favorites snapshot evidence."
                )
            return

        if self.last_error is not None:
            raise ValueError(
                "Closed external Favorites provenance lifecycle state must not mix "
                "successful restoration and failure evidence."
            )

    @property
    def provenance_present(self) -> bool | None:
        "Report persisted-state presence after successful startup restoration."

        if self.favorites_snapshot is None:
            return None
        return self.provenance_records is not None


class FavoritesExternalProvenanceLifecycle:
    "Own one explicit startup restoration of durable external provenance."

    def __init__(
        self,
        storage_source: FavoritesStorageSource,
        provenance_path: str | Path,
        *,
        max_bytes: int = FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_BYTES,
        max_records: int = FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_RECORDS,
        max_fields_per_record: int = (
            FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_FIELDS_PER_RECORD
        ),
    ) -> None:
        read_snapshot = getattr(storage_source, "read_snapshot", None)
        if not callable(read_snapshot):
            raise TypeError(
                "External Favorites provenance lifecycle requires "
                "a FavoritesStorageSource."
            )
        if not isinstance(provenance_path, (str, Path)):
            raise TypeError(
                "External Favorites provenance lifecycle path must be "
                "str or pathlib.Path."
            )
        if isinstance(provenance_path, str) and not provenance_path.strip():
            raise ValueError(
                "External Favorites provenance lifecycle path must not be empty."
            )

        resolved_path = Path(provenance_path)
        if not resolved_path.is_absolute() or not resolved_path.name:
            raise ValueError(
                "External Favorites provenance lifecycle path must identify "
                "an absolute file."
            )
        _require_positive_limit(
            max_bytes,
            label="External Favorites provenance lifecycle maximum size",
        )
        _require_positive_limit(
            max_records,
            label="External Favorites provenance lifecycle maximum record count",
        )
        _require_positive_limit(
            max_fields_per_record,
            label="External Favorites provenance lifecycle maximum field count",
        )

        self.storage_source = storage_source
        self.provenance_path = resolved_path
        self.max_bytes = max_bytes
        self.max_records = max_records
        self.max_fields_per_record = max_fields_per_record

        self._lifecycle_lock = threading.RLock()
        self._state = FavoritesExternalProvenanceLifecycleState.IDLE
        self._favorites_snapshot: FavoritesStorageSnapshot | None = None
        self._provenance_records: tuple[FavoritesExternalRecordState, ...] | None = None
        self._last_error: str | None = None
        self._last_adopted_name_acceptance_result: (
            FavoritesExternalNameAcceptanceDurableResult | None
        ) = None

    def snapshot(self) -> FavoritesExternalProvenanceLifecycleSnapshot:
        "Return immutable lifecycle and restoration evidence."

        with self._lifecycle_lock:
            return self._snapshot_locked()

    def start(self) -> FavoritesExternalProvenanceLifecycleSnapshot:
        "Read one fresh Favorites snapshot and restore persisted provenance."

        with self._lifecycle_lock:
            if self._state is FavoritesExternalProvenanceLifecycleState.ACTIVE:
                return self._snapshot_locked()
            if self._state is FavoritesExternalProvenanceLifecycleState.CLOSED:
                raise RuntimeError(
                    "External Favorites provenance lifecycle is closed "
                    "and cannot be started."
                )
            if self._state is FavoritesExternalProvenanceLifecycleState.FAILED:
                raise RuntimeError(
                    "External Favorites provenance lifecycle startup failed "
                    "and cannot be retried."
                )

            try:
                favorites_snapshot = self.storage_source.read_snapshot()
                if not isinstance(favorites_snapshot, FavoritesStorageSnapshot):
                    raise TypeError(
                        "External Favorites provenance lifecycle storage source "
                        "must return FavoritesStorageSnapshot."
                    )
                provenance_records = load_favorites_external_provenance(
                    self.provenance_path,
                    favorites_snapshot,
                    max_bytes=self.max_bytes,
                    max_records=self.max_records,
                    max_fields_per_record=self.max_fields_per_record,
                )
            except BaseException as error:
                self._state = FavoritesExternalProvenanceLifecycleState.FAILED
                self._favorites_snapshot = None
                self._provenance_records = None
                self._last_error = error.__class__.__name__
                raise

            self._state = FavoritesExternalProvenanceLifecycleState.ACTIVE
            self._favorites_snapshot = favorites_snapshot
            self._provenance_records = provenance_records
            self._last_error = None
            return self._snapshot_locked()

    def close(self) -> None:
        "Permanently close this lifecycle owner without mutating persisted state."

        with self._lifecycle_lock:
            if self._state is FavoritesExternalProvenanceLifecycleState.CLOSED:
                return
            self._state = FavoritesExternalProvenanceLifecycleState.CLOSED

    def advance_after_name_acceptance(
        self,
        result: FavoritesExternalNameAcceptanceDurableResult,
    ) -> FavoritesExternalProvenanceLifecycleSnapshot:
        """Adopt one already-verified durable name acceptance in memory."""

        if type(result) is not FavoritesExternalNameAcceptanceDurableResult:
            raise TypeError(
                "External Favorites provenance lifecycle advancement requires "
                "FavoritesExternalNameAcceptanceDurableResult."
            )

        with self._lifecycle_lock:
            if self._state is not FavoritesExternalProvenanceLifecycleState.ACTIVE:
                raise RuntimeError(
                    "External Favorites provenance lifecycle must be active "
                    "to advance after name acceptance."
                )
            if self.provenance_path != result.provenance_path:
                raise FavoritesExternalProvenanceLifecycleAdvanceError(
                    "External Favorites provenance lifecycle path does not match "
                    "the durable name acceptance."
                )

            if self._last_adopted_name_acceptance_result is result:
                return self._snapshot_locked()

            if (
                self._favorites_snapshot
                != result.execution.plan.write_plan.baseline_snapshot
            ):
                raise FavoritesExternalProvenanceLifecycleAdvanceError(
                    "External Favorites provenance lifecycle Favorites evidence "
                    "does not match the durable name acceptance baseline."
                )
            if self._provenance_records != result.baseline_provenance_records:
                raise FavoritesExternalProvenanceLifecycleAdvanceError(
                    "External Favorites provenance lifecycle records do not match "
                    "the durable name acceptance baseline."
                )

            self._favorites_snapshot = result.execution.observed_snapshot
            self._provenance_records = result.provenance_records
            self._last_adopted_name_acceptance_result = result
            return self._snapshot_locked()

    def _snapshot_locked(self) -> FavoritesExternalProvenanceLifecycleSnapshot:
        return FavoritesExternalProvenanceLifecycleSnapshot(
            state=self._state,
            provenance_path=self.provenance_path,
            favorites_snapshot=self._favorites_snapshot,
            provenance_records=self._provenance_records,
            last_error=self._last_error,
        )


def _require_positive_limit(value: int, *, label: str) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{label} must be a positive integer.")


__all__ = [
    "FavoritesExternalProvenanceLifecycle",
    "FavoritesExternalProvenanceLifecycleAdvanceError",
    "FavoritesExternalProvenanceLifecycleSnapshot",
    "FavoritesExternalProvenanceLifecycleState",
]
