"""libs.lint.naming — the user-visible-string naming lint (§14 AC-CON-002).

Enforces the naming law: user-visible strings never contain internal component
names (Orchestrator / Scribe / workroom). The product and the agent are Proxy.
The lint flags any internal name that leaks into a user-visible string and exits
non-zero, so CI blocks the leak.

CLI (``python -m lint.naming``): scans committed product source for internal names
in user-visible sink calls and exits non-zero on any leak. Runs beside the copy
guide (``python -m lint.copy_guide``) as a merge-blocking guard.
"""
from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Internal component names that must never surface in a user-visible string.
_INTERNAL_NAMES: tuple[str, ...] = ("Orchestrator", "Scribe", "workroom")
_INTERNAL_RX = re.compile(r"\b(?:%s)\b" % "|".join(_INTERNAL_NAMES), re.IGNORECASE)

# Repo root: libs/ops/src/lint/naming.py -> parents[4] == repo root.
_REPO_ROOT = Path(__file__).resolve().parents[4]
_SCAN_ROOTS: tuple[str, ...] = ("services", "libs")


@dataclass(frozen=True)
class LintResult:
    """Result of the naming lint. ``exit_code`` is non-zero on any violation."""

    exit_code: int
    violations: tuple[str, ...] = field(default_factory=tuple)


def check_user_visible_strings(mapping: dict[str, str]) -> LintResult:
    """Flag any user-visible string that contains an internal name.

    ``mapping`` is ``{string_key: user_visible_value}``. Returns a
    :class:`LintResult` whose ``exit_code`` is non-zero when any value contains
    an internal component name, and 0 otherwise.
    """
    violations = tuple(
        key for key, value in mapping.items() if _INTERNAL_RX.search(value)
    )
    return LintResult(exit_code=1 if violations else 0, violations=violations)


def _docstring_ids(tree: ast.Module) -> set[int]:
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


def scan_source(root: Path | None = None) -> list[str]:
    """Return ``path:line`` hits for internal names in committed user-visible copy.

    A "user-visible copy sink" is decided by the shared, receiver-aware classifier
    in ``lint.copy_guide`` (so a ``_log.warning(...)`` internal log line is never a
    false positive), keeping ONE definition of "user-visible" across both guards.
    """
    from .copy_guide import is_user_visible_sink

    base = root if root is not None else _REPO_ROOT
    hits: list[str] = []
    for b in _SCAN_ROOTS:
        d = base / b
        if not d.is_dir():
            continue
        for path in sorted(d.rglob("*.py")):
            if ".git" in path.parts or path.name == "naming.py":
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except (OSError, SyntaxError):
                continue
            docstrings = _docstring_ids(tree)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not is_user_visible_sink(node):
                    continue
                for arg in node.args:
                    if (
                        isinstance(arg, ast.Constant)
                        and isinstance(arg.value, str)
                        and id(arg) not in docstrings
                        and _INTERNAL_RX.search(arg.value)
                    ):
                        hits.append(f"{path}:{getattr(arg, 'lineno', 0)} {arg.value!r}")
    return hits


def check(root: Path | None = None) -> int:
    """Return 0 when clean; raise ``AssertionError`` on any leak (fail-closed)."""
    hits = scan_source(root)
    if hits:
        raise AssertionError("internal names in user-visible strings:\n  " + "\n  ".join(hits))
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for CI + pre-commit; exits non-zero on any leak."""
    _ = argv
    try:
        return check()
    except AssertionError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
