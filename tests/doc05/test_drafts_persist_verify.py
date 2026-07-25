"""Doc 05 · node ``workroom.drafts-persist-verify`` — the durability floor (§3.8 / CANONICAL §4).

This suite RE-PROVES the node's Definition of Done on the REAL substrate seam
(real Postgres + the durable objectstore), not an isolation-only module:

  * **persist at creation** — ``objectstore.put`` writes the full body durably and
    ``objectstore.get`` reads it back BYPASSING any in-memory session; the
    ``staged_drafts`` row is ``proposed`` the moment ``propose_change`` is called;
  * **accept-after-sandbox-gone reads DURABLE storage** — the in-memory review
    session is torn down (and dropped) BEFORE the human accept; the accept still
    reads the persisted row + GCS body (``read_from == 'durable'``), never the dead
    session, and flips the row to ``applied``;
  * **a code-change accept NEVER pushes** — ``drafts.accept_code_change_draft`` and
    the ``control_plane.accept`` handler are handed a *spy* ``origin`` whose ``push``
    would record the call; the accept records approval + returns a download bundle
    handle and the spy proves ``push`` was NEVER invoked (push is Expansion behind
    ``contents:write``, §3.8 / CANONICAL §12.9 / AC-INV-007).

These tests would go RED if accept read a dead in-memory session, if the body were
not durable at creation, or if a core code-change accept could push — the three
NOT-done conditions the node names.

Integration bodies open ``S.pg_conn()`` (skip when no local Postgres) and import the
product FIRST (missing product → RED, missing DB → skip). The row is written to the
real ``staged_drafts`` table migrated to head — no in-memory dict stands in.
"""
from __future__ import annotations

import json
import pathlib
import sys
import uuid

import pytest

pytestmark = [pytest.mark.integration]

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "doc00"))
import _support as S  # noqa: E402  reuse pg_conn / apply_migrations / _local_dsn


# ---------------------------------------------------------------------------
# A push-recording spy ``origin``. If ANY accept path calls ``push`` the spy
# flips ``pushed`` — a core code-change accept must NEVER reach it (§3.8).
# ---------------------------------------------------------------------------
class _PushSpyOrigin:
    """A git origin whose ``push`` records the call (it must never be invoked in core)."""

    def __init__(self) -> None:
        self.push_calls: list[tuple] = []

    def push(self, *args, **kwargs):  # noqa: ANN002, ANN003
        self.push_calls.append((args, kwargs))
        raise AssertionError("core accept must NEVER call origin.push (Expansion seam, §3.8)")

    @property
    def pushed(self) -> bool:
        return bool(self.push_calls)


def _require_schema(conn) -> None:
    """Ensure the substrate is at head for this test; skip if no reachable DB."""
    for table in ("tenants", "meetings", "staged_drafts"):
        if conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0] is None:
            r = S.apply_migrations(S._local_dsn() or "")
            assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"
            return


def _seed_meeting(conn, *, tenant_name: str) -> tuple[str, str]:
    """Insert a tenant + a meeting under it; return (tenant_id, meeting_id UUID str)."""
    tid = conn.execute(
        "INSERT INTO tenants (name) VALUES (%s) RETURNING id", (tenant_name,)
    ).fetchone()[0]
    mid = conn.execute(
        "INSERT INTO meetings (tenant_id, status) VALUES (%s, 'ended') RETURNING id",
        (tid,),
    ).fetchone()[0]
    return str(tid), str(mid)


# ===========================================================================
# 1. objectstore.put/get — the durable body survives with no in-memory session.
# ===========================================================================
@pytest.mark.integration
def test_objectstore_put_persists_and_get_reads_durably():
    """put() writes a NEW durable object version; get() reads it back verbatim.

    No sandbox, no session, no DB — pure durable-store round-trip. This is the
    persistence floor the accept path stands on: the body is readable from durable
    storage alone (never a dead in-memory review session).
    """
    from workroom import objectstore  # product first → red if absent

    ref = f"gs://proxy-drafts/{uuid.uuid4().hex}/bundle.json"
    body = json.dumps({"files": [{"path": "a.py", "new_content": "x = 1\n"}]})

    returned = objectstore.put(ref, body)
    assert returned == ref, "put returns the ref it durably stored under"

    # Read back from durable storage — no session object exists at all here.
    got = objectstore.get(ref)
    assert got == body, "get must read the durable body back verbatim"

    # A never-written ref reads as None (absence, not a raise).
    assert objectstore.get(f"gs://proxy-drafts/{uuid.uuid4().hex}/missing") is None


@pytest.mark.integration
def test_objectstore_get_survives_a_simulated_process_boundary():
    """A body put in one 'process' is readable in a fresh objectstore import (durable, not in-memory).

    Re-importing the module (a stand-in for the sandbox being gone) still reads the
    same durable body — proving the store is on the durable substrate, not process
    memory that dies with the sandbox.
    """
    from workroom import objectstore

    ref = f"gs://proxy-drafts/{uuid.uuid4().hex}/x"
    objectstore.put(ref, "durable-body")

    # Drop and re-import the module — no module-level in-memory cache may hold it.
    for name in [m for m in list(sys.modules) if m.endswith("workroom.objectstore")]:
        del sys.modules[name]
    from workroom import objectstore as objectstore2  # a fresh import

    assert objectstore2.get(ref) == "durable-body", "body must survive a fresh import (durable substrate)"


# ===========================================================================
# 2. propose_change persists ONE durable body + ONE 'proposed' row AT CREATION.
# ===========================================================================
@pytest.mark.integration
def test_draft_is_durable_at_creation_body_and_proposed_row():
    """At creation: a 'proposed' staged_drafts row + a GCS bundle body, both durable.

    Reads the body straight from ``objectstore.get(row.artifact_ref)`` — the durable
    read path, not the propose() return object — to prove the body is durable at
    creation, not merely in the caller's hand.
    """
    from workroom import drafts, objectstore  # product first

    with S.pg_conn() as conn:
        _require_schema(conn)
        _tenant, meeting = _seed_meeting(conn, tenant_name=f"t-{uuid.uuid4().hex[:8]}")

        result = drafts.propose_change(
            conn,
            meeting_id=meeting,
            kind="code-change",
            summary="durable at creation",
            files=[{"path": "a.py", "new_content": "1\n"}],
        )

        row = conn.execute(
            "SELECT artifact_ref, status FROM staged_drafts WHERE draft_id = %s",
            (result.draft_id,),
        ).fetchone()
        assert row is not None, "the row must be persisted at creation"
        artifact_ref, status = row
        assert status == "proposed", f"the durable row status is 'proposed' at creation, got {status!r}"

        # The body is durable at creation — read it from the durable store by its ref.
        body = objectstore.get(artifact_ref)
        assert body is not None, "the bundle body must be durable at creation (GCS put)"
        assert json.loads(body)["files"][0]["path"] == "a.py"


# ===========================================================================
# 3. accept_draft reads DURABLE storage after the sandbox / session is gone.
# ===========================================================================
@pytest.mark.integration
def test_accept_reads_durable_after_sandbox_gone_via_control_plane():
    """propose → tear down + DROP the review session → accept reads durable row + body.

    The control_plane accept-handler (the already-built orchestrator.accept-handler)
    reads the persisted ``staged_drafts`` row + GCS body AFTER the in-memory session
    is torn down AND its id is dropped — proving ``read_from == 'durable'`` (never the
    dead session) and a durable status flip to 'applied'.
    """
    from workroom import drafts  # product first
    from control_plane.accept import apply_accepted_draft

    with S.pg_conn() as conn:
        _require_schema(conn)
        _tenant, meeting = _seed_meeting(conn, tenant_name=f"t-{uuid.uuid4().hex[:8]}")

        result = drafts.propose_change(
            conn,
            meeting_id=meeting,
            kind="code-change",
            summary="accept after teardown",
            files=[{"path": "a.py", "new_content": "1\n"}, {"path": "b.py", "new_content": "2\n"}],
        )
        draft_id = result.draft_id

        # The sandbox dies: tear down the in-memory review session and DROP its handle,
        # so nothing in this test can accidentally read a live session.
        drafts.teardown_review_session(result.review_session_id)
        result = None  # noqa: F841 — the in-memory proposal object is gone with the sandbox

        applied = apply_accepted_draft(conn, meeting_id=meeting, draft_id=draft_id)
        assert applied.read_from == "durable", "accept MUST read durable storage, never the dead session"
        assert applied.kind == "code-change"
        assert applied.pushed is False, "a code-change accept must NEVER push"
        assert applied.bundle_url, "a code-change accept exposes the download bundle handle"

        status = conn.execute(
            "SELECT status FROM staged_drafts WHERE draft_id = %s", (draft_id,)
        ).fetchone()[0]
        assert status == "applied", f"approval must be recorded durably, got {status!r}"


# ===========================================================================
# 4. accept_code_change_draft records approval + returns a bundle, NEVER pushes.
#    A push-recording spy origin proves push is never reached.
# ===========================================================================
@pytest.mark.integration
def test_accept_code_change_records_approval_and_never_pushes():
    """drafts.accept_code_change_draft → approval recorded + bundle handle; spy origin unpushed."""
    from workroom import drafts  # product first

    spy = _PushSpyOrigin()
    tenant = f"t-{uuid.uuid4().hex[:8]}"
    draft_id = uuid.uuid4().hex

    accepted = drafts.accept_code_change_draft(
        draft_id=draft_id,
        tenant=tenant,
        actor="sam@acme.test",
        origin=spy,
    )

    assert accepted.approval_recorded is True, "the human approval must be recorded"
    assert accepted.approved is True
    assert accepted.bundle_url, "must return a download bundle handle"
    assert accepted.scope == "contents:read", "core holds only the read-only scope"
    # The load-bearing guarantee: the spy origin's push was NEVER called.
    assert spy.pushed is False, "core accept must NEVER call origin.push (Expansion seam, §3.8)"
    assert spy.push_calls == [], spy.push_calls


@pytest.mark.integration
def test_control_plane_accept_never_touches_a_push_spy_origin():
    """The full post-teardown accept path, handed a push-spy origin, never pushes.

    Even wired end-to-end (propose → teardown → control_plane accept), no code path
    reaches ``origin.push``. The spy would raise on any push; a clean accept proves
    push is unreachable in core.
    """
    from workroom import drafts
    from control_plane.accept import apply_accepted_draft

    with S.pg_conn() as conn:
        _require_schema(conn)
        _tenant, meeting = _seed_meeting(conn, tenant_name=f"t-{uuid.uuid4().hex[:8]}")

        result = drafts.propose_change(
            conn,
            meeting_id=meeting,
            kind="code-change",
            summary="no-push end to end",
            files=[{"path": "a.py", "new_content": "1\n"}],
        )
        drafts.teardown_review_session(result.review_session_id)

        spy = _PushSpyOrigin()
        # The control_plane accept-handler has no origin parameter by design: it
        # records approval + exposes the bundle and structurally cannot push. Prove
        # that the accept completes AND the spy — passed to the drafts-layer accept —
        # is never pushed. (If a push seam were ever wired in, the spy would raise.)
        applied = apply_accepted_draft(conn, meeting_id=meeting, draft_id=result.draft_id)
        code_accept = drafts.accept_code_change_draft(
            draft_id=result.draft_id, tenant=_tenant, actor="sam@acme.test", origin=spy
        )

        assert applied.pushed is False and code_accept.approved is True
        assert spy.pushed is False, "no accept path may call origin.push in core (§3.8)"


# ===========================================================================
# 5. Double-accept is idempotent on the durable row (post-restart belt).
# ===========================================================================
@pytest.mark.integration
def test_accept_is_idempotent_on_the_durable_row():
    """A second accept of an already-applied draft re-reads the durable row, no double-apply."""
    from workroom import drafts
    from control_plane.accept import apply_accepted_draft

    with S.pg_conn() as conn:
        _require_schema(conn)
        _tenant, meeting = _seed_meeting(conn, tenant_name=f"t-{uuid.uuid4().hex[:8]}")

        result = drafts.propose_change(
            conn,
            meeting_id=meeting,
            kind="code-change",
            summary="idempotent accept",
            files=[{"path": "a.py", "new_content": "1\n"}],
        )
        drafts.teardown_review_session(result.review_session_id)

        first = apply_accepted_draft(conn, meeting_id=meeting, draft_id=result.draft_id)
        assert first.applied_status == "applied" and first.already_applied is False

        second = apply_accepted_draft(conn, meeting_id=meeting, draft_id=result.draft_id)
        assert second.read_from == "durable", "the replay still reads durable storage"
        assert second.already_applied is True, "an already-applied draft is not re-applied"
        assert second.pushed is False
