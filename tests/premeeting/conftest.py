"""Shared fixtures for the premeeting test tree.

Roots the per-tenant volume at an isolated tmp dir so a test never touches ``/tenants`` or a
sibling test's data, and provides a real on-disk git fixture repo builder for the clone /
verify / toolbelt real-infra tests.
"""
from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_volume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Root ``premeeting.paths.volume_root`` at an isolated tmp dir for every test."""
    root = tmp_path / "tenants"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PROXY_TENANT_VOLUME_ROOT", str(root))
    yield root


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(  # noqa: S603 - fixed git binary, argv list, no shell
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )


@pytest.fixture
def make_git_repo(tmp_path: Path) -> "object":
    """Build a real on-disk git repo from a ``{relpath: content}`` map; return its path + HEAD sha."""

    def _make(files: dict[str, str], name: str = "fixture-repo") -> tuple[Path, str]:
        repo = tmp_path / name
        repo.mkdir(parents=True, exist_ok=True)
        for rel, content in files.items():
            p = repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        _git(["init", "-q", "-b", "main"], repo)
        _git(["config", "user.email", "t@example.com"], repo)
        _git(["config", "user.name", "Test"], repo)
        _git(["add", "-A"], repo)
        _git(["commit", "-q", "-m", "init"], repo)
        sha = subprocess.run(  # noqa: S603
            ["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True, text=True, check=True
        ).stdout.strip()
        return repo, sha

    return _make
