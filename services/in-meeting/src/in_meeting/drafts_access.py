"""Draft-staging access — the ``drafts`` toolbelt the agent composes (Law 3, DRAFT-TOOL).

Anything irreversible/world-touching — a change applied, a PR opened — is NEVER done
directly: it is STAGED AS A DRAFT behind a human click (the prime's hard gate). This
module gives the agent the pipe for that: ``mcp__drafts__propose_change``, an
in-process ``claude_agent_sdk`` server under the ``drafts`` name — the exact
``create_sdk_mcp_server`` recipe ``meeting_control`` and ``sandbox`` use. The tool
stages a draft via the REAL staging machinery (``workroom.drafts.propose_change`` —
ONE durable object-store bundle + ONE ``staged_drafts`` row, reused verbatim, never a
second draft store) bound to THIS meeting, and returns the ``draft_id`` + the approve
URL (the control-plane accept route's path). Apply fires ONLY via that accept route,
behind the human click — never from here.

Composition, not hard-coding (Law 4): the handler NEVER auto-posts an approve card.
The AGENT decides to share the returned approve URL in chat via its own chat tool —
the ENGINE and this pipe own no situation→action mapping.

Meeting binding: the server is built PER MEETING with ``meeting_id`` closed over at
build time, so a staged draft can never land in another meeting — the same
bind-at-build isolation as ``meeting_control`` (bot_id) and ``sandbox`` (handle).

Substrate shapes: the staging machinery binds EITHER the async ``libs.db.Database``
pool facade (the provisioner's boot-path handle — a per-call acquire, safe for the
meeting's whole lifetime) OR a raw sync psycopg connection (the durable post-teardown
shape the accept side already runs on). Anything else cannot stage, and
:func:`build_drafts_server` returns ``None`` — the ``RepoContext.build_server`` honest
caller-guard, so the agent is never handed a tool name that can't succeed.

Every handler is NEVER-THROW (Hard Rule 6): a staging fault returns an ``is_error``
result the agent can hear about and speak plainly (Law 2), never a raised exception.

Callers mount it the CODE-LOOKUP way: ``allowed_tools = ... + DRAFT_TOOLS`` with
``mcp_servers={..., "drafts": build_drafts_server(db=..., meeting_id=...)}`` — the
Engine just threads what it's given.
"""
from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable
from typing import Any

from claude_agent_sdk import McpSdkServerConfig, create_sdk_mcp_server, tool

# The server name the fully-qualified ``mcp__drafts__*`` allowed_tools resolve against.
SERVER_NAME = "drafts"

# The draft-staging tool basenames, in the order the server advertises them.
TOOL_BASENAMES: tuple[str, ...] = ("propose_change",)

#: The fully-qualified tool names callers pass as ``allowed_tools`` (the
#: ``CODE_TOOLS``/``MEETING_TOOLS``/``SANDBOX_TOOLS`` pattern).
DRAFT_TOOLS: tuple[str, ...] = ("mcp__drafts__propose_change",)

#: The approve URL's path shape — the control-plane accept route
#: (``control_plane.accept_route.ACCEPT_PATH``). Stated here because this service
#: never imports control_plane (the dependency runs the other way); the acceptance
#: battery cross-checks the two constants so they can never drift.
APPROVE_PATH_TEMPLATE = "/m/{meeting_id}/drafts/{draft_id}/accept"

#: The injectable staging seam: an async callable with the REAL machinery's keyword
#: surface — ``stage(db, meeting_id=..., kind=..., summary=..., files=...,
#: unified_diff=...)`` returning the staged draft (a ``draft_id``-bearing result).
#: Tests inject a recording fake; the default is the real workroom staging machinery.
StageFn = Callable[..., Awaitable[Any]]

_TOOL_DESCRIPTION = (
    "Stage a change draft for human approval: one or more files (COMPLETE new content "
    "per file) or a unified_diff, plus a one-line summary. The draft is STAGED ONLY — "
    "nothing lands, nothing is pushed, until a human clicks approve. Returns the "
    "draft_id and the approve_url; share the approve_url in the meeting chat so a "
    "person can review and click it."
)

# A FULL JSON Schema (the SDK passes a {"type","properties"} dict through verbatim):
# only ``summary`` is required at the validation layer — ``kind`` defaults in the
# handler and the files-OR-unified_diff rule is the handler's honest guard (a plain
# ``is_error`` message, never an opaque validation throw).
_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {
            "type": "string",
            "description": "The draft kind (defaults to 'code-change').",
        },
        "summary": {
            "type": "string",
            "description": "A one-line summary of the proposed change.",
        },
        "files": {
            "type": "array",
            "description": "The changed files, each with its COMPLETE new content.",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_sha": {"type": "string"},
                    "new_content": {"type": "string"},
                },
                "required": ["path", "new_content"],
            },
        },
        "unified_diff": {
            "type": "string",
            "description": "A unified diff of the change, instead of a files list.",
        },
    },
    "required": ["summary"],
}


def approve_path(meeting_id: Any, draft_id: Any) -> str:
    """The approve URL for one staged draft — the accept route's path, filled in."""
    return APPROVE_PATH_TEMPLATE.format(meeting_id=meeting_id, draft_id=draft_id)


def _text_result(payload: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload)}]}


def _error_result(msg: str) -> dict[str, Any]:
    """The never-throw boundary (Hard Rule 6): a tool fault returns an ``is_error`` result."""
    return {"is_error": True, "content": [{"type": "text", "text": msg}]}


def _can_stage(db: Any) -> bool:
    """Whether the REAL staging machinery can bind this substrate handle.

    True for the async ``libs.db.Database`` pool facade (the boot-path shape) and for
    a raw sync connection exposing ``execute`` (the durable psycopg shape). Anything
    else — a test stand-in, a missing handle — cannot persist a draft, so the caller
    mounts NO server (honest degradation; the tool policy stays truthful).
    """
    from libs.db import Database

    if isinstance(db, Database):
        return True
    return callable(getattr(db, "execute", None))


async def _stage_via_store(db: Any, **kwargs: Any) -> Any:
    """The DEFAULT staging seam: the REAL machinery, reused verbatim.

    ``workroom.drafts.propose_change`` persists exactly ONE durable object-store
    bundle + ONE ``staged_drafts`` row and never lands or pushes anything. It returns
    a coroutine for the async ``Database`` facade and a plain result for a sync
    connection; both are normalised here. Imported lazily (the ``sandbox`` seam
    recipe) so mounting against an injected fake never drags the staging home's
    transitive deps into the hot import path.
    """
    from workroom.drafts import propose_change

    result = propose_change(db, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def build_drafts_server(
    *, db: Any, meeting_id: Any, stage: StageFn | None = None
) -> McpSdkServerConfig | None:
    """Build the in-process draft-staging SDK server bound to ONE meeting.

    ``meeting_id`` is bound at build time — a server is built PER MEETING, so a
    staged draft can never land in another meeting. ``db`` is the durable substrate
    the staging machinery binds (the async pool facade or a sync connection); a
    substrate that cannot stage returns ``None`` and the caller mounts nothing (the
    same caller-guard honesty as ``code_intel``/``sandbox`` — advertised names and
    mounted servers never diverge). ``stage`` is the injectable staging seam (tests
    pass a recording fake; the default is the real machinery).

    The handler NEVER throws (Hard Rule 6) and NEVER posts to chat itself: it returns
    the ``draft_id`` + ``approve_url`` for the AGENT to compose its own approve card
    with (Law 4 — the situation→action choice stays in model judgment).
    """
    if stage is None and not _can_stage(db):
        return None
    stage_fn: StageFn = stage if stage is not None else _stage_via_store

    @tool("propose_change", _TOOL_DESCRIPTION, _TOOL_SCHEMA)
    async def propose_change_tool(args: dict[str, Any]) -> dict[str, Any]:
        try:
            summary = str(args.get("summary") or "")
            if not summary.strip():
                return _error_result("propose_change error: summary is required")
            files = args.get("files")
            unified_diff = args.get("unified_diff")
            if not files and not unified_diff:
                return _error_result(
                    "propose_change error: a files list or a unified_diff is required"
                )
            result = await stage_fn(
                db,
                meeting_id=meeting_id,
                kind=str(args.get("kind") or "code-change"),
                summary=summary,
                files=files,
                unified_diff=unified_diff,
            )
            draft_id = getattr(result, "draft_id", None)
            if draft_id is None:
                return _error_result("propose_change error: staging returned no draft id")
            payload = {
                "draft_id": str(draft_id),
                "meeting_id": str(meeting_id),
                "status": "needs_review",
                "approve_url": approve_path(meeting_id, draft_id),
                "note": (
                    "Staged as a draft — nothing lands or is pushed until a human "
                    "approves it. Share the approve_url in the meeting chat for a "
                    "person to review and click."
                ),
            }
            return _text_result(payload)
        except Exception as exc:  # noqa: BLE001 - never-throw boundary (Hard Rule 6)
            return _error_result(f"propose_change error: {exc}")

    handlers = {"propose_change": propose_change_tool}
    return create_sdk_mcp_server(
        name=SERVER_NAME, version="1.0.0", tools=[handlers[n] for n in TOOL_BASENAMES]
    )


__all__ = [
    "APPROVE_PATH_TEMPLATE",
    "DRAFT_TOOLS",
    "SERVER_NAME",
    "TOOL_BASENAMES",
    "StageFn",
    "approve_path",
    "build_drafts_server",
]
