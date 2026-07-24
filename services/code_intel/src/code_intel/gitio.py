"""The single git subprocess seam.

Every git invocation the clone/index/freshness paths make flows through
:func:`run_git`, which records the argv on an optional interceptor *before*
executing and hard-refuses any ``push`` (Proxy never writes to upstream, AC-M2-004)
and never executes repository-supplied code (AC-M2-005 — only git binaries run,
never a repo ``setup.py``/hook/Makefile).
"""
from __future__ import annotations

import logging
import subprocess  # noqa: S404 - git is the only binary invoked, argv-list form
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger("code_intel.git")


class Interceptor(Protocol):
    def record(self, args: Any) -> None: ...


def run_git(
    args: list[str],
    interceptor: Interceptor | None = None,
    cwd: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run ``git <args>`` (argv list), recording it first; never pushes."""
    if "push" in args:
        raise RuntimeError("code_intel never pushes to upstream (AC-M2-004)")
    argv = ["git", *args]
    if interceptor is not None:
        interceptor.record(argv)
    result = subprocess.run(  # noqa: S603 - fixed git binary, no shell, argv list
        argv, cwd=cwd, capture_output=True, text=True, check=False
    )
    if check and result.returncode != 0:
        logger.warning("git %s failed: %s", args[0] if args else "", result.stderr.strip()[:200])
    return result


def list_tracked_files(
    clone_path: Path, interceptor: Interceptor | None = None
) -> list[str] | None:
    """Return the repo-relative paths of every git-TRACKED file at the checkout,
    or ``None`` when the tracked set cannot be resolved (no ``.git`` / git error).

    This is the SINGLE source of truth for the "file universe" of a checkout —
    both the structural build walk (:meth:`GraphBuilder.build`) and the readiness
    coverage gate (:func:`pipeline._coverage_gate_ok`) enumerate this exact set,
    so ``indexed + flagged == len(tracked)`` holds by construction rather than
    incidentally (G8). ``git ls-files`` reports files relative to the work-tree
    TOP-LEVEL, so its paths align 1:1 with the ``rel = p.relative_to(clone_path)``
    keys used in the coverage record ONLY when ``clone_path`` is itself that
    top-level — which is exactly the production invariant (``Cloner`` materialises
    the checkout as the git work-tree). We therefore verify
    ``git rev-parse --show-toplevel`` resolves to ``clone_path`` and return
    ``None`` otherwise, so callers fall back to the on-disk walk. Guarding on the
    real work-tree (not merely ``parent/.git`` existing) means listing a *subdir*
    of a repo — which ``ls-files`` would report with repo-root-relative paths that
    do NOT rebase under the subdir — correctly degrades instead of silently
    indexing nothing.

    ``None`` (not ``[]``) signals *unavailable* so callers can degrade explicitly:
    an empty list means a real, empty tracked set.

    We let git DISCOVER the repo from inside ``clone_path`` (``git -C clone_path``)
    rather than assuming where ``.git`` lives — this handles both the production
    ``Cloner`` split layout (``.git`` beside the ``checkout`` work-tree, discovered
    by walking up) and an ordinary ``git clone`` (``.git`` inside the repo). We
    then require the discovered work-tree top-level to equal ``clone_path``.
    """
    if not clone_path.exists():
        return None
    top = run_git(
        ["-C", str(clone_path), "rev-parse", "--show-toplevel"],
        interceptor,
        check=False,
    )
    if top.returncode != 0:
        return None
    toplevel = top.stdout.strip()
    if not toplevel:
        return None
    try:
        same_root = Path(toplevel).resolve() == clone_path.resolve()
    except OSError:
        return None
    if not same_root:
        # ``clone_path`` is a subdir of (or unrelated to) the work-tree top-level;
        # ls-files paths would be repo-root-relative and not rebase here.
        return None
    res = run_git(
        ["-C", str(clone_path), "ls-files"],
        interceptor,
        check=False,
    )
    if res.returncode != 0:
        return None
    return [ln for ln in res.stdout.splitlines() if ln.strip()]
