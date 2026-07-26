"""control_plane ``GET /m/{meeting_id}`` — the authenticated per-meeting home (§2.8).

The flagship's missing home (F1 + F9): it renders the **§2.6 notes markdown**,
folded server-side from ``note_deltas`` via the SAME canonical fold
``GET /internal/notes/{meeting_id}`` uses (``scribe.notes_reader.read_notes``,
CANONICAL §11.4) — never a stale scribe cache — **plus that meeting's
``staged_drafts`` cards** (§2.4 #8) for the authenticated view.

Dual-mode, one route (§4.6):

* a **signed-in tenant member** takes it ``protected()``-equivalent — but ONLY
  after a **SERVER-SIDE ``meeting→tenant`` check**: the meeting is read scoped to
  the caller's authenticated tenant, so a member of a DIFFERENT tenant simply
  gets no row → ``Not found``. A client-supplied ``meeting_id`` is NEVER trusted
  to authorize the entity (isolation triad, invariant 9); the tenant rides the
  server-side session, never a client field.
* a **forwarded-to recipient** presents a **signed, short-TTL, meeting-scoped,
  revocable capability token** — the ONLY public entry (this route is in
  ``PUBLIC_ROUTES``). A valid grant unlocks a **read-only, notes-ONLY** view:
  **NO drafts, ever** (Law 3 — the token grants read-only notes and nothing
  world-touching; accept/reject stay ``protected()`` on a separate route).
* **neither** (no session, no token, or a wrong-tenant/tenant-less session) →
  ``Not found`` (404, the generic refusal — never a 401/leak that would tell an
  anonymous caller the meeting exists).

It is deliberately **not a dashboard**: one meeting's notes + drafts, addressable
by its ``meeting_id`` UUID — no cross-meeting list, no analytics, no history.

Two surfaces live here: :func:`meeting_home_handler` is the framework-agnostic
host-side logic (unit-testable against the real fold + tenant seam), and
:func:`install_meeting_home_route` mounts it as the LIVE ``GET /m/{meeting_id}``
route on ``control_plane`` against ``app.state.db``. Handlers never throw — every
failure collapses to an honest status (the never-throw boundary, §4.6 safeError).
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Optional

from starlette.requests import Request
from starlette.responses import Response

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastapi import FastAPI

M_SURFACE_PATH = "/m/{meeting_id}"


# ── The server-side meeting→tenant barrier (invariant 9) ──────────────────────
async def _meeting_belongs_to_tenant(conn: Any, meeting_id: str, tenant_id: str) -> bool:
    """True iff ``meeting_id`` belongs to ``tenant_id`` — the query itself is
    tenant-scoped, so a mismatched tenant simply returns no row (no leak).

    The tenant is the caller's SERVER-SIDE authenticated tenant, never a
    client-supplied field. A cross-tenant read is structurally impossible: the
    ``AND tenant_id = $2`` clause means tenant B can never resolve tenant A's
    meeting (isolation triad, invariant 9).
    """
    row = await conn.fetchrow(
        "SELECT id, tenant_id, repo_id, pinned_sha, status "
        "FROM meetings WHERE id = $1 AND tenant_id = $2",
        meeting_id,
        tenant_id,
    )
    return row is not None


async def _drafts_for_meeting(conn: Any, meeting_id: str) -> list[dict[str, Any]]:
    """That ONE meeting's staged-draft cards (§2.4 #8), oldest-first.

    Reuses the canonical per-meeting reader (``db.repos.drafts``); scoped to the
    single ``meeting_id`` the caller already proved they own — never a
    cross-meeting list (§2.8 is not a dashboard).
    """
    from db.repos.drafts import list_drafts_for_meeting

    rows = await list_drafts_for_meeting(conn, meeting_id)
    cards: list[dict[str, Any]] = []
    for r in rows:
        cards.append(
            {
                "draft_id": str(r.get("draft_id")),
                "kind": r.get("kind"),
                "summary": r.get("summary"),
                "status": r.get("status"),
            }
        )
    return cards


async def _folded_notes(meeting_id: str, db: Any) -> Any:
    """The §2.6 notes object, folded from ``note_deltas`` via the CANONICAL reader.

    Delegates to ``scribe.notes_reader.read_notes`` — the SAME deterministic
    left-fold ``GET /internal/notes/{meeting_id}`` and ``m_handler`` share
    (CANONICAL §11.4). Notes are NEVER read from ``NOTES_CACHE`` or any other
    source on this path; the fold-from-``note_deltas`` reader is the only origin.
    """
    from scribe.notes_reader import read_notes

    return await read_notes(meeting_id, db=db)


def _render_body(notes: Any, drafts: list[dict[str, Any]]) -> str:
    """The §2.6 notes markdown object + the §2.4 #8 draft cards, one JSON body.

    ``notes.to_serializable()`` is the byte-stable folded notes object (entries,
    current_goal, freshness_flag). ``drafts`` is the per-meeting card list — an
    EMPTY list for the token path (notes only, no drafts).
    """
    payload = dict(notes.to_serializable())
    payload["drafts"] = drafts
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


class HomeResponse:
    """A framework-agnostic response: a status code + an optional JSON body.

    Body is ``None`` for every refusal/degradation status (404/503), so a check
    can assert an error status NEVER carries a notes object or draft leak.
    """

    __slots__ = ("status_code", "body")

    def __init__(self, status_code: int, body: Optional[str] = None) -> None:
        self.status_code = status_code
        self.body = body


async def meeting_home_handler(
    meeting_id: str,
    *,
    session: Any,
    cap_grant: Any,
    db: Any,
) -> HomeResponse:
    """Render the §2.8 home for ONE meeting — dual-mode, fail-closed, never throws.

    Order of decisions (fail-closed):

    1. **Token grant** (the only public entry): a valid capability grant reads the
       §2.6 notes read-ONLY — **drafts are ``[]``, never returned to a token
       bearer** (Law 3). An empty ledger → ``Not found`` (404).
    2. **Session path**: a signed-in member with a non-null tenant. The
       ``meeting→tenant`` check runs SERVER-SIDE; a wrong-tenant / tenant-less /
       unknown meeting yields ``Not found`` (404) — never a leak. A member who
       owns the meeting sees the notes + that meeting's staged-draft cards.
    3. **Neither** → ``Not found`` (404, generic).

    A substrate failure degrades to an honest **503** (never a fabricated 200,
    never an unhandled exception — the never-throw boundary, §4.6).
    """
    try:
        # (1) Capability-token path — notes only, NEVER drafts (Law 3).
        if cap_grant is not None:
            notes = await _folded_notes(meeting_id, db)
            if notes.freshness_flag.delta_count == 0:
                return HomeResponse(404)  # unknown meeting — generic Not found
            return HomeResponse(200, _render_body(notes, []))

        # (2) Session path — server-side meeting→tenant check FIRST.
        tenant_id = None
        if isinstance(session, dict):
            tenant_id = session.get("tenant_id")
        if session and tenant_id:
            async with db.acquire() as conn:
                if not await _meeting_belongs_to_tenant(
                    conn, str(meeting_id), str(tenant_id)
                ):
                    # A different tenant OR an unknown meeting — both Not found.
                    return HomeResponse(404)
                drafts = await _drafts_for_meeting(conn, str(meeting_id))
            notes = await _folded_notes(meeting_id, db)
            if notes.freshness_flag.delta_count == 0:
                return HomeResponse(404)
            return HomeResponse(200, _render_body(notes, drafts))

        # (3) No token, no (tenant-bearing) session → Not found (generic).
        return HomeResponse(404)
    except Exception:  # noqa: BLE001 — any substrate failure degrades honestly (5xx)
        return HomeResponse(503)


def install_meeting_home_route(app: "FastAPI") -> None:
    """Mount the LIVE ``GET /m/{meeting_id}`` dual-mode home on ``control_plane``.

    The route pulls the capability token off the ``?token=`` query, the session
    off the signed cookie (server-side — never a client body field), and the DB
    acquirer off ``app.state.db``. It re-checks the token's own signed
    ``meeting_id`` against the path via ``verify_capability_token`` (a client
    ``meeting_id`` is never trusted), and delegates to :func:`meeting_home_handler`.

    It is in ``PUBLIC_ROUTES`` (its public exemption is the scoped token); a
    missing substrate handle is an honest 503, never a fabricated 200.
    """
    from libs.ops import verify_capability_token

    @app.get(M_SURFACE_PATH, include_in_schema=True)
    async def meeting_home(
        meeting_id: str, request: Request, token: str | None = None
    ) -> Response:
        db = getattr(request.app.state, "db", None)
        if db is None:
            return Response(status_code=503)  # no substrate handle → honest 503

        # The capability path re-checks the token's OWN signed meeting_id against
        # the path (verify returns a grant only for a same-meeting, unexpired,
        # unrevoked notes:read token — else None). A None grant falls to the
        # session path, so a garbage/wrong-meeting/expired/revoked token can never
        # 500 the public route and never reads another meeting.
        grant = verify_capability_token(token, str(meeting_id))

        session: Any = None
        if grant is None:
            # Resolve the signed-in tenant member from the DURABLE session — the HMAC
            # 'session' cookie ``auth_callback`` writes via ``complete_signin`` (the sessions
            # row is the source of truth) — NOT the ``request.session["user"]`` SessionMiddleware
            # dict, which the OAuth callback deliberately never populates (§2.8 convergence: one
            # cookie, one source of truth). No/invalid cookie → None → Not found; the capability
            # token path above is separate and unaffected.
            try:
                from harness.session import resolve_session

                session = await resolve_session(db, request.cookies)
            except Exception:  # noqa: BLE001 - a resolution fault is treated as no session (fail-closed)
                session = None

        resp = await meeting_home_handler(
            str(meeting_id), session=session, cap_grant=grant, db=db
        )
        if resp.body is None:
            return Response(status_code=resp.status_code)
        return Response(
            content=resp.body,
            status_code=resp.status_code,
            media_type="application/json",
        )
