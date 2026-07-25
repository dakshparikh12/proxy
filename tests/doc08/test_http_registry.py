"""Doc-08 §4.6 — the contract-registry HTTP wrappers: protected()/PublicAuthzCtx,
the PUBLIC_ROUTES allowlist, and safeError. Real-path tests over a live FastAPI app.

These exercise the actual behaviours the node's DoD names:
  * a handler declaring ``protected()`` gets an :class:`AuthzCtx` with a non-null
    tenant, NEVER a raw Request/Response;
  * ``protected()`` 401s an anonymous caller and 403s a session with no tenant;
  * :class:`PublicAuthzCtx.tenant_id` is nullable (``str | None``);
  * ``safe_error_handler`` returns the per-status fallback for a non-validation
    error (no internal string leaks) and the issues body for a validation error.
"""
from __future__ import annotations

import dataclasses
from typing import Optional, get_type_hints

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from libs.http import (
    PUBLIC_ROUTES,
    AuthzCtx,
    PublicAuthzCtx,
    install_safe_error_handler,
    protected,
    public,
    safe_error_handler,
)


# --------------------------------------------------------------------------- #
# AuthzCtx / PublicAuthzCtx shape
# --------------------------------------------------------------------------- #
def test_public_authz_ctx_tenant_id_is_nullable() -> None:
    """PublicAuthzCtx.tenant_id must be Optional — the load-bearing type that stops
    an accidental cross-tenant read on a public route (node risk #1)."""
    hints = get_type_hints(PublicAuthzCtx)
    # Optional[str] == Union[str, None]; None must be an admissible value.
    assert hints["tenant_id"] == Optional[str]
    assert hints["user_id"] == Optional[str]
    # And it actually accepts None at construction.
    ctx = PublicAuthzCtx(user_id=None, tenant_id=None)
    assert ctx.tenant_id is None


def test_authz_ctx_tenant_id_is_non_null_str() -> None:
    """AuthzCtx.tenant_id is a plain ``str`` — non-null by construction, safe filter."""
    hints = get_type_hints(AuthzCtx)
    assert hints["tenant_id"] is str
    assert hints["user_id"] is str
    ctx = AuthzCtx(user_id="u1", tenant_id="t1")
    assert ctx.tenant_id == "t1"


def test_both_contexts_are_frozen() -> None:
    """A frozen context cannot be mutated in-flight (no tenant swap mid-handler)."""
    assert dataclasses.fields(AuthzCtx)  # is a dataclass
    with pytest.raises(dataclasses.FrozenInstanceError):
        AuthzCtx(user_id="u", tenant_id="t").tenant_id = "other"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        PublicAuthzCtx(user_id=None, tenant_id=None).tenant_id = "t"  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# protected() — server-side session, fail-closed, credentials-only handler
# --------------------------------------------------------------------------- #
def _app_with_protected(resolver) -> FastAPI:
    app = FastAPI()

    @app.get("/scoped")
    async def scoped(ctx: AuthzCtx = protected(resolver)):  # noqa: B008 - FastAPI dep
        # The handler received ONLY the context — proving tenant is usable directly.
        return {"user_id": ctx.user_id, "tenant_id": ctx.tenant_id}

    install_safe_error_handler(app)
    return app


def test_protected_yields_authzctx_with_server_side_tenant() -> None:
    """A resolved session yields an AuthzCtx; the handler never touches the request."""

    async def resolver(_request):
        return {"user_id": "u-42", "tenant_id": "tenant-A"}

    client = TestClient(_app_with_protected(resolver))
    resp = client.get("/scoped")
    assert resp.status_code == 200
    assert resp.json() == {"user_id": "u-42", "tenant_id": "tenant-A"}


def test_protected_401_when_anonymous() -> None:
    """No session ⇒ 401, and the body is the safe fallback (no internal string)."""

    async def resolver(_request):
        return None

    client = TestClient(_app_with_protected(resolver))
    resp = client.get("/scoped")
    assert resp.status_code == 401
    assert resp.json() == {"error": "Unauthorized"}


def test_protected_403_when_session_has_no_tenant() -> None:
    """A session without a tenant ⇒ 403 — a tenant-less principal is never scoped."""

    async def resolver(_request):
        return {"user_id": "u-1", "tenant_id": None}

    client = TestClient(_app_with_protected(resolver))
    resp = client.get("/scoped")
    assert resp.status_code == 403
    assert resp.json() == {"error": "Forbidden"}


def test_protected_handler_signature_excludes_raw_request() -> None:
    """The handler CANNOT declare a raw Request/Response by convention: it receives a
    credentials-only AuthzCtx. We assert the injected value is exactly an AuthzCtx —
    'read the tenant from the request' is unrepresentable in the handler body."""
    captured: dict[str, object] = {}

    async def resolver(_request):
        return {"user_id": "u", "tenant_id": "t"}

    app = FastAPI()

    @app.get("/capture")
    async def capture(ctx: AuthzCtx = protected(resolver)):  # noqa: B008
        captured["ctx"] = ctx
        return {"ok": True}

    TestClient(app).get("/capture")
    assert isinstance(captured["ctx"], AuthzCtx)
    assert captured["ctx"].tenant_id == "t"


def test_public_dep_yields_public_ctx() -> None:
    """public() yields a PublicAuthzCtx with nullable fields for a dual-mode route."""
    app = FastAPI()

    @app.get("/m/{meeting_id}")
    async def home(meeting_id: str, ctx: PublicAuthzCtx = public()):  # noqa: B008
        return {"tenant_id": ctx.tenant_id, "user_is_none": ctx.user_id is None}

    resp = TestClient(app).get("/m/abc")
    assert resp.status_code == 200
    assert resp.json() == {"tenant_id": None, "user_is_none": True}


# --------------------------------------------------------------------------- #
# safeError — per-status fallback for non-validation; issues body for validation
# --------------------------------------------------------------------------- #
def test_safe_error_collapses_internal_500_to_fallback() -> None:
    """An uncaught internal exception NEVER leaks its message to the caller."""
    app = FastAPI()

    @app.get("/boom")
    async def boom() -> dict:
        raise RuntimeError("secret internal detail: table meetings row 42")

    install_safe_error_handler(app)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/boom")
    assert resp.status_code == 500
    body = resp.json()
    assert body == {"error": "Service temporarily unavailable"}
    assert "secret internal detail" not in resp.text
    assert "meetings" not in resp.text


def test_safe_error_per_status_fallback_for_httpexception() -> None:
    """An HTTPException collapses to the per-status fallback, dropping its detail."""
    app = FastAPI()

    @app.get("/nope")
    async def nope() -> dict:
        raise HTTPException(status_code=404, detail="meeting 0xDEADBEEF not in tenant xyz")

    install_safe_error_handler(app)
    resp = TestClient(app, raise_server_exceptions=False).get("/nope")
    assert resp.status_code == 404
    assert resp.json() == {"error": "Not found"}
    assert "0xDEADBEEF" not in resp.text
    assert "tenant xyz" not in resp.text


def test_safe_error_returns_issues_for_validation_error() -> None:
    """A RequestValidationError DOES return its issues — it's the caller's bad input.

    Triggered via a typed path param (a malformed int in the URL) which reliably
    raises a ``RequestValidationError`` regardless of body/query binding. The
    contract: 422 + ``error: "invalid request"`` + a non-empty issues list that
    names the caller's own bad field (never an internal string)."""
    app = FastAPI()

    @app.get("/items/{item_id}")
    async def item(item_id: int) -> dict:  # noqa: ARG001 - only the type matters
        return {"ok": True}

    install_safe_error_handler(app)
    resp = TestClient(app, raise_server_exceptions=False).get("/items/not-an-int")
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "invalid request"
    assert isinstance(body["issues"], list) and body["issues"]
    # The issues describe the caller's own bad input (the item_id path field),
    # never an internal string.
    assert any("item_id" in str(issue.get("loc", "")) for issue in body["issues"])


def test_safe_error_validation_body_is_directly_returned() -> None:
    """The safe_error_handler routes a RequestValidationError straight to the issues
    body — proven at the handler level (independent of FastAPI's param binding)."""
    import asyncio
    import json as _json

    from fastapi.exceptions import RequestValidationError

    raw_errors = [
        {"type": "int_parsing", "loc": ("path", "meeting_id"), "msg": "bad", "input": "x"}
    ]
    exc = RequestValidationError(raw_errors)

    async def _run():
        return await safe_error_handler(None, exc)  # type: ignore[arg-type]

    resp = asyncio.run(_run())
    payload = _json.loads(bytes(resp.body).decode())
    assert resp.status_code == 422
    assert payload["error"] == "invalid request"
    assert payload["issues"], "the validation issues must be returned verbatim"
    assert any("meeting_id" in str(i.get("loc", "")) for i in payload["issues"])


def test_safe_error_unknown_status_still_opaque() -> None:
    """An exception with an unlisted status still yields a generic (never internal)."""

    class WeirdError(Exception):
        status_code = 418

    import asyncio

    async def _run():
        return await safe_error_handler(None, WeirdError("teapot internals"))  # type: ignore[arg-type]

    resp = asyncio.run(_run())
    import json

    payload = json.loads(bytes(resp.body).decode())
    assert payload == {"error": "Request failed"}
    assert "teapot internals" not in bytes(resp.body).decode()


# --------------------------------------------------------------------------- #
# PUBLIC_ROUTES allowlist shape
# --------------------------------------------------------------------------- #
def test_public_routes_is_frozenset_of_method_path_keys() -> None:
    """The allowlist is a frozenset of 'METHOD /path' strings (immutable at import)."""
    assert isinstance(PUBLIC_ROUTES, frozenset)
    for key in PUBLIC_ROUTES:
        method, _, path = key.partition(" ")
        assert method in {"GET", "POST", "PUT", "PATCH", "DELETE"}
        assert path.startswith("/")
    # The dual-mode notes home is public by allowlist (token-gated at runtime).
    assert "GET /m/{meeting_id}" in PUBLIC_ROUTES
    # Accept/reject mutations are NEVER public.
    assert not any("accept" in k or "reject" in k for k in PUBLIC_ROUTES)
