from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sds200 import (
    FavoritesExternalFieldBinding,
    FavoritesExternalFieldObservation,
    FavoritesExternalFieldObservationState,
    FavoritesExternalFieldOwnership,
    FavoritesExternalObservationEvidence,
    FavoritesExternalProvenanceLifecycle,
    FavoritesExternalRecordIdentity,
    FavoritesExternalRecordObservation,
    FavoritesExternalRecordObservationState,
    FavoritesExternalRefreshResult,
    FavoritesExternalSourceIdentity,
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
    bind_favorites_external_record,
    detach_favorites_external_record,
    plan_favorites_external_refresh_record_import,
    preview_favorites_external_import,
    save_favorites_external_provenance,
    select_favorites_record_target,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "favorites"


def snapshot() -> FavoritesStorageSnapshot:
    return FavoritesStorageSnapshot(
        catalog_bytes=(FIXTURE_ROOT / "synthetic-f_list.cfg").read_bytes(),
        documents=(FavoritesStorageDocument(
            "f_000001.hpd", (FIXTURE_ROOT / "synthetic-favorites.hpd").read_bytes()
        ),),
    )


def identity(value: str) -> FavoritesExternalRecordIdentity:
    return FavoritesExternalRecordIdentity(
        FavoritesExternalSourceIdentity("synthetic-provider", "metro"), value
    )


def evidence(revision: str) -> FavoritesExternalObservationEvidence:
    return FavoritesExternalObservationEvidence(
        datetime(2026, 8, 16, 12, 0, tzinfo=UTC), revision
    )


def active_observation(value: str, name: str) -> FavoritesExternalRecordObservation:
    return FavoritesExternalRecordObservation(
        identity(value), evidence(f"{value}-current"),
        (FavoritesExternalFieldObservation(
            "name", FavoritesExternalFieldObservationState.VALUE, name
        ),),
    )


def linked_state(
    source: FavoritesStorageSnapshot, index: int, value: str,
    *, detached: bool = False,
):
    target = select_favorites_record_target(source, index, document_index=0)
    state = bind_favorites_external_record(
        target, active_observation(value, target.record.fields[2]),
        (FavoritesExternalFieldBinding(
            "name", 2, FavoritesExternalFieldOwnership.EXTERNAL
        ),),
    )
    if detached:
        return detach_favorites_external_record(state)
    return state


class Storage:
    def __init__(self, value: object) -> None:
        self.value = value
        self.calls = 0

    def read_snapshot(self):
        self.calls += 1
        if isinstance(self.value, BaseException):
            raise self.value
        return self.value


def import_plan(tmp_path: Path, *, records=None):
    source = snapshot()
    path = tmp_path / "state" / "provenance.json"
    if records is not None:
        save_favorites_external_provenance(records, path)
    storage = Storage(source)
    lifecycle = FavoritesExternalProvenanceLifecycle(storage, path)
    lifecycle_snapshot = lifecycle.start()
    anchor = select_favorites_record_target(source, 4, document_index=0)
    template = select_favorites_record_target(source, 5, document_index=0).record
    observation = active_observation("new-channel", template.fields[2])
    refresh = FavoritesExternalRefreshResult(
        lifecycle_snapshot, (observation,),
        preview_favorites_external_import(records or (), (observation,)),
    )
    plan = plan_favorites_external_refresh_record_import(
        refresh, refresh.preview.records[0], anchor, template,
        (FavoritesExternalFieldBinding(
            "name", 2, FavoritesExternalFieldOwnership.EXTERNAL
        ),),
    )
    return lifecycle, storage, path, plan


def removed_observation(value: str) -> FavoritesExternalRecordObservation:
    return FavoritesExternalRecordObservation(
        identity(value), evidence(f"{value}-removed"), (),
        FavoritesExternalRecordObservationState.REMOVED,
    )
