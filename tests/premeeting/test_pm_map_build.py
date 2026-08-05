"""map_build.py — the pre-meeting map-build entrypoint.

:func:`map_build.build_map` (the entrypoint both callers use) returns the DETERMINISTIC,
GROUNDABLE symbol map (:func:`premeeting.symbol_map.build_symbol_map`) — real ``file:line`` +
ranked signatures, NO model call, so what pre-meeting STORES is what a meeting cites (Law 1).
These run on a real on-disk tree (offline). ``provider`` / ``model`` are retained on the signature
as the legacy model seam the callers pass; the deterministic build ignores them (a recording
``agentkit.Provider`` proves it is never streamed). The old LLM prose loop was deleted (dead code);
Part 2's native-Claude comprehension pass is covered by ``test_pm_comprehension.py``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from premeeting import map_build
from premeeting.exclusions import ExclusionManager


class FakeProvider:
    """A stand-in ``agentkit.Provider`` — the legacy model seam ``build_map`` accepts but ignores.

    The deterministic Part-1 build never streams it; the tests assert exactly that (``seen`` stays
    False). It carries the ``name`` attribute a real Provider exposes so the signature type holds."""

    name = "claude"

    def __init__(self) -> None:
        self.seen = False


def _fixture_repo(tmp_path: Path) -> tuple[Path, ExclusionManager]:
    root = tmp_path / "checkout"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "README.md").write_text("# widget\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname='widget'\n", encoding="utf-8")
    # Cross-referenced symbols so the ranking graph forms and BaseCommand/process rank as
    # entry points (a real, groundable map — not a stub).
    (root / "src" / "server.py").write_text(
        "class BaseCommand:\n"
        "    def invoke(self, ctx):\n"
        "        return process(ctx)\n"
        "\n"
        "def process(ctx):\n"
        "    return BaseCommand().invoke(ctx)\n",
        encoding="utf-8",
    )
    (root / "src" / "models.py").write_text(
        "from server import process, BaseCommand\n"
        "def helper():\n"
        "    return process(BaseCommand())\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_x.py").write_text("def test_x(): ...\n", encoding="utf-8")
    # a planted secret — must never reach the stored map
    (root / ".env").write_text("API_KEY=leak_abcdefgh\n", encoding="utf-8")
    em = ExclusionManager()
    em.scan_after_clone(root)
    return root, em


# ── build_map STORES the deterministic, groundable symbol map ─────────────────
@pytest.mark.asyncio
async def test_build_map_stores_groundable_symbol_map(tmp_path: Path) -> None:
    """The stored map is the deterministic symbol map: real file:line + ranked signatures, NOT the
    old six-section prose shape, and NO model call (the fake provider is never streamed)."""
    root, em = _fixture_repo(tmp_path)
    provider = FakeProvider()
    res = await map_build.build_map(
        provider=provider, clone_path=root, repo_name="widget", sha="abc123", exclusions=em
    )

    # Groundable: real file:line — file headers + the highest-rank entry point with a line number.
    assert "src/server.py:" in res.index_md
    assert "src/models.py:" in res.index_md
    assert "1│class BaseCommand:" in res.index_md  # def line 1 rendered with its real line number
    # Ranked signatures: the cross-referenced symbols are named; the section headers are present.
    assert "process" in res.index_md and "BaseCommand" in res.index_md
    assert "Entry points" in res.index_md and "Where things live" in res.index_md
    assert "Ranked signatures" in res.index_md
    # Bodies elided — signatures, not full source (Aider's to_tree marker).
    assert "...⋮..." in res.index_md
    # Non-trivial (not a stub) and NOT the deprecated prose shape.
    assert len(res.index_md) > 300
    assert "## What this is" not in res.index_md
    assert "## Key models / domain" not in res.index_md
    # Deterministic: never degraded, no model turn, no tool log — the provider was NOT streamed.
    assert res.degraded is False
    assert res.turns == 0 and res.tool_log == []
    assert provider.seen is False


@pytest.mark.asyncio
async def test_build_map_excludes_secrets(tmp_path: Path) -> None:
    """A planted secret path/value never reaches the stored map (the exclusion boundary holds)."""
    root, em = _fixture_repo(tmp_path)
    res = await map_build.build_map(
        provider=FakeProvider(), clone_path=root, repo_name="widget", sha="abc123", exclusions=em
    )
    assert ".env" not in res.index_md
    assert "leak_abcdefgh" not in res.index_md


@pytest.mark.asyncio
async def test_build_map_empty_repo_is_honest(tmp_path: Path) -> None:
    """A repo with no parseable source yields an honest map, never a crash (Law 1/2)."""
    root = tmp_path / "empty"
    root.mkdir()
    (root / "notes.txt").write_text("just prose, no code\n", encoding="utf-8")
    em = ExclusionManager()
    em.scan_after_clone(root)
    res = await map_build.build_map(
        provider=FakeProvider(), clone_path=root, repo_name="empty", sha="x", exclusions=em
    )
    assert "no parseable source symbols" in res.index_md
    assert res.degraded is False
