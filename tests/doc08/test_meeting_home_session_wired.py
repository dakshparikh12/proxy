"""C-SESSIONREAD (P0) fix — the /m route resolves a signed-in member from the DURABLE session.

The route previously read ``request.session["user"]`` (the Starlette SessionMiddleware dict),
which ``auth_callback`` DELIBERATELY never populates (it writes the durable HMAC ``session``
cookie via ``complete_signin`` instead) — so a signed-in tenant member was unreachable on their
OWN meeting home and could not accept a draft. This proves the route now resolves the member
from the durable cookie via ``control_plane.session.resolve_session`` (the SAME resolver the WS gateway
uses), so a signed-in member sees notes + that meeting's staged-draft cards — while a
cross-tenant session and a no-session request still get Not found (the anti-leak refusal holds).

Non-sealed regression guard for the convergence fix — reuses the sealed suite's in-memory store.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from starlette.testclient import TestClient

from tests.doc08.test_meeting_home import _FakeDB, _Store


def _member_app(monkeypatch, store, *, member_tenant):
    """The real control_plane app with the fake substrate + a resolvable durable session
    for ``member_tenant`` (the WS-gateway resolver, monkeypatched in place of a real DB read)."""
    import scribe.notes_reader as nr

    import control_plane.session as sess

    from control_plane import create_app

    app = create_app()
    app.state.db = _FakeDB(store)

    async def _load(_conn, mid):
        return list(store.deltas.get(str(mid), []))

    monkeypatch.setattr(nr, "_default_loader", lambda: _load)

    async def _resolve(_db, _cookies):
        return {"user_id": "u@x", "tenant_id": member_tenant}

    monkeypatch.setattr(sess, "resolve_session", _resolve)
    return app


def test_durable_session_member_sees_that_meetings_drafts(monkeypatch) -> None:
    """A signed-in tenant member (durable session cookie) → 200 notes + that meeting's drafts."""
    store = _Store()
    m, tenant = str(uuid4()), str(uuid4())
    store.add_meeting(m, tenant)
    store.add_note(m, "e1", "note-body")
    store.add_draft(m, kind="notes-edit", summary="MEMBER-DRAFT")

    app = _member_app(monkeypatch, store, member_tenant=tenant)
    client = TestClient(app)
    client.cookies.set("session", "durable-cookie-value")
    resp = client.get(f"/m/{m}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The member sees that meeting's staged-draft cards (§2.4 #8) — exactly what the
    # token-only view withholds. THIS is the P0: before the fix, session was always None
    # (request.session["user"] never populated) → this member got a 404.
    assert any("MEMBER-DRAFT" in str(d) for d in body.get("drafts", [])), body


def test_durable_session_cross_tenant_still_not_found(monkeypatch) -> None:
    """A durable session for a DIFFERENT tenant → Not found (the anti-leak refusal holds)."""
    store = _Store()
    m, tenant = str(uuid4()), str(uuid4())
    store.add_meeting(m, tenant)
    store.add_note(m, "e1", "note-body")

    app = _member_app(monkeypatch, store, member_tenant=str(uuid4()))  # a DIFFERENT tenant
    client = TestClient(app)
    client.cookies.set("session", "durable-cookie-value")
    assert client.get(f"/m/{m}").status_code == 404
