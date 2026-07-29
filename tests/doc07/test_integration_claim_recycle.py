"""AC-PME-10-NEG's two store-backed arms, against real Postgres.

Written BEFORE the §112 wrapper, per the standing rule in CLAUDE.md. Both arms were
previously "unit-verified" over a fake that had no partial unique index and no notion of a
crashed owner, so neither could have failed for the right reason:

* **the atomic claim under real concurrency** — the partial unique index
  ``operation_runs_one_running_per_scope`` on ``(scope_id, operation_type) WHERE
  status='running'`` is what excludes the loser. Not an application lock, and not a
  simulated race: two real connections race the same INSERT.
* **reclaim after a worker dies** — a ``running`` row whose ``last_heartbeat_at`` has gone
  stale is swept to ``interrupted`` so the index stops blocking a replacement, and the
  replacement then claims successfully.

Per amendment P10 the run is keyed ``scope_id`` = meeting id and ``operation_type`` =
``workroom:{task_id}``, so these tests use that shape — a claim keyed any other way would
not collide the way the index intends.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
import pytest_asyncio

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]




@pytest_asyncio.fixture()
async def pool(dsn):
    asyncpg = pytest.importorskip("asyncpg")
    p = await asyncpg.create_pool(dsn, min_size=2, max_size=6)
    try:
        yield p
    finally:
        await p.close()


@pytest_asyncio.fixture()
async def scope(pool):
    """A fresh (meeting_id, task_id) pair; every run row for it is cleaned up after."""
    meeting_id, task_id = uuid.uuid4(), uuid.uuid4()
    yield str(meeting_id), str(task_id)
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM operation_runs WHERE scope_id = $1", str(meeting_id)
        )


_CLAIM = (
    "INSERT INTO operation_runs (scope_id, operation_type, status, created_by) "
    "VALUES ($1, $2, 'running', $3) "
    "ON CONFLICT (scope_id, operation_type) WHERE status = 'running' DO NOTHING "
    "RETURNING id"
)


async def _claim(pool, scope_id: str, op_type: str, owner: str):
    """One real atomic claim on its own connection. Returns the run id or None."""
    async with pool.acquire() as conn:
        return await conn.fetchval(_CLAIM, scope_id, op_type, owner)


# ── arm 1 · the atomic claim under REAL concurrency ───────────────────────
@pytest.mark.negative
async def test_ac_pme_10_neg_concurrent_claims_yield_exactly_one_winner(pool, scope):
    meeting_id, task_id = scope
    op_type = f"workroom:{task_id}"

    results = await asyncio.gather(
        *(_claim(pool, meeting_id, op_type, f"worker-{i}") for i in range(8))
    )
    winners = [r for r in results if r is not None]

    assert len(winners) == 1, f"{len(winners)} claims won the same task"
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, status FROM operation_runs "
            " WHERE scope_id = $1 AND operation_type = $2",
            meeting_id, op_type,
        )
    assert len(rows) == 1, "a losing claim still inserted a row"
    assert rows[0]["id"] == winners[0]
    assert rows[0]["status"] == "running"


@pytest.mark.negative
async def test_ac_pme_10_neg_the_partial_index_is_what_excludes(pool, scope):
    """Proven by dropping to raw SQL with no application coordination at all."""
    meeting_id, task_id = scope
    op_type = f"workroom:{task_id}"

    async with pool.acquire() as conn:
        idx = await conn.fetchval(
            "SELECT indexdef FROM pg_indexes "
            " WHERE indexname = 'operation_runs_one_running_per_scope'"
        )
    assert idx is not None, "the partial unique index is missing"
    assert "scope_id" in idx and "operation_type" in idx
    assert "status = 'running'" in idx.replace('"', "'")

    first = await _claim(pool, meeting_id, op_type, "a")
    second = await _claim(pool, meeting_id, op_type, "b")
    assert first is not None and second is None


async def test_two_different_tasks_in_one_meeting_both_claim(pool, scope):
    """The key is (meeting, task) — a second task must NOT be blocked by the first."""
    meeting_id, task_id = scope
    other_task = uuid.uuid4()

    a = await _claim(pool, meeting_id, f"workroom:{task_id}", "a")
    b = await _claim(pool, meeting_id, f"workroom:{other_task}", "b")
    assert a is not None and b is not None and a != b


# ── arm 2 · reclaim after the worker dies ─────────────────────────────────
@pytest.mark.negative
async def test_ac_pme_10_neg_a_stale_run_is_reclaimed_after_worker_death(pool, scope):
    """Kill the owner (stop heartbeating), sweep, and prove a replacement can claim.

    The death is simulated by ageing ``last_heartbeat_at`` past the staleness threshold —
    which is exactly what a killed process looks like to the substrate, since a dead
    worker's only observable trait is that it stopped heartbeating.
    """
    from libs.db.src.db.config import stale_after_s

    meeting_id, task_id = scope
    op_type = f"workroom:{task_id}"

    original = await _claim(pool, meeting_id, op_type, "worker-1")
    assert original is not None

    # A live owner still blocks a replacement.
    assert await _claim(pool, meeting_id, op_type, "worker-2") is None

    # The worker dies: its heartbeat stops and ages out.
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE operation_runs "
            "   SET last_heartbeat_at = now() - make_interval(secs => $2) "
            " WHERE id = $1",
            original, float(stale_after_s()) + 60,
        )

    # The reaper flips the orphan so the index stops blocking.
    async with pool.acquire() as conn:
        swept = await conn.execute(
            "UPDATE operation_runs SET status = 'interrupted' "
            " WHERE status = 'running' AND scope_id = $1 "
            "   AND last_heartbeat_at < now() - make_interval(secs => $2)",
            meeting_id, float(stale_after_s()),
        )
    assert swept.endswith("1"), f"the stale run was not swept: {swept!r}"

    # A replacement now claims — and there is still exactly ONE running row.
    replacement = await _claim(pool, meeting_id, op_type, "worker-2")
    assert replacement is not None and replacement != original

    async with pool.acquire() as conn:
        running = await conn.fetch(
            "SELECT id FROM operation_runs "
            " WHERE scope_id = $1 AND operation_type = $2 AND status = 'running'",
            meeting_id, op_type,
        )
        interrupted = await conn.fetchval(
            "SELECT status FROM operation_runs WHERE id = $1", original
        )
    assert [r["id"] for r in running] == [replacement]
    assert interrupted == "interrupted", "the dead run was not left as interrupted"


@pytest.mark.negative
async def test_a_live_run_is_never_swept(pool, scope):
    """The reaper must not reap a worker that is still heartbeating."""
    from libs.db.src.db.config import stale_after_s

    meeting_id, task_id = scope
    op_type = f"workroom:{task_id}"
    run = await _claim(pool, meeting_id, op_type, "worker-1")

    async with pool.acquire() as conn:
        swept = await conn.execute(
            "UPDATE operation_runs SET status = 'interrupted' "
            " WHERE status = 'running' AND scope_id = $1 "
            "   AND last_heartbeat_at < now() - make_interval(secs => $2)",
            meeting_id, float(stale_after_s()),
        )
        status = await conn.fetchval(
            "SELECT status FROM operation_runs WHERE id = $1", run
        )
    assert swept.endswith("0"), "a live run was swept"
    assert status == "running"


async def test_reclaim_is_idempotent(pool, scope):
    """Two consecutive sweeps leave the same state — the reaper is re-runnable."""
    from libs.db.src.db.config import stale_after_s

    meeting_id, task_id = scope
    op_type = f"workroom:{task_id}"
    run = await _claim(pool, meeting_id, op_type, "worker-1")

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE operation_runs SET last_heartbeat_at = now() - make_interval(secs => $2) "
            " WHERE id = $1",
            run, float(stale_after_s()) + 60,
        )
        first = await conn.execute(
            "UPDATE operation_runs SET status='interrupted' WHERE status='running' "
            " AND scope_id=$1 AND last_heartbeat_at < now() - make_interval(secs => $2)",
            meeting_id, float(stale_after_s()),
        )
        second = await conn.execute(
            "UPDATE operation_runs SET status='interrupted' WHERE status='running' "
            " AND scope_id=$1 AND last_heartbeat_at < now() - make_interval(secs => $2)",
            meeting_id, float(stale_after_s()),
        )
    assert first.endswith("1") and second.endswith("0")
