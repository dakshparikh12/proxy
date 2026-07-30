"""Doc-08 §4.6 — POST /meetings: the hosted invite route (the product's front door).

"Give Proxy a meeting URL" over HTTP: an authenticated tenant member POSTs a
meeting link + repo, the route resolves the caller's tenant SERVER-SIDE (the
§4.6 ``protected()`` wall — never a client body field), proves the repo belongs
to that tenant, and drives the EXISTING ``control_plane.meetings.invite_proxy``
— the meetings row lands bound to (tenant, repo, pinned_sha=HEAD) and the REAL
Recall bot launches through the transport seam with the FULL join config.

What this file proves on the real path (the P4 acceptance set):

1. an authed POST with a valid link+repo → 201 {meeting_id, bot_id}, the
   meetings row created, and the transport join issued with the full config
   (streaming transcription + realtime delivery + output media) — recorded at
   the ``http_client`` seam under the REAL ``RecallTransport`` (no live bot);
2. an anonymous caller is refused (401/403) BEFORE the handler;
3. a repo belonging to ANOTHER tenant is refused with the byte-identical
   response an unknown repo gets — no existence leak;
4. a malformed meeting URL is a 422 (nothing inserted, nothing launched);
5. the route classifies ``protected`` under the §4.6 route-enumeration
   machinery and is NEVER on the PUBLIC_ROUTES allowlist.

The Recall wire is faked ONLY at the ``libs.http`` ``http_client`` seam (the
sole raw-client home) so the real ``call_external`` + ``RecallTransport`` +
``JoinSession`` path runs — tests never create a live bot.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import uuid
from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient

from libs.http import PUBLIC_ROUTES, classify_route
from libs.http.src.http.registry import route_key

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "doc00"))
import _support as S  # noqa: E402  reuse pg_conn / apply_migrations / _local_dsn

_MEETINGS_KEY = "POST /meetings"
_MEET_URL = "https://meet.google.com/abc-defg-hij"
_WEBHOOK_URL = "https://proxy.example.com/webhooks/recall"
_OUTPUT_MEDIA_URL = "https://proxy.example.com/output-media/surface"

# Recall's real create-bot response shape — the launched bot's unique id.
_BOT_ID = "7d1e2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a5b"
_RECALL_PAYLOAD: dict[str, Any] = {"id": _BOT_ID, "status_changes": [{"code": "joining_call"}]}


def _app() -> Any:
    from control_plane import create_app

    return create_app()


def _install_fake_http(monkeypatch: Any) -> list[dict[str, Any]]:
    """Patch ``libs.http``'s ``http_client`` with a recording fake; return the wire log.

    The ONLY fake sits at the single raw-client home (§14 external-calls rule), so the
    REAL ``call_external`` + ``RecallTransport`` + ``JoinSession`` path runs end-to-end
    and the log records the FULL create-bot config the route actually sent.
    """
    import libs.http.src.http.external as ext

    calls: list[dict[str, Any]] = []

    class _FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return _RECALL_PAYLOAD

    class _FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            self._kwargs = kwargs

        async def __aenter__(self) -> "_FakeClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def request(
            self,
            method: str,
            url: str,
            *,
            headers: dict[str, str] | None = None,
            json: Any = None,
            **kwargs: Any,
        ) -> _FakeResponse:
            calls.append({"method": method, "url": url, "json": json})
            return _FakeResponse()

    monkeypatch.setattr(ext, "http_client", _FakeClient)
    return calls


def _fake_transport() -> Any:
    """The REAL RecallTransport over the funded seam — configured with the full join
    config (webhook + output-media URLs) so the create-bot body carries everything."""
    from libs.http.src.http.external import call_external
    from transport.recall import RecallTransport

    return RecallTransport(
        call_external,
        api_key="rk_p4_route_test",
        webhook_url=_WEBHOOK_URL,
        output_media_url=_OUTPUT_MEDIA_URL,
    )


async def _signin_and_seed(
    db: Any, *, email: str, full_name: str, map_sha: str | None
) -> tuple[str, str, str]:
    """Real durable sign-in + a repo row (and optionally its latest map) for that tenant.

    Returns ``(session_cookie, tenant_id, repo_id)`` — the cookie is the SAME durable
    HMAC session the live OAuth callback mints (``complete_signin``), so the route
    authenticates by the product's real session mechanism, not a test-only shim.
    """
    from code_intel.paths import repo_name_from_url
    from control_plane.session import complete_signin

    signin = await complete_signin(db, email=email)
    tenant_id = str(signin.tenant_id)
    async with db.acquire() as conn:
        repo_id = await conn.fetchval(
            "INSERT INTO repos (tenant_id, full_name, default_branch) "
            "VALUES ($1, $2, 'main') RETURNING id",
            uuid.UUID(tenant_id),
            full_name,
        )
        if map_sha is not None:
            await conn.execute(
                "INSERT INTO repo_maps (tenant_id, repo, sha, map) VALUES ($1, $2, $3, $4)",
                uuid.UUID(tenant_id),
                repo_name_from_url(full_name),
                map_sha,
                "# repo map",
            )
    return signin.cookie, tenant_id, str(repo_id)


def _dsn_or_skip() -> str:
    dsn = S._local_dsn()
    if not dsn:
        pytest.skip("no local Postgres (set TEST_DATABASE_URL)")
    r = S.apply_migrations(dsn)
    assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"
    return dsn


async def _post_meetings(
    app: Any, *, cookie: str | None, body: dict[str, Any]
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://cp") as client:
        if cookie is not None:
            client.cookies.set("session", cookie)
        return await client.post("/meetings", json=body)


# ── (5) §4.6 route-registry discipline ──────────────────────────────────────────


def test_meetings_route_is_mounted_and_classified_protected() -> None:
    """POST /meetings exists on the live app AND classifies ``protected`` (never raw,
    never public) — the §4.6 enumeration accepts it by STRUCTURE, not exemption."""
    app = _app()
    route = next((r for r in app.routes if route_key(r) == _MEETINGS_KEY), None)
    assert route is not None, "POST /meetings is not mounted on control_plane"
    verdict = classify_route(route)
    assert verdict == "protected", f"POST /meetings classified {verdict!r}, expected protected"
    assert _MEETINGS_KEY not in PUBLIC_ROUTES, "the invite route must never be public"


# ── (2) anonymous → refused before the handler ─────────────────────────────────


def test_anonymous_invite_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """No session ⇒ the ``protected()`` wall fires 401/403 — and NO bot launches."""
    calls = _install_fake_http(monkeypatch)
    app = _app()
    client = TestClient(app)
    resp = client.post("/meetings", json={"meeting_url": _MEET_URL, "repo": "acme/api"})
    assert resp.status_code in (401, 403), (
        f"anonymous POST /meetings must be refused, got {resp.status_code}"
    )
    assert calls == [], "an anonymous invite must never reach the transport"


# ── (1) the happy path: authed invite → 201 + row + full join config ───────────


@pytest.mark.integration
def test_authed_invite_creates_meeting_and_launches_bot_with_full_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An authed POST with a valid link+repo → 201 {meeting_id, bot_id}; the meetings
    row lands bound to (tenant, repo, pinned_sha=HEAD-as-indexed); the transport join
    rode the seam with the FULL create-bot config; the consent notice posted.

    criterion_id: P4-INVITE-01
    """
    dsn = _dsn_or_skip()
    calls = _install_fake_http(monkeypatch)
    full_name = f"acme/api-{uuid.uuid4().hex[:8]}"
    head_sha = "d0c8beef" + uuid.uuid4().hex[:8]

    async def _flow() -> tuple[httpx.Response, dict[str, Any] | None, str, str]:
        from libs.db import Database

        db = await Database.connect(dsn)
        try:
            app = _app()
            app.state.db = db
            app.state.transport_provider = _fake_transport()
            cookie, tenant_id, repo_id = await _signin_and_seed(
                db, email=f"owner-{uuid.uuid4().hex[:8]}@example.com",
                full_name=full_name, map_sha=head_sha,
            )
            resp = await _post_meetings(
                app, cookie=cookie, body={"meeting_url": _MEET_URL, "repo": full_name}
            )
            row = None
            if resp.status_code == 201:
                async with db.acquire() as conn:
                    fetched = await conn.fetchrow(
                        "SELECT tenant_id, repo_id, meeting_url, pinned_sha, "
                        "       recall_bot_id, status, platform "
                        "  FROM meetings WHERE id = $1",
                        uuid.UUID(resp.json()["meeting_id"]),
                    )
                row = dict(fetched) if fetched is not None else None
            return resp, row, tenant_id, repo_id
        finally:
            await db.close()

    resp, row, tenant_id, repo_id = asyncio.run(_flow())

    # 201 + the response carries exactly the launched identity.
    assert resp.status_code == 201, f"expected 201, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["meeting_id"], "the response must carry the created meeting id"
    assert body["bot_id"] == _BOT_ID, "bot_id must be the id Recall actually returned"

    # Naming law: no internal component name in the user-visible response.
    lowered = resp.text.lower()
    for internal in ("orchestrator", "scribe", "workroom"):
        assert internal not in lowered, f"response leaks internal name {internal!r}"

    # The meetings row is bound to (tenant, repo, pinned_sha=HEAD) + the REAL bot id.
    assert row is not None, "a meetings row must exist for the returned meeting_id"
    assert str(row["tenant_id"]) == tenant_id
    assert str(row["repo_id"]) == repo_id
    assert row["meeting_url"] == _MEET_URL
    assert row["pinned_sha"] == head_sha, "pinned_sha must be the repo's indexed HEAD"
    assert row["recall_bot_id"] == _BOT_ID
    assert row["status"] == "live"
    assert row["platform"] == "meet"

    # The join went out through the seam with the FULL create-bot config.
    join_calls = [c for c in calls if c["method"] == "POST" and c["url"].endswith("/bot")]
    assert len(join_calls) == 1, f"expected exactly one create-bot call, got {calls!r}"
    join_body = join_calls[0]["json"]
    assert join_body["meeting_url"] == _MEET_URL
    assert join_body["recording_config"]["transcript"]["provider"] == {
        "assembly_ai_v3_streaming": {}
    }
    endpoints = join_body["recording_config"]["realtime_endpoints"]
    assert endpoints[0]["url"] == _WEBHOOK_URL
    assert "transcript.data" in endpoints[0]["events"]
    assert join_body["output_media"]["camera"]["config"]["url"] == _OUTPUT_MEDIA_URL

    # The consent notice posted (the join FSM completed, not just the bot create).
    chat_calls = [c for c in calls if c["url"].endswith("/chat")]
    assert chat_calls, "the consent notice must post as part of the invite path"
    assert chat_calls[0]["json"].get("pinned") is True


# ── (3) cross-tenant repo → refused with NO existence leak ─────────────────────


@pytest.mark.integration
def test_cross_tenant_repo_is_refused_without_existence_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A repo owned by ANOTHER tenant answers byte-identically to a repo that does
    not exist at all — refused, nothing inserted, nothing launched.

    criterion_id: P4-INVITE-03
    """
    dsn = _dsn_or_skip()
    calls = _install_fake_http(monkeypatch)
    victim_repo = f"victim/secret-{uuid.uuid4().hex[:8]}"

    async def _flow() -> tuple[httpx.Response, httpx.Response, int]:
        from libs.db import Database

        db = await Database.connect(dsn)
        try:
            app = _app()
            app.state.db = db
            app.state.transport_provider = _fake_transport()
            # Tenant B owns the repo (indexed and everything) …
            await _signin_and_seed(
                db, email=f"victim-{uuid.uuid4().hex[:8]}@example.com",
                full_name=victim_repo, map_sha="b" * 12,
            )
            # … tenant A is the caller, owning no such repo.
            cookie_a, _, _ = await _signin_and_seed(
                db, email=f"attacker-{uuid.uuid4().hex[:8]}@example.com",
                full_name=f"attacker/own-{uuid.uuid4().hex[:8]}", map_sha=None,
            )
            cross = await _post_meetings(
                app, cookie=cookie_a, body={"meeting_url": _MEET_URL, "repo": victim_repo}
            )
            missing = await _post_meetings(
                app, cookie=cookie_a,
                body={"meeting_url": _MEET_URL, "repo": f"no/such-{uuid.uuid4().hex[:8]}"},
            )
            async with db.acquire() as conn:
                n = await conn.fetchval(
                    "SELECT count(*) FROM meetings m JOIN repos r ON m.repo_id = r.id "
                    " WHERE r.full_name = $1",
                    victim_repo,
                )
            return cross, missing, int(n)
        finally:
            await db.close()

    cross, missing, rows = asyncio.run(_flow())

    assert cross.status_code in (403, 404), f"cross-tenant invite must be refused, got {cross.status_code}"
    # No existence leak: the cross-tenant answer is INDISTINGUISHABLE from not-found.
    assert cross.status_code == missing.status_code, (
        f"cross-tenant ({cross.status_code}) vs unknown ({missing.status_code}) leaks existence"
    )
    assert cross.text == missing.text, "response bodies must not distinguish the two cases"
    assert rows == 0, "no meeting may land against another tenant's repo"
    assert calls == [], "a refused invite must never reach the transport"


# ── (4) malformed meeting URL → 422, nothing happens ───────────────────────────


@pytest.mark.integration
def test_malformed_meeting_url_is_422(monkeypatch: pytest.MonkeyPatch) -> None:
    """An authed POST with a non-http(s) meeting link is a 422 — no row, no bot.

    criterion_id: P4-INVITE-04
    """
    dsn = _dsn_or_skip()
    calls = _install_fake_http(monkeypatch)
    full_name = f"acme/web-{uuid.uuid4().hex[:8]}"

    async def _flow() -> tuple[list[httpx.Response], int]:
        from libs.db import Database

        db = await Database.connect(dsn)
        try:
            app = _app()
            app.state.db = db
            app.state.transport_provider = _fake_transport()
            cookie, tenant_id, _ = await _signin_and_seed(
                db, email=f"member-{uuid.uuid4().hex[:8]}@example.com",
                full_name=full_name, map_sha="c" * 12,
            )
            responses = []
            for bad in ("totally not a url", "ftp://meet.example.com/x", ""):
                responses.append(
                    await _post_meetings(
                        app, cookie=cookie, body={"meeting_url": bad, "repo": full_name}
                    )
                )
            # A missing field is the caller's own validation error too.
            responses.append(await _post_meetings(app, cookie=cookie, body={"repo": full_name}))
            async with db.acquire() as conn:
                n = await conn.fetchval(
                    "SELECT count(*) FROM meetings WHERE tenant_id = $1",
                    uuid.UUID(tenant_id),
                )
            return responses, int(n)
        finally:
            await db.close()

    responses, rows = asyncio.run(_flow())
    for resp in responses:
        assert resp.status_code == 422, f"malformed input must be 422, got {resp.status_code}: {resp.text}"
    assert rows == 0, "a malformed invite must insert nothing"
    assert calls == [], "a malformed invite must never reach the transport"


# ── honesty guard: an unindexed repo is refused plainly (never a fabricated pin) ─


@pytest.mark.integration
def test_unindexed_repo_is_refused_honestly(monkeypatch: pytest.MonkeyPatch) -> None:
    """A repo with NO built map has no HEAD to pin — the invite is refused with an
    honest 409 (Law 2), never a meetings row carrying a fabricated/empty pinned_sha."""
    dsn = _dsn_or_skip()
    calls = _install_fake_http(monkeypatch)
    full_name = f"acme/new-{uuid.uuid4().hex[:8]}"

    async def _flow() -> tuple[httpx.Response, int]:
        from libs.db import Database

        db = await Database.connect(dsn)
        try:
            app = _app()
            app.state.db = db
            app.state.transport_provider = _fake_transport()
            cookie, tenant_id, _ = await _signin_and_seed(
                db, email=f"eager-{uuid.uuid4().hex[:8]}@example.com",
                full_name=full_name, map_sha=None,  # connected but never indexed
            )
            resp = await _post_meetings(
                app, cookie=cookie, body={"meeting_url": _MEET_URL, "repo": full_name}
            )
            async with db.acquire() as conn:
                n = await conn.fetchval(
                    "SELECT count(*) FROM meetings WHERE tenant_id = $1",
                    uuid.UUID(tenant_id),
                )
            return resp, int(n)
        finally:
            await db.close()

    resp, rows = asyncio.run(_flow())
    assert resp.status_code == 409, f"an unindexed repo must refuse honestly, got {resp.status_code}"
    assert rows == 0
    assert calls == [], "no bot may launch for a repo Proxy cannot ground in"
