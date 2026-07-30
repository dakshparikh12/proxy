"""The in-meeting runtime entrypoint — assemble the Engine, drive it from the meeting (RUNTIME).

The Engine (``in_meeting.engine``) is the proven always-on loop, but on its own it is
never a runnable product: something has to hand it its REAL access and feed it the
meeting. This module is that integration spine — the future cutover target the old
harness boot path will call — and it does exactly two things:

* :func:`assemble_engine` wires the already-built pieces together: the pre-meeting
  ``index.md`` map loaded by pinned sha (MAP-LOAD), the grounded code toolbelt served
  off the tenant's clone (``premeeting.repo_context`` — the KEEP integration seam),
  the meeting-control toolbelt bound to THIS meeting's bot
  (``in_meeting.meeting_control``), and — when the caller passes them — the sandbox
  execution toolbelt (``in_meeting.sandbox``, off a provisioned handle) and the
  meeting-scoped draft-staging toolbelt (``in_meeting.drafts_access``, the Law-3
  write-to-the-world gate: propose stages, only the human accept route applies).
  Degradation is honest by construction: an unindexed repo / missing clone mounts no
  ``code_intel`` server and advertises no code tools, and no sandbox handle mounts
  no ``sandbox`` server and advertises no sandbox tools (the sim's caller-guard,
  mirrored on both) — Proxy still joins the meeting with its meeting-control access
  intact.
* :func:`run_meeting` drives the assembled Engine from the meeting's injected
  sources: each transcript line is fed to ``Engine.feed_transcript`` (idle is free —
  the trigger decides when Proxy wakes), and an optional chat source feeds
  ``Engine.feed_chat``.

Physics only (Law 4): this module ASSEMBLES access and PUMPS inputs — it makes no
situation→action decision and owns no capability choice; everything "Proxy does"
stays in the agent (the prime + its mounted access). The transcript SOURCE and the
SPEAK sink are INJECTED seams: the real webhook→``TranscriptLine`` adapter and the
real TTS speak sink are separate nodes that plug into them — none of that wiring
lives here. No ``services/harness`` import.
"""
from __future__ import annotations

from collections.abc import AsyncIterable
from pathlib import Path
from typing import Any

from agentkit import Provider
from claude_agent_sdk import McpSdkServerConfig
from premeeting.repo_context import RepoContext

from in_meeting.drafts_access import DRAFT_TOOLS
from in_meeting.engine import CODE_TOOLS, Engine, SpeakFn, SpeakSink
from in_meeting.map_loader import load_meeting_map
from in_meeting.meeting_control import (
    MEETING_TOOLS,
    MeetingControlTransport,
    build_meeting_control_server,
)
from in_meeting.notes import TranscriptLine
from in_meeting.prompt import PROXY_SYSTEM_PROMPT
from in_meeting.provider import EngineProvider
from in_meeting.sandbox import SANDBOX_TOOLS, build_sandbox_server
from in_meeting.trigger import ChatLine, Disambiguate


async def assemble_engine(
    *,
    model: str,
    tenant_id: str,
    repo: str,
    pinned_sha: str,
    bot_id: str,
    transport: MeetingControlTransport,
    conn: Any,
    clone_path: Path,
    speak: SpeakFn | SpeakSink,
    disambiguate: Disambiguate,
    provider: Provider | None = None,
    prime: str = PROXY_SYSTEM_PROMPT,
    sandbox: Any | None = None,
    drafts: McpSdkServerConfig | None = None,
) -> Engine:
    """Assemble ONE meeting's Engine with its full real access, honestly degraded.

    The map is loaded for the meeting's exact pinned ``(tenant_id, repo, pinned_sha)``
    key (never "latest"; ``None`` when unindexed — the Engine already runs prime-only,
    D-032). The code toolbelt is built off the tenant's clone via
    ``RepoContext.build_server()`` — ``None`` (no clone) mounts nothing, and the
    caller-guard mirrors the sim's: the ``mcp__code_intel__*`` names are only
    advertised when the server actually mounted, so the agent is never handed a tool
    name that can't resolve. The meeting-control server is bound to THIS meeting's
    ``bot_id`` at build time (one meeting's tools can never steer another's bot).

    ``sandbox`` is the ALREADY-PROVISIONED per-meeting E2B handle (warm-at-join) or
    ``None``: the caller provisions it and owns its lifetime (``kill()``); this
    function only MOUNTS it — ``build_sandbox_server`` binds the handle into the
    ``sandbox`` server and ``SANDBOX_TOOLS`` are advertised ONLY when it mounted
    (the same caller-guard as ``code_intel``, so names and servers never diverge).

    ``drafts`` is the ALREADY-BUILT meeting-scoped draft-staging server
    (``in_meeting.drafts_access.build_drafts_server`` — the boot path/provisioner
    builds it over the durable substrate and passes it in, exactly as it passes the
    provisioned ``sandbox`` handle) or ``None``: no server mounts no ``drafts``
    access and advertises no draft tools (the same caller-guard, mirrored on both) —
    Proxy then simply cannot stage a world-touching draft this meeting and must say
    so honestly (Law 2) rather than hold a tool name that can't resolve.

    ``conn`` is a borrowed asyncpg connection (the ``premeeting.map_store`` shape);
    ``speak``/``disambiguate``/``provider`` are the Engine's injected seams, threaded
    through unchanged (``provider=None`` = the real :class:`EngineProvider`).
    """
    map_text = await load_meeting_map(
        conn=conn, tenant_id=tenant_id, repo=repo, pinned_sha=pinned_sha
    )
    code_server: McpSdkServerConfig | None = RepoContext(
        clone_path=Path(clone_path), map_text=map_text, tenant_id=tenant_id
    ).build_server()
    meeting_server = build_meeting_control_server(transport, bot_id=bot_id)

    allowed_tools: tuple[str, ...] = (
        (CODE_TOOLS if code_server is not None else ())
        + MEETING_TOOLS
        + (SANDBOX_TOOLS if sandbox is not None else ())
        + (DRAFT_TOOLS if drafts is not None else ())
    )
    mcp_servers: dict[str, Any] = {"meeting": meeting_server}
    if code_server is not None:
        mcp_servers["code_intel"] = code_server
    if sandbox is not None:
        mcp_servers["sandbox"] = build_sandbox_server(sandbox)
    if drafts is not None:
        mcp_servers["drafts"] = drafts

    return Engine(
        model=model,
        allowed_tools=allowed_tools,
        speak=speak,
        disambiguate=disambiguate,
        map_text=map_text,
        mcp_servers=mcp_servers,
        prime=prime,
        provider=provider if provider is not None else EngineProvider(),
    )


async def run_meeting(
    engine: Engine,
    *,
    transcript_source: AsyncIterable[TranscriptLine],
    chat_source: AsyncIterable[ChatLine] | None = None,
) -> None:
    """Drive the assembled Engine from the meeting's injected sources until they end.

    Every transcript line is fed to :meth:`Engine.feed_transcript` — the trigger
    decides whether Proxy wakes (idle lines are free). NEVER BLOCKED (L7/W2 done):
    a wake turn runs as a background task inside the Engine, so the next line is
    pulled immediately — Proxy keeps listening while it works, and overlapping asks
    run as simultaneous turns, each isolated per ask. Turn COMPLETION order may
    differ from ask order (``engine.turns`` holds every result, completion-ordered).
    A turn fault never crashes this driver: the Engine's ``_wake_and_run`` absorbs
    provider errors into an honest ``TurnResult.error`` and NEVER raises (engine.py
    §9). When ``chat_source`` is given it is consumed the same way through
    :meth:`Engine.feed_chat` after the transcript source is exhausted. Once both
    sources end, :meth:`Engine.drain` awaits every in-flight turn — the driver
    returns only when all turns have finished.
    """
    async for line in transcript_source:
        await engine.feed_transcript(line)
    if chat_source is not None:
        async for msg in chat_source:
            await engine.feed_chat(msg)
    await engine.drain()


__all__ = ["assemble_engine", "run_meeting"]
