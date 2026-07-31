"""admin_routes — the authenticated tenant-offboard/deletion surface (B6).

``ops.run_reconcile_sweep(conn=..., tenant=..., gcs=..., reason=...)`` is a correct,
idempotent tenant-offboard sweep: it deletes EVERY tenant-scoped Postgres row (every
``public`` table carrying a ``tenant``/``tenant_id`` column) and the tenant's GCS
prefix namespace (``tenants/<tenant>/``). It had NO caller/route, so there was no
wired way to honour a customer deletion/offboard request (a compliance + isolation
gap). This module mounts the ONE HTTP caller:

  * ``POST /admin/tenants/{tenant_id}/offboard`` — gated behind the internal admin
    bearer (``X-Internal-Token``: ``PROXY_INTERNAL_TOKEN``, with a back-compat fall
    back to ``INTERNAL_RECONCILE_TOKEN``) compared CONSTANT-TIME (``hmac.compare_digest``).
    This is the SAME server-to-server trust plane as the ``/internal/*`` routes — NOT
    a user session — so the route is stamped ``mark_internal_scoped`` (the §4.6
    route-enumeration gate classifies it as scoped, not raw). A missing/wrong token is
    a fixed 401 and the destructive sweep is NEVER invoked.

Never-throw boundary (§4.6): a bad token is a 401; any sweep fault is an honest 500-
class JSON, never an unhandled crash. The blocking psycopg sweep runs in a worker
thread so the event loop is never stalled.
"""
from __future__ import annotations

import hmac
import logging
import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from libs.http import mark_internal_scoped

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastapi import FastAPI

logger = logging.getLogger(__name__)

#: The admin offboard route path — a single constant so the mount + any URL builder
#: can never drift by a typo.
ADMIN_OFFBOARD_PATH = "/admin/tenants/{tenant_id}/offboard"


def _internal_token_header(headers: Any) -> str:
    """The presented ``X-Internal-Token`` header value ("" if absent)."""
    return str(headers.get("x-internal-token", "") or "")


def _expected_admin_token() -> str:
    """The bound internal admin token — ``PROXY_INTERNAL_TOKEN`` then the reconcile token.

    Read at request time (not import) so a rotated secret is picked up. In prod both
    are boot hard-gates (settings, B4), so this is never the insecure dev literal on a
    running production process. Empty when neither is set → the compare fails CLOSED.
    """
    return (
        os.environ.get("PROXY_INTERNAL_TOKEN")
        or os.environ.get("INTERNAL_RECONCILE_TOKEN")
        or ""
    )


def _authorized(headers: Any) -> bool:
    """True iff the presented admin token matches the bound one (constant-time)."""
    expected = _expected_admin_token()
    presented = _internal_token_header(headers)
    if not expected or not presented:
        return False
    return hmac.compare_digest(presented, expected)


def install_admin_routes(
    app: "FastAPI",
    *,
    sweep_fn: Callable[..., Any] | None = None,
    conn_factory: Callable[[], Any] | None = None,
) -> None:
    """Mount ``POST /admin/tenants/{tenant_id}/offboard`` — the tenant-deletion caller (B6).

    ``sweep_fn`` defaults to ``ops.run_reconcile_sweep`` (the real offboard sweep);
    ``conn_factory`` defaults to a fresh autocommit psycopg connection over the app DSN
    (the same seam the connect store uses). Both are injectable so the route is provable
    offline without a live Postgres. The GCS handle is resolved off ``app.state.gcs``
    (a funded deployment wires it; absent, the sweep drops Postgres rows only and skips
    the GCS prefix delete — never a crash).
    """

    @app.post(ADMIN_OFFBOARD_PATH, include_in_schema=True)
    async def admin_offboard_tenant(tenant_id: str, request: Request) -> JSONResponse:
        # 1) Authenticate the internal admin bearer (constant-time). This is the
        #    server-to-server trust plane, NEVER a user session.
        if not _authorized(request.headers):
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        # 2) Resolve the sweep seam + a raw conn + the optional GCS handle. The sweep is
        #    a SYNC psycopg operation, so it runs in a worker thread off the event loop.
        import anyio

        if sweep_fn is not None:
            sweep: Callable[..., Any] = sweep_fn
        else:
            from libs.ops import run_reconcile_sweep

            sweep = run_reconcile_sweep

        factory = conn_factory or _default_conn_factory()
        gcs = getattr(request.app.state, "gcs", None)

        def _run() -> Any:
            conn = factory()
            try:
                return sweep(
                    conn=conn,
                    tenant=tenant_id,
                    gcs=gcs,
                    reason="admin-offboard",
                )
            finally:
                close = getattr(conn, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:  # noqa: BLE001 - a close failure is irrelevant
                        pass

        try:
            result = await anyio.to_thread.run_sync(_run)
        except Exception as exc:  # noqa: BLE001 - never-throw: honest JSON, not a crash
            logger.exception("admin offboard sweep failed (tenant=%s)", tenant_id)
            return JSONResponse(
                {"error": "offboard failed", "detail": str(exc) or exc.__class__.__name__},
                status_code=500,
            )

        body: dict[str, Any] = {"tenant_id": tenant_id, "offboarded": True}
        if isinstance(result, dict):
            body.update({k: v for k, v in result.items() if k != "tenant"})
        return JSONResponse(body, status_code=200)

    # A server-to-server bearer, not a user session — the /internal-style trust plane
    # (§4.6). Stamp it so the route-enumeration gate classifies it as scoped, not raw.
    mark_internal_scoped(admin_offboard_tenant)


def _default_conn_factory() -> Callable[[], Any]:
    """A factory that opens ONE fresh autocommit psycopg connection over the app DSN.

    Reuses the connect module's DSN resolution + psycopg factory so the admin sweep
    borrows a connection exactly like the connect store (a low-rate admin surface, so a
    fresh short-lived connection is warranted; autocommit keeps each delete durable).
    """
    from .connect import _default_dsn, _psycopg_conn_factory

    return _psycopg_conn_factory(_default_dsn())
