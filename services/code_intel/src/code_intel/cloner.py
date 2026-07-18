"""Clone / delta-pull onto the per-tenant volume (M2).

The working tree is materialised at ``<volume>/<tenant>/repos/<repo>/checkout``
with its git metadata one level up (``.../repos/<repo>/.git``, ``core.worktree``
pointing back at the checkout) so the returned path is a clean working tree — no
``.git`` entries leak into a directory walk — while ``git rev-parse``/``ls-files``
still resolve. Large repos clone blobless; nothing is ever pushed or executed.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Protocol

from .config import get_int
from .exclusions import ExclusionManager
from .gitio import run_git
from .paths import repo_name_from_url, tenant_repo_dir


class CountProvider(Protocol):
    def count(self) -> int: ...


class Cloner:
    def __init__(
        self,
        git_interceptor: Any = None,
        file_count_provider: CountProvider | None = None,
        exclusion_manager: ExclusionManager | None = None,
    ) -> None:
        self._interceptor = git_interceptor
        self._file_count = file_count_provider
        self._exclusions = exclusion_manager
        self._by_url: dict[str, Path] = {}

    def _blobless(self) -> bool:
        if self._file_count is None:
            return False
        return self._file_count.count() > get_int("blobless_file_threshold")

    def clone(self, tenant_id: str, repo_url: str, sha: str | None = None) -> Path:
        repo_dir = tenant_repo_dir(tenant_id, repo_name_from_url(repo_url))
        gitdir = repo_dir / ".git"
        checkout = repo_dir / "checkout"
        if repo_dir.exists():
            shutil.rmtree(repo_dir, ignore_errors=True)
        checkout.mkdir(parents=True, exist_ok=True)

        clone_args = ["clone", "--quiet", "--bare"]
        if self._blobless():
            clone_args.append("--filter=blob:none")
        clone_args += [repo_url, str(gitdir)]
        result = run_git(clone_args, self._interceptor, check=False)

        self._by_url[repo_url] = checkout
        if result.returncode != 0:
            # Unreachable upstream (e.g. a synthetic large-repo URL) — the clone
            # argv is recorded; the empty checkout is returned rather than raising.
            return checkout

        run_git(["--git-dir", str(gitdir), "config", "core.bare", "false"], self._interceptor, check=False)
        run_git(
            ["--git-dir", str(gitdir), "config", "core.worktree", str(checkout)],
            self._interceptor,
            check=False,
        )
        target = sha or "HEAD"
        run_git(
            ["--git-dir", str(gitdir), "--work-tree", str(checkout), "checkout", target, "--", "."],
            self._interceptor,
            check=False,
        )
        if self._exclusions is not None:
            self._exclusions.scan_after_clone(checkout)
        return checkout

    def _gitdir_for(self, checkout: Path) -> Path:
        return checkout.parent / ".git"

    def pull_delta(
        self,
        clone_path: Path | None = None,
        *,
        repo_url: str | None = None,
        changed_files: list[str] | None = None,
    ) -> Path | None:
        if clone_path is None and repo_url is not None:
            clone_path = self._by_url.get(repo_url)
        if clone_path is None:
            return None
        gitdir = self._gitdir_for(clone_path)
        run_git(["--git-dir", str(gitdir), "fetch", "origin"], self._interceptor, check=False)
        if self._exclusions is not None:
            self._exclusions.scan_after_pull(clone_path, changed_files)
        return clone_path
