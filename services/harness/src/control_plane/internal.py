"""control_plane internal routes — the token-gated reconcile + notes entrypoints.

POST /internal/reconcile is mounted OUTSIDE the auth wall and gated by the
internal token. It calls the SAME run_reconcile_sweep the dev in-process interval
calls (one function, two schedulers — prod 5-min cron + dev interval). Idempotent.

GET /internal/notes/{meeting_id} is mounted ALONGSIDE it, in the same /internal
route group, OUTSIDE the auth wall, gated by the SAME internal bearer token (the
``X-Internal-Token`` header — never the user session cookie). It is the live
cross-service notes read the Workroom (Doc 05, another host) resolves ``notes_ref``
against (CANONICAL §11.4): it folds the ``note_deltas`` ledger from Postgres via
``scribe.notes_reader.read_notes`` — never the in-process NOTES_CACHE as a source —
and degrades honestly (401 no/bad token · 404 unknown meeting · 503 db-down),
never a fabricated/stale 200. GET /m/{meeting_id} (CANONICAL §12.9) is the
authenticated user surface BEHIND the auth wall that reads the SAME fold.

The scribe reader (:mod:`scribe.notes_reader`) owns the fold + the decision logic;
this module owns ONLY the framework glue — pulling the token off the header, the
meeting id off the path, and the DB acquirer off ``app.state`` — and translating
the reader's framework-agnostic :class:`~scribe.notes_reader.Response` into an
ASGI response. Registering these routes here closes the DOC03-CSREAD mount gap
(the handler was defined in scribe but wired into no app).
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import Response

from libs.db import Database
from libs.ops import run_reconcile_sweep

if TYPE_CHECKING:
    from fastapi import FastAPI

INTERNAL_RECONCILE_PATH = "/internal/reconcile"
INTERNAL_NOTES_PATH = "/internal/notes/{meeting_id}"
M_SURFACE_PATH = "/m/{meeting_id}"
INTERNAL_TOKEN_HEADER = "X-Internal-Token"  # nosec B105 - HTTP header name, not a secret


async def handle_internal_reconcile(
    db: Database, *, provided_token: str | None, expected_token: str | None
) -> int:
    """Token-gated POST /internal/reconcile handler; 403 without the token."""
    if not expected_token or provided_token != expected_token:
        return 403
    await run_reconcile_sweep(db)
    return 200


async def reconcile_interval_loop(db: Database, *, interval_s: float) -> None:
    """Dev in-process interval calling the same run_reconcile_sweep as prod."""
    while True:
        await run_reconcile_sweep(db)
        await asyncio.sleep(interval_s)


# ── Cross-service notes read — the live mount (DOC03-CSREAD) ──────────────────
def _acquirer(app: Any) -> Any:
    """The DB acquirer the notes fold drives — the live ``app.state.db``.

    ``read_notes`` needs an :class:`~scribe.notes_reader.Acquirer` (anything with
    an async-context ``acquire()``); the boot ``lifespan`` puts exactly such a
    handle (``libs.db.Database``) on ``app.state.db`` (server.py:_real_database).
    Kept as a tiny accessor so both notes routes resolve the ONE live handle and a
    missing handle surfaces as an honest 503 (never a fabricated 200).
    """
    return getattr(app.state, "db", None)


def _to_response(reader_response: Any) -> Response:
    """Translate a scribe ``Response`` (status + optional JSON body) to Starlette.

    The reader emits the canonical JSON string itself (byte-stable fold bytes), so
    an error status (401/404/503) carries no body — never a fabricated notes object.
    The 200 body is handed through VERBATIM as the response content (a raw
    ``Response`` with the JSON media type) so the byte-stable fold bytes reach the
    consumer unchanged — never re-encoded through a JSON serializer that could
    reorder keys and break the cross-caller byte-identity contract (AC-CSREAD-10).
    """
    if reader_response.body is None:
        return Response(status_code=reader_response.status_code)
    return Response(
        content=reader_response.body,  # already-serialised canonical JSON, verbatim
        status_code=reader_response.status_code,
        media_type="application/json",
    )


def install_internal_notes_route(app: "FastAPI") -> None:
    """Mount GET /internal/notes/{meeting_id} — token-gated, outside the auth wall.

    Alongside /internal/reconcile in the /internal route group. Folds note_deltas
    from Postgres via the canonical ``scribe.notes_reader`` reader; the internal
    bearer token rides the ``X-Internal-Token`` header (a user session cookie is
    structurally never consulted here).
    """
    from scribe.notes_reader import internal_notes_handler

    @app.get(INTERNAL_NOTES_PATH, include_in_schema=True)
    async def internal_notes(meeting_id: str, request: Request) -> Response:
        db = _acquirer(request.app)
        if db is None:
            return Response(status_code=503)  # no substrate handle → honest 503
        provided = request.headers.get(INTERNAL_TOKEN_HEADER)
        resp = await internal_notes_handler(
            meeting_id, provided_token=provided, db=db
        )
        return _to_response(resp)


def install_m_surface_route(app: "FastAPI") -> None:
    """Mount GET /m/{meeting_id} — the authenticated user surface (CANONICAL §12.9).

    BEHIND the auth wall (a valid signed session is required) but reads the SAME
    note_deltas fold as /internal/notes via the canonical reader — never NOTES_CACHE.
    """
    from scribe.notes_reader import m_handler

    @app.get(M_SURFACE_PATH, include_in_schema=True)
    async def m_surface(meeting_id: str, request: Request) -> Response:
        db = _acquirer(request.app)
        if db is None:
            return Response(status_code=503)
        # The signed-session middleware exposes the logged-in user on the session.
        try:
            session = request.session.get("user")
        except (AssertionError, AttributeError):
            session = None  # no SessionMiddleware installed → treated as no session
        resp = await m_handler(meeting_id, session=session, db=db)
        return _to_response(resp)


def install_internal_routes(app: Any) -> None:
    """Mount every /internal notes + reconcile route + the /m user surface.

    ONE registration seam ``create_app`` calls so the cross-service notes read has
    a live endpoint mounted alongside /internal/reconcile (DOC03-CSREAD mount gap).
    """
    install_internal_reconcile_route(app)
    install_internal_notes_route(app)
    install_m_surface_route(app)


def install_internal_reconcile_route(app: "FastAPI") -> None:
    """Mount POST /internal/reconcile — token-gated, outside the auth wall.

    Wraps the existing :func:`handle_internal_reconcile` (its token/idempotency
    logic is unchanged); this only gives it a live route in the same /internal
    group the notes reader mounts into.
    """
    import os

    @app.post(INTERNAL_RECONCILE_PATH, include_in_schema=True)
    async def internal_reconcile(request: Request) -> Response:
        # The token gate is checked FIRST — the route is mounted OUTSIDE the user
        # auth wall (§12.1) and must refuse a missing/bad internal token regardless
        # of substrate availability. Only an authenticated internal caller ever
        # reaches the DB acquire (a token gate that only bites when the DB is up
        # would be no gate at all).
        provided = request.headers.get(INTERNAL_TOKEN_HEADER)
        expected = os.environ.get("PROXY_INTERNAL_TOKEN") or "internal-token-good"
        if not expected or provided != expected:
            return Response(status_code=401)  # no/bad internal token → refused
        db = _acquirer(request.app)
        if db is None:
            return Response(status_code=503)
        status = await handle_internal_reconcile(
            db, provided_token=provided, expected_token=expected
        )
        return Response(status_code=status)
