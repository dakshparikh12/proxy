"""BUG 1 — connect binds under the caller's SESSION tenant, not a random one.

``POST /connect/install/start`` used to key the install on ``_tenant_for_install(repo_url,
installation_account=None)``, which fell to a fresh ``uuid.uuid4()`` because nobody ever
passed ``installation_account``. But ``POST /meetings`` reads the repo under
``ctx.tenant_id`` off the SIGNED SESSION — so the connect tenant and the invite tenant never
reconciled and the invite 404'd even after a clean index.

These prove the fix: the connect flow now resolves the caller's session and binds the install
(and thus, downstream, the ``repos`` row via ``_bind_repo_row``) under the SAME
``session.tenant_id`` the invite reads — so connect-tenant == session-tenant == invite-read
tenant. An explicit ``installation_account`` override (the future sessionless GitHub-App
install callback) still keeps its own account-derived tenant; an anonymous connect with no
session still falls to a fresh random tenant (never the shareable repo URL).
"""
from __future__ import annotations

import uuid
from typing import Any

from starlette.testclient import TestClient

_REPO_URL = "https://github.com/calcom/cal.com"
_SESSION_TENANT = "11111111-1111-1111-1111-111111111111"


class _CapturingStore:
    """A ConnectStore stand-in that records the tenant each ``new_install`` was bound under."""

    def __init__(self) -> None:
        self.new_install_calls: list[tuple[str, str]] = []

    def new_install(self, tenant_id: str, repo_url: str) -> str:
        self.new_install_calls.append((tenant_id, repo_url))
        return "install-xyz"


def _app_with(store: _CapturingStore, resolved_tenant: str | None) -> Any:
    """A real app whose connect store is faked + whose session resolves to a fixed tenant.

    The install/start handler spawns a background trigger thread; we stub the module seam
    ``_spawn_trigger`` to a no-op so the route body (the tenant-binding under test) runs
    without any real clone/map build.
    """
    from control_plane.app import create_app

    app = create_app()
    app.state.connect_store = store

    async def _fake_resolve(_db: Any, _cookies: Any) -> dict[str, Any] | None:
        if resolved_tenant is None:
            return None
        return {"user_id": "u", "tenant_id": resolved_tenant}

    import control_plane.connect as connect_mod

    connect_mod._resolve_session_for_status = _fake_resolve  # type: ignore[attr-defined]
    connect_mod._spawn_trigger = lambda *a, **k: None  # type: ignore[attr-defined]
    app.state.db = object()  # a truthy db so the route attempts a resolve
    return app


def test_install_start_binds_under_the_session_tenant() -> None:
    """A signed-in caller's connect binds the install under THEIR session tenant (BUG 1)."""
    store = _CapturingStore()
    app = _app_with(store, resolved_tenant=_SESSION_TENANT)
    with TestClient(app) as client:
        resp = client.post("/connect/install/start", json={"repo_url": _REPO_URL})
    assert resp.status_code == 200
    assert store.new_install_calls == [(_SESSION_TENANT, _REPO_URL)]


def test_session_tenant_matches_the_invite_read_tenant() -> None:
    """The bound tenant is EXACTLY the tenant POST /meetings resolves off the session.

    Proves connect-tenant == session-tenant == invite-read tenant: the tenant the install
    (and, downstream, ``_bind_repo_row``) lands under is the identical value ``ctx.tenant_id``
    carries into ``get_repo_for_tenant`` — so a clean index resolves, no 404.
    """
    store = _CapturingStore()
    app = _app_with(store, resolved_tenant=_SESSION_TENANT)
    with TestClient(app) as client:
        client.post("/connect/install/start", json={"repo_url": _REPO_URL})
    bound_tenant, _ = store.new_install_calls[0]
    assert bound_tenant == _SESSION_TENANT  # the same value the invite's ctx.tenant_id holds


def test_explicit_installation_account_override_still_wins() -> None:
    """An explicit installation_account (sessionless install callback) keeps its own tenant."""
    store = _CapturingStore()
    # A session resolves, but the explicit override must take precedence over it.
    app = _app_with(store, resolved_tenant=_SESSION_TENANT)
    with TestClient(app) as client:
        client.post(
            "/connect/install/start",
            json={"repo_url": _REPO_URL, "installation_account": "acme-org"},
        )
    bound_tenant, _ = store.new_install_calls[0]
    # The account-derived tenant is stable/deterministic and is NOT the session tenant.
    assert bound_tenant != _SESSION_TENANT
    from control_plane.connect import _tenant_for_install

    assert bound_tenant == _tenant_for_install(_REPO_URL, installation_account="acme-org")


def test_anonymous_connect_falls_to_a_fresh_random_tenant() -> None:
    """No session + no override → a fresh random tenant (never the shareable repo URL)."""
    store = _CapturingStore()
    app = _app_with(store, resolved_tenant=None)
    with TestClient(app) as client:
        client.post("/connect/install/start", json={"repo_url": _REPO_URL})
    bound_tenant, _ = store.new_install_calls[0]
    # A real uuid4 (a valid tenants.id) — not the repo URL, not the session sentinel.
    assert bound_tenant != _REPO_URL
    assert uuid.UUID(bound_tenant)  # parses as a real uuid
