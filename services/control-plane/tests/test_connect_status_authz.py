"""B7 — GET /connect/status is bound to the caller's authenticated tenant.

The poll was a PUBLIC bearer-handle read: anyone holding the opaque ``install_id``
could read that install's readiness + repo_url. It is now bound to the caller's
session — the install must belong to the caller's tenant, else it is refused. The
honest readiness states are preserved for the legitimate owner.
"""
from __future__ import annotations

from typing import Any

from starlette.testclient import TestClient


class _FakeStore:
    """In-memory ConnectStore stand-in: one install owned by ``tenant`` at ``status``."""

    def __init__(self, install_id: str, tenant: str, status_: str = "ready") -> None:
        self._install_id = install_id
        self._tenant = tenant
        self._status = status_

    def _row_tenant(self, install_id: str) -> str | None:
        return self._tenant if install_id == self._install_id else None

    def tenant_for_install(self, install_id: str) -> str | None:
        return self._row_tenant(install_id)

    def status(self, install_id: str) -> Any:
        from contracts.readiness import ReadinessReport

        if install_id != self._install_id:
            return ReadinessReport(status="connecting")
        return ReadinessReport(status=self._status, coverage_pct=100.0, gaps=[])

    def flagged_files(self, install_id: str) -> list[Any]:
        return []


def _app_with(store: _FakeStore, resolved_tenant: str | None) -> Any:
    """A real app whose connect store is faked + whose session resolves to a fixed tenant."""
    from control_plane.app import create_app

    app = create_app()
    app.state.connect_store = store

    async def _fake_resolve(_db: Any, _cookies: Any) -> dict[str, Any] | None:
        if resolved_tenant is None:
            return None
        return {"user_id": "u", "tenant_id": resolved_tenant}

    # The route resolves the session via control_plane.session.resolve_session — patch it.
    import control_plane.connect as connect_mod

    connect_mod._resolve_session_for_status = _fake_resolve  # type: ignore[attr-defined]
    app.state.db = object()  # a truthy db so the route attempts a resolve
    return app


def test_owner_with_matching_tenant_gets_the_row() -> None:
    """The legitimate owner (session tenant == install tenant) reads the honest row."""
    store = _FakeStore("inst-1", tenant="tenant-A", status_="ready")
    app = _app_with(store, resolved_tenant="tenant-A")
    with TestClient(app) as client:
        resp = client.get("/connect/status", params={"install_id": "inst-1"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


def test_no_session_is_refused() -> None:
    """A request with no valid session for that install is refused (never leaks the row)."""
    store = _FakeStore("inst-1", tenant="tenant-A")
    app = _app_with(store, resolved_tenant=None)
    with TestClient(app) as client:
        resp = client.get("/connect/status", params={"install_id": "inst-1"})
    assert resp.status_code in (401, 403, 404)
    assert resp.json().get("status") != "ready"


def test_wrong_tenant_is_refused() -> None:
    """A session for a DIFFERENT tenant cannot read this install's readiness (cross-tenant)."""
    store = _FakeStore("inst-1", tenant="tenant-A")
    app = _app_with(store, resolved_tenant="tenant-B")
    with TestClient(app) as client:
        resp = client.get("/connect/status", params={"install_id": "inst-1"})
    assert resp.status_code in (403, 404)
    assert resp.json().get("status") != "ready"


def test_unknown_install_is_refused_not_leaked() -> None:
    """An unknown install id for an authed caller is a clean refusal, never a fabricated row."""
    store = _FakeStore("inst-1", tenant="tenant-A")
    app = _app_with(store, resolved_tenant="tenant-A")
    with TestClient(app) as client:
        resp = client.get("/connect/status", params={"install_id": "does-not-exist"})
    assert resp.status_code in (403, 404)
