"""The provider seam (§3.3) — the ONE place an agent call becomes a normalized,
provider-neutral ``AgentChunk`` stream.

The SDK is never called from harness business logic; every seam-routed agent
call (Proxy's wake turn, the Workroom build in Doc 05) goes through a
:class:`Provider` that translates native SDK events → :class:`AgentChunk` and
re-throws nothing in-band. Providers stay *dumb*; the cross-cutting concerns
(delta computation, cost metering, abort, the SDK-isolation triad, stale-session
recovery) live above the seam in :class:`~agentkit.execution.BehaviorRunner`.

This module owns the seam's *shape*, not a concrete vendor client:

  * :class:`ProviderQuery` — the immutable per-call options the runner computes,
    carrying the SDK-isolation triad (``strict_mcp_config=True``,
    ``setting_sources=[]``, a computed built-in ``tools`` list);
  * :class:`ProviderError` — the boundary exception a pass-through ``ERROR`` chunk
    is surfaced as at the runner boundary, where §3.5 recovery catches it;
  * :class:`Provider` — the ``Protocol`` a concrete Claude-SDK provider satisfies
    (``stream(prompt, query) -> AsyncIterator[AgentChunk]``).

The concrete SDK-message → ``AgentChunk`` mapping is a confirm-at-build item
(D-010 / CANONICAL §11.10): the live ``claude_agent_sdk`` message shapes are
pinned inside the concrete provider impl (:mod:`~agentkit.sdk_provider`), never
guessed here.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from libs.contracts import AgentChunk

# The SDK-isolation triad markers the ``check-sdk-isolation-triad`` guard requires
# a query()-hosting module to carry (CANONICAL §11.11). This module is the seam
# these params flow through, so it names them structurally: no built-in host tool
# executes outside the sandbox, and no discovered .mcp.json / connector leaks in.
SDK_LOCAL_TOOLS: tuple[str, ...] = ("Read", "Grep", "Glob", "Bash", "Write", "Edit")
# The isolation permission mode the seam pins for every SDK call. A seam-routed call is a
# HEADLESS SERVER AGENT — there is no human at a terminal to answer a tool-permission prompt,
# so ``permission_mode="default"`` would leave every tool call waiting on a prompt that a
# non-interactive subprocess auto-DENIES, and the model silently gives up having called nothing
# (the exact silent no-op that left the real file unedited). ``bypassPermissions`` is therefore
# the correct — and the only workable — mode for these headless agents. Isolation is NOT
# weakened by it: the real gate is the curated built-in ``tools`` list (``[]`` in sandbox mode)
# + ``strict_mcp_config`` + ``setting_sources=[]`` + ``disallowed_tools`` (the host-built-in
# block-list), never an interactive permission prompt. This mirrors the Workroom warm session's
# (``in_meeting.session_host``) ``permission_mode="bypassPermissions"`` so the two paths never disagree.
permission_mode: str = "bypassPermissions"
# World-touching built-ins that must never be advertised to a seam-routed call —
# they run on the orchestrator host, not in E2B. Kept OUT of every computed list.
disallowed_tools: tuple[str, ...] = SDK_LOCAL_TOOLS


class ProviderError(Exception):
    """The seam boundary exception. A pass-through ``ERROR`` ``AgentChunk`` is
    surfaced as this at the :class:`~agentkit.execution.BehaviorRunner` boundary,
    where the §3.5 stale-session / retry recovery layer catches it.

    It carries the originating ``chunk`` so recovery can read
    ``chunk.metadata["message"]`` to classify a stale-session error.
    """

    def __init__(self, chunk: AgentChunk) -> None:
        self.chunk = chunk
        message = ""
        if chunk is not None and getattr(chunk, "metadata", None):
            message = str(chunk.metadata.get("message", ""))
        super().__init__(message or "provider error")


@dataclass(frozen=True)
class ProviderQuery:
    """The immutable per-call options the runner computes and hands to the seam.

    Every SDK call sets the isolation triad by construction (the defaults are the
    safe ones): ``strict_mcp_config=True`` ignores all discovered ``.mcp.json`` /
    user settings / claude.ai connectors; ``setting_sources=[]`` loads no
    filesystem permissions/hooks/CLAUDE.md (both are needed — neither suppresses
    connectors alone); and ``tools`` is a *computed* built-in list (``[]`` in
    sandbox mode). ``allowed_tools`` is the behavior's curated subset (§10.5) —
    never the union.
    """

    model: str
    allowed_tools: tuple[str, ...]
    system_prompt: str = ""
    max_turns: int = 1
    tools: tuple[str, ...] = ()                       # computed built-ins ([] in sandbox mode)
    strict_mcp_config: bool = True                    # isolation triad
    setting_sources: tuple[str, ...] = ()             # isolation triad ([] — load no fs settings)
    thinking_enabled: bool = False
    thinking_budget_tokens: int = 0
    resume: str | None = None
    preamble: str | None = None
    abort: Any = None
    # The CURATED in-process/HTTP MCP servers whose tools this behavior's ``allowed_tools``
    # reference — a mapping of server-name → SDK MCP server config (e.g. the sandbox ``code``
    # HTTP server, the host-side ``propose_change`` in-process server, a code-intel server).
    # ``build_sdk_options`` mounts EXACTLY these onto ``ClaudeAgentOptions.mcp_servers``; with
    # ``strict_mcp_config=True`` ONLY these explicitly-passed servers are mounted (a discovered
    # ``.mcp.json`` is still ignored — the isolation triad holds). ``None``/empty = mount no
    # servers (backward-compatible: a query that advertises only host built-ins needs none).
    mcp_servers: Any = None
    # The curated per-turn env the SDK subprocess reads (e.g. the ``MAX_OUTPUT_TOKENS`` output
    # clamp, §3.2/§3.9). Threaded onto ``ClaudeAgentOptions.env`` by ``build_sdk_options``;
    # empty = the SDK default env.
    env: dict[str, str] = field(default_factory=dict)
    # The per-query disallowed-tool block-list — the ``SDK_LOCAL_TOOLS`` host-built-in backstop
    # PLUS, for a read-only disposition, the mutating tools it must not reach (``allowed_tools``
    # does not filter MCP tools, so a write block MUST go through ``disallowed_tools``, §3.8).
    # ``build_sdk_options`` MERGES this into the module-level ``disallowed_tools`` on the options
    # the SDK enforces. Empty = the module-level host-built-in block-list alone (the seam default).
    disallowed_tools: tuple[str, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Provider(Protocol):
    """A provider translates a rendered prompt + :class:`ProviderQuery` into a
    normalized async ``AgentChunk`` stream. It never raises in-band: a fault is a
    terminal ``ERROR`` chunk on the stream, surfaced as :class:`ProviderError` at
    the runner boundary."""

    def stream(self, prompt: str, query: ProviderQuery) -> AsyncIterator[AgentChunk]: ...
