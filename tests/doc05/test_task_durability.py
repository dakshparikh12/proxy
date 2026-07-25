"""Doc 05 · workroom.task-durability — restart-unless-deliverable, on the REAL DB.

Node DoD (build/chain.json ``workroom.task-durability``, §3.1 + CANONICAL §12.10):

    A recycled orchestrator re-runs a ``workroom:<id>`` unit UNLESS a SQL
    completion check shows ``result_ref`` already holds the deliverable; the
    check is over the SAME ``operation_runs`` row, never a bespoke table. NOT
    done if a ``workroom_tasks`` table exists, if recovery trusts a *done flag*
    over the SQL check, or if a completed task is re-run.

A Workroom task IS an ``operation_runs`` row (``operation_type='workroom:<id>'``,
``progress`` jsonb = the bundle, ``result_ref`` = the terminal Envelope = the
outbox). Recovery (:mod:`workroom.recovery`) decides re-run PURELY from that row:

  * ``should_restart(conn, op_id)`` — the SYNC (psycopg ``%s``) completion check
    over ``operation_runs.result_ref``: ``False`` iff the deliverable is present.
  * ``recover_task(db, scope_id, op_type)`` — the ASYNC (asyncpg ``$1``) recovery
    entrypoint: reads the latest ``workroom:<id>`` row for the scope and returns
    ``RecoverResult(restarted=<not deliverable>)``.

These exercise the REAL functions on the REAL local Postgres — insert a real
``operation_runs`` row keyed ``workroom:<id>``, run the real reaper to simulate a
recycle (crash → the row goes ``interrupted``), then run the REAL recovery and
assert restart-unless-deliverable. They SKIP cleanly when no local PG is set,
mirroring ``test_session_per_task.py``'s real-DB block.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import Any

import pytest

from workroom.recovery import (
    RecoverResult,
    _has_deliverable,
    recover_task,
    should_restart,
)


# ── real-DB helpers (mirror test_session_per_task.py / test_reconcile) ────────
def _local_dsn() -> str | None:
    for var in ("TEST_DATABASE_URL", "DATABASE_URL"):
        dsn = os.environ.get(var, "").strip()
        if dsn:
            return dsn
    return None


def _require_dsn() -> str:
    dsn = _local_dsn()
    if dsn is None:
        pytest.skip("no local Postgres (set TEST_DATABASE_URL)")
    return dsn


def _psycopg_conn() -> Any:
    """A real autocommit psycopg3 connection — the SYNC shape ``should_restart``
    speaks (``conn.execute('... %s ...', (v,)).fetchone()``)."""
    import psycopg

    return psycopg.connect(_require_dsn(), autocommit=True)


def _envelope_result_ref(task_id: uuid.UUID) -> dict[str, Any]:
    """The terminal-Envelope outbox shape that lives in ``result_ref`` — its
    presence (a non-empty ``deliverable``) is what marks the task done (§12.10)."""
    return {
        "task_id": str(task_id),
        "status": "done",
        "deliverable": {"headline": "done", "artifact": "gs://bucket/report.md"},
    }


# ── _has_deliverable: the pure completion predicate (both shapes of result_ref) ─
def test_has_deliverable_is_true_only_when_result_ref_holds_a_deliverable() -> None:
    """The predicate that decides done: a non-empty ``deliverable`` present.

    Handles the two shapes ``result_ref`` can arrive as (risk in the node card):
    a dict (asyncpg jsonb decode) OR a JSON string (psycopg text/round-trip)."""
    tid = uuid.uuid4()
    ref = _envelope_result_ref(tid)
    # dict shape (asyncpg-decoded jsonb)
    assert _has_deliverable(ref) is True
    # JSON-string shape (must be parsed, not treated as opaque)
    assert _has_deliverable(json.dumps(ref)) is True
    # absent / empty → not done
    assert _has_deliverable(None) is False
    assert _has_deliverable({}) is False
    assert _has_deliverable({"status": "done"}) is False  # NO deliverable key
    assert _has_deliverable(json.dumps({"status": "done"})) is False
    assert _has_deliverable("not json at all") is False


# ── DoD negative: recovery trusts the SQL result_ref, NEVER a `done` flag ──────
def test_recovery_ignores_a_done_flag_and_trusts_result_ref() -> None:
    """DoD (negative): a row that *claims* ``status='completed'`` / ``done:true``
    but whose ``result_ref`` holds NO deliverable is RE-RUN — recovery must not
    trust a done flag over the SQL completion check."""
    # A done-flag-only shape with no deliverable is NOT complete.
    assert _has_deliverable({"done": True, "status": "completed"}) is False
    assert _has_deliverable(json.dumps({"done": True})) is False


# ── should_restart (SYNC path) over the REAL operation_runs row ───────────────
@pytest.mark.integration
def test_should_restart_reads_result_ref_over_the_same_operation_runs_row() -> None:
    """``should_restart(conn, op_id)`` on the REAL DB: False iff the SAME
    ``operation_runs`` row's ``result_ref`` already holds the deliverable; True
    when it is absent — the completion check over the one row, no bespoke table."""
    _require_dsn()
    conn = _psycopg_conn()
    try:
        task_id = uuid.uuid4()
        op_type = f"workroom:{task_id}"
        # An interrupted workroom:<id> row with NO deliverable (crashed mid-task).
        row = conn.execute(
            "INSERT INTO operation_runs (scope_id, operation_type, status, result_ref) "
            "VALUES (%s, %s, 'interrupted', NULL) RETURNING id",
            (str(task_id), op_type),
        ).fetchone()
        op_id = row[0]
        # No deliverable yet → the coarse unit MUST be re-run.
        assert should_restart(conn, op_id) is True

        # Now the terminal Envelope lands in the SAME row's result_ref.
        conn.execute(
            "UPDATE operation_runs SET result_ref = %s, status = 'completed' WHERE id = %s",
            (json.dumps(_envelope_result_ref(task_id)), op_id),
        )
        # The deliverable is present → NOT re-run (idempotent completion).
        assert should_restart(conn, op_id) is False

        # A missing row is treated as "no deliverable" → restart (fail-safe).
        assert should_restart(conn, uuid.uuid4()) is True
    finally:
        with conn:
            conn.execute(
                "DELETE FROM operation_runs WHERE operation_type = %s",
                (f"workroom:{task_id}",),
            )
        conn.close()


# ── recover_task (ASYNC path): the full recycle-then-recover on REAL PG ───────
@pytest.mark.integration
def test_recover_task_restarts_the_interrupted_unit_when_no_deliverable() -> None:
    """The FULL recycle-then-recover path on the REAL DB, NO deliverable case:

      1. dispatch persists a running ``workroom:<id>`` row (progress = bundle),
      2. the orchestrator is RECYCLED → the real reaper sweeps the stale row to
         ``interrupted`` (the crash),
      3. the REAL ``recover_task`` reads the SAME row and returns
         ``restarted=True`` — the coarse unit is re-run because ``result_ref``
         holds no deliverable.
    """
    dsn = _require_dsn()

    async def _run() -> RecoverResult:
        from libs.db import Database

        db = await Database.connect(dsn)
        try:
            task_id = uuid.uuid4()
            op_type = f"workroom:{task_id}"
            bundle = {"ask": "build the rate limiter", "notes_ref": str(uuid.uuid4())}
            async with db.acquire() as conn:
                await conn.execute(
                    "DELETE FROM operation_runs WHERE operation_type = $1", op_type
                )
                # dispatch: a running task row (progress = the bundle), result_ref NULL.
                await conn.execute(
                    "INSERT INTO operation_runs "
                    "(scope_id, operation_type, status, progress, last_heartbeat_at) "
                    "VALUES ($1, $2, 'running', $3, now() - interval '10 minutes')",
                    str(task_id),
                    op_type,
                    json.dumps(bundle),
                )
            # RECYCLE: the real boot sweep flips the stale running row → interrupted.
            swept = await db.sweep_stale_operation_runs()
            assert swept >= 1, "the reaper must sweep the stale workroom row to interrupted"
            async with db.acquire() as conn:
                status = await conn.fetchval(
                    "SELECT status FROM operation_runs WHERE operation_type = $1", op_type
                )
                assert status == "interrupted", "the crashed unit must be interrupted post-recycle"
            try:
                return await recover_task(db, str(task_id), op_type)
            finally:
                async with db.acquire() as conn:
                    await conn.execute(
                        "DELETE FROM operation_runs WHERE operation_type = $1", op_type
                    )
        finally:
            await db.close()

    result = asyncio.run(_run())
    assert result.restarted is True, (
        "a recycled unit whose result_ref holds no deliverable MUST be re-run"
    )


@pytest.mark.integration
def test_recover_task_does_not_rerun_when_result_ref_holds_the_deliverable() -> None:
    """The FULL recycle-then-recover path, DELIVERABLE-PRESENT case: a completed
    ``workroom:<id>`` row whose ``result_ref`` already holds the terminal Envelope
    is NOT re-run — ``recover_task`` returns ``restarted=False`` (idempotent
    completion over the SAME row; a completed task is never re-run)."""
    dsn = _require_dsn()

    async def _run() -> dict[str, Any]:
        from libs.db import Database

        db = await Database.connect(dsn)
        try:
            task_id = uuid.uuid4()
            op_type = f"workroom:{task_id}"
            deliverable = _envelope_result_ref(task_id)
            async with db.acquire() as conn:
                await conn.execute(
                    "DELETE FROM operation_runs WHERE operation_type = $1", op_type
                )
                # A completed row: the terminal Envelope is already in result_ref.
                await conn.execute(
                    "INSERT INTO operation_runs "
                    "(scope_id, operation_type, status, result_ref, completed_at) "
                    "VALUES ($1, $2, 'completed', $3, now())",
                    str(task_id),
                    op_type,
                    json.dumps(deliverable),
                )
            try:
                result = await recover_task(db, str(task_id), op_type)
                # Prove the decision came from the SAME operation_runs row (no other table).
                async with db.acquire() as conn:
                    n = await conn.fetchval(
                        "SELECT count(*) FROM operation_runs WHERE operation_type = $1",
                        op_type,
                    )
                return {"restarted": result.restarted, "rowcount": n}
            finally:
                async with db.acquire() as conn:
                    await conn.execute(
                        "DELETE FROM operation_runs WHERE operation_type = $1", op_type
                    )
        finally:
            await db.close()

    out = asyncio.run(_run())
    assert out["restarted"] is False, (
        "a task whose result_ref already holds the deliverable must NOT be re-run"
    )
    assert out["rowcount"] == 1, "the completion check is over the ONE operation_runs row"


@pytest.mark.integration
def test_recover_task_reads_the_latest_run_for_the_scope() -> None:
    """When a scope has re-run (an earlier interrupted attempt + a later completed
    one under the same ``workroom:<id>`` key), ``recover_task`` reads the LATEST
    row (``ORDER BY started_at DESC``) — so a completed retry is seen as done and
    is NOT re-run again, even though an older no-deliverable attempt exists."""
    dsn = _require_dsn()

    async def _run() -> bool:
        from libs.db import Database

        db = await Database.connect(dsn)
        try:
            task_id = uuid.uuid4()
            op_type = f"workroom:{task_id}"
            async with db.acquire() as conn:
                await conn.execute(
                    "DELETE FROM operation_runs WHERE operation_type = $1", op_type
                )
                # older attempt: interrupted, no deliverable
                await conn.execute(
                    "INSERT INTO operation_runs "
                    "(scope_id, operation_type, status, result_ref, started_at) "
                    "VALUES ($1, $2, 'interrupted', NULL, now() - interval '2 minutes')",
                    str(task_id),
                    op_type,
                )
                # later attempt: completed, deliverable present
                await conn.execute(
                    "INSERT INTO operation_runs "
                    "(scope_id, operation_type, status, result_ref, started_at) "
                    "VALUES ($1, $2, 'completed', $3, now())",
                    str(task_id),
                    op_type,
                    json.dumps(_envelope_result_ref(task_id)),
                )
            try:
                result = await recover_task(db, str(task_id), op_type)
                return result.restarted
            finally:
                async with db.acquire() as conn:
                    await conn.execute(
                        "DELETE FROM operation_runs WHERE operation_type = $1", op_type
                    )
        finally:
            await db.close()

    restarted = asyncio.run(_run())
    assert restarted is False, (
        "the LATEST run for the scope holds the deliverable → the task must NOT be re-run"
    )
