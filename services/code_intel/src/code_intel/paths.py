"""Per-tenant volume-root resolution.

Invariant 3 (amended per D-INV-03): each tenant's clone lives on its own
encrypted volume, one tenant never sharing volume/process/index with another —
rooted at ``/tenants/<tenant>/``. On the production ``code_intel`` host that mount
is writable; on a dev/CI host without it we fall back to a writable base while
preserving the same ``<root>/<tenant>/repos/<repo>`` shape so isolation semantics
still hold. The production path prefix is honoured whenever ``/tenants`` exists
and is writable (or ``PROXY_TENANT_VOLUME_ROOT`` is set).
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

_PROD_ROOT = Path("/tenants")


def volume_root() -> Path:
    """Return the writable per-tenant volume root.

    Prefers ``$PROXY_TENANT_VOLUME_ROOT`` then the canonical ``/tenants`` mount;
    falls back to a writable temp base when the canonical mount is unavailable.
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


def tenant_repo_dir(tenant_id: str, repo_name: str) -> Path:
    """The per-tenant repo directory ``<root>/<tenant>/repos/<repo>``."""
    return volume_root() / tenant_id / "repos" / repo_name


def repo_name_from_url(repo_url: str) -> str:
    """Derive a stable repo directory name from a clone URL / local path."""
    name = repo_url.rstrip("/").split("/")[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return name or "repo"
