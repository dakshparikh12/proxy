"""Doc 05 · the mcp_servers provider-seam fix — the curated MCP servers a behavior
advertises are ACTUALLY MOUNTED onto the real SDK query() (05 §3.3 / §3.5, 04 §3.3).

The confirmed root cause these tests lock down: a Workroom/wake path advertised
``mcp__<server>__*`` tool names via ``allowed_tools`` but the servers that PROVIDE those
tools were never mounted onto the ``ClaudeAgentOptions`` handed to
``claude_agent_sdk.query()`` — so the model could never call them. The fix threads an
``mcp_servers`` mapping through every query-builder:

    query-builder → ProviderQuery.mcp_servers → build_sdk_options → ClaudeAgentOptions.mcp_servers

These are UNIT tests (no live SDK, no E2B) that prove each hop of that seam carries the
mapping; the live proof is the WORKROOM_LIVE_E2E gate (test_workroom_real_task.py).
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from libs.ops import sandbox_provider


# ── (1) ProviderQuery carries mcp_servers; build_sdk_options mounts it ────────
@pytest.mark.integration
def test_provider_query_has_mcp_servers_field_default_none() -> None:
    """``ProviderQuery`` gains an ``mcp_servers`` field, default None (backward-compatible)."""
    from agentkit import ProviderQuery

    q = ProviderQuery(model="claude-opus-4-8", allowed_tools=("mcp__code__read_file",))
    assert q.mcp_servers is None, "default is None → no servers mounted (backward-compatible)"
    q2 = ProviderQuery(
        model="m", allowed_tools=(), mcp_servers={"code": object()}
    )
    assert set(q2.mcp_servers) == {"code"}


@pytest.mark.integration
def test_build_sdk_options_mounts_mcp_servers_onto_claude_agent_options() -> None:
    """``build_sdk_options`` sets ``options.mcp_servers`` from ``query.mcp_servers`` — the hop
    that was missing. The isolation triad stays intact (strict_mcp_config=True)."""
    from agentkit import ProviderQuery
    from control_plane.provider import build_sdk_options

    code_server = {"type": "http", "url": "https://8081-sbx.e2b.app/mcp"}
    q = ProviderQuery(
        model="claude-opus-4-8",
        allowed_tools=("mcp__code__read_file", "mcp__code__edit_file"),
        mcp_servers={"code": code_server},
    )
    options = build_sdk_options("edit the file", q)
    assert options.mcp_servers == {"code": code_server}, (
        "the code server MUST be mounted onto the ClaudeAgentOptions the SDK query() receives — "
        "without this the mcp__code__* tools the behavior advertises are never reachable"
    )
    # The triad is untouched: ONLY these explicitly-passed servers are mounted.
    assert options.strict_mcp_config is True
    assert list(options.setting_sources) == []


@pytest.mark.integration
def test_build_sdk_options_no_mcp_servers_is_backward_compatible() -> None:
    """A query WITHOUT mcp_servers leaves the SDK default (no crash, no bogus mount)."""
    from agentkit import ProviderQuery
    from control_plane.provider import build_sdk_options

    q = ProviderQuery(model="claude-opus-4-8", allowed_tools=("speak",))
    options = build_sdk_options("prompt", q)
    # The SDK default for mcp_servers is an empty dict — never our sentinel.
    assert not options.mcp_servers


# ── (2) BehaviorRunner threads a provided mcp_servers onto the ProviderQuery ──
@pytest.mark.integration
def test_behavior_runner_threads_mcp_servers_onto_the_query() -> None:
    """The orchestrator wake turn: ``BehaviorRunner`` accepts an ``mcp_servers`` mapping and
    sets it on the ``ProviderQuery`` — so the wake turn CAN mount its code-intel MCP server."""
    from agentkit import BehaviorConfig, BehaviorRunner

    code_intel_server = {"type": "sdk", "name": "code_intel"}
    b = BehaviorConfig(name="wake", tools=("mcp__code_intel__grep",), model="claude-opus-4-8")
    runner = BehaviorRunner(config=b, mcp_servers={"code_intel": code_intel_server})
    query = runner.build_query(None, {})
    assert query.mcp_servers == {"code_intel": code_intel_server}, (
        "a provided server MUST reach the ProviderQuery so the wake turn's code-intel tools "
        "are actually mounted (the seam gap this closes)"
    )


@pytest.mark.integration
def test_behavior_runner_without_mcp_servers_is_none() -> None:
    """No mcp_servers wired → the ProviderQuery carries None (backward-compatible default)."""
    from agentkit import BehaviorConfig, BehaviorRunner

    b = BehaviorConfig(name="catchup", tools=("speak",), model="m")
    runner = BehaviorRunner(config=b)
    assert runner.build_query(None, {}).mcp_servers is None


# ── (3) SessionDriver builds a ProviderQuery carrying the code server ─────────
@pytest.mark.integration
def test_session_driver_builds_provider_query_with_code_server_mounted() -> None:
    """``SessionDriver._build_query_options`` returns an ``agentkit.ProviderQuery`` (the shape
    the provider expects — NOT a ClaudeAgentOptions) carrying ``mcp_servers={'code': ...}`` so
    the worker's ``mcp__code__*`` tools are mounted. This is the fix for SEAM BREAK #1 (wrong
    type handed to the provider) + SEAM GAP #2 (the code server never mounted)."""
    from agentkit import ProviderQuery

    from workroom.session import SessionDriver

    sandbox_provider._reset_for_test()
    handle = sandbox_provider.provision(meeting_id="m-seam")

    driver = SessionDriver(disposition="worker", model="claude-opus-4-8")
    options = driver._build_query_options(handle, access="readwrite", meeting_id="m-seam")

    # SEAM BREAK #1: the driver now builds the type the provider's stream(prompt, query) expects.
    assert isinstance(options, ProviderQuery), (
        "the driver MUST build an agentkit.ProviderQuery — a ClaudeAgentOptions has no "
        "thinking_enabled attribute and the provider AttributeErrors on it (SEAM BREAK #1)"
    )
    # SEAM GAP #2: the sandbox `code` server is mounted, so mcp__code__* is reachable.
    assert options.mcp_servers is not None and "code" in options.mcp_servers, (
        "the sandbox `code` MCP server MUST be mounted — the worker advertises mcp__code__* "
        "read/write tools whose providing server was never mounted before this fix"
    )
    # The worker also mounts the host-side propose_change server (the one sanctioned write, §3.8).
    assert "propose_change" in options.mcp_servers, (
        "the readwrite worker mounts the host propose_change server so the staged-draft write "
        "(mcp__propose_change__*) is reachable"
    )
    # The worker advertises the mcp__code__* tools those servers provide.
    assert any(t.startswith("mcp__code__") for t in options.allowed_tools)
    # The isolation triad rides the ProviderQuery by construction.
    assert options.strict_mcp_config is True
    assert tuple(options.tools) == ()  # computed built-in list is [] in sandbox mode


@pytest.mark.integration
def test_session_driver_captures_draft_id_from_propose_change_result() -> None:
    """A successful ``propose_change`` TOOL_RESULT carries the staged ``draft_id``; the driver
    captures it onto the terminal Envelope so the §1.2 needs_review/unverified mapping fires
    (Law 3 — a world-touching change is a staged draft). A failed staging (is_error) is ignored
    — it never fabricates a draft."""
    from workroom.session import _extract_draft_id

    ok = '{"draft_id": "b1a7c0de-0000-4000-8000-000000000001", "status": "needs_review"}'
    assert _extract_draft_id(ok) == "b1a7c0de-0000-4000-8000-000000000001"
    # A failed staging (no host conn) carries no draft_id and NEVER fabricates one.
    err = '{"code": "propose_change_error", "message": "no host connection bound", "is_error": true}'
    assert _extract_draft_id(err) is None
    # A non-JSON / read-tool result is ignored.
    assert _extract_draft_id('{"content": "def login(): ..."}') is None
    assert _extract_draft_id(None) is None


@pytest.mark.integration
def test_session_driver_readonly_disposition_mounts_code_without_propose_change() -> None:
    """A read-only disposition mounts the sandbox `code` server (read tools) but NEVER the
    propose_change write server (§3.5 / §3.8 — only the worker may write to the world)."""
    from workroom.session import SessionDriver

    sandbox_provider._reset_for_test()
    handle = sandbox_provider.provision(meeting_id="m-ro")

    driver = SessionDriver(disposition="quick", model="claude-sonnet-4-6")
    options = driver._build_query_options(handle, access="readonly", meeting_id="m-ro")

    assert "code" in (options.mcp_servers or {}), "read-only still reads code through mcp__code__*"
    assert "propose_change" not in (options.mcp_servers or {}), (
        "a read-only disposition must NEVER mount the world-touching propose_change server"
    )
