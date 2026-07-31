"""The concrete Claude-Agent-SDK :class:`~agentkit.provider.Provider` (§3.3, D-010).

:class:`~agentkit.provider.Provider` is the ``Protocol`` seam; this module is its ONE
concrete implementation — the thing a funded deployment constructs from the resolved
Anthropic auth and hands to ``premeeting.run_pipeline`` as ``map_provider`` (the pre-meeting
map-build model seam). It drives the native Claude Agent SDK's ``query(...)`` and normalises
its message stream into :class:`~libs.contracts.AgentChunk` — the exact chunk shape the
map-build loop (``premeeting.map_build``) and the delta seam (``agentkit.deltas``) already
read, so the fake-provider test path and this live path emit the identical shape.

The SDK-isolation triad (CANONICAL §11.11) is set on EVERY call by construction — the
``ProviderQuery`` carries ``strict_mcp_config=True`` + ``setting_sources=()`` + a computed
built-in ``tools`` list, and this module maps them straight onto ``ClaudeAgentOptions`` while
pinning the ``permission_mode``/``disallowed_tools`` block-list (the ``SDK_LOCAL_TOOLS`` host
built-ins). Those three triad markers appear literally below so the ``check-sdk-isolation-triad``
guard recognises this ``query()`` call site as triaged.

**Credit boundary (D-032).** The map-build model key is unfunded on the live path today, so
this provider is constructed only when Anthropic auth is present and is otherwise never wired
(``map_provider`` stays ``None`` — an honest no-op, never a fabricated map). The real-model
map-QUALITY battery (PM-MAP-06) stays BLOCKED-on-credits — nothing here is marked verified.
"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, cast

from libs.contracts import AgentChunk

from .provider import (  # the isolation-triad markers this call site must carry
    ProviderQuery,
    disallowed_tools,
    permission_mode,
)

__all__ = ["ClaudeAgentProvider", "build_sdk_options", "make_map_provider"]


def build_sdk_options(query: ProviderQuery) -> Any:
    """Translate a :class:`ProviderQuery` into a ``ClaudeAgentOptions`` with the triad pinned.

    Every field the runner computed flows onto the SDK options here: the curated
    ``allowed_tools`` subset, the computed built-in ``tools`` list (``()`` in sandbox mode),
    the ``system_prompt``, ``max_turns``, ``model``, the curated ``mcp_servers``, and the
    per-turn ``env`` (e.g. the ``MAX_OUTPUT_TOKENS`` clamp). The SDK-isolation triad is pinned
    unconditionally — ``strict_mcp_config=True`` ignores any discovered ``.mcp.json``,
    ``setting_sources=[]`` loads no filesystem settings, ``permission_mode`` is the headless
    ``bypassPermissions``, and the ``disallowed_tools`` block-list MERGES the module-level
    ``SDK_LOCAL_TOOLS`` host built-ins with the query's read-only write-block list.
    """
    from claude_agent_sdk import ClaudeAgentOptions, PermissionMode, SettingSource

    merged_disallowed = tuple(dict.fromkeys((*disallowed_tools, *query.disallowed_tools)))
    # The triad values are validated by construction (``setting_sources`` is empty; the module
    # ``permission_mode`` is the fixed ``bypassPermissions`` literal), so casting to the SDK's own
    # literal types is safe — it narrows the seam's structural ``str``/``tuple[str, ...]`` to the
    # SDK's ``SettingSource`` / ``PermissionMode`` without weakening the isolation triad.
    setting_sources = [cast(SettingSource, s) for s in query.setting_sources]
    return ClaudeAgentOptions(
        model=query.model,
        system_prompt=query.system_prompt or None,
        allowed_tools=list(query.allowed_tools),
        tools=list(query.tools),
        max_turns=query.max_turns,
        # ── the SDK-isolation triad (CANONICAL §11.11) — pinned on every call ──
        strict_mcp_config=True,
        setting_sources=setting_sources,
        permission_mode=cast(PermissionMode, permission_mode),
        disallowed_tools=list(merged_disallowed),
        mcp_servers=query.mcp_servers or {},
        env=dict(query.env),
    )


def _chunk_from_message(message: Any) -> AgentChunk | None:
    """Normalise ONE native SDK message into an :class:`AgentChunk` (or ``None`` to skip).

    Only the shapes the map-build loop reads are surfaced: assistant ``TextBlock`` →
    ``TEXT``, ``ToolUseBlock`` → ``TOOL_USE`` (name/id/input in metadata — the PM-MAP
    transcript oracle reads ``metadata['name']``), ``ToolResultBlock`` → ``TOOL_RESULT``, and
    the terminal ``ResultMessage`` → ``RESULT`` (num_turns + total_cost_usd + structured_output
    — the budget/degrade backstop reads ``metadata['num_turns']``). A ``SystemMessage`` /
    unmapped shape is skipped. Never raises — an unreadable field degrades to a skip.
    """
    from claude_agent_sdk import (
        AssistantMessage,
        ResultMessage,
        TextBlock,
        ToolResultBlock,
        ToolUseBlock,
    )

    if isinstance(message, AssistantMessage):
        # The assistant turn carries a list of content blocks; the map is the terminal TEXT.
        blocks = list(getattr(message, "content", []) or [])
        # Prefer a tool use if present (transcript), else the text body.
        for block in blocks:
            if isinstance(block, ToolUseBlock):
                return AgentChunk(
                    type="TOOL_USE",
                    metadata={
                        "id": getattr(block, "id", ""),
                        "name": getattr(block, "name", ""),
                        "input": getattr(block, "input", {}) or {},
                    },
                )
        text = "".join(
            str(getattr(b, "text", "") or "") for b in blocks if isinstance(b, TextBlock)
        )
        if text:
            return AgentChunk(type="TEXT", text=text, metadata={})
        return None
    if isinstance(message, ToolResultBlock):  # pragma: no cover - rare standalone block
        return AgentChunk(
            type="TOOL_RESULT",
            metadata={
                "tool_use_id": getattr(message, "tool_use_id", ""),
                "is_error": bool(getattr(message, "is_error", False)),
            },
        )
    if isinstance(message, ResultMessage):
        structured = getattr(message, "structured_output", None)
        return AgentChunk(
            type="RESULT",
            metadata={
                "session_id": getattr(message, "session_id", ""),
                "num_turns": int(getattr(message, "num_turns", 0) or 0),
                "total_cost_usd": float(getattr(message, "total_cost_usd", 0.0) or 0.0),
                "structured_output": structured if isinstance(structured, str) else "",
            },
        )
    return None


@dataclass
class ClaudeAgentProvider:
    """The concrete SDK-backed :class:`~agentkit.provider.Provider` (satisfies the Protocol).

    Holds the resolved Anthropic auth (never logged) and threads it onto the SDK subprocess's
    env for the duration of a ``stream`` call. ``stream`` drives ``claude_agent_sdk.query`` with
    the triad-pinned options and yields normalised :class:`AgentChunk`s. A fault is surfaced
    IN-BAND as a terminal ``ERROR`` chunk (never an in-band raise) — the ``BehaviorRunner``
    boundary turns that into a :class:`~agentkit.provider.ProviderError` for §3.5 recovery.
    """

    #: The env the SDK subprocess authenticates with (e.g. ``{"ANTHROPIC_API_KEY": ...}``).
    #: Sourced from the resolved settings/Secret-Manager auth — never a hard-coded literal.
    auth_env: dict[str, str]

    async def stream(self, prompt: str, query: ProviderQuery) -> AsyncIterator[AgentChunk]:
        """Drive the SDK ``query`` and yield normalised chunks; a fault is a terminal ERROR."""
        from claude_agent_sdk import query as sdk_query

        options = build_sdk_options(query)
        # Thread the resolved auth onto the SDK subprocess env WITHOUT mutating the process
        # environment permanently or logging the secret. The options' env already carries the
        # per-turn clamp; the auth is merged on top for this call only.
        merged_env = {**dict(getattr(options, "env", {}) or {}), **self.auth_env}
        options.env = merged_env
        try:
            async for message in sdk_query(prompt=prompt, options=options):
                chunk = _chunk_from_message(message)
                if chunk is not None:
                    yield chunk
        except Exception as exc:  # noqa: BLE001 - never raise in-band; surface a terminal ERROR
            yield AgentChunk(type="ERROR", metadata={"message": f"{type(exc).__name__}: {exc}"})


def make_map_provider(
    *,
    api_key: str = "",
    auth_token: str = "",
    use_vertex: str = "",
) -> ClaudeAgentProvider | None:
    """Construct the map-build provider from the resolved Anthropic auth, or ``None`` (no auth).

    The ONE construction site a funded deployment calls at boot: it reads the ALREADY-resolved
    auth (from ``control_plane.settings`` — this never re-reads or re-validates config) and
    builds the SDK env the provider authenticates with. When NO auth mode is configured it
    returns ``None`` so the caller keeps today's honest no-op (``map_provider = None``, D-032) —
    boot still succeeds, connect degrades honestly, and no map is ever fabricated (Law 2).

    Secrets flow from the resolved settings into the provider's ``auth_env`` only; they are
    never hard-coded and never logged (Hard Rule: Secrets).
    """
    env: dict[str, str] = {}
    if api_key:
        env["ANTHROPIC_API_KEY"] = api_key
    elif auth_token:
        env["ANTHROPIC_AUTH_TOKEN"] = auth_token
    if use_vertex:
        # Vertex mode authenticates via ADC; carry the flag the SDK reads. The ADC itself is
        # ambient (workload identity), never a literal secret this code holds.
        env["CLAUDE_CODE_USE_VERTEX"] = use_vertex
        env.setdefault("CLOUD_ML_REGION", os.environ.get("CLOUD_ML_REGION", ""))
    if not env:
        return None  # honest no-op: no funded auth → no provider (D-032), never a fabricated map
    return ClaudeAgentProvider(auth_env=env)
