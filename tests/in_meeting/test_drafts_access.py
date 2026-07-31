"""Acceptance battery for DRAFT-TOOL — the world-touching draft→approve arc (Law 3).

The prompt tells Proxy that anything world-touching is staged as a draft behind a
human click, and the control-plane accept route exists — but the engine mounted NO
tool that can create a draft. ``in_meeting.drafts_access`` closes that gap the same
access-pattern way as code/meeting/sandbox: an in-process ``drafts`` SDK MCP server
whose ONE tool, ``propose_change``, stages a draft via the REAL staging machinery
(one durable bundle + one ``staged_drafts`` row) bound to THIS meeting, and returns
the draft id + the approve URL (the accept route path). The agent then posts the
approve card itself via its chat tool — composition, never an auto-post.

Deterministic and offline: the staging seam is either a recording fake or the REAL
machinery over a recording sync substrate (never a live GCS/PG wire here — the
PG-gated full arc lives in ``tests/doc08/test_draft_tool_full_arc.py``); the tools
are invoked through the REAL mcp ``CallToolRequest`` path, exactly as the SDK
drives them. The AC groups:

1. dispatch + meeting binding — ``propose_change`` driven through the real MCP
   dispatch stages a draft bound to the EXACT meeting (captured call / captured
   INSERT) and returns ``draft_id`` + ``approve_url`` + ``status=needs_review``;
2. never-throw — a raising staging seam / substrate fault is an ``is_error``
   result, never a raised exception; missing args never touch the seam;
3. ``DRAFT_TOOLS`` exact fully-qualified names + the ``drafts`` server shape;
4. engine integration — ``assemble_engine(drafts=...)`` advertises DRAFT_TOOLS and
   carries the ``drafts`` server on the captured provider query (caller-guard:
   ``drafts=None`` mounts nothing — pinned by the existing runtime battery);
5. approve URL — the tool's approve path IS the mounted accept route's path shape
   (cross-checked against ``control_plane.accept_route.ACCEPT_PATH``);
6. provisioner assembly — the REAL boot path (``provisioner._assemble_engine``)
   passes a meeting-scoped drafts server over the durable substrate, and the
   mounted tool's staged row binds to THAT meeting (cutover test pattern).
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from agentkit import ProviderQuery
from contracts import AgentChunk

from in_meeting.drafts_access import (
    APPROVE_PATH_TEMPLATE,
    DRAFT_TOOLS,
    SERVER_NAME,
    TOOL_BASENAMES,
    approve_path,
    build_drafts_server,
)
from in_meeting.meeting_control import MEETING_TOOLS
from in_meeting.notes import TranscriptLine
from in_meeting.sandbox import SANDBOX_TOOLS

_FILES = [{"path": "libs/http/client.py", "new_content": "RETRIES = 5\n"}]


# ── the real in-process MCP dispatch (exactly as the SDK drives a mounted server) ──


async def _call(server: Any, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Invoke a mounted tool through the REAL mcp CallToolRequest path."""
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
    import mcp.types as mt

    inst = server["instance"]
    handler = inst.request_handlers[mt.ListToolsRequest]
    res = await handler(mt.ListToolsRequest(method="tools/list"))
    return [t.name for t in res.root.tools]


# ── fakes: a recording staging seam + recording/raising sync substrates ──────────


class _StagedResult:
    """The ``ProposedDraft`` shape the staging machinery returns."""

    def __init__(self, draft_id: str, meeting_id: str) -> None:
        self.draft_id = draft_id
        self.meeting_id = meeting_id
        self.status = "needs_review"


class RecordingStage:
    """A recording staging seam (the injectable ``stage`` kwarg)."""

    def __init__(self, draft_id: str = "d-1") -> None:
        self.draft_id = draft_id
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, db: Any, **kwargs: Any) -> _StagedResult:
        self.calls.append({"db": db, **kwargs})
        return _StagedResult(self.draft_id, str(kwargs.get("meeting_id")))


class RaisingStage:
    """The staging seam raising — the vendor-fault half of never-throw."""

    async def __call__(self, db: Any, **kwargs: Any) -> Any:
        raise RuntimeError("gcs 503")


class _Cursor:
    def __init__(self, row: Any) -> None:
        self._row = row

    def fetchone(self) -> Any:
        return self._row


class RecordingSyncConn:
    """A psycopg-shaped sync substrate: records every execute, returns the staged row
    for the ``staged_drafts`` INSERT — the REAL (sync-path) machinery runs verbatim."""

    def __init__(self, draft_id: str = "draft-77") -> None:
        self.draft_id = draft_id
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, sql: str, params: Any = None) -> _Cursor:
        text = " ".join(sql.split())
        p = tuple(params or ())
        self.executed.append((text, p))
        if "INSERT INTO staged_drafts" in text:
            # RETURNING draft_id, meeting_id, artifact_ref, status
            return _Cursor((self.draft_id, p[0], p[3], "proposed"))
        return _Cursor(None)


class RaisingSyncConn:
    """A substrate whose write faults — the machinery-level never-throw half."""

    def execute(self, sql: str, params: Any = None) -> _Cursor:
        raise RuntimeError("pg down")


# ── AC3: the server shape + DRAFT_TOOLS exact names ──────────────────────────────


def test_draft_tools_names_the_one_canonical_propose_tool() -> None:
    assert DRAFT_TOOLS == ("mcp__drafts__propose_change",)
    assert SERVER_NAME == "drafts"
    assert TOOL_BASENAMES == ("propose_change",)


@pytest.mark.asyncio
async def test_build_returns_sdk_server_named_drafts_advertising_propose_change() -> None:
    server = build_drafts_server(db=object(), meeting_id="m-1", stage=RecordingStage())
    assert server is not None
    assert server["type"] == "sdk"
    assert server["name"] == SERVER_NAME == "drafts"
    assert await _mounted_tool_names(server) == list(TOOL_BASENAMES)


def test_a_substrate_that_cannot_stage_mounts_no_server() -> None:
    """The caller-guard honesty (the ``RepoContext.build_server`` recipe): a substrate
    the staging machinery cannot bind to mounts NOTHING — the agent is never handed
    a tool name that can't succeed structurally."""
    assert build_drafts_server(db=object(), meeting_id="m-1") is None


# ── AC1: real dispatch stages a draft bound to THIS meeting, returns id + URL ────


@pytest.mark.asyncio
async def test_propose_change_stages_bound_to_the_meeting_and_returns_id_and_approve_url() -> None:
    stage = RecordingStage(draft_id="d-42")
    db = object()
    server = build_drafts_server(db=db, meeting_id="m-9", stage=stage)
    assert server is not None

    out = await _call(
        server,
        "propose_change",
        {"kind": "code-change", "summary": "raise the retry cap", "files": _FILES},
    )

    assert "__error__" not in out
    # The staging seam was driven ONCE with the bound substrate + the bound meeting —
    # a draft can never land in another meeting.
    assert len(stage.calls) == 1
    call = stage.calls[0]
    assert call["db"] is db
    assert call["meeting_id"] == "m-9"
    assert call["kind"] == "code-change"
    assert call["summary"] == "raise the retry cap"
    assert call["files"] == _FILES
    # The agent gets the id + the approve URL to compose its own approve card with.
    assert out["draft_id"] == "d-42"
    assert out["meeting_id"] == "m-9"
    assert out["status"] == "needs_review"
    assert out["approve_url"] == "/m/m-9/drafts/d-42/accept"


@pytest.mark.asyncio
async def test_propose_change_runs_the_real_machinery_on_a_sync_substrate() -> None:
    """The DEFAULT staging path (no injected seam) drives the REAL machinery: one
    durable bundle written + ONE ``staged_drafts`` INSERT carrying the bound
    meeting_id — captured on a recording psycopg-shaped substrate."""
    from workroom import objectstore

    conn = RecordingSyncConn(draft_id="draft-77")
    server = build_drafts_server(db=conn, meeting_id="m-77")
    assert server is not None

    out = await _call(
        server,
        "propose_change",
        {"kind": "code-change", "summary": "raise the retry cap", "files": _FILES},
    )

    assert "__error__" not in out
    inserts = [(sql, p) for sql, p in conn.executed if "INSERT INTO staged_drafts" in sql]
    assert len(inserts) == 1, "exactly ONE staged_drafts row per propose"
    _, params = inserts[0]
    assert params[0] == "m-77", "the staged row must bind to THIS meeting"
    assert params[1] == "code-change"
    # The ONE durable bundle really exists at the persisted artifact_ref.
    artifact_ref = params[3]
    body = objectstore.get(artifact_ref)
    assert body is not None
    bundle = json.loads(body)
    assert bundle["files"][0]["path"] == "libs/http/client.py"
    assert bundle["files"][0]["new_content"] == "RETRIES = 5\n"
    # And the agent got the real row's id + the approve URL.
    assert out["draft_id"] == "draft-77"
    assert out["status"] == "needs_review"
    assert out["approve_url"] == "/m/m-77/drafts/draft-77/accept"


@pytest.mark.asyncio
async def test_propose_change_accepts_a_unified_diff_instead_of_files() -> None:
    stage = RecordingStage()
    server = build_drafts_server(db=object(), meeting_id="m-9", stage=stage)
    assert server is not None

    out = await _call(
        server,
        "propose_change",
        {"summary": "fix off-by-one", "unified_diff": "--- a/x.py\n+++ b/x.py\n"},
    )

    assert "__error__" not in out
    assert stage.calls[0]["unified_diff"] == "--- a/x.py\n+++ b/x.py\n"


# ── AC2: never-throw — seam faults and missing args are is_error results ─────────


@pytest.mark.asyncio
async def test_staging_seam_fault_returns_is_error_never_raises() -> None:
    server = build_drafts_server(db=object(), meeting_id="m-9", stage=RaisingStage())
    assert server is not None

    out = await _call(
        server, "propose_change", {"summary": "s", "files": _FILES}
    )  # must not raise

    assert out.get("__error__") is not None
    assert "gcs 503" in out["__error__"]


@pytest.mark.asyncio
async def test_real_machinery_substrate_fault_returns_is_error_never_raises() -> None:
    server = build_drafts_server(db=RaisingSyncConn(), meeting_id="m-9")
    assert server is not None

    out = await _call(server, "propose_change", {"summary": "s", "files": _FILES})

    assert out.get("__error__") is not None
    assert "pg down" in out["__error__"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args",
    [
        {"files": [{"path": "a.py", "new_content": "x"}]},  # no summary
        {"summary": ""},  # blank summary
        {"summary": "s"},  # neither files nor unified_diff
    ],
)
async def test_missing_required_args_are_an_error_and_never_touch_the_seam(
    args: dict[str, Any],
) -> None:
    stage = RecordingStage()
    server = build_drafts_server(db=object(), meeting_id="m-9", stage=stage)
    assert server is not None

    out = await _call(server, "propose_change", args)

    assert "__error__" in out
    assert stage.calls == []


# ── AC5: the approve URL is the accept route's path (cross-checked, never drifts) ──


def test_approve_path_template_is_the_mounted_accept_route_path() -> None:
    from control_plane.accept_route import ACCEPT_PATH

    assert APPROVE_PATH_TEMPLATE == ACCEPT_PATH
    assert approve_path("m-1", "d-2") == "/m/m-1/drafts/d-2/accept"


# ── AC4: engine integration — assembled engine carries the drafts server + tools ──


def _happy_turn(answer: str) -> list[AgentChunk]:
    return [
        AgentChunk(type="INIT", text=None, metadata={"session_id": "s-1", "tools": [], "mcp_servers": []}),
        AgentChunk(type="TEXT", text=answer, metadata={"msg_id": "m-1"}),
        AgentChunk(type="RESULT", text=answer, metadata={"session_id": "s-1", "total_cost_usd": 0.01}),
    ]


class FakeProvider:
    """A scripted ``agentkit.Provider``: records every (prompt, query) call."""

    def __init__(self, turns: Sequence[Sequence[AgentChunk]] | None = None) -> None:
        self.calls: list[tuple[str, ProviderQuery]] = []
        self._turns: list[list[AgentChunk]] = [
            list(t) for t in (turns or [_happy_turn("on it, staged")])
        ]

    async def stream(self, prompt: str, query: ProviderQuery) -> AsyncIterator[AgentChunk]:
        self.calls.append((prompt, query))
        script = self._turns[min(len(self.calls) - 1, len(self._turns) - 1)]
        for chunk in script:
            yield chunk


class FakeTransport:
    """The MeetingControlTransport verbs as inert recorders."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def mute(self, bot_id: str) -> None:
        self.calls.append(f"mute:{bot_id}")

    async def unmute(self, bot_id: str) -> None:
        self.calls.append(f"unmute:{bot_id}")

    async def post_chat(self, bot_id: str, message: str, *, pinned: bool = False) -> None:
        self.calls.append(f"post_chat:{bot_id}")

    async def send_dm(self, bot_id: str, message: str, participant_id: str) -> None:
        self.calls.append(f"send_dm:{bot_id}")


async def _confirm_every_hit(text: str) -> bool:
    return True


class _LoaderRecorder:
    def __init__(self, map_text: str | None) -> None:
        self.map_text = map_text

    async def __call__(self, *, conn: Any, tenant_id: str, repo: str, pinned_sha: str) -> str | None:
        return self.map_text


_ASK = TranscriptLine(
    text="Proxy, stage that retry-cap change as a draft.",
    speaker="Devon",
    timestamp=20.0,
    end_of_turn=True,
)


@pytest.mark.asyncio
async def test_assembled_engine_carries_drafts_server_and_tools_on_the_captured_query(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC4 — ``assemble_engine(drafts=<server>)`` advertises DRAFT_TOOLS and mounts
    the ``drafts`` server on the captured provider query (the runtime test pattern);
    the ``drafts=None`` default mounting nothing is pinned by the existing runtime
    battery's exact-surface assertions."""
    from in_meeting import runtime
    from in_meeting.runtime import assemble_engine

    monkeypatch.setattr(runtime, "load_meeting_map", _LoaderRecorder(None))
    provider = FakeProvider()
    spoken: list[str] = []

    async def speak(text: str) -> None:
        spoken.append(text)

    drafts_server = build_drafts_server(db=object(), meeting_id="m-1", stage=RecordingStage())
    assert drafts_server is not None

    engine = await assemble_engine(
        model="claude-opus-4-6",
        tenant_id="tenant-a",
        repo="acme/widget",
        pinned_sha="abc123",
        bot_id="bot-7",
        transport=FakeTransport(),
        conn=object(),
        clone_path=tmp_path / "missing-clone",
        speak=speak,
        disambiguate=_confirm_every_hit,
        provider=provider,
        drafts=drafts_server,
    )

    await engine.feed_transcript(_ASK)
    await engine.drain()

    assert len(provider.calls) == 1
    _, query = provider.calls[0]
    assert query.allowed_tools == MEETING_TOOLS + DRAFT_TOOLS
    assert query.mcp_servers is not None
    assert set(query.mcp_servers) == {"meeting", "drafts"}
    assert query.mcp_servers["drafts"] is drafts_server


# ── AC6: provisioner assembly — the boot path passes the meeting-scoped server ────


class FakeConn:
    """A minimal asyncpg-conn stand-in (the cutover pattern) that also answers the
    ``staged_drafts`` INSERT so the REAL async staging machinery runs verbatim."""

    def __init__(
        self,
        *,
        meeting_row: dict[str, Any] | None = None,
        repo_row: dict[str, Any] | None = None,
    ) -> None:
        self.meeting_row = meeting_row
        self.repo_row = repo_row
        self.insert_args: list[tuple[Any, ...]] = []

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        if "INSERT INTO staged_drafts" in sql:
            self.insert_args.append(args)
            return {
                "draft_id": "d-9",
                "meeting_id": args[0],
                "kind": args[1],
                "summary": args[2],
                "artifact_ref": args[3],
                "status": args[4],
                "created_at": None,
            }
        if "FROM meetings" in sql:
            return self.meeting_row
        if "FROM repos" in sql:
            return self.repo_row
        return None

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        return []

    async def execute(self, sql: str, *args: Any) -> str:
        return "UPDATE 1"


class _FakePool:
    """The asyncpg-pool shape ``Database.acquire`` borrows from."""

    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> Any:
        conn = self._conn

        class _Ctx:
            async def __aenter__(self) -> FakeConn:
                return conn

            async def __aexit__(self, *exc: Any) -> None:
                return None

        return _Ctx()


class FakeSpeakPipe:
    def __init__(self) -> None:
        self.said: list[str] = []

    async def say(self, text: str) -> None:
        self.said.append(text)

    async def aclose(self) -> None:
        return None


class FakeSandboxBackend:
    def __init__(self) -> None:
        self.handle = _FakeSandboxHandle()

    async def __call__(self, **kwargs: Any) -> Any:
        return self.handle


class _FakeSandboxHandle:
    @property
    def commands(self) -> Any:
        return None

    @property
    def files(self) -> Any:
        return None

    async def kill(self) -> None:
        return None


def _resolved_row() -> dict[str, Any]:
    return {
        "id": "m-1",
        "tenant_id": "tenant-1",
        "repo_id": "r-1",
        "pinned_sha": "abc123",
        "recall_bot_id": "bot-7",
        "meeting_url": "https://meet.example/x",
    }


@pytest.mark.asyncio
async def test_provisioner_assembly_passes_the_meeting_scoped_drafts_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC6 — the REAL boot path (``provisioner._assemble_engine``) over the durable
    substrate facade mounts the ``drafts`` server + advertises DRAFT_TOOLS, and the
    mounted tool's staged row binds to THAT meeting (never another's): driving the
    ACTUAL mounted server through the real MCP dispatch lands the INSERT with the
    meeting's id and returns its approve URL."""
    from control_plane import provisioner as prov
    from in_meeting import runtime as im_runtime
    from libs.db import Database

    monkeypatch.setenv("PROXY_TENANT_VOLUME_ROOT", str(tmp_path))
    monkeypatch.setattr(im_runtime, "load_meeting_map", _LoaderRecorder(None))

    conn = FakeConn(
        meeting_row=_resolved_row(),
        repo_row={"id": "r-1", "tenant_id": "tenant-1", "full_name": "acme/widget"},
    )
    db = Database(_FakePool(conn), "drafts-test-instance")
    provider = FakeProvider()
    pipe = FakeSpeakPipe()

    engine, _, _ = await prov._assemble_engine(
        _resolved_row(),
        db=db,
        bot_id="bot-7",
        provider=provider,
        transport=FakeTransport(),
        speak=pipe,
        disambiguate=_confirm_every_hit,
        sandbox_backend=FakeSandboxBackend(),
        model="claude-orch-test",
    )

    await engine.feed_transcript(_ASK)
    await engine.drain()

    assert len(provider.calls) == 1
    _, query = provider.calls[0]
    assert query.allowed_tools == MEETING_TOOLS + SANDBOX_TOOLS + DRAFT_TOOLS
    assert query.mcp_servers is not None
    assert set(query.mcp_servers) == {"meeting", "sandbox", "drafts"}

    # Drive the ACTUAL mounted drafts server: the staged row binds to THIS meeting.
    out = await _call(
        query.mcp_servers["drafts"],
        "propose_change",
        {"kind": "code-change", "summary": "raise the retry cap", "files": _FILES},
    )
    assert "__error__" not in out
    assert len(conn.insert_args) == 1
    assert conn.insert_args[0][0] == "m-1", "the staged row must bind to the boot meeting"
    assert out["draft_id"] == "d-9"
    assert out["approve_url"] == "/m/m-1/drafts/d-9/accept"
