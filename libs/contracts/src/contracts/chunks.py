"""AgentChunk union + ChunkType discriminator (AC-CMP-004/013/015).

``ChunkType`` is a ``Literal`` (so ``typing.get_args`` yields the six string
members for the contract oracle) that also carries attribute access
(``ChunkType.TEXT``) for ergonomic producers/consumers.
"""
from __future__ import annotations

from typing import Any, Literal, get_args

from pydantic import BaseModel, Field

from .registry import register_field_consumer

ChunkType = Literal["INIT", "TEXT", "TOOL_USE", "TOOL_RESULT", "RESULT", "ERROR"]
# Expose each member as an attribute (ChunkType.TEXT == "TEXT") without losing
# get_args() introspection. Literal aliases accept attribute assignment.
for _member in get_args(ChunkType):
    setattr(ChunkType, _member, _member)


class AgentChunk(BaseModel):
    """One streamed chunk from a behavior run. Discriminated by ``type``."""

    type: ChunkType
    text: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# ── the LIVE AgentChunk consumers name the fields they read (§4.8 field-diff) ──
# ``services/harness/provider.py`` maps every SDK message into an ``AgentChunk`` and its
# downstream consumers discriminate on ``.type`` (provider.py:261), read the accumulated
# ``.text`` (provider.py:166), and inspect ``.metadata`` (provider.py:263). Naming the
# fields the REAL consumer reads is what makes the ``.kind``→``.type`` drift a build
# failure: a consumer stuck on ``.kind`` would leave ``.type`` produced-but-unconsumed.
register_field_consumer("AgentChunk", "type", "text", "metadata")


# Per-variant metadata keys (RESULT carries total_cost_usd — the cost-meter seam).
AGENT_CHUNK_METADATA_KEYS: dict[str, set[str]] = {
    "INIT": {"session_id", "tools", "mcp_servers"},
    "TEXT": {"msg_id"},
    "TOOL_USE": {"id", "name", "input"},
    "TOOL_RESULT": {"tool_use_id", "is_error", "structured"},
    "RESULT": {"session_id", "num_turns", "total_cost_usd", "structured_output"},
    "ERROR": {"message"},
}
