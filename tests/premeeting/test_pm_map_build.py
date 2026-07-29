"""map_build.py — the bounded one-agent loop plumbing (PM-MAP-01..05).

Fakes ONLY at the model seam (a recording ``agentkit.Provider``); everything else is the real
path — the real skeleton walk, the real prompt assembly, the real ProviderQuery the seam
receives, the real terminal-text capture. PM-MAP-06 (real-model map QUALITY) is
BLOCKED-on-credits (D-032) and is NOT tested here — see the report.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from agentkit import ProviderQuery
from libs.contracts import AgentChunk

from premeeting import map_build
from premeeting.exclusions import ExclusionManager

_CANNED_MAP = """# Repo Map — widget @ abc123

## What this is
A small Python service.

## Where things live
- src/ — the app
- tests/ — the tests

## Entry points
- src/server.py — the HTTP server

## Key models / domain
- src/models.py — the domain types

## Conventions
- pytest for tests; ruff for lint.

## Notes
Single-language repo.
"""


class FakeProvider:
    """A recording ``agentkit.Provider`` — NO live model call (the seam, D-032).

    Captures the ProviderQuery it received (so the test can assert the read-only disposition +
    budget the real plumbing set), emits a scripted TOOL_USE stream (a single batch_read of the
    high-yield files — the bounded discipline), then the canned index.md as terminal TEXT + a
    RESULT. It NEVER emits a full ls-files read — proving the plumbing never fed one."""

    name = "claude"

    def __init__(self, *, index_md: str = _CANNED_MAP, num_turns: int = 3,
                 tool_names: tuple[str, ...] = ("mcp__code_intel__batch_read",)) -> None:
        self.index_md = index_md
        self.num_turns = num_turns
        self.tool_names = tool_names
        self.seen_query: ProviderQuery | None = None
        self.seen_prompt: str = ""

    async def stream(self, prompt: str, query: ProviderQuery) -> AsyncIterator[AgentChunk]:
        self.seen_prompt = prompt
        self.seen_query = query
        yield AgentChunk(type="INIT", metadata={"session_id": "s1"})
        for name in self.tool_names:
            yield AgentChunk(type="TOOL_USE", metadata={"id": "t", "name": name, "input": {}})
        yield AgentChunk(type="TEXT", text=self.index_md, metadata={"msg_id": "m1"})
        yield AgentChunk(type="RESULT", metadata={"num_turns": self.num_turns, "total_cost_usd": 0.0})


def _fixture_repo(tmp_path: Path) -> tuple[Path, ExclusionManager]:
    root = tmp_path / "checkout"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "README.md").write_text("# widget\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname='widget'\n", encoding="utf-8")
    (root / "src" / "server.py").write_text("def main(): ...\n", encoding="utf-8")
    (root / "src" / "models.py").write_text("class Thing: ...\n", encoding="utf-8")
    # a planted secret — must never reach the skeleton / high-yield offer
    (root / ".env").write_text("API_KEY=leak_abcdefgh\n", encoding="utf-8")
    em = ExclusionManager()
    em.scan_after_clone(root)
    return root, em


# ── PM-MAP-01: all six sections present ──────────────────────────────────────
@pytest.mark.asyncio
async def test_pm_map_01_index_has_all_six_sections(tmp_path: Path) -> None:
    root, em = _fixture_repo(tmp_path)
    res = await map_build.build_map(
        provider=FakeProvider(), clone_path=root, repo_name="widget", sha="abc123", exclusions=em
    )
    for section in map_build.REQUIRED_SECTIONS:
        assert f"## {section}" in res.index_md, f"missing section {section!r}"
    assert not res.degraded


# ── PM-MAP-02: never ingests the full file list; skeleton depth bounded ──────
@pytest.mark.asyncio
async def test_pm_map_02_no_full_list_and_bounded_depth(tmp_path: Path) -> None:
    root, em = _fixture_repo(tmp_path)
    # Plant a DEEP tree so a naive full-walk would exceed the depth bound.
    deep = root / "a" / "b" / "c" / "d" / "e"
    deep.mkdir(parents=True)
    (deep / "buried.py").write_text("x=1\n", encoding="utf-8")

    skeleton = map_build.build_skeleton(root, exclusions=em)
    # Depth bound: the deepest indent is <= (MAX_SKELETON_DEPTH-1)*2 spaces, so 'buried.py'
    # (depth 6) never appears.
    assert "buried.py" not in skeleton
    # The skeleton is bounded, never the ~10-token/file full dump.
    assert len(skeleton.splitlines()) <= map_build.MAX_SKELETON_LINES
    # The planted secret never appears in the skeleton (excluded before any read).
    assert ".env" not in skeleton

    res = await map_build.build_map(
        provider=FakeProvider(), clone_path=root, repo_name="widget", sha="abc123", exclusions=em
    )
    # The transcript contains NO full ls-files dump tool (the plumbing never fed one).
    assert not any("ls-files" in t or "list_all" in t for t in res.tool_log)
    # The prompt handed to the model carries the bounded skeleton, NOT a raw file list.
    provider = FakeProvider()
    await map_build.build_map(
        provider=provider, clone_path=root, repo_name="widget", sha="abc123", exclusions=em
    )
    assert "Directory skeleton (bounded depth)" in provider.seen_prompt
    assert "buried.py" not in provider.seen_prompt  # deep path never entered context


# ── PM-MAP-03: high-yield files read in a BATCH ──────────────────────────────
@pytest.mark.asyncio
async def test_pm_map_03_high_yield_batched(tmp_path: Path) -> None:
    root, em = _fixture_repo(tmp_path)
    hy = map_build.collect_high_yield(root, exclusions=em)
    assert "README.md" in hy and "pyproject.toml" in hy
    assert ".env" not in hy  # secret never offered
    provider = FakeProvider(tool_names=("mcp__code_intel__batch_read",))
    res = await map_build.build_map(
        provider=provider, clone_path=root, repo_name="widget", sha="abc123", exclusions=em
    )
    # A single batched read appears (not N one-by-one reads).
    assert res.tool_log.count("mcp__code_intel__batch_read") >= 1
    assert res.tool_log.count("mcp__code_intel__read") == 0
    # The prompt instructs a single batch of the high-yield files.
    assert "ONE batch" in provider.seen_prompt


# ── PM-MAP-04: read-only quick disposition — mutations blocked ───────────────
@pytest.mark.asyncio
async def test_pm_map_04_read_only_disposition(tmp_path: Path) -> None:
    root, em = _fixture_repo(tmp_path)
    provider = FakeProvider()
    await map_build.build_map(
        provider=provider, clone_path=root, repo_name="widget", sha="abc123", exclusions=em
    )
    q = provider.seen_query
    assert q is not None
    # Only read/grep/glob/batch_read advertised — NO write/edit/run tool.
    assert set(q.allowed_tools) == set(map_build.MAP_BUILD_READ_TOOLS)
    for mut in ("mcp__code__write_file", "mcp__code__edit_file", "mcp__code__run_command", "Write", "Bash"):
        assert mut in q.disallowed_tools
        assert mut not in q.allowed_tools
    # No advertised tool is a mutation tool.
    assert not any(t in map_build.MAP_BUILD_BLOCKED_TOOLS for t in q.allowed_tools)


# ── PM-MAP-05: budget backstop → degrade, never hang/truncate ────────────────
@pytest.mark.asyncio
async def test_pm_map_05_budget_backstop_degrades_gracefully(tmp_path: Path) -> None:
    root, em = _fixture_repo(tmp_path)
    # A provider that hits max_turns and returns NO usable body → forces degrade.
    hit_cap = FakeProvider(index_md="", num_turns=map_build.DEFAULT_MAX_TURNS)
    res = await map_build.build_map(
        provider=hit_cap, clone_path=root, repo_name="widget", sha="abc123", exclusions=em,
        max_turns=map_build.DEFAULT_MAX_TURNS,
    )
    assert res.degraded
    # A COMPLETE top-level map (all six sections) + the depth-via-live-search note — never empty.
    for section in map_build.REQUIRED_SECTIONS:
        assert f"## {section}" in res.index_md
    assert "live search" in res.index_md
    # Bounded: the run count is capped (never a runaway).
    assert res.turns <= map_build.DEFAULT_MAX_TURNS
    # The max_turns cap is set on the real ProviderQuery.
    assert hit_cap.seen_query is not None and hit_cap.seen_query.max_turns == map_build.DEFAULT_MAX_TURNS
