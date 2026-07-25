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

from uuid import uuid4

import pytest
from starlette.testclient import TestClient

from libs.http import PUBLIC_ROUTES, classify_route
from libs.http.src.http.registry import route_key
from libs.ops import encode_capability_token, mint_capability_token

_READ = "notes:read"
_ACCEPT = "POST /m/{meeting_id}/drafts/{draft_id}/accept"
_REJECT = "POST /m/{meeting_id}/drafts/{draft_id}/reject"


def _app():
    from control_plane import create_app

    return create_app()


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
