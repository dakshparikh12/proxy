"""Doc-08 §2.8 / §4.6 — the capability-token read path is LIVE on control_plane.

``GET /m/{meeting_id}`` is dual-mode (§4.6): a signed-in tenant member takes it
``protected()``-equivalent; a forwarded-to recipient takes it PUBLIC with a valid
capability token, seeing **read-only notes — NO drafts, never accept/reject**.

These tests exercise the ACTUAL mounted route on the ACTUAL ``create_app()`` app
via ``TestClient``, proving on the real path:

* a valid same-meeting token reads the notes (200) and gets NO drafts;
* a wrong-meeting / expired / tampered / revoked token is refused (404 Not found)
  — the same generic "Not found" a no-session no-token caller gets;
* the accept/reject mutations are NOT reachable by any token (they are on
  separate ``protected()`` routes, not the ``/m/{meeting_id}`` read route);
* ``GET /m/{meeting_id}`` is in the PUBLIC_ROUTES allowlist (its only public entry
  earns its exemption via the scoped token — §4.6).

The notes-fold loader is patched via pytest's ``monkeypatch`` (auto-restored after
each test) so the route runs hermetically (no live Postgres) WITHOUT leaking global
module state into any later test — the fold-from-``note_deltas`` contract still runs
for real, only the row source is a fixture.
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from starlette.testclient import TestClient

from libs.http import PUBLIC_ROUTES

# Import via the ``libs.ops`` facade — the SAME module identity the live route uses
# (``from libs.ops import verify_capability_token``). Minting here and verifying on
# the route therefore share ONE process-global signing-key + revoked-set, so a token
# this test mints is honoured (and, once revoked, refused) by the real route.
from libs.ops import (
    encode_capability_token,
    mint_capability_token,
    revoke_capability_token,
)

_READ = "notes:read"


def _app_with_notes(monkeypatch: pytest.MonkeyPatch, meeting_id: str) -> Any:
    """Build the LIVE control_plane app whose notes fold returns a non-empty object
    for exactly ``meeting_id`` (→ 200) and empty for every other (→ 404).

    Patches ``scribe.notes_reader._default_loader`` via ``monkeypatch`` so the swap
    is auto-restored after the test — no global-state leak into later tests.
    """
    from control_plane import create_app

    app = create_app()

    class _Conn:
        async def fetch(self, *a: Any, **k: Any) -> Any:
            return []

        async def fetchval(self, *a: Any, **k: Any) -> Any:
            return None

    class _Acquire:
        async def __aenter__(self) -> "_Conn":
            return _Conn()

        async def __aexit__(self, *exc: Any) -> None:
            return None

    class _DB:
        def acquire(self) -> "_Acquire":
            return _Acquire()

    app.state.db = _DB()

    async def _load(_conn: Any, mid: Any) -> Any:
        # The real ``note_deltas`` row shape the fold consumes (entry_id/op/payload/
        # created_at). A single "add" row makes the known meeting a non-empty notes
        # object (delta_count 1 → 200); every other meeting folds empty (→ 404).
        if str(mid) == meeting_id:
            return [
                {
                    "entry_id": "e1",
                    "op": "add",
                    "payload": {"kind": "note", "text": "hello"},
                    "created_at": "2026-07-25T00:00:00Z",
                }
            ]
        return []

    import scribe.notes_reader as nr

    monkeypatch.setattr(nr, "_default_loader", lambda: _load)
    return app


def test_get_m_route_is_public_only_with_a_token() -> None:
    """The read route earns its public exemption via the scoped token — it must be
    in the PUBLIC_ROUTES allowlist (§4.6)."""
    assert "GET /m/{meeting_id}" in PUBLIC_ROUTES


def test_valid_token_reads_notes_and_gets_no_drafts(monkeypatch: pytest.MonkeyPatch) -> None:
    """A forwarded-to recipient with a valid same-meeting token sees the notes
    (200) and NO drafts — token = notes only (§4.6)."""
    m = str(uuid4())
    app = _app_with_notes(monkeypatch, m)
    tok = mint_capability_token(meeting_id=m, scope=_READ, ttl_seconds=300)
    token_str = encode_capability_token(tok)

    client = TestClient(app)
    resp = client.get(f"/m/{m}", params={"token": token_str})
    assert resp.status_code == 200, resp.text
    # A token bearer NEVER receives drafts. The read fold carries notes entries; a
    # non-empty draft array would be the leak this asserts against.
    body = resp.text.lower()
    assert '"drafts": [' not in body and '"drafts":[' not in body or '"drafts": []' in body or '"drafts":[]' in body


def test_wrong_meeting_token_cannot_read_notes(monkeypatch: pytest.MonkeyPatch) -> None:
    """A token minted for meeting A presented at meeting B → Not found (404)."""
    a, b = str(uuid4()), str(uuid4())
    app = _app_with_notes(monkeypatch, b)  # b has notes
    tok_a = mint_capability_token(meeting_id=a, scope=_READ, ttl_seconds=300)

    client = TestClient(app)
    resp = client.get(f"/m/{b}", params={"token": encode_capability_token(tok_a)})
    assert resp.status_code == 404, f"wrong-meeting token must not read B; got {resp.status_code}"


def test_expired_token_cannot_read_notes(monkeypatch: pytest.MonkeyPatch) -> None:
    """An expired token → Not found (404) — same as no token at all."""
    m = str(uuid4())
    app = _app_with_notes(monkeypatch, m)
    past = mint_capability_token(meeting_id=m, scope=_READ, ttl_seconds=-1)

    client = TestClient(app)
    resp = client.get(f"/m/{m}", params={"token": encode_capability_token(past)})
    assert resp.status_code == 404


def test_tampered_token_cannot_read_notes(monkeypatch: pytest.MonkeyPatch) -> None:
    """A tampered signature → Not found (404), never a 200 or a 500."""
    m = str(uuid4())
    app = _app_with_notes(monkeypatch, m)
    tok = mint_capability_token(meeting_id=m, scope=_READ, ttl_seconds=300)
    tampered = encode_capability_token(tok)[:-6] + "000000"

    client = TestClient(app)
    resp = client.get(f"/m/{m}", params={"token": tampered})
    assert resp.status_code == 404


def test_revoked_token_cannot_read_notes(monkeypatch: pytest.MonkeyPatch) -> None:
    """A revoked (but otherwise valid) token → Not found (404): revocation bites on
    the LIVE read path, not merely in the primitive."""
    m = str(uuid4())
    app = _app_with_notes(monkeypatch, m)
    tok = mint_capability_token(meeting_id=m, scope=_READ, ttl_seconds=300)
    token_str = encode_capability_token(tok)

    client = TestClient(app)
    assert client.get(f"/m/{m}", params={"token": token_str}).status_code == 200
    revoke_capability_token(tok)
    assert client.get(f"/m/{m}", params={"token": token_str}).status_code == 404


def test_no_token_no_session_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neither a token nor a session → Not found (404) — the public entry is the
    token ONLY."""
    m = str(uuid4())
    app = _app_with_notes(monkeypatch, m)
    client = TestClient(app)
    assert client.get(f"/m/{m}").status_code == 404


def test_token_never_reaches_accept_or_reject_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The mutations are on SEPARATE protected() routes; a token cannot POST an
    accept. The read route grants notes only (Law 3, human control is absolute)."""
    m = str(uuid4())
    draft = str(uuid4())
    app = _app_with_notes(monkeypatch, m)
    tok = mint_capability_token(meeting_id=m, scope=_READ, ttl_seconds=300)
    token_str = encode_capability_token(tok)

    client = TestClient(app)
    # Even carrying a valid read token as a query param, the accept mutation is not
    # reachable — it is a protected() route with no token entry (401/403/404, never 200).
    resp = client.post(f"/m/{m}/drafts/{draft}/accept", params={"token": token_str})
    assert resp.status_code != 200, "a capability token must NEVER accept a draft"
