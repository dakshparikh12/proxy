"""Layer 4 chaos — doc03 (meeting understanding): kill Postgres mid-delta-write.

doc03's durability invariant (§3.3): the append-only ``note_deltas`` ledger is the
SOURCE OF TRUTH, and the live notes object is its deterministic left-fold in ``id``
order. A crash mid-write must NOT corrupt the ledger — a killed harness simply
replays it. Two design guards make that safe: each delta is a single-row atomic
INSERT (so a crash mid-write either lands the whole row or none of it), and the
``(meeting_id, window_start_s, entry_id, op)`` UNIQUE INDEX makes a re-append a
silent no-op (so replay is idempotent).

Steady state : the REAL durable path — ``db.repos.notes.append_delta`` commits a
               batch of deltas over the prod-shaped asyncpg pool, and
               ``load_deltas`` → ``Notes.fold_all`` reads a consistent, folded
               snapshot of the notes object.
Fault        : SIGKILL the live postmaster while an UNCOMMITTED delta INSERT is in
               flight (a delta write interrupted mid-transaction — the "killed
               harness mid-write" case).
Invariant    : after crash recovery, no partial/corrupt row exists —
               (1) the committed ledger survived byte-for-byte (the fold replays
                   to the SAME notes object it had before the crash),
               (2) the in-flight uncommitted row was rolled back (atomicity: no
                   half-written delta),
               (3) replaying the committed deltas is a silent no-op (the unique
                   index → ``append_delta`` returns None, never a dup row or a
                   crash), and the row count is unchanged, and
               (4) the fold is still deterministic post-recovery.

Uses the REAL functions (append_delta / load_deltas / Notes.fold_all) so the
durability contract runs for real against a live postmaster, not a mock.

Run directly:  python verification/chaos/doc03.py   (prints JSON)
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import pathsetup  # noqa: E402

pathsetup.bootstrap()
from config.chaos_lib import ChaosResult, EphemeralPostgres, kill_process, wait_gone  # noqa: E402

# The §3.3 note_deltas schema (migration 0004), minus the tenants FK — the chaos
# cluster is standalone, and append-only note_deltas carries no FK by design
# (§3.3: "no FK, append-only, standalone"). This is the shape the real repo SQL
# and the real fold both target.
_DDL_NOTE_DELTAS = """
CREATE TABLE note_deltas (
    id             bigserial PRIMARY KEY,
    meeting_id     uuid NOT NULL,
    entry_id       text NOT NULL,
    op             text NOT NULL CHECK (op IN ('add', 'patch', 'close')),
    payload        jsonb NOT NULL,
    window_start_s double precision,
    created_at     timestamptz NOT NULL DEFAULT now()
)
"""
_DDL_UNIQ = (
    "CREATE UNIQUE INDEX note_deltas_source_window_uniq "
    "ON note_deltas (meeting_id, window_start_s, entry_id, op)"
)
_DDL_ORDER_IDX = "CREATE INDEX note_deltas_meeting_id_id_idx ON note_deltas (meeting_id, id)"

# A committed batch of deltas: an add, a patch on it, a second add, a close. The
# fold reduces these to two entries — one patched, one resolved.
_COMMITTED = [
    ("e1", "add", {"text": "ship the checkout fix", "current_goal": "unblock checkout"}, 0.0),
    ("e1", "patch", {"changes": {"status": "final"}}, 1.0),
    ("e2", "add", {"text": "open question: rollout window?"}, 1.0),
    ("e2", "close", {"resolution": "answered: Friday"}, 2.0),
]


async def _experiment(pg: EphemeralPostgres) -> ChaosResult:
    from db.database import open_pool
    from db.repos.notes import append_delta, load_deltas
    from scribe.notes_reader import Notes

    meeting_id = uuid4()

    # ---- Steady state: create the ledger, commit the batch, fold a snapshot. ----
    pool = await open_pool(pg.dsn())
    async with pool.acquire() as con:
        await con.execute(_DDL_NOTE_DELTAS)
        await con.execute(_DDL_UNIQ)
        await con.execute(_DDL_ORDER_IDX)
    async with pool.acquire() as con:
        for entry_id, op, payload, ws in _COMMITTED:
            await append_delta(
                con, meeting_id=meeting_id, entry_id=entry_id, op=op,
                payload=payload, window_start_s=ws,
            )
    async with pool.acquire() as con:
        rows_before = await load_deltas(con, meeting_id)
    fold_before = Notes.fold_all(rows_before).to_canonical_json()
    count_before = len(rows_before)
    steady = (f"committed {count_before} note_deltas; fold reads a consistent "
              f"snapshot ({len(Notes.fold_all(rows_before).order)} entries)")

    # ---- Fault: an UNCOMMITTED delta INSERT in flight, then SIGKILL the server. ----
    con2 = await pool.acquire()
    tr = con2.transaction()
    await tr.start()
    # A half-written, never-committed delta — the "killed harness mid-write" row.
    await con2.execute(
        "INSERT INTO note_deltas (meeting_id, entry_id, op, payload, window_start_s) "
        "VALUES ($1, $2, $3, $4::jsonb, $5)",
        meeting_id, "e3", "add", json.dumps({"text": "phantom mid-write"}), 3.0,
    )

    pid = pg.postmaster_pid()
    assert pid is not None
    kill_process(pid)          # SIGKILL the postmaster mid-write
    wait_gone(pid)

    # The in-flight connection must fail loudly, not silently "succeed" the write.
    client_errored = False
    try:
        await asyncio.wait_for(con2.execute("SELECT 1"), timeout=5.0)
    except Exception:          # asyncpg connection-lost family, or TimeoutError
        client_errored = True
    pool.terminate()           # non-graceful (the server is gone)

    # ---- Recovery: restart the SAME data dir; WAL replays committed work only. ----
    pg.start()
    pool2 = await asyncio.wait_for(open_pool(pg.dsn()), timeout=15.0)

    async with pool2.acquire() as con:
        rows_after = await load_deltas(con, meeting_id)
    fold_after = Notes.fold_all(rows_after).to_canonical_json()
    count_after = len(rows_after)

    # (1) committed ledger survived: same fold + same row count.
    ledger_survived = (fold_after == fold_before) and (count_after == count_before)
    # (2) the uncommitted phantom row was rolled back — no half-written delta.
    no_phantom = all(str(r["entry_id"]) != "e3" for r in rows_after)
    # (3) replaying the committed deltas is a silent no-op (unique index), row
    #     count unchanged, no exception raised on any re-append.
    replay_noops = True
    async with pool2.acquire() as con:
        for entry_id, op, payload, ws in _COMMITTED:
            res = await append_delta(
                con, meeting_id=meeting_id, entry_id=entry_id, op=op,
                payload=payload, window_start_s=ws,
            )
            if res is not None:  # a real insert on replay would mean a dup row landed
                replay_noops = False
        rows_replayed = await load_deltas(con, meeting_id)
    count_stable = len(rows_replayed) == count_before
    # (4) the fold is still deterministic post-recovery + post-replay.
    fold_replayed = Notes.fold_all(rows_replayed).to_canonical_json()
    fold_deterministic = fold_replayed == fold_before
    pool2.terminate()

    survived = (
        client_errored
        and ledger_survived
        and no_phantom
        and replay_noops
        and count_stable
        and fold_deterministic
    )
    detail = (
        f"client_errored={client_errored}; rows_before={count_before} "
        f"rows_after_recovery={count_after}; ledger_survived={ledger_survived}; "
        f"phantom_rolled_back={no_phantom}; replay_was_noop={replay_noops}; "
        f"count_stable_after_replay={count_stable}; fold_deterministic={fold_deterministic}"
    )
    return ChaosResult(
        name="kill_postgres_mid_delta_write", doc="doc03", steady_state=steady,
        fault="SIGKILL postmaster during an uncommitted note_deltas INSERT",
        survived=survived, detail=detail,
    )


def run() -> ChaosResult:
    pg = EphemeralPostgres()
    if not pg.available:
        return ChaosResult(
            name="kill_postgres_mid_delta_write", doc="doc03",
            steady_state="", fault="SIGKILL postmaster mid-delta-write",
            survived=False, detail="", skipped=True,
            skip_reason="no Postgres binary (initdb/pg_ctl/postgres) found on host",
        )
    try:
        pg.start()
        return asyncio.run(_experiment(pg))
    finally:
        pg.cleanup()


if __name__ == "__main__":
    result = run()
    print(json.dumps(result.to_dict(), indent=2))
    raise SystemExit(0 if (result.survived or result.skipped) else 1)
