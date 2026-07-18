"""Claude Agent SDK ``query()`` config for the Workroom / repo agent.

This module is the single place that builds the tool policy and the cached
stable system-prompt prefix for the Workroom agent (the sandboxed agent that
reasons over a tenant's cloned repo).

Two standing laws are made structural here:

  * Law 3 — human control is absolute. Every world-touching primitive (``Bash``,
    ``Write``, ``Edit``, direct pushes, outbound fetches) is placed in
    ``disallowed_tools`` so the model cannot reach the world directly. The ONE
    sanctioned write is :func:`workroom.drafts.propose_change`, which stages a
    durable draft behind a human click — it is deliberately kept OUT of the
    disallowed list. (AC-INV-006)

  * Performance — the stable system-prompt prefix carries a 1-hour-TTL prompt
    cache (``cache_control`` with ``ttl=3600``), not the default 5-minute TTL,
    so the Workroom agent reuses its large repo-grounding prefix cheaply across
    a meeting. (AC-INV-001)
"""
from __future__ import annotations

from typing import Any

# The Workroom agent's sanctioned staged-draft write. Referenced (not disallowed)
# so the tool policy below stays honest about the one write path we allow:
# ``propose_change`` stages a durable draft; it never touches the world directly.
from .drafts import propose_change as propose_change

# 1-hour TTL for the stable-prefix prompt cache (seconds). The default provider
# cache TTL is 5 minutes; we explicitly widen it to one hour for the large,
# rarely-changing repo-grounding prefix.
WORKROOM_CACHE_TTL_SECONDS = 3600

# World-touching primitives the Workroom agent may NEVER invoke directly. The
# only sanctioned mutation is the staged draft (``propose_change``), which is
# intentionally absent from this list.
disallowed_tools = [
    "Bash",
    "Write",
    "Edit",
    "WebFetch",
    "WebSearch",
    "NotebookEdit",
]

# Read-only tools the Workroom agent is allowed to use while grounding on the
# tenant's clone. Mutations flow exclusively through the staged-draft path.
allowed_tools = [
    "Read",
    "Grep",
    "Glob",
]

# The stable, cacheable system-prompt prefix. This text does not change within a
# meeting, so it is a prompt-cache breakpoint carrying the 1-hour TTL.
WORKROOM_SYSTEM_PREFIX = (
    "You are Proxy, grounding on this company's codebase. Cite file:line from "
    "the current clone or say 'not found by this method'. Never push, write, or "
    "run shell commands directly: the only sanctioned change is a staged draft "
    "(propose_change) placed behind a human click."
)


def build_workroom_query_config(
    *,
    system_prefix: str = WORKROOM_SYSTEM_PREFIX,
    ttl_seconds: int = WORKROOM_CACHE_TTL_SECONDS,
) -> dict[str, Any]:
    """Build the ``query()`` config for the Workroom / repo agent.

    The returned mapping mirrors the Claude Agent SDK ``query()`` options: the
    world-touching tools are structurally blocked via ``disallowed_tools`` and
    the stable system-prefix carries a 1-hour ``cache_control`` breakpoint.
    """
    return {
        "system": [
            {
                "type": "text",
                "text": system_prefix,
                # 1-hour prompt cache on the stable prefix (ttl=3600), not the
                # default 5-minute TTL.
                "cache_control": {"type": "ephemeral", "ttl": ttl_seconds},
            }
        ],
        "allowed_tools": list(allowed_tools),
        "disallowed_tools": list(disallowed_tools),
    }
