"""G8 · build-walk file-universe == the readiness gate's file-universe.

Gap G8-COVERAGE-GATE-VS-BUILD-WALK-SOURCE-MISMATCH: the readiness coverage gate
(``_coverage_gate_ok``) counts ``indexed + flagged`` against ``git ls-files``
(the *tracked* set), but ``GraphBuilder.build`` used to enumerate the file
universe by walking the filesystem (``clone_path.rglob('*')`` — *every on-disk
file*). Those two sets coincide only *incidentally* (the cloner materialises a
clean checkout with no untracked files). If ANY untracked/generated file ever
lands in the checkout before indexing, the walk would index/flag it, pushing
``indexed + flagged`` ABOVE ``len(tracked)`` so the ``==`` equality FAILS and the
repo is wrongly forced ``not_ready`` — even though every tracked file was fully
classified.

These tests drive the REAL entrypoint (``run_full_pipeline`` -> the real
``GraphBuilder``/coverage/readiness) on a REAL local git repo, injecting an
untracked file into the checkout exactly at the ``indexing`` transition (the same
instant the production build walk runs). The fix makes the build enumerate the
SAME ``git ls-files`` set the gate uses, so the equality is *structurally*
guaranteed rather than incidentally true.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from services.code_intel.paths import repo_name_from_url, tenant_repo_dir
from services.code_intel.pipeline import run_full_pipeline
from services.code_intel.readiness import ReadinessCollector
from tests.fixtures.repos import small_repo_fixture


class _InjectUntrackedOnIndexing(ReadinessCollector):
    """A readiness listener that drops an UNTRACKED file into the checkout the
    moment the pipeline transitions to ``indexing`` -- i.e. immediately before the
    real ``GraphBuilder.build`` walk runs (pipeline.py emits ``indexing`` then
    builds). This reproduces a generated/untracked artifact landing in the
    working tree without being tracked by git.
    """

    def __init__(self, checkout: Path, untracked_rel: str) -> None:
        super().__init__()
        self._checkout = checkout
        self._untracked_rel = untracked_rel
        self.injected = False

    def emit(self, state: str) -> None:
        super().emit(state)
        if state == "indexing" and not self.injected:
            target = self._checkout / self._untracked_rel
            target.parent.mkdir(parents=True, exist_ok=True)
            # A syntactically-valid Python file so, if the walk DID pick it up, it
            # would be counted as `indexed` (not merely flagged) -- making the
            # over-count unambiguous.
            target.write_text("def generated_symbol():\n    return 0\n")
            self.injected = True


def _checkout_for(tenant_id: str, repo_url: str) -> Path:
    return tenant_repo_dir(tenant_id, repo_name_from_url(repo_url)) / "checkout"


def _tracked(checkout: Path) -> list[str]:
    gitdir = checkout.parent / ".git"
    return subprocess.run(
        ["git", "--git-dir", str(gitdir), "ls-files"],
        capture_output=True, text=True, check=False,
    ).stdout.split()


def test_g8_untracked_file_at_index_time_does_not_break_readiness_equality():
    """An untracked file present in the checkout at index time must NOT be counted
    into ``indexed + flagged`` (which would exceed ``git ls-files`` and force
    ``not_ready``). The repo is fully classified w.r.t. its TRACKED files, so it
    must reach ``ready``."""
    tenant_id = "tenant-g8-untracked"
    fixture = small_repo_fixture()
    untracked_rel = "generated/_g8_untracked.py"

    checkout = _checkout_for(tenant_id, fixture.url)
    listener = _InjectUntrackedOnIndexing(checkout, untracked_rel)

    pipeline = run_full_pipeline(
        tenant_id=tenant_id,
        repo_url=fixture.url,
        readiness_listener=listener,
    )

    assert listener.injected, (
        "test precondition failed: the untracked file was never injected -- the "
        "'indexing' transition did not fire"
    )
    # The untracked file really is on disk at index time...
    assert (checkout / untracked_rel).exists(), "untracked file missing from checkout"
    # ...and it really is NOT tracked by git (so it is outside the gate's universe).
    tracked = _tracked(checkout)
    assert untracked_rel not in tracked, "precondition: file must be untracked"

    # The build must NOT have indexed/flagged the untracked file -> no coverage row.
    assert not pipeline.coverage_record.has_entry(untracked_rel), (
        f"untracked file {untracked_rel!r} was pulled into the coverage record -- "
        "the build walk is enumerating on-disk files, not the git ls-files universe"
    )

    # indexed + flagged must equal exactly the tracked count (the gate's universe).
    indexed = pipeline.coverage_record.count_by_status("indexed")
    flagged = pipeline.coverage_record.count_by_status("flagged")
    assert indexed + flagged == len(tracked), (
        f"coverage total {indexed + flagged} != tracked {len(tracked)} -- build walk "
        "and readiness gate disagree on the file universe (G8)"
    )

    # The gate therefore holds and the repo is READY (not wrongly not_ready).
    assert "ready" in listener.emitted_states, (
        f"expected 'ready'; got states {listener.emitted_states} -- an untracked "
        "file wrongly forced not_ready via the count mismatch (G8)"
    )
    assert "not_ready" not in listener.emitted_states, (
        "readiness wrongly reached 'not_ready' because an untracked file inflated "
        "indexed+flagged past the tracked count (G8)"
    )


def test_g8_every_coverage_row_is_a_tracked_file():
    """Structural guarantee: with the build driven by ``git ls-files``, EVERY row
    in the coverage record is a tracked file -- even when an untracked file is
    present on disk at index time. This is the invariant that makes
    ``indexed + flagged == len(tracked)`` hold by construction."""
    tenant_id = "tenant-g8-rows-tracked"
    fixture = small_repo_fixture()
    untracked_rel = "build_output/_g8_artifact.py"

    checkout = _checkout_for(tenant_id, fixture.url)
    listener = _InjectUntrackedOnIndexing(checkout, untracked_rel)

    pipeline = run_full_pipeline(
        tenant_id=tenant_id,
        repo_url=fixture.url,
        readiness_listener=listener,
    )
    assert listener.injected

    tracked = set(_tracked(checkout))

    row_paths = {r.path for r in pipeline.coverage_record.all_rows()}
    extraneous = row_paths - tracked
    assert not extraneous, (
        f"coverage record contains non-tracked paths {sorted(extraneous)} -- the "
        "build walk is not sourced from git ls-files (G8)"
    )
    # And no tracked file is silently dropped.
    missing = tracked - row_paths
    assert not missing, (
        f"tracked files with no coverage row {sorted(missing)} -- the build walk "
        "misses part of the git ls-files universe (G8)"
    )
