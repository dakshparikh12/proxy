"""verify.py — deterministic completeness + no-hallucination + no-leak (PM-VERIFY-01..04).

Real clone (a real on-disk fixture repo); model-free verification. Every failure mode names its
gap; ready only on a full pass.
"""
from __future__ import annotations

from typing import Any

import pytest

from premeeting.cloner import Cloner
from premeeting.exclusions import ExclusionManager
from premeeting.verify import extract_named_paths, verify_map

_FAITHFUL_MAP = """# Repo Map — fixture-repo @ abc

## What this is
A small service.

## Where things live
- src/ — the app code
- tests/ — the tests

## Entry points
- src/server.py — the HTTP server

## Key models / domain
- src/models.py — the domain types

## Conventions
pytest + ruff.

## Notes
Single language.
"""


def _clone_fixture(make_git_repo: Any) -> tuple[Any, ExclusionManager]:
    src, _sha = make_git_repo(
        {
            "src/server.py": "def main(): ...\n",
            "src/models.py": "class Thing: ...\n",
            "tests/test_x.py": "def test_x(): ...\n",
            "README.md": "# fixture\n",
        }
    )
    em = ExclusionManager()
    checkout = Cloner(exclusion_manager=em).clone("tenant-a", src.as_uri())
    return checkout, em


# ── PM-VERIFY-01: faithful map passes; a fabricated path fails ───────────────
def test_pm_verify_01_faithful_map_passes(make_git_repo: Any) -> None:
    checkout, em = _clone_fixture(make_git_repo)
    res = verify_map(_FAITHFUL_MAP, checkout, exclusions=em)
    assert res.ready, res.reasons
    assert res.status == "ready"


def test_pm_verify_01_fabricated_path_fails(make_git_repo: Any) -> None:
    checkout, em = _clone_fixture(make_git_repo)
    bad = _FAITHFUL_MAP.replace("src/server.py", "src/does_not_exist.py")
    res = verify_map(bad, checkout, exclusions=em)
    assert not res.ready
    assert any("not in the clone" in r for r in res.reasons)
    assert any("does_not_exist.py" in r for r in res.reasons)


# ── PM-VERIFY-02: an omitted top-level dir fails with the dir named ──────────
def test_pm_verify_02_uncovered_top_dir_fails(make_git_repo: Any) -> None:
    checkout, em = _clone_fixture(make_git_repo)
    # Drop the 'tests' mention → the tracked top-level dir 'tests' is uncovered.
    no_tests = _FAITHFUL_MAP.replace("- tests/ — the tests\n", "")
    res = verify_map(no_tests, checkout, exclusions=em)
    assert not res.ready
    assert any("top-level dirs not covered" in r and "tests" in r for r in res.reasons)


# ── PM-VERIFY-03: a secret value / secret path in the map fails ──────────────
def test_pm_verify_03_secret_path_leak_fails(make_git_repo: Any) -> None:
    src, _sha = make_git_repo(
        {
            "src/server.py": "def main(): ...\n",
            "tests/test_x.py": "def t(): ...\n",
            ".env": "API_KEY=leakvalue_abcdefgh\n",
        }
    )
    em = ExclusionManager()
    checkout = Cloner(exclusion_manager=em).clone("tenant-a", src.as_uri())
    # A map that echoes the excluded .env path.
    leaky = _FAITHFUL_MAP.replace("- src/ — the app code", "- .env — config\n- src/ — the app code")
    res = verify_map(leaky, checkout, exclusions=em)
    assert not res.ready
    assert any("secret path leaked" in r for r in res.reasons)


def test_pm_verify_03_secret_value_leak_fails(make_git_repo: Any) -> None:
    src, _sha = make_git_repo(
        {
            "src/server.py": 'TOKEN = "AKIAABCDEFGHIJKLMNOP"\n',
            "src/models.py": "class T: ...\n",
            "tests/t.py": "x=1\n",
        }
    )
    em = ExclusionManager()
    checkout = Cloner(exclusion_manager=em).clone("tenant-a", src.as_uri())
    assert "AKIAABCDEFGHIJKLMNOP" in em.secret_values()
    leaky = _FAITHFUL_MAP + "\nThe key is AKIAABCDEFGHIJKLMNOP\n"
    res = verify_map(leaky, checkout, exclusions=em)
    assert not res.ready
    assert any("secret value leaked" in r for r in res.reasons)


# ── PM-VERIFY-04: ready only on full pass; empty / missing-section named ─────
def test_pm_verify_04_empty_map_not_ready() -> None:
    from pathlib import Path

    res = verify_map("", Path("/nonexistent"))
    assert not res.ready
    assert any("empty" in r for r in res.reasons)


def test_pm_verify_04_missing_section_named(make_git_repo: Any) -> None:
    checkout, em = _clone_fixture(make_git_repo)
    no_notes = _FAITHFUL_MAP.replace("## Notes\nSingle language.\n", "")
    res = verify_map(no_notes, checkout, exclusions=em)
    assert not res.ready
    assert any("missing sections" in r and "Notes" in r for r in res.reasons)


def test_extract_named_paths_ignores_prose() -> None:
    named = extract_named_paths("This is prose, e.g. a sentence. But src/app.py is a path.")
    assert "src/app.py" in named
    assert "This" not in named and "sentence" not in named and "e.g" not in named
