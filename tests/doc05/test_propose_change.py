"""Doc 05 · §3.8 — multi-file ``propose_change`` on the REAL host path (integration).

The Workroom agent's ONLY write-to-the-world is ``propose_change`` — a HOST-side
in-process SDK MCP tool (CANONICAL §11.7), never a sandbox ``code`` tool. This suite
proves the node's Definition of Done on the reachable host path:

  * MULTI-FILE — one call accepts ``files:[{path, old_sha?, new_content}]`` OR a
    ``unified_diff`` and stages a whole code-change draft (CANONICAL §12.9);
  * ONE bundle, ONE row — it persists EXACTLY one GCS Object-Versioned bundle body +
    EXACTLY one ``staged_drafts`` row AT CREATION (durable before the sandbox teardown,
    CANONICAL §4);
  * ``draft_id`` + ``status=needs_review`` — the return contract; it NEVER lands and
    NEVER pushes (§3.8, propose-not-apply);
  * the LIVE-ASSEMBLY seam — the persisted bundle is accepted from DURABLE storage by
    the already-built accept-handler (``control_plane.accept``) AFTER the sandbox is
    gone, proving the real substrate seam (not an isolation-only module);
  * ``make_propose_change_server()`` — a host-side in-process SDK MCP server, mounted
    ONLY for the worker disposition (never quick / plan / critic / verifier).

Integration bodies open ``S.pg_conn()`` (skip when no local Postgres) and import the
product FIRST so a missing product is RED, a missing DB is a skip. The row is written to
the real ``staged_drafts`` table migrated to head — no in-memory dict stands in.
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


def _count_drafts(conn, meeting_id: str) -> int:
    return conn.execute(
        "SELECT count(*) FROM staged_drafts WHERE meeting_id = %s", (meeting_id,)
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# 1. Multi-file: one call stages a whole multi-file code-change draft.
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_propose_change_multi_file_persists_one_bundle_and_one_row():
    """A worker's multi-file propose_change → ONE bundle + ONE row, needs_review, no push."""
    from workroom import drafts, objectstore  # product first → red if absent

    with S.pg_conn() as conn:
        _require_schema(conn)
        _tenant, meeting = _seed_meeting(conn, tenant_name=f"t-{uuid.uuid4().hex[:8]}")

        before = _count_drafts(conn, meeting)
        result = drafts.propose_change(
            conn,
            meeting_id=meeting,
            kind="code-change",
            summary="add the per-user rate limiter",
            files=[
                {"path": "app/limiter.py", "new_content": "def limit(): ...\n"},
                {"path": "app/middleware.py", "old_sha": "deadbeef", "new_content": "X = 1\n"},
                {"path": "tests/test_limiter.py", "new_content": "def test(): assert True\n"},
            ],
        )

        # The return contract: a durable draft_id + needs_review, never proposed/applied.
        assert result.status == "needs_review", result.status
        assert result.draft_id is not None

        # EXACTLY one staged_drafts row was persisted (not >1) — durable at creation.
        assert _count_drafts(conn, meeting) == before + 1, "must persist EXACTLY one row"

        row = conn.execute(
            "SELECT kind, summary, artifact_ref, status FROM staged_drafts WHERE draft_id = %s",
            (result.draft_id,),
        ).fetchone()
        assert row is not None, "the row must exist in durable storage"
        kind, summary, artifact_ref, row_status = row
        assert kind == "code-change"
        assert summary == "add the per-user rate limiter"
        # The row is durable as 'proposed' (needs_review is the surfaced envelope status).
        assert row_status == "proposed", row_status

        # EXACTLY one GCS bundle body carries ALL three files as one Object-Versioned blob.
        body = objectstore.get(artifact_ref)
        assert body is not None, "the bundle body must be durable at creation"
        bundle = json.loads(body)
        paths = [f["path"] for f in bundle["files"]]
        assert paths == ["app/limiter.py", "app/middleware.py", "tests/test_limiter.py"], paths
        assert bundle["kind"] == "code-change"


# ---------------------------------------------------------------------------
# 2. A unified_diff is accepted as the alternative multi-file form.
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_propose_change_accepts_unified_diff():
    """propose_change accepts a unified_diff (the alternative to a files list)."""
    from workroom import drafts, objectstore

    with S.pg_conn() as conn:
        _require_schema(conn)
        _tenant, meeting = _seed_meeting(conn, tenant_name=f"t-{uuid.uuid4().hex[:8]}")

        udiff = (
            "--- a/app/x.py\n+++ b/app/x.py\n@@ -1 +1 @@\n-old\n+new\n"
            "--- a/app/y.py\n+++ b/app/y.py\n@@ -1 +1 @@\n-a\n+b\n"
        )
        before = _count_drafts(conn, meeting)
        result = drafts.propose_change(
            conn,
            meeting_id=meeting,
            kind="code-change",
            summary="two-file diff",
            unified_diff=udiff,
        )
        assert result.status == "needs_review"
        assert _count_drafts(conn, meeting) == before + 1, "one row for a unified_diff too"

        row = conn.execute(
            "SELECT artifact_ref FROM staged_drafts WHERE draft_id = %s", (result.draft_id,)
        ).fetchone()
        bundle = json.loads(objectstore.get(row[0]))
        assert bundle["unified_diff"] == udiff, "the diff rides the single bundle body"


# ---------------------------------------------------------------------------
# 3. LIVE-ASSEMBLY: the durable bundle is accepted after the sandbox is gone.
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_proposed_bundle_is_accepted_from_durable_storage_after_teardown():
    """The persisted multi-file bundle is accepted by the accept-handler post-teardown.

    Proves the real substrate seam: propose (host) → tear down the dead review session
    → a human accept reads the DURABLE row + GCS body (never the dead session), records
    approval for a code-change, exposes the bundle, and NEVER pushes.
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
            summary="staged after teardown",
            files=[{"path": "a.py", "new_content": "1\n"}, {"path": "b.py", "new_content": "2\n"}],
        )

        # The sandbox / review session dies — the in-memory state is gone.
        drafts.teardown_review_session(result.review_session_id)

        # A human accepts LATER, from durable storage only.
        applied = apply_accepted_draft(conn, meeting_id=meeting, draft_id=result.draft_id)
        assert applied.read_from == "durable", "accept must read durable storage, never the dead session"
        assert applied.kind == "code-change"
        assert applied.pushed is False, "a code-change accept must NEVER push (Expansion seam)"
        assert applied.bundle_url, "a code-change accept exposes the download bundle handle"

        status = conn.execute(
            "SELECT status FROM staged_drafts WHERE draft_id = %s", (result.draft_id,)
        ).fetchone()[0]
        assert status == "applied", f"approval must be recorded durably, got {status!r}"


# ---------------------------------------------------------------------------
# 4. make_propose_change_server() — a host-side in-process SDK MCP server.
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_make_propose_change_server_is_host_in_process_sdk_server():
    """make_propose_change_server() returns an in-process SDK MCP server holding the tool."""
    from workroom import drafts

    server = drafts.make_propose_change_server(conn=None, meeting_id=str(uuid.uuid4()))
    # The SDK in-process server config shape: {type:'sdk', name, instance}.
    assert isinstance(server, dict), server
    assert server.get("type") == "sdk", "must be an in-process SDK MCP server (not http/stdio)"
    assert server.get("name") == "propose_change"
    assert server.get("instance") is not None, "the mcp Server instance must be present"


@pytest.mark.integration
def test_propose_change_server_tool_persists_one_bundle_and_row_and_never_pushes():
    """Invoking the SDK tool handler itself persists ONE bundle + ONE row, needs_review, no push."""
    import asyncio

    from workroom import drafts, objectstore

    with S.pg_conn() as conn:
        _require_schema(conn)
        _tenant, meeting = _seed_meeting(conn, tenant_name=f"t-{uuid.uuid4().hex[:8]}")

        # Build the factory-per-query server bound to this connection + meeting.
        tool = drafts.make_propose_change_tool(conn=conn, meeting_id=meeting)

        before = _count_drafts(conn, meeting)
        out = asyncio.get_event_loop().run_until_complete(
            tool.handler(
                {
                    "kind": "code-change",
                    "summary": "via the tool handler",
                    "files": [
                        {"path": "one.py", "new_content": "a\n"},
                        {"path": "two.py", "new_content": "b\n"},
                    ],
                }
            )
        )
        # The handler returns the SDK content shape carrying draft_id + needs_review.
        text = out["content"][0]["text"]
        payload = json.loads(text)
        assert payload["status"] == "needs_review", payload
        assert payload["draft_id"], payload
        assert "push" not in text.lower() or "nothing lands or is pushed" in text.lower()

        # EXACTLY one row + one durable bundle.
        assert _count_drafts(conn, meeting) == before + 1, "the tool persists EXACTLY one row"
        row = conn.execute(
            "SELECT artifact_ref FROM staged_drafts WHERE draft_id = %s",
            (payload["draft_id"],),
        ).fetchone()
        bundle = json.loads(objectstore.get(row[0]))
        assert [f["path"] for f in bundle["files"]] == ["one.py", "two.py"]


@pytest.mark.integration
def test_tool_handler_never_throws_on_bad_input():
    """The tool handler returns a structured error, never raises (Hard Rule 6 / D-018)."""
    import asyncio

    from workroom import drafts

    # No conn / no files / no diff → the handler must return is_error content, not raise.
    tool = drafts.make_propose_change_tool(conn=None, meeting_id=str(uuid.uuid4()))
    out = asyncio.get_event_loop().run_until_complete(tool.handler({"summary": "x"}))
    assert isinstance(out, dict) and "content" in out, out
    assert out.get("is_error") is True, "a bad input must be a returned error, never a raise"


# ---------------------------------------------------------------------------
# 5. Worker-only mount — the server is mounted ONLY for the worker disposition.
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_propose_change_server_mounted_only_for_worker_disposition():
    """The in-process propose_change server is mounted ONLY for the worker disposition."""
    from workroom import drafts

    worker = drafts.mcp_servers_for_disposition("worker", conn=None, meeting_id=str(uuid.uuid4()))
    assert "propose_change" in worker, "the worker MUST carry the host propose_change server"
    assert worker["propose_change"]["type"] == "sdk"

    for ro in ("quick", "plan", "critic", "verifier"):
        mounted = drafts.mcp_servers_for_disposition(ro, conn=None, meeting_id=str(uuid.uuid4()))
        assert "propose_change" not in mounted, (
            f"{ro} must NOT mount the host propose_change server (worker-only, §3.8)"
        )


@pytest.mark.integration
def test_worker_advertises_propose_change_readonly_dispositions_block_it():
    """The advertised tool policy: worker advertises propose_change; read-only block it."""
    from workroom.agent_config import PROPOSE_CHANGE_TOOL, disposition_tool_policy

    worker = disposition_tool_policy("worker")
    assert PROPOSE_CHANGE_TOOL in worker.allowed_tools, "worker advertises the host propose_change"
    assert PROPOSE_CHANGE_TOOL not in worker.disallowed_tools

    for ro in ("quick", "plan", "critic", "verifier"):
        pol = disposition_tool_policy(ro)
        assert PROPOSE_CHANGE_TOOL not in pol.allowed_tools, f"{ro} must not advertise propose_change"
        assert PROPOSE_CHANGE_TOOL in pol.disallowed_tools, (
            f"{ro} must BLOCK propose_change via disallowed_tools (allowed_tools "
            "does not filter MCP tools, §3.8)"
        )
