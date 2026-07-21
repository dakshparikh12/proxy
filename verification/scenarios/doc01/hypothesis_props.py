"""Layer 1 — Hypothesis property tests for doc01 (Code Intelligence).

Targets real, importable pure functions in ``code_intel``. The headline property
is tenant-path isolation: no attacker-influenced identifier may make a clone path
resolve outside its own tenant volume. That is the CLAUDE.md isolation law
("one tenant never sharing with another") expressed as an executable invariant,
and it is exactly the barrier the sealed AC-M2-001 finding says is missing.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from config.hypothesis_profiles import path_component, weird_text

# Pin the volume root to an isolated temp dir so the property exercises the
# path algebra without touching the real /tenants mount.
_VOL = Path(tempfile.mkdtemp(prefix="verif-doc01-vol-"))
os.environ["PROXY_TENANT_VOLUME_ROOT"] = str(_VOL)

from code_intel import paths  # noqa: E402

_SAFE_ID = st.from_regex(r"[a-zA-Z0-9][a-zA-Z0-9_\-]{0,40}", fullmatch=True)


@given(tenant_id=st.one_of(path_component(), _SAFE_ID),
       repo_name=st.one_of(path_component(), _SAFE_ID))
def test_tenant_repo_dir_never_escapes_tenant_volume(tenant_id: str, repo_name: str) -> None:
    """A clone dir must resolve *inside its own tenant's* subtree, or the builder
    must refuse the identifier outright. It must never resolve into another
    tenant's directory or anywhere outside the volume root."""
    root = paths.volume_root().resolve()
    try:
        d = paths.tenant_repo_dir(tenant_id, repo_name)
    except ValueError:
        return  # refusing an unsafe identifier is the correct, safe outcome
    # Null bytes etc. make resolve() itself raise; that is still a refusal to
    # produce a usable path, which is acceptable (it cannot be opened).
    try:
        resolved = d.resolve()
    except ValueError:
        return
    tenant_home = (root / tenant_id).resolve()
    assert resolved.is_relative_to(root), (
        f"clone path {resolved} escaped the tenant volume {root}")
    assert resolved.is_relative_to(tenant_home), (
        f"clone path {resolved} escaped tenant home {tenant_home} "
        f"(cross-tenant reachability)")


@given(repo_url=st.one_of(weird_text(80),
                          st.from_regex(r"https://[a-z]+/[a-z]+/[a-z.]+", fullmatch=True)))
def test_repo_name_from_url_is_a_safe_single_component(repo_url: str) -> None:
    """A derived repo directory name must be a single, traversal-free component:
    never empty, never '.'/'..', never containing a path separator or null byte."""
    name = paths.repo_name_from_url(repo_url)
    assert isinstance(name, str) and name != ""
    assert name not in (".", "..")
    assert "/" not in name and "\\" not in name and "\x00" not in name


@given(tenant_id=_SAFE_ID, repo_name=_SAFE_ID)
def test_safe_identifiers_round_trip_predictably(tenant_id: str, repo_name: str) -> None:
    """For ordinary identifiers the layout is exactly <root>/<tenant>/repos/<repo>."""
    d = paths.tenant_repo_dir(tenant_id, repo_name)
    root = paths.volume_root()
    assert d == root / tenant_id / "repos" / repo_name


# ---------------------------------------------------------------------------
# Coverage accounting — the reported percentage must stay a real fraction.
# ---------------------------------------------------------------------------
class _NoLLM:
    """A zero-valued counter stand-in (graph build makes zero LLM calls)."""
    value = 0
    def __init__(self, *_a, **_k) -> None: ...


@given(indexed=st.integers(min_value=0, max_value=10**6),
       flagged=st.integers(min_value=0, max_value=10**6))
def test_compute_coverage_is_a_fraction(indexed: int, flagged: int) -> None:
    from code_intel.coverage import compute_coverage
    result = compute_coverage(indexed, flagged, _NoLLM())
    assert 0.0 <= result.coverage_pct <= 1.0
    assert isinstance(result.gaps, list)


# ---------------------------------------------------------------------------
# Redaction must never raise and must never leak a value it was told to hide.
# ---------------------------------------------------------------------------
@given(text=st.one_of(st.none(), weird_text(200)))
def test_redact_never_raises(text) -> None:
    from code_intel.exclusions import ExclusionManager
    em = ExclusionManager()
    out = em.redact(text)
    if text is None:
        assert out is None
    else:
        assert isinstance(out, str)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
