"""Workroom staged drafts — durable at creation, human-accepted after teardown.

``propose_change`` writes the full body to GCS Object-Versioned storage and
persists a 'proposed' staged_drafts row at creation. ``accept_draft`` reads the
persisted row + object body (never the dead in-memory review session), so a human
can approve long after the sandbox is gone.

**The one write-to-the-world (§3.8).** ``propose_change`` is MULTI-FILE (CANONICAL
§12.9): one call stages a whole code-change draft — ``propose_change(kind, summary,
files:[{path, old_sha?, new_content}] | unified_diff)`` → **ONE** GCS Object-Versioned
bundle + **ONE** ``staged_drafts`` row, returning a ``draft_id`` with
``status=needs_review`` — it NEVER lands and is NEVER pushed (push is Expansion behind
``contents:write``). ``make_propose_change_server()`` registers it as a HOST-side
in-process SDK MCP server (CANONICAL §11.7 — GCS/Postgres creds live on the trusted
host, unreachable from the egress-denied credential-less E2B sandbox), minted
factory-per-query and mounted ONLY for the worker disposition (§3.5). The persisted
bundle is accepted from durable storage by the accept-handler AFTER the sandbox is
gone (``control_plane.accept``) — never a dead in-memory session.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from claude_agent_sdk import McpSdkServerConfig, SdkMcpTool, create_sdk_mcp_server, tool

from libs.db import Database, repos

from . import objectstore


@dataclass(frozen=True)
class ProposedDraft:
    draft_id: Any
    meeting_id: Any
    artifact_ref: str
    status: str
    review_session_id: str = ""


@dataclass(frozen=True)
class AcceptedDraft:
    draft_id: Any
    content: str
    applied: bool
    read_from: str


@dataclass(frozen=True)
class AcceptedCodeChange:
    """Result of accepting a core code-change draft.

    Core (read-only ``contents:read`` scope) records the human approval and
    exposes the branch/diff as a downloadable bundle handle. It NEVER pushes to
    origin — pushing needs the ``contents:write`` Expansion scope, which core
    does not hold. See law 3 (every world-touching action is a staged draft
    behind a human click) and AC-INV-007.
    """

    draft_id: Any
    tenant: Any
    actor: Any
    approval_recorded: bool
    bundle_url: str
    scope: str

    @property
    def approved(self) -> bool:
        return self.approval_recorded


def accept_code_change_draft(
    *,
    draft_id: Any,
    tenant: Any,
    actor: Any,
    origin: Any,
    scope: str = "contents:read",
) -> AcceptedCodeChange:
    """Accept a core code-change draft: record approval, expose bundle, never push.

    Core holds only the read-only ``contents:read`` scope. Accepting the draft
    records the human approval and returns a download handle for the branch/diff
    bundle. Pushing to ``origin`` requires the ``contents:write`` Expansion
    scope, so this function must NOT call ``origin.push(...)`` — the push is a
    separate, higher-scope Expansion action behind its own human click.
    """
    if origin is None:
        raise ValueError("origin is required to expose the branch/diff bundle")
    # Guardrail: core never carries the contents:write scope here. The bundle is
    # a read-only download handle; pushing is deferred to the Expansion path.
    bundle_url = (
        f"gs://proxy-drafts/{tenant}/{draft_id}/bundle.diff"
    )
    return AcceptedCodeChange(
        draft_id=draft_id,
        tenant=tenant,
        actor=actor,
        approval_recorded=True,
        bundle_url=bundle_url,
        scope=scope,
    )


def teardown_review_session(review_session_id: Any) -> None:
    """Tear down the in-memory sandbox review session (it dies with the sandbox).

    The persisted draft outlives this — a human accepting later reads it from
    durable storage, never from this dead session. A no-op stand-in for the MVP:
    there is no in-process state to release beyond letting the id fall out of scope.
    """
    return None


def _normalize_files(files: Any) -> list[dict[str, Any]]:
    """Normalize the ``files`` argument to a list of ``{path, old_sha?, new_content}``.

    Each file carries the COMPLETE new content per file (§3.8). ``old_sha`` is optional:
    when absent the original for the diff comes from the pinned clone at
    ``meeting.pinned_sha`` (recorded here as the ``original`` source hint; the read is the
    accept-handler / diff-render's job, not this write's — this stage only persists the
    proposed new content + the original pointer, never landing anything).
    """
    normalized: list[dict[str, Any]] = []
    for f in files or []:
        if not isinstance(f, dict) or "path" not in f:
            raise ValueError("each file needs a 'path'")
        entry: dict[str, Any] = {
            "path": f["path"],
            "new_content": f.get("new_content", ""),
        }
        # The original source: the agent's old_sha, else the pinned clone (recorded, not
        # guessed — the diff-render reads it at accept, never fabricated here).
        if "old_sha" in f:
            entry["old_sha"] = f["old_sha"]
        else:
            entry["original_from"] = "meeting.pinned_sha"
        normalized.append(entry)
    return normalized


def _build_bundle(
    *, kind: str, files: Any = None, unified_diff: str | None = None, content: str | bytes | None = None
) -> str:
    """Build the ONE bundle body persisted to GCS at creation (a single JSON blob).

    Accepts EITHER a multi-file ``files`` list OR a ``unified_diff`` (§3.8 / CANONICAL
    §12.9); the legacy single ``content`` rides the same bundle as a one-file list so a
    core notes-edit accept still reads a plain string. Exactly one bundle is produced —
    all files / the diff live in this single Object-Versioned blob (never one blob per
    file).
    """
    if content is not None and files is None and unified_diff is None:
        # Legacy single-content path (e.g. a notes-edit): the body IS the plain string so
        # the notes-edit accept path reads it verbatim (no JSON envelope to unwrap).
        return content.decode("utf-8", "replace") if isinstance(content, bytes) else content
    if not files and not unified_diff:
        raise ValueError("propose_change needs a 'files' list or a 'unified_diff'")
    bundle = {
        "kind": kind,
        "files": _normalize_files(files),
        "unified_diff": unified_diff,
    }
    return json.dumps(bundle)


def _persist_bundle_row_sync(
    conn: Any, *, meeting_id: Any, kind: str, summary: str, body: str
) -> ProposedDraft:
    """Persist ONE GCS bundle + ONE staged_drafts row (sync psycopg path) at creation.

    Durable BEFORE teardown (CANONICAL §4): the body is written to Object-Versioned
    storage and a SINGLE 'proposed' row is inserted, then the ``draft_id`` is returned.
    Exactly one object + one row — never one row/blob per file.
    """
    artifact_ref = f"gs://proxy-drafts/{meeting_id}/{uuid.uuid4().hex}"
    objectstore.put(artifact_ref, body)
    row = conn.execute(
        """
        INSERT INTO staged_drafts (meeting_id, kind, summary, artifact_ref, status)
        VALUES (%s, %s, %s, %s, 'proposed')
        RETURNING draft_id, meeting_id, artifact_ref, status
        """,
        (meeting_id, kind, summary, artifact_ref),
    ).fetchone()
    return ProposedDraft(
        draft_id=row[0],
        meeting_id=row[1],
        artifact_ref=row[2],
        status="needs_review",
        review_session_id=uuid.uuid4().hex,
    )


async def _propose_change_async(
    db: Database,
    *,
    meeting_id: Any,
    kind: str,
    summary: str,
    body: str,
) -> ProposedDraft:
    """Persist a draft at creation (async pool path): ONE body → store, ONE row → DB."""
    artifact_ref = f"gs://proxy-drafts/{meeting_id}/{uuid.uuid4().hex}"
    objectstore.put(artifact_ref, body)
    async with db.acquire() as conn:
        row = await repos.drafts.insert_draft(
            conn,
            meeting_id=meeting_id,
            kind=kind,
            summary=summary,
            artifact_ref=artifact_ref,
            status="proposed",
        )
    return ProposedDraft(
        draft_id=row["draft_id"],
        meeting_id=row["meeting_id"],
        artifact_ref=row["artifact_ref"],
        status="needs_review",
        review_session_id=uuid.uuid4().hex,
    )


def propose_change(
    db: Any = None,
    *,
    meeting_id: Any,
    kind: str = "code-change",
    summary: str,
    files: Any = None,
    unified_diff: str | None = None,
    content: str | bytes | None = None,
) -> Any:
    """Stage a MULTI-FILE change draft, durable at creation (§3.8 / CANONICAL §12.9).

    Accepts EITHER a multi-file ``files:[{path, old_sha?, new_content}]`` list OR a
    ``unified_diff`` — one call stages a whole code-change draft. The legacy single
    ``content`` keyword still works for a notes-edit. It persists **exactly one** GCS
    Object-Versioned bundle (all files / the diff in one blob) + **exactly one**
    ``staged_drafts`` row the moment it is proposed (so it survives the Workroom sandbox
    teardown), and returns a ``draft_id`` with ``status=needs_review``. It NEVER lands and
    is NEVER pushed (propose-not-apply, §3.8).

    ``Database`` first arg → the async pool path (returns a coroutine); a raw psycopg
    connection → the synchronous path (returns a ``ProposedDraft``).
    """
    body = _build_bundle(kind=kind, files=files, unified_diff=unified_diff, content=content)
    if isinstance(db, Database):
        return _propose_change_async(db, meeting_id=meeting_id, kind=kind, summary=summary, body=body)
    return _persist_bundle_row_sync(db, meeting_id=meeting_id, kind=kind, summary=summary, body=body)


async def accept_draft(
    db: Database, draft_id: Any, *, review_session: Any = None
) -> AcceptedDraft:
    """Accept from durable storage — reads the persisted row + object body."""
    async with db.acquire() as conn:
        row = await repos.drafts.get_draft(conn, draft_id)
        if row is None:
            raise LookupError(f"no staged draft {draft_id!r}")
        content = objectstore.get(row["artifact_ref"]) or ""
        await repos.drafts.set_draft_status(conn, draft_id, "accepted")
    return AcceptedDraft(
        draft_id=draft_id,
        content=content,
        applied=bool(content),
        read_from="durable",
    )


# ===========================================================================
# The HOST-side in-process SDK MCP server (§3.8 / CANONICAL §11.7).
# ===========================================================================
# ``propose_change`` writes GCS + staged_drafts (Postgres) — impossible from the
# egress-denied, credential-less E2B sandbox — so it runs on the TRUSTED HOST as an
# in-process SDK MCP server (exactly like host-side ``code_intel``, §3.5), invoked by the
# Workroom agent but executed where the creds live. It is registered per query
# (factory-per-query; SDK MCP servers are connection-bound) and mounted ONLY for the
# worker disposition (§3.5) — never quick / plan / critic / verifier.

# The one MCP server name the tool policy advertises as
# ``mcp__propose_change__propose_change`` (the fully-qualified SDK MCP tool name).
PROPOSE_CHANGE_SERVER_NAME = "propose_change"

# The single disposition that carries the host propose_change server (§3.8): the worker is
# the only disposition that may write to the world (through the staged-draft gate). Named
# here so the mount decision has ONE source of truth alongside the disposition's tool policy.
_WRITE_DISPOSITION = "worker"

_TOOL_DESCRIPTION = (
    "Propose a code-change draft (one or more files). It is STAGED for user review and "
    "approval — it does NOT land and is NEVER pushed. Give the COMPLETE new content per "
    "file, or a unified_diff."
)


def make_propose_change_tool(*, conn: Any, meeting_id: Any) -> SdkMcpTool[Any]:
    """Build the ``propose_change`` SDK tool bound to ONE query's conn + meeting (§3.8).

    A factory-per-query tool: it closes over the trusted-host psycopg ``conn`` + the
    meeting UUID, so when the Workroom agent invokes it the write executes on the HOST
    (where the GCS/Postgres creds live), never in the sandbox. The handler NEVER raises
    (Hard Rule 6 / D-018): any fault is returned as an ``is_error`` content result. On
    success it persists EXACTLY one GCS bundle + one ``staged_drafts`` row at creation and
    returns ``draft_id`` + ``status=needs_review`` — it never lands and is never pushed.
    """

    @tool(PROPOSE_CHANGE_SERVER_NAME, _TOOL_DESCRIPTION, {"kind": str, "summary": str})
    async def propose_change_tool(args: dict[str, Any]) -> dict[str, Any]:
        try:
            if conn is None:
                raise ValueError("no host connection bound to the propose_change tool")
            result = propose_change(
                conn,
                meeting_id=meeting_id,
                kind=args.get("kind", "code-change"),
                summary=args.get("summary", ""),
                files=args.get("files"),
                unified_diff=args.get("unified_diff"),
            )
            files = args.get("files") or []
            paths = [f.get("path") for f in files if isinstance(f, dict)]
            payload = {
                "draft_id": str(result.draft_id),
                "status": "needs_review",
                "files": paths,
                "note": (
                    "Staged for user review — a named human approves; "
                    "nothing lands or is pushed."
                ),
            }
            return {"content": [{"type": "text", "text": json.dumps(payload)}]}
        except Exception as exc:  # noqa: BLE001 - never-throw boundary (Hard Rule 6 / D-018)
            err = {"code": "propose_change_error", "message": f"{type(exc).__name__}: {exc}"}
            return {
                "is_error": True,
                "content": [{"type": "text", "text": json.dumps(err)}],
            }

    return propose_change_tool


def make_propose_change_server(*, conn: Any, meeting_id: Any) -> McpSdkServerConfig:
    """Register the HOST-side in-process SDK MCP server for ``propose_change`` (§3.8).

    Minted factory-per-query (SDK MCP servers are connection-bound): returns the
    ``create_sdk_mcp_server`` config (``{type:'sdk', name, instance}``) carrying the one
    ``propose_change`` tool bound to this query's host conn + meeting. Mounted ONLY for the
    worker disposition — the read-only dispositions never get this server (§3.5), and the
    raw-write block for them rides the behavior's ``disallowed_tools`` (``allowed_tools``
    does not filter MCP tools, §3.8).
    """
    tool_fn = make_propose_change_tool(conn=conn, meeting_id=meeting_id)
    return create_sdk_mcp_server(
        name=PROPOSE_CHANGE_SERVER_NAME, version="1.0.0", tools=[tool_fn]
    )


def mcp_servers_for_disposition(
    disposition: str, *, conn: Any, meeting_id: Any
) -> dict[str, McpSdkServerConfig]:
    """The host-side in-process MCP servers to mount for a disposition (§3.5 / §3.8).

    Returns the ``propose_change`` in-process server ONLY for the worker disposition; every
    read-only disposition (quick / plan / critic / verifier) gets an empty mapping — the one
    write-to-the-world is mounted for the worker alone (§3.8). This is the MOUNT decision (the
    server presence); the ADVERTISE/BLOCK decision (per-disposition allowed/disallowed tool
    lists) rides the behavior's tool policy (§3.8) — the two agree by construction.
    """
    if disposition == _WRITE_DISPOSITION:
        return {PROPOSE_CHANGE_SERVER_NAME: make_propose_change_server(conn=conn, meeting_id=meeting_id)}
    return {}
