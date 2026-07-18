"""The metered model gateway call surface + the global in-flight semaphore.

Every model call in the product goes through :func:`call_model` — no direct
Anthropic clients elsewhere. Two DISTINCT concurrency bounds meet here:

  * ``PROXY_MAX_INFLIGHT_LLM`` (default 16) — the process-wide semaphore that
    caps the number of concurrent model calls in flight (protects the host + the
    provider rate limit). This is a seat/secret-class env knob.
  * ``PER_MEETING_CONCURRENCY`` ([3–5]) — the per-meeting fan-out, a numeric
    tunable read from ``config/defaults.toml``. It is deliberately NOT 16.

Claude SDK auth resolves from WHICHEVER of the three modes is configured
(``ANTHROPIC_API_KEY`` / ``ANTHROPIC_AUTH_TOKEN`` OAuth / Vertex) — no single
mode is hard-wired as the sole path.
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import tomllib
from collections.abc import Awaitable, Callable
from functools import lru_cache
from typing import Any

from .routing import model_for

Transport = Callable[..., Awaitable[Any]]

_DEFAULT_MAX_INFLIGHT = 16
_semaphore: asyncio.Semaphore | None = None


def _max_inflight() -> int:
    """The process-wide in-flight cap (``PROXY_MAX_INFLIGHT_LLM``, default 16)."""
    raw = os.environ.get("PROXY_MAX_INFLIGHT_LLM", str(_DEFAULT_MAX_INFLIGHT)).strip()
    try:
        return int(raw)
    except ValueError:
        return _DEFAULT_MAX_INFLIGHT


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(_max_inflight())
    return _semaphore


def reset_inflight_semaphore() -> None:
    """Drop the cached semaphore so the next call re-reads the configured cap."""
    global _semaphore
    _semaphore = None


@lru_cache(maxsize=1)
def _per_meeting_concurrency() -> int:
    """The per-meeting [3–5] fan-out — a numeric tunable from defaults.toml."""
    here = pathlib.Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "config" / "defaults.toml"
        if candidate.is_file():
            try:
                data = tomllib.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, tomllib.TOMLDecodeError):
                break
            section = data.get("llm", {})
            if isinstance(section, dict) and "per_meeting_concurrency" in section:
                return int(section["per_meeting_concurrency"])
            break
    return 4


PER_MEETING_CONCURRENCY: int = _per_meeting_concurrency()


def resolve_auth() -> dict[str, Any]:
    """Resolve Claude SDK auth from whichever of the three modes is configured.

    Returns the kwargs/env the Agent SDK client construction consumes; no mode is
    hard-wired as the only path (an API-key-only build is one valid case, Vertex
    or an OAuth token are the others).
    """
    auth: dict[str, Any] = {}
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
    use_vertex = os.environ.get("CLAUDE_CODE_USE_VERTEX", "")
    if use_vertex:
        auth["mode"] = "vertex"
    elif auth_token:
        auth["mode"] = "oauth"
        auth["auth_token"] = auth_token
    elif api_key:
        auth["mode"] = "api_key"
        auth["api_key"] = api_key
    return auth


async def _default_transport(
    *, model: str, messages: list[dict[str, Any]], **_kwargs: Any
) -> Any:
    """Real transport — construct the Claude SDK client from resolved auth.

    The concrete SDK round-trip is wired as the model gateway matures; the auth
    resolution (all three modes) is owned here so no call site touches a raw
    client.
    """
    resolve_auth()
    raise NotImplementedError(
        f"real model transport not wired yet (model={model!r}); inject a transport"
    )


async def call_model(
    *,
    seat: str,
    messages: list[dict[str, Any]],
    _transport: Transport | None = None,
    **kwargs: Any,
) -> Any:
    """Issue one model call for ``seat`` under the global in-flight semaphore."""
    model = model_for(seat)
    transport = _transport if _transport is not None else _default_transport
    async with _get_semaphore():
        return await transport(model=model, seat=seat, messages=messages, **kwargs)
