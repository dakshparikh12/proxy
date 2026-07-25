"""ASGI-mount oracle for CROSS-SESSION-READ — the endpoint MUST be LIVE.

The sibling ``test_csread_db`` / ``_static`` / ``_fault`` tiers prove the READER
(``scribe.notes_reader``) is correct against the real note_deltas fold. But the
node's definition-of-done adds a distinct obligation the reader tests cannot see:
the handler must be **REGISTERED into a running ASGI app, outside the auth wall,
alongside /internal/reconcile** — "NOT done if the handler is defined but NOT
mounted into any app (the current gap)".

This module drives the REAL ``services.control_plane`` ASGI app through Starlette's
``TestClient`` and asserts the live route surface:

* ``GET /internal/notes/{meeting_id}`` is MOUNTED and token-gated by the internal
  bearer (``X-Internal-Token``) — 401 without it, a real note_deltas fold with it
  (200 known / 404 unknown / 503 db-down), and a user session cookie is NEVER a
  credential here (mounted OUTSIDE the auth wall, alongside /internal/reconcile).
* ``GET /m/{meeting_id}`` is MOUNTED behind the auth wall and reads the SAME fold.
* ``POST /internal/reconcile`` — the sibling this mounts alongside — is also live,
  so the two internal routes share the one internal route group.

The db fold runs for REAL: ``app.state.db`` is set to a live asyncpg pool over the
test note_deltas DB (the exact seam ``read_notes`` drives in production). No mock.
Env-gated on the same DOC03_STORE_SPEC_DB + TEST_DATABASE_URL opt-in as the db tier.
"""
from __future__ import annotations

import json
import os
import uuid

import pytest

from .conftest import DsnAcquirer, seed_delta

_HAVE_TEST_DB = bool(os.environ.get("TEST_DATABASE_URL", "").strip())
_SPEC_SCHEMA_AVAILABLE = bool(os.environ.get("DOC03_STORE_SPEC_DB", "").strip())

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (_HAVE_TEST_DB and _SPEC_SCHEMA_AVAILABLE),
        reason=(
            "db integration tier: set TEST_DATABASE_URL to a Postgres carrying "
            "the section 3.3 note_deltas schema AND set DOC03_STORE_SPEC_DB to opt in"
        ),
    ),
]

GOOD_TOKEN = "internal-token-good"  # module default when PROXY_INTERNAL_TOKEN is unset
INTERNAL_HEADER = "X-Internal-Token"


def _client(app, db):
    """A Starlette TestClient over the real app with the live db handle bound."""
    from starlette.testclient import TestClient

    app.state.db = db
    return TestClient(app)


@pytest.fixture()
def app_and_client():
    """The real control_plane app with a LIVE, loop-agnostic db handle bound.

    ``DsnAcquirer`` opens a fresh real asyncpg connection inside the TestClient's
    own event loop (the sync-portal thread) — the same loop-affinity production
    gets from a lifespan-opened pool. Still the real note_deltas seam, no stub.
    """
    from services.control_plane.app import create_app

    app = create_app()
    db = DsnAcquirer(os.environ["TEST_DATABASE_URL"])
    with _client(app, db) as client:
        yield app, client


# -- The mount itself: /internal/notes is a LIVE route on the app --------------
def test_csread_mount_internal_notes_route_is_registered(app_and_client) -> None:
    app, _ = app_and_client
    paths = {getattr(r, "path", None) for r in app.routes}
    # The two internal routes are mounted alongside each other in the /internal group.
    assert "/internal/notes/{meeting_id}" in paths, (
        f"/internal/notes/{{meeting_id}} not mounted into the ASGI app; routes={sorted(p for p in paths if p)}"
    )
    assert "/internal/reconcile" in paths, "the sibling /internal/reconcile must be mounted too"
    # /m/{meeting_id} authenticated user surface is mounted as well.
    assert "/m/{meeting_id}" in paths, "/m/{meeting_id} user surface not mounted"


# -- Token gate on the LIVE route: 401 without the internal bearer -------------
def test_csread_mount_missing_token_is_401(app_and_client) -> None:
    _, client = app_and_client
    resp = client.get(f"/internal/notes/{uuid.uuid4()}")
    assert resp.status_code == 401


def test_csread_mount_bad_token_is_401(app_and_client) -> None:
    _, client = app_and_client
    resp = client.get(
        f"/internal/notes/{uuid.uuid4()}", headers={INTERNAL_HEADER: "wrong-token"}
    )
    assert resp.status_code == 401


def test_csread_mount_session_cookie_alone_is_denied(app_and_client) -> None:
    # A user session cookie is NEVER a credential on the internal reader (outside
    # the auth wall). No internal token header => 401 even with a cookie set.
    _, client = app_and_client
    client.cookies.set("session", "some-user-session-cookie")
    resp = client.get(f"/internal/notes/{uuid.uuid4()}")
    assert resp.status_code == 401


# -- Real fold over the LIVE route: 200 known, 404 unknown ---------------------
@pytest.mark.asyncio
async def test_csread_mount_known_meeting_is_200_with_real_fold(app_and_client, pool) -> None:
    app, client = app_and_client
    meeting_id = uuid.uuid4()
    async with pool.acquire() as conn:
        await seed_delta(conn, meeting_id=meeting_id, entry_id="E1", op="add",
                         payload={"text": "mounted-and-folded"}, window_start_s=0.0)
    resp = client.get(
        f"/internal/notes/{meeting_id}", headers={INTERNAL_HEADER: GOOD_TOKEN}
    )
    assert resp.status_code == 200, resp.text
    parsed = json.loads(resp.text)
    assert parsed["entries"][0]["text"] == "mounted-and-folded"
    assert "freshness_flag" in parsed


def test_csread_mount_unknown_meeting_is_404(app_and_client) -> None:
    app, client = app_and_client
    # Guard against a false pass: a route-not-mounted 404 must not masquerade as a
    # valid unknown-meeting 404. The route MUST be registered first.
    assert "/internal/notes/{meeting_id}" in {getattr(r, "path", None) for r in app.routes}
    resp = client.get(
        f"/internal/notes/{uuid.uuid4()}", headers={INTERNAL_HEADER: GOOD_TOKEN}
    )
    assert resp.status_code == 404


# -- Honest degradation over the LIVE route: db down => 503, never a fake 200 --
def test_csread_mount_db_outage_is_503() -> None:
    from starlette.testclient import TestClient

    from services.control_plane.app import create_app

    from .conftest import FaultAcquirer

    app = create_app()
    app.state.db = FaultAcquirer()
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get(
            f"/internal/notes/{uuid.uuid4()}", headers={INTERNAL_HEADER: GOOD_TOKEN}
        )
    assert resp.status_code == 503
    # A 503 must not carry a fabricated notes object.
    body = resp.text or ""
    assert '"entries"' not in body


# -- /m/{meeting_id} user surface is behind the auth wall on the LIVE app ------
def test_csread_mount_m_surface_requires_session(app_and_client) -> None:
    # No session => the user surface denies (401), independent of the internal token.
    _, client = app_and_client
    resp = client.get(f"/m/{uuid.uuid4()}")
    assert resp.status_code == 401
