"""note_deltas + transcript_segments repository — the durable live-append plane (§3.3).

This module carries the *parameterised SQL* for the two-plane store's Postgres
half (Plane 1, §3.3): the append-only ``note_deltas`` ledger and the
``transcript_segments`` status flips, plus the boot-time stale-row reaper. It is
raw asyncpg only — **no ORM** (AC-STORE-04): each function takes a borrowed
asyncpg connection (from ``db.acquire()``) so the caller owns the transaction
boundary, exactly like the sibling repo modules.

Design boundary (deliberate): the durable object is the deterministic *left-fold*
of ``note_deltas`` in ``id`` order (§3.3), but that typed fold belongs to the
read-path layer (``scribe.notes_reader.read_notes`` → ``Notes.fold_all``). This
repo deals in **raw rows** only: :func:`load_deltas` returns the ordered jsonb
rows the fold consumes — it never folds them. Keeping the fold out of the store
keeps the store a pure substrate seam.

The SQL below is matched to the canonical DDL in §3.3:

    CREATE TABLE transcript_segments (
      id bigserial PRIMARY KEY, meeting_id uuid NOT NULL, speaker text,
      text text NOT NULL, start_s double precision, end_s double precision,
      status text NOT NULL DEFAULT 'pending', created_at timestamptz ... );
    CREATE TABLE note_deltas (
      id bigserial PRIMARY KEY, meeting_id uuid NOT NULL, entry_id text NOT NULL,
      op text NOT NULL, payload jsonb NOT NULL, window_start_s double precision,
      created_at timestamptz ... );
    CREATE UNIQUE INDEX ON note_deltas (meeting_id, window_start_s, entry_id, op);
"""
from __future__ import annotations

import json
from typing import Any

# Staleness window for the boot reaper (PART II.1 / §3.3): a ``running``
# meeting-harness row whose heartbeat is older than this is orphaned. It is a
# fixed SQL ``interval`` LITERAL inlined into the reaper query below (never an
# f-string interpolation — no injection surface, and byte-for-byte the spec's
# ``interval '5 minutes'``). The operational ops-sweep tunable lives separately
# in ``db.config.stale_after_s``.


async def append_delta(
    conn: Any,
    *,
    meeting_id: Any,
    entry_id: str,
    op: str,
    payload: Any,
    window_start_s: float | None = None,
) -> dict[str, Any] | None:
    """Append ONE row to the append-only ``note_deltas`` ledger (§3.3).

    ``ON CONFLICT DO NOTHING`` on the ``(meeting_id, window_start_s, entry_id, op)``
    UNIQUE INDEX makes a stray re-append a silent no-op (AC-STORE-02): the replay
    idempotency belt-and-suspenders from §3.3. A distinct ``op`` for the same
    ``(meeting_id, window_start_s, entry_id)`` is a genuinely new row and is NOT
    discarded (AC-STORE-02-NEG) — ``op`` is part of the key.

    Returns the inserted row (id + created_at) on a real insert, or ``None`` when
    the conflict clause silently discarded a duplicate — so the caller can tell a
    fresh append from a no-op WITHOUT any exception ever being raised on a dup.

    ``payload`` is stored as ``jsonb``: a dict/list is json-encoded here so the
    parameter binds as a jsonb text literal; a str is passed through as-is (it is
    assumed already-serialised json).
    """
    payload_arg = payload if isinstance(payload, str) else json.dumps(payload)
    row = await conn.fetchrow(
        """
        INSERT INTO note_deltas
            (meeting_id, entry_id, op, payload, window_start_s)
        VALUES ($1, $2, $3, $4::jsonb, $5)
        ON CONFLICT (meeting_id, window_start_s, entry_id, op) DO NOTHING
        RETURNING id, created_at
        """,
        meeting_id,
        entry_id,
        op,
        payload_arg,
        window_start_s,
    )
    return dict(row) if row is not None else None


async def load_deltas(conn: Any, meeting_id: Any) -> list[dict[str, Any]]:
    """Load ALL ``note_deltas`` for ONE meeting in ascending ``id`` order (§3.3).

    ``ORDER BY id`` is the load-bearing correctness axis (AC-STORE-05): the durable
    object is the deterministic left-fold of these rows in *write* order, and the
    ``(meeting_id, id)`` index makes this replay cheap. Meeting-scoped by
    construction (tenant isolation) — never a cross-meeting sweep.

    Returns RAW rows (each a dict with ``id, entry_id, op, payload, window_start_s,
    created_at``). It does NOT fold them — the typed left-fold is the read-path
    layer's job (``Notes.fold_all``); this store returns evidence in order.
    """
    rows = await conn.fetch(
        """
        SELECT id, meeting_id, entry_id, op, payload, window_start_s, created_at
          FROM note_deltas
         WHERE meeting_id = $1
         ORDER BY id
        """,
        meeting_id,
    )
    return [dict(row) for row in rows]


async def insert_segment(
    conn: Any,
    *,
    meeting_id: Any,
    text: str,
    speaker: str | None = None,
    start_s: float | None = None,
    end_s: float | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """Append one ``transcript_segments`` row (the append-only archive, §3.3).

    ``status`` is left to the column DEFAULT ('pending', §3.3 / AC-STORE-01) unless
    a caller explicitly overrides it — a landed segment is ALWAYS 'pending' until
    the applier flips it 'comprehended' transactionally (§3.1). When ``status`` is
    ``None`` the column is OMITTED from the INSERT so Postgres applies its DEFAULT
    (never a client-side 'pending' literal that could mask a wrong default).
    """
    if status is None:
        row = await conn.fetchrow(
            """
            INSERT INTO transcript_segments (meeting_id, speaker, text, start_s, end_s)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id, meeting_id, speaker, text, start_s, end_s, status, created_at
            """,
            meeting_id,
            speaker,
            text,
            start_s,
            end_s,
        )
    else:
        row = await conn.fetchrow(
            """
            INSERT INTO transcript_segments
                (meeting_id, speaker, text, start_s, end_s, status)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id, meeting_id, speaker, text, start_s, end_s, status, created_at
            """,
            meeting_id,
            speaker,
            text,
            start_s,
            end_s,
            status,
        )
    return dict(row)


async def set_segment_status(conn: Any, *, segment_id: Any, status: str) -> None:
    """Flip one ``transcript_segments`` row's status (§3.3 linchpin).

    'pending' → 'comprehended' (applier, in the delta's tx) | → 'gap' (skip) —
    the close pass backfills anything still 'pending'/'gap'. A caller that needs
    the flip atomic with the note-delta append wraps both in one transaction.
    """
    await conn.execute(
        "UPDATE transcript_segments SET status = $2 WHERE id = $1",
        segment_id,
        status,
    )


async def count_segments(conn: Any, meeting_id: Any) -> int:
    """Row count of ``transcript_segments`` for ONE meeting (AC-STORE-13 oracle).

    The transcript plane is the append-only archive/backstore (§3.3, Granola's
    pattern) — rows are NEVER deleted during or after a meeting. This count is the
    before/after invariant the close pass must preserve (equal counts prove no
    lifecycle DELETE/TRUNCATE touched the archive).
    """
    value = await conn.fetchval(
        "SELECT count(*) FROM transcript_segments WHERE meeting_id = $1",
        meeting_id,
    )
    return int(value)


async def reap_orphaned_meetings(pool: Any) -> int:
    """Boot-time stale-row reaping (§3.3 / AC-STORE-07/08).

    A ``live`` meeting whose ``meeting-harness`` operation row is stale or
    interrupted belongs to a killed harness; leaving it pins the UI to "in
    progress" forever. The reaper reaps meetings via a JOIN to ``operation_runs``
    on ``scope_id = meeting_id::text`` (the one documented §11.2 cast) —

    it MUST NOT key off ``meetings.last_heartbeat_at``: that column does NOT
    exist (``meetings`` carries only ``status live|ended|interrupted``, §11.1).
    The heartbeat lives on ``operation_runs.last_heartbeat_at``. A meeting whose
    ``meeting-harness`` row is ``interrupted`` (already swept) OR ``running``-but
    -stale (heartbeat older than the staleness window) → mark the meeting
    ``interrupted``.

    Returns the number of meetings reaped (rows updated) so the caller can log the
    heal count. Takes the POOL (not a borrowed conn) — it is a boot barrier that
    owns its own statement, matching the spec signature ``reap_orphaned_meetings(pool)``.
    """
    rows = await pool.fetch(
        """
        UPDATE meetings m SET status = 'interrupted'
        WHERE m.status = 'live'
          AND EXISTS (
            SELECT 1 FROM operation_runs r
            WHERE r.scope_id       = m.id::text
              AND r.operation_type = 'meeting-harness'
              AND ( r.status = 'interrupted'
                 OR (r.status = 'running'
                     AND r.last_heartbeat_at < now() - interval '5 minutes') )
          )
        RETURNING m.id
        """
    )
    return len(rows)
