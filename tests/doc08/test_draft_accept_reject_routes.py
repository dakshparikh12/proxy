"""Doc-08 §2.8 / §4.6 / CANONICAL §12.9 — the draft accept + reject routes are LIVE.

``POST /m/{meeting_id}/drafts/{draft_id}/accept`` and ``.../reject`` are the ONE
world-touching pair (Law 3). This exercises the ACTUAL mounted routes on the ACTUAL
``create_app()`` app via ``TestClient``, proving on the real path that BOTH routes:

* are ``protected()``-only — NEVER in ``PUBLIC_ROUTES``, and a capability token
  cannot reach either (401/403, never 200);
* are classified ``protected`` by the route-enumeration machinery (they declare the
  §4.6 ``protected()`` wrapper, so a fail-closed 401/403 fires server-side before the
  handler) — not ``raw``, not ``public``;
* refuse an anonymous (no-session) caller (401);

and that the reject route is symmetric to accept (same wall, same shape).

The handler layer's tenant/CSRF/idempotency/audit is proven directly against durable
Postgres in ``tests/doc04/test_{accept,reject}_handler_durable.py``; this file proves
the WIRING — the routes exist on the live app, are protected, and no token reaches them.
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import sys
import uuid
from base64 import b64encode
from typing import Any
from uuid import uuid4

import pytest
from starlette.testclient import TestClient

from libs.http import PUBLIC_ROUTES, classify_route
from libs.http.src.http.registry import route_key
from libs.ops import encode_capability_token, mint_capability_token

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "doc00"))
import _support as S  # noqa: E402  reuse pg_conn / apply_migrations / _local_dsn

_READ = "notes:read"
_ACCEPT = "POST /m/{meeting_id}/drafts/{draft_id}/accept"
_REJECT = "POST /m/{meeting_id}/drafts/{draft_id}/reject"

# The live audit channel the mounted accept/reject routes record onto (structured
# log line = the durable audit trail in prod / Cloud Logging). The live-path audit
# test asserts a record lands HERE on a real green POST — the property the DoD names
# as a hard requirement ("a world-touching action is recorded").
_AUDIT_LOGGER = "services.harness.control_plane.audit"


def _app():
    from control_plane import create_app

    return create_app()


def _signed_session_cookie(*, tenant: str, user: str) -> str:
    """A VALID signed session cookie the live SessionMiddleware will accept.

    Signs ``{"user": {tenant_id, email}}`` with the SAME ``SESSION_SECRET`` the live
    ``create_app()`` uses, so the mounted ``protected()`` wall + the route's own
    ``_principal_and_key`` both read the acting tenant SERVER-SIDE off the signed
    session (never a client body field) and the POST reaches the handler on the real
    path. Mirrors Starlette's SessionMiddleware cookie format exactly.
    """
    import itsdangerous

    secret = os.environ.get("SESSION_SECRET", "dev-only-unsigned")
    signer = itsdangerous.TimestampSigner(secret)
    payload = {"user": {"tenant_id": tenant, "user_id": user, "email": user}}
    data = b64encode(json.dumps(payload).encode("utf-8"))
    return signer.sign(data).decode("utf-8")


class _AsyncConnWrapper:
    """Wrap a sync psycopg conn as the ``async with db.acquire() as conn`` handle the
    live route borrows — the kind-aware apply runs synchronous psycopg SQL on it."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def acquire(self) -> "_AsyncConnWrapper":
        return self

    async def __aenter__(self) -> Any:
        return self._conn

    async def __aexit__(self, *exc: Any) -> None:
        return None


def _seed_meeting(conn, *, tenant_name: str) -> tuple[str, str]:
    tid = conn.execute(
        "INSERT INTO tenants (name) VALUES (%s) RETURNING id", (tenant_name,)
    ).fetchone()[0]
    mid = conn.execute(
        "INSERT INTO meetings (tenant_id, status) VALUES (%s, 'ended') RETURNING id",
        (tid,),
    ).fetchone()[0]
    return str(tid), str(mid)


def _seed_draft(conn, *, meeting_id: str, kind: str, body: str) -> str:
    from workroom import objectstore

    artifact_ref = f"gs://proxy-drafts/{meeting_id}/{uuid.uuid4().hex}"
    objectstore.put(artifact_ref, body)
    did = conn.execute(
        """
        INSERT INTO staged_drafts (meeting_id, kind, summary, artifact_ref, status)
        VALUES (%s, %s, %s, %s, 'proposed')
        RETURNING draft_id
        """,
        (meeting_id, kind, f"{kind} summary", artifact_ref),
    ).fetchone()[0]
    return str(did)


def _require_schema(conn) -> None:
    for table in ("tenants", "meetings", "staged_drafts", "note_deltas"):
        if conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0] is None:
            r = S.apply_migrations(S._local_dsn() or "")
            assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"
            return


def _route_for(app, key: str):
    for r in app.routes:
        if route_key(r) == key:
            return r
    return None


def test_accept_and_reject_are_not_public() -> None:
    """Neither mutation is on the PUBLIC_ROUTES allowlist (§4.6, Law 3)."""
    assert _ACCEPT not in PUBLIC_ROUTES, "accept must never be allowlisted public"
    assert _REJECT not in PUBLIC_ROUTES, "reject must never be allowlisted public"


def test_both_routes_are_mounted_and_classified_protected() -> None:
    """Both routes exist on the live app AND classify as ``protected`` (not raw)."""
    app = _app()
    for key in (_ACCEPT, _REJECT):
        route = _route_for(app, key)
        assert route is not None, f"{key} is not mounted on control_plane"
        verdict = classify_route(route)
        assert verdict == "protected", f"{key} classified {verdict!r}, expected protected"


def test_anonymous_caller_cannot_accept_or_reject() -> None:
    """No session ⇒ the ``protected()`` wall fires 401/403 before the handler."""
    app = _app()
    client = TestClient(app)
    m, draft = str(uuid4()), str(uuid4())
    for verb_path in (f"/m/{m}/drafts/{draft}/accept", f"/m/{m}/drafts/{draft}/reject"):
        resp = client.post(verb_path)
        assert resp.status_code in (401, 403), (
            f"anonymous POST {verb_path} must be refused, got {resp.status_code}"
        )
        assert resp.status_code != 200


def test_capability_token_cannot_reach_accept_or_reject() -> None:
    """A valid read token is notes-only — it can NEVER accept OR reject (Law 3)."""
    app = _app()
    client = TestClient(app)
    m, draft = str(uuid4()), str(uuid4())
    tok = mint_capability_token(meeting_id=m, scope=_READ, ttl_seconds=300)
    token_str = encode_capability_token(tok)

    for verb_path in (f"/m/{m}/drafts/{draft}/accept", f"/m/{m}/drafts/{draft}/reject"):
        # As a query param AND as a bearer header — neither reaches a protected() route.
        r1 = client.post(verb_path, params={"token": token_str})
        r2 = client.post(verb_path, headers={"Authorization": f"Bearer {token_str}"})
        assert r1.status_code != 200, f"a token must NEVER reach {verb_path}"
        assert r2.status_code != 200, f"a token must NEVER reach {verb_path}"
        assert r1.status_code in (401, 403)
        assert r2.status_code in (401, 403)


def test_no_raw_draft_mutation_route_exists() -> None:
    """No accept/reject variant is classified ``raw`` (unwrapped + not allowlisted)."""
    app = _app()
    raw = [
        route_key(r)
        for r in app.routes
        if classify_route(r) == "raw"
        and "/drafts/" in (route_key(r) or "")
    ]
    assert raw == [], f"draft mutation routes must never be raw: {raw}"


# ── the LIVE mounted route AUDITS a real green POST (the hard DoD requirement) ──────
@pytest.mark.integration
def test_live_accept_route_audits_a_real_green_post(caplog: pytest.LogCaptureFixture) -> None:
    """A REAL authenticated CSRF-valid accept POST on the ACTUAL ``create_app()`` app
    lands an audit record naming the acting tenant member — audit fires on the LIVE
    route path, not only when a caller hand-passes an audit_sink (§2.8, CANONICAL §12.9).

    This binds the property the DoD names 'a hard requirement' on the real path: if the
    live mount stopped auditing, this test goes RED. We drive the mounted route via
    TestClient with a signed session (server-side tenant) + double-submit CSRF, backed by
    a real durable Postgres row, and assert the audit channel captured the world-touching act.
    """
    with S.pg_conn() as conn:
        _require_schema(conn)
        tenant, meeting = _seed_meeting(conn, tenant_name=f"t-{uuid.uuid4().hex[:8]}")
        draft = _seed_draft(conn, meeting_id=meeting, kind="notes-edit", body="edited notes")

        app = _app()
        app.state.db = _AsyncConnWrapper(conn)
        client = TestClient(app)
        client.cookies.set("session", _signed_session_cookie(tenant=tenant, user="carol@t"))
        client.cookies.set("csrf_token", "csrf-abc")

        with caplog.at_level(logging.INFO, logger=_AUDIT_LOGGER):
            resp = client.post(
                f"/m/{meeting}/drafts/{draft}/accept",
                headers={"X-CSRF-Token": "csrf-abc", "Idempotency-Key": "live-audit-1"},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json().get("accepted") is True

        audited = [r.getMessage() for r in caplog.records if r.name == _AUDIT_LOGGER]
        assert audited, "the LIVE accept route must emit an audit record on a real green POST"
        rec = " ".join(audited)
        assert "accept" in rec and str(tenant) in rec and "carol@t" in rec and str(draft) in rec, (
            f"the live audit record must name the acting tenant member + draft; got {audited!r}"
        )
        # And it durably applied (proof the green POST really ran the world-touching path).
        status = conn.execute(
            "SELECT status FROM staged_drafts WHERE draft_id = %s", (draft,)
        ).fetchone()[0]
        assert status == "applied", f"the audited accept must have applied; got {status!r}"


@pytest.mark.integration
def test_live_reject_route_audits_a_real_green_post(caplog: pytest.LogCaptureFixture) -> None:
    """The reject twin: a REAL authenticated CSRF-valid reject POST on the ACTUAL
    ``create_app()`` app lands an audit record — symmetric to accept, on the live path."""
    with S.pg_conn() as conn:
        _require_schema(conn)
        tenant, meeting = _seed_meeting(conn, tenant_name=f"t-{uuid.uuid4().hex[:8]}")
        draft = _seed_draft(conn, meeting_id=meeting, kind="notes-edit", body="body")

        app = _app()
        app.state.db = _AsyncConnWrapper(conn)
        client = TestClient(app)
        client.cookies.set("session", _signed_session_cookie(tenant=tenant, user="dave@t"))
        client.cookies.set("csrf_token", "csrf-xyz")

        with caplog.at_level(logging.INFO, logger=_AUDIT_LOGGER):
            resp = client.post(
                f"/m/{meeting}/drafts/{draft}/reject",
                headers={"X-CSRF-Token": "csrf-xyz", "Idempotency-Key": "live-audit-r"},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json().get("rejected") is True

        audited = [r.getMessage() for r in caplog.records if r.name == _AUDIT_LOGGER]
        assert audited, "the LIVE reject route must emit an audit record on a real green POST"
        rec = " ".join(audited)
        assert "reject" in rec and str(tenant) in rec and "dave@t" in rec and str(draft) in rec, (
            f"the live audit record must name the acting tenant member + draft; got {audited!r}"
        )
