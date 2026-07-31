"""``relay`` — the host receiver for the in-sandbox meeting MCP server (SPEC §4/§5).

Native Claude, running inside the per-meeting E2B sandbox, reaches the live room through its ONE
``to_meeting`` MCP tool. The in-sandbox server (``in_meeting.sandbox_meeting_mcp``) POSTs each such
call to THIS route — ``POST /meetings/{meeting_id}/relay`` — which authenticates the per-meeting
bearer, looks up the meeting's live runtime, and lands the call on its ``MeetingConnection``. That
connection holds the real Recall/Cartesia creds (host-side, never in the sandbox) and carries the
agent's chosen medium (say/chat/dm/screen/offer/mute) to the physical pipe.

This is a **driver, not a decision** (Law 4): the agent chose the content + medium inside the
sandbox; the host only relays. World-touching stays a human click by the credential boundary (Law 3).

Scoping (§4.6): the sandbox has no user session — it authenticates with the per-meeting
``PROXY_MEETING_TOKEN`` minted at join. That is a server-to-server bearer trust plane exactly like
the ``/internal/*`` routes, so the route is stamped ``mark_internal_scoped`` (the route-enumeration
gate accepts it as scoped, not raw). A missing/wrong bearer, an unknown meeting, or a meeting with no
runtime is an honest error JSON — NEVER a crash (the never-throw boundary; a bad relay must not take
down the control plane or the agent's turn).
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

#: The route the sandbox MCP server POSTs its ``to_meeting`` calls to. Kept as a template so the
#: mount and the provisioner's relay-URL computation can never drift by a typo.
RELAY_PATH = "/meetings/{meeting_id}/relay"


def _bearer(headers: Any) -> str:
    """The bearer token from an ``Authorization: Bearer <token>`` header ("" if absent)."""
    raw = str(headers.get("authorization", "") or "")
    prefix = "Bearer "
    return raw[len(prefix):].strip() if raw.startswith(prefix) else ""


def _expected_token(app: Any, meeting_id: str) -> str:
    """The per-meeting relay token minted at join, resolved off the live runtime.

    The provisioner stashes the minted token on the meeting's :class:`MeetingRuntime` (via the
    workroom) so the relay route can compare against it without a second store. An absent runtime
    or token yields "" — which fails the constant-time compare CLOSED."""
    registry = getattr(getattr(app, "state", None), "meeting_runtimes", None)
    if registry is None:
        return ""
    runtime = registry.get(meeting_id)
    workroom = getattr(runtime, "workroom", None) if runtime is not None else None
    return str(getattr(workroom, "relay_token", "") or "")


def install_relay_route(app: "FastAPI") -> None:
    """Mount ``POST /meetings/{meeting_id}/relay`` — the in-sandbox MCP relay receiver (§4/§5).

    Never-throw boundary: EVERY failure mode returns an honest error JSON rather than raising, so a
    forged/misdirected relay can neither crash the control plane nor surface as an exception in the
    agent's turn. The route is stamped internal-token-scoped (a per-meeting bearer, not a user
    session) so the §4.6 route-enumeration gate classifies it as scoped, not raw.
    """

    @app.post(RELAY_PATH, include_in_schema=True)
    async def meeting_relay(meeting_id: str, request: Request) -> Any:
        # 1) Authenticate the per-meeting bearer (constant-time). No user session here — this is
        #    the sandbox→host trust plane keyed on the token minted at join.
        expected = _expected_token(request.app, meeting_id)
        presented = _bearer(request.headers)
        if not expected or not presented or not hmac.compare_digest(expected, presented):
            return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)

        # 2) Parse the posted intent: {content, medium, to} — the exact shape the in-sandbox MCP
        #    server sends. A malformed body is the caller's own bad input, an honest 400.
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 — a non-JSON body is the caller's fault, never a 500
            return JSONResponse({"ok": False, "error": "invalid body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "error": "invalid body"}, status_code=400)

        content = str(body.get("content", "") or "")
        medium = str(body.get("medium", "say") or "say")
        to = body.get("to")
        to = str(to) if to not in (None, "") else None

        # 3) Resolve the meeting's live connection + land the call. The runtime/connection may be
        #    absent (a relay that raced teardown, or a meeting with no workroom) — an honest 404.
        registry = getattr(request.app.state, "meeting_runtimes", None)
        runtime = registry.get(meeting_id) if registry is not None else None
        connection = getattr(runtime, "connection", None) if runtime is not None else None
        if connection is None:
            return JSONResponse({"ok": False, "error": "no live meeting"}, status_code=404)

        try:
            send = await connection.to_meeting(content, medium=medium, to=to)
        except Exception as exc:  # noqa: BLE001 — never-throw: a send fault is honest JSON, not 500
            logger.exception("meeting relay send failed (meeting=%s medium=%s)", meeting_id, medium)
            return JSONResponse(
                {"ok": False, "error": str(exc) or exc.__class__.__name__}, status_code=200
            )
        # MeetingConnection.to_meeting is itself never-throw (returns MeetingSend with ok/detail).
        return JSONResponse(
            {"ok": bool(getattr(send, "ok", True)),
             "medium": str(getattr(send, "medium", medium)),
             "detail": str(getattr(send, "detail", ""))},
            status_code=200,
        )

    # A per-meeting bearer, not a user session — the /internal-style trust plane (§4.6). Stamp it so
    # the route-enumeration gate classifies it as scoped rather than raw.
    mark_internal_scoped(meeting_relay)


def relay_url_for(meeting_id: str, *, base_url: str = "") -> str:
    """The public relay URL for a meeting: ``<base_url>/meetings/<id>/relay`` ("" if no base).

    ``base_url`` is the deployment's public origin (``PUBLIC_BASE_URL``). Empty ⇒ "" — the honest
    degrade: no reachable relay, so the agent's dynamic mediums are recorded locally only and the
    session falls back to speaking the result text. Pure string physics (Law 4)."""
    base = (base_url or os.environ.get("PUBLIC_BASE_URL", "")).strip().rstrip("/")
    if not base:
        return ""
    return f"{base}/meetings/{meeting_id}/relay"


__all__ = ["RELAY_PATH", "install_relay_route", "relay_url_for"]
