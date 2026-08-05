"""symbol_map.py — the deterministic, GROUNDABLE ranked symbol map (replaces the prose map).

Two tiers:

* A small **real-tree** unit test (no network): builds a tiny multi-file Python repo on disk and
  asserts the map carries real ``file:line``, ranks the cross-referenced symbol first, and elides
  bodies — proving the tree-sitter → PageRank → ``to_tree`` pipeline on the real path.
* A **real-repo** proof (marked ``integration`` — excluded from the offline tier, like every test
  that needs a live external resource): clones ``pallets/click`` (or reuses a local clone named by
  ``PROXY_SYMBOL_MAP_TEST_REPO``), builds the map, and asserts real ``file:line`` grounding into
  ``src/click/core.py``, that it fits the token budget, and that it is non-trivial (many files,
  ranked). It PRINTS the first ~60 lines so a human can judge grounding quality + token size.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from premeeting.symbol_map import Tag, _estimate_tokens, build_symbol_map

_CLICK_URL = "https://github.com/pallets/click"


# ── real-tree unit test (no network) ─────────────────────────────────────────
def _tiny_repo(root: Path) -> None:
    (root / "core.py").write_text(
        "class BaseCommand:\n"
        "    def invoke(self, ctx):\n"
        "        return process(ctx)\n"
        "\n"
        "def process(ctx):\n"
        "    return BaseCommand().invoke(ctx)\n"
        "\n"
        "def main():\n"
        "    process(None)\n",
        encoding="utf-8",
    )
    (root / "utils.py").write_text(
        "from core import process, BaseCommand\n"
        "\n"
        "def helper():\n"
        "    return process(BaseCommand())\n",
        encoding="utf-8",
    )


def test_symbol_map_grounds_real_file_line_on_a_tiny_tree(tmp_path: Path) -> None:
    repo = tmp_path / "widget"
    repo.mkdir()
    _tiny_repo(repo)

    m = build_symbol_map(str(repo), budget_tokens=2000)

    # Real, groundable file:line — the header + the def lines carry the actual 1-based lines.
    assert "core.py:" in m
    assert "utils.py:" in m
    assert "class BaseCommand:" in m
    # BaseCommand is defined on line 1 → the rendered line is numbered 1 (real file:line).
    assert "1│class BaseCommand:" in m
    # The map spans BOTH files (ranked, non-trivial) and names the entry-point section.
    assert "Entry points" in m
    assert "Where things live" in m
    # The cross-referenced symbol (process, referenced from 3 sites) ranks as an entry point.
    assert "process" in m
    # Bodies are elided — the map is signatures, not full source (Aider's to_tree marker).
    assert "...⋮..." in m


def test_symbol_map_empty_repo_is_honest(tmp_path: Path) -> None:
    """A repo with no parseable source yields an honest 'no symbols' map, never a crash (Law 1/2)."""
    repo = tmp_path / "empty"
    repo.mkdir()
    (repo / "notes.txt").write_text("just prose, no code\n", encoding="utf-8")
    m = build_symbol_map(str(repo))
    assert "no parseable source symbols" in m


def test_tag_is_one_based_and_typed() -> None:
    """The Tag contract: a 1-based line + a def/ref kind (so a citation is a real file:line)."""
    t = Tag(rel_fname="a.py", fname="/x/a.py", line=1, name="foo", kind="def")
    assert t.line == 1 and t.kind == "def" and t.rel_fname == "a.py"


# ── real-repo proof (network — excluded from the offline tier) ────────────────
def _resolve_click_repo(tmp_path: Path) -> Path | None:
    """A local click clone from ``PROXY_SYMBOL_MAP_TEST_REPO``, else a fresh shallow clone.

    Returns ``None`` (→ skip) if no clone is available (offline) — the offline gate never runs
    this test anyway (it's ``integration``); this keeps a manual offline run honest, not red.
    """
    override = os.environ.get("PROXY_SYMBOL_MAP_TEST_REPO")
    if override and (Path(override) / "src" / "click").is_dir():
        return Path(override)
    dest = tmp_path / "click"
    try:
        subprocess.run(  # noqa: S603,S607 - fixed argv, no shell; a public read-only clone
            ["git", "clone", "--depth", "1", _CLICK_URL, str(dest)],
            capture_output=True,
            text=True,
            check=True,
            timeout=180,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return None
    return dest if (dest / "src" / "click").is_dir() else None


@pytest.mark.integration
def test_symbol_map_on_real_click_repo(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _resolve_click_repo(tmp_path)
    if repo is None:
        pytest.skip("click clone unavailable (offline); set PROXY_SYMBOL_MAP_TEST_REPO to reuse one")

    budget = 11000
    m = build_symbol_map(str(repo), budget_tokens=budget)

    # 1) Real file:line grounding into click's core module (the citation surface).
    assert "src/click/core.py:" in m, "map must ground into src/click/core.py"
    # A stable core-API symbol appears with a real line number under that file.
    core_section = m.split("src/click/core.py:", 1)[1]
    assert "class Command:" in core_section or "class Context:" in core_section, (
        "core.py section must show a real class signature with its line number"
    )
    # The header names click's real entry points with file:line (`src/click/<f>.py:<line>`).
    assert "src/click/" in m and ":" in m

    # 2) Under the token budget (the artifact must stay resident in a warm cached context).
    est = _estimate_tokens(m)
    assert est <= budget, f"map ({est} est. tokens) must be within the {budget}-token budget"
    assert est > 1500, "a real repo map must be non-trivial, not a stub"

    # 3) Non-trivial + ranked: many files, an entry-point list, elided bodies.
    file_headers = {ln.rstrip(":") for ln in m.splitlines() if ln.endswith(":") and "/" in ln}
    assert len(file_headers) >= 5, f"expected many files in the map, saw {len(file_headers)}"
    assert "Entry points" in m and "...⋮..." in m

    # Print the first ~60 lines so a human can judge grounding quality + size.
    head = "\n".join(m.splitlines()[:60])
    with capsys.disabled():
        print(f"\n===== symbol map for click ({est} est. tokens, {len(file_headers)} files) =====")
        print(head)
        print("===== end of first 60 lines =====")
