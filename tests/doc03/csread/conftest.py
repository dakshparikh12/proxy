"""Shared fixtures for the doc03 CROSS-SESSION-READ tier.

The §3.3 schema is LIVE on the local test Postgres this session (``note_deltas``
exists), so the ``db:postgres`` oracles run for REAL against the ``db.repos.notes``
seam and ``Notes.fold_all`` — never stubbed, never faked (the mock_boundary forbids
an in-memory substitute). The db tier is gated on the DSN being reachable: it runs
when ``TEST_DATABASE_URL`` points at a Postgres carrying ``note_deltas``, and skips
(never fakes a pass) only when no such DB is reachable.

Fault injection is a REAL asyncpg failure: :class:`FaultAcquirer.acquire` opens a
connection to an unroutable DSN, so the ``503`` path is exercised by a genuine
``asyncpg``/``OSError`` at the seam — the db seam is driven, not mocked.
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Any, Optional

import pytest
import pytest_asyncio

try:
    import asyncpg
except Exception:  # pragma: no cover - asyncpg always present in this workspace
    asyncpg = None  # type: ignore[assignment]

# The db tier reads its DSN from TEST_DATABASE_URL. The db-test module owns its
# own skip gate (it requires BOTH TEST_DATABASE_URL and the DOC03_STORE_SPEC_DB
# opt-in) — deliberately NOT a live-connect probe here, because the repo-root
# conftest force-sets TEST_DATABASE_URL to a schema-divergent throwaway Postgres,
# so a probe would fire a real connection on every collection (even a default
# no-DB run). Keeping this env-only leaves collection side-effect-free.
_DSN = os.environ.get("TEST_DATABASE_URL", "").strip()

# A DSN that refuses instantly — a REAL connection error, never a stub.
UNROUTABLE_DSN = "postgresql://proxy@127.0.0.1:1/nonexistent"


class PoolAcquirer:
    """A REAL :class:`scribe.notes_reader.Acquirer` backed by an asyncpg pool.

    ``acquire()`` returns the pool's own async-context connection borrow — the
    exact seam ``read_notes`` drives in production. No in-memory substitute.
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    def acquire(self) -> Any:
        return self._pool.acquire()


class FaultAcquirer:
    """A REAL Acquirer whose ``acquire()`` connects to an unroutable DSN.

    This is genuine fault injection at the db seam: borrowing a connection raises
    a real ``asyncpg``/``OSError`` — the handler's ``except`` turns it into a 503.
    Nothing is mocked; the failure is a real transport error.
    """

    def acquire(self) -> "FaultAcquirer":
        return self

    async def __aenter__(self) -> Any:
        return await asyncpg.connect(UNROUTABLE_DSN, timeout=1)

    async def __aexit__(self, *exc: Any) -> bool:
        return False


@pytest.fixture()
def meeting_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest_asyncio.fixture()
async def pool() -> Any:
    """A live asyncpg pool over the real note_deltas DB (closed on teardown)."""
    p = await asyncpg.create_pool(_DSN, min_size=1, max_size=4)
    try:
        yield p
    finally:
        await p.close()


@pytest.fixture()
def db(pool: Any) -> PoolAcquirer:
    """The real Acquirer seam read_notes/handlers drive."""
    return PoolAcquirer(pool)


async def seed_delta(
    conn: Any,
    *,
    meeting_id: Any,
    entry_id: str,
    op: str,
    payload: dict[str, Any],
    window_start_s: float,
) -> None:
    """Append one REAL note_deltas row via the production repo seam.

    Uses ``db.repos.notes.append_delta`` — the same writer the store layer uses —
    so the row's ``payload`` is stored as jsonb and reloaded as a JSON STRING,
    exactly like production (the KEY reconstruction step the fold must handle).
    """
    from db.repos import notes as notes_repo

    await notes_repo.append_delta(
        conn,
        meeting_id=meeting_id,
        entry_id=entry_id,
        op=op,
        payload=payload,
        window_start_s=window_start_s,
    )


def json_key_present(body: Optional[str], key: str) -> bool:
    if body is None:
        return False
    try:
        parsed = json.loads(body)
    except (ValueError, TypeError):
        return False
    return isinstance(parsed, dict) and key in parsed
