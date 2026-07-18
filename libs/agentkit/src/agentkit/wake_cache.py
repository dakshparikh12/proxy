"""Orchestrator-wake stable-prefix prompt cache (1-hour TTL).

The orchestrator wake loop re-sends a large, identical system/tool prefix on
every transcript-driven wake. That prefix is marked with an ``ephemeral``
``cache_control`` breakpoint carrying the 1-HOUR TTL (``ttl=3600``) rather than
the SDK default 5-minute one, so the stable prefix is served from cache across
the whole meeting instead of only for five minutes.

This is the orchestrator-wake counterpart to the Scribe cache: caching must not
be Scribe-only — the wake prefix and the Workroom prefix are cached too.
"""
from __future__ import annotations

from typing import Any

# 1-hour prompt-cache TTL (ttl=3600), NOT the default 5-minute breakpoint.
CACHE_TTL_SECONDS = 3600  # ttl="1h"


def _breakpoint() -> dict[str, Any]:
    """An ``ephemeral`` cache_control breakpoint pinned to the 1-hour TTL."""
    return {"type": "ephemeral", "ttl": CACHE_TTL_SECONDS}


def build_wake_cache_prefix(
    *, system_prompt: str, tool_schema: str
) -> list[dict[str, Any]]:
    """Build the orchestrator-wake stable prefix as cached content blocks.

    The final block of the stable prefix carries the ``cache_control``
    breakpoint so everything up to it is cached for one hour.
    """
    cache_control = _breakpoint()
    return [
        {"type": "text", "text": system_prompt},
        {"type": "text", "text": tool_schema, "cache_control": cache_control},
    ]
