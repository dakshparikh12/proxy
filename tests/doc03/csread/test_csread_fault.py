"""REAL db:postgres fault-injection oracles for CROSS-SESSION-READ.

The fault is genuine: :class:`FaultAcquirer` borrows a connection to an
UNROUTABLE DSN, so ``read_notes`` raises a real ``asyncpg``/``OSError`` at the
seam — the db seam is DRIVEN, not stubbed (the mock_boundary forbids a stub). The
handlers must degrade honestly: a 5xx with NO notes body, never a stale/empty 200.

These do not need a live Postgres (the fault IS the point), so they run every
session — no skip.
"""
from __future__ import annotations

import uuid

import pytest

from scribe import notes_reader as nr

from .conftest import FaultAcquirer

pytestmark = pytest.mark.asyncio

GOOD_TOKEN = "internal-token-good"


# -- AC-CSREAD-02-NEG -- /internal/notes -> 503, no fabricated/stale 200 -------
async def test_csread_02neg_internal_notes_503_on_db_outage() -> None:
    resp = await nr.internal_notes_handler(
        uuid.uuid4(), provided_token=GOOD_TOKEN, db=FaultAcquirer()
    )
    assert resp.status_code >= 500
    assert resp.status_code == 503
    assert resp.body is None  # NOT a valid notes object
    assert resp.is_notes_object is False


async def test_csread_02neg_read_notes_propagates_the_real_db_error() -> None:
    # The durable read has NO silent empty-notes fallback: the real fault surfaces.
    with pytest.raises(Exception):  # noqa: B017 - a real asyncpg/OSError from the seam
        await nr.read_notes(uuid.uuid4(), db=FaultAcquirer())


# -- AC-CSREAD-06-NEG -- honest degradation even when a cache is absent --------
async def test_csread_06neg_absent_cache_plus_db_outage_is_503() -> None:
    # notes_cache=None => must reach the fold path, which faults => 503 (no proceed).
    resp = await nr.internal_notes_handler(
        uuid.uuid4(), provided_token=GOOD_TOKEN, db=FaultAcquirer(), notes_cache=None
    )
    assert resp.status_code == 503
    assert resp.body is None


# -- AC-CSREAD-08-NEG -- /m/ -> error, never a stale/empty 200 -----------------
async def test_csread_08neg_m_handler_503_on_db_outage() -> None:
    resp = await nr.m_handler(
        uuid.uuid4(), session={"user": "vp"}, db=FaultAcquirer()
    )
    assert resp.status_code == 503
    assert resp.body is None
    assert resp.is_notes_object is False


# -- AC-CSREAD-10-NEG -- the fold path degrades honestly on a db fault ---------
async def test_csread_10neg_fold_path_surfaces_fault_no_corruption() -> None:
    # Both callers over a faulting seam surface the failure identically (503),
    # never a partially-folded / corrupted object.
    internal = await nr.internal_notes_handler(
        uuid.uuid4(), provided_token=GOOD_TOKEN, db=FaultAcquirer()
    )
    m = await nr.m_handler(uuid.uuid4(), session={"user": "vp"}, db=FaultAcquirer())
    assert internal.status_code == m.status_code == 503
    assert internal.body is None and m.body is None
