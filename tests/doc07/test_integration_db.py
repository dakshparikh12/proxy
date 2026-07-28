"""Integration rung — the DATABASE rejects the writes, not just the application.

Criteria: **AC-PME-07-NEG** (the approval gate holds when the store errors or returns
ambiguous state) and **AC-PME-15-NEG** (a staging failure never leaves a bad draft row),
plus the ``operation_ref`` foreign key those two depend on.

Why this module exists. The unit rung uses ``FakeTaskStore``, which re-implements
migration 0009's CHECK and trigger in Python. That proves the *application* never attempts
an illegal write — a real property, but not the one the criteria state. AC-PME-07-NEG says
the database rejects an ``APPROVED`` row with a null approver *"independently of
application code"*, and AC-PME-15-NEG says an out-of-enum draft status is rejected by the
database. A fake cannot establish either: it is application code.

So every assertion here runs the REAL ``PostMeetingTaskStore`` SQL against a REAL
Postgres, and the expected outcome is a ``psycopg`` integrity error raised by Postgres
itself — identified by constraint name, so a test cannot pass because some *other* error
happened to fire.

Marked ``integration``: it skips cleanly when no database is reachable, per the sealed
bundle's ``db:postgres`` mock_boundary ("real Postgres only; no in-memory substitute for
the integration tier").
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from harness.post_meeting.approval import approve
from harness.post_meeting.models import Source, TaskRecord, TaskState, Tier
from harness.post_meeting.store import ClarifyItemStore, PostMeetingTaskStore

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)


def _dsn() -> str | None:
    for var in ("TEST_DATABASE_URL", "DATABASE_URL"):
        dsn = os.environ.get(var, "").strip()
        if dsn:
            return dsn
    return None


class _Pool:
    """Minimal ``libs.db.Database``-shaped adapter: ``.acquire()`` yields a connection.

    The store is written against that surface, so wrapping an asyncpg pool here means the
    tests drive the store's REAL SQL — placeholders, casts and all — rather than a
    paraphrase of it.
    """

    def __init__(self, pool):
        self._pool = pool

    def acquire(self):
        return self._pool.acquire()


@pytest.fixture(scope="module")
def dsn():
    d = _dsn()
    if not d:
        pytest.skip("no local Postgres (set TEST_DATABASE_URL or DATABASE_URL)")
    return d


@pytest_asyncio.fixture()
async def db(dsn):
    asyncpg = pytest.importorskip("asyncpg")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
    try:
        yield _Pool(pool)
    finally:
        await pool.close()


@pytest_asyncio.fixture()
async def seed(db):
    """One tenant + one meeting, cleaned up after. Returns (tenant_id, meeting_id)."""
    async with db.acquire() as conn:
        tenant_id = await conn.fetchval(
            "INSERT INTO tenants (name) VALUES ('doc07-it') RETURNING id"
        )
        meeting_id = await conn.fetchval(
            "INSERT INTO meetings (tenant_id, status) VALUES ($1, 'ended') RETURNING id",
            tenant_id,
        )
    yield tenant_id, meeting_id
    async with db.acquire() as conn:
        await conn.execute("DELETE FROM post_meeting_tasks WHERE tenant_id = $1", tenant_id)
        await conn.execute("DELETE FROM clarify_items WHERE tenant_id = $1", tenant_id)
        await conn.execute("DELETE FROM meetings WHERE tenant_id = $1", tenant_id)
        await conn.execute("DELETE FROM tenants WHERE id = $1", tenant_id)


async def _task(store, tenant_id, meeting_id, *, tier=Tier.TICKET_PLAN_DRAFT):
    tid = await store.insert_task(
        TaskRecord(task_id=None, tenant_id=tenant_id, meeting_id=meeting_id,
                   source=Source.CLOSE_ITEM, item_ref="m#0", owner="Sam")
    )
    if tier is not None:
        await store.set_tier(tid, tier, state=TaskState.TRIAGED)
    return tid


# ── the schema is really there ────────────────────────────────────────────
async def test_migrations_produced_the_doc07_schema(db):
    async with db.acquire() as conn:
        tables = {
            r["tablename"]
            for r in await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname='public'"
            )
        }
    assert {"post_meeting_tasks", "clarify_items"} <= tables


# ── AC-PME-07-NEG · the DATABASE refuses, independently of application code ─
@pytest.mark.negative
async def test_ac_pme_07_neg_db_rejects_approved_without_an_approver(db, seed):
    """CHECK post_meeting_tasks_approved_needs_approver, enforced by Postgres."""
    asyncpg = pytest.importorskip("asyncpg")
    tenant_id, meeting_id = seed
    store = PostMeetingTaskStore(db)
    tid = await _task(store, tenant_id, meeting_id)

    # Bypass the application entirely: raw SQL straight at the table.
    with pytest.raises(asyncpg.exceptions.CheckViolationError) as ei:
        async with db.acquire() as conn:
            await conn.execute(
                "UPDATE post_meeting_tasks SET state='APPROVED' WHERE task_id=$1", tid
            )
    assert ei.value.constraint_name == "post_meeting_tasks_approved_needs_approver"

    for col in ("approved_by", "approved_at"):
        other = "approved_at" if col == "approved_by" else "approved_by"
        val = "'Sam'" if col == "approved_by" else "now()"
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            async with db.acquire() as conn:
                await conn.execute(
                    f"UPDATE post_meeting_tasks SET state='APPROVED', {col}={val}, "
                    f"{other}=NULL WHERE task_id=$1",
                    tid,
                )

    async with db.acquire() as conn:
        state = await conn.fetchval(
            "SELECT state FROM post_meeting_tasks WHERE task_id=$1", tid
        )
    assert state == TaskState.TRIAGED.value, "a rejected write still changed the row"


@pytest.mark.negative
async def test_ac_pme_07_neg_db_rejects_running_not_from_approved(db, seed):
    """TRIGGER post_meeting_tasks_running_gate, UPDATE arm."""
    asyncpg = pytest.importorskip("asyncpg")
    tenant_id, meeting_id = seed
    store = PostMeetingTaskStore(db)

    for state in (TaskState.EXTRACTED, TaskState.TRIAGED, TaskState.CLARIFYING,
                  TaskState.PLANNED, TaskState.DISCARDED):
        tid = await _task(store, tenant_id, meeting_id)
        await store.set_state(tid, state)
        with pytest.raises(asyncpg.exceptions.RaiseError, match="RUNNING may only be"):
            await store.set_state(tid, TaskState.RUNNING)
        async with db.acquire() as conn:
            got = await conn.fetchval(
                "SELECT state FROM post_meeting_tasks WHERE task_id=$1", tid
            )
        assert got == state.value


@pytest.mark.negative
async def test_ac_pme_07_neg_db_rejects_a_row_born_running(db, seed):
    """TRIGGER post_meeting_tasks_running_gate, INSERT arm.

    A BEFORE UPDATE trigger alone would miss this — INSERT ... state='RUNNING' enters
    RUNNING having never been APPROVED.
    """
    asyncpg = pytest.importorskip("asyncpg")
    tenant_id, meeting_id = seed
    with pytest.raises(asyncpg.exceptions.RaiseError, match="cannot INSERT a row directly"):
        async with db.acquire() as conn:
            await conn.execute(
                "INSERT INTO post_meeting_tasks (tenant_id, meeting_id, source, item_ref, "
                "state) VALUES ($1,$2,'close-item','m#0','RUNNING')",
                tenant_id, meeting_id,
            )


async def test_ac_pme_07_a_real_approval_then_running_is_accepted(db, seed):
    """The happy path must still work — a guard that blocks everything proves nothing."""
    tenant_id, meeting_id = seed
    store = PostMeetingTaskStore(db)
    tid = await _task(store, tenant_id, meeting_id)
    await store.set_state(tid, TaskState.PLANNED)
    await approve(task_id=tid, approver="Sam", store=store, now=NOW)
    await store.set_state(tid, TaskState.RUNNING)

    row = await store.get(tid)
    assert row["state"] == TaskState.RUNNING.value
    assert row["approved_by"] == "Sam" and row["approved_at"] is not None


@pytest.mark.negative
async def test_ac_pme_07_neg_updates_to_an_already_running_row_still_work(db, seed):
    """The trigger gates the TRANSITION, not every write to a running row."""
    tenant_id, meeting_id = seed
    store = PostMeetingTaskStore(db)
    tid = await _task(store, tenant_id, meeting_id)
    await store.set_state(tid, TaskState.PLANNED)
    await approve(task_id=tid, approver="Sam", store=store, now=NOW)
    await store.set_state(tid, TaskState.RUNNING)

    await store.set_outcome(tid, state=TaskState.RUNNING, outcome="progress", cost_usd=0.4)
    row = await store.get(tid)
    assert row["state"] == TaskState.RUNNING.value and float(row["cost_usd"]) == 0.4


# ── AC-PME-15-NEG · the DATABASE refuses a bad draft status ───────────────
@pytest.mark.negative
async def test_ac_pme_15_neg_db_rejects_out_of_enum_draft_status(db):
    """CHECK staged_drafts_status_enum (migration 0011)."""
    asyncpg = pytest.importorskip("asyncpg")
    for bad in ("needs_review", "draft", "verified", "PROPOSED", ""):
        with pytest.raises(asyncpg.exceptions.CheckViolationError) as ei:
            async with db.acquire() as conn:
                await conn.execute(
                    "INSERT INTO staged_drafts (kind, summary, status) "
                    "VALUES ('code-change','it',$1)",
                    bad,
                )
        assert ei.value.constraint_name == "staged_drafts_status_enum", bad


async def test_ac_pme_15_every_enum_value_is_accepted(db):
    """The constraint must admit all four CANONICAL §4 values, not just 'proposed'."""
    async with db.acquire() as conn:
        for good in ("proposed", "accepted", "rejected", "applied"):
            did = await conn.fetchval(
                "INSERT INTO staged_drafts (kind, summary, status) "
                "VALUES ('code-change','it',$1) RETURNING draft_id",
                good,
            )
            await conn.execute("DELETE FROM staged_drafts WHERE draft_id=$1", did)


async def test_ac_pme_15_default_status_is_proposed(db):
    async with db.acquire() as conn:
        did = await conn.fetchval(
            "INSERT INTO staged_drafts (kind, summary) VALUES ('code-change','it') "
            "RETURNING draft_id"
        )
        status = await conn.fetchval(
            "SELECT status FROM staged_drafts WHERE draft_id=$1", did
        )
        await conn.execute("DELETE FROM staged_drafts WHERE draft_id=$1", did)
    assert status == "proposed"


# ── AC-PME-10 · operation_ref really is a FK to operation_runs(id) ────────
@pytest.mark.negative
async def test_ac_pme_10_neg_db_rejects_an_operation_ref_that_is_not_a_run(db, seed):
    """This is the defect fixed in 'operation_ref is the run_id, not the task id'.

    Writing the TASK id into operation_ref is an FK violation. The unit rung could not
    catch it — both values are uuids and the fake store has no foreign keys.
    """
    asyncpg = pytest.importorskip("asyncpg")
    tenant_id, meeting_id = seed
    store = PostMeetingTaskStore(db)
    tid = await _task(store, tenant_id, meeting_id)

    with pytest.raises(asyncpg.exceptions.ForeignKeyViolationError) as ei:
        await store.set_operation_ref(tid, tid)  # the task id — the old, wrong value
    assert "operation_ref" in (ei.value.constraint_name or "")


async def test_ac_pme_10_a_real_run_id_is_accepted_as_operation_ref(db, seed):
    """And the corrected value — the operation_runs primary key — is accepted."""
    tenant_id, meeting_id = seed
    store = PostMeetingTaskStore(db)
    tid = await _task(store, tenant_id, meeting_id)

    async with db.acquire() as conn:
        run_id = await conn.fetchval(
            "INSERT INTO operation_runs (scope_id, operation_type, status) "
            "VALUES ($1, $2, 'running') RETURNING id",
            str(meeting_id), f"workroom:{tid}",
        )
    await store.set_operation_ref(tid, run_id)
    row = await store.get(tid)
    assert row["operation_ref"] == run_id

    async with db.acquire() as conn:
        await conn.execute("UPDATE post_meeting_tasks SET operation_ref=NULL WHERE task_id=$1", tid)
        await conn.execute("DELETE FROM operation_runs WHERE id=$1", run_id)


# ── AC-PME-11 · the cap query is real SQL that really filters ─────────────
async def test_ac_pme_11_meeting_cap_query_ignores_non_dispatchable_rows(db, seed):
    """The defect: 11 informational items blocked all dispatch. Proven against real SQL."""
    tenant_id, meeting_id = seed
    store = PostMeetingTaskStore(db)

    for tier in (Tier.INFORMATIONAL, Tier.QUESTION, Tier.TICKET, Tier.TICKET_PLAN):
        for _ in range(3):
            await _task(store, tenant_id, meeting_id, tier=tier)
    await _task(store, tenant_id, meeting_id, tier=None)  # untriaged

    assert await store.count_dispatchable_for_meeting(meeting_id) == 0

    d1 = await _task(store, tenant_id, meeting_id)
    assert await store.count_dispatchable_for_meeting(meeting_id) == 1
    assert await store.count_dispatchable_for_meeting(meeting_id, exclude_task_id=d1) == 0

    await store.set_outcome(d1, state=TaskState.DISCARDED, outcome="done")
    assert await store.count_dispatchable_for_meeting(meeting_id) == 0, (
        "a terminal task still held its meeting slot"
    )


# ── clarify_items is really writable, and really co-owned ────────────────
async def test_clarify_items_round_trips_against_the_real_table(db, seed):
    tenant_id, meeting_id = seed
    store = ClarifyItemStore(db)
    cid = await store.insert(
        tenant_id=tenant_id, meeting_id=meeting_id,
        question="Who is cutting over on Friday?", kind="post-meeting",
        blocking_ref="m#0", urgency="high",
    )
    pending = await store.pending_for_meeting(meeting_id)
    assert [r["clarify_id"] for r in pending] == [cid]
    assert pending[0]["blocking_ref"] == "m#0"
