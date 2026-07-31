"""The CONCRETE Claude ``AgentProvider`` — the SINGLE place the Claude Agent SDK
is called for a seam-routed agent turn (04 §3.3, CANONICAL §1.1 / §11.10 / §12.3,
D-022 / §10.6).

This module realizes §3.3: a *dumb* Claude provider that translates native
``claude_agent_sdk`` messages into the canonical ``contracts.AgentChunk`` union
(six variants; discriminator ``.type``; ``TEXT.text`` is the **accumulated** text
for its ``msg_id``, NOT a delta) and **re-throws nothing in-band** — a transport
fault becomes a terminal ``ERROR`` chunk on the stream, surfaced as
``ProviderError`` at the ``BehaviorRunner`` boundary (§3.5). It satisfies the
``agentkit.Provider`` Protocol (``stream(prompt, query) -> AsyncIterator[AgentChunk]``)
and registers itself as the default via ``pick_provider`` (§3.3: unknown model → Claude).

The seam owns every cross-cutting concern named in §3.3:

  * **The SDK-isolation triad on EVERY call** — ``strict_mcp_config=True`` (ignore all
    discovered ``.mcp.json`` / user settings / claude.ai connectors), ``setting_sources=[]``
    (load no filesystem permissions/hooks/CLAUDE.md — both are required; neither
    suppresses connectors alone), a computed built-in ``tools`` list (``[]`` in sandbox
    mode: no host-side ``Read``/``Grep``/``Bash``), and the ``SDK_LOCAL_TOOLS`` block-list
    pinned into ``disallowed_tools``. ``check_sdk_isolation_triad`` (libs/ops) asserts
    these markers are present at this ``query()`` call site.
  * **Extended/adaptive thinking per D-022 / §10.6** — ON only for a real code-reasoning
    turn (Opus-escalated grounded answer, Workroom build-planning), OFF on every fast
    path (should-I-speak gate, quick lookup, Scribe micro-call) where a thinking preamble
    is latency-toxic. When on, the budget is capped well below the output-token ceiling
    so a large structured emission can't truncate mid-object (platform N3).
  * **The [CRITICAL] tripwire** — a non-MCP built-in host tool (``Read``/``Grep``/``Bash``/
    ``Glob``/``Write``/``Edit``) firing while the run is in sandbox mode means the
    isolation triad leaked and a tool is executing on the *orchestrator host*, not in E2B
    → log ``[CRITICAL]``.
  * **Env sanitization** — hand the SDK subprocess a *curated* env with the mutually-
    exclusive auth keys stripped to at most one (a leaked dev ``.env`` otherwise makes the
    SDK pick the wrong auth path).
  * **stderr redaction** — route SDK stderr through a ``sk-ant-*`` / ``Bearer`` / ``token=…``
    redactor before logging.

**The SDK-message → AgentChunk mapping is CONFIRMED against the installed
``claude_agent_sdk`` (0.2.128) live shapes (D-010 / §11.10), never guessed.** The
mapping (from the real dataclasses):

  * ``SystemMessage(subtype="init", data={session_id, tools, mcp_servers, model})`` → ``INIT``
  * ``AssistantMessage(content=[TextBlock|ToolUseBlock|ToolResultBlock|ThinkingBlock|...],
    message_id, session_id, error)`` → per-block ``TEXT`` (accumulated) / ``TOOL_USE`` /
    ``TOOL_RESULT``; a non-None ``.error`` → ``ERROR``
  * ``UserMessage(content=str | [ToolResultBlock|...])`` → ``TOOL_RESULT`` per result block
  * ``ResultMessage(subtype, total_cost_usd, session_id, num_turns, structured_output,
    is_error, result)`` → ``RESULT`` (or ``ERROR`` when ``is_error``)
"""
from __future__ import annotations

import contextlib
import logging
import re
from collections.abc import AsyncIterator, Iterable, Iterator
from typing import Any, Callable

# Import agentkit by its installed top-level name (``agentkit``, not ``libs.agentkit``):
# under the src-layout facade the ``libs.agentkit.provider`` path loads a SECOND module
# copy with its own registry globals, so pick_provider/register_provider imported that way
# would not share a registry — the top-level ``agentkit.provider`` is the single canonical
# copy for both. It is also the name the mypy ``agentkit.*`` override recognizes.
#
# The isolation-triad constants are plain values (a tuple + a str); importing them here
# plants the SDK_LOCAL_TOOLS / disallowed_tools / permission_mode markers this module must
# carry for check_sdk_isolation_triad at the query() call site (§11.11).
from agentkit import (
    Provider,
    ProviderQuery,
    register_provider,
)
from agentkit import (
    pick_provider as pick_provider,
)
from agentkit.provider import (
    SDK_LOCAL_TOOLS as SDK_LOCAL_TOOLS,
)
from agentkit.provider import (
    disallowed_tools as disallowed_tools,
)
from agentkit.provider import (
    permission_mode as permission_mode,
)
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

_LOG = logging.getLogger("services.control_plane.provider")

# The non-MCP built-in host tools the [CRITICAL] tripwire watches for. Same block-list
# the isolation triad names — these run on the orchestrator host, never in E2B, so a
# firing in sandbox mode means the triad leaked. Matched case-insensitively.
_HOST_BUILTINS: frozenset[str] = frozenset(t.lower() for t in SDK_LOCAL_TOOLS)


# ---------------------------------------------------------------------------
# SDK-message → AgentChunk mapping (confirmed against claude_agent_sdk 0.2.128)
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


def map_sdk_messages(messages: Iterable[Any]) -> Iterator[AgentChunk]:
    """Map a SYNC iterable of SDK messages (a recorded cassette) → AgentChunk stream."""
    for msg in messages:
        yield from map_sdk_message(msg)


# ---------------------------------------------------------------------------
# The [CRITICAL] tripwire — a non-MCP host built-in firing in sandbox mode
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
# Env sanitization + stderr redaction (seam cross-cutting concerns)
# ---------------------------------------------------------------------------

# Auth keys the SDK inspects; mutually exclusive — a subprocess handed more than one
# picks the wrong auth path. Ordered by precedence (keep the first present, drop the rest).
_AUTH_KEY_PRECEDENCE: tuple[str, ...] = (
    "CLAUDE_CODE_USE_VERTEX",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
)


def sanitize_env(env: dict[str, str]) -> dict[str, str]:
    """Return a curated copy of ``env`` with the auth keys reduced to at most one.

    A leaked dev ``.env`` that carries both an API key and an OAuth token would make the
    SDK pick the wrong auth path; the seam keeps the highest-precedence key present and
    strips the rest. All non-auth env survives untouched.
    """
    curated = dict(env)
    kept = False
    for key in _AUTH_KEY_PRECEDENCE:
        if key in curated:
            if kept:
                del curated[key]
            else:
                kept = True
    return curated


# sk-ant-* API keys, Bearer tokens, and token=… assignments — never surface these in a
# log line. Ordered longest-context-first so the value is masked, not just the prefix.
_REDACT_MARKER = "[REDACTED]"
_SK_ANT_RX = re.compile(r"sk-ant-[A-Za-z0-9_\-]+")
_BEARER_RX = re.compile(r"Bearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE)
_TOKEN_ASSIGN_RX = re.compile(r"(token\s*[=:]\s*)([A-Za-z0-9._\-]+)", re.IGNORECASE)


def redact_stderr_line(line: str) -> str:
    """Redact ``sk-ant-*`` keys, ``Bearer <tok>``, and ``token=<tok>`` from a log line.

    The SDK subprocess's stderr is routed through here before it ever reaches a log
    handler, so a credential printed by the CLI never lands in a log.
    """
    line = _SK_ANT_RX.sub(_REDACT_MARKER, line)
    line = _BEARER_RX.sub(f"Bearer {_REDACT_MARKER}", line)
    line = _TOKEN_ASSIGN_RX.sub(rf"\1{_REDACT_MARKER}", line)
    return line


# ---------------------------------------------------------------------------
# ClaudeAgentOptions — the SDK-isolation triad + ThinkingConfig per D-022
# ---------------------------------------------------------------------------

def _thinking_config(query: ProviderQuery) -> dict[str, Any] | None:
    """The ThinkingConfig for this turn (D-022 / §10.6).

    OFF on every fast path — ``None`` (no thinking preamble; latency-toxic on the
    should-I-speak gate / quick lookup / Scribe). ON only for a real code-reasoning turn,
    with the budget capped below the output ceiling so a large structured emission can't
    truncate mid-object (platform N3). Adaptive is the reasoning-model on-mode; when a
    non-adaptive family carries an explicit budget the ``enabled`` config is used.
    """
    if not query.thinking_enabled:
        return None
    # Opus-tier reasoning families take adaptive thinking (budget-less on-mode);
    # older/explicit-budget turns take the enabled config with the capped budget.
    model = query.model
    if model.startswith("claude-opus") or model.startswith("claude-fable"):
        return {"type": "adaptive"}
    budget = query.thinking_budget_tokens or 3000
    return {"type": "enabled", "budget_tokens": budget}


def build_sdk_options(prompt: str, query: ProviderQuery) -> ClaudeAgentOptions:
    """Build the ``ClaudeAgentOptions`` for one seam call, pinning the isolation triad.

    Every call sets the triad by construction: ``strict_mcp_config=True`` +
    ``setting_sources=[]`` (both needed to suppress discovered ``.mcp.json`` / user
    settings / claude.ai connectors), a computed built-in ``tools`` list (``[]`` — no
    host-side built-ins), the ``SDK_LOCAL_TOOLS`` block-list in ``disallowed_tools``, and
    the behavior's curated ``allowed_tools`` subset (§10.5, never the whole-Proxy union).
    ThinkingConfig follows D-022. The isolation-triad markers (``SDK_LOCAL_TOOLS`` /
    ``disallowed_tools`` / ``permission_mode``) present in this module satisfy
    ``check_sdk_isolation_triad``.
    """
    _ = prompt  # the prompt is passed to query(); options carry only the envelope
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
    # Mount the behavior's CURATED MCP servers (the sandbox ``code`` HTTP server, the host-side
    # ``propose_change`` in-process server, a code-intel server, …) so the ``mcp__<server>__*``
    # tool names the behavior advertises in ``allowed_tools`` are actually REACHABLE by the
    # model. Without this the model advertises tools whose providing server is never mounted and
    # can never call them (the seam gap this fixes). ``strict_mcp_config=True`` keeps the triad
    # intact: ONLY these explicitly-passed servers are mounted — a discovered ``.mcp.json`` /
    # user connector is still ignored. An empty/None mapping leaves the SDK default (no servers).
    if query.mcp_servers:
        options.mcp_servers = query.mcp_servers
        # CRITICAL: ``tools`` is the SDK's BASE tool set → the CLI serializes it as ``--tools
        # <csv>``, and an EMPTY list emits ``--tools ""`` which zeroes out the ENTIRE base set,
        # SUPPRESSING the mounted MCP tools too — so the model sees no tools and calls none (the
        # silent no-op that left auth.py unchanged in the real gate). When servers ARE mounted we
        # therefore DROP an empty ``tools`` (leave it the SDK default / ``--tools`` omitted) so
        # the MCP tools load; isolation still holds via the curated ``allowed_tools`` permission
        # gate (only ``mcp__code__*`` permitted) + ``disallowed_tools`` (host built-ins blocked) +
        # ``strict_mcp_config`` + ``setting_sources=[]``. A genuinely NON-empty computed built-in
        # list is still pinned as the restrictive base set (it names real built-ins to allow).
        if not query.tools:
            options.tools = None
    # The curated per-turn env (the output-token clamp, §3.2/§3.9) rides the OPTIONS the SDK
    # enforces so it actually caps this model's output.
    if query.env:
        options.env = dict(query.env)
    # The per-query ``disallowed_tools`` block-list (the host-built-in backstop + a read-only
    # disposition's mutating-tool block) MERGES into the module-level block-list on the options:
    # it must ride the options because ``allowed_tools`` does not filter MCP tools (§3.8), so a
    # read-only disposition's write block goes through ``disallowed_tools``.
    if query.disallowed_tools:
        options.disallowed_tools = list(
            dict.fromkeys([*options.disallowed_tools, *query.disallowed_tools])
        )
    return options


# ---------------------------------------------------------------------------
# The concrete Claude AgentProvider (satisfies the agentkit.Provider Protocol)
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


class ClaudeAgentProvider:
    """A *dumb* Claude provider: translates native SDK events → ``AgentChunk`` and
    re-throws nothing in-band. A transport fault becomes a terminal ``ERROR`` chunk
    (surfaced as ``ProviderError`` at the ``BehaviorRunner`` boundary, §3.5).

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
        options = build_sdk_options(prompt, query)
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


def register_claude_provider() -> ClaudeAgentProvider:
    """Register the Claude provider as the seam default (§3.3: unknown model → Claude)."""
    provider = ClaudeAgentProvider()
    register_provider(
        provider,
        models=("claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-8"),
        default=True,
    )
    return provider


# Assert the Protocol is satisfied at import (structural check, cheap).
_provider_protocol_check: Provider = ClaudeAgentProvider()
