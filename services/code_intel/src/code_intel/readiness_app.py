"""code_intel readiness surface — the GCE-MIG drain probe (Doc 00 §6).

``code_intel`` is one of the two GCE-MIG deployables (with ``meeting_runtime``); a MIG
recycle drains the instance. This module exposes the routine-drain contract the MIG
polls: a ``draining`` flag on the app plus a GET ``/readiness`` probe that returns

  * **200** while the host is serving (the MIG keeps routing tenant queries to it), and
  * **503** once the instance is draining (the MIG stops routing new work to it).

The code_intel host holds the per-tenant encrypted volume behind its internal API; this
readiness probe is the small always-on ASGI surface that lets the MIG drain it cleanly
(distinct from the §5 heartbeat/reclaim exception, which is a rare hard-drain fallback).
The drain state is kept here — local to code_intel — so this package never imports the
harness (services stay independent code packages, §3).
"""
from __future__ import annotations

from typing import Any


def init_drain_state(app: Any) -> None:
    """Initialise the routine-drain state on ``app.state`` (ready by default)."""
    app.state.draining = False


def is_draining(app: Any) -> bool:
    """True once the code_intel host has begun a routine MIG drain."""
    return bool(getattr(app.state, "draining", False))


def set_draining(app: Any, value: bool = True) -> None:
    """Set the ``draining`` flag the /readiness probe reads to answer 503/200."""
    app.state.draining = bool(value)


def readiness_status(app: Any) -> tuple[int, str]:
    """The (http_status, state) /readiness reports: 503 while draining, else 200."""
    if is_draining(app):
        return 503, "draining"
    return 200, "ready"


def build_code_intel_readiness_app() -> Any:
    """Build the code_intel readiness ASGI app exposing GET /readiness.

    A minimal Starlette app carrying the ``draining`` state + the /readiness probe the
    GCE MIG polls to drain this stateful host cleanly. Returns 503 while draining so the
    MIG stops routing new tenant queries, 200 otherwise.
    """
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    app: Any = None

    async def readiness(_request: Request) -> JSONResponse:
        status, state = readiness_status(app)
        return JSONResponse({"status": state}, status_code=status)

    app = Starlette(routes=[Route("/readiness", readiness, methods=["GET"])])
    init_drain_state(app)
    return app
