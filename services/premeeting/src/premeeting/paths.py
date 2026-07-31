"""Per-tenant volume-root resolution — the isolation triad's storage leg (PM-ISO-01).

Each tenant's clone + derived artifacts live under its own root ``<root>/<tenant>/`` — one
tenant NEVER sharing a directory with another. On the production host the ``/tenants`` mount
is writable; on a dev/CI host without it we fall back to a writable temp base while keeping
the same ``<root>/<tenant>/repos/<repo>`` shape so isolation semantics hold identically.

The path is ALWAYS rooted at the tenant id (:func:`tenant_repo_dir` / :func:`tenant_map_dir`),
so a resolver for tenant B can never name tenant A's directory — the cross-tenant read is
unrepresentable at the path layer (PM-ISO-01 / PM-STORE-02). A blank tenant id is refused
rather than silently rooting at the shared volume root.

The MAP has no on-disk mirror: Postgres (:mod:`premeeting.map_store`) is its durable source of
truth, and ``verify`` reads the in-memory map the same pipeline process just built — so there
is deliberately no ``tenant_map_dir``. This module roots only the regenerable CLONE cache.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

_PROD_ROOT = Path("/tenants")


def volume_root() -> Path:
    """Return the writable per-tenant volume root.

    Prefers ``$PROXY_TENANT_VOLUME_ROOT`` then the canonical ``/tenants`` mount; falls back
    to a writable temp base when the canonical mount is unavailable (dev/CI).
    """
    override = os.environ.get("PROXY_TENANT_VOLUME_ROOT")
    if override:
        return Path(override)
    try:
        _PROD_ROOT.mkdir(parents=True, exist_ok=True)
        if os.access(_PROD_ROOT, os.W_OK):
            return _PROD_ROOT
    except OSError:
        pass
    return Path(tempfile.gettempdir()) / "proxy-tenants"


def _tenant_root(tenant_id: str) -> Path:
    """The isolated per-tenant root ``<volume>/<tenant>``. A blank tenant id is refused so a
    path can never collapse to the SHARED volume root and read a sibling tenant's data."""
    if not tenant_id or not str(tenant_id).strip():
        raise ValueError("tenant_id is required — a blank tenant id would break isolation")
    return volume_root() / str(tenant_id)


def tenant_repo_dir(tenant_id: str, repo_name: str) -> Path:
    """The per-tenant repo directory ``<root>/<tenant>/repos/<repo>`` (clone cache)."""
    return _tenant_root(tenant_id) / "repos" / repo_name


def repo_name_from_url(repo_url: str) -> str:
    """Derive a stable repo directory name from a clone URL / local path."""
    name = repo_url.rstrip("/").split("/")[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return name or "repo"
