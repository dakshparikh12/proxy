"""REAL db:postgres oracles for CROSS-SESSION-READ — note_deltas fold, no stubs.

Every test here seeds REAL ``note_deltas`` rows via the production repo seam
(``db.repos.notes.append_delta``) and folds them through the production read path
(``read_notes`` / the two handlers). ``payload`` round-trips as a jsonb → JSON
STRING, exactly like production — proving ``Notes.fold_all`` ``json.loads``-rebuilds.

Env-gated on BOTH ``TEST_DATABASE_URL`` (a Postgres carrying the section 3.3
``note_deltas`` schema) AND the ``DOC03_STORE_SPEC_DB`` opt-in -- the same
shape as the sibling STORE / CORR tiers. When both are set these run for REAL;
otherwise they SKIP -- never stubbed, never fake a pass.
"""
from __future__ import annotations

import json
import os
import uuid

import pytest

from db.repos import notes as notes_repo

from scribe import notes_reader as nr

from .conftest import seed_delta

# db integration tier: these oracles seed + fold REAL note_deltas rows over the
# db.repos.notes seam, so they need a reachable Postgres carrying the section 3.3
# schema. The gate is env-based (NOT a live-connect probe) and requires BOTH the
# explicit DOC03_STORE_SPEC_DB opt-in AND TEST_DATABASE_URL -- the same shape as
# the sibling STORE / CORR tiers. This is deliberate: the repo-root conftest
# auto-provisions a throwaway local Postgres and force-sets TEST_DATABASE_URL for
# the Doc-00 substrate tests, but that DB does NOT carry the section 3.3
# note_deltas schema (no entry_id column). Keying the skip on TEST_DATABASE_URL
# alone would therefore let these RUN against the wrong-schema DB and hard-fail on
# a default no-DB run instead of skipping cleanly. Requiring DOC03_STORE_SPEC_DB
# too guarantees: default run -> clean SKIP (never a fake pass); the with-DB
# command (which sets both, pointing at the doc03_test DB carrying the section 3.3
# schema) -> these run for REAL over db.repos.notes. Never stubbed, never faked.
_HAVE_TEST_DB = bool(os.environ.get("TEST_DATABASE_URL", "").strip())
_SPEC_SCHEMA_AVAILABLE = bool(os.environ.get("DOC03_STORE_SPEC_DB", "").strip())

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not (_HAVE_TEST_DB and _SPEC_SCHEMA_AVAILABLE),
        reason=(
            "db integration tier: set TEST_DATABASE_URL to a Postgres carrying "
            "the section 3.3 note_deltas schema AND set DOC03_STORE_SPEC_DB to "
            "opt in (the repo-root conftest force-sets TEST_DATABASE_URL to a "
            "schema-divergent throwaway DB, so the opt-in is required to skip "
            "cleanly by default rather than fail on the wrong schema)"
        ),
    ),
]

GOOD_TOKEN = "internal-token-good"  # the module default when PROXY_INTERNAL_TOKEN is unset


# -- AC-CSREAD-03 -- 200 for a known meeting, 404 for an unknown meeting -------
async def test_csread_03_known_meeting_is_200_with_notes_json(db, pool, meeting_id) -> None:
    async with pool.acquire() as conn:
        await seed_delta(conn, meeting_id=meeting_id, entry_id="E1", op="add",
                         payload={"text": "hello"}, window_start_s=0.0)
    resp = await nr.internal_notes_handler(meeting_id, provided_token=GOOD_TOKEN, db=db)
    assert resp.status_code == 200
    assert resp.is_notes_object is True
    parsed = json.loads(resp.body)
    assert parsed["entries"][0]["text"] == "hello"


async def test_csread_03_unknown_meeting_is_404_not_empty_200(db, meeting_id) -> None:
    # No rows seeded for this fresh uuid.
    resp = await nr.internal_notes_handler(meeting_id, provided_token=GOOD_TOKEN, db=db)
    assert resp.status_code == 404
    assert resp.body is None
    assert resp.is_notes_object is False


# -- AC-CSREAD-02 -- the contract path folds from note_deltas (real) -----------
async def test_csread_02_handler_folds_real_note_deltas(db, pool, meeting_id) -> None:
    async with pool.acquire() as conn:
        await seed_delta(conn, meeting_id=meeting_id, entry_id="E1", op="add",
                         payload={"text": "v1"}, window_start_s=0.0)
        await seed_delta(conn, meeting_id=meeting_id, entry_id="E1", op="patch",
                         payload={"changes": {"text": "v2"}}, window_start_s=10.0)
    resp = await nr.internal_notes_handler(meeting_id, provided_token=GOOD_TOKEN, db=db)
    assert resp.status_code == 200
    parsed = json.loads(resp.body)
    # left-fold in id order: the patch (later id) wins.
    assert parsed["entries"][0]["text"] == "v2"


async def test_csread_02_read_notes_json_loads_the_jsonb_string_payload(db, pool, meeting_id) -> None:
    # asyncpg returns note_deltas.payload as a JSON STRING; the fold must decode it.
    async with pool.acquire() as conn:
        await seed_delta(conn, meeting_id=meeting_id, entry_id="E1", op="add",
                         payload={"text": "decoded", "n": 7}, window_start_s=0.0)
        raw = await notes_repo.load_deltas(conn, meeting_id)
    # PROVE the raw payload really is a str (the reconstruction premise).
    assert isinstance(raw[0]["payload"], str)
    notes = await nr.read_notes(meeting_id, db=db)
    assert notes.entries["E1"]["text"] == "decoded"
    assert notes.entries["E1"]["n"] == 7


# -- AC-CSREAD-05 -- bearer accepted (200); a session-cookie value denied (401)
async def test_csread_05_correct_bearer_is_200(db, pool, meeting_id) -> None:
    async with pool.acquire() as conn:
        await seed_delta(conn, meeting_id=meeting_id, entry_id="E1", op="add",
                         payload={"text": "x"}, window_start_s=0.0)
    ok = await nr.internal_notes_handler(meeting_id, provided_token=GOOD_TOKEN, db=db)
    denied = await nr.internal_notes_handler(meeting_id, provided_token="session=cookie", db=db)
    assert ok.status_code == 200
    assert denied.status_code == 401
    assert denied.body is None


# -- AC-CSREAD-06 -- NOTES_CACHE serve is a CONDITIONAL optimization -----------
class _Cache:
    def __init__(self, meeting_id, body: str) -> None:
        self._id = meeting_id
        self._body = body

    def __contains__(self, meeting_id: object) -> bool:
        return meeting_id == self._id

    def get_bytes(self, meeting_id: object) -> str:
        return self._body


async def test_csread_06_cold_cache_folds_populated_cache_serves_equal_bytes(db, pool, meeting_id) -> None:
    async with pool.acquire() as conn:
        await seed_delta(conn, meeting_id=meeting_id, entry_id="E1", op="add",
                         payload={"text": "cache-me", "b": 1, "a": 2}, window_start_s=0.0)
    # Cold cache (None): folds from note_deltas.
    cold = await nr.internal_notes_handler(meeting_id, provided_token=GOOD_TOKEN, db=db, notes_cache=None)
    assert cold.status_code == 200
    # The cache, if present, is BUILT FROM the same fold -> identical bytes.
    cache = _Cache(meeting_id, cold.body)
    warm = await nr.internal_notes_handler(
        meeting_id, provided_token=GOOD_TOKEN, db=db, notes_cache=cache
    )
    assert warm.status_code == 200
    assert warm.body == cold.body  # cache-hit bytes == fold bytes (no divergence)


async def test_csread_06_empty_cache_falls_through_to_fold(db, pool, meeting_id) -> None:
    async with pool.acquire() as conn:
        await seed_delta(conn, meeting_id=meeting_id, entry_id="E1", op="add",
                         payload={"text": "y"}, window_start_s=0.0)
    # A cache that does NOT hold this meeting must fall through to the fold path.
    empty = _Cache(uuid.uuid4(), "should-not-be-served")
    resp = await nr.internal_notes_handler(
        meeting_id, provided_token=GOOD_TOKEN, db=db, notes_cache=empty
    )
    assert resp.status_code == 200
    assert "should-not-be-served" not in (resp.body or "")


# -- AC-CSREAD-08 -- /m/ user surface folds from the SAME note_deltas ----------
async def test_csread_08_m_handler_folds_same_bytes_as_internal(db, pool, meeting_id) -> None:
    async with pool.acquire() as conn:
        await seed_delta(conn, meeting_id=meeting_id, entry_id="E1", op="add",
                         payload={"text": "same"}, window_start_s=0.0)
    internal = await nr.internal_notes_handler(meeting_id, provided_token=GOOD_TOKEN, db=db)
    m = await nr.m_handler(meeting_id, session={"user": "vp"}, db=db)
    assert internal.status_code == m.status_code == 200
    assert m.body == internal.body  # same fold, same bytes


async def test_csread_08_m_handler_requires_session(db, meeting_id) -> None:
    resp = await nr.m_handler(meeting_id, session=None, db=db)
    assert resp.status_code == 401
    assert resp.body is None


async def test_csread_08_m_handler_unknown_meeting_is_404(db, meeting_id) -> None:
    resp = await nr.m_handler(meeting_id, session={"user": "vp"}, db=db)
    assert resp.status_code == 404


# -- AC-CSREAD-09 -- freshness flag present in every 200 -----------------------
async def test_csread_09_freshness_flag_present_and_nonempty_in_200(db, pool, meeting_id) -> None:
    async with pool.acquire() as conn:
        await seed_delta(conn, meeting_id=meeting_id, entry_id="E1", op="add",
                         payload={"text": "f"}, window_start_s=0.0)
    resp = await nr.internal_notes_handler(meeting_id, provided_token=GOOD_TOKEN, db=db)
    parsed = json.loads(resp.body)
    assert "freshness_flag" in parsed
    flag = parsed["freshness_flag"]
    assert flag is not None and flag != "" and flag != {}
    assert flag["delta_count"] >= 1
    assert flag["as_of"] is not None
    assert flag["is_empty"] is False


# -- AC-CSREAD-09-NEG -- freshness reflects the Postgres-fold, not a stale clock
async def test_csread_09neg_freshness_advances_after_a_new_delta(db, pool, meeting_id) -> None:
    async with pool.acquire() as conn:
        await seed_delta(conn, meeting_id=meeting_id, entry_id="E1", op="add",
                         payload={"text": "first"}, window_start_s=0.0)
    first = await nr.read_notes(meeting_id, db=db)
    first_as_of = first.freshness_flag.as_of
    first_count = first.freshness_flag.delta_count

    # A new delta is written AFTER the first fold.
    async with pool.acquire() as conn:
        await seed_delta(conn, meeting_id=meeting_id, entry_id="E2", op="add",
                         payload={"text": "later"}, window_start_s=10.0)
    second = await nr.read_notes(meeting_id, db=db)

    # The re-fold must reflect the NEW row: strictly more deltas, and as_of not older.
    assert second.freshness_flag.delta_count == first_count + 1
    assert second.freshness_flag.as_of is not None
    assert second.freshness_flag.as_of >= first_as_of  # never lags behind the new delta


# -- AC-CSREAD-10 -- byte-identical across /internal, /m, and a direct fold ----
async def test_csread_10_three_callers_byte_identical_and_stable(db, pool, meeting_id) -> None:
    async with pool.acquire() as conn:
        await seed_delta(conn, meeting_id=meeting_id, entry_id="E1", op="add",
                         payload={"text": "first", "z": 26, "a": 1}, window_start_s=0.0)
        await seed_delta(conn, meeting_id=meeting_id, entry_id="E2", op="add",
                         payload={"text": "second"}, window_start_s=5.0)
        await seed_delta(conn, meeting_id=meeting_id, entry_id="E1", op="patch",
                         payload={"changes": {"text": "patched"}}, window_start_s=10.0)

    internal = await nr.internal_notes_handler(meeting_id, provided_token=GOOD_TOKEN, db=db)
    m = await nr.m_handler(meeting_id, session={"user": "vp"}, db=db)
    direct = (await nr.read_notes(meeting_id, db=db)).to_canonical_json()

    assert internal.body == m.body == direct
    # No ordering non-determinism across repeated folds of the FROZEN rows.
    for _ in range(10):
        again = (await nr.read_notes(meeting_id, db=db)).to_canonical_json()
        assert again == direct
