"""Shared fixtures + skip gates for the doc03 CLOSE-PASS tier.

Two dependency tiers cannot run for real THIS session and are skip-gated here —
never stubbed, never Mock-faked (the criteria mock_boundaries forbid replacing
the seam):

* ``requires_anthropic`` — the vendor:anthropic ``generateStructured`` reduce
  call. Neither ``anthropic`` nor ``claude_agent_sdk`` is installed and there is
  no recorded cassette this session, so the strong-model close pass, the dedup/
  conflict-resolution eval, and the model-id cassette assertion stay skipped. A
  real vendor pass is NEVER simulated with a Mock (§3.7 / AC-CLOSE-01/03/06/12).

* ``requires_gcs`` — a real GCS bucket with Object Versioning ON for the
  integration ``if_generation_match=0`` create-only oracle. Not available this
  session; the create-only DISCIPLINE is exercised at the unit tier through the
  real ``write_finalized_notes`` seam driven by a create-only bucket double that
  honours the 412 contract, but the live-bucket integration oracle stays skipped.

The db:postgres backfill read DOES run for real against ``TEST_DATABASE_URL``
(the transcript_segments archive is reachable on :55432 this session).
"""
from __future__ import annotations

import importlib.util
import os
import uuid

import pytest

_ANTHROPIC = importlib.util.find_spec("anthropic") is not None
_SDK = importlib.util.find_spec("claude_agent_sdk") is not None

requires_anthropic = pytest.mark.skipif(
    not (_ANTHROPIC and _SDK),
    reason=(
        "reality tier: neither anthropic nor claude_agent_sdk is installed and no "
        "generateStructured cassette exists this session; the vendor:anthropic "
        "close pass is NOT Mock-faked (§3.7 / AC-CLOSE-01/03/06/12)"
    ),
)

requires_gcs = pytest.mark.skipif(
    os.environ.get("DOC03_CLOSE_GCS_BUCKET", "").strip() == "",
    reason=(
        "integration tier: a real GCS bucket with Object Versioning ON is not "
        "available this session; set DOC03_CLOSE_GCS_BUCKET to run the live "
        "if_generation_match=0 create-only oracle"
    ),
)

_PG_DSN = os.environ.get(
    "TEST_DATABASE_URL", "postgres://proxy:proxy@127.0.0.1:55432/postgres"
)


@pytest.fixture()
def pg_dsn() -> str:
    return _PG_DSN


@pytest.fixture()
def meeting_id() -> uuid.UUID:
    return uuid.uuid4()
