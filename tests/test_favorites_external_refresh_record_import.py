from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

import sds200
from sds200 import (
    FavoritesExternalFieldBinding,
    FavoritesExternalFieldOwnership,
    FavoritesExternalRefreshResult,
    deserialize_favorites_external_provenance,
    plan_favorites_external_refresh_record_import,
    preview_favorites_external_import,
    select_favorites_record_target,
    serialize_favorites_external_provenance,
)
from tests.checkpoint_c_helpers import active_observation, import_plan, linked_state, snapshot


def test_first_import_exactly_inserts_binds_and_rebinds(tmp_path) -> None:
    _, _, _, plan = import_plan(tmp_path)
    created = plan.intended_state
    assert plan.baseline_provenance_records is None
    assert created.target.source_index == plan.anchor.source_index + 1
    assert created.target.document_index == plan.anchor.document_index
    assert created.external_identity == plan.observation.identity
    assert created.last_observation == plan.observation.evidence
    assert created.fields[0].ownership is FavoritesExternalFieldOwnership.EXTERNAL
    assert plan.intended_provenance_records == (created,)
    encoded = serialize_favorites_external_provenance(plan.intended_provenance_records)
    assert deserialize_favorites_external_provenance(
        encoded, plan.write_plan.intended_snapshot
    ) == plan.intended_provenance_records
    with pytest.raises(FrozenInstanceError):
        plan.anchor = plan.anchor  # type: ignore[misc]


def test_existing_order_shift_and_bytes_are_preserved(tmp_path) -> None:
    source = snapshot()
    before = linked_state(source, 5, "later")
    other = linked_state(source, 14, "other-document-logical")
    _, _, _, plan = import_plan(tmp_path, records=(other, before))
    shifted_other = plan.intended_provenance_records[0]
    shifted = plan.intended_provenance_records[1]
    assert shifted_other.target.source_index == other.target.source_index + 1
    assert shifted_other.target.record.raw_bytes == other.target.record.raw_bytes
    assert replace(shifted_other, target=other.target) == other
    assert shifted.target.source_index == before.target.source_index + 1
    assert shifted.target.record.raw_bytes == before.target.record.raw_bytes
    assert replace(shifted, target=before.target) == before
    assert plan.intended_provenance_records[-1] is plan.intended_state


def test_import_rejects_foreign_preview_evidence_anchor_and_bindings(tmp_path) -> None:
    _, _, _, plan = import_plan(tmp_path)
    args = (plan.refresh_result, plan.selected_preview, plan.anchor, plan.template, plan.bindings)
    with pytest.raises(ValueError, match="exact retained"):
        plan_favorites_external_refresh_record_import(
            args[0], replace(args[1]), args[2], args[3], args[4]
        )
    foreign = active_observation("new-channel", plan.template.fields[2])
    bad_refresh = FavoritesExternalRefreshResult(
        plan.refresh_result.lifecycle_snapshot, (foreign,),
        preview_favorites_external_import((), (foreign,)),
    )
    with pytest.raises(ValueError, match="exact retained"):
        plan_favorites_external_refresh_record_import(
            bad_refresh, plan.selected_preview, args[2], args[3], args[4]
        )
    with pytest.raises(ValueError):
        plan_favorites_external_refresh_record_import(
            args[0], args[1], select_favorites_record_target(snapshot(), 3, document_index=0),
            args[3], args[4]
        )
    with pytest.raises(ValueError, match="externally owned"):
        plan_favorites_external_refresh_record_import(
            args[0], args[1], args[2], args[3],
            (FavoritesExternalFieldBinding("name", 2, FavoritesExternalFieldOwnership.LOCAL),),
        )


def test_import_editor_rejects_unsupported_invalid_and_mismatched_templates(tmp_path) -> None:
    _, _, _, plan = import_plan(tmp_path)
    unsupported = select_favorites_record_target(snapshot(), 3, document_index=0).record
    with pytest.raises(ValueError):
        plan_favorites_external_refresh_record_import(
            plan.refresh_result, plan.selected_preview, plan.anchor, unsupported, plan.bindings
        )
    mismatch = replace(plan.template, content=plan.template.content.replace(
        b"Synthetic Channel", b"Different Channel"
    ))
    with pytest.raises(ValueError):
        plan_favorites_external_refresh_record_import(
            plan.refresh_result, plan.selected_preview, plan.anchor, mismatch, plan.bindings
        )
    invalid = replace(plan.template, content=b"C-Freq\ttoo-short")
    with pytest.raises(ValueError):
        plan_favorites_external_refresh_record_import(
            plan.refresh_result, plan.selected_preview, plan.anchor, invalid, plan.bindings
        )


def test_public_import_exports() -> None:
    assert "FavoritesExternalRefreshRecordImportPlan" in sds200.__all__
    assert "plan_favorites_external_refresh_record_import" in sds200.__all__
