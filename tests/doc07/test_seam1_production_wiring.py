"""SEAM 1's PRODUCTION wiring — the test that would have caught the dead seam.

``test_seam1_close_intake.py`` proves the seam behaves correctly *given a hook*. Every one
of its cases constructs ``CloseConfig(..., post_meeting_intake=<something>)`` itself. That
is precisely why the gap survived: production never set the field, ``_run_post_meeting_intake``
returned at its first line on every real close, and ``run_extract`` / ``run_triage`` had no
production caller — while the suite stayed green, because the suite supplied the thing
production was missing.

So nothing here injects a hook. These tests go at the real construction site
(``server._build_close_config``) and at the real store path, and assert the chain is
connected end to end against real Postgres:

    _build_close_config(db)  →  CloseConfig.post_meeting_intake is not None
                            →  hook(final_notes, meeting_id=...)
                            →  tenant resolved from the meetings ROW
                            →  post_meeting_tasks rows exist

Written against real Postgres per CLAUDE.md's standing rule: the whole point is what the
substrate ends up holding, and a fake store would once again be the test supplying what
production must.
"""
from __future__ import annotations

import logging
import os
import uuid

import pytest
import pytest_asyncio
from harness.post_meeting.models import Tier
from harness.post_meeting.wire import make_intake_hook

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


class _Pool:
    """``libs.db.Database``-shaped adapter so the real stores run their real SQL."""

    def __init__(self, pool):
        self._pool = pool

    def acquire(self):
        return self._pool.acquire()


@pytest_asyncio.fixture()
async def db(dsn):
    asyncpg = pytest.importorskip("asyncpg")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
    try:
        yield _Pool(pool)
    finally:
        await pool.close()


@pytest_asyncio.fixture()
async def seed(db):
    async with db.acquire() as conn:
        tenant_id = await conn.fetchval(
            "INSERT INTO tenants (name) VALUES ('seam1-prod') RETURNING id"
        )
        meeting_id = await conn.fetchval(
            "INSERT INTO meetings (tenant_id, status) VALUES ($1, 'ended') RETURNING id",
            tenant_id,
        )
    yield tenant_id, meeting_id
    async with db.acquire() as conn:
        await conn.execute("DELETE FROM post_meeting_tasks WHERE tenant_id = $1", tenant_id)
        await conn.execute("DELETE FROM clarify_items WHERE tenant_id = $1", tenant_id)
        await conn.execute("DELETE FROM meetings WHERE tenant_id = $1", tenant_id)
        await conn.execute("DELETE FROM tenants WHERE id = $1", tenant_id)


class _Item:
    def __init__(self, text, owner=None):
        self.text = text
        self.owner = owner


class _Notes:
    """The close's finalized record, in the shape ``extract_items`` actually reads."""

    def __init__(self, *items):
        self.action_items = list(items)


def _triage_caller(tier: str = "ticket"):
    """A stand-in for the ANTHROPIC edge ONLY — never for the store or the wiring.

    The vendor round-trip is the single thing that cannot run here. Everything this file is
    actually about (the hook being set, the tenant resolving, the rows landing in Postgres)
    runs for real.
    """

    import re

    async def _call(*, model=None, prompt="", output_schema=None, tool_name=""):
        from libs.llm.src.llm.structured import StructuredResult

        schema = output_schema or {}
        props = (schema.get("properties") or {})
        if "verdicts" in props:
            # B2 triage. The item_refs are read back OUT of the prompt rather than
            # reconstructed, so this fake cannot drift from extract's real ref format
            # (``{meeting_id}#action_items[{i}]``) — the drift that would make it
            # "pass" while production produced nothing.
            refs = re.findall(r"[0-9a-f-]{36}#action_items\[\d+\]", prompt)
            assert refs, "the triage prompt carried no item refs — extract produced nothing"
            return StructuredResult(
                data={
                    "verdicts": [
                        {
                            "item_ref": r,
                            "tier": tier,
                            "draft_conditions_met": [],
                        }
                        for r in refs
                    ]
                },
                total_cost_usd=0.0,
            )
        # B4 plan.
        return StructuredResult(
            data={
                "task_one_line": "bump the retry ceiling",
                "why_it_exists": "the room asked for it",
                "meeting_reference": "action_items[0]",
                "owner": "Sam",
                "done_looks_like": "the ceiling is 5",
                "confidence": "high",
            },
            total_cost_usd=0.0,
        )

    return _call


async def _pass_through_external(op, *, service=None, **kwargs):
    """The ``call_external`` seam's real SHAPE — run the op, no retry, no network.

    ``generate_structured`` calls ``call_external(_op, service=...)`` and reads ``.value``
    off the outcome, so a fake that merely raises (as the first draft of this file did)
    fails the triage stage for the wrong reason and hides whether the wiring works.
    """
    return await op()


# ── the wiring itself ─────────────────────────────────────────────────────
#: Asks the REAL production builder one question, in a process of its own.
_PROBE = """
import json, os, sys
sys.path.insert(0, os.getcwd())
from harness import server
cfg = server._build_close_config(db=object())
print(json.dumps({
    "built": cfg is not None,
    "hook_set": cfg is not None and cfg.post_meeting_intake is not None,
    "hook_callable": cfg is not None and callable(cfg.post_meeting_intake),
}))
"""


async def test_production_close_config_actually_sets_the_intake_hook():
    """THE regression. ``_build_close_config`` is the only production construction.

    **Run in a SUBPROCESS, and that is not incidental.** ``settings`` parses the environment
    once at import and caches it on a module-level object, so importing the real boot module
    in-process — under the fake env its fail-fast gate (doc00 §6) demands — poisons that
    cache for every later test in the run. The first draft of this file did exactly that and
    flipped two genuinely failing doc04 boot tests to passing: ``doc07 + doc04`` reported
    529 passed while ``doc04`` alone reported 2 failed. Evicting ``sys.modules`` afterwards
    did not fix it either (the eviction itself changed what doc04 re-imported).

    A test that hides two real failures to prove one wire is a bad trade. The subprocess
    boundary is the only honest way to exercise the real builder: nothing it imports, parses
    or caches can reach the parent process.
    """
    import json
    import subprocess
    import sys

    env = dict(os.environ)
    env.update(
        {k: "test-value" for k in (
            "DATABASE_URL", "RECALL_API_KEY", "AES_KEY_RECALL", "AES_KEY_STT",
            "AES_KEY_CALENDAR", "ANTHROPIC_API_KEY",
        )}
    )
    env["GCS_BUCKET"] = "test-bucket"
    env["PYTHONUTF8"] = "1"

    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert proc.returncode == 0, f"the production builder could not run:\n{proc.stderr}"
    out = json.loads(proc.stdout.strip().splitlines()[-1])

    assert out["built"], "no close config was built at all"
    assert out["hook_set"], (
        "CloseConfig.post_meeting_intake is None in PRODUCTION wiring — the seam runs on "
        "every close and does nothing; no action item ever becomes a task"
    )
    assert out["hook_callable"]


@pytest.mark.negative
async def test_a_missing_hook_is_logged_at_error_not_swallowed(caplog):
    """The silence that hid this for the whole build is now an ERROR line."""
    from harness.scribe_runtime import CloseConfig, _run_post_meeting_intake

    cfg = CloseConfig(bucket=object(), bucket_name="b", post_chat_link=None)
    assert cfg.post_meeting_intake is None, "the premise: an unwired config"

    with caplog.at_level(logging.ERROR):
        await _run_post_meeting_intake(cfg, uuid.uuid4(), _Notes())

    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert errors, "an unwired seam passed quietly — the exact defect this file exists for"
    joined = " ".join(r.getMessage() for r in errors)
    assert "NOT wired" in joined
    assert "make_intake_hook" in joined, "the log must name the fix, not just the symptom"
    assert "close itself is unaffected" in joined, "Doc 07 §2 must stay stated"


# ── the hook the production site supplies actually works ──────────────────
async def test_the_production_hook_writes_tasks_to_real_postgres(db, seed):
    """End to end through the REAL hook: no injected seam except the vendor call."""
    tenant_id, meeting_id = seed
    hook = make_intake_hook(
        db, caller=_triage_caller(), call_external=_pass_through_external
    )

    result = await hook(
        _Notes(_Item("bump the retry ceiling on checkout", owner="Sam")),
        meeting_id=meeting_id,
    )

    assert result is not None, "intake did not run"
    assert result.ok, f"intake failed at {result.failed_stage}: {result.error!r}"
    assert result.task_count == 1

    async with db.acquire() as conn:
        rows = await conn.fetch(
            "SELECT tenant_id, meeting_id, owner, tier, state FROM post_meeting_tasks "
            " WHERE meeting_id = $1",
            meeting_id,
        )
    assert len(rows) == 1, "the close produced an action item but no task row landed"
    assert rows[0]["tenant_id"] == tenant_id, "the tenant must come from the meeting ROW"
    assert rows[0]["owner"] == "Sam"
    assert rows[0]["tier"] == Tier.TICKET.value


async def test_the_tenant_is_resolved_server_side_not_supplied(db, seed):
    """The hook takes only (final_notes, meeting_id). A tenant cannot be passed in."""
    import inspect

    _, meeting_id = seed
    hook = make_intake_hook(db, caller=_triage_caller(), call_external=_pass_through_external)
    params = inspect.signature(hook).parameters
    assert "tenant_id" not in params, "a caller-supplied tenant is a cross-tenant hazard"
    assert set(params) == {"final_notes", "meeting_id"}


@pytest.mark.negative
async def test_an_unknown_meeting_writes_nothing_and_says_why(db, caplog):
    """No meeting row means no tenant, and there is no safe tenant to invent."""
    hook = make_intake_hook(db, caller=_triage_caller(), call_external=_pass_through_external)
    ghost = uuid.uuid4()

    with caplog.at_level(logging.ERROR):
        result = await hook(_Notes(_Item("do a thing", owner="Sam")), meeting_id=ghost)

    assert result is None
    assert any("has no row" in r.getMessage() for r in caplog.records)
    async with db.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM post_meeting_tasks WHERE meeting_id = $1", ghost
        )
    assert count == 0, "a task was written for a meeting with no resolvable tenant"


@pytest.mark.negative
async def test_a_failing_hook_still_cannot_break_the_close(caplog):
    """Doc 07 §2 holds regardless of what the now-wired hook does."""
    from harness.scribe_runtime import CloseConfig, _run_post_meeting_intake

    async def exploding(final_notes, *, meeting_id):
        raise RuntimeError("intake exploded")

    cfg = CloseConfig(
        bucket=object(), bucket_name="b", post_chat_link=None,
        post_meeting_intake=exploding,
    )
    with caplog.at_level(logging.ERROR):
        out = await _run_post_meeting_intake(cfg, uuid.uuid4(), _Notes())
    assert out is None, "the seam must not propagate a failure into the close"
