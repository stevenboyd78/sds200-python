from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path

import pytest

import sds200
from sds200 import (
    FAVORITES_EXTERNAL_PROVENANCE_FILENAME,
    FavoritesExternalChangeKind,
    FavoritesExternalFieldBinding,
    FavoritesExternalFieldObservation,
    FavoritesExternalFieldObservationState,
    FavoritesExternalFieldOwnership,
    FavoritesExternalImportPreview,
    FavoritesExternalObservationEvidence,
    FavoritesExternalProvenanceLifecycle,
    FavoritesExternalRecordIdentity,
    FavoritesExternalRecordObservation,
    FavoritesExternalRecordState,
    FavoritesExternalRefreshResult,
    FavoritesExternalRefreshSession,
    FavoritesExternalSourceIdentity,
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
    bind_favorites_external_record,
    save_favorites_external_provenance,
    select_favorites_record_target,
)

_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "favorites"


def _snapshot() -> FavoritesStorageSnapshot:
    return FavoritesStorageSnapshot(
        catalog_bytes=(_FIXTURE_ROOT / "synthetic-f_list.cfg").read_bytes(),
        documents=(
            FavoritesStorageDocument(
                filename="f_000001.hpd",
                content=(_FIXTURE_ROOT / "synthetic-favorites.hpd").read_bytes(),
            ),
        ),
    )


def _path(tmp_path: Path) -> Path:
    return tmp_path / "state" / FAVORITES_EXTERNAL_PROVENANCE_FILENAME


def _observation(name: str = "Synthetic Channel") -> FavoritesExternalRecordObservation:
    return FavoritesExternalRecordObservation(
        identity=FavoritesExternalRecordIdentity(
            source=FavoritesExternalSourceIdentity(
                provider="synthetic-provider",
                dataset="metro",
            ),
            record_id="channel-101",
        ),
        evidence=FavoritesExternalObservationEvidence(
            observed_at=datetime(2026, 8, 15, 3, 0, tzinfo=UTC),
            revision=f"revision-{name}",
        ),
        fields=(
            FavoritesExternalFieldObservation(
                name="name",
                state=FavoritesExternalFieldObservationState.VALUE,
                value=name,
            ),
        ),
    )


class FakeStorageSource:
    def __init__(self, value: FavoritesStorageSnapshot) -> None:
        self.value = value

    def read_snapshot(self) -> FavoritesStorageSnapshot:
        return self.value


class FailingStorageSource:
    def read_snapshot(self) -> FavoritesStorageSnapshot:
        raise RuntimeError("synthetic startup failure")


class FakeExternalSource:
    def __init__(self, values: list[object]) -> None:
        self.values = values
        self.read_calls = 0

    def read_observations(self) -> object:
        value = self.values[self.read_calls]
        self.read_calls += 1
        if isinstance(value, BaseException):
            raise value
        return value


class CountingLifecycle(FavoritesExternalProvenanceLifecycle):
    def __init__(self, storage: FakeStorageSource, path: Path) -> None:
        super().__init__(storage, path)
        self.snapshot_calls = 0

    def snapshot(self) -> sds200.FavoritesExternalProvenanceLifecycleSnapshot:
        self.snapshot_calls += 1
        return super().snapshot()


def _active_lifecycle(
    tmp_path: Path,
    *,
    records: tuple[FavoritesExternalRecordState, ...] | None = None,
) -> FavoritesExternalProvenanceLifecycle:
    snapshot = _snapshot()
    path = _path(tmp_path)
    if records is not None:
        save_favorites_external_provenance(records, path)
    lifecycle = FavoritesExternalProvenanceLifecycle(FakeStorageSource(snapshot), path)
    lifecycle.start()
    return lifecycle


def test_refresh_with_missing_provenance_retains_absence(tmp_path: Path) -> None:
    lifecycle = _active_lifecycle(tmp_path)
    observations = (_observation(),)
    source = FakeExternalSource([observations])

    result = FavoritesExternalRefreshSession(lifecycle, source).refresh()

    assert result.lifecycle_snapshot.provenance_records is None
    assert result.lifecycle_snapshot.provenance_present is False
    assert result.observations is observations
    assert result.preview.records[0].kind is FavoritesExternalChangeKind.ADDED


def test_refresh_with_present_empty_provenance_retains_presence(tmp_path: Path) -> None:
    lifecycle = _active_lifecycle(tmp_path, records=())

    result = FavoritesExternalRefreshSession(
        lifecycle,
        FakeExternalSource([(_observation(),)]),
    ).refresh()

    assert result.lifecycle_snapshot.provenance_records == ()
    assert result.lifecycle_snapshot.provenance_present is True
    assert result.preview.records[0].kind is FavoritesExternalChangeKind.ADDED


def test_refresh_with_linked_provenance_previews_update_without_mutation(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    target = select_favorites_record_target(snapshot, 5, document_index=0)
    state = bind_favorites_external_record(
        target,
        _observation(),
        (
            FavoritesExternalFieldBinding(
                name="name",
                field_index=2,
                ownership=FavoritesExternalFieldOwnership.EXTERNAL,
            ),
        ),
    )
    path = _path(tmp_path)
    save_favorites_external_provenance((state,), path)
    provenance_bytes = path.read_bytes()
    lifecycle = FavoritesExternalProvenanceLifecycle(FakeStorageSource(snapshot), path)
    lifecycle.start()

    result = FavoritesExternalRefreshSession(
        lifecycle,
        FakeExternalSource([(_observation("Dispatch Updated"),)]),
    ).refresh()

    assert result.preview.records[0].kind is FavoritesExternalChangeKind.REPLACED
    assert lifecycle.snapshot().favorites_snapshot is snapshot
    assert snapshot.documents[0].content == _snapshot().documents[0].content
    assert path.read_bytes() == provenance_bytes


def test_each_refresh_captures_one_snapshot_and_reads_source_once(
    tmp_path: Path,
) -> None:
    lifecycle = CountingLifecycle(FakeStorageSource(_snapshot()), _path(tmp_path))
    lifecycle.start()
    source = FakeExternalSource([(_observation(),), (_observation("New Name"),)])
    session = FavoritesExternalRefreshSession(lifecycle, source)

    first = session.refresh()
    second = session.refresh()

    assert lifecycle.snapshot_calls == 2
    assert source.read_calls == 2
    assert first.observations != second.observations
    assert first.preview != second.preview


@pytest.mark.parametrize("state", ["idle", "failed", "closed"])
def test_non_active_lifecycle_rejects_before_source_read(
    tmp_path: Path,
    state: str,
) -> None:
    lifecycle = FavoritesExternalProvenanceLifecycle(
        FakeStorageSource(_snapshot()),
        _path(tmp_path),
    )
    if state == "failed":
        lifecycle = FavoritesExternalProvenanceLifecycle(
            FailingStorageSource(),
            _path(tmp_path),
        )
        with pytest.raises(RuntimeError, match="synthetic startup failure"):
            lifecycle.start()
    elif state == "closed":
        lifecycle.start()
        lifecycle.close()
    source = FakeExternalSource([(_observation(),)])

    with pytest.raises(RuntimeError, match="requires an active"):
        FavoritesExternalRefreshSession(lifecycle, source).refresh()
    assert source.read_calls == 0


def test_source_exception_propagates_and_later_refresh_retries(tmp_path: Path) -> None:
    lifecycle = _active_lifecycle(tmp_path)
    source = FakeExternalSource([RuntimeError("provider detail"), (_observation(),)])
    session = FavoritesExternalRefreshSession(lifecycle, source)

    with pytest.raises(RuntimeError, match="provider detail"):
        session.refresh()
    result = session.refresh()

    assert source.read_calls == 2
    assert result.observations == (_observation(),)


def test_invalid_source_shape_fails_without_retained_partial_state(
    tmp_path: Path,
) -> None:
    lifecycle = _active_lifecycle(tmp_path)
    source = FakeExternalSource([[_observation()], (_observation(),)])
    session = FavoritesExternalRefreshSession(lifecycle, source)

    with pytest.raises(TypeError, match="immutable tuple"):
        session.refresh()
    assert vars(session).keys() == {"lifecycle", "source", "_refresh_lock"}
    assert session.refresh().observations == (_observation(),)


def test_refresh_result_is_immutable(tmp_path: Path) -> None:
    result = FavoritesExternalRefreshSession(
        _active_lifecycle(tmp_path),
        FakeExternalSource([(_observation(),)]),
    ).refresh()

    with pytest.raises(FrozenInstanceError):
        result.preview = result.preview  # type: ignore[misc]


def test_refresh_result_rejects_preview_mismatched_with_retained_evidence(
    tmp_path: Path,
) -> None:
    lifecycle_snapshot = _active_lifecycle(tmp_path).snapshot()
    observations = (_observation(),)

    with pytest.raises(ValueError, match="must match its retained evidence"):
        FavoritesExternalRefreshResult(
            lifecycle_snapshot=lifecycle_snapshot,
            observations=observations,
            preview=FavoritesExternalImportPreview(records=()),
        )


def test_refresh_public_symbols_are_package_exports() -> None:
    for name in ("FavoritesExternalRefreshResult", "FavoritesExternalRefreshSession"):
        assert name in sds200.__all__
        assert hasattr(sds200, name)
