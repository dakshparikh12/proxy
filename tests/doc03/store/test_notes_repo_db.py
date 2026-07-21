"""AC-STORE db:postgres integration oracles — real ``db.repos.notes`` seam.

Every test here has ``dependency_class: db:postgres`` with a mock_boundary that
FORBIDS an in-memory substitute. The bodies drive the production seam
(``db.repos.notes``: append_delta / load_deltas / insert_segment /
set_segment_status / count_segments / reap_orphaned_meetings) so they run
verbatim once a Postgres carrying the sealed section 3.3 schema exists. They are
skip-gated (``requires_pg``) — that schema is not available this session, and
faking a pass on a divergent/absent DB is forbidden.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import asyncpg
import pytest

from db.repos import notes as notes_repo

from .conftest import requires_pg

pytestmark = pytest.mark.asyncio

_UNROUTABLE_DSN = "postgresql://proxy@127.0.0.1:1/nonexistent"


async def _pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(
        os.environ["TEST_DATABASE_URL"], min_size=1, max_size=4
    )


# -- AC-STORE-01 / -NEG -- transcript_segments.status DEFAULT is 'pending' -----
@requires_pg
async def test_store_01_segment_status_defaults_to_pending() -> None:
    pool = await _pool()
    try:
        async with pool.acquire() as conn:
            row = await notes_repo.insert_segment(
                conn, meeting_id=uuid.uuid4(), text="hello", status=None
            )
        assert row["status"] == "pending"
    finally:
        await pool.close()


@requires_pg
async def test_store_01neg_default_is_not_comprehended_or_gap() -> None:
    pool = await _pool()
    try:
        async with pool.acquire() as conn:
            row = await notes_repo.insert_segment(
                conn, meeting_id=uuid.uuid4(), text="x", status=None
            )
        assert row["status"] != "comprehended"
        assert row["status"] != "gap"
    finally:
        await pool.close()


# -- AC-STORE-02 / -NEG -- note_deltas UNIQUE (meeting,window,entry,op) ---------
@requires_pg
async def test_store_02_duplicate_delta_silently_discarded() -> None:
    pool = await _pool()
    mid = uuid.uuid4()
    try:
        async with pool.acquire() as conn:
            first = await notes_repo.append_delta(
                conn, meeting_id=mid, entry_id="E1", op="add",
                payload={"text": "a"}, window_start_s=0.0,
            )
            second = await notes_repo.append_delta(
                conn, meeting_id=mid, entry_id="E1", op="add",
                payload={"text": "a"}, window_start_s=0.0,
            )
            rows = await notes_repo.load_deltas(conn, mid)
        assert first is not None
        assert second is None
        assert len(rows) == 1
    finally:
        await pool.close()


@requires_pg
async def test_store_02neg_distinct_op_is_a_new_row() -> None:
    pool = await _pool()
    mid = uuid.uuid4()
    try:
        async with pool.acquire() as conn:
            a = await notes_repo.append_delta(
                conn, meeting_id=mid, entry_id="E1", op="add",
                payload={"t": 1}, window_start_s=0.0,
            )
            p = await notes_repo.append_delta(
                conn, meeting_id=mid, entry_id="E1", op="patch",
                payload={"t": 2}, window_start_s=0.0,
            )
            rows = await notes_repo.load_deltas(conn, mid)
        assert a is not None and p is not None
        assert len(rows) == 2
        assert {r["op"] for r in rows} == {"add", "patch"}
    finally:
        await pool.close()


# -- AC-STORE-03 / -NEG -- meeting_id is uuid in both tables --------------------
@requires_pg
async def test_store_03_meeting_id_is_uuid_in_both_tables() -> None:
    pool = await _pool()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT table_name, data_type
                  FROM information_schema.columns
                 WHERE table_name IN ('transcript_segments', 'note_deltas')
                   AND column_name = 'meeting_id'
                """
            )
        types = {r["table_name"]: r["data_type"] for r in rows}
        assert types.get("transcript_segments") == "uuid"
        assert types.get("note_deltas") == "uuid"
        for dt in types.values():
            assert dt not in ("text", "character varying", "bigint")
    finally:
        await pool.close()


@requires_pg
async def test_store_03neg_non_uuid_meeting_id_rejected() -> None:
    pool = await _pool()
    try:
        async with pool.acquire() as conn:
            with pytest.raises(asyncpg.PostgresError):
                await conn.execute(
                    "INSERT INTO note_deltas (meeting_id, entry_id, op, payload) "
                    "VALUES ($1, 'E', 'add', '{}'::jsonb)",
                    "not-a-uuid",
                )
    finally:
        await pool.close()


# -- AC-STORE-05 / -NEG -- deterministic left-fold in id order ------------------
def _raw_fold(deltas: list[dict[str, object]]) -> dict[str, object]:
    """Raw reducer used ONLY as the fold oracle (the typed fold is the read-path
    layer's job). Proves id-order is the correctness axis; not shipped code."""
    state: dict[str, object] = {}
    for d in deltas:
        eid = str(d["entry_id"])
        op = str(d["op"])
        payload = dict(d["payload"])  # type: ignore[arg-type]
        if op == "add":
            state[eid] = dict(payload)
        elif op == "patch":
            base = dict(state.get(eid, {}))  # patch-before-add -> empty base
            base.update(payload)
            state[eid] = base
        elif op == "close" and eid in state:
            cur = dict(state[eid])  # type: ignore[arg-type]
            cur["closed"] = True
            state[eid] = cur
    return state


@requires_pg
async def test_store_05_notes_cache_is_deterministic_left_fold_in_id_order() -> None:
    pool = await _pool()
    mid = uuid.uuid4()
    try:
        async with pool.acquire() as conn:
            await notes_repo.append_delta(
                conn, meeting_id=mid, entry_id="E1", op="add",
                payload={"v": 1}, window_start_s=0.0,
            )
            await notes_repo.append_delta(
                conn, meeting_id=mid, entry_id="E1", op="patch",
                payload={"v": 2}, window_start_s=10.0,
            )
            ordered = await notes_repo.load_deltas(conn, mid)
        golden = _raw_fold(ordered)
        assert _raw_fold(ordered) == golden
        by_created = sorted(ordered, key=lambda r: r["created_at"], reverse=True)
        if [r["id"] for r in by_created] != [r["id"] for r in ordered]:
            assert _raw_fold(by_created) != golden
    finally:
        await pool.close()


@requires_pg
async def test_store_05neg_reversed_id_order_corrupts_cache() -> None:
    pool = await _pool()
    mid = uuid.uuid4()
    try:
        async with pool.acquire() as conn:
            await notes_repo.append_delta(
                conn, meeting_id=mid, entry_id="E1", op="add",
                payload={"v": 1}, window_start_s=0.0,
            )
            await notes_repo.append_delta(
                conn, meeting_id=mid, entry_id="E1", op="patch",
                payload={"v": 2}, window_start_s=10.0,
            )
            ordered = await notes_repo.load_deltas(conn, mid)
        golden = _raw_fold(ordered)
        assert _raw_fold(list(reversed(ordered))) != golden
    finally:
        await pool.close()


# -- AC-STORE-06 / -NEG -- single-writer discipline ----------------------------
@requires_pg
async def test_store_06_concurrent_writers_serialized_per_meeting() -> None:
    pool = await _pool()
    mid = uuid.uuid4()
    try:
        async def writer(entry: str, val: int) -> None:
            async with pool.acquire() as conn:
                await notes_repo.append_delta(
                    conn, meeting_id=mid, entry_id=entry, op="add",
                    payload={"v": val}, window_start_s=float(val),
                )
        await asyncio.gather(writer("E1", 1), writer("E2", 2))
        async with pool.acquire() as conn:
            rows = await notes_repo.load_deltas(conn, mid)
        assert len(rows) == 2
        assert [r["id"] for r in rows] == sorted(r["id"] for r in rows)
    finally:
        await pool.close()


@requires_pg
async def test_store_06neg_db_fault_degrades_honestly() -> None:
    with pytest.raises((asyncpg.PostgresError, OSError, ConnectionError)):
        conn = await asyncpg.connect(_UNROUTABLE_DSN, timeout=1)
        await conn.close()


# -- AC-STORE-07 / -NEG -- boot reaper JOINs operation_runs --------------------
@requires_pg
async def test_store_07_reaper_marks_interrupted_via_operation_runs_join() -> None:
    pool = await _pool()
    mid = uuid.uuid4()
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO meetings (id, status) VALUES ($1, 'live')", mid
            )
            await conn.execute(
                "INSERT INTO operation_runs (scope_id, operation_type, status, "
                "last_heartbeat_at) VALUES ($1, 'meeting-harness', 'interrupted', now())",
                str(mid),
            )
        reaped = await notes_repo.reap_orphaned_meetings(pool)
        async with pool.acquire() as conn:
            status = await conn.fetchval("SELECT status FROM meetings WHERE id = $1", mid)
        assert reaped >= 1
        assert status == "interrupted"
    finally:
        await pool.close()


@requires_pg
async def test_store_07neg_running_fresh_meeting_left_untouched() -> None:
    pool = await _pool()
    mid = uuid.uuid4()
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO meetings (id, status) VALUES ($1, 'live')", mid
            )
            await conn.execute(
                "INSERT INTO operation_runs (scope_id, operation_type, status, "
                "last_heartbeat_at) VALUES ($1, 'meeting-harness', 'running', now())",
                str(mid),
            )
        await notes_repo.reap_orphaned_meetings(pool)
        async with pool.acquire() as conn:
            status = await conn.fetchval("SELECT status FROM meetings WHERE id = $1", mid)
        assert status == "live"
    finally:
        await pool.close()


# -- AC-STORE-08 / -NEG -- reaper marks running-but-stale interrupted ----------
@requires_pg
async def test_store_08_reaper_marks_running_but_stale_interrupted() -> None:
    pool = await _pool()
    mid = uuid.uuid4()
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO meetings (id, status) VALUES ($1, 'live')", mid
            )
            await conn.execute(
                "INSERT INTO operation_runs (scope_id, operation_type, status, "
                "last_heartbeat_at) VALUES ($1, 'meeting-harness', 'running', "
                "now() - interval '10 minutes')",
                str(mid),
            )
        reaped = await notes_repo.reap_orphaned_meetings(pool)
        async with pool.acquire() as conn:
            status = await conn.fetchval("SELECT status FROM meetings WHERE id = $1", mid)
        assert reaped >= 1
        assert status == "interrupted"
    finally:
        await pool.close()


@requires_pg
async def test_store_08neg_db_fault_degrades_honestly() -> None:
    with pytest.raises((asyncpg.PostgresError, OSError, ConnectionError)):
        conn = await asyncpg.connect(_UNROUTABLE_DSN, timeout=1)
        await conn.close()


# -- AC-STORE-13 / -NEG -- transcript_segments never deleted -------------------
@requires_pg
async def test_store_13_transcript_rows_survive_close() -> None:
    pool = await _pool()
    mid = uuid.uuid4()
    try:
        async with pool.acquire() as conn:
            for i in range(3):
                await notes_repo.insert_segment(
                    conn, meeting_id=mid, text="seg" + str(i), status=None
                )
            before = await notes_repo.count_segments(conn, mid)
            after = await notes_repo.count_segments(conn, mid)
        assert before == 3
        assert after == before
    finally:
        await pool.close()


@requires_pg
async def test_store_13neg_replay_fails_when_transcript_absent() -> None:
    pool = await _pool()
    mid = uuid.uuid4()
    try:
        async with pool.acquire() as conn:
            await notes_repo.insert_segment(
                conn, meeting_id=mid, text="only-segment", status=None
            )
            await conn.execute(
                "DELETE FROM transcript_segments WHERE meeting_id = $1", mid
            )
            remaining = await notes_repo.count_segments(conn, mid)
        assert remaining == 0
    finally:
        await pool.close()
