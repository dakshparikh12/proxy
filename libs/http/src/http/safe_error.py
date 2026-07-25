"""``safeError`` — external callers NEVER see an internal error string (§4.6).

Recall, an anonymous connect-page visitor, the forwarded-to VP: none of them ever
learns *why* a request failed beyond a per-status fallback. An internal exception
message (a stack detail, a DB error, a table name) is a leak — it hands an
attacker reconnaissance and it violates "external callers never see an internal
error" (a Doc-08 invariant). So every non-validation error collapses to a fixed
per-status body.

The ONE exception is a :class:`RequestValidationError`: that error describes the
*caller's own* bad input (a missing field, a malformed UUID), not our internals,
so returning its ``.errors()`` is safe and useful — it tells the caller how to fix
their request. Everything else is opaque.

Wire it with ``app.add_exception_handler(Exception, safe_error_handler)`` and
``app.add_exception_handler(RequestValidationError, safe_error_handler)`` — see
:func:`install_safe_error_handler`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from starlette.requests import Request
    from starlette.responses import JSONResponse

# The per-status fallback bodies. Anything not listed collapses to a generic
# "Request failed" — an unknown status must never leak an internal string either.
_FALLBACK: dict[int, str] = {
    400: "Bad request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not found",
    409: "Conflict",
    422: "Unprocessable entity",
    429: "Too many requests",
    500: "Service temporarily unavailable",
    503: "Service temporarily unavailable",
}


async def safe_error_handler(request: "Request", exc: Exception) -> "JSONResponse":
    """Collapse any handler error to a safe body — never an internal string.

    * :class:`RequestValidationError` → ``422`` with ``{"error": "invalid request",
      "issues": [...]}`` (the caller's own bad input, safe to echo).
    * Anything else → the per-status fallback body for ``exc.status_code`` (or
      ``500`` when the exception carries no status), with NO detail from the
      exception. The internal message is dropped on the floor.

    ``status_code`` is read via ``getattr`` so a bare ``Exception`` (a genuine
    unexpected 500) is handled identically to an ``HTTPException`` — both yield a
    fixed fallback, neither leaks ``str(exc)``.
    """
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse

    if isinstance(exc, RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"error": "invalid request", "issues": _jsonable(exc.errors())},
        )

    status = getattr(exc, "status_code", 500)
    if not isinstance(status, int):
        status = 500
    return JSONResponse(
        status_code=status,
        content={"error": _FALLBACK.get(status, "Request failed")},
    )


def _jsonable(errors: Any) -> Any:
    """Make ``RequestValidationError.errors()`` JSON-serialisable.

    Pydantic v2 error dicts can carry a non-serialisable ``ctx`` (e.g. the original
    exception object) or a ``bytes`` input. We coerce anything that is not a plain
    JSON scalar/container to ``str`` so the issues body always serialises — while
    still describing the caller's bad input, never our internals.
    """
    if isinstance(errors, dict):
        return {str(k): _jsonable(v) for k, v in errors.items()}
    if isinstance(errors, (list, tuple)):
        return [_jsonable(v) for v in errors]
    if isinstance(errors, (str, int, float, bool)) or errors is None:
        return errors
    return str(errors)


def install_safe_error_handler(app: Any) -> None:
    """Register :func:`safe_error_handler` for both the generic + validation errors.

    Registering it for ``Exception`` catches genuine 500s (the leaky ones); for
    ``RequestValidationError`` catches the 422s (the ones we DO return, so the
    handler routes them to the issues body). ``HTTPException`` is a subclass path
    that Starlette routes through the same handler via the ``Exception`` binding —
    we register it explicitly too so the fallback body (not Starlette's default
    ``{"detail": ...}``) is what an external caller sees.
    """
    from fastapi import HTTPException
    from fastapi.exceptions import RequestValidationError
    from starlette.exceptions import HTTPException as StarletteHTTPException

    app.add_exception_handler(RequestValidationError, safe_error_handler)
    app.add_exception_handler(HTTPException, safe_error_handler)
    app.add_exception_handler(StarletteHTTPException, safe_error_handler)
    app.add_exception_handler(Exception, safe_error_handler)


__all__ = [
    "safe_error_handler",
    "install_safe_error_handler",
]
