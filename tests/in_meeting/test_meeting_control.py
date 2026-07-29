"""Acceptance battery for N2 — meeting-control access (the ``meeting`` toolbelt).

``in_meeting.meeting_control.build_meeting_control_server`` gives the agent
COMPOSABLE access to the in-meeting controls — mute/unmute itself, post to the
meeting chat, DM a participant — as MCP tools it chooses to call, mirroring the
grounded code-lookup pattern (``premeeting.RepoContext.build_server`` →
``mcp__code_intel__*``). The ENGINE never decides to mute; the AGENT does, by
calling a tool. Each tool is a thin wrapper over the REAL ``RecallTransport``
verbs (N1) and NEVER throws (Hard Rule 6): a vendor fault is an ``is_error``
result. These are reversible in-meeting actions — no approval gate.

Deterministic and offline: the transport is a FAKE that records calls (never a
real Recall round-trip); the tools are invoked through the REAL mcp
``CallToolRequest`` path, exactly as the SDK drives them. The four AC groups:

1. build → ``McpSdkServerConfig`` named ``meeting``; each tool calls the
   matching transport method with the bot_id/args (captured on the fake);
2. never-throw — a raising transport method becomes an ``is_error`` result;
3. ``MEETING_TOOLS`` names EXACTLY the four fully-qualified tools;
4. integration — an Engine mounted with the meeting server + MEETING_TOOLS
   delivers both onto the captured provider query (the CODE-LOOKUP pattern).
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from agentkit import ProviderQuery
from contracts import AgentChunk

from in_meeting.engine import Engine
from in_meeting.meeting_control import (
    MEETING_TOOLS,
    SERVER_NAME,
    TOOL_BASENAMES,
    build_meeting_control_server,
)
from in_meeting.notes import TranscriptLine

_BOT_ID = "b1"


async def _confirm_every_hit(text: str) -> bool:
    """The injected ASYNC disambiguation seam, scripted to confirm every name-hit."""
    return True


# ── fakes: a call-recording transport + a vendor-faulting transport ───────────


class FakeTransport:
    """Records every meeting-control call; the REAL RecallTransport verb shapes
    (mute/unmute/post_chat/send_dm) — never a wire round-trip."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def mute(self, bot_id: str) -> None:
        self.calls.append(("mute", {"bot_id": bot_id}))

    async def unmute(self, bot_id: str) -> None:
        self.calls.append(("unmute", {"bot_id": bot_id}))

    async def post_chat(self, bot_id: str, message: str, *, pinned: bool = False) -> None:
        self.calls.append(("post_chat", {"bot_id": bot_id, "message": message, "pinned": pinned}))

    async def send_dm(self, bot_id: str, message: str, participant_id: str) -> None:
        self.calls.append(
            ("send_dm", {"bot_id": bot_id, "message": message, "participant_id": participant_id})
        )


class RaisingTransport:
    """Every verb raises — the vendor-fault half of the never-throw boundary."""

    async def mute(self, bot_id: str) -> None:
        raise RuntimeError("recall api 502")

    async def unmute(self, bot_id: str) -> None:
        raise RuntimeError("recall api 502")

    async def post_chat(self, bot_id: str, message: str, *, pinned: bool = False) -> None:
        raise RuntimeError("recall api 502")

    async def send_dm(self, bot_id: str, message: str, participant_id: str) -> None:
        raise RuntimeError("recall api 502")


async def _call(server: Any, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Invoke a mounted tool through the REAL mcp CallToolRequest path (as the SDK drives it)."""
    import mcp.types as mt

    inst = server["instance"]
    handler = inst.request_handlers[mt.CallToolRequest]
    req = mt.CallToolRequest(
        method="tools/call", params=mt.CallToolRequestParams(name=tool_name, arguments=dict(args))
    )
    res = await handler(req)
    text = res.root.content[0].text
    if getattr(res.root, "isError", False):
        return {"__error__": text}
    return dict(json.loads(text))


async def _mounted_tool_names(server: Any) -> list[str]:
    """The tool names the server advertises, via the REAL ListToolsRequest path."""
    import mcp.types as mt

    inst = server["instance"]
    handler = inst.request_handlers[mt.ListToolsRequest]
    res = await handler(mt.ListToolsRequest(method="tools/list"))
    return [t.name for t in res.root.tools]


# ── AC1: build → sdk server config; each tool drives the matching transport verb ──


@pytest.mark.asyncio
async def test_build_returns_sdk_server_named_meeting_advertising_the_four_tools() -> None:
    server = build_meeting_control_server(FakeTransport(), bot_id=_BOT_ID)
    assert server["type"] == "sdk"
    assert server["name"] == SERVER_NAME == "meeting"
    assert await _mounted_tool_names(server) == list(TOOL_BASENAMES)


@pytest.mark.asyncio
async def test_mute_and_unmute_call_the_transport_with_the_bot_id() -> None:
    transport = FakeTransport()
    server = build_meeting_control_server(transport, bot_id=_BOT_ID)

    out = await _call(server, "mute", {})
    assert "__error__" not in out
    out = await _call(server, "unmute", {})
    assert "__error__" not in out

    assert transport.calls == [("mute", {"bot_id": _BOT_ID}), ("unmute", {"bot_id": _BOT_ID})]


@pytest.mark.asyncio
async def test_post_chat_broadcasts_the_message_to_the_meeting_chat() -> None:
    transport = FakeTransport()
    server = build_meeting_control_server(transport, bot_id=_BOT_ID)

    out = await _call(server, "post_chat", {"message": "PR draft is up for review"})
    assert "__error__" not in out
    assert transport.calls == [
        ("post_chat", {"bot_id": _BOT_ID, "message": "PR draft is up for review", "pinned": False})
    ]


@pytest.mark.asyncio
async def test_send_dm_targets_the_named_participant() -> None:
    transport = FakeTransport()
    server = build_meeting_control_server(transport, bot_id=_BOT_ID)

    out = await _call(server, "send_dm", {"message": "the branch name is fix/retry", "participant_id": "p-9"})
    assert "__error__" not in out
    assert transport.calls == [
        (
            "send_dm",
            {"bot_id": _BOT_ID, "message": "the branch name is fix/retry", "participant_id": "p-9"},
        )
    ]


@pytest.mark.asyncio
async def test_send_dm_without_a_participant_never_broadcasts() -> None:
    """A DM with no recipient must NOT fall through to a broadcast (an empty ``to``
    would leak a direct message to the whole meeting): it is an ``is_error`` result
    and the transport is never touched."""
    transport = FakeTransport()
    server = build_meeting_control_server(transport, bot_id=_BOT_ID)

    out = await _call(server, "send_dm", {"message": "secret branch name"})

    assert "__error__" in out
    assert transport.calls == []


# ── AC2: never-throw — a vendor fault is an is_error result, not an exception ──


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "args"),
    [
        ("mute", {}),
        ("unmute", {}),
        ("post_chat", {"message": "hello"}),
        ("send_dm", {"message": "hello", "participant_id": "p-1"}),
    ],
)
async def test_vendor_fault_returns_is_error_never_raises(tool_name: str, args: dict[str, Any]) -> None:
    server = build_meeting_control_server(RaisingTransport(), bot_id=_BOT_ID)

    out = await _call(server, tool_name, args)  # must not raise

    assert out.get("__error__") is not None
    assert "recall api 502" in out["__error__"]


# ── AC3: MEETING_TOOLS exact fully-qualified names ────────────────────────────


def test_meeting_tools_names_the_four_canonical_meeting_tools() -> None:
    assert MEETING_TOOLS == (
        "mcp__meeting__mute",
        "mcp__meeting__unmute",
        "mcp__meeting__post_chat",
        "mcp__meeting__send_dm",
    )


# ── AC4: integration — the Engine threads the meeting server + tools to the turn ──


class FakeProvider:
    """A scripted ``agentkit.Provider``: records every ``(prompt, query)`` call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ProviderQuery]] = []

    async def stream(self, prompt: str, query: ProviderQuery) -> AsyncIterator[AgentChunk]:
        self.calls.append((prompt, query))
        yield AgentChunk(type="TEXT", text="going quiet now", metadata={"msg_id": "m-1"})
        yield AgentChunk(type="RESULT", text="going quiet now", metadata={})


@pytest.mark.asyncio
async def test_engine_delivers_meeting_server_and_tools_onto_the_provider_query() -> None:
    """The turn that speaks is the turn that can actually drive the meeting controls:
    an Engine built with the meeting toolbelt threads BOTH the server config and the
    MEETING_TOOLS names onto the captured provider query (the CODE-LOOKUP pattern)."""
    server = build_meeting_control_server(FakeTransport(), bot_id=_BOT_ID)
    provider = FakeProvider()

    async def speak(_text: str) -> None:
        return None

    engine = Engine(
        provider=provider,
        model="claude-opus-4-6",
        allowed_tools=MEETING_TOOLS,
        speak=speak,
        disambiguate=_confirm_every_hit,
        mcp_servers={"meeting": server},
    )

    engagement = await engine.feed_transcript(
        TranscriptLine(text="Proxy, mute yourself for a minute.", speaker="Priya", timestamp=12.0, end_of_turn=True)
    )
    await engine.drain()

    assert engagement is not None
    assert len(provider.calls) == 1
    _, query = provider.calls[0]
    assert query.allowed_tools == MEETING_TOOLS
    assert query.mcp_servers == {"meeting": server}
