"""Meeting-control access — the ``meeting`` toolbelt the agent composes (N2).

Proxy drives its own in-meeting presence the same way it drives code-lookup: as
MCP tools it CHOOSES to call. This module mounts the real transport verbs —
mute/unmute itself, post to the meeting chat, DM a participant — as an
in-process ``claude_agent_sdk`` server under the ``meeting`` name, the exact
``create_sdk_mcp_server`` recipe premeeting's ``build_repo_context_server``
uses for ``code_intel``. The ENGINE never decides to mute (Law 4 — no
situation→action mapping lives in code); the AGENT decides, by calling a tool;
this module owns only the pipe.

Each tool is a thin wrapper over the REAL ``RecallTransport`` verbs (typed here
as a structural :class:`MeetingControlTransport` Protocol so this service stays
decoupled from the transport package, exactly as the Engine stays decoupled
from premeeting). Every handler is NEVER-THROW (Hard Rule 6): a vendor fault
returns an ``is_error`` tool result, never a raised exception. These are
REVERSIBLE in-meeting actions — no approval gate rides here (the human-click
staging gate is for irreversible/external actions only, Law 3).

Callers mount it the CODE-LOOKUP way: ``allowed_tools = CODE_TOOLS +
MEETING_TOOLS`` with ``mcp_servers={"code_intel": ..., "meeting":
build_meeting_control_server(transport, bot_id=...)}`` — the Engine just
threads what it's given.
"""
from __future__ import annotations

import json
from typing import Any, Protocol

from claude_agent_sdk import McpSdkServerConfig, create_sdk_mcp_server, tool

# The server name the fully-qualified ``mcp__meeting__*`` allowed_tools resolve against.
SERVER_NAME = "meeting"

# The meeting-control tool basenames, in the order the server advertises them.
TOOL_BASENAMES: tuple[str, ...] = ("mute", "unmute", "post_chat", "send_dm")

#: The fully-qualified tool names callers pass as ``allowed_tools`` (the
#: ``CODE_TOOLS`` pattern): ``mcp__<SERVER_NAME>__<basename>``.
MEETING_TOOLS: tuple[str, ...] = (
    "mcp__meeting__mute",
    "mcp__meeting__unmute",
    "mcp__meeting__post_chat",
    "mcp__meeting__send_dm",
)


class MeetingControlTransport(Protocol):
    """The meeting-control verbs this toolbelt wraps — the ``RecallTransport``
    shapes (N1), stated structurally so any conforming transport mounts."""

    async def mute(self, bot_id: str) -> None: ...

    async def unmute(self, bot_id: str) -> None: ...

    async def post_chat(self, bot_id: str, message: str, *, pinned: bool = False) -> None: ...

    async def send_dm(self, bot_id: str, message: str, participant_id: str) -> None: ...


def _text_result(payload: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload)}]}


def _error_result(msg: str) -> dict[str, Any]:
    """The never-throw boundary (Hard Rule 6): a tool fault returns an ``is_error`` result."""
    return {"is_error": True, "content": [{"type": "text", "text": msg}]}


def build_meeting_control_server(
    transport: MeetingControlTransport, *, bot_id: str
) -> McpSdkServerConfig:
    """Build the in-process meeting-control SDK server over ONE bot's transport verbs.

    ``bot_id`` is bound at build time — a server is built PER MEETING for THAT
    meeting's bot, so a tool call can never steer another meeting's bot. Every
    handler awaits the REAL transport method (whose round-trips ride the
    ``call_external`` seam inside the transport) and NEVER throws: a fault comes
    back as an ``is_error`` result the agent can hear about and speak honestly
    (Law 2)."""

    @tool("mute", "Mute yourself in the meeting (stops your audio output until you unmute).", {})
    async def mute(args: dict[str, Any]) -> dict[str, Any]:
        _ = args
        try:
            await transport.mute(bot_id)
            return _text_result({"muted": True})
        except Exception as exc:  # noqa: BLE001 - never-throw boundary
            return _error_result(f"mute error: {exc}")

    @tool("unmute", "Unmute yourself in the meeting (your audio output resumes).", {})
    async def unmute(args: dict[str, Any]) -> dict[str, Any]:
        _ = args
        try:
            await transport.unmute(bot_id)
            return _text_result({"muted": False})
        except Exception as exc:  # noqa: BLE001 - never-throw boundary
            return _error_result(f"unmute error: {exc}")

    @tool("post_chat", "Post a message to the meeting chat (visible to everyone).", {"message": str})
    async def post_chat(args: dict[str, Any]) -> dict[str, Any]:
        try:
            message = str(args.get("message") or "")
            if not message.strip():
                return _error_result("post_chat error: message is required")
            await transport.post_chat(bot_id, message)
            return _text_result({"posted": True})
        except Exception as exc:  # noqa: BLE001 - never-throw boundary
            return _error_result(f"post_chat error: {exc}")

    @tool(
        "send_dm",
        "Send a direct message to one meeting participant (by participant_id).",
        {"message": str, "participant_id": str},
    )
    async def send_dm(args: dict[str, Any]) -> dict[str, Any]:
        try:
            message = str(args.get("message") or "")
            participant_id = str(args.get("participant_id") or "")
            if not message.strip():
                return _error_result("send_dm error: message is required")
            if not participant_id.strip():
                # A DM with no recipient must NEVER fall through to a broadcast —
                # an empty ``to`` would leak a direct message to the whole meeting.
                return _error_result("send_dm error: participant_id is required")
            await transport.send_dm(bot_id, message, participant_id)
            return _text_result({"sent": True, "participant_id": participant_id})
        except Exception as exc:  # noqa: BLE001 - never-throw boundary
            return _error_result(f"send_dm error: {exc}")

    handlers = {"mute": mute, "unmute": unmute, "post_chat": post_chat, "send_dm": send_dm}
    return create_sdk_mcp_server(
        name=SERVER_NAME, version="1.0.0", tools=[handlers[n] for n in TOOL_BASENAMES]
    )


__all__ = [
    "MEETING_TOOLS",
    "SERVER_NAME",
    "TOOL_BASENAMES",
    "MeetingControlTransport",
    "build_meeting_control_server",
]
