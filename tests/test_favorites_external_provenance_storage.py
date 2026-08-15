from __future__ import annotations

import errno
import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

import sds200
import sds200.favorites_external_provenance_storage as provenance_storage
from sds200 import (
    FAVORITES_EXTERNAL_PROVENANCE_FILENAME,
    FavoritesExternalFieldBinding,
    FavoritesExternalFieldObservation,
    FavoritesExternalFieldObservationState,
    FavoritesExternalFieldOwnership,
    FavoritesExternalObservationEvidence,
    FavoritesExternalProvenanceError,
    FavoritesExternalProvenanceStorageError,
    FavoritesExternalRecordIdentity,
    FavoritesExternalRecordObservation,
    FavoritesExternalRecordState,
    FavoritesExternalSourceIdentity,
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
    bind_favorites_external_record,
    deserialize_favorites_external_provenance,
    load_favorites_external_provenance,
    resolve_configuration_paths,
    save_favorites_external_provenance,
    save_favorites_external_provenance_if_current,
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
                content=(_FIXTURE_ROOT / "synthetic-favorites.hpd").read_bytes(),
            ),
        ),
    )


def _linked_state(
    snapshot: FavoritesStorageSnapshot,
    *,
    revision: str = "accepted-r1",
) -> FavoritesExternalRecordState:
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
            observed_at=datetime(2026, 8, 15, 2, 0, tzinfo=UTC),
            revision=revision,
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


def _write_private(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, mode=0o700)
    path.parent.chmod(0o700)
    path.write_bytes(content)
    path.chmod(0o600)


def test_state_filename_and_configuration_path_are_stable() -> None:
    assert FAVORITES_EXTERNAL_PROVENANCE_FILENAME == "favorites-external-provenance.json"
    paths = resolve_configuration_paths(
        environ={},
        home=Path("/tmp/sds200-provenance-path-test"),
    )
    assert paths.favorites_external_provenance_file == (
        paths.user_state_dir / FAVORITES_EXTERNAL_PROVENANCE_FILENAME
    )


def test_missing_state_is_distinct_from_present_empty_document(tmp_path: Path) -> None:
    snapshot = _snapshot()
    path = _state_path(tmp_path)

    assert load_favorites_external_provenance(path, snapshot) is None
    assert path.parent.exists() is False

    assert save_favorites_external_provenance((), path) == path
    assert path.exists()
    assert load_favorites_external_provenance(path, snapshot) == ()


def test_save_and_load_round_trip_exact_linked_state(tmp_path: Path) -> None:
    snapshot = _snapshot()
    state = _linked_state(snapshot)
    path = _state_path(tmp_path)

    assert save_favorites_external_provenance((state,), path) == path
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_bytes() == serialize_favorites_external_provenance((state,))
    assert load_favorites_external_provenance(path, snapshot) == (state,)
    assert list(path.parent.glob(f".{path.name}.*.tmp")) == []


def test_save_replaces_exact_existing_state_under_publication_lock(tmp_path: Path) -> None:
    snapshot = _snapshot()
    path = _state_path(tmp_path)
    first = _linked_state(snapshot, revision="accepted-r1")
    second = _linked_state(snapshot, revision="accepted-r2")

    save_favorites_external_provenance((first,), path)
    save_favorites_external_provenance((second,), path)

    assert load_favorites_external_provenance(path, snapshot) == (second,)
    assert path.read_bytes() == serialize_favorites_external_provenance((second,))


def test_conditional_save_distinguishes_absent_and_exact_state(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    path = _state_path(tmp_path)
    first = _linked_state(snapshot, revision="accepted-r1")
    second = _linked_state(snapshot, revision="accepted-r2")

    save_favorites_external_provenance_if_current(
        (first,),
        path,
        expected_current_records=None,
    )
    assert load_favorites_external_provenance(path, snapshot) == (first,)

    save_favorites_external_provenance_if_current(
        (second,),
        path,
        expected_current_records=(first,),
    )
    assert load_favorites_external_provenance(path, snapshot) == (second,)

    with pytest.raises(
        FavoritesExternalProvenanceStorageError,
        match="does not match the expected current state",
    ):
        save_favorites_external_provenance_if_current(
            (),
            path,
            expected_current_records=None,
        )

    assert load_favorites_external_provenance(path, snapshot) == (second,)


def test_conditional_save_rejects_stale_expected_state_without_replacement(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    path = _state_path(tmp_path)
    first = _linked_state(snapshot, revision="accepted-r1")
    current = _linked_state(snapshot, revision="accepted-r2")
    proposed = _linked_state(snapshot, revision="accepted-r3")

    save_favorites_external_provenance((current,), path)
    before = path.read_bytes()

    with pytest.raises(
        FavoritesExternalProvenanceStorageError,
        match="does not match the expected current state",
    ):
        save_favorites_external_provenance_if_current(
            (proposed,),
            path,
            expected_current_records=(first,),
        )

    assert path.read_bytes() == before
    assert load_favorites_external_provenance(path, snapshot) == (current,)


def test_conditional_save_treats_present_empty_as_distinct_from_absent(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    path = _state_path(tmp_path)
    state = _linked_state(snapshot)

    save_favorites_external_provenance((), path)

    with pytest.raises(
        FavoritesExternalProvenanceStorageError,
        match="does not match the expected current state",
    ):
        save_favorites_external_provenance_if_current(
            (state,),
            path,
            expected_current_records=None,
        )

    save_favorites_external_provenance_if_current(
        (state,),
        path,
        expected_current_records=(),
    )
    assert load_favorites_external_provenance(path, snapshot) == (state,)


@pytest.mark.parametrize("path", ["relative.json", ""])
def test_state_path_must_be_nonempty_absolute_path(path: str) -> None:
    with pytest.raises(ValueError):
        load_favorites_external_provenance(path, _snapshot())


def test_load_rejects_symlink_state_file(tmp_path: Path) -> None:
    snapshot = _snapshot()
    path = _state_path(tmp_path)
    real = path.parent / "real.json"
    _write_private(real, serialize_favorites_external_provenance((_linked_state(snapshot),)))
    try:
        path.symlink_to(real)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")

    with pytest.raises(FavoritesExternalProvenanceStorageError, match="regular file"):
        load_favorites_external_provenance(path, snapshot)


def test_load_rejects_nonprivate_state_file(tmp_path: Path) -> None:
    snapshot = _snapshot()
    path = _state_path(tmp_path)
    _write_private(path, serialize_favorites_external_provenance((_linked_state(snapshot),)))
    path.chmod(0o644)

    with pytest.raises(
        FavoritesExternalProvenanceStorageError,
        match="permissions are unsafe",
    ):
        load_favorites_external_provenance(path, snapshot)


def test_load_rejects_nonprivate_state_directory(tmp_path: Path) -> None:
    snapshot = _snapshot()
    path = _state_path(tmp_path)
    _write_private(path, serialize_favorites_external_provenance((_linked_state(snapshot),)))
    path.parent.chmod(0o755)

    with pytest.raises(
        FavoritesExternalProvenanceStorageError,
        match="directory permissions are unsafe",
    ):
        load_favorites_external_provenance(path, snapshot)


def test_load_enforces_bound_before_codec_parse(tmp_path: Path) -> None:
    snapshot = _snapshot()
    path = _state_path(tmp_path)
    _write_private(path, serialize_favorites_external_provenance((_linked_state(snapshot),)))

    with pytest.raises(FavoritesExternalProvenanceStorageError, match="size is invalid"):
        load_favorites_external_provenance(path, snapshot, max_bytes=8)


def test_load_passes_exact_bytes_to_existing_codec(tmp_path: Path) -> None:
    path = _state_path(tmp_path)
    _write_private(path, b"{}\n")

    with pytest.raises(FavoritesExternalProvenanceError):
        load_favorites_external_provenance(path, _snapshot())


def test_save_refuses_nested_same_process_publication(
    tmp_path: Path,
) -> None:
    if not all(
        hasattr(os, name)
        for name in ("lockf", "F_TLOCK", "F_ULOCK")
    ):
        pytest.skip("POSIX lockf is unavailable.")

    path = _state_path(tmp_path)
    path.parent.mkdir(parents=True, mode=0o700)
    path.parent.chmod(0o700)

    with (
        provenance_storage._publication_lock(path.parent),
        pytest.raises(
            FavoritesExternalProvenanceStorageError,
            match="publication is already active",
        ),
    ):
        save_favorites_external_provenance(
            (_linked_state(_snapshot()),),
            path,
        )

    assert path.exists() is False


def test_save_refuses_concurrent_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not all(hasattr(os, name) for name in ("lockf", "F_TLOCK", "F_ULOCK")):
        pytest.skip("POSIX lockf is unavailable.")

    path = _state_path(tmp_path)
    real_lockf = os.lockf

    def busy_lock(descriptor: int, command: int, length: int) -> None:
        if command == os.F_TLOCK:
            raise OSError(errno.EAGAIN, "provider-secret-must-not-escape")
        real_lockf(descriptor, command, length)

    monkeypatch.setattr(os, "lockf", busy_lock)

    with pytest.raises(
        FavoritesExternalProvenanceStorageError,
        match="publication is already active",
    ) as captured:
        save_favorites_external_provenance((_linked_state(_snapshot()),), path)

    assert "provider-secret-must-not-escape" not in str(captured.value)
    assert path.exists() is False


def test_save_refuses_target_change_observed_before_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    path = _state_path(tmp_path)
    first = _linked_state(snapshot, revision="accepted-r1")
    proposed = _linked_state(snapshot, revision="accepted-r2")
    external = _linked_state(snapshot, revision="unexpected-race")

    save_favorites_external_provenance((first,), path)
    external_content = serialize_favorites_external_provenance((external,))
    original = provenance_storage._require_target_unchanged

    def mutate_then_revalidate(
        target: Path,
        expected: provenance_storage._DurableFileState | None,
        *,
        max_bytes: int,
    ) -> None:
        target.write_bytes(external_content)
        target.chmod(0o600)
        original(
            target,
            expected,
            max_bytes=max_bytes,
        )

    monkeypatch.setattr(
        provenance_storage,
        "_require_target_unchanged",
        mutate_then_revalidate,
    )

    with pytest.raises(
        FavoritesExternalProvenanceStorageError,
        match="changed during publication",
    ):
        save_favorites_external_provenance((proposed,), path)

    assert path.read_bytes() == external_content
    assert list(path.parent.glob(f".{path.name}.*.tmp")) == []


def test_replace_failure_is_redacted_and_cleans_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _state_path(tmp_path)

    def fail_replace(source: object, target: object) -> None:
        del source, target
        raise OSError("provider-secret-must-not-escape")

    monkeypatch.setattr(provenance_storage.os, "replace", fail_replace)

    with pytest.raises(
        FavoritesExternalProvenanceStorageError,
        match="atomically publish",
    ) as captured:
        save_favorites_external_provenance((_linked_state(_snapshot()),), path)

    assert "provider-secret-must-not-escape" not in str(captured.value)
    assert path.exists() is False
    assert list(path.parent.glob(f".{path.name}.*.tmp")) == []


def test_save_refuses_existing_symlink_target(tmp_path: Path) -> None:
    snapshot = _snapshot()
    path = _state_path(tmp_path)
    path.parent.mkdir(parents=True, mode=0o700)
    path.parent.chmod(0o700)
    outside = tmp_path / "outside.json"
    outside.write_text("preserve\n", encoding="utf-8")
    try:
        path.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")

    with pytest.raises(FavoritesExternalProvenanceStorageError, match="regular file"):
        save_favorites_external_provenance((_linked_state(snapshot),), path)

    assert outside.read_text(encoding="utf-8") == "preserve\n"


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
def test_load_rejects_invalid_structural_limits_even_when_file_is_missing(
    tmp_path: Path,
    max_bytes: int,
    max_records: int,
    max_fields: int,
) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        load_favorites_external_provenance(
            _state_path(tmp_path),
            _snapshot(),
            max_bytes=max_bytes,
            max_records=max_records,
            max_fields_per_record=max_fields,
        )


def test_storage_public_api_is_exported() -> None:
    expected = {
        "FAVORITES_EXTERNAL_PROVENANCE_FILENAME",
        "FavoritesExternalProvenanceStorageError",
        "load_favorites_external_provenance",
        "save_favorites_external_provenance",
        "save_favorites_external_provenance_if_current",
    }
    assert expected <= set(sds200.__all__)
    for name in expected:
        assert hasattr(sds200, name)


def test_saved_document_remains_exact_existing_codec_document(tmp_path: Path) -> None:
    snapshot = _snapshot()
    state = _linked_state(snapshot)
    path = _state_path(tmp_path)

    save_favorites_external_provenance((state,), path)
    content = path.read_bytes()

    assert json.loads(content) == json.loads(serialize_favorites_external_provenance((state,)))
    assert deserialize_favorites_external_provenance(content, snapshot) == (state,)
