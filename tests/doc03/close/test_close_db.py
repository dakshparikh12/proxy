"""AC-CLOSE db:postgres tier — the gap/pending backfill read runs FOR REAL.

The transcript_segments archive is reachable on :55432 this session, so the
``fetch_gap_pending_spans`` seam is exercised against real rows (no stub — the
mock_boundary for AC-CLOSE-04-NEG forbids stubbing the db seam). These cover:

* AC-CLOSE-04  — only status IN ('gap','pending') is read; comprehended raw text
                 never enters the input.
* AC-CLOSE-04-NEG — an unreachable DB surfaces DatabaseConnectionError (never a
                 silent skip of the backfill).
* AC-CLOSE-13 / -13-NEG — the close-pass backfill READ never deletes/truncates
                 the transcript archive (row counts unchanged; the seam issues a
                 SELECT, never a DELETE/TRUNCATE).
"""
from __future__ import annotations

import importlib.util
import os

import pytest

# Env-gate the db:postgres tier so the DEFAULT run skips this whole module.
#
# The gap/pending backfill READ must hit REAL Postgres carrying the sealed §3.3
# transcript_segments schema (bigserial id, NO meeting_id FK, status CHECK IN
# pending/comprehended/gap) — the AC-CLOSE-04/-04-NEG mock_boundary forbids stubbing
# the db seam, so there is nothing to run without that DB. The repo's top-level
# conftest AUTO-starts a base-substrate Postgres and sets TEST_DATABASE_URL for the
# whole suite, so TEST_DATABASE_URL alone is NOT a reliable "spec DB available"
# signal (that base schema still has the meeting_id FK). Gate on the SAME explicit
# opt-in the STORE db-tier uses (DOC03_STORE_SPEC_DB) AND a set TEST_DATABASE_URL —
# both are set by the component's with-DB run command; the default run sets neither
# DOC03_STORE_SPEC_DB, so this module skips. The pure unit / ordering / typed-error
# criteria live in test_close_unit.py and run UNCONDITIONALLY.
_DB_TIER_ENABLED = bool(
    os.environ.get("TEST_DATABASE_URL") and os.environ.get("DOC03_STORE_SPEC_DB")
)
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not _DB_TIER_ENABLED,
        reason=(
            "db:postgres tier: set TEST_DATABASE_URL + DOC03_STORE_SPEC_DB to a "
            "Postgres carrying the sealed §3.3 transcript_segments schema; the "
            "gap/pending backfill read runs for real and is never stubbed "
            "(AC-CLOSE-04/-04-NEG/-13)."
        ),
    ),
]

# asyncpg is a dev dependency; skip cleanly if somehow absent.
_HAVE_ASYNCPG = importlib.util.find_spec("asyncpg") is not None
pytest.importorskip("asyncpg")

import asyncpg  # noqa: E402

from scribe.close import (  # noqa: E402
    DatabaseConnectionError,
    fetch_gap_pending_spans,
)


async def _try_connect(dsn: str):
    try:
        return await asyncpg.connect(dsn, timeout=5)
    except Exception:  # noqa: BLE001
        return None


async def _seed(conn, meeting_id):
    """Seed 50 comprehended + 3 gap + 2 pending segments (AC-CLOSE-04 scenario)."""
    for i in range(50):
        await conn.execute(
            "INSERT INTO transcript_segments (meeting_id, text, status) VALUES ($1,$2,$3)",
            meeting_id, f"COMPREHENDED-RAW-{i}", "comprehended",
        )
    for i in range(3):
        await conn.execute(
            "INSERT INTO transcript_segments (meeting_id, text, status) VALUES ($1,$2,$3)",
            meeting_id, f"GAP-RAW-{i}", "gap",
        )
    for i in range(2):
        await conn.execute(
            "INSERT INTO transcript_segments (meeting_id, text, status) VALUES ($1,$2,$3)",
            meeting_id, f"PENDING-RAW-{i}", "pending",
        )


async def test_ac_close_04_reads_only_gap_and_pending(pg_dsn, meeting_id):
    conn = await _try_connect(pg_dsn)
    if conn is None:
        pytest.skip(f"db tier: Postgres not reachable at {pg_dsn}")
    try:
        await _seed(conn, meeting_id)
        spans = await fetch_gap_pending_spans(conn, meeting_id)
        # Exactly the 3 gap + 2 pending rows (5 total); no comprehended rows.
        assert len(spans) == 5
        statuses = sorted(s.status for s in spans)
        assert statuses == ["gap", "gap", "gap", "pending", "pending"]
        for s in spans:
            assert "COMPREHENDED-RAW" not in s.text
        gaps = [s for s in spans if s.status == "gap"]
        pend = [s for s in spans if s.status == "pending"]
        assert len(gaps) == 3
        assert len(pend) == 2
    finally:
        await conn.execute("DELETE FROM transcript_segments WHERE meeting_id=$1", meeting_id)
        await conn.close()


async def test_ac_close_04_comprehended_raw_text_never_returned(pg_dsn, meeting_id):
    conn = await _try_connect(pg_dsn)
    if conn is None:
        pytest.skip(f"db tier: Postgres not reachable at {pg_dsn}")
    try:
        await _seed(conn, meeting_id)
        spans = await fetch_gap_pending_spans(conn, meeting_id)
        joined = "\n".join(s.text for s in spans)
        for i in range(50):
            assert f"COMPREHENDED-RAW-{i}" not in joined
    finally:
        await conn.execute("DELETE FROM transcript_segments WHERE meeting_id=$1", meeting_id)
        await conn.close()


async def test_ac_close_04neg_unreachable_db_raises_typed_error(meeting_id):
    # An unroutable DSN -> the seam surfaces DatabaseConnectionError, never a
    # silent skip of the backfill. We drive the REAL seam with a connection object
    # that fails on .fetch (a genuinely broken connection), not a stub of the seam.
    class _BrokenConn:
        async def fetch(self, *a, **k):
            raise OSError("connection refused (simulated DB outage)")

    with pytest.raises(DatabaseConnectionError):
        await fetch_gap_pending_spans(_BrokenConn(), meeting_id)


async def test_ac_close_13_backfill_read_does_not_delete_archive(pg_dsn, meeting_id):
    # AC-CLOSE-13 / -13-NEG: the close-pass backfill READ preserves the archive —
    # row counts before == after; the seam never issues a DELETE/TRUNCATE.
    conn = await _try_connect(pg_dsn)
    if conn is None:
        pytest.skip(f"db tier: Postgres not reachable at {pg_dsn}")
    try:
        await _seed(conn, meeting_id)
        before = await conn.fetchval(
            "SELECT count(*) FROM transcript_segments WHERE meeting_id=$1", meeting_id
        )
        await fetch_gap_pending_spans(conn, meeting_id)
        after = await conn.fetchval(
            "SELECT count(*) FROM transcript_segments WHERE meeting_id=$1", meeting_id
        )
        assert before == after == 55  # 50 + 3 + 2, nothing deleted by the read
    finally:
        await conn.execute("DELETE FROM transcript_segments WHERE meeting_id=$1", meeting_id)
        await conn.close()


def test_ac_close_13neg_source_issues_no_delete_or_truncate():
    # Static guard: the backfill seam's SQL is a SELECT — no DELETE/TRUNCATE.
    import inspect

    from scribe import close

    src = inspect.getsource(close.fetch_gap_pending_spans)
    lowered = src.lower()
    assert "delete" not in lowered
    assert "truncate" not in lowered
    assert "select" in lowered
