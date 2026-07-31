"""B6 — the authenticated admin tenant-offboard route wires the deletion sweep.

``ops.run_reconcile_sweep(conn=..., tenant=..., gcs=..., reason=...)`` deletes every
tenant-scoped Postgres row + the tenant's GCS prefixes, but had NO caller/route — so
there was no wired way to honour a deletion/offboard request. This mounts
``POST /admin/tenants/{tenant_id}/offboard`` behind the internal admin token
(constant-time compare) and drives that sweep. These prove:

  * an AUTHENTICATED admin call invokes the sweep for the named tenant;
  * an unauthenticated (missing/wrong token) call is refused (401) and the sweep is
    NEVER invoked;
  * the token compare is constant-time (``hmac.compare_digest``), never a naked ``==``.
"""
from __future__ import annotations

import inspect
from typing import Any

from starlette.testclient import TestClient


class _FakeConn:
    """A raw-conn stand-in the offboard seam borrows (never touched by these tests)."""

    def close(self) -> None:  # pragma: no cover - trivial
        pass


def _app_with_admin(monkeypatch, token: str = "admin-token") -> tuple[Any, dict[str, Any]]:
    """A real app with the admin offboard route + a recorded sweep seam."""
    from control_plane import admin_routes

    monkeypatch.setenv("PROXY_INTERNAL_TOKEN", token)

    calls: dict[str, Any] = {"count": 0, "tenant": None, "gcs": None, "reason": None}

    def _fake_sweep(*, conn: Any, tenant: str, gcs: Any = None, reason: str | None = None) -> Any:
        calls["count"] += 1
        calls["tenant"] = tenant
        calls["gcs"] = gcs
        calls["reason"] = reason
        return {"tenant": tenant, "rows_deleted": 7, "reason": reason}

    from fastapi import FastAPI

    app = FastAPI()
    from libs.http import install_safe_error_handler

    install_safe_error_handler(app)
    sentinel_gcs = object()
    app.state.gcs = sentinel_gcs
    admin_routes.install_admin_routes(
        app, sweep_fn=_fake_sweep, conn_factory=lambda: _FakeConn()
    )
    calls["sentinel_gcs"] = sentinel_gcs
    return app, calls


def test_authenticated_admin_call_invokes_the_sweep(monkeypatch) -> None:
    """A valid admin token drives run_reconcile_sweep for the named tenant, with the GCS handle."""
    app, calls = _app_with_admin(monkeypatch, token="admin-token")
    with TestClient(app) as client:
        resp = client.post(
            "/admin/tenants/tenant-XYZ/offboard",
            headers={"X-Internal-Token": "admin-token"},
        )
    assert resp.status_code == 200, resp.text
    assert calls["count"] == 1
    assert calls["tenant"] == "tenant-XYZ"
    assert calls["gcs"] is calls["sentinel_gcs"]  # the sweep got the app's GCS handle
    assert resp.json()["rows_deleted"] == 7


def test_missing_token_is_refused_and_sweep_never_runs(monkeypatch) -> None:
    """No token → 401 and the destructive sweep is NEVER invoked."""
    app, calls = _app_with_admin(monkeypatch, token="admin-token")
    with TestClient(app) as client:
        resp = client.post("/admin/tenants/tenant-XYZ/offboard")
    assert resp.status_code == 401
    assert calls["count"] == 0


def test_wrong_token_is_refused_and_sweep_never_runs(monkeypatch) -> None:
    """A wrong token → 401 and the destructive sweep is NEVER invoked."""
    app, calls = _app_with_admin(monkeypatch, token="admin-token")
    with TestClient(app) as client:
        resp = client.post(
            "/admin/tenants/tenant-XYZ/offboard",
            headers={"X-Internal-Token": "not-the-token"},
        )
    assert resp.status_code == 401
    assert calls["count"] == 0


def test_admin_token_compare_is_constant_time() -> None:
    """The admin-token gate uses hmac.compare_digest — no naked == on the secret."""
    from control_plane import admin_routes

    src = inspect.getsource(admin_routes)
    assert "compare_digest" in src, "admin-token compare must be constant-time"


def test_admin_route_is_registered_via_connect_routes(monkeypatch) -> None:
    """install_connect_routes mounts the admin offboard route (app.py already calls it)."""
    monkeypatch.setenv("PROXY_INTERNAL_TOKEN", "admin-token")
    from control_plane.app import create_app

    app = create_app()
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/admin/tenants/{tenant_id}/offboard" in paths


def test_admin_route_classifies_internal_not_raw(monkeypatch) -> None:
    """The route is stamped internal-scoped so the §4.6 enumeration gate accepts it (not raw)."""
    monkeypatch.setenv("PROXY_INTERNAL_TOKEN", "admin-token")
    from control_plane.app import create_app
    from libs.http import classify_route

    app = create_app()
    for route in app.routes:
        if getattr(route, "path", "") == "/admin/tenants/{tenant_id}/offboard":
            assert classify_route(route) == "internal"
            return
    raise AssertionError("admin offboard route not found")
