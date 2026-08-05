"""POST /meetings — the hosted invite route ("give Proxy a meeting URL"), §4.6-scoped.

The product's front door: an authenticated tenant member POSTs a meeting link +
repo and Proxy joins that meeting already knowing that codebase. The route is the
missing HTTP entry over the EXISTING ``control_plane.meetings.invite_proxy`` — it
adds only auth + tenant-scoped resolution, never a second invite implementation:

1. the caller's tenant resolves SERVER-SIDE through the §4.6 ``protected()``
   wrapper (the same signed-session wall the draft accept/reject mutations
   declare) — an anonymous caller 401s, a tenant-less session 403s, and the
   handler receives a credentials-only :class:`~libs.http.AuthzCtx` whose
   ``tenant_id`` is non-null by construction (never a client body field);
2. the named repo must belong to THAT tenant: the lookup is tenant-filtered by
   construction (``repos.meetings.get_repo_for_tenant``), so a repo owned by
   another tenant answers byte-identically to a repo that does not exist —
   refused with no existence leak;
3. the pinned HEAD is the repo's latest DURABLE indexed sha (the pre-meeting
   ``repo_maps`` row — readable from any instance, per the source-of-truth rule);
   a repo with no built map has no HEAD to pin, so the invite refuses honestly
   (409, Law 2) rather than fabricating a pin;
4. ``invite_proxy`` then creates the meetings row bound to (tenant, repo,
   pinned_sha=HEAD) and launches the REAL Recall bot through the transport seam
   (``_default_transport`` in deployment; a test injects a recording transport on
   ``app.state.transport_provider`` — no live bot in tests).

Success is ``201 {meeting_id, bot_id}`` — the id Recall actually returned, never
a fabricated one. Every user-visible string here is internal-name-free (§14
naming law, enforced by ``lint.naming``).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

# Module-scope Starlette request/response types: ``from __future__ import annotations``
# stringizes the handler's annotations and FastAPI resolves them via the MODULE globals
# (the same convention every other control_plane route mount uses — a function-local
# ``Request`` import would make FastAPI misread ``request`` as a query param).
from fastapi import Body
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from libs.http import AuthzCtx, SessionResolver, protected

from . import meetings as _meetings

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastapi import FastAPI

# The route path — one constant so the mount and its tests can never drift by a typo.
MEETINGS_PATH = "/meetings"

_log = logging.getLogger("services.control_plane.meetings_route")


def _is_http_url(candidate: str) -> bool:
    """True iff ``candidate`` parses as an absolute http(s) URL (pure string physics).

    The meeting link must be something a Recall bot can actually join from — an
    absolute web URL. Scheme + host presence is the ONLY validation here (Law 4:
    no per-platform judgment lives in code; ``invite_proxy`` records the platform
    as a URL fact and Recall brokers the join).
    """
    try:
        parts = urlsplit(candidate)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.netloc)


async def _latest_indexed_sha(db: Any, *, tenant_id: str, full_name: str) -> str | None:
    """The repo's HEAD as the product durably knows it: its latest built map's sha.

    The pre-meeting system's one durable artifact is the per-(tenant, repo, sha)
    ``repo_maps`` row (Postgres — readable from ANY instance, surviving recycle);
    its freshest sha IS the indexed HEAD a meeting pins. The clone is a rebuildable
    derived cache and per-host, so it is never consulted here. ``None`` means the
    repo has no built index yet — the caller refuses honestly (fail closed)."""
    from premeeting.map_store import load_latest_map
    from premeeting.paths import repo_name_from_url

    async with db.acquire() as conn:
        latest = await load_latest_map(
            conn, tenant_id=tenant_id, repo=repo_name_from_url(full_name)
        )
    return None if latest is None else latest[0]


def install_meetings_route(
    app: "FastAPI",
    *,
    session_resolver: SessionResolver,
) -> None:
    """Mount ``POST /meetings`` BEHIND the §4.6 ``protected()`` wall.

    The handler declares ``ctx = protected(session_resolver)`` and receives ONLY the
    resulting credentials-only :class:`AuthzCtx` — "read the tenant from the body" is
    unrepresentable in its signature, and the dependency is the structural marker the
    ``tests/security/test_routes_are_scoped.py`` enumeration reads (the route
    classifies ``protected``, never ``raw``; it is NOT on ``PUBLIC_ROUTES``).

    ``session_resolver`` is the app's server-side session reader (the same one the
    draft accept/reject mounts use), injected so this module never owns a second
    session mechanism. The transport rides ``app.state.transport_provider`` when a
    test injects a recording one; unset, ``invite_proxy`` constructs the product's
    real Recall transport — the live path launches a real bot.
    """
    auth_dep = protected(session_resolver)

    @app.post(MEETINGS_PATH, include_in_schema=True)
    async def create_meeting_route(
        request: Request,
        ctx: AuthzCtx = auth_dep,
        meeting_url: str = Body(..., embed=True),
        repo: str = Body(..., embed=True),
    ) -> Response:
        db = getattr(request.app.state, "db", None)
        if db is None:
            # No durable substrate handle — an honest 503, never a fabricated invite.
            return JSONResponse(
                {"error": "Service is not ready; try again shortly"}, status_code=503
            )

        # (1) The meeting link must be an absolute http(s) URL — the caller's own
        #     bad input is a 422 (validation), refused before anything happens.
        if not _is_http_url(meeting_url):
            return JSONResponse(
                {"error": "meeting_url must be an http(s) meeting link"},
                status_code=422,
            )

        # (2) SERVER-SIDE tenant-scoped repo resolution. ``ctx.tenant_id`` came off
        #     the signed session (non-null by construction); the lookup is filtered
        #     by it, so another tenant's repo is INDISTINGUISHABLE from a repo that
        #     does not exist — refused with no existence leak.
        from libs.db import repos as _repos

        async with db.acquire() as conn:
            repo_row = await _repos.meetings.get_repo_for_tenant(
                conn, tenant_id=ctx.tenant_id, full_name=repo
            )
        if repo_row is None:
            return JSONResponse({"error": "Repo not found"}, status_code=404)

        # (3) Pin HEAD from the durable index — a repo Proxy has never indexed has
        #     no HEAD to pin, so the invite refuses plainly (Law 2), never a row
        #     with a fabricated/empty pin and never a bot that joins unable to
        #     ground in the codebase.
        head_sha = await _latest_indexed_sha(
            db, tenant_id=str(repo_row["tenant_id"]), full_name=str(repo_row["full_name"])
        )
        if head_sha is None:
            return JSONResponse(
                {"error": "This repo is not indexed yet — finish connecting it, then invite Proxy"},
                status_code=409,
            )

        # (4) The one real invite path: meetings row + workroom ASSEMBLED FIRST, THEN a REAL bot
        #     launch through the transport seam. ``transport_provider`` is the test seam; unset
        #     (the live deployment) invite_proxy constructs the real Recall transport.
        #
        # READY-BEFORE-JOIN (FIX 1): build the (before_join, after_join) hooks so the workroom is
        # fully assembled BEFORE the bot joins — "if Proxy is knocking, Proxy is ready". before_join
        # claims + assembles from the meeting row (no bot yet); after_join binds the real bot id onto
        # the live connection, posts the ready line, and spawns the run-until-end lifecycle. Absent
        # provisioning state (a bare test app) leaves the hooks None — the plain row+bot invite runs.
        transport = getattr(request.app.state, "transport_provider", None)
        before_join, after_join = _meetings.ready_before_join_hooks(request.app.state)
        try:
            invited = await _meetings.invite_proxy(
                db,
                tenant_id=repo_row["tenant_id"],
                repo_id=repo_row["id"],
                meeting_url=meeting_url,
                head_sha=head_sha,
                transport=transport,
                before_join=before_join,
                after_join=after_join,
            )
        except Exception:  # noqa: BLE001 - the join failure is honest but its internals stay server-side
            # §4.6 safeError: the caller learns the join failed (plainly, Law 2) but
            # never sees an internal error string; the reason lands in the server log.
            _log.exception("invite failed for meeting_url=%s repo=%s", meeting_url, repo)
            return JSONResponse(
                {"error": "Proxy could not join the meeting"}, status_code=502
            )

        return JSONResponse(
            {"meeting_id": str(invited.id), "bot_id": invited.recall_bot_id},
            status_code=201,
        )
