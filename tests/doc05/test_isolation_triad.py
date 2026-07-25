"""Acceptance — the Workroom SDK-isolation triad on every ``query()`` (05 §3.4).

Node ``workroom.sdk-isolation-triad`` (evidence class ``[negative]``). The one thing
catastrophic to get wrong: E2B isolates the *sandbox*, but ``query()`` runs its tools on
the *orchestrator host*. Without the triad the Workroom agent (i) inherits the host's
discovered MCP config — the operator's **claude.ai connectors (Gmail/Slack/Drive)** — and
(ii) runs ``Bash``/``Read``/``Grep`` **on the host**. §3.4 mandates ALL THREE layers on
EVERY call:

  1. ``strict_mcp_config=True`` — ignore ALL discovered ``.mcp.json`` / user settings /
     claude.ai connectors. ``setting_sources=[]`` alone does NOT suppress connectors.
  2. ``setting_sources=[]`` — load NO filesystem permissions/hooks/CLAUDE.md.
  3. a COMPUTED built-in ``tools`` allow-list — ``[]`` in sandbox mode. This (NOT
     ``disallowed_tools``) is the REAL gate: ``disallowed_tools`` does not reliably remove
     built-ins under ``permission_mode="bypassPermissions"``.

Plus the ``SDK_LOCAL_TOOLS`` block-list pinned into ``disallowed_tools`` as the backstop —
including ``Task`` (a subagent is a sandbox-isolation escape: ``disallowed_tools`` does not
propagate to child agents).

These run on the REAL host path: ``workroom_options(...)`` builds a real
``claude_agent_sdk.ClaudeAgentOptions`` from the installed SDK — no stub, no dict.
"""
from __future__ import annotations

import dataclasses

import pytest

pytestmark = pytest.mark.isolation


def _options(**overrides):
    """Build a Workroom options object via the real host path with sane defaults."""
    from workroom.agent_config import workroom_options

    kwargs = dict(
        system_prompt="You are Proxy grounding on this repo.",
        allowed_tools=["mcp__code__read", "mcp__code__grep", "mcp__code__edit_file"],
        mcp_servers={"code": {"type": "http", "url": "https://sbx.local/mcp"}},
        model="claude-opus-4-8",
        max_turns=1,
    )
    kwargs.update(overrides)
    return workroom_options(**kwargs)


# ---------------------------------------------------------------------------
# It returns a REAL ClaudeAgentOptions — not a plain dict (DoD hard requirement)
# ---------------------------------------------------------------------------

def test_returns_real_claude_agent_options_not_a_dict() -> None:
    from claude_agent_sdk import ClaudeAgentOptions

    opts = _options()
    assert isinstance(opts, ClaudeAgentOptions), (
        "workroom_options must return a real claude_agent_sdk.ClaudeAgentOptions "
        "fed to query(), NOT a plain dict"
    )
    assert not isinstance(opts, dict)


def test_dict_returning_stub_is_gone() -> None:
    """The old ``build_workroom_query_config`` dict stub must be deleted (D-007 P0)."""
    import workroom.agent_config as ac

    assert not hasattr(ac, "build_workroom_query_config"), (
        "the actively-wrong dict-returning stub must be deleted, not extended"
    )


# ---------------------------------------------------------------------------
# Triad layer 1 — strict_mcp_config=True (suppress discovered connectors)
# ---------------------------------------------------------------------------

def test_strict_mcp_config_true_suppresses_connectors() -> None:
    opts = _options()
    assert opts.strict_mcp_config is True, (
        "strict_mcp_config=True is separately load-bearing: it suppresses discovered "
        ".mcp.json / user settings / claude.ai connectors (Gmail/Slack/Drive). "
        "setting_sources=[] alone does NOT."
    )


# ---------------------------------------------------------------------------
# Triad layer 2 — setting_sources=[] (load no host filesystem settings)
# ---------------------------------------------------------------------------

def test_setting_sources_empty_loads_no_host_settings() -> None:
    opts = _options()
    assert opts.setting_sources == [], (
        "setting_sources=[] loads NO filesystem permissions/hooks/CLAUDE.md from the host"
    )


# ---------------------------------------------------------------------------
# Triad layer 3 — the COMPUTED built-in tools list is [] in sandbox mode (the REAL gate)
# ---------------------------------------------------------------------------

def test_computed_tools_is_empty_in_sandbox_mode() -> None:
    """In sandbox mode allowed_tools are all mcp__* → the built-in allow-list is []."""
    opts = _options(allowed_tools=["mcp__code__read", "mcp__code__edit_file"])
    assert opts.tools == [], (
        "the computed built-in tools list must be [] in sandbox mode — this (NOT "
        "disallowed_tools) is the real gate that stops host Read/Grep/Bash executing "
        "on the orchestrator host"
    )


def test_computed_tools_excludes_host_builtins_even_if_passed_in_allowed() -> None:
    """A host built-in that leaks into allowed_tools must NOT surface in the computed
    built-in list — it is filtered by SDK_LOCAL_TOOLS (belt) and the mcp__-only rule."""
    from workroom.agent_config import SDK_LOCAL_TOOLS

    opts = _options(allowed_tools=["mcp__code__read", "Read", "Bash", "Task"])
    for host_builtin in ("Read", "Bash", "Task"):
        assert host_builtin in SDK_LOCAL_TOOLS
        assert host_builtin not in opts.tools, (
            f"host built-in {host_builtin!r} must never appear in the computed "
            "built-in tools list (it would execute on the host)"
        )


def test_tools_is_the_gate_not_disallowed_tools() -> None:
    """DoD: NOT done if disallowed_tools is relied on as the gate instead of tools=[].

    Even with a permissive allowed_tools naming host built-ins, the computed ``tools``
    list is the empty allow-list — the SDK loads NO host built-ins. ``disallowed_tools``
    is only the belt-and-suspenders backstop, never the primary gate.
    """
    opts = _options(allowed_tools=["Read", "Grep", "Glob", "Bash", "mcp__code__read"])
    assert opts.tools == [], "tools=[] is the gate; it must be empty regardless of allowed_tools"


# ---------------------------------------------------------------------------
# The SDK_LOCAL_TOOLS block-list (backstop) — full §3.4 set incl. Task
# ---------------------------------------------------------------------------

# The complete §3.4 block-list — every host-executing SDK built-in.
_REQUIRED_BLOCKLIST = {
    "Bash", "BashOutput", "KillShell", "Read", "Write", "Edit",
    "Glob", "Grep", "NotebookEdit",
    "Task",  # subagent escape — disallowed_tools does NOT propagate to child agents
    "EnterPlanMode", "ExitPlanMode", "Skill", "SlashCommand",
    "WebFetch", "WebSearch",
}


def test_sdk_local_tools_is_the_full_blocklist() -> None:
    from workroom.agent_config import SDK_LOCAL_TOOLS

    present = set(SDK_LOCAL_TOOLS)
    missing = _REQUIRED_BLOCKLIST - present
    assert not missing, f"SDK_LOCAL_TOOLS missing §3.4 host built-ins: {sorted(missing)}"


def test_task_is_blocked_as_isolation_escape() -> None:
    """Task spawns a host subprocess with its OWN unrestricted tools — a sandbox escape.
    DoD: NOT done if Task is absent from the block-list."""
    from workroom.agent_config import SDK_LOCAL_TOOLS

    assert "Task" in SDK_LOCAL_TOOLS


def test_named_network_and_shell_builtins_blocked() -> None:
    from workroom.agent_config import SDK_LOCAL_TOOLS

    for tool in ("WebFetch", "WebSearch", "Bash", "Read", "Write", "Edit", "NotebookEdit"):
        assert tool in SDK_LOCAL_TOOLS, f"{tool} must be in the block-list (§3.4 / DoD)"


def test_disallowed_tools_carries_the_full_blocklist() -> None:
    opts = _options()
    from workroom.agent_config import SDK_LOCAL_TOOLS

    assert set(opts.disallowed_tools) >= set(SDK_LOCAL_TOOLS), (
        "disallowed_tools must pin the full SDK_LOCAL_TOOLS block-list as the backstop"
    )
    assert "Task" in opts.disallowed_tools


# ---------------------------------------------------------------------------
# All three layers ride EVERY call — parametrized over disposition shapes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "kwargs",
    [
        {"allowed_tools": ["mcp__code__read"]},                       # read-only disposition
        {"allowed_tools": ["mcp__code__read", "mcp__code__edit_file"]},  # worker disposition
        {"resume": "sess-abc-123"},                                    # a resumed session
        {"max_turns": 40},                                             # a long build turn
    ],
)
def test_triad_rides_every_call_shape(kwargs) -> None:
    opts = _options(**kwargs)
    assert opts.strict_mcp_config is True
    assert opts.setting_sources == []
    assert opts.tools == []
    assert "Task" in opts.disallowed_tools


def test_permission_mode_is_bypass_for_headless_server_agent() -> None:
    """Headless server agents run bypassPermissions — which is exactly why tools=[]
    (not disallowed_tools) must be the gate."""
    opts = _options()
    assert opts.permission_mode == "bypassPermissions"


# ---------------------------------------------------------------------------
# The one-write law + 1hr cache prefix (kept from the prior module contract)
# ---------------------------------------------------------------------------

def test_propose_change_is_the_one_write_and_not_disallowed() -> None:
    """The ONLY sanctioned mutation is the staged draft; it is never in the block-list."""
    from workroom.agent_config import SDK_LOCAL_TOOLS, propose_change

    assert callable(propose_change)
    assert "propose_change" not in SDK_LOCAL_TOOLS


def test_one_hour_cache_prefix_preserved() -> None:
    from workroom.agent_config import WORKROOM_CACHE_TTL_SECONDS

    assert WORKROOM_CACHE_TTL_SECONDS == 3600, (
        "the stable repo-grounding prefix carries a 1-hour prompt cache (not the "
        "default 5-minute TTL)"
    )


# ---------------------------------------------------------------------------
# The guard (libs/ops/check_sdk_isolation_triad) passes on the new module
# ---------------------------------------------------------------------------

def test_check_sdk_isolation_triad_guard_passes() -> None:
    """ops.check_sdk_isolation_triad must return 0 with the rebuilt module in place."""
    from ops.check_sdk_isolation_triad import check

    assert check() == 0


def test_module_carries_the_guard_triad_markers() -> None:
    """The guard requires SDK_LOCAL_TOOLS + disallowed_tools + permission_mode markers on
    any module hosting a query() site; agent_config is the triad's authoritative owner and
    carries all three so a downstream query() importing it is covered (§11.11)."""
    import inspect

    import workroom.agent_config as ac

    src = inspect.getsource(ac)
    for marker in ("SDK_LOCAL_TOOLS", "disallowed_tools", "permission_mode"):
        assert marker in src, f"guard marker {marker!r} absent from agent_config.py"


# ---------------------------------------------------------------------------
# No user-visible internal names leak (Hard Rule: naming) — sanity on the prefix
# ---------------------------------------------------------------------------

def test_no_internal_component_names_in_system_prefix() -> None:
    from workroom.agent_config import WORKROOM_SYSTEM_PREFIX

    lowered = WORKROOM_SYSTEM_PREFIX.lower()
    for internal in ("orchestrator", "scribe", "workroom"):
        assert internal not in lowered, (
            f"user-visible system prefix must not carry the internal name {internal!r}"
        )
    assert "proxy" in lowered
