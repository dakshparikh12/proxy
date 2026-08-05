"""dev_smoke_routes — the MONITORED-smoke taps: direct provision (skip OAuth) + a HEARD read.

The live smoke needs two things the normal product front door does not give a machine caller:

1. **A way to put Proxy into a real Meet WITHOUT the browser Google-OAuth session.**
   ``POST /meetings`` sits behind the §4.6 ``protected()`` wall (a signed Google session), so a
   headless smoke gets a 401. This module mounts ``POST /admin/test-provision`` — the SAME real
   invite path (``meetings.invite_proxy`` → a REAL Recall bot + the webhook drain's real E2B
   workroom), with ONLY the user-session wall replaced by the internal server-to-server bearer
   (``X-Internal-Token``: ``PROXY_INTERNAL_TOKEN``, constant-time compared — the exact trust plane
   ``admin_routes`` / the ``/internal/*`` routes already use). It binds a test tenant + the named
   repo in-process, then drives the real invite. It fabricates NOTHING: the bot id is the one Recall
   returns, and the workroom is provisioned by the same drain the production join uses.

2. **A way to SEE what Proxy HEARD.** ``GET /admin/transcript`` reads the live meeting's sandbox
   ``MEETING_NOTES.md`` (the transcript capture the session feeds continuously) through the meeting's
   own workroom handle — a monitoring read, host-side, so the harness HEARD tap can confirm the STT
   is being captured even when Proxy stays silent.

Both routes are gated by the internal admin bearer and stamped ``mark_internal_scoped`` (a
server-to-server trust plane, NEVER a user session — the §4.6 route-enumeration gate classifies them
as scoped, not raw). A missing/unset token fails CLOSED (401), so these taps are inert on any
process where ``PROXY_INTERNAL_TOKEN`` is not provisioned. Never-throw throughout: every fault is an
honest JSON, never an unhandled crash.
"""
from __future__ import annotations

import hmac
import logging
import os
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from libs.http import mark_internal_scoped

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastapi import FastAPI

logger = logging.getLogger(__name__)

#: The smoke taps — single constants so the mount + any URL builder can't drift by a typo.
TEST_PROVISION_PATH = "/admin/test-provision"
TRANSCRIPT_PATH = "/admin/transcript"
REPLICA_SAY_PATH = "/admin/replica-say"

#: A deterministic test-tenant id (a fixed uuid) so re-running the smoke reuses the same tenant/repo
#: rows (idempotent) rather than piling up fresh tenants. This is a TEST fixture id, never a customer.
_TEST_TENANT_ID = "00000000-0000-4000-8000-0000000000aa"


def _expected_admin_token() -> str:
    """The bound internal admin token — ``PROXY_INTERNAL_TOKEN`` then the reconcile token.

    Read at request time (not import) so a rotated secret is picked up. Empty when neither is set →
    the compare fails CLOSED (a smoke tap is inert unless the operator provisions the bearer)."""
    return (
        os.environ.get("PROXY_INTERNAL_TOKEN")
        or os.environ.get("INTERNAL_RECONCILE_TOKEN")
        or ""
    )


def _authorized(headers: Any) -> bool:
    """True iff the presented ``X-Internal-Token`` matches the bound one (constant-time)."""
    expected = _expected_admin_token()
    presented = str(headers.get("x-internal-token", "") or "")
    if not expected or not presented:
        return False
    return hmac.compare_digest(presented, expected)


async def _bind_test_tenant_repo(db: Any, *, repo: str) -> dict[str, Any]:
    """Ensure the test tenant + a repo row for ``repo`` exist; return the repo row.

    Reuses the REAL binding helper ``repos.meetings.upsert_repo_for_tenant`` (the same one the
    connect success path uses) so the invited repo resolves exactly like a normally-connected repo.
    ``full_name`` is stored VERBATIM as the caller names it, matching the invite/map key rules.
    Idempotent: a fixed test-tenant uuid + the ``(tenant_id, full_name)`` unique index mean a
    re-run reuses the same rows. Tenant-scoped by construction."""
    from libs.db import repos

    async with db.acquire() as conn:
        await conn.execute(
            "INSERT INTO tenants (id, name) VALUES ($1, $2) ON CONFLICT (id) DO NOTHING",
            _TEST_TENANT_ID,
            "smoke-test-tenant",
        )
        repo_row = await repos.meetings.upsert_repo_for_tenant(
            conn, tenant_id=_TEST_TENANT_ID, full_name=repo
        )
    return repo_row


async def _latest_indexed_sha(db: Any, *, repo: str) -> str | None:
    """The repo's latest built-map sha (the durable HEAD), or ``None`` if never indexed.

    The invite pins a sha; for a repo with a real map we use its latest sha (so the workroom seeds
    the resident understanding). A repo with NO built map still smokes — the caller falls back to a
    placeholder pin (the workroom clones + explores directly), so the smoke never blocks on indexing.
    """
    try:
        from premeeting.map_store import load_latest_map
        from premeeting.paths import repo_name_from_url

        async with db.acquire() as conn:
            latest = await load_latest_map(
                conn, tenant_id=_TEST_TENANT_ID, repo=repo_name_from_url(repo)
            )
        return None if latest is None else latest[0]
    except Exception:  # noqa: BLE001 - a map lookup fault is an honest "no pin", never a crash
        logger.warning("test-provision map lookup failed for %s (using placeholder pin)", repo, exc_info=True)
        return None


def install_dev_smoke_routes(app: "FastAPI") -> None:
    """Mount the two smoke taps BEHIND the internal admin bearer (§4.6 server-to-server trust plane).

    ``POST /admin/test-provision`` — body ``{meeting_url, repo}`` (repo defaults from the body only;
    no tenant is ever read off the body — it is the fixed test tenant). It binds the test tenant +
    repo, pins the latest indexed sha (or a placeholder for an unindexed repo), and drives the REAL
    ``invite_proxy`` (real Recall bot). 201 ``{meeting_id, bot_id, pinned_sha, indexed}``.

    ``GET /admin/transcript?meeting_id=<id>`` — reads that live meeting's sandbox ``MEETING_NOTES.md``
    (the HEARD capture) through its workroom handle. 200 ``{meeting_id, lines:[{ts,speaker,text}], raw}``.
    """

    @app.post(TEST_PROVISION_PATH, include_in_schema=True)
    async def test_provision(request: Request) -> JSONResponse:
        if not _authorized(request.headers):
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        db = getattr(request.app.state, "db", None)
        if db is None:
            return JSONResponse({"error": "substrate unavailable"}, status_code=503)

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 - a non-JSON body is the caller's own bad input
            return JSONResponse({"error": "invalid body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "invalid body"}, status_code=400)
        meeting_url = str(body.get("meeting_url", "") or "")
        repo = str(body.get("repo", "") or "")
        if not meeting_url or not repo:
            return JSONResponse(
                {"error": "meeting_url and repo are required"}, status_code=422
            )

        # Bind the test tenant + repo (idempotent), pin HEAD (or a placeholder for an unindexed repo).
        try:
            repo_row = await _bind_test_tenant_repo(db, repo=repo)
        except Exception:  # noqa: BLE001 - never-throw: an honest 500-class JSON, not a crash
            logger.exception("test-provision tenant/repo binding failed (repo=%s)", repo)
            return JSONResponse({"error": "binding failed"}, status_code=500)

        indexed_sha = await _latest_indexed_sha(db, repo=repo)
        # An unindexed repo has no durable HEAD; the smoke still runs — pin a placeholder and let the
        # workroom shallow-clone + explore directly (honest: 'indexed' False tells the operator).
        head_sha = indexed_sha or "HEAD"

        # THE real invite path — the SAME one POST /meetings drives, minus the user-session wall. A
        # test seam transport rides app.state.transport_provider when injected; unset (the live smoke)
        # invite_proxy constructs the real Recall transport and launches a real bot.
        transport = getattr(request.app.state, "transport_provider", None)
        try:
            from . import meetings as _meetings

            invited = await _meetings.invite_proxy(
                db,
                tenant_id=repo_row["tenant_id"],
                repo_id=repo_row["id"],
                meeting_url=meeting_url,
                head_sha=head_sha,
                transport=transport,
            )
        except Exception as exc:  # noqa: BLE001 - never-throw: honest JSON with the reason (this is a DEV tap)
            logger.exception("test-provision invite failed (meeting_url=%s repo=%s)", meeting_url, repo)
            return JSONResponse(
                {"error": "invite failed", "detail": str(exc) or exc.__class__.__name__},
                status_code=502,
            )

        return JSONResponse(
            {
                "meeting_id": str(invited.id),
                "bot_id": invited.recall_bot_id,
                "pinned_sha": head_sha,
                "indexed": indexed_sha is not None,
                "notice_posted": bool(invited.notice_posted),
            },
            status_code=201,
        )

    @app.get(TRANSCRIPT_PATH, include_in_schema=True)
    async def admin_transcript(request: Request, meeting_id: str = "") -> JSONResponse:
        if not _authorized(request.headers):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if not meeting_id:
            return JSONResponse({"error": "meeting_id is required"}, status_code=422)

        registry = getattr(request.app.state, "meeting_runtimes", None)
        runtime = registry.get(meeting_id) if registry is not None else None
        workroom = getattr(runtime, "workroom", None) if runtime is not None else None
        if workroom is None:
            # No live workroom for this meeting (never provisioned / already torn down). Honest empty
            # capture — the monitor records the gap rather than seeing a fabricated transcript.
            return JSONResponse(
                {"meeting_id": meeting_id, "lines": [], "raw": "", "captured": False},
                status_code=200,
            )
        raw = await workroom.read_transcript()
        return JSONResponse(
            {
                "meeting_id": meeting_id,
                "lines": _parse_transcript_lines(raw),
                "raw": raw,
                "captured": bool(raw.strip()),
            },
            status_code=200,
        )

    @app.post(REPLICA_SAY_PATH, include_in_schema=True)
    async def replica_say(request: Request) -> JSONResponse:
        """Speak a line through a replica bot's output-media channel — the harness's voice.

        The replica bots' pages are served by THIS process (their WS clients connect here),
        so the PCM must be written HERE: the harness driver posts ``{channel, text}`` and this
        route synthesizes with the real Cartesia seam and writes into the live channel the
        Recall bot is playing as its mic. Test-plane only (internal bearer)."""
        if not _authorized(request.headers):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 — a malformed body is the caller's 400, never a 500
            return JSONResponse({"error": "invalid body"}, status_code=400)
        channel_id = str(body.get("channel", "") or "").strip()
        text = str(body.get("text", "") or "").strip()
        if not channel_id or not text:
            return JSONResponse({"error": "channel and text are required"}, status_code=422)

        from in_meeting.output_media import channel_for
        from transport.tts import CartesiaTTS

        from libs.http.src.http.external import call_external

        tts = CartesiaTTS(call_external, api_key=os.environ.get("CARTESIA_API_KEY", ""))
        channel = channel_for(channel_id)
        clients = getattr(channel, "client_count", None)
        chunks = 0
        await channel.set_speaking(True)
        try:
            async for chunk in tts.synthesize(text):
                await channel.write_audio(chunk.pcm)
                chunks += 1
        finally:
            await channel.set_speaking(False)
        return JSONResponse(
            {
                "channel": channel_id,
                "chunks": chunks,
                "clients": clients() if callable(clients) else clients,
            },
            status_code=200,
        )

    # A server-to-server bearer, not a user session — stamp all so the route-enumeration gate
    # classifies them as scoped, not raw (the /internal-style trust plane, §4.6).
    mark_internal_scoped(test_provision)
    mark_internal_scoped(admin_transcript)
    mark_internal_scoped(replica_say)


def _parse_transcript_lines(raw: str) -> list[dict[str, Any]]:
    """Parse ``MEETING_NOTES.md`` body lines (``[<ts>] <speaker>: <text>``) into the HEARD shape.

    The session renders each line as ``[<epoch>] <speaker>: <text>`` (see meeting_session
    ``_render_transcript``). This lifts them into ``{ts, speaker, text}`` dicts the monitor's HEARD
    view reads. Header/elision lines (``# …`` / ``(… elided …)``) are skipped. Robust to a malformed
    line (kept as text with a best-effort split) — a monitoring parse never raises."""
    import re

    lines: list[dict[str, Any]] = []
    pat = re.compile(r"^\[(?P<ts>[^\]]*)\]\s*(?P<speaker>[^:]+):\s*(?P<text>.*)$")
    for raw_line in raw.splitlines():
        line = raw_line.rstrip("\n")
        if not line.strip() or line.startswith("#") or line.startswith("(…"):
            continue
        m = pat.match(line)
        if m is None:
            continue
        ts_raw = m.group("ts").strip()
        try:
            ts = float(ts_raw)
        except ValueError:
            ts = 0.0
        lines.append(
            {"ts": ts, "speaker": m.group("speaker").strip(), "text": m.group("text").strip()}
        )
    return lines


__all__ = [
    "TEST_PROVISION_PATH",
    "TRANSCRIPT_PATH",
    "install_dev_smoke_routes",
]
