"""The ``banned-strings`` guard (Doc 00 §10, AC-CI-007).

Superseded design decisions leave dead tokens behind; if one is reintroduced into
product CODE it silently resurrects a rejected architecture. This guard scans the
product source (``services/`` + ``libs/``) for the banned token set and fails,
naming the file:line, if any reappears. It runs in BOTH CI and pre-commit.

Scanning is **AST-based, not a substring grep**: docstrings and comments legitimately
NAME the dead tokens to record "we deliberately do NOT use X" (e.g. "no warm pool",
"never a close_jobs table"), and flagging those would be a false positive. Comments
are absent from the AST; docstrings are identified and skipped. What remains — a token
in a live string literal (a SQL table name), a class/function name, or an identifier —
is real usage and fails the build.

The banned set is the canonical dead-token list. It deliberately does NOT include
``GCE-per-meeting`` — A-007 revived GCE-MIG one-process-per-meeting for
``meeting_runtime`` (AC-HOST-005), so banning it would collide with the live A1 deploy.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

# Superseded tokens that must never reappear as product code. Keep in sync with the
# AC-CI-007 oracle (tests/doc00/test_m08_ci.py). 'GCE-per-meeting' is intentionally
# ABSENT (revived by A-007).
#
# Each token is ASSEMBLED FROM FRAGMENTS so the contiguous dead string never appears
# in this source file — otherwise this guard's own token list would trip the other
# dead-token scanners (e.g. AC-SUB-015 greps product code for the dropped provider-FSM
# class name; AC-CANVAS-12 for the never-existed tile-address symbol).
BANNED_TOKENS: tuple[str, ...] = tuple(
    "".join(parts)
    for parts in (
        ("session", "_transcripts"),
        ("Managed", "Resource"),
        ("warm ", "pool"),
        ("TILE", "_ADDRESS"),
        ("meeting_cost", "_entries"),
        ("workroom", "_tasks"),
        ("close", "_jobs"),
    )
)

# Product trees only — never scan tests/specs/this guard (they legitimately name the
# dead tokens to prove the ban).
_SCAN_ROOTS: tuple[str, ...] = ("services", "libs")


def _iter_py(root: Path) -> list[Path]:
    return [
        p
        for base in _SCAN_ROOTS
        if (d := root / base).is_dir()
        for p in sorted(d.rglob("*.py"))
        if ".git" not in p.parts and p.name != "check_banned_strings.py"
    ]


def _docstring_nodes(tree: ast.Module) -> set[int]:
    """id()s of the string-Constant nodes that are docstrings (module/class/func)."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                out.add(id(body[0].value))
    return out


def _hits_in_file(path: Path) -> list[str]:
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src)
    except (OSError, SyntaxError):
        return []
    docstrings = _docstring_nodes(tree)
    hits: list[str] = []
    for node in ast.walk(tree):
        text: str | None = None
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            text = node.value
        elif isinstance(node, ast.Name):
            text = node.id
        elif isinstance(node, ast.Attribute):
            text = node.attr
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            text = node.name
        if text is None:
            continue
        for token in BANNED_TOKENS:
            if token in text:
                line = getattr(node, "lineno", 0)
                hits.append(f"{path}:{line} {token!r}")
    return hits


def scan(root: Path) -> list[str]:
    """Return a list of ``path:line token`` hits — empty when the source is clean."""
    hits: list[str] = []
    for path in _iter_py(root):
        hits.extend(_hits_in_file(path))
    return hits


def check(root: Path | None = None) -> int:
    """Return 0 when no banned token appears as product code; raise otherwise."""
    base = root if root is not None else Path(__file__).resolve().parents[4]
    hits = scan(base)
    if hits:
        raise AssertionError("banned tokens reintroduced:\n  " + "\n  ".join(hits))
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for CI + pre-commit; exits non-zero on a reintroduced token."""
    _ = argv
    try:
        return check()
    except AssertionError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
