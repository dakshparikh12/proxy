"""Regression guard — CANARY ESCAPE #1: ``owner()`` must never reveal ownership of an
excluded/secret path.

``CodeIntelMCPServer.owner(path)`` (mcp_server.py) applies the same ``self._excluded(path)``
filter every sibling tool (get_dependents / who_writes / shares_table / find_references) applies:
an excluded path never appears in ANY tool result (§3.3 / AC-M3-004). ``owner()`` was the one
sibling with the fix but NO guarding test — a future edit could delete the guard and every
existing test would still pass while ``owner()`` silently leaked the owner (and file path) of a
``.env`` / ``*.pem`` / ``credentials`` secret file.

This test asserts:
  * ``owner(<excluded/secret path>)`` returns ``None`` (the leak is closed), AND
  * ``owner(<normal source path>)`` still resolves to a non-None OwnerResult (the guard did not
    over-fire and break the happy path).

Deleting the ``if self._excluded(path): return None`` guard from ``owner()`` turns this RED.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.integration
def test_owner_returns_none_for_excluded_secret_path(tmp_path: Path) -> None:
    """owner() abstains on an excluded/secret path but still resolves a normal path."""
    from code_intel.exclusions import ExclusionManager
    from code_intel.mcp_server import CodeIntelMCPServer

    # A minimal fixture repo on disk: one normal source file + one secret file that the
    # exclusion policy (default secret globs + an explicit ``.env*`` policy glob) excludes.
    clone = tmp_path / "checkout"
    clone.mkdir()
    normal_rel = "app/handler.py"
    secret_rel = ".env.production"
    (clone / "app").mkdir()
    (clone / normal_rel).write_text("def handle():\n    return 1\n", encoding="utf-8")
    (clone / secret_rel).write_text("API_KEY=AKIAIOSFODNN7EXAMPLE\n", encoding="utf-8")

    # Mirror how tests/test_m3_exclusions.py sets up exclusions: an ExclusionManager scanned
    # over the clone with the policy globs, wired into the server alongside its clone_path.
    exclusions = ExclusionManager(policy_globs=[".env*"])
    exclusions.scan_after_clone(clone)

    server = CodeIntelMCPServer(clone_path=clone, exclusion_manager=exclusions)

    # Sanity: the fixture is actually configured so the secret path IS excluded and the
    # normal path is NOT — otherwise the assertions below would be vacuous.
    assert exclusions.is_excluded(secret_rel), "fixture misconfigured: secret path is not excluded"
    assert not exclusions.is_excluded(normal_rel), "fixture misconfigured: normal path is excluded"

    # THE GUARD: owner() must never reveal ownership of an excluded/secret path.
    assert server.owner(secret_rel) is None, (
        "owner() leaked ownership of an excluded/secret path — the §3.3 exclusion guard is gone"
    )

    # The guard must not over-fire: a normal source path still resolves to a real OwnerResult
    # (git-blame fallback yields at minimum a non-None '(unknown)' OwnerResult).
    normal_owner = server.owner(normal_rel)
    assert normal_owner is not None, "owner() over-fired and abstained on a normal (non-excluded) path"
