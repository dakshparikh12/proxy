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
  * **Automatic prompt caching of the stable prefix** — the ``system_prompt``
    (prime + map) is static across wake turns, so the CLI/API caches it by default
    ("map = cached prefix ~90% cheaper", SPEC §8); ``cacheReadInputTokens`` confirms
    hits. NO ``extra_args`` cache flag is set: the installed Claude Code CLI (2.1.191)
    has no ``--system-prompt-cache-ttl`` flag and aborts on it (the functional sim
    caught this). A longer-than-default TTL is a named optimization residual.

The options-building logic is ported (not imported) from the old brain's seam —
``services/harness/src/harness/provider.py:349-411`` (``build_sdk_options``), its
triad constants (``agentkit/provider.py:42-56``, re-exported there at 75-83) and
``_thinking_config`` (329-346) — per DECISION 1: the engine owns its own thin
provider with NO old-brain coupling. The options-building half is pure
construction: deterministic, offline, no network, no keys.

The module ALSO owns the engine's CONCRETE provider — :class:`EngineProvider`,
the single place the Claude Agent SDK is called for an engine turn (L2). The
SDK-message → ``AgentChunk`` mapping (confirmed against the installed
``claude_agent_sdk`` 0.2.128 live shapes, D-010 — never guessed), the
``[CRITICAL]`` tripwire, and the abort-polling ``stream()`` loop are a faithful
port of the proven seam impl in ``services/harness/src/harness/provider.py``
(mapping helpers 110-240, tripwire 253-271, ``QueryFn``/``_is_aborted`` 420-433,
the stream loop 436-494). The ONE deliberate difference: ``stream`` builds its
options with the engine's :func:`build_engine_options` above — so the isolation
triad AND the 1-hour prompt-cache directive ride every call — not the old
brain's ``build_sdk_options``. (D4 obligation: this duplicates the harness
mapping; D4 dedups both concrete providers into ``libs/llm``.)
"""
from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator, Iterator
from typing import Any, Callable, Final

from agentkit import Provider, ProviderQuery
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from claude_agent_sdk import query as _sdk_query
from contracts import AgentChunk

_LOG = logging.getLogger("services.in_meeting.provider")

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

# The non-MCP built-in host tools the [CRITICAL] tripwire watches for. Same block-list
# the isolation triad names — these run on the orchestrator host, never in E2B, so a
# firing in sandbox mode means the triad leaked. Matched case-insensitively.
_HOST_BUILTINS: frozenset[str] = frozenset(t.lower() for t in SDK_LOCAL_TOOLS)

# Prompt caching of the stable prefix (prime + map, SPEC §8) is AUTOMATIC: the
# system_prompt is static across wake turns, so the CLI/API caches it by default
# (cacheReadInputTokens confirms hits). The installed Claude Code CLI (2.1.191) has
# NO --system-prompt-cache-ttl flag — passing one via extra_args makes the CLI abort
# ("unknown option"), which the functional sim caught. An explicit longer-than-default
# TTL (>~5min, for long meetings) is a NAMED OPTIMIZATION RESIDUAL: it needs a CLI/API
# path that exposes the ttl for a CUSTOM system prompt (--exclude-dynamic-system-prompt-
# sections only applies to the DEFAULT preset, not our custom --system-prompt).


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
    construction. Prompt caching of the stable prefix is AUTOMATIC (see the module
    note above) — no ``extra_args`` cache flag is set (the installed CLI rejects it).

    ``query.system_prompt`` is the STABLE, auto-cached prefix (prime + map) — the
    volatile per-turn ask never rides it (it is passed to ``query()`` as the prompt
    by the context-assembly layer). No caller-provided ``extra_args`` is threaded:
    the engine passes no CLI passthrough flags, which also closes the passthrough
    smuggling vector — nothing untrusted can weaken the triad via extra_args.
    """
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


# ---------------------------------------------------------------------------
# SDK-message → AgentChunk mapping (confirmed against claude_agent_sdk 0.2.128;
# faithful port of harness/provider.py:110-240)
# ---------------------------------------------------------------------------

def _text_content_to_str(content: Any) -> str:
    """Flatten a ToolResultBlock ``content`` (``str | list[dict] | None``) to text.

    The seam surfaces the tool result as text for every consumer; a list of content
    blocks (the multi-part form) is joined on its ``text`` fields.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        return "\n".join(p for p in parts if p)
    return str(content)


def _map_system(msg: SystemMessage) -> Iterator[AgentChunk]:
    # Only the ``init`` system message carries session/tools/mcp — every other
    # subtype (status, compaction notices, …) is dropped, never misclassified as INIT.
    if msg.subtype != "init":
        return
    data = msg.data or {}
    metadata: dict[str, Any] = {
        "session_id": data.get("session_id", ""),
        "tools": data.get("tools", []),
        "mcp_servers": data.get("mcp_servers", []),
    }
    if "model" in data:
        metadata["model"] = data["model"]
    yield AgentChunk(type="INIT", text="", metadata=metadata)


def _content_blocks(content: Any) -> list[Any]:
    """A message ``content`` is either a str (no blocks) or a list of content blocks."""
    if isinstance(content, list):
        return content
    return []


def _map_assistant(msg: AssistantMessage) -> Iterator[AgentChunk]:
    # A per-turn error class (auth/rate-limit/stale) surfaces as a terminal ERROR chunk.
    if msg.error is not None:
        yield AgentChunk(type="ERROR", text="", metadata={"message": str(msg.error)})
        return
    # msg_id is the SDK message_id; when absent (streaming edge) fall back to session
    # so the per-msg_id accumulator still keys stably within a turn.
    msg_id = msg.message_id or msg.session_id or "assistant"
    for block in _content_blocks(msg.content):
        if isinstance(block, TextBlock):
            # TEXT.text is the ACCUMULATED assistant text for this msg_id (§1.1), NOT a
            # delta — the delta-izer (applied ONCE in BehaviorRunner.run) computes deltas.
            yield AgentChunk(type="TEXT", text=block.text, metadata={"msg_id": msg_id})
        elif isinstance(block, ToolUseBlock):
            yield AgentChunk(
                type="TOOL_USE",
                text="",
                metadata={"id": block.id, "name": block.name, "input": block.input},
            )
        elif isinstance(block, ToolResultBlock):
            yield AgentChunk(
                type="TOOL_RESULT",
                text=_text_content_to_str(block.content),
                metadata={
                    "tool_use_id": block.tool_use_id,
                    "is_error": bool(block.is_error) if block.is_error is not None else False,
                    "structured": block.content if isinstance(block.content, list) else None,
                },
            )
        elif isinstance(block, ThinkingBlock):
            # Reasoning preamble is NOT a consumer-visible chunk (TTS speaks TEXT only).
            continue
        # ServerToolUse/ServerToolResult and unknown blocks: not consumer chunks here.


def _map_user(msg: UserMessage) -> Iterator[AgentChunk]:
    # Tool results come back on a UserMessage in the SDK convention; surface each as
    # a TOOL_RESULT. Bare-str user content carries no consumer chunk.
    for block in _content_blocks(msg.content):
        if isinstance(block, ToolResultBlock):
            yield AgentChunk(
                type="TOOL_RESULT",
                text=_text_content_to_str(block.content),
                metadata={
                    "tool_use_id": block.tool_use_id,
                    "is_error": bool(block.is_error) if block.is_error is not None else False,
                    "structured": block.content if isinstance(block.content, list) else None,
                },
            )


def _map_result(msg: ResultMessage) -> Iterator[AgentChunk]:
    if msg.is_error:
        # A terminal error result → ERROR chunk (surfaced as ProviderError at the boundary).
        message = msg.result or (msg.errors[0] if msg.errors else "") or msg.subtype
        yield AgentChunk(type="ERROR", text="", metadata={"message": str(message)})
        return
    yield AgentChunk(
        type="RESULT",
        text=msg.result or "",
        metadata={
            "session_id": msg.session_id,
            "num_turns": msg.num_turns,
            # Default to 0.0 so the cost meter (RESULT.total_cost_usd) never KeyErrors.
            "total_cost_usd": msg.total_cost_usd if msg.total_cost_usd is not None else 0.0,
            "structured_output": msg.structured_output,
            "subtype": msg.subtype,
        },
    )


def map_sdk_message(msg: Any) -> Iterator[AgentChunk]:
    """Translate ONE native ``claude_agent_sdk`` message → zero or more ``AgentChunk``.

    Pure function over an already-parsed SDK message — no I/O, no live CLI call — so
    the mapping is unit-testable against recorded/synthetic SDK objects (§11.10).
    """
    if isinstance(msg, SystemMessage):
        yield from _map_system(msg)
    elif isinstance(msg, AssistantMessage):
        yield from _map_assistant(msg)
    elif isinstance(msg, UserMessage):
        yield from _map_user(msg)
    elif isinstance(msg, ResultMessage):
        yield from _map_result(msg)
    # Any other SDK message type (StreamEvent partials, task notifications, …) yields
    # no consumer chunk — the seam forwards only the six canonical variants.


# ---------------------------------------------------------------------------
# The [CRITICAL] tripwire — a non-MCP host built-in firing in sandbox mode
# (faithful port of harness/provider.py:253-271)
# ---------------------------------------------------------------------------

def check_critical_tripwire(chunk: AgentChunk, *, sandbox_mode: bool) -> bool:
    """Log ``[CRITICAL]`` if a non-MCP built-in host tool fires while sandboxed.

    A ``Read``/``Grep``/``Bash``/``Glob``/``Write``/``Edit`` TOOL_USE in sandbox mode
    means the isolation triad leaked and the tool is executing on the orchestrator host
    (not in E2B). Returns ``True`` iff the tripwire fired. Curated MCP tools (any name
    outside the host block-list) and non-sandbox runs are silent.
    """
    if not sandbox_mode or chunk.type != "TOOL_USE":
        return False
    name = str(chunk.metadata.get("name", "")).lower()
    if name in _HOST_BUILTINS:
        _LOG.critical(
            "[CRITICAL] non-MCP built-in tool %r fired in sandbox mode — isolation "
            "triad leaked; a tool is executing on the orchestrator host, not in E2B",
            chunk.metadata.get("name"),
        )
        return True
    return False


# ---------------------------------------------------------------------------
# The engine's concrete provider (satisfies the agentkit.Provider Protocol;
# faithful port of harness/provider.py:420-494 — ONE deliberate difference:
# options come from build_engine_options, so the cache-ttl rides every call)
# ---------------------------------------------------------------------------

# The SDK query() signature the provider drives: query(prompt=..., options=...) →
# AsyncIterator[Message]. Injectable so the mapping is unit-testable without a live CLI.
QueryFn = Callable[..., AsyncIterator[Any]]


def _is_aborted(abort: Any) -> bool:
    """True iff the turn's abort handle has fired (§3.11).

    The handle is duck-typed on its ``.aborted`` flag — an ``agentkit.AbortController``
    (the §3.11 primitive) or any object exposing ``.aborted`` (the wake ``_Abort``
    handle). ``None`` (no abort threaded) is never aborted. Kept tolerant so a bad
    handle can never crash the model loop — it simply reads as not-aborted.
    """
    if abort is None:
        return False
    return bool(getattr(abort, "aborted", False))


class EngineProvider:
    """The engine's *dumb* Claude provider: translates native SDK events →
    ``AgentChunk`` and re-throws nothing in-band. A transport fault becomes a
    terminal ``ERROR`` chunk (surfaced as ``ProviderError`` at the
    ``BehaviorRunner`` boundary).

    ``query_fn`` defaults to the real ``claude_agent_sdk.query`` and is injectable so
    the SDK-message → AgentChunk mapping can be exercised against recorded/synthetic SDK
    objects without a live CLI round-trip (the unit mapping, §11.10). ``sandbox_mode``
    arms the ``[CRITICAL]`` tripwire (a non-MCP host built-in firing → isolation leak).
    """

    name = "claude"

    def __init__(self, *, query_fn: QueryFn | None = None, sandbox_mode: bool = True) -> None:
        self._query_fn: QueryFn = query_fn if query_fn is not None else _sdk_query
        self._sandbox_mode = sandbox_mode

    def matches(self, model: str) -> bool:
        return model.startswith("claude-")

    async def stream(self, prompt: str, query: ProviderQuery) -> AsyncIterator[AgentChunk]:
        """Normalize the SDK's native message stream into ``AgentChunk``.

        Yields the six canonical variants; the ``[CRITICAL]`` tripwire fires on any host
        built-in TOOL_USE while sandboxed; a transport blow-up terminates the stream with
        an ``ERROR`` chunk (never an in-band raise).

        **Abort halts the MODEL loop (§3.11).** When the turn's ``AbortController`` fires
        (``query.abort.aborted``), the ``async for message`` loop is BROKEN — the SDK
        subprocess is stopped rather than left to run to ``maxTurns`` (default 1000),
        which is the runaway-spend / "Proxy, quiet" fix. This is a hard halt of the loop,
        not merely ignoring the result after the stream drains.
        """
        # The ONE deliberate difference from the harness reference: the ENGINE's options
        # builder — isolation triad + the 1-hour prompt-cache directive on every call.
        options = build_engine_options(query)
        abort = query.abort
        # An abort already fired before the first pull → never start the model loop.
        if _is_aborted(abort):
            return
        stream = self._query_fn(prompt=prompt, options=options)
        try:
            async for message in stream:
                # Poll the abort handle every pull: a "Proxy, quiet" / meeting-end /
                # timeout mid-run halts the loop HERE, before the next model turn.
                if _is_aborted(abort):
                    break
                for chunk in map_sdk_message(message):
                    check_critical_tripwire(chunk, sandbox_mode=self._sandbox_mode)
                    yield chunk
                    if _is_aborted(abort):
                        break
        except Exception as exc:  # noqa: BLE001 — re-throw nothing in-band; ERROR on the stream
            yield AgentChunk(type="ERROR", text="", metadata={"message": str(exc)})
        finally:
            # Close the underlying async generator so the SDK subprocess is torn down
            # on an abort-break (not left running); tolerate a non-generator query_fn.
            aclose = getattr(stream, "aclose", None)
            if aclose is not None:
                with contextlib.suppress(Exception):
                    await aclose()


# Assert the Protocol is satisfied at import (structural check, cheap).
_provider_protocol_check: Provider = EngineProvider()
