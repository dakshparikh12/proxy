"""AgentChunk union + ChunkType discriminator (AC-CMP-004/013/015).

``ChunkType`` is a ``Literal`` (so ``typing.get_args`` yields the six string
members for the contract oracle) that also carries attribute access
(``ChunkType.TEXT``) for ergonomic producers/consumers.
"""
from __future__ import annotations

from typing import Any, Literal, get_args

from pydantic import BaseModel, Field

ChunkType = Literal["INIT", "TEXT", "TOOL_USE", "TOOL_RESULT", "RESULT", "ERROR"]
# Expose each member as an attribute (ChunkType.TEXT == "TEXT") without losing
# get_args() introspection. Literal aliases accept attribute assignment.
for _member in get_args(ChunkType):
    setattr(ChunkType, _member, _member)


class AgentChunk(BaseModel):
    """One streamed chunk from a behavior run. Discriminated by ``type``.

    ``text`` is ``str | None`` (default ``None``), NOT ``str = ''`` (A16 / C-CHUNKNULL).
    CANONICAL §1.1 sketches ``str = ''``, but ``str | None`` is the DELIBERATE, sealed
    shape here: only the ``TEXT`` variant carries text; every other variant (``INIT``/
    ``TOOL_USE``/``TOOL_RESULT``/``RESULT``/``ERROR``) legitimately has NO text, and
    ``None`` says "absent" honestly rather than conflating it with an empty string. The
    sealed contract tests construct the non-text variants as ``text=None``
    (``tests/doc00/test_m00_cmp.py`` T-CMP-015, ``tests/doc00/test_w_workflows.py`` W11),
    so ``None`` is locked in and cannot be narrowed to ``str = ''`` without editing a
    sealed test. ``None`` is a safe SUPERSET of ``''``: every consumer that reads a chunk's
    text guards it — the load-bearing one is ``agentkit.deltas.stream_deltas`` at
    ``deltas.py:49`` (``accumulated = chunk.text or ""``), which is the single delta
    seam every downstream consumer reads through. Non-TEXT chunks never reach that read
    with a meaningful body. This is ONE consistent contract: producers emit ``None`` on
    non-TEXT variants; the sole text consumer coalesces ``None``→``""``.
    """

    type: ChunkType
    text: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# ── the LIVE AgentChunk consumer reads are DERIVED, not hand-listed (§4.8 field-diff) ──
# The fields the real consumers read off an ``AgentChunk`` (``.type`` discriminator,
# ``.text``, ``.metadata``) are NOT declared here — a hand-list co-located with the model
# would be a tautology (it would just restate ``model_fields``) and could not fail when a
# real consumer renames ``chunk.type``→``chunk.kind``. Instead ``contracts.contract_reads``
# AST-sweeps ``services/*/src`` (e.g. provider.py:261 ``chunk.type``, projector.py:108,
# session.py:840) and records the ATTRIBUTE reads the product code actually performs. That
# is what makes the ``.kind``→``.type`` drift a real build failure on the real path.


# Per-variant metadata keys (RESULT carries total_cost_usd — the cost-meter seam).
AGENT_CHUNK_METADATA_KEYS: dict[str, set[str]] = {
    "INIT": {"session_id", "tools", "mcp_servers"},
    "TEXT": {"msg_id"},
    "TOOL_USE": {"id", "name", "input"},
    "TOOL_RESULT": {"tool_use_id", "is_error", "structured"},
    "RESULT": {"session_id", "num_turns", "total_cost_usd", "structured_output"},
    "ERROR": {"message"},
}
