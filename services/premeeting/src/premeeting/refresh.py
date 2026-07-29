"""Push-triggered refresh: delta-pull → re-build → re-store → re-verify (PM-REFRESH-01).

The live GitHub push webhook (HMAC-verified upstream) drives this for THAT repo: the existing
clone is delta-pulled (a ``fetch``, never a full re-clone), the map is re-built on the updated
tree, re-stored at the new SHA, and re-verified. It reuses the same modules the first-pass
:mod:`premeeting.pipeline` uses — the ONLY difference is the delta-pull instead of a fresh clone.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .cloner import Cloner
from .exclusions import ExclusionManager
from .github_auth import AuthError, InstallationTokenMinter
from .gitio import head_sha
from .map_build import build_map
from .paths import repo_name_from_url, tenant_repo_dir
from .verify import verify_map


@dataclass
class RefreshResult:
    """The outcome of a push refresh — the honest re-verify verdict at the new SHA."""

    ready: bool
    repo: str
    sha: str = ""
    reasons: list[str] | None = None
    rebuilt: bool = False

    @property
    def status(self) -> str:
        return "ready" if self.ready else "not_ready"


async def refresh_on_push(
    *,
    tenant_id: str,
    repo_url: str,
    provider: Any,
    map_store: Any | None = None,
    minter: InstallationTokenMinter | None = None,
    installation_id: str | None = None,
    changed_files: list[str] | None = None,
    model: str = "claude-sonnet",
) -> RefreshResult:
    """Delta-pull the tenant's existing clone, re-build/store/verify the map (never raises out).

    Returns ``not_ready`` (honest) when there is no existing clone to delta-pull (a push before
    the first connect index) or when re-verify fails, naming the gap. Fake at ``provider`` (the
    model seam, D-032)."""
    repo = repo_name_from_url(repo_url)
    checkout = tenant_repo_dir(tenant_id, repo) / "checkout"
    if not checkout.exists():
        return RefreshResult(
            ready=False, repo=repo, reasons=["no existing clone to refresh (connect first)"]
        )

    token: str | None = None
    if minter is not None and installation_id:
        try:
            token = await minter.mint(installation_id)
        except AuthError as exc:
            return RefreshResult(ready=False, repo=repo, reasons=[f"auth: {exc}"])

    em = ExclusionManager()
    cloner = Cloner(exclusion_manager=em)
    updated = cloner.pull_delta(
        clone_path=checkout, repo_url=repo_url, token=token, changed_files=changed_files
    )
    if updated is None:
        return RefreshResult(ready=False, repo=repo, reasons=["delta-pull failed"])

    new_sha = head_sha(updated) or ""
    build = await build_map(
        provider=provider, clone_path=updated, repo_name=repo, sha=new_sha, model=model, exclusions=em
    )
    if map_store is not None:
        try:
            await map_store.save(tenant_id=tenant_id, repo=repo, sha=new_sha, map_text=build.index_md)
        except Exception as exc:  # noqa: BLE001 - a store fault is an honest not_ready reason
            return RefreshResult(
                ready=False, repo=repo, sha=new_sha,
                reasons=[f"store: {type(exc).__name__}"], rebuilt=True,
            )

    result = verify_map(build.index_md, updated, exclusions=em)
    return RefreshResult(
        ready=result.ready, repo=repo, sha=new_sha,
        reasons=None if result.ready else list(result.reasons), rebuilt=True,
    )


__all__ = ["RefreshResult", "refresh_on_push"]
