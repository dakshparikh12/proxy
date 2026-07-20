"""Langfuse tracing scaffold — wired but INERT by default (Doc 00 §13).

The agent SDK is trace-wrapped with Langfuse's ``@observe`` decorator so every
``query()`` is a span when tracing is enabled. The scaffold is INERT by default:
``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY`` are read from the environment and
unset by default, so no data leaves the process and no self-hosted analytics
backend is installed (that is deferred Expansion). ``flush_tracing()`` is the single
scaffold flush, awaited inside the server's parallel shutdown ``gather`` so buffered
spans are drained before the process exits.
"""
from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any, TypeVar

_F = TypeVar("_F", bound=Callable[..., Any])


def _langfuse_enabled() -> bool:
    """True only when BOTH Langfuse keys are present (inert otherwise)."""
    return bool(os.environ.get("LANGFUSE_PUBLIC_KEY")) and bool(
        os.environ.get("LANGFUSE_SECRET_KEY")
    )


def observe(fn: _F) -> _F:
    """Langfuse ``@observe`` trace-wrap around an SDK ``query()`` call.

    When the scaffold is inert (keys unset) this is a transparent pass-through;
    when enabled it defers to Langfuse's real ``observe`` decorator. Importing
    Langfuse is lazy/guarded so the wrap is a no-op when the dep is absent.
    """
    if not _langfuse_enabled():
        return fn
    try:
        from langfuse.decorators import (
            observe as _langfuse_observe,
        )
    except ModuleNotFoundError:
        return fn
    return _langfuse_observe(fn)  # type: ignore[no-any-return]


async def flush_tracing() -> None:
    """Flush buffered Langfuse spans (no-op while the scaffold is inert).

    Awaited inside the server's parallel shutdown ``gather`` so trace spans are
    drained before the process exits. Best-effort: a flush failure never blocks
    shutdown.
    """
    if not _langfuse_enabled():
        return None
    try:
        from langfuse import Langfuse
    except ModuleNotFoundError:
        return None
    try:
        Langfuse().flush()
    except Exception:  # noqa: BLE001 — flush is best-effort on the shutdown path
        return None
    return None
