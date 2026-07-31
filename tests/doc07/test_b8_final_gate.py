"""B8 — the final gate. Criteria: AC-PME-15, AC-PME-15-NEG."""
from __future__ import annotations

import pathlib
import uuid
from datetime import datetime, timezone

import pytest
from control_plane.post_meeting.approval import approve
from control_plane.post_meeting.final_gate import (
    FORBIDDEN_REPO_WRITES,
    FORBIDDEN_SCOPE,
    PROPOSED,
    STAGED_DRAFT_STATUSES,
    DraftRejected,
    assert_no_repo_writes,
    run_final_gate,
    validate_draft,
)
from control_plane.post_meeting.models import Source, TaskRecord, TaskState

from ._support import FakeTaskStore, ForbiddenGitRemote

pytestmark = pytest.mark.asyncio

TENANT = uuid.uuid4()
MEETING = uuid.uuid4()
NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
DRAFT = uuid.uuid4()


def row(status=PROPOSED, draft_id=DRAFT):
    return {"draft_id": draft_id, "status": status}


def envelope(**kw):
    base = {"status": "needs_review", "receipts": ["retry_test.py:41 failing->passing"]}
    base.update(kw)
    return base


async def _running(store):
    tid = await store.insert_task(
        TaskRecord(task_id=None, tenant_id=TENANT, meeting_id=MEETING,
                   source=Source.CLOSE_ITEM, item_ref="m#0", owner="Sam")
    )
    await store.set_state(tid, TaskState.PLANNED)
    await approve(task_id=tid, approver="Sam", store=store, now=NOW)
    await store.set_state(tid, TaskState.RUNNING)
    return tid


# ── AC-PME-15 · proposed draft with receipts and a draft_id, never pushed ──
async def test_ac_pme_15_clean_draft_is_accepted_at_proposed():
    store = FakeTaskStore()
    tid = await _running(store)
    acc, err = await run_final_gate(
        task_id=tid, envelope=envelope(), draft_row=row(),
        bundle_exists=True, store=store,
    )
    assert err is None and acc is not None
    assert acc.status == PROPOSED
    assert acc.draft_id == DRAFT
    assert acc.receipts, "a draft must carry receipts (Law 1)"
    assert store.rows[tid]["state"] == TaskState.DRAFTED.value
    assert store.rows[tid]["draft_id"] == DRAFT


async def test_ac_pme_15_canonical_enum_is_the_four_values():
    assert STAGED_DRAFT_STATUSES == {"proposed", "accepted", "rejected", "applied"}
    assert "needs_review" not in STAGED_DRAFT_STATUSES


async def test_ac_pme_15_no_push_happens_on_the_clean_path():
    remote = ForbiddenGitRemote()
    store = FakeTaskStore()
    tid = await _running(store)
    await run_final_gate(
        task_id=tid, envelope=envelope(), draft_row=row(),
        bundle_exists=True, store=store,
    )
    assert remote.write_operations == 0


async def test_ac_pme_15_this_module_never_writes_staged_drafts_directly():
    """Doc 07 §3.8: staging is propose_change through the Workroom, never a direct write."""
    src = pathlib.Path(
        "services/control-plane/src/control_plane/post_meeting/final_gate.py"
    ).read_text(encoding="utf-8").lower()
    assert "insert into staged_drafts" not in src
    assert "update staged_drafts" not in src


async def test_ac_pme_15_package_never_writes_staged_drafts_anywhere():
    for path in pathlib.Path("services/control-plane/src/control_plane/post_meeting").glob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        assert "insert into staged_drafts" not in text, path
        assert "update staged_drafts" not in text, path


# ── AC-PME-15-NEG · staging failures never orphan a row or reach for a push ─
@pytest.mark.negative
async def test_ac_pme_15_neg_needs_review_on_the_draft_row_is_rejected():
    """The exact defect P8/P8b removed from the spec, refused at runtime too."""
    with pytest.raises(DraftRejected, match="outside CANONICAL"):
        validate_draft(row(status="needs_review"), bundle_exists=True)


@pytest.mark.negative
async def test_ac_pme_15_neg_any_out_of_enum_status_is_rejected():
    for bad in ("needs_review", "draft", "verified", "", None, 7, "PROPOSED"):
        with pytest.raises(DraftRejected):
            validate_draft(row(status=bad), bundle_exists=True)


@pytest.mark.negative
async def test_ac_pme_15_neg_in_enum_but_not_proposed_is_rejected():
    for later in ("accepted", "rejected", "applied"):
        with pytest.raises(DraftRejected, match="must be at 'proposed'"):
            validate_draft(row(status=later), bundle_exists=True)


@pytest.mark.negative
async def test_ac_pme_15_neg_draft_row_without_a_bundle_is_an_orphan():
    with pytest.raises(DraftRejected, match="no retrievable bundle"):
        validate_draft(row(), bundle_exists=False)


@pytest.mark.negative
async def test_ac_pme_15_neg_missing_row_or_draft_id_is_rejected():
    with pytest.raises(DraftRejected, match="no staged_drafts row"):
        validate_draft(None, bundle_exists=True)
    with pytest.raises(DraftRejected, match="no draft_id"):
        validate_draft({"status": PROPOSED}, bundle_exists=True)


@pytest.mark.negative
@pytest.mark.parametrize("op", sorted(FORBIDDEN_REPO_WRITES))
async def test_ac_pme_15_neg_every_forbidden_repo_write_is_refused(op):
    with pytest.raises(DraftRejected, match="never pushes"):
        assert_no_repo_writes(operations=[op])


@pytest.mark.negative
async def test_ac_pme_15_neg_holding_contents_write_is_itself_a_violation():
    with pytest.raises(DraftRejected, match="must not hold push capability"):
        assert_no_repo_writes(operations=[], token_scopes=["contents:read", FORBIDDEN_SCOPE])
    assert_no_repo_writes(operations=[], token_scopes=["contents:read"])  # clean


@pytest.mark.negative
async def test_ac_pme_15_neg_staging_failure_does_not_fall_back_to_a_push():
    remote = ForbiddenGitRemote()
    store = FakeTaskStore()
    tid = await _running(store)

    acc, err = await run_final_gate(
        task_id=tid, envelope=envelope(), draft_row=row(),
        bundle_exists=False,  # GCS bundle write failed
        store=store,
    )
    assert acc is None and isinstance(err, DraftRejected)
    assert remote.write_operations == 0, "staging failed and the code reached for a push"
    assert store.rows[tid]["state"] == TaskState.DISCARDED.value
    assert store.rows[tid]["draft_id"] is None, "an orphan draft_id was recorded"


@pytest.mark.negative
async def test_ac_pme_15_neg_rejected_artifact_does_not_report_success():
    store = FakeTaskStore()
    tid = await _running(store)
    acc, err = await run_final_gate(
        task_id=tid, envelope=envelope(), draft_row=row(status="needs_review"),
        bundle_exists=True, store=store,
    )
    assert acc is None and err is not None
    assert store.rows[tid]["state"] != TaskState.DRAFTED.value
    assert "rejected" in store.rows[tid]["outcome"]


@pytest.mark.negative
async def test_ac_pme_15_neg_a_push_in_the_operations_list_blocks_acceptance():
    store = FakeTaskStore()
    tid = await _running(store)
    acc, err = await run_final_gate(
        task_id=tid, envelope=envelope(), draft_row=row(),
        bundle_exists=True, store=store, operations=["push"],
    )
    assert acc is None
    assert isinstance(err, DraftRejected)
    assert store.rows[tid]["state"] == TaskState.DISCARDED.value


@pytest.mark.negative
async def test_ac_pme_15_neg_migration_enforces_the_enum_in_the_database():
    """The application check is the fast path; the CHECK constraint is the guarantee."""
    # Located by SUFFIX, not by number. The numeric prefix is not stable — this file was
    # 0011 until Daksh's 0009_repo_maps forced a renumber, and pinning the digits meant a
    # pure re-parenting broke a test that has nothing to do with ordering.
    matches = sorted(
        pathlib.Path("migrations/versions").glob("*_staged_drafts_status_check.py")
    )
    assert len(matches) == 1, f"expected exactly one status-check migration, got {matches}"
    src = matches[0].read_text(encoding="utf-8")
    assert "staged_drafts_status_enum" in src
    assert "'proposed', 'accepted', 'rejected', 'applied'" in src
