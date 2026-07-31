"""DRAFT-TOOL full arc on the REAL durable path — propose via the agent tool, human accept.

Law 3's one write-to-the-world, end to end on real Postgres + the real durable
object store: the agent-side ``mcp__drafts__propose_change`` tool (driven through
the REAL mcp ``CallToolRequest`` dispatch, exactly as the SDK drives it) stages a
draft via the real staging machinery — ONE durable bundle + ONE ``staged_drafts``
row bound to THIS meeting — and returns the draft id + the approve URL (the accept
route path). Then the LIVE mounted accept route on the ACTUAL ``create_app()`` app:

  * REFUSES a member of a DIFFERENT tenant (403; the row stays ``proposed`` —
    the server-side draft→meeting→tenant fence, never a client-supplied tenant);
  * applies the draft for the owning tenant's member (200; code-change = approval
    recorded + bundle handle exposed, ``pushed`` is ALWAYS False; row → ``applied``).

Reuses the existing accept-route test infrastructure verbatim (doc00 ``_support``
pg_conn/migrations, the doc08 signed-session + double-submit CSRF recipe). Bodies
import the product FIRST (missing product = red) and open ``S.pg_conn()`` after
(missing local Postgres = skip).
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import uuid
from typing import Any

import pytest
from starlette.testclient import TestClient

pytestmark = [pytest.mark.integration]

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "doc00"))
import _support as S  # noqa: E402  reuse pg_conn / apply_migrations / _local_dsn


def _require_schema(conn) -> None:
    for table in ("tenants", "meetings", "staged_drafts", "note_deltas"):
        if conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0] is None:
            r = S.apply_migrations(S._local_dsn() or "")
            assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"
            return


def _seed_meeting(conn, *, tenant_name: str) -> tuple[str, str]:
    tid = conn.execute(
        "INSERT INTO tenants (name) VALUES (%s) RETURNING id", (tenant_name,)
    ).fetchone()[0]
    mid = conn.execute(
        "INSERT INTO meetings (tenant_id, status) VALUES (%s, 'ended') RETURNING id",
        (tid,),
    ).fetchone()[0]
    return str(tid), str(mid)


def _signed_session_cookie(*, tenant: str, user: str) -> str:
    """A VALID signed session cookie the live SessionMiddleware accepts (doc08 recipe)."""
    import os
    from base64 import b64encode

    import itsdangerous

    secret = os.environ.get("SESSION_SECRET", "dev-only-unsigned")
    signer = itsdangerous.TimestampSigner(secret)
    payload = {"user": {"tenant_id": tenant, "user_id": user, "email": user}}
    data = b64encode(json.dumps(payload).encode("utf-8"))
    return signer.sign(data).decode("utf-8")


class _AsyncConnWrapper:
    """Wrap a sync psycopg conn as the ``async with db.acquire() as conn`` handle."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def acquire(self) -> "_AsyncConnWrapper":
        return self

    async def __aenter__(self) -> Any:
        return self._conn

    async def __aexit__(self, *exc: Any) -> None:
        return None


async def _call(server: Any, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Invoke a mounted tool through the REAL mcp CallToolRequest path."""
    import mcp.types as mt

    inst = server["instance"]
    handler = inst.request_handlers[mt.CallToolRequest]
    req = mt.CallToolRequest(
        method="tools/call", params=mt.CallToolRequestParams(name=tool_name, arguments=dict(args))
    )
    res = await handler(req)
    text = res.root.content[0].text
    if getattr(res.root, "isError", False):
        return {"__error__": text}
    return dict(json.loads(text))


@pytest.mark.integration
def test_full_arc_tool_proposes_real_row_nonowner_fenced_owner_applies() -> None:
    """propose (real MCP dispatch → real PG row) → non-owner accept REFUSED →
    owner accept APPLIES via the LIVE route — the whole draft→approve arc."""
    # Product first → a missing product is RED, a missing database is a skip.
    from control_plane import create_app
    from in_meeting.drafts_access import build_drafts_server
    from workroom import objectstore

    with S.pg_conn() as conn:
        _require_schema(conn)
        tenant, meeting = _seed_meeting(conn, tenant_name=f"t-{uuid.uuid4().hex[:8]}")
        other_tenant, _ = _seed_meeting(conn, tenant_name=f"t-{uuid.uuid4().hex[:8]}")

        # ── 1) The agent-side tool stages the draft via the REAL machinery ──────
        server = build_drafts_server(db=conn, meeting_id=meeting)
        assert server is not None, "a durable substrate must mount the drafts server"

        out = asyncio.run(
            _call(
                server,
                "propose_change",
                {
                    "kind": "code-change",
                    "summary": "raise the retry cap to 5",
                    "files": [{"path": "libs/http/client.py", "new_content": "RETRIES = 5\n"}],
                },
            )
        )
        assert "__error__" not in out, out
        draft_id = out["draft_id"]
        assert out["status"] == "needs_review"
        assert out["approve_url"] == f"/m/{meeting}/drafts/{draft_id}/accept"

        # The REAL durable row exists, bound to THIS meeting, still only proposed.
        row = conn.execute(
            "SELECT meeting_id, kind, status, artifact_ref FROM staged_drafts WHERE draft_id = %s",
            (draft_id,),
        ).fetchone()
        assert row is not None, "propose_change must persist a real staged_drafts row"
        assert str(row[0]) == meeting, "the draft must bind to the proposing meeting"
        assert row[1] == "code-change"
        assert row[2] == "proposed", "nothing may land before the human click"
        # The ONE durable bundle really holds the proposed change.
        body = objectstore.get(row[3])
        assert body is not None and "libs/http/client.py" in body

        # ── 2) The LIVE accept route on the ACTUAL app ──────────────────────────
        app = create_app()
        app.state.db = _AsyncConnWrapper(conn)
        client = TestClient(app)

        # A member of a DIFFERENT tenant is REFUSED — and NOTHING is applied.
        client.cookies.set(
            "session", _signed_session_cookie(tenant=other_tenant, user="mallory@other")
        )
        client.cookies.set("csrf_token", "csrf-arc")
        refused = client.post(
            out["approve_url"],
            headers={"X-CSRF-Token": "csrf-arc", "Idempotency-Key": "arc-refused"},
        )
        assert refused.status_code in (401, 403), refused.text
        assert refused.status_code != 200
        status = conn.execute(
            "SELECT status FROM staged_drafts WHERE draft_id = %s", (draft_id,)
        ).fetchone()[0]
        assert status == "proposed", "a fenced accept must leave the draft untouched"

        # The OWNING tenant's member accepts: approval recorded, bundle exposed,
        # NEVER pushed, row durably 'applied'.
        client.cookies.set("session", _signed_session_cookie(tenant=tenant, user="alice@t"))
        client.cookies.set("csrf_token", "csrf-arc2")
        accepted = client.post(
            out["approve_url"],
            headers={"X-CSRF-Token": "csrf-arc2", "Idempotency-Key": "arc-accept"},
        )
        assert accepted.status_code == 200, accepted.text
        payload = accepted.json()
        assert payload["accepted"] is True
        assert payload["kind"] == "code-change"
        assert payload["pushed"] is False, "core NEVER pushes — approval + bundle only"
        assert payload["bundle_url"], "the accepted code-change must expose its bundle handle"

        status = conn.execute(
            "SELECT status FROM staged_drafts WHERE draft_id = %s", (draft_id,)
        ).fetchone()[0]
        assert status == "applied", f"the human-accepted draft must be applied; got {status!r}"
