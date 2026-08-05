"""understanding.py — the resident doc = the qualitative COMPREHENSION + a compact navigation aid.

Proves the compose logic + the honest degrade: comprehension present → comprehension on top, the
compact navigation aid beneath; comprehension missing → the deterministic symbol map alone (never a
naked divider); and (via map_build.build_understanding_map) the full artifact still passes the
deterministic readiness gate.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from premeeting import map_build
from premeeting.exclusions import ExclusionManager
from premeeting.understanding import build_understanding
from premeeting.verify import verify_map


def test_build_understanding_combines_comprehension_and_navigation() -> None:
    comp = "## What this is\nA CLI toolkit. Context lives in the core module.\n"
    nav = "# Navigation map — click\n\n## Where things live (top-level areas)\n- src/\n"
    doc = build_understanding(comprehension=comp, navigation=nav)
    # comprehension first (the mental model), navigation aid beneath (the geography)
    assert doc.index("Codebase understanding") < doc.index("# Navigation map — click")
    assert "A CLI toolkit" in doc
    assert "## Where things live (top-level areas)" in doc


def test_build_understanding_degrades_to_navigation_alone() -> None:
    nav = "# Navigation map — click\n\n## Where things live (top-level areas)\n- src/\n"
    doc = build_understanding(comprehension="", navigation=nav)
    assert doc.strip() == nav.strip()
    assert "Codebase understanding" not in doc  # no empty comprehension header, no naked divider


def test_build_understanding_comprehension_only() -> None:
    comp = "## What this is\nA CLI toolkit.\n"
    doc = build_understanding(comprehension=comp, navigation="")
    assert "Codebase understanding" in doc
    assert "---" not in doc  # no divider when there's no nav aid below


def _fixture_repo(tmp_path: Path) -> tuple[Path, ExclusionManager]:
    root = tmp_path / "checkout"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "README.md").write_text("# widget\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname='widget'\n", encoding="utf-8")
    (root / "src" / "server.py").write_text(
        "class BaseCommand:\n    def invoke(self, ctx):\n        return process(ctx)\n"
        "\ndef process(ctx):\n    return BaseCommand().invoke(ctx)\n",
        encoding="utf-8",
    )
    (root / "src" / "models.py").write_text(
        "from server import process, BaseCommand\ndef helper():\n    return process(BaseCommand())\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_x.py").write_text("def test_x(): ...\n", encoding="utf-8")
    em = ExclusionManager()
    em.scan_after_clone(root)
    return root, em


@pytest.mark.asyncio
async def test_build_understanding_map_no_creds_is_symbol_map_and_verifies(tmp_path: Path) -> None:
    """With no E2B seam/token, build_understanding_map returns the Part-1 symbol map alone, and that
    artifact still passes the deterministic readiness gate (a clean degrade)."""
    root, em = _fixture_repo(tmp_path)
    res = await map_build.build_understanding_map(
        clone_path=root, repo_name="widget", sha="abc123", exclusions=em,  # no call/token
    )
    assert "# Symbol map" in res.index_md
    assert "src/server.py:" in res.index_md
    assert "Codebase understanding" not in res.index_md  # Part 2 skipped cleanly
    verdict = verify_map(res.index_md, root, exclusions=em)
    assert verdict.ready, verdict.reasons


@pytest.mark.asyncio
async def test_build_understanding_map_with_comprehension_is_comprehension_first(tmp_path: Path) -> None:
    """A combined doc is COMPREHENSION-FIRST: the qualitative mental model on top, a COMPACT
    navigation aid (area map + entry points) beneath — NOT the ranked-signatures dump. The
    comprehension is code-free prose, so it carries no file:line to verify and passes on substance."""
    root, em = _fixture_repo(tmp_path)

    # Fake sandbox returns a code-free, holistic comprehension (no pasted code, no line numbers).
    class _Files:
        def __init__(self) -> None:
            self.written: dict[str, str] = {}

        def write(self, path: str, content: str):  # type: ignore[no-untyped-def]
            self.written[path] = content
            return type("O", (), {"value": None})()

        def read(self, path: str):  # type: ignore[no-untyped-def]
            body = (
                "## What this is\nA small widget service that dispatches commands, with enough "
                "grounded, code-free prose here to clear the verification length floor comfortably.\n\n"
                "## How it works\nThe entry point is the base command, which invokes the process "
                "routine in the server module; the models module carries the domain types. The flow "
                "reads a request, dispatches it through the command, and returns the result.\n\n"
                "## Where things live\nCommands and dispatch live in the server area; the domain "
                "types live in the models area; tests are isolated in the tests directory.\n\n"
                "## Conventions\nThe repo uses a single import surface; commands are the unit of work "
                "and parameters attach to them. A context object threads state through the invocation. "
                "Errors are typed exceptions carrying user-facing messages, and tests use an isolated "
                "runner that captures output without touching the real process — plenty of grounded "
                "context here so the doc clears the minimum length on substance alone.\n"
            )
            return type("O", (), {"value": body})()

    class _Cmds:
        def run(self, cmd: str, timeout: int = 0, envs=None):  # type: ignore[no-untyped-def]
            return type("O", (), {"value": type("R", (), {"stdout": "", "stderr": ""})()})()

    class _Sbx:
        def __init__(self) -> None:
            self.commands = _Cmds()
            self.files = _Files()

        def kill(self) -> None:
            pass

    class _Cls:
        def create(self, timeout: int = 0) -> _Sbx:
            return _Sbx()

    async def _call(thunk, **_kw):  # type: ignore[no-untyped-def]
        return thunk()

    res = await map_build.build_understanding_map(
        clone_path=root, repo_name="widget", sha="abc123", exclusions=em,
        call=_call, token="sk-ant-test", sandbox_class=_Cls(),
        repo_url="https://example.invalid/widget.git",
    )
    assert "Codebase understanding" in res.index_md   # comprehension landed, on top
    assert "# Navigation map" in res.index_md          # compact nav aid beneath
    assert "## Ranked signatures" not in res.index_md  # NOT the giant symbol-index dump
    # comprehension appears before the navigation aid (comprehension-first)
    assert res.index_md.index("Codebase understanding") < res.index_md.index("# Navigation map")
    assert res.degraded is False
    verdict = verify_map(res.index_md, root, exclusions=em)
    assert verdict.ready, verdict.reasons
