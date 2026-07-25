"""Acceptance: the CONCRETE Claude AgentProvider (services/harness/provider.py)
maps REAL ``claude_agent_sdk`` messages → the six ``AgentChunk`` variants
(04 §3.3, CANONICAL §1.1 / §11.10 / §12.3, D-022).

The SDK-message → AgentChunk mapping is a confirm-at-build item (§11.10): these
tests feed **real** ``claude_agent_sdk`` message objects (SystemMessage /
AssistantMessage / UserMessage / ResultMessage and the real content blocks —
constructed from the installed 0.2.128 dataclasses, never hand-rolled dicts)
through the provider and assert the normalized chunks + metadata keys. No live
CLI round-trip is needed for the unit mapping — the provider's translator is a
pure function over already-parsed SDK messages.
"""
from __future__ import annotations

import logging

import pytest

# REAL installed SDK types — the mapping is confirmed against these, not guessed.
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from libs.agentkit import Provider, ProviderError, ProviderQuery
from libs.contracts import AGENT_CHUNK_METADATA_KEYS, AgentChunk

from harness.provider import (
    SDK_LOCAL_TOOLS,
    ClaudeAgentProvider,
    build_sdk_options,
    check_critical_tripwire,
    disallowed_tools,
    map_sdk_message,
    permission_mode,
    pick_provider,
    redact_stderr_line,
    register_claude_provider,
    sanitize_env,
)


# ---------------------------------------------------------------------------
# SDK-message → AgentChunk mapping (the heart of §3.3, confirmed live §11.10)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_init_from_system_init_carries_session_id_tools_mcp():
    # SystemMessage(subtype="init", data={...}) — the real init shape.
    msg = SystemMessage(
        subtype="init",
        data={
            "session_id": "sess-abc",
            "tools": ["read", "speak"],
            "mcp_servers": [{"name": "code_intel", "status": "connected"}],
            "model": "claude-sonnet-4-6",
        },
    )
    chunks = list(map_sdk_message(msg))
    assert [c.type for c in chunks] == ["INIT"]
    init = chunks[0]
    assert init.metadata["session_id"] == "sess-abc"
    assert init.metadata["tools"] == ["read", "speak"]
    assert init.metadata["mcp_servers"] == [{"name": "code_intel", "status": "connected"}]
    # Metadata keys are within the canonical INIT allow-set.
    assert set(init.metadata).issubset(AGENT_CHUNK_METADATA_KEYS["INIT"] | {"model"})


@pytest.mark.integration
def test_non_init_system_message_is_dropped_not_misclassified():
    # A non-init system subtype must NOT masquerade as an INIT chunk.
    msg = SystemMessage(subtype="status", data={"note": "compacting"})
    assert list(map_sdk_message(msg)) == []


@pytest.mark.integration
def test_text_is_accumulated_per_msg_id_not_a_delta():
    # AssistantMessage carries TextBlocks; TEXT.text is ACCUMULATED per msg_id
    # (CANONICAL §1.1 — the load-bearing contract), and msg_id == message_id.
    msg = AssistantMessage(
        content=[TextBlock(text="Hello world")],
        model="claude-sonnet-4-6",
        message_id="msg-1",
        session_id="sess-abc",
    )
    chunks = list(map_sdk_message(msg))
    assert [c.type for c in chunks] == ["TEXT"]
    text = chunks[0]
    assert text.text == "Hello world", "TEXT.text is the accumulated text, not a delta"
    assert text.metadata["msg_id"] == "msg-1"
    assert set(text.metadata).issubset(AGENT_CHUNK_METADATA_KEYS["TEXT"] | {"turn"})


@pytest.mark.integration
def test_thinking_block_is_not_emitted_as_a_visible_text_chunk():
    # A ThinkingBlock is not a consumer-visible TEXT chunk (barge-in/TTS speaks
    # TEXT, never the reasoning preamble).
    msg = AssistantMessage(
        content=[ThinkingBlock(thinking="reasoning...", signature="sig"), TextBlock(text="Answer")],
        model="claude-opus-4-8",
        message_id="msg-2",
    )
    types = [c.type for c in map_sdk_message(msg)]
    assert types == ["TEXT"], "only the visible TextBlock becomes a TEXT chunk"


@pytest.mark.integration
def test_tool_use_block_maps_to_tool_use_with_id_name_input():
    msg = AssistantMessage(
        content=[ToolUseBlock(id="tu-1", name="get_dependents", input={"symbol": "checkout"})],
        model="claude-sonnet-4-6",
        message_id="msg-3",
    )
    chunks = list(map_sdk_message(msg))
    assert [c.type for c in chunks] == ["TOOL_USE"]
    tu = chunks[0]
    assert tu.metadata["id"] == "tu-1"
    assert tu.metadata["name"] == "get_dependents"
    assert tu.metadata["input"] == {"symbol": "checkout"}
    assert set(tu.metadata).issubset(AGENT_CHUNK_METADATA_KEYS["TOOL_USE"])


@pytest.mark.integration
def test_tool_result_block_maps_to_tool_result_with_tool_use_id_and_is_error():
    # Tool results come back on a UserMessage in the SDK convention.
    msg = UserMessage(
        content=[ToolResultBlock(tool_use_id="tu-1", content="42 dependents", is_error=False)],
    )
    chunks = list(map_sdk_message(msg))
    assert [c.type for c in chunks] == ["TOOL_RESULT"]
    tr = chunks[0]
    assert tr.metadata["tool_use_id"] == "tu-1"
    assert tr.metadata["is_error"] is False
    assert tr.text == "42 dependents"
    assert set(tr.metadata).issubset(AGENT_CHUNK_METADATA_KEYS["TOOL_RESULT"])


@pytest.mark.integration
def test_tool_result_list_content_is_flattened_to_text():
    # content can be a list[dict] of blocks; the seam surfaces text for consumers.
    msg = UserMessage(
        content=[
            ToolResultBlock(
                tool_use_id="tu-9",
                content=[{"type": "text", "text": "line A"}, {"type": "text", "text": "line B"}],
                is_error=True,
            )
        ],
    )
    (tr,) = list(map_sdk_message(msg))
    assert tr.type == "TOOL_RESULT"
    assert tr.metadata["is_error"] is True
    assert "line A" in tr.text and "line B" in tr.text


@pytest.mark.integration
def test_result_message_carries_total_cost_usd_and_session_id():
    msg = ResultMessage(
        subtype="success",
        duration_ms=1200,
        duration_api_ms=900,
        is_error=False,
        num_turns=2,
        session_id="sess-abc",
        total_cost_usd=0.0123,
        result="done",
        structured_output={"answer": "here"},
    )
    chunks = list(map_sdk_message(msg))
    assert [c.type for c in chunks] == ["RESULT"]
    r = chunks[0]
    assert r.metadata["total_cost_usd"] == 0.0123, "the cost-meter seam reads this"
    assert r.metadata["session_id"] == "sess-abc"
    assert r.metadata["num_turns"] == 2
    assert r.metadata["structured_output"] == {"answer": "here"}
    assert set(r.metadata).issubset(AGENT_CHUNK_METADATA_KEYS["RESULT"] | {"subtype"})


@pytest.mark.integration
def test_result_none_cost_defaults_to_zero_not_missing():
    # A RESULT with no cost still populates total_cost_usd so the meter never KeyErrors.
    msg = ResultMessage(
        subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
        num_turns=1, session_id="s", total_cost_usd=None,
    )
    (r,) = list(map_sdk_message(msg))
    assert r.metadata["total_cost_usd"] == 0.0


@pytest.mark.integration
def test_result_is_error_becomes_error_chunk():
    # is_error result → a terminal ERROR chunk (surfaced as ProviderError at the boundary).
    msg = ResultMessage(
        subtype="error_during_execution", duration_ms=1, duration_api_ms=1, is_error=True,
        num_turns=1, session_id="s", total_cost_usd=0.0, result="boom",
    )
    (e,) = list(map_sdk_message(msg))
    assert e.type == "ERROR"
    assert "boom" in e.metadata["message"]
    assert set(e.metadata).issubset(AGENT_CHUNK_METADATA_KEYS["ERROR"])


@pytest.mark.integration
def test_assistant_error_field_becomes_error_chunk():
    # AssistantMessage.error (a stale-session/auth class) → ERROR chunk, not silence.
    msg = AssistantMessage(
        content=[], model="claude-sonnet-4-6", message_id="msg-x", error="rate_limit",
    )
    types_and_msgs = [(c.type, c.metadata.get("message")) for c in map_sdk_message(msg)]
    assert ("ERROR", "rate_limit") in [(t, m) for t, m in types_and_msgs]


# ---------------------------------------------------------------------------
# ERROR never raised in-band; surfaced as ProviderError at the boundary
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_error_chunk_wraps_as_provider_error_without_raising_in_band():
    err = AgentChunk(type="ERROR", metadata={"message": "stale session"})
    pe = ProviderError(err)
    assert pe.chunk is err
    assert "stale session" in str(pe)


# ---------------------------------------------------------------------------
# ClaudeAgentProvider satisfies the Protocol; streams AgentChunk end to end
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_claude_provider_satisfies_the_provider_protocol():
    assert isinstance(ClaudeAgentProvider(), Provider)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_claude_provider_streams_normalized_chunks_from_synthetic_sdk_stream():
    # Inject a fake SDK async-query returning REAL SDK message objects; the provider
    # normalizes them to AgentChunk without any live CLI call.
    async def fake_query(*, prompt, options):  # noqa: ARG001
        yield SystemMessage(subtype="init", data={"session_id": "s1", "tools": [], "mcp_servers": []})
        yield AssistantMessage(
            content=[TextBlock(text="Hi")],
            model="claude-sonnet-4-6", message_id="m1", session_id="s1",
        )
        yield AssistantMessage(
            content=[ToolUseBlock(id="t1", name="speak", input={"text": "Hi"})],
            model="claude-sonnet-4-6", message_id="m1",
        )
        yield ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="s1", total_cost_usd=0.002,
        )

    provider = ClaudeAgentProvider(query_fn=fake_query)
    q = ProviderQuery(model="claude-sonnet-4-6", allowed_tools=("speak",))
    got = [c async for c in provider.stream("say hi", q)]
    assert [c.type for c in got] == ["INIT", "TEXT", "TOOL_USE", "RESULT"]
    assert got[0].metadata["session_id"] == "s1"
    assert got[-1].metadata["total_cost_usd"] == 0.002


@pytest.mark.integration
@pytest.mark.asyncio
async def test_provider_surfaces_transport_failure_as_terminal_error_chunk_not_raise():
    # A transport blow-up must become a terminal ERROR chunk on the stream, never
    # an in-band raise (providers re-throw nothing in-band — §3.3).
    async def exploding_query(*, prompt, options):  # noqa: ARG001
        raise RuntimeError("connection reset")
        yield  # pragma: no cover  (makes this an async generator)

    provider = ClaudeAgentProvider(query_fn=exploding_query)
    q = ProviderQuery(model="claude-sonnet-4-6", allowed_tools=())
    got = [c async for c in provider.stream("x", q)]
    assert got[-1].type == "ERROR"
    assert "connection reset" in got[-1].metadata["message"]


# ---------------------------------------------------------------------------
# The SDK-isolation triad on every seam call + ThinkingConfig per D-022
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_build_sdk_options_pins_the_isolation_triad():
    from claude_agent_sdk import ClaudeAgentOptions

    q = ProviderQuery(model="claude-sonnet-4-6", allowed_tools=("speak", "read"))
    opts = build_sdk_options("prompt", q)
    assert isinstance(opts, ClaudeAgentOptions)
    # strict_mcp_config=True + setting_sources=[] is the two-part connector suppression.
    assert opts.strict_mcp_config is True
    assert opts.setting_sources == []
    # No host built-ins advertised; the world-touching block-list is disallowed.
    assert list(opts.tools) == []
    assert set(SDK_LOCAL_TOOLS).issubset(set(opts.disallowed_tools))
    # allowed_tools is the behavior's curated subset, never the union.
    assert set(opts.allowed_tools) == {"speak", "read"}
    assert opts.permission_mode == permission_mode


@pytest.mark.integration
def test_thinking_off_on_the_fast_path_and_on_for_reasoning_per_d022():
    from claude_agent_sdk import ClaudeAgentOptions  # noqa: F401

    # Fast path (Haiku gate / quick lookup): NO thinking preamble (latency-toxic).
    fast = ProviderQuery(model="claude-haiku-4-5", allowed_tools=("speak",), thinking_enabled=False)
    fopts = build_sdk_options("p", fast)
    # Disabled or unset — never an "enabled"/"adaptive" reasoning config on the fast path.
    assert fopts.thinking is None or fopts.thinking.get("type") == "disabled"

    # Reasoning turn (Opus build-planning / grounded answer): thinking ON, budget capped.
    reason = ProviderQuery(
        model="claude-opus-4-8", allowed_tools=("read",),
        thinking_enabled=True, thinking_budget_tokens=3000,
    )
    ropts = build_sdk_options("p", reason)
    assert ropts.thinking is not None
    assert ropts.thinking.get("type") in {"adaptive", "enabled"}


@pytest.mark.integration
def test_resume_and_model_flow_into_the_options():
    q = ProviderQuery(model="claude-sonnet-4-6", allowed_tools=(), resume="sess-prev")
    opts = build_sdk_options("p", q)
    assert opts.resume == "sess-prev"
    assert opts.model == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# The [CRITICAL] tripwire — a non-MCP built-in firing in sandbox mode
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_critical_tripwire_logs_on_host_builtin_in_sandbox_mode(caplog):
    tu = AgentChunk(type="TOOL_USE", metadata={"id": "x", "name": "Read", "input": {}})
    with caplog.at_level(logging.CRITICAL):
        fired = check_critical_tripwire(tu, sandbox_mode=True)
    assert fired is True
    assert any("[CRITICAL]" in r.getMessage() for r in caplog.records)


@pytest.mark.integration
def test_critical_tripwire_silent_for_mcp_tool_or_non_sandbox(caplog):
    mcp_tool = AgentChunk(type="TOOL_USE", metadata={"id": "x", "name": "get_dependents", "input": {}})
    host_tool = AgentChunk(type="TOOL_USE", metadata={"id": "y", "name": "Bash", "input": {}})
    with caplog.at_level(logging.CRITICAL):
        assert check_critical_tripwire(mcp_tool, sandbox_mode=True) is False  # curated MCP tool
        assert check_critical_tripwire(host_tool, sandbox_mode=False) is False  # not sandboxed
    assert not any("[CRITICAL]" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Env sanitization + stderr redaction (the seam's cross-cutting concerns)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_sanitize_env_strips_mutually_exclusive_auth_keys():
    # A leaked dev .env with BOTH api-key and oauth-token would make the SDK pick
    # the wrong auth path — the seam curates to ONE.
    raw = {
        "ANTHROPIC_API_KEY": "sk-ant-key",
        "ANTHROPIC_AUTH_TOKEN": "oauth-tok",
        "PATH": "/usr/bin",
        "RANDOM_DEV_VAR": "x",
    }
    curated = sanitize_env(raw)
    present = {k for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN") if k in curated}
    assert len(present) <= 1, "auth keys are mutually exclusive after curation"
    assert "PATH" in curated, "essential env survives"


@pytest.mark.integration
def test_redact_stderr_line_masks_sk_ant_bearer_and_token():
    assert "sk-ant-" not in redact_stderr_line("using key sk-ant-abc123DEF456 now")
    assert "Bearer secretvalue" not in redact_stderr_line("Authorization: Bearer secretvalue")
    red = redact_stderr_line("connecting token=deadbeefcafe host=x")
    assert "deadbeefcafe" not in red
    # A benign line is unchanged.
    assert redact_stderr_line("plain log line") == "plain log line"


# ---------------------------------------------------------------------------
# Registration wiring — pick_provider resolves a Claude provider
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_register_claude_provider_makes_pick_provider_resolve_claude():
    register_claude_provider()
    prov = pick_provider("claude-sonnet-4-6")
    assert isinstance(prov, Provider)
    # An unknown model still resolves to a provider (Claude is the registered default).
    assert isinstance(pick_provider("some-unregistered-model"), Provider)
