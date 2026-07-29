"""The single git subprocess seam — with token redaction at the record boundary.

Every git invocation the clone / delta-pull path makes flows through :func:`run_git`, which
records the argv on an optional interceptor *before* executing and hard-refuses any ``push``
(Proxy never writes to upstream) and never executes repository-supplied code (only git binaries
run — no repo ``setup.py`` / hook / Makefile).

**Token redaction is at the RECORD boundary (PM-CLONE-02).** The installation token rides inside
the clone URL argv element (``https://x-access-token:<token>@github.com/...``). The interceptor
is handed a REDACTED copy of the argv — the token is masked to ``x-access-token:***@`` — so the
raw token is never in the recorded argv nor in any log line derived from it. The real (un-redacted)
argv is used ONLY for the actual ``subprocess.run`` exec; it is never recorded and never logged.
"""
from __future__ import annotations

import logging
import re
import subprocess  # noqa: S404 - git is the only binary invoked, argv-list form
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger("premeeting.git")

# Mask the token in ``x-access-token:<token>@`` (the private-clone URL form) at the record
# boundary. Case-insensitive on the user; the token is any non-``@``/non-``/`` run.
_TOKEN_URL_RX = re.compile(r"(x-access-token:)[^@/\s]+(@)", re.IGNORECASE)
# The re.sub REPLACEMENT TEMPLATE that MASKS the token (``\1`` = ``x-access-token:``, ``\2`` =
# ``@``); ``***`` is the mask, never a credential. It is the OPPOSITE of a hardcoded secret —
# it is what strips one from the recorded argv. (B105 false positive.)
_REDACTED_TOKEN = r"\1***\2"  # nosec B105 - regex mask template, not a secret


class Interceptor(Protocol):
    def record(self, args: Any) -> None: ...


def redact_argv(args: list[str]) -> list[str]:
    """Return a copy of ``args`` with any embedded installation token masked.

    Masks the token in every ``x-access-token:<token>@`` occurrence so the recorded argv (and
    any log line built from it) never carries the live credential (PM-CLONE-02)."""
    return [_TOKEN_URL_RX.sub(_REDACTED_TOKEN, a) for a in args]


def run_git(
    args: list[str],
    interceptor: Interceptor | None = None,
    cwd: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run ``git <args>`` (argv list), recording a REDACTED copy first; never pushes."""
    if "push" in args:
        raise RuntimeError("premeeting never pushes to upstream")
    argv = ["git", *args]
    # The interceptor + any logger only ever see the REDACTED argv — the raw token never
    # crosses the record/log boundary. The real argv is used solely for the exec below.
    safe_argv = redact_argv(argv)
    if interceptor is not None:
        interceptor.record(safe_argv)
    result = subprocess.run(  # noqa: S603 - fixed git binary, no shell, argv list
        argv, cwd=cwd, capture_output=True, text=True, check=False
    )
    if check and result.returncode != 0:
        # Log the REDACTED first arg + a redacted stderr — never the raw argv/token.
        stderr = _TOKEN_URL_RX.sub(_REDACTED_TOKEN, result.stderr.strip()[:200])
        logger.warning("git %s failed: %s", safe_argv[1] if len(safe_argv) > 1 else "", stderr)
    return result


def list_tracked_files(
    clone_path: Path, interceptor: Interceptor | None = None
) -> list[str] | None:
    """Return every git-TRACKED file's repo-relative path at the checkout, or ``None`` when the
    tracked set cannot be resolved (no ``.git`` / git error / ``clone_path`` is not the work-tree
    top-level). ``None`` (not ``[]``) signals *unavailable* so callers can degrade explicitly."""
    if not clone_path.exists():
        return None
    top = run_git(["-C", str(clone_path), "rev-parse", "--show-toplevel"], interceptor, check=False)
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
        return None
    res = run_git(["-C", str(clone_path), "ls-files"], interceptor, check=False)
    if res.returncode != 0:
        return None
    return [ln for ln in res.stdout.splitlines() if ln.strip()]


def head_sha(clone_path: Path, interceptor: Interceptor | None = None) -> str | None:
    """The current HEAD sha at the checkout, or ``None`` when it cannot be resolved."""
    if not clone_path.exists():
        return None
    res = run_git(["-C", str(clone_path), "rev-parse", "HEAD"], interceptor, check=False)
    sha = res.stdout.strip()
    return sha or None
