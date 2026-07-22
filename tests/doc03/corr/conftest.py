"""Fixtures for the doc03 CORR tier — REAL Postgres, no in-memory substitute.

Every db:postgres criterion here (AC-CORR-01/-02/-03/-04/-05/-06/-07/-08/-09/-10
and their -NEG twins) has a mock_boundary that FORBIDS a stubbed db seam. The
tests drive the production ``scribe.corrections.apply_correction`` against the
committed ``db.repos.notes`` ledger on a real Postgres carrying the sealed §3.3
schema (note_deltas: entry_id/op/payload jsonb/window_start_s + UNIQUE INDEX).

The db:postgres tier is OFF by default and opts in explicitly (the same shape as
the sibling STORE tier). If the opt-in is not set the db tests skip with a clear
reason (never a fake pass) — the moment the DB is provided they run verbatim.

Note (§3.3.1): ``note_deltas.payload`` is a jsonb column; asyncpg returns it as a
JSON *string*, so every read of a folded payload ``json.loads``-decodes it.
"""
from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
import pytest_asyncio

# The db:postgres integration tier is OFF by default and opts in explicitly, the
# same shape as the sibling STORE tier (tests/doc03/store/conftest.py). The gate
# is deterministic and env-based — NOT a live connect probe — because the repo
# root conftest auto-provisions a throwaway local Postgres and exports
# ``TEST_DATABASE_URL`` for the Doc-00 substrate tests; that DB does NOT carry the
# sealed §3.3 note_deltas schema, so a probe keyed on "is some note_deltas
# reachable" would let these tests RUN (and hard-fail on the divergent schema)
# instead of skipping cleanly on a default no-DB run. Requiring the explicit
# ``DOC03_STORE_SPEC_DB`` opt-in AND ``TEST_DATABASE_URL`` guarantees: default run
# -> clean skip; the with-DB command (which sets both) -> the tests run verbatim
# against the Postgres that carries the §3.3 schema (via ``alembic upgrade head``).
_SPEC_SCHEMA_AVAILABLE = os.environ.get("DOC03_STORE_SPEC_DB", "").strip() != ""
_HAVE_TEST_DB = os.environ.get("TEST_DATABASE_URL", "").strip() != ""

_DSN = os.environ.get(
    "TEST_DATABASE_URL", "postgres://proxy:proxy@127.0.0.1:55432/postgres"
)

requires_pg = pytest.mark.skipif(
    not (_SPEC_SCHEMA_AVAILABLE and _HAVE_TEST_DB),
    reason=(
        "db:postgres integration tier: set TEST_DATABASE_URL to a Postgres "
        "carrying the sealed §3.3 note_deltas schema (entry_id/op/payload "
        "jsonb/window_start_s + UNIQUE INDEX; apply via `alembic upgrade head`) "
        "and set DOC03_STORE_SPEC_DB to opt in — mock_boundary forbids an "
        "in-memory substitute, so a fake pass is never taken"
    ),
)

# An unroutable DSN for the real-fault (write-failure) negative tier. Port 1 is
# never a Postgres — a real asyncpg.connect against it raises, exercising the
# honest-degradation path WITHOUT stubbing the seam.
UNROUTABLE_DSN = "postgresql://proxy@127.0.0.1:1/nonexistent"


class PoolAcquirer:
    """A real ``SupportsAcquire`` over an asyncpg pool — the production db shape.

    ``acquire()`` returns the pool's async context manager yielding a borrowed
    connection, exactly what ``apply_correction`` expects. This is NOT a stub of
    the db seam: it is the real connection-borrow façade; the write still lands
    on real Postgres via the real ``db.repos.notes.append_delta``.
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    def acquire(self) -> Any:
        return self._pool.acquire()


class FailingAcquirer:
    """A real ``SupportsAcquire`` whose ``acquire()`` opens a REAL asyncpg
    connection to an unroutable DSN — the connect raises, so the write fails for
    real. No mock: the failure originates in the real asyncpg client, exercising
    the F-CORR-FALSE-CONFIRM / F-CORR-UNCOMMITTED-STICKS honest-degradation path.
    """

    def __init__(self, dsn: str = UNROUTABLE_DSN) -> None:
        self._dsn = dsn

    def acquire(self) -> Any:
        return _FailingCM(self._dsn)


class _FailingCM:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    async def __aenter__(self) -> Any:
        import asyncpg

        # Real connect to an unroutable endpoint -> raises here (the commit point
        # is never reached). This is the real seam failing, not a stub.
        return await asyncpg.connect(self._dsn, timeout=1)

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class RecordingSpeak:
    """Records every line handed to the speech seam. Doc 02's mouth is a vendor
    speech path (out of process); this records the ONE line the corrections layer
    would hand to it, letting us assert call-count and ordering without a vendor.
    It is the injected ``SpeakFn`` boundary, not a stub of the corrections logic.
    """

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.calls = 0

    async def __call__(self, line: str) -> None:
        self.calls += 1
        self.lines.append(line)


@pytest.fixture()
def meeting_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture()
def speak() -> RecordingSpeak:
    return RecordingSpeak()


@pytest_asyncio.fixture()
async def pool() -> Any:
    import asyncpg

    p = await asyncpg.create_pool(_DSN, min_size=1, max_size=4)
    try:
        yield p
    finally:
        await p.close()


@pytest.fixture()
def db(pool: Any) -> PoolAcquirer:
    return PoolAcquirer(pool)
