"""Acceptance battery for Task L3 — the engine's SDK-options builder + prompt cache.

``in_meeting.provider.build_engine_options`` is the engine's OWN options builder
(no old-brain coupling): it turns an ``agentkit.ProviderQuery`` into a REAL
``claude_agent_sdk.ClaudeAgentOptions`` carrying (a) the SDK-isolation triad by
construction and (b) the 1-hour prompt-cache directive via
``extra_args["system-prompt-cache-ttl"]``, so the stable system-prompt prefix
(prime + map) is cached across wake turns (SPEC §8 — "map = cached prefix").

Deterministic and offline: this only constructs an options object — no network,
no keys, no live CLI.
"""
from __future__ import annotations

from typing import Any

from agentkit import ProviderQuery
from claude_agent_sdk import ClaudeAgentOptions

from contracts import AgentChunk

from in_meeting.provider import (
    SDK_LOCAL_TOOLS,
    build_engine_options,
    check_critical_tripwire,
    disallowed_tools,
    permission_mode,
)

#: The STABLE, cacheable prefix — the Proxy prime + the pre-meeting ``index.md``
#: map. This is what rides ``system_prompt`` and gets the 1-hour cache TTL.
_PRIME_AND_MAP = (
    "You are Proxy, joining this meeting already knowing the codebase.\n\n"
    "# Repo map (index.md)\n"
    "- services/billing/worker.py — the retry loop (backoff in _retry_call)\n"
    "- services/billing/ledger.py — double-entry postings\n"
)

#: A per-turn ASK — the VOLATILE tail. It is passed to ``query()`` later by the
#: context-assembly task (L1); it must NEVER ride the cached system prompt.
_ASK = "Proxy, what's the retry logic in billing-worker?"

_HOST_BUILTINS = ("Read", "Grep", "Glob", "Bash", "Write", "Edit")

#: The dangerous host built-ins BEYOND the local six — everything the installed
#: bundled CLI advertises in an engine-shaped turn that executes, spawns,
#: schedules, or fetches on the ENGINE HOST (introspected from the real init
#: message + the CLI binary; ``Agent`` is the dispatch alias of the Task family
#: that the plan-quality trace caught fabricating a "run"). All of these must be
#: blocked on every engine call — only the curated ``mcp__*`` tools may act.
_DANGEROUS_HOST_BUILTINS = (
    "Agent", "Task", "TaskCreate", "TaskGet", "TaskList", "TaskOutput",
    "TaskStop", "TaskUpdate",
    "WebSearch", "WebFetch",
    "NotebookEdit", "Skill", "SlashCommand",
    "BashOutput", "KillShell", "KillBash",
    "EnterWorktree", "ExitWorktree",
    "Monitor", "SendMessage", "RemoteTrigger", "PushNotification",
    "CronCreate", "CronDelete", "CronList", "ScheduleWakeup", "Workflow",
    "DesignSync", "ReportFindings",
)


def _query(**overrides: Any) -> ProviderQuery:
    """A representative engine ProviderQuery (opus model, curated MCP subset)."""
    base: dict[str, Any] = {
        "model": "claude-opus-4-6",
        "allowed_tools": ("mcp__code__search", "mcp__code__read"),
        "system_prompt": _PRIME_AND_MAP,
    }
    base.update(overrides)
    return ProviderQuery(**base)


# ---------------------------------------------------------------------------
# AC 1 — caching is automatic; NO unsupported extra_args flag (the sim-caught CLI bug)
# ---------------------------------------------------------------------------

def test_no_unsupported_extra_args_flags() -> None:
    """No CLI passthrough flags are set. Prompt caching of the static system-prompt
    prefix is automatic; the installed Claude Code CLI (2.1.191) has no
    ``--system-prompt-cache-ttl`` flag — passing one aborts the real turn ("unknown
    option"), which the functional sim caught. ``extra_args`` stays empty."""
    options = build_engine_options(_query())
    assert options.extra_args == {}


def test_caller_extra_args_are_not_threaded_through() -> None:
    """A caller-provided ``extra_args`` sub-dict is NOT passed through to the CLI: the
    engine sets no passthrough flags, so nothing untrusted can ride ``extra_args`` to
    weaken the isolation triad (closes the deferred smuggling-vector finding)."""
    q = _query(
        extra={"extra_args": {"system-prompt-cache-ttl": "5m", "setting-sources": "user"}}
    )
    options = build_engine_options(q)
    assert options.extra_args == {}


# ---------------------------------------------------------------------------
# AC 2 — the stable prefix rides system_prompt; the ask does NOT
# ---------------------------------------------------------------------------

def test_stable_prefix_rides_system_prompt_and_ask_does_not() -> None:
    q = _query()
    options = build_engine_options(q)
    assert options.system_prompt == q.system_prompt
    assert isinstance(options.system_prompt, str)
    assert _ASK not in options.system_prompt


# ---------------------------------------------------------------------------
# AC 3 — the isolation triad holds by construction on EVERY call
# ---------------------------------------------------------------------------

def test_isolation_triad_by_construction() -> None:
    options = build_engine_options(_query())
    assert options.strict_mcp_config is True
    assert options.setting_sources == []


# ---------------------------------------------------------------------------
# AC 4 — headless permission mode + host built-ins blocked
# ---------------------------------------------------------------------------

def test_permission_mode_and_host_builtins_blocked() -> None:
    options = build_engine_options(_query())
    assert options.permission_mode == "bypassPermissions"
    for tool in _HOST_BUILTINS:
        assert tool in options.disallowed_tools
    # The module-level triad markers: the local six stay the seam-verbatim value,
    # and the module block-list is a SUPERSET (local six + dangerous host built-ins).
    assert SDK_LOCAL_TOOLS == _HOST_BUILTINS
    assert set(SDK_LOCAL_TOOLS) <= set(disallowed_tools)
    assert permission_mode == "bypassPermissions"


def test_dangerous_host_builtins_blocked_on_every_call() -> None:
    """The full dangerous built-in set (Agent/Task family, WebSearch/WebFetch,
    worktree/cron/notify/skill executors) rides ``disallowed_tools`` so the CLI
    blocks them — the SDK ``Agent`` fabricated-"run" hole is closed. Curated
    ``mcp__*`` tools are never blocked."""
    options = build_engine_options(_query())
    for tool in (*_HOST_BUILTINS, *_DANGEROUS_HOST_BUILTINS):
        assert tool in options.disallowed_tools, f"{tool} not blocked"
    assert not any(t.startswith("mcp__") for t in options.disallowed_tools)


def test_query_disallowed_tools_merge_dedup_order_preserving() -> None:
    """A per-query block-list MERGES into the module block-list (dedup,
    order-preserving) — a read-only turn's write block rides the options."""
    q = _query(disallowed_tools=("mcp__code__write", "Bash"))
    options = build_engine_options(q)
    assert options.disallowed_tools == [*disallowed_tools, "mcp__code__write"]


# ---------------------------------------------------------------------------
# AC 5 — the mcp_servers + empty-tools caveat
# ---------------------------------------------------------------------------

def test_mcp_servers_with_empty_tools_drops_tools_to_none() -> None:
    """An empty ``--tools ""`` would zero the SDK base set and suppress the
    mounted MCP tools — so with servers mounted and no computed built-ins,
    ``tools`` must be dropped to ``None`` (SDK default)."""
    servers = {"code": {"type": "http", "url": "http://sandbox:8081/mcp"}}
    q = _query(mcp_servers=servers, tools=())
    options = build_engine_options(q)
    assert options.tools is None
    assert options.mcp_servers == servers


def test_mcp_servers_with_nonempty_tools_preserves_them() -> None:
    servers = {"code": {"type": "http", "url": "http://sandbox:8081/mcp"}}
    q = _query(mcp_servers=servers, tools=("WebSearch",))
    options = build_engine_options(q)
    assert options.tools == ["WebSearch"]


# ---------------------------------------------------------------------------
# AC 6 — a real ClaudeAgentOptions instance (not a dict), fields threaded
# ---------------------------------------------------------------------------

def test_returns_real_claude_agent_options_instance() -> None:
    options = build_engine_options(_query())
    assert isinstance(options, ClaudeAgentOptions)
    assert not isinstance(options, dict)


def test_query_fields_thread_onto_options() -> None:
    q = _query(
        max_turns=6,
        resume="sess-abc123",
        env={"MAX_OUTPUT_TOKENS": "32000"},
    )
    options = build_engine_options(q)
    assert options.model == q.model
    assert options.allowed_tools == list(q.allowed_tools)
    assert options.max_turns == 6
    assert options.resume == "sess-abc123"
    # The per-query env rides through; the engine's own env pin is additive.
    assert options.env["MAX_OUTPUT_TOKENS"] == "32000"


# ---------------------------------------------------------------------------
# AC 7 — curated MCP tool schemas load UP FRONT: no per-turn ToolSearch tax
# ---------------------------------------------------------------------------

def test_tool_search_deferral_disabled_on_every_call() -> None:
    """``ENABLE_TOOL_SEARCH=false`` rides the subprocess env on EVERY engine call:
    the CLI runs in standard tool-loading mode, so the 8 tiny curated ``mcp__*``
    schemas are advertised up front and no ToolSearch round-trip is ever needed
    (verified against the real bundled CLI: ToolSearch leaves the init tool set)."""
    options = build_engine_options(_query())
    assert options.env["ENABLE_TOOL_SEARCH"] == "false"


def test_tool_search_pin_merges_with_query_env_and_cannot_be_overridden() -> None:
    """The per-query env (e.g. the output-token clamp) is preserved, and nothing
    a query carries can re-enable schema deferral — the engine owns this pin."""
    q = _query(env={"MAX_OUTPUT_TOKENS": "32000", "ENABLE_TOOL_SEARCH": "true"})
    options = build_engine_options(q)
    assert options.env["MAX_OUTPUT_TOKENS"] == "32000"
    assert options.env["ENABLE_TOOL_SEARCH"] == "false"


# ---------------------------------------------------------------------------
# AC 8 — the [CRITICAL] tripwire watches the FULL dangerous built-in set
# ---------------------------------------------------------------------------

def _tool_use(name: str) -> AgentChunk:
    return AgentChunk(type="TOOL_USE", text="", metadata={"id": "tu-1", "name": name, "input": {}})


def test_tripwire_flags_agent_and_the_dangerous_builtins_in_sandbox_mode() -> None:
    """An ``Agent`` (or any dangerous host built-in) TOOL_USE in sandbox mode is an
    isolation leak — the tripwire must fire, not just for the local six."""
    for name in ("Agent", "Task", "WebSearch", "WebFetch", "Bash", "SendMessage"):
        assert check_critical_tripwire(_tool_use(name), sandbox_mode=True), name
    # Case-insensitive: the watch matches however the name is cased on the wire.
    assert check_critical_tripwire(_tool_use("agent"), sandbox_mode=True)


def test_tripwire_silent_for_curated_mcp_tools_and_outside_sandbox() -> None:
    assert not check_critical_tripwire(_tool_use("mcp__code_intel__search"), sandbox_mode=True)
    assert not check_critical_tripwire(_tool_use("Agent"), sandbox_mode=False)


def test_thinking_off_by_default_adaptive_for_opus_budget_otherwise() -> None:
    assert build_engine_options(_query()).thinking is None
    adaptive = build_engine_options(_query(thinking_enabled=True))
    assert adaptive.thinking == {"type": "adaptive"}
    budgeted = build_engine_options(
        _query(model="claude-sonnet-4-5", thinking_enabled=True, thinking_budget_tokens=2048)
    )
    assert budgeted.thinking == {"type": "enabled", "budget_tokens": 2048}
