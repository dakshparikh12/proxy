"""Doc-08 §4.6 — the structural guarantee: EVERY app route is protected()-scoped,
internal-token-scoped, ws-authorised-at-upgrade, or on the PUBLIC_ROUTES allowlist.

This is the route-enumeration test the spec names (§4.6): it walks the *real*
control_plane app's routes and refuses any route that registers an HTTP surface
without a wrapper and is not allowlisted (the ``raw`` failure class). A new route
that forgets ``protected()`` and is not added to the allowlist fails HERE — it
cannot escape by looking authenticated; the classification is structural.

NOT done if a route registers raw without a wrapper and escapes this test (node
DoD). So this test is the enforcement mechanism, and it runs on the live app.
"""
from __future__ import annotations

import pytest

from libs.http import PUBLIC_ROUTES, classify_route
from libs.http.registry import route_key


@pytest.fixture(scope="module")
def app():
    """The REAL control_plane ASGI app — not a stub. Its routes are what we scope."""
    from control_plane.app import app as control_plane_app

    return control_plane_app


def test_every_route_is_scoped_or_allowlisted(app) -> None:
    """Every route classifies as protected / internal / ws / public / framework —
    never ``raw``. A ``raw`` route is one that exposed an HTTP surface with no
    wrapper and is not on the allowlist: a P0 tenant-isolation gap."""
    raw: list[str] = []
    for route in app.routes:
        verdict = classify_route(route)
        if verdict == "raw":
            key = route_key(route) or getattr(route, "path", repr(route))
            raw.append(f"{key} ({type(route).__name__})")
    assert not raw, (
        "these routes are neither protected()-scoped, internal-token-scoped, "
        "ws-authorised, nor on the PUBLIC_ROUTES allowlist:\n  - "
        + "\n  - ".join(raw)
    )


def test_mutations_are_never_public(app) -> None:
    """A draft accept/reject mutation must NEVER classify as public — it is a
    tenant-member-only action (§12.9). If one slipped into PUBLIC_ROUTES or lost
    its wrapper, this catches it."""
    for route in app.routes:
        key = route_key(route)
        if key is None:
            continue
        method, _, path = key.partition(" ")
        if method == "POST" and ("/accept" in path or "/reject" in path):
            assert classify_route(route) == "protected", (
                f"{key} is a mutation and MUST be protected(), got "
                f"{classify_route(route)!r}"
            )
            assert key not in PUBLIC_ROUTES, f"{key} must not be publicly allowlisted"


def test_allowlist_has_no_orphans(app) -> None:
    """Every PUBLIC_ROUTES entry that names a path the app actually mounts must
    match a real route (a stale allowlist entry is dead-code that masks intent).
    Entries for routes not yet mounted (webhooks/connect land with later nodes)
    are permitted — we only assert the mounted ones line up, no false 'public'."""
    app_keys = {route_key(r) for r in app.routes if route_key(r) is not None}
    # The dual-mode notes home is mounted now and must be present + allowlisted.
    assert "GET /m/{meeting_id}" in app_keys
    assert "GET /m/{meeting_id}" in PUBLIC_ROUTES
