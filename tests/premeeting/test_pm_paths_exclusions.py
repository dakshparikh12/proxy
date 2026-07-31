"""paths.py + exclusions.py — tenant isolation at the path layer + the secret boundary.

Covers PM-ISO-01 (path layer): a tenant-B resolver can never name tenant-A's directory.
Covers the exclusions containment PM-CLONE-04 / PM-VERIFY-03 lean on (path exclusion +
value redaction + the collected secret-value set).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from premeeting import exclusions, paths


# ── paths / isolation ──────────────────────────────────────────────────────
def test_tenant_repo_dir_is_rooted_at_tenant() -> None:
    d = paths.tenant_repo_dir("tenant-a", "repo")
    assert "tenant-a" in d.parts
    assert d.name == "repo" and d.parent.name == "repos"


def test_repo_dir_is_under_repos_kind_dir() -> None:
    repo = paths.tenant_repo_dir("tenant-a", "r")
    assert repo.parent.name == "repos"
    # No on-disk map dir — Postgres is the map's source of truth.
    assert not hasattr(paths, "tenant_map_dir")


def test_pm_iso_01_path_layer_no_cross_tenant_dir() -> None:
    """A resolver for tenant B never yields a path under tenant A's root (isolation)."""
    a = paths.tenant_repo_dir("tenant-a", "repo").resolve()
    b = paths.tenant_repo_dir("tenant-b", "repo").resolve()
    assert a != b
    assert a not in b.parents and b not in a.parents
    assert "tenant-a" not in b.parts and "tenant-b" not in a.parts


def test_blank_tenant_id_is_refused_never_collapses_to_shared_root() -> None:
    for bad in ("", "   "):
        with pytest.raises(ValueError, match="tenant_id"):
            paths.tenant_repo_dir(bad, "repo")


def test_repo_name_from_url_strips_git_and_trailing_slash() -> None:
    assert paths.repo_name_from_url("https://github.com/acme/widget.git") == "widget"
    assert paths.repo_name_from_url("https://github.com/acme/widget/") == "widget"
    assert paths.repo_name_from_url("acme/thing") == "thing"


# ── exclusions / secret boundary ────────────────────────────────────────────
def test_secret_paths_are_excluded_after_clone(tmp_path: Path) -> None:
    clone = tmp_path / "checkout"
    clone.mkdir()
    (clone / ".env").write_text("API_KEY=supersecretvalue123\n", encoding="utf-8")
    (clone / "id_rsa").write_text("-----BEGIN KEY-----\n", encoding="utf-8")
    (clone / "app.py").write_text("print('hi')\n", encoding="utf-8")
    em = exclusions.ExclusionManager()
    em.scan_after_clone(clone)
    assert em.is_excluded(".env")
    assert em.is_excluded("id_rsa")
    assert not em.is_excluded("app.py")


def test_inline_secret_values_are_redacted_and_collected(tmp_path: Path) -> None:
    clone = tmp_path / "checkout"
    clone.mkdir()
    (clone / "config.py").write_text('API_KEY = "AKIAABCDEFGHIJKLMNOP"\n', encoding="utf-8")
    em = exclusions.ExclusionManager()
    em.scan_after_clone(clone)
    # the value was collected (verify uses this set)
    assert "AKIAABCDEFGHIJKLMNOP" in em.secret_values()
    # and redaction masks it on any read path
    redacted = em.redact('here is AKIAABCDEFGHIJKLMNOP in prose')
    assert redacted is not None and "AKIAABCDEFGHIJKLMNOP" not in redacted
    assert "[REDACTED]" in redacted
