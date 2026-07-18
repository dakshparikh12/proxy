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
