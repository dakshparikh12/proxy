"""The ``check-sdk-isolation-triad`` guard (Doc 00 §10, CANONICAL §11.11).

The lethal-trifecta containment floor: EVERY Claude-Agent-SDK ``query()`` call site
must set the SDK-isolation triad (tools land in the sandbox, not the host) and pin the
``SDK_LOCAL_TOOLS`` block-list — an automated check, never a manual re-audit. A single
untriaged ``query()`` is how repo/agent code reaches the host filesystem, so this gate
must FAIL the build on any bare call.

It is an AST scan of product code (``services/`` + ``libs/``) for calls to a bare
``query(...)`` (the SDK entrypoint). For every such call site the enclosing module must
carry the triad markers (``SDK_LOCAL_TOOLS`` block-list + the isolation options). With
zero real ``query()`` call sites (V0: the Workroom agent that will own them is a later
build), the gate passes honestly — nothing to triage — and it goes load-bearing the
moment the first ``query()`` lands without the triad.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

_SCAN_ROOTS: tuple[str, ...] = ("services", "libs")

# The triad markers a module hosting a query() site must carry (CANONICAL §11.11):
# the block-list constant plus the isolation options that keep tools in the sandbox.
_TRIAD_MARKERS: tuple[str, ...] = ("SDK_LOCAL_TOOLS", "disallowed_tools", "permission_mode")


def _iter_py(root: Path) -> list[Path]:
    return [
        p
        for base in _SCAN_ROOTS
        if (d := root / base).is_dir()
        for p in sorted(d.rglob("*.py"))
        if ".git" not in p.parts and p.name != "check_sdk_isolation_triad.py"
    ]


def _is_bare_query_call(node: ast.AST) -> bool:
    """True for a call written ``query(...)`` — the SDK entrypoint, not ``x.query(...)``
    (a method like a DB cursor's) and not a ``def query``."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "query"
    )


def _module_has_triad(src: str) -> bool:
    return all(marker in src for marker in _TRIAD_MARKERS)


def query_sites_missing_triad(root: Path) -> list[str]:
    """Return ``path:line`` for every bare ``query()`` call site whose module lacks the
    triad markers. Empty when every site is triaged (or none exist)."""
    offenders: list[str] = []
    for path in _iter_py(root):
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(src)
        except (OSError, SyntaxError):
            continue
        sites = [n for n in ast.walk(tree) if _is_bare_query_call(n)]
        if not sites:
            continue
        if not _module_has_triad(src):
            offenders.extend(f"{path}:{getattr(n, 'lineno', 0)}" for n in sites)
    return offenders


def check(root: Path | None = None) -> int:
    """Return 0 when every ``query()`` site carries the triad; raise otherwise."""
    base = root if root is not None else Path(__file__).resolve().parents[4]
    offenders = query_sites_missing_triad(base)
    if offenders:
        raise AssertionError(
            "SDK-isolation triad missing at query() sites:\n  " + "\n  ".join(offenders)
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for CI + pre-commit; exits non-zero on an untriaged query() site."""
    _ = argv
    try:
        return check()
    except AssertionError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
