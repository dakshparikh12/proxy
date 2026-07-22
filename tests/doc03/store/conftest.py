"""Shared fixtures for the doc03 STORE integration tier.

Postgres carrying the SEALED §3.3 schema is not available this session, so the
db:postgres oracles are written against the real ``db.repos.notes`` seam and
skipped via ``requires_pg`` — never stubbed, never faked (the mock_boundary
forbids an in-memory substitute). The moment a Postgres with the §3.3 spec DDL
(note_deltas entry_id/window_start_s/payload jsonb + UNIQUE INDEX,
transcript_segments speaker/start_s/end_s) is reachable, dropping the skip
marker runs them verbatim.
"""
from __future__ import annotations

import os
import uuid

import pytest

_SPEC_SCHEMA_AVAILABLE = os.environ.get("DOC03_STORE_SPEC_DB", "").strip() != ""

requires_pg = pytest.mark.skipif(
    not _SPEC_SCHEMA_AVAILABLE,
    reason=(
        "integration tier: Postgres carrying the sealed \u00a73.3 STORE schema "
        "(note_deltas entry_id/window_start_s/payload jsonb + UNIQUE INDEX) is "
        "not available this session; set DOC03_STORE_SPEC_DB to run"
    ),
)

requires_gcs = pytest.mark.skipif(
    os.environ.get("DOC03_STORE_GCS_BUCKET", "").strip() == "",
    reason=(
        "integration tier: a real GCS bucket with Object Versioning ON is not "
        "available this session; set DOC03_STORE_GCS_BUCKET to run"
    ),
)


@pytest.fixture()
def meeting_id() -> uuid.UUID:
    return uuid.uuid4()
