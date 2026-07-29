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

from in_meeting.provider import (
    SDK_LOCAL_TOOLS,
    build_engine_options,
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
# AC 1 — the 1-hour prompt-cache directive is present (and unconditional)
# ---------------------------------------------------------------------------

def test_cache_ttl_directive_present() -> None:
    options = build_engine_options(_query())
    assert options.extra_args["system-prompt-cache-ttl"] == "1h"


def test_cache_ttl_wins_over_query_extra_and_merge_preserved() -> None:
    """A ``query.extra``-provided ``extra_args`` sub-dict is merged, but the
    cache-ttl always wins/persists (it is unconditional on every engine turn)."""
    q = _query(
        extra={"extra_args": {"system-prompt-cache-ttl": "5m", "some-flag": "on"}}
    )
    options = build_engine_options(q)
    assert options.extra_args["system-prompt-cache-ttl"] == "1h"
    assert options.extra_args["some-flag"] == "on"


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
    # The module-level triad markers mirror the seam values verbatim.
    assert SDK_LOCAL_TOOLS == _HOST_BUILTINS
    assert disallowed_tools == SDK_LOCAL_TOOLS
    assert permission_mode == "bypassPermissions"


def test_query_disallowed_tools_merge_dedup_order_preserving() -> None:
    """A per-query block-list MERGES into the module block-list (dedup,
    order-preserving) — a read-only turn's write block rides the options."""
    q = _query(disallowed_tools=("mcp__code__write", "Bash"))
    options = build_engine_options(q)
    assert options.disallowed_tools == [*SDK_LOCAL_TOOLS, "mcp__code__write"]


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
    assert options.env == {"MAX_OUTPUT_TOKENS": "32000"}


def test_thinking_off_by_default_adaptive_for_opus_budget_otherwise() -> None:
    assert build_engine_options(_query()).thinking is None
    adaptive = build_engine_options(_query(thinking_enabled=True))
    assert adaptive.thinking == {"type": "adaptive"}
    budgeted = build_engine_options(
        _query(model="claude-sonnet-4-5", thinking_enabled=True, thinking_budget_tokens=2048)
    )
    assert budgeted.thinking == {"type": "enabled", "budget_tokens": 2048}
