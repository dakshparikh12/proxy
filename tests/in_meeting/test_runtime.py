"""Acceptance battery for RUNTIME — the in-meeting runtime entrypoint (``in_meeting.runtime``).

The Engine is proven but un-integrated; ``runtime.py`` is the integration spine that
assembles it with ALL its real access — the pre-meeting map (MAP-LOAD), the grounded
code toolbelt (``premeeting.repo_context``), and the meeting-control toolbelt — and
drives it from the meeting's transcript. Deterministic and offline: the provider is a
scripted fake (no live CLI), ``load_meeting_map`` is monkeypatched (the real-Postgres
path is pinned by MAP-LOAD's own tests in ``test_map_loader.py``), the transport is a
fake, and the clone is a tmp dir. The four AC groups:

1. full assembly — the captured provider query carries ``CODE_TOOLS + MEETING_TOOLS``,
   BOTH the ``code_intel`` and ``meeting`` servers, and the map in ``system_prompt``;
   the loader was called with the meeting's exact pinned ``(tenant, repo, sha)`` key;
2. no-map/no-clone degrade — meeting tools only, ``{"meeting": ...}`` only, and the
   turn still assembles + runs (Proxy stays functional with no codebase this meeting);
3. driver loop — a scripted [idle, addressed, idle] source runs EXACTLY one provider
   turn (idle=free) and the driver completes without raising;
4. graceful — a provider that errors on a turn never crashes ``run_meeting``: the
   source is consumed to the end and the later addressed line still runs.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Sequence
from pathlib import Path
from typing import Any

import pytest
from agentkit import ProviderQuery
from contracts import AgentChunk

from in_meeting import runtime
from in_meeting.engine import CODE_TOOLS
from in_meeting.meeting_control import MEETING_TOOLS
from in_meeting.notes import TranscriptLine
from in_meeting.prompt import PROXY_SYSTEM_PROMPT
from in_meeting.runtime import assemble_engine, run_meeting
from in_meeting.trigger import ChatLine

_MODEL = "claude-opus-4-6"
_MAP = "# Repo Map\n- retries live in libs/http/client.py\n- auth in services/auth/login.py"
_ASK = "Proxy, where's the retry logic?"
_ANSWER = "on it, the retry logic is in client.py:42"


def _line(text: str, speaker: str = "Devon", timestamp: float = 20.0) -> TranscriptLine:
    return TranscriptLine(text=text, speaker=speaker, timestamp=timestamp, end_of_turn=True)


async def _source(lines: Iterable[TranscriptLine]) -> AsyncIterator[TranscriptLine]:
    for line in lines:
        yield line


async def _chat_source(msgs: Iterable[ChatLine]) -> AsyncIterator[ChatLine]:
    for msg in msgs:
        yield msg


def _happy_turn() -> list[AgentChunk]:
    return [
        AgentChunk(type="INIT", text=None, metadata={"session_id": "s-1", "tools": [], "mcp_servers": []}),
        AgentChunk(type="TEXT", text=_ANSWER, metadata={"msg_id": "m-1"}),
        AgentChunk(type="RESULT", text=_ANSWER, metadata={"session_id": "s-1", "total_cost_usd": 0.01}),
    ]


class FakeProvider:
    """A scripted ``agentkit.Provider``: records every ``(prompt, query)`` call and
    replays the per-call chunk script (the last script repeats for extra calls)."""

    def __init__(self, turns: Sequence[Sequence[AgentChunk]] | None = None) -> None:
        self.calls: list[tuple[str, ProviderQuery]] = []
        self._turns: list[list[AgentChunk]] = [list(t) for t in (turns or [_happy_turn()])]

    async def stream(self, prompt: str, query: ProviderQuery) -> AsyncIterator[AgentChunk]:
        self.calls.append((prompt, query))
        script = self._turns[min(len(self.calls) - 1, len(self._turns) - 1)]
        for chunk in script:
            yield chunk


class ErroringProvider:
    """Raises on the FIRST turn (before any chunk), then replays the happy turn —
    the graceful-driver half: the Engine absorbs the fault, the loop survives."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ProviderQuery]] = []

    async def stream(self, prompt: str, query: ProviderQuery) -> AsyncIterator[AgentChunk]:
        self.calls.append((prompt, query))
        if len(self.calls) == 1:
            raise RuntimeError("SDK subprocess died before the first chunk")
        for chunk in _happy_turn():
            yield chunk


class FakeTransport:
    """The MeetingControlTransport verbs as inert recorders (never called here —
    assembly mounts the toolbelt; the agent decides whether to use it)."""

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


class _LoaderRecorder:
    """Stands in for ``load_meeting_map``: records the exact pinned key it was
    called with and returns the scripted map text (or None)."""

    def __init__(self, map_text: str | None) -> None:
        self.map_text = map_text
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, *, conn: Any, tenant_id: str, repo: str, pinned_sha: str) -> str | None:
        self.calls.append(
            {"conn": conn, "tenant_id": tenant_id, "repo": repo, "pinned_sha": pinned_sha}
        )
        return self.map_text


async def _assemble(
    monkeypatch: pytest.MonkeyPatch,
    *,
    provider: Any,
    clone_path: Path,
    map_text: str | None,
    speak_into: list[str],
) -> tuple[Any, _LoaderRecorder, FakeTransport, object]:
    """Assemble an Engine through the REAL ``assemble_engine`` path with the
    injected fakes; returns (engine, loader, transport, conn sentinel)."""
    loader = _LoaderRecorder(map_text)
    monkeypatch.setattr(runtime, "load_meeting_map", loader)
    transport = FakeTransport()
    conn = object()

    async def speak(text: str) -> None:
        speak_into.append(text)

    engine = await assemble_engine(
        model=_MODEL,
        tenant_id="tenant-a",
        repo="acme/widget",
        pinned_sha="abc123",
        bot_id="bot-7",
        transport=transport,
        conn=conn,
        clone_path=clone_path,
        speak=speak,
        disambiguate=lambda text: True,
        provider=provider,
    )
    return engine, loader, transport, conn


# ── AC1: full assembly — map + code tools + meeting tools, all mounted ────────


@pytest.mark.asyncio
async def test_full_assembly_mounts_code_and_meeting_access(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC1 — with a stored map and a real clone dir, ONE addressed line driven
    through ``run_meeting`` reaches the provider carrying the FULL composed
    access: CODE_TOOLS + MEETING_TOOLS, both servers, and the map in the prefix."""
    (tmp_path / "client.py").write_text("def retry():\n    return 42\n", encoding="utf-8")
    provider = FakeProvider()
    spoken: list[str] = []

    engine, loader, _, conn = await _assemble(
        monkeypatch, provider=provider, clone_path=tmp_path, map_text=_MAP, speak_into=spoken
    )

    # The loader was called ONCE with the meeting's exact pinned key (never "latest").
    assert loader.calls == [
        {"conn": conn, "tenant_id": "tenant-a", "repo": "acme/widget", "pinned_sha": "abc123"}
    ]

    await run_meeting(engine, transcript_source=_source([_line(_ASK)]))

    assert len(provider.calls) == 1
    _, query = provider.calls[0]
    assert query.allowed_tools == CODE_TOOLS + MEETING_TOOLS
    assert query.mcp_servers is not None
    assert "code_intel" in query.mcp_servers and "meeting" in query.mcp_servers
    assert _MAP in query.system_prompt
    assert PROXY_SYSTEM_PROMPT in query.system_prompt
    assert spoken == [_ANSWER]


# ── AC2: no-map/no-clone degrade — meeting access only, still runs ────────────


@pytest.mark.asyncio
async def test_no_map_no_clone_degrades_to_meeting_tools_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC2 — an unindexed repo (map None) with no clone on disk assembles with
    MEETING_TOOLS alone and only the ``meeting`` server, and still runs a turn."""
    provider = FakeProvider()
    spoken: list[str] = []

    engine, _, _, _ = await _assemble(
        monkeypatch,
        provider=provider,
        clone_path=tmp_path / "missing-clone",
        map_text=None,
        speak_into=spoken,
    )

    await run_meeting(engine, transcript_source=_source([_line(_ASK)]))

    assert len(provider.calls) == 1
    _, query = provider.calls[0]
    assert query.allowed_tools == MEETING_TOOLS
    assert query.mcp_servers is not None
    assert set(query.mcp_servers) == {"meeting"}
    assert "# Repository map" not in query.system_prompt
    assert spoken == [_ANSWER]


# ── AC3: driver loop — idle is free, the driver completes ─────────────────────


@pytest.mark.asyncio
async def test_driver_runs_exactly_one_turn_across_idle_lines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC3 — [idle, addressed, idle]: the provider is called EXACTLY once (idle
    does zero provider work) and ``run_meeting`` completes without raising."""
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    provider = FakeProvider()
    spoken: list[str] = []

    engine, _, _, _ = await _assemble(
        monkeypatch, provider=provider, clone_path=tmp_path, map_text=_MAP, speak_into=spoken
    )

    lines = [
        _line("Let's look at the flaky checkout calls.", "Priya", 10.2),
        _line(_ASK, "Devon", 20.0),
        _line("Moving on to the roadmap.", "Marcus", 30.5),
    ]
    await run_meeting(engine, transcript_source=_source(lines))

    assert len(provider.calls) == 1
    assert spoken == [_ANSWER]
    # All three lines accumulated as notes — the driver fed every line, wake or not.
    assert len(engine.notes) == 3


@pytest.mark.asyncio
async def test_driver_consumes_the_chat_source_too(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC3 (chat seam) — a ``chat_source`` is consumed through ``feed_chat``:
    the ``@proxy`` token wakes a turn, plain chat stays free."""
    provider = FakeProvider()
    spoken: list[str] = []

    engine, _, _, _ = await _assemble(
        monkeypatch,
        provider=provider,
        clone_path=tmp_path / "missing-clone",
        map_text=None,
        speak_into=spoken,
    )

    msgs = [
        ChatLine(sender="Priya", message="the proxy server is fine"),
        ChatLine(sender="Priya", message="@proxy summarize the decision"),
    ]
    await run_meeting(
        engine, transcript_source=_source([]), chat_source=_chat_source(msgs)
    )

    assert len(provider.calls) == 1
    prompt, _ = provider.calls[0]
    assert "@proxy summarize the decision" in prompt


# ── AC4: graceful — a turn error never crashes the driver ─────────────────────


@pytest.mark.asyncio
async def test_turn_error_never_crashes_run_meeting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC4 — the first addressed turn's provider RAISES; ``run_meeting`` still
    consumes the source to the end and the later addressed line runs a full turn."""
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    provider = ErroringProvider()
    spoken: list[str] = []

    engine, _, _, _ = await _assemble(
        monkeypatch, provider=provider, clone_path=tmp_path, map_text=_MAP, speak_into=spoken
    )

    lines = [
        _line(_ASK, "Devon", 20.0),
        _line("No wake word in this one.", "Priya", 25.0),
        _line("Proxy, are you still with us?", "Marcus", 31.0),
    ]
    await run_meeting(engine, transcript_source=_source(lines))  # must not raise

    assert len(provider.calls) == 2  # the failed turn + the surviving retry
    assert engine.last_turn is not None and engine.last_turn.error is None
    assert spoken == [_ANSWER]  # the second turn spoke; the first failed honestly
    assert len(engine.notes) == 3  # every line was still fed after the fault
