"""The MONITORED-smoke taps — direct provision (skip OAuth) + the HEARD transcript read.

``POST /admin/test-provision`` drives the REAL ``invite_proxy`` (real Recall bot + the drain's real
E2B workroom) WITHOUT the Google-OAuth wall, so a headless smoke can put Proxy into a Meet. ``GET
/admin/transcript`` surfaces the live meeting's sandbox ``MEETING_NOTES.md`` (the HEARD capture).
Both are gated by the internal admin bearer (constant-time), stamped internal-scoped, and fail CLOSED
when the token is unset. These prove:

  * an AUTHENTICATED test-provision binds the test tenant + repo and calls the real invite path;
  * an unauthenticated (missing/wrong token) call is refused (401) and NO invite is attempted;
  * the transcript tap reads the live meeting's workroom notes (and honest-empties on no runtime);
  * both routes classify internal (not raw) for the §4.6 enumeration gate.
"""
from __future__ import annotations

import inspect
from typing import Any

from starlette.testclient import TestClient


class _FakeConn:
    """An asyncpg-conn stand-in: records executes, returns a repo row on the upsert fetchrow."""

    def __init__(self, sink: dict[str, Any]) -> None:
        self._sink = sink

    async def execute(self, sql: str, *args: Any) -> str:
        self._sink.setdefault("executes", []).append((sql, args))
        return "OK"

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any]:
        # The upsert_repo_for_tenant INSERT ... RETURNING → a repo row.
        self._sink.setdefault("fetchrows", []).append((sql, args))
        return {
            "id": "repo-1",
            "tenant_id": args[0] if args else "tenant-1",
            "full_name": args[1] if len(args) > 1 else "owner/repo",
            "default_branch": None,
            "github_installation_id": None,
        }


class _FakeDB:
    """A minimal ``db.acquire()`` async-context stand-in over one recording conn."""

    def __init__(self) -> None:
        self.sink: dict[str, Any] = {}

    def acquire(self) -> Any:
        conn = _FakeConn(self.sink)

        class _Ctx:
            async def __aenter__(self_inner) -> Any:
                return conn

            async def __aexit__(self_inner, *exc: Any) -> bool:
                return False

        return _Ctx()


def _app(monkeypatch, token: str = "admin-token") -> tuple[Any, dict[str, Any]]:
    """A real FastAPI app with just the dev-smoke routes + a fake DB + a recorded invite seam."""
    from fastapi import FastAPI

    from control_plane import dev_smoke_routes
    from libs.http import install_safe_error_handler

    monkeypatch.setenv("PROXY_INTERNAL_TOKEN", token)

    calls: dict[str, Any] = {"invite": 0, "invite_kwargs": None}

    class _Invited:
        id = "meeting-42"
        recall_bot_id = "recall-bot-real"
        notice_posted = True

    async def _fake_invite(db: Any, **kwargs: Any) -> Any:
        calls["invite"] += 1
        calls["invite_kwargs"] = kwargs
        return _Invited()

    # Patch the invite_proxy the route imports (control_plane.meetings.invite_proxy).
    from control_plane import meetings as _meetings

    monkeypatch.setattr(_meetings, "invite_proxy", _fake_invite)

    # No built map for the test repo → the sha lookup returns None (placeholder pin path).
    async def _no_map(*a: Any, **k: Any) -> Any:
        return None

    monkeypatch.setattr(dev_smoke_routes, "_latest_indexed_sha", _no_map)

    app = FastAPI()
    install_safe_error_handler(app)
    app.state.db = _FakeDB()
    dev_smoke_routes.install_dev_smoke_routes(app)
    return app, calls


# ── test-provision auth + real-invite wiring ──────────────────────────────────────


def test_authenticated_test_provision_drives_the_real_invite(monkeypatch) -> None:
    """A valid admin token binds the test tenant/repo and calls the real invite_proxy → 201."""
    app, calls = _app(monkeypatch)
    with TestClient(app) as client:
        resp = client.post(
            "/admin/test-provision",
            headers={"X-Internal-Token": "admin-token"},
            json={"meeting_url": "https://meet.google.com/xqw-roey-ohm", "repo": "pgoel813/cova"},
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["meeting_id"] == "meeting-42"
    assert body["bot_id"] == "recall-bot-real"       # the id Recall returned, never fabricated
    assert body["indexed"] is False                   # unindexed repo → placeholder pin
    assert body["pinned_sha"] == "HEAD"
    # the real invite path was driven with the bound tenant/repo + the Meet URL:
    assert calls["invite"] == 1
    kw = calls["invite_kwargs"]
    assert kw["meeting_url"] == "https://meet.google.com/xqw-roey-ohm"
    assert kw["repo_id"] == "repo-1"


def test_missing_token_is_refused_and_no_invite(monkeypatch) -> None:
    app, calls = _app(monkeypatch)
    with TestClient(app) as client:
        resp = client.post(
            "/admin/test-provision",
            json={"meeting_url": "https://meet.google.com/x", "repo": "o/r"},
        )
    assert resp.status_code == 401
    assert calls["invite"] == 0


def test_wrong_token_is_refused_and_no_invite(monkeypatch) -> None:
    app, calls = _app(monkeypatch)
    with TestClient(app) as client:
        resp = client.post(
            "/admin/test-provision",
            headers={"X-Internal-Token": "nope"},
            json={"meeting_url": "https://meet.google.com/x", "repo": "o/r"},
        )
    assert resp.status_code == 401
    assert calls["invite"] == 0


def test_missing_fields_are_a_422(monkeypatch) -> None:
    app, _ = _app(monkeypatch)
    with TestClient(app) as client:
        resp = client.post(
            "/admin/test-provision",
            headers={"X-Internal-Token": "admin-token"},
            json={"meeting_url": ""},
        )
    assert resp.status_code == 422


# ── HEARD transcript tap ──────────────────────────────────────────────────────────


def test_transcript_tap_reads_the_live_meeting_notes(monkeypatch) -> None:
    """The tap reads the live meeting's sandbox MEETING_NOTES.md through its workroom handle and
    parses the lines into the HEARD shape."""
    app, _ = _app(monkeypatch)

    class _Workroom:
        async def read_transcript(self) -> str:
            return "# Meeting transcript\n[10] Riya: hi proxy can you hear me\n[11] Pranav: yes\n"

    class _Runtime:
        workroom = _Workroom()

    class _Registry:
        def get(self, mid: str) -> Any:
            return _Runtime() if mid == "meeting-42" else None

    app.state.meeting_runtimes = _Registry()

    with TestClient(app) as client:
        resp = client.get(
            "/admin/transcript",
            headers={"X-Internal-Token": "admin-token"},
            params={"meeting_id": "meeting-42"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["captured"] is True
    speakers = {ln["speaker"] for ln in body["lines"]}
    assert {"Riya", "Pranav"} <= speakers
    riya = next(ln for ln in body["lines"] if ln["speaker"] == "Riya")
    assert riya["ts"] == 10.0 and "hi proxy" in riya["text"]


def test_transcript_tap_no_runtime_is_honest_empty(monkeypatch) -> None:
    """No live workroom for the meeting → an honest empty capture (captured=False), never fabricated."""
    app, _ = _app(monkeypatch)

    class _Registry:
        def get(self, mid: str) -> Any:
            return None

    app.state.meeting_runtimes = _Registry()
    with TestClient(app) as client:
        resp = client.get(
            "/admin/transcript",
            headers={"X-Internal-Token": "admin-token"},
            params={"meeting_id": "ghost"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"meeting_id": "ghost", "lines": [], "raw": "", "captured": False}


def test_transcript_tap_requires_auth(monkeypatch) -> None:
    app, _ = _app(monkeypatch)
    with TestClient(app) as client:
        resp = client.get("/admin/transcript", params={"meeting_id": "meeting-42"})
    assert resp.status_code == 401


# ── scoping / constant-time gate ──────────────────────────────────────────────────


def test_token_compare_is_constant_time() -> None:
    from control_plane import dev_smoke_routes

    src = inspect.getsource(dev_smoke_routes)
    assert "compare_digest" in src, "the admin-token compare must be constant-time"


def test_smoke_routes_classify_internal_not_raw(monkeypatch) -> None:
    """Both taps are stamped internal-scoped so the §4.6 enumeration gate accepts them (not raw)."""
    monkeypatch.setenv("PROXY_INTERNAL_TOKEN", "admin-token")
    from control_plane.app import create_app
    from libs.http import classify_route

    app = create_app()
    seen = {}
    for route in app.routes:
        path = getattr(route, "path", "")
        if path in ("/admin/test-provision", "/admin/transcript"):
            seen[path] = classify_route(route)
    assert seen.get("/admin/test-provision") == "internal"
    assert seen.get("/admin/transcript") == "internal"
