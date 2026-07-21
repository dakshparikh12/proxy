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
import re
import tempfile
from pathlib import Path

_PROD_ROOT = Path("/tenants")


class UnsafePathComponent(ValueError):
    """A tenant/repo identifier that would escape its per-tenant volume.

    Raised instead of silently returning a path that resolves outside the
    tenant subtree — the isolation law forbids one tenant's identifiers from
    reaching another tenant's (or the host's) filesystem.
    """


def _safe_component(name: str, *, kind: str) -> str:
    """Validate a single path component used to build a per-tenant clone path.

    A component must be a plain name — no separators, no traversal, no null
    bytes, not absolute, and not the ``.``/``..`` specials. Anything else could
    make ``<root>/<tenant>/repos/<repo>`` resolve into a sibling tenant's tree or
    outside the volume entirely, so it is refused at the boundary.
    """
    if not isinstance(name, str) or name == "":
        raise UnsafePathComponent(f"empty {kind}")
    if "\x00" in name:
        raise UnsafePathComponent(f"{kind} contains a null byte")
    if name in (".", ".."):
        raise UnsafePathComponent(f"{kind} is a path-traversal special: {name!r}")
    if "/" in name or "\\" in name or os.sep in name or (os.altsep and os.altsep in name):
        raise UnsafePathComponent(f"{kind} contains a path separator: {name!r}")
    if Path(name).is_absolute():
        raise UnsafePathComponent(f"{kind} is an absolute path: {name!r}")
    return name


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
    """The per-tenant repo directory ``<root>/<tenant>/repos/<repo>``.

    Both identifiers are validated as single, traversal-free components so the
    returned path can never resolve outside ``<root>/<tenant>``. Unsafe input
    raises :class:`UnsafePathComponent` rather than yielding an escaping path.
    """
    safe_tenant = _safe_component(tenant_id, kind="tenant_id")
    safe_repo = _safe_component(repo_name, kind="repo_name")
    return volume_root() / safe_tenant / "repos" / safe_repo


_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def repo_name_from_url(repo_url: str) -> str:
    """Derive a stable, traversal-free repo directory name from a URL / path.

    The last path segment is sanitised to a single safe component: separators
    and null bytes are dropped, and traversal specials collapse to ``repo`` so
    the result is always a usable, non-escaping directory name.
    """
    name = repo_url.replace("\\", "/").rstrip("/").split("/")[-1]
    if name.endswith(".git"):
        name = name[:-4]
    name = _UNSAFE_NAME_CHARS.sub("", name)
    if name in ("", ".", ".."):
        return "repo"
    return name
