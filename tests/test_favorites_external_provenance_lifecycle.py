from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path

import pytest

import sds200
from sds200 import (
    FAVORITES_EXTERNAL_PROVENANCE_FILENAME,
    FavoritesExternalFieldBinding,
    FavoritesExternalFieldObservation,
    FavoritesExternalFieldObservationState,
    FavoritesExternalFieldOwnership,
    FavoritesExternalObservationEvidence,
    FavoritesExternalProvenanceError,
    FavoritesExternalProvenanceLifecycle,
    FavoritesExternalProvenanceLifecycleSnapshot,
    FavoritesExternalProvenanceLifecycleState,
    FavoritesExternalRecordIdentity,
    FavoritesExternalRecordObservation,
    FavoritesExternalRecordState,
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


def _linked_state(snapshot: FavoritesStorageSnapshot) -> FavoritesExternalRecordState:
    target = select_favorites_record_target(snapshot, 5, document_index=0)
    observation = FavoritesExternalRecordObservation(
        identity=FavoritesExternalRecordIdentity(
            source=FavoritesExternalSourceIdentity(
                provider="synthetic-provider",
                dataset="metro",
            ),
            record_id="channel-101",
        ),
        evidence=FavoritesExternalObservationEvidence(
            observed_at=datetime(2026, 8, 15, 3, 0, tzinfo=UTC),
            revision="accepted-r1",
        ),
        fields=(
            FavoritesExternalFieldObservation(
                name="name",
                state=FavoritesExternalFieldObservationState.VALUE,
                value=target.record.fields[2],
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


def _state_path(tmp_path: Path) -> Path:
    return tmp_path / "state" / "sdsctl" / FAVORITES_EXTERNAL_PROVENANCE_FILENAME


class FakeStorageSource:
    def __init__(
        self,
        snapshot: object,
        *,
        error: BaseException | None = None,
    ) -> None:
        self.value = snapshot
        self.error = error
        self.read_calls = 0

    def read_snapshot(self) -> object:
        self.read_calls += 1
        if self.error is not None:
            raise self.error
        return self.value


def test_lifecycle_starts_idle_with_no_restoration_evidence(tmp_path: Path) -> None:
    source = FakeStorageSource(_snapshot())
    path = _state_path(tmp_path)
    lifecycle = FavoritesExternalProvenanceLifecycle(source, path)

    observed = lifecycle.snapshot()

    assert observed == FavoritesExternalProvenanceLifecycleSnapshot(
        state=FavoritesExternalProvenanceLifecycleState.IDLE,
        provenance_path=path,
        favorites_snapshot=None,
        provenance_records=None,
        last_error=None,
    )
    assert observed.provenance_present is None
    assert source.read_calls == 0


def test_start_restores_missing_state_against_one_fresh_snapshot(tmp_path: Path) -> None:
    favorites_snapshot = _snapshot()
    source = FakeStorageSource(favorites_snapshot)
    lifecycle = FavoritesExternalProvenanceLifecycle(
        source,
        _state_path(tmp_path),
    )

    restored = lifecycle.start()

    assert restored.state is FavoritesExternalProvenanceLifecycleState.ACTIVE
    assert restored.favorites_snapshot == favorites_snapshot
    assert restored.favorites_snapshot is favorites_snapshot
    assert restored.provenance_records is None
    assert restored.provenance_present is False
    assert restored.last_error is None
    assert source.read_calls == 1


def test_start_preserves_present_empty_state(tmp_path: Path) -> None:
    favorites_snapshot = _snapshot()
    path = _state_path(tmp_path)
    save_favorites_external_provenance((), path)
    source = FakeStorageSource(favorites_snapshot)
    lifecycle = FavoritesExternalProvenanceLifecycle(source, path)

    restored = lifecycle.start()

    assert restored.provenance_records == ()
    assert restored.provenance_present is True
    assert restored.favorites_snapshot is favorites_snapshot
    assert source.read_calls == 1


def test_start_restores_exact_linked_state(tmp_path: Path) -> None:
    favorites_snapshot = _snapshot()
    state = _linked_state(favorites_snapshot)
    path = _state_path(tmp_path)
    save_favorites_external_provenance((state,), path)
    source = FakeStorageSource(favorites_snapshot)
    lifecycle = FavoritesExternalProvenanceLifecycle(source, path)

    restored = lifecycle.start()

    assert restored.provenance_records == (state,)
    assert restored.provenance_present is True
    assert restored.favorites_snapshot is favorites_snapshot
    assert source.read_calls == 1


def test_active_start_is_idempotent_and_does_not_reread_storage(tmp_path: Path) -> None:
    favorites_snapshot = _snapshot()
    source = FakeStorageSource(favorites_snapshot)
    path = _state_path(tmp_path)
    lifecycle = FavoritesExternalProvenanceLifecycle(source, path)

    first = lifecycle.start()
    save_favorites_external_provenance((), path)
    path.write_bytes(b"not canonical provenance")
    second = lifecycle.start()

    assert first == second
    assert source.read_calls == 1
    assert second.state is FavoritesExternalProvenanceLifecycleState.ACTIVE
    assert second.provenance_records is None


def test_stale_provenance_fails_closed_without_partial_restoration_evidence(
    tmp_path: Path,
) -> None:
    baseline = _snapshot()
    state = _linked_state(baseline)
    path = _state_path(tmp_path)
    save_favorites_external_provenance((state,), path)
    stale = FavoritesStorageSnapshot(
        catalog_bytes=baseline.catalog_bytes,
        documents=(
            FavoritesStorageDocument(
                filename="f_000002.hpd",
                content=baseline.documents[0].content,
            ),
        ),
    )
    source = FakeStorageSource(stale)
    lifecycle = FavoritesExternalProvenanceLifecycle(source, path)

    with pytest.raises(FavoritesExternalProvenanceError):
        lifecycle.start()

    failed = lifecycle.snapshot()
    assert failed.state is FavoritesExternalProvenanceLifecycleState.FAILED
    assert failed.favorites_snapshot is None
    assert failed.provenance_records is None
    assert failed.provenance_present is None
    assert failed.last_error == "FavoritesExternalProvenanceError"
    assert source.read_calls == 1


def test_changed_record_identity_fails_closed_without_partial_evidence(
    tmp_path: Path,
) -> None:
    baseline = _snapshot()
    state = _linked_state(baseline)
    path = _state_path(tmp_path)
    save_favorites_external_provenance((state,), path)
    changed = FavoritesStorageSnapshot(
        catalog_bytes=baseline.catalog_bytes,
        documents=(
            FavoritesStorageDocument(
                filename=baseline.documents[0].filename,
                content=baseline.documents[0].content.replace(
                    b"Synthetic Channel",
                    b"Synthetic Changed",
                    1,
                ),
            ),
        ),
    )
    source = FakeStorageSource(changed)
    lifecycle = FavoritesExternalProvenanceLifecycle(source, path)

    with pytest.raises(FavoritesExternalProvenanceError):
        lifecycle.start()

    failed = lifecycle.snapshot()
    assert failed.state is FavoritesExternalProvenanceLifecycleState.FAILED
    assert failed.favorites_snapshot is None
    assert failed.provenance_records is None
    assert failed.last_error == "FavoritesExternalProvenanceError"
    assert source.read_calls == 1


def test_moved_record_fails_closed_without_partial_evidence(tmp_path: Path) -> None:
    baseline = _snapshot()
    state = _linked_state(baseline)
    path = _state_path(tmp_path)
    save_favorites_external_provenance((state,), path)
    moved = FavoritesStorageSnapshot(
        catalog_bytes=baseline.catalog_bytes,
        documents=(
            FavoritesStorageDocument(
                filename="f_000002.hpd",
                content=baseline.documents[0].content,
            ),
            baseline.documents[0],
        ),
    )
    source = FakeStorageSource(moved)
    lifecycle = FavoritesExternalProvenanceLifecycle(source, path)

    with pytest.raises(FavoritesExternalProvenanceError):
        lifecycle.start()

    failed = lifecycle.snapshot()
    assert failed.state is FavoritesExternalProvenanceLifecycleState.FAILED
    assert failed.favorites_snapshot is None
    assert failed.provenance_records is None
    assert failed.last_error == "FavoritesExternalProvenanceError"
    assert source.read_calls == 1


def test_source_failure_is_redacted_in_lifecycle_state(tmp_path: Path) -> None:
    source = FakeStorageSource(
        _snapshot(),
        error=RuntimeError("secret source failure detail"),
    )
    lifecycle = FavoritesExternalProvenanceLifecycle(
        source,
        _state_path(tmp_path),
    )

    with pytest.raises(RuntimeError, match="secret source failure detail"):
        lifecycle.start()

    failed = lifecycle.snapshot()
    assert failed.state is FavoritesExternalProvenanceLifecycleState.FAILED
    assert failed.last_error == "RuntimeError"
    assert "secret source failure detail" not in repr(failed)
    assert source.read_calls == 1


def test_process_control_exception_is_reraised_and_terminally_redacted(
    tmp_path: Path,
) -> None:
    source = FakeStorageSource(_snapshot(), error=KeyboardInterrupt())
    lifecycle = FavoritesExternalProvenanceLifecycle(source, _state_path(tmp_path))

    with pytest.raises(KeyboardInterrupt):
        lifecycle.start()

    observed = lifecycle.snapshot()
    assert observed.state is FavoritesExternalProvenanceLifecycleState.FAILED
    assert observed.favorites_snapshot is None
    assert observed.provenance_records is None
    assert observed.last_error == "KeyboardInterrupt"
    assert source.read_calls == 1
    with pytest.raises(RuntimeError, match="cannot be retried"):
        lifecycle.start()
    assert source.read_calls == 1


def test_invalid_storage_snapshot_type_fails_closed(tmp_path: Path) -> None:
    source = FakeStorageSource(object())
    lifecycle = FavoritesExternalProvenanceLifecycle(
        source,
        _state_path(tmp_path),
    )

    with pytest.raises(TypeError, match="must return FavoritesStorageSnapshot"):
        lifecycle.start()

    failed = lifecycle.snapshot()
    assert failed.state is FavoritesExternalProvenanceLifecycleState.FAILED
    assert failed.last_error == "TypeError"
    assert source.read_calls == 1


def test_failed_lifecycle_cannot_be_retried(tmp_path: Path) -> None:
    source = FakeStorageSource(
        _snapshot(),
        error=RuntimeError("startup failed"),
    )
    lifecycle = FavoritesExternalProvenanceLifecycle(
        source,
        _state_path(tmp_path),
    )

    with pytest.raises(RuntimeError, match="startup failed"):
        lifecycle.start()
    with pytest.raises(RuntimeError, match="cannot be retried"):
        lifecycle.start()

    assert source.read_calls == 1


def test_close_is_idempotent_and_prevents_start(tmp_path: Path) -> None:
    source = FakeStorageSource(_snapshot())
    lifecycle = FavoritesExternalProvenanceLifecycle(
        source,
        _state_path(tmp_path),
    )

    lifecycle.close()
    lifecycle.close()

    closed = lifecycle.snapshot()
    assert closed.state is FavoritesExternalProvenanceLifecycleState.CLOSED
    assert closed.favorites_snapshot is None
    assert closed.provenance_records is None
    with pytest.raises(RuntimeError, match="closed and cannot be started"):
        lifecycle.start()
    assert source.read_calls == 0


def test_close_retains_successful_restoration_evidence(tmp_path: Path) -> None:
    favorites_snapshot = _snapshot()
    state = _linked_state(favorites_snapshot)
    path = _state_path(tmp_path)
    save_favorites_external_provenance((state,), path)
    lifecycle = FavoritesExternalProvenanceLifecycle(
        FakeStorageSource(favorites_snapshot),
        path,
    )

    active = lifecycle.start()
    lifecycle.close()
    closed = lifecycle.snapshot()

    assert active.state is FavoritesExternalProvenanceLifecycleState.ACTIVE
    assert closed.state is FavoritesExternalProvenanceLifecycleState.CLOSED
    assert closed.favorites_snapshot is favorites_snapshot
    assert closed.provenance_records == (state,)
    assert closed.last_error is None
    assert closed.provenance_present is True


def test_close_after_failure_retains_only_redacted_failure_evidence(
    tmp_path: Path,
) -> None:
    source = FakeStorageSource(
        _snapshot(),
        error=RuntimeError("secret startup failure"),
    )
    lifecycle = FavoritesExternalProvenanceLifecycle(source, _state_path(tmp_path))

    with pytest.raises(RuntimeError, match="secret startup failure"):
        lifecycle.start()
    lifecycle.close()

    closed = lifecycle.snapshot()
    assert closed.state is FavoritesExternalProvenanceLifecycleState.CLOSED
    assert closed.favorites_snapshot is None
    assert closed.provenance_records is None
    assert closed.provenance_present is None
    assert closed.last_error == "RuntimeError"
    assert "secret startup failure" not in repr(closed)
    with pytest.raises(RuntimeError, match="closed and cannot be started"):
        lifecycle.start()
    assert source.read_calls == 1


@pytest.mark.parametrize(
    ("path", "error"),
    [
        ("", ValueError),
        ("relative.json", ValueError),
        (object(), TypeError),
    ],
)
def test_lifecycle_requires_absolute_state_file(
    path: object,
    error: type[BaseException],
) -> None:
    with pytest.raises(error):
        FavoritesExternalProvenanceLifecycle(
            FakeStorageSource(_snapshot()),
            path,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("max_bytes", "max_records", "max_fields"),
    [
        (0, 4096, 256),
        (True, 4096, 256),
        (1024, 0, 256),
        (1024, True, 256),
        (1024, 4096, 0),
        (1024, 4096, True),
    ],
)
def test_lifecycle_rejects_invalid_structural_limits(
    tmp_path: Path,
    max_bytes: int,
    max_records: int,
    max_fields: int,
) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        FavoritesExternalProvenanceLifecycle(
            FakeStorageSource(_snapshot()),
            _state_path(tmp_path),
            max_bytes=max_bytes,
            max_records=max_records,
            max_fields_per_record=max_fields,
        )


def test_lifecycle_requires_storage_source(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="FavoritesStorageSource"):
        FavoritesExternalProvenanceLifecycle(
            object(),  # type: ignore[arg-type]
            _state_path(tmp_path),
        )


def test_lifecycle_snapshot_is_immutable(tmp_path: Path) -> None:
    snapshot = FavoritesExternalProvenanceLifecycle(
        FakeStorageSource(_snapshot()),
        _state_path(tmp_path),
    ).snapshot()

    with pytest.raises(FrozenInstanceError):
        snapshot.last_error = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("favorites_snapshot", "provenance_records", "last_error"),
    [
        (None, (), None),
        (_snapshot(), None, "RuntimeError"),
        (_snapshot(), (), "RuntimeError"),
    ],
)
def test_closed_snapshot_rejects_inconsistent_evidence(
    tmp_path: Path,
    favorites_snapshot: FavoritesStorageSnapshot | None,
    provenance_records: tuple[FavoritesExternalRecordState, ...] | None,
    last_error: str | None,
) -> None:
    with pytest.raises(ValueError, match="Closed external Favorites"):
        FavoritesExternalProvenanceLifecycleSnapshot(
            state=FavoritesExternalProvenanceLifecycleState.CLOSED,
            provenance_path=_state_path(tmp_path),
            favorites_snapshot=favorites_snapshot,
            provenance_records=provenance_records,
            last_error=last_error,
        )


def test_snapshot_rejects_empty_failure_evidence(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="error must not be empty"):
        FavoritesExternalProvenanceLifecycleSnapshot(
            state=FavoritesExternalProvenanceLifecycleState.FAILED,
            provenance_path=_state_path(tmp_path),
            favorites_snapshot=None,
            provenance_records=None,
            last_error=" ",
        )


def test_lifecycle_public_api_is_exported() -> None:
    expected = {
        "FavoritesExternalProvenanceLifecycle",
        "FavoritesExternalProvenanceLifecycleSnapshot",
        "FavoritesExternalProvenanceLifecycleState",
    }
    assert expected <= set(sds200.__all__)
    for name in expected:
        assert getattr(sds200, name) is not None
