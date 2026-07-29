"""Clone / delta-pull onto the per-tenant volume (PM-CLONE-01..05).

The working tree is materialised at ``<volume>/<tenant>/repos/<repo>/checkout`` with its git
metadata one level up (``.../repos/<repo>/.git``, ``core.worktree`` pointing back at the
checkout) so the returned path is a clean working tree — no ``.git`` entries leak into a
directory walk (the map-build skeleton) — while ``git rev-parse`` / ``ls-files`` still resolve.

A PRIVATE repo is cloned through an authenticated URL built from a freshly-minted installation
token: ``https://x-access-token:<token>@github.com/<owner>/<repo>``. The token rides ONLY in
that URL and is REDACTED from every recorded ``run_git`` argv + log line (the redaction is at
the record boundary inside :func:`premeeting.gitio.run_git`), so PM-CLONE-02 holds by
construction. Large repos clone blobless; nothing is ever pushed or executed.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from .exclusions import ExclusionManager
from .gitio import run_git
from .paths import repo_name_from_url, tenant_repo_dir

# Above this tracked-file count the clone goes blobless (``--filter=blob:none``) so a monster
# repo never drags the whole object store onto disk. A physics floor (Law 4), not model judgment.
_BLOBLESS_FILE_THRESHOLD = 100_000


def build_authenticated_url(repo_url: str, token: str | None) -> str:
    """Return the clone URL with the installation token injected as ``x-access-token`` userinfo.

    ``https://github.com/acme/repo`` + token → ``https://x-access-token:<token>@github.com/acme/repo``.
    A ``None`` token (a public repo / a test) returns the URL unchanged. Only ``https`` URLs are
    authenticated (an ``ssh``/``file`` URL is returned as-is)."""
    if not token:
        return repo_url
    parsed = urlparse(repo_url)
    if parsed.scheme != "https":
        return repo_url
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    netloc = f"x-access-token:{token}@{host}"
    return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))


class Cloner:
    """Bare + blobless(large) + read-only per-tenant clone, secret-scanned after landing."""

    def __init__(
        self,
        git_interceptor: Any = None,
        file_count_provider: Any = None,
        exclusion_manager: ExclusionManager | None = None,
        blobless_threshold: int = _BLOBLESS_FILE_THRESHOLD,
    ) -> None:
        self._interceptor = git_interceptor
        # Optional file-count seam: a duck-typed ``.count() -> int`` source. When present and
        # above the threshold the clone goes blobless; absent (the default), a normal clone.
        self._file_count = file_count_provider
        self._exclusions = exclusion_manager
        self._threshold = blobless_threshold
        self._by_url: dict[str, Path] = {}

    def _blobless(self) -> bool:
        if self._file_count is None:
            return False
        return int(self._file_count.count()) > self._threshold

    def clone(
        self,
        tenant_id: str,
        repo_url: str,
        sha: str | None = None,
        *,
        token: str | None = None,
    ) -> Path:
        """Clone ``repo_url`` into the per-tenant checkout, authenticated with ``token`` if given.

        Bare + read-only (no push, no ref-write, no repo-code execution — enforced by
        :func:`run_git`), blobless above the file threshold, materialised as a clean work-tree.
        Runs :meth:`ExclusionManager.scan_after_clone` so a secret file is stripped before any
        read. The token is injected only into the remote URL and is redacted from the recorded
        argv (PM-CLONE-02)."""
        repo_dir = tenant_repo_dir(tenant_id, repo_name_from_url(repo_url))
        gitdir = repo_dir / ".git"
        checkout = repo_dir / "checkout"
        if repo_dir.exists():
            shutil.rmtree(repo_dir, ignore_errors=True)
        checkout.mkdir(parents=True, exist_ok=True)

        auth_url = build_authenticated_url(repo_url, token)
        clone_args = ["clone", "--quiet", "--bare"]
        if self._blobless():
            clone_args.append("--filter=blob:none")
        clone_args += [auth_url, str(gitdir)]
        result = run_git(clone_args, self._interceptor, check=False)

        self._by_url[repo_url] = checkout
        if result.returncode != 0:
            # Unreachable upstream — the (redacted) clone argv is recorded; the empty checkout is
            # returned rather than raising so the pipeline can degrade to an honest not_ready.
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
        token: str | None = None,
        changed_files: list[str] | None = None,
    ) -> Path | None:
        """Delta-pull an EXISTING clone — a ``fetch`` (+ checkout), never a full re-clone (PM-CLONE-05).

        Fetches from the authenticated origin into the existing ``.git`` and fast-forwards the
        work-tree, then re-scans the changed set so a newly-added secret is excluded. Returns
        the checkout path, or ``None`` when no existing clone is known."""
        if clone_path is None and repo_url is not None:
            clone_path = self._by_url.get(repo_url)
        if clone_path is None:
            return None
        gitdir = self._gitdir_for(clone_path)
        # Re-point origin at the freshly-authenticated URL (a re-minted token) before fetching,
        # so the delta-pull uses a live credential; the URL set is redacted at the record boundary.
        if repo_url is not None:
            auth_url = build_authenticated_url(repo_url, token)
            run_git(
                ["--git-dir", str(gitdir), "remote", "set-url", "origin", auth_url],
                self._interceptor,
                check=False,
            )
        run_git(["--git-dir", str(gitdir), "fetch", "--quiet", "origin"], self._interceptor, check=False)
        # Advance the checked-out branch ref to the freshly-fetched tip, then materialise the
        # work-tree. A ``checkout FETCH_HEAD -- .`` alone updates FILES but not HEAD, so a
        # subsequent ``rev-parse HEAD`` would report the STALE sha — moving the branch ref first
        # keeps HEAD, the branch, and the work-tree all at the new commit (a fast-forward pull).
        branch = run_git(
            ["--git-dir", str(gitdir), "symbolic-ref", "--short", "HEAD"], self._interceptor, check=False
        )
        branch_name = branch.stdout.strip() or "main"
        run_git(
            ["--git-dir", str(gitdir), "update-ref", f"refs/heads/{branch_name}", "FETCH_HEAD"],
            self._interceptor,
            check=False,
        )
        run_git(
            ["--git-dir", str(gitdir), "--work-tree", str(clone_path), "checkout", "-f", branch_name, "--", "."],
            self._interceptor,
            check=False,
        )
        if self._exclusions is not None:
            self._exclusions.scan_after_pull(clone_path, changed_files)
        return clone_path
