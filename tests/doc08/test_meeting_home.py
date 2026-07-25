"""Doc-08 §2.8 — the authenticated ``GET /m/{meeting_id}`` home (node
``experience.meeting-home-page``).

The per-meeting home is the flagship's missing home (F1 + F9): it renders the
§2.6 notes (folded server-side from ``note_deltas`` via the SAME
``read_notes`` fold ``/internal/notes`` uses, CANONICAL §11.4) PLUS that
meeting's ``staged_drafts`` cards (§2.4 #8) — for a signed-in tenant member.

Dual-mode (§4.6):

* a **signed-in tenant member** → notes + that meeting's staged-draft cards,
  but ONLY after a SERVER-SIDE ``meeting→tenant`` check (a cross-tenant member
  gets ``Not found`` — a client ``meeting_id`` is never trusted to authorize);
* a **forwarded-to recipient** with a valid capability token → notes-ONLY,
  **NO drafts** (Law 3: the token grants read-only notes, never a draft);
* **neither** (no session, no token, or a wrong-tenant session) → ``Not found``
  (404, the generic refusal — never a leak of whether the meeting exists).

These tests drive the framework-agnostic ``meeting_home_handler`` (the host-side
logic) AND the LIVE mounted ``GET /m/{meeting_id}`` route on the real
``create_app()`` app, so the DoD runs on the real path — not a mock of it.

NOT done if a token exposes drafts, if a cross-tenant member reads the meeting,
if notes are read from anywhere but the folded ``note_deltas`` reader, or if it
grows a cross-meeting list/analytics/history.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import uuid4

import pytest
from starlette.testclient import TestClient

from libs.ops import encode_capability_token, mint_capability_token

_READ = "notes:read"


# --------------------------------------------------------------------------- #
# A tiny in-memory asyncpg-shaped substrate: meetings + note_deltas +
# staged_drafts, honouring the tenant filter the real SQL applies. It is the
# SAME seam ``read_notes`` (load_deltas) and the drafts/meetings readers drive
# in production — only the row source is a fixture, the fold + the tenant check
# run for real.
# --------------------------------------------------------------------------- #
class _FakeConn:
    def __init__(self, store: "_Store") -> None:
        self._store = store

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        s = " ".join(sql.split()).lower()
        # meeting -> tenant check: SELECT ... FROM meetings WHERE id=$1 AND tenant_id=$2
        if "from meetings" in s and "tenant_id" in s:
            meeting_id, tenant_id = str(args[0]), str(args[1])
            m = self._store.meetings.get(meeting_id)
            if m is not None and str(m["tenant_id"]) == tenant_id:
                return dict(m)
            return None
        return None

    async def fetch(self, sql: str, *args: Any) -> Any:
        s = " ".join(sql.split()).lower()
        # note_deltas fold source (load_deltas)
        if "from note_deltas" in s:
            return list(self._store.deltas.get(str(args[0]), []))
        # staged_drafts for a meeting
        if "from staged_drafts" in s:
            return [dict(d) for d in self._store.drafts.get(str(args[0]), [])]
        return []


class _Acquire:
    def __init__(self, store: "_Store") -> None:
        self._store = store

    async def __aenter__(self) -> _FakeConn:
        return _FakeConn(self._store)

    async def __aexit__(self, *exc: Any) -> None:
        return None


class _FakeDB:
    def __init__(self, store: "_Store") -> None:
        self._store = store

    def acquire(self) -> _Acquire:
        return _Acquire(self._store)


class _Store:
    def __init__(self) -> None:
        self.meetings: dict[str, dict[str, Any]] = {}
        self.deltas: dict[str, list[dict[str, Any]]] = {}
        self.drafts: dict[str, list[dict[str, Any]]] = {}

    def add_meeting(self, meeting_id: str, tenant_id: str) -> None:
        self.meetings[meeting_id] = {
            "id": meeting_id,
            "tenant_id": tenant_id,
            "repo_id": None,
            "pinned_sha": None,
            "status": "ended",
        }

    def add_note(self, meeting_id: str, entry_id: str, text: str) -> None:
        self.deltas.setdefault(meeting_id, []).append(
            {
                "entry_id": entry_id,
                "op": "add",
                "payload": {"kind": "note", "text": text},
                "created_at": "2026-07-25T00:00:00Z",
            }
        )

    def add_draft(self, meeting_id: str, *, kind: str, summary: str) -> str:
        did = str(uuid4())
        self.drafts.setdefault(meeting_id, []).append(
            {
                "draft_id": did,
                "meeting_id": meeting_id,
                "kind": kind,
                "summary": summary,
                "artifact_ref": "gs://bucket/obj",
                "status": "proposed",
                "created_at": "2026-07-25T00:00:00Z",
            }
        )
        return did


def _run(coro: Any) -> Any:
    return asyncio.get_event_loop().run_until_complete(coro)


# --------------------------------------------------------------------------- #
# 1 · The host-side handler — the DoD, unit tier
# --------------------------------------------------------------------------- #
def test_session_member_sees_notes_and_that_meetings_drafts() -> None:
    """A signed-in tenant member sees the §2.6 notes + that meeting's staged-draft
    cards (§2.4 #8) — both present, folded from note_deltas."""
    from control_plane.meeting_home import meeting_home_handler

    store = _Store()
    m, tenant = str(uuid4()), str(uuid4())
    store.add_meeting(m, tenant)
    store.add_note(m, "e1", "ship Friday")
    store.add_draft(m, kind="notes-edit", summary="tighten the retry test")

    resp = _run(
        meeting_home_handler(
            m, session={"tenant_id": tenant, "user_id": "u@x"}, cap_grant=None,
            db=_FakeDB(store),
        )
    )
    assert resp.status_code == 200, resp.body
    body = json.loads(resp.body)
    assert body["entries"] and body["entries"][0]["text"] == "ship Friday"
    assert len(body["drafts"]) == 1
    assert body["drafts"][0]["summary"] == "tighten the retry test"


def test_cross_tenant_member_gets_not_found() -> None:
    """A member of tenant B opening tenant A's meeting → Not found (404): the
    server-side meeting→tenant check refuses; a client meeting_id is never
    trusted to authorize the entity."""
    from control_plane.meeting_home import meeting_home_handler

    store = _Store()
    m, tenant_a, tenant_b = str(uuid4()), str(uuid4()), str(uuid4())
    store.add_meeting(m, tenant_a)
    store.add_note(m, "e1", "secret")
    store.add_draft(m, kind="code-change", summary="secret change")

    resp = _run(
        meeting_home_handler(
            m, session={"tenant_id": tenant_b, "user_id": "b@x"}, cap_grant=None,
            db=_FakeDB(store),
        )
    )
    assert resp.status_code == 404
    # Absolutely no tenant-A data leaks in the refusal body.
    assert resp.body is None or "secret" not in resp.body


def test_token_grant_is_notes_only_no_drafts() -> None:
    """A capability-token grant → notes only, NEVER drafts (Law 3)."""
    from control_plane.meeting_home import meeting_home_handler

    store = _Store()
    m, tenant = str(uuid4()), str(uuid4())
    store.add_meeting(m, tenant)
    store.add_note(m, "e1", "public-facing note")
    store.add_draft(m, kind="notes-edit", summary="MUST NOT LEAK")

    resp = _run(
        meeting_home_handler(
            m, session=None, cap_grant=True, db=_FakeDB(store)
        )
    )
    assert resp.status_code == 200, resp.body
    body = json.loads(resp.body)
    assert body["entries"], "the token bearer still reads the notes"
    assert body["drafts"] == [], "a token bearer NEVER receives drafts"
    assert "MUST NOT LEAK" not in resp.body


def test_no_session_no_token_is_not_found() -> None:
    """Neither a session nor a token → Not found (404)."""
    from control_plane.meeting_home import meeting_home_handler

    store = _Store()
    m, tenant = str(uuid4()), str(uuid4())
    store.add_meeting(m, tenant)
    store.add_note(m, "e1", "note")

    resp = _run(
        meeting_home_handler(m, session=None, cap_grant=None, db=_FakeDB(store))
    )
    assert resp.status_code == 404


def test_session_without_tenant_is_not_found() -> None:
    """A session with no tenant (a half-provisioned principal) cannot read a
    meeting — Not found, never a query widened to every tenant."""
    from control_plane.meeting_home import meeting_home_handler

    store = _Store()
    m = str(uuid4())
    store.add_meeting(m, str(uuid4()))
    store.add_note(m, "e1", "note")

    resp = _run(
        meeting_home_handler(
            m, session={"user_id": "u@x", "tenant_id": None}, cap_grant=None,
            db=_FakeDB(store),
        )
    )
    assert resp.status_code == 404


def test_unknown_meeting_for_a_member_is_not_found() -> None:
    """A member opening a meeting id that does not exist → Not found (404)."""
    from control_plane.meeting_home import meeting_home_handler

    store = _Store()
    tenant = str(uuid4())
    resp = _run(
        meeting_home_handler(
            str(uuid4()), session={"tenant_id": tenant, "user_id": "u@x"},
            cap_grant=None, db=_FakeDB(store),
        )
    )
    assert resp.status_code == 404


def test_handler_never_throws_on_db_failure() -> None:
    """A substrate failure degrades to an honest 5xx (never a fabricated 200 and
    never an unhandled throw — the never-throw boundary)."""
    from control_plane.meeting_home import meeting_home_handler

    class _BoomAcquire:
        async def __aenter__(self) -> Any:
            raise RuntimeError("db down")

        async def __aexit__(self, *exc: Any) -> None:
            return None

    class _BoomDB:
        def acquire(self) -> Any:
            return _BoomAcquire()

    m, tenant = str(uuid4()), str(uuid4())
    resp = _run(
        meeting_home_handler(
            m, session={"tenant_id": tenant, "user_id": "u@x"}, cap_grant=None,
            db=_BoomDB(),
        )
    )
    assert resp.status_code == 503
    assert resp.body is None or "entries" not in (resp.body or "")


# --------------------------------------------------------------------------- #
# 2 · The LIVE mounted route — the same DoD over the real create_app() app
# --------------------------------------------------------------------------- #
def _live_app(monkeypatch: pytest.MonkeyPatch, store: _Store) -> Any:
    """The real control_plane app with the fake substrate bound to app.state.db,
    and the note_deltas loader pointed at the in-memory store via ``monkeypatch``
    (auto-restored after the test — NO global-state leak into any later test). The
    fold runs for real; only the row source is a fixture."""
    from control_plane import create_app
    import scribe.notes_reader as nr

    app = create_app()
    app.state.db = _FakeDB(store)

    async def _load(_conn: Any, mid: Any) -> Any:
        return list(store.deltas.get(str(mid), []))

    monkeypatch.setattr(nr, "_default_loader", lambda: _load)
    return app


def test_live_route_token_reads_notes_only_no_drafts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LIVE: a valid same-meeting token → 200 notes, NO drafts card leaks."""
    store = _Store()
    m, tenant = str(uuid4()), str(uuid4())
    store.add_meeting(m, tenant)
    store.add_note(m, "e1", "note")
    store.add_draft(m, kind="notes-edit", summary="LEAK-CANARY")

    app = _live_app(monkeypatch, store)
    tok = mint_capability_token(meeting_id=m, scope=_READ, ttl_seconds=300)
    client = TestClient(app)
    resp = client.get(f"/m/{m}", params={"token": encode_capability_token(tok)})
    assert resp.status_code == 200, resp.text
    assert "LEAK-CANARY" not in resp.text
    body = resp.json()
    assert body["drafts"] == []


def test_live_route_no_session_no_token_is_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LIVE: no session and no token → Not found (404), the generic refusal."""
    store = _Store()
    m = str(uuid4())
    store.add_meeting(m, str(uuid4()))
    store.add_note(m, "e1", "note")
    app = _live_app(monkeypatch, store)
    client = TestClient(app)
    assert client.get(f"/m/{m}").status_code == 404


def test_live_route_is_public_only_via_token() -> None:
    """LIVE: the route earns its public exemption via the scoped token only."""
    from libs.http import PUBLIC_ROUTES

    assert "GET /m/{meeting_id}" in PUBLIC_ROUTES
