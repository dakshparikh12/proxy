"""The in-meeting engine's OWN SDK-options builder — the cached-prefix seam (SPEC §8).

Proxy is ONE Claude Agent SDK session living in a meeting. Every wake turn wakes
with the same STABLE prefix — the Proxy prime + the pre-meeting ``index.md`` map —
riding ``system_prompt`` as plain text, and the volatile ask passed to ``query()``
as the prompt. This module builds the ``ClaudeAgentOptions`` for such a turn:

  * **The SDK-isolation triad on EVERY call** — ``strict_mcp_config=True`` (ignore
    all discovered ``.mcp.json`` / user settings / claude.ai connectors),
    ``setting_sources=[]`` (load no filesystem permissions/hooks/CLAUDE.md — both
    are required; neither suppresses connectors alone), a computed built-in
    ``tools`` list (``()`` in sandbox mode: no host-side ``Read``/``Grep``/``Bash``),
    and the ``SDK_LOCAL_TOOLS`` block-list pinned into ``disallowed_tools``.
  * **The 1-hour prompt-cache directive** — ``extra_args["system-prompt-cache-ttl"]
    = "1h"`` (the SDK's CLI passthrough; proven at
    ``services/workroom/src/workroom/agent_config.py:265-284``), so the CLI marks
    the system-prompt breakpoint with a 1-hour TTL and the stable prime+map prefix
    is cached across wake turns ("map = cached prefix ~90% cheaper", SPEC §8).
    It is UNCONDITIONAL: every engine turn caches the stable prefix.

The options-building logic is ported (not imported) from the old brain's seam —
``services/harness/src/harness/provider.py:349-411`` (``build_sdk_options``), its
triad constants (``agentkit/provider.py:42-56``, re-exported there at 75-83) and
``_thinking_config`` (329-346) — per DECISION 1: the engine owns its own thin
provider with NO old-brain coupling. This module is pure construction:
deterministic, offline, no network, no keys.
"""
from __future__ import annotations

from typing import Any, Final

from agentkit import ProviderQuery
from claude_agent_sdk import ClaudeAgentOptions

# The SDK-isolation triad markers the ``check-sdk-isolation-triad`` guard requires
# a query()-hosting module to carry (CANONICAL §11.11), defined IN this module so
# the engine satisfies the guard on its own — values mirror the seam verbatim
# (agentkit/provider.py:42-56): no built-in host tool executes outside the sandbox,
# and no discovered .mcp.json / connector leaks in.
SDK_LOCAL_TOOLS: tuple[str, ...] = ("Read", "Grep", "Glob", "Bash", "Write", "Edit")
# The isolation permission mode pinned for every engine SDK call. An engine turn is a
# HEADLESS SERVER AGENT — there is no human at a terminal to answer a tool-permission
# prompt, so ``permission_mode="default"`` would leave every tool call waiting on a
# prompt that a non-interactive subprocess auto-DENIES (the silent no-op).
# ``bypassPermissions`` is the only workable mode; isolation is NOT weakened by it —
# the real gate is the curated ``tools`` list (``()`` in sandbox mode) +
# ``strict_mcp_config`` + ``setting_sources=[]`` + ``disallowed_tools``.
# (``Final`` — not ``str`` — so mypy sees the SDK's PermissionMode literal; same value.)
permission_mode: Final = "bypassPermissions"
# World-touching built-ins that must never be advertised to an engine turn — they
# would run on the engine host, not in the sandbox. Kept OUT of every computed list.
disallowed_tools: tuple[str, ...] = SDK_LOCAL_TOOLS

#: The 1-hour prompt-cache TTL on the stable-prefix breakpoint (SPEC §8): the
#: Messages-API "1h" wire token, carried as the CLI passthrough the SDK enforces.
SYSTEM_PROMPT_CACHE_TTL: str = "1h"


def _thinking_config(query: ProviderQuery) -> dict[str, Any] | None:
    """The ThinkingConfig for this turn (ported from the old brain, lines 329-346).

    OFF (``None`` — no thinking preamble) whenever ``query.thinking_enabled`` is
    False. ON for a real reasoning turn: adaptive (budget-less on-mode) for the
    opus/fable reasoning families, else the enabled config with the capped budget.
    """
    if not query.thinking_enabled:
        return None
    model = query.model
    if model.startswith("claude-opus") or model.startswith("claude-fable"):
        return {"type": "adaptive"}
    budget = query.thinking_budget_tokens or 3000
    return {"type": "enabled", "budget_tokens": budget}


def build_engine_options(query: ProviderQuery) -> ClaudeAgentOptions:
    """Build the ``ClaudeAgentOptions`` for one engine turn: the isolation triad by
    construction + the 1-hour prompt-cache directive on the stable prefix.

    ``query.system_prompt`` is the STABLE, cached prefix (prime + map) — the
    volatile per-turn ask never rides it (it is passed to ``query()`` as the
    prompt by the context-assembly layer). ``extra_args`` carries the
    ``system-prompt-cache-ttl`` CLI passthrough unconditionally; a
    ``query.extra``-provided ``extra_args`` sub-dict is merged, but the cache-ttl
    always wins.
    """
    # The 1-hour stable-prefix cache directive — unconditional on every engine turn.
    extra_raw = query.extra.get("extra_args") if query.extra else None
    extra_args: dict[str, str | None] = dict(extra_raw) if isinstance(extra_raw, dict) else {}
    extra_args["system-prompt-cache-ttl"] = SYSTEM_PROMPT_CACHE_TTL

    options = ClaudeAgentOptions(
        model=query.model,
        allowed_tools=list(query.allowed_tools),
        # Computed built-in list — empty in sandbox mode (no host Read/Grep/Bash). See the
        # mcp_servers block below for why an EMPTY list is NOT pinned when servers are mounted.
        tools=list(query.tools),
        disallowed_tools=list(disallowed_tools),
        permission_mode=permission_mode,
        strict_mcp_config=True,                   # isolation triad
        setting_sources=[],                       # isolation triad (load no fs settings)
        max_turns=query.max_turns,
        thinking=_thinking_config(query),         # type: ignore[arg-type]
        extra_args=extra_args,
    )
    if query.system_prompt:
        options.system_prompt = query.system_prompt
    if query.resume:
        options.resume = query.resume
    # Mount the turn's CURATED MCP servers so the ``mcp__<server>__*`` tool names in
    # ``allowed_tools`` are actually REACHABLE. ``strict_mcp_config=True`` keeps the triad
    # intact: ONLY these explicitly-passed servers are mounted — a discovered ``.mcp.json``
    # / user connector is still ignored. An empty/None mapping leaves the SDK default.
    if query.mcp_servers:
        options.mcp_servers = query.mcp_servers
        # CRITICAL: ``tools`` is the SDK's BASE tool set → the CLI serializes it as
        # ``--tools <csv>``, and an EMPTY list emits ``--tools ""`` which zeroes out the
        # ENTIRE base set, SUPPRESSING the mounted MCP tools too — the model would see no
        # tools and call none (a silent no-op). When servers ARE mounted we therefore DROP
        # an empty ``tools`` (leave it the SDK default / ``--tools`` omitted) so the MCP
        # tools load; isolation still holds via the curated ``allowed_tools`` permission
        # gate + ``disallowed_tools`` (host built-ins blocked) + ``strict_mcp_config`` +
        # ``setting_sources=[]``. A genuinely NON-empty computed built-in list is still
        # pinned as the restrictive base set.
        if not query.tools:
            options.tools = None
    # The curated per-turn env (e.g. the output-token clamp) rides the options the SDK
    # subprocess enforces.
    if query.env:
        options.env = dict(query.env)
    # The per-query ``disallowed_tools`` block-list (a read-only turn's write block)
    # MERGES into the module-level block-list (dedup, order-preserving): it must ride the
    # options because ``allowed_tools`` does not filter MCP tools.
    if query.disallowed_tools:
        options.disallowed_tools = list(
            dict.fromkeys([*options.disallowed_tools, *query.disallowed_tools])
        )
    return options
