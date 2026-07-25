"""lint.copy_guide — the user-visible-copy CI check (Doc 08 §2.1/§2.3).

A BUILD-TIME guard on user-visible copy, run in CI **alongside the naming lint**
(``lint.naming``). It is fail-closed: a banned pattern or a missing honesty shape
makes the check **exit non-zero and fail the build** — it never merely warns.

Two obligations, both read from ONE checked-in artifact (``copy_seeds.json`` — the
single source; never inline literals):

  1. §2.1 the copy voice — user-visible copy must never contain a BANNED pattern:
       * "As an AI…" self-reference,
       * filler ("Certainly!", "Great question!", …),
       * exclamation-theatre (the exclamation mark itself).
     Any hit -> non-zero exit, naming the offending ``file:line`` / string key.

  2. §2.3 bullet 11 — the three honesty shapes Proxy must be able to speak
     (recuse / unknown / partial) are present as CANONICAL SEED STRINGS in the
     artifact. A missing/renamed shape -> non-zero exit.

The artifact is the SINGLE SOURCE both obligations read: the banned regexes and the
honesty seed strings live there, not in this module. Edit the JSON, never the code.

CLI (guard parity — same invocation in ``.pre-commit-config.yaml`` and
``.github/workflows/guards.yml``, run right beside ``python -m lint.naming``)::

    python -m lint.copy_guide

exits non-zero if any committed user-visible string carries a banned pattern OR the
seed artifact is missing an honesty shape.

Scanning product source is AST-based (mirrors ``ops.check_banned_strings``): only
*live* string literals passed to user-visible sinks (``st.error``/``title``/…) are
scanned, so a docstring that legitimately quotes "As an AI" to record the ban is not
a false positive.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# The checked-in seed artifact — the SINGLE source of the banned patterns and the
# three honesty shapes. A path relative to the repo root (this file is
# libs/ops/src/lint/copy_guide.py -> parents[4] == repo root).
_THIS = Path(__file__).resolve()
_REPO_ROOT = _THIS.parents[4]
SEED_ARTIFACT_PATH = "libs/ops/src/lint/copy_seeds.json"

# Product trees whose committed user-visible strings the CLI scans.
_SCAN_ROOTS: tuple[str, ...] = ("services", "libs")

# User-visible sink calls: a string literal passed to one of these is user-visible
# copy (Streamlit surfaces, toasts, titles/labels/placeholders/help text, render
# frames). Two flavours, so a logger method (``_log.warning(...)`` — an INTERNAL log
# line, not user copy) is never a false positive:
#   * _ATTR_SINKS  — methods that are user-visible ONLY on a Streamlit-style receiver
#     (``st.error`` / ``st.warning`` / …). The name alone is ambiguous (``log.warning``
#     collides), so we additionally require a UI receiver and exclude logger receivers.
#   * _BARE_SINKS  — always user-visible (a toast / spoken line), receiver-independent.
# Mirrors the ``st.(...)``-scoped leak scan in tests/doc00/test_m12_con.py.
_ATTR_SINKS: frozenset[str] = frozenset(
    {
        "error", "warning", "info", "write", "markdown", "success", "caption",
        "title", "label", "placeholder", "help", "text", "subheader", "header",
    }
)
_BARE_SINKS: frozenset[str] = frozenset({"toast", "say", "speak"})
# Streamlit receiver roots (and layout containers derived from them).
_UI_RECEIVERS: frozenset[str] = frozenset(
    {"st", "sidebar", "col", "cols", "container", "tab", "expander", "placeholder", "empty"}
)
# Receiver substrings that mark an INTERNAL logger (never user-visible copy).
_LOGGER_HINTS: tuple[str, ...] = ("log", "logger", "logging")


# ── the seed artifact loaders (the single source) ────────────────────────────
def _resolve_seed_path(seed_path: Path | str | None) -> Path:
    if seed_path is None:
        seed_path = SEED_ARTIFACT_PATH
    p = Path(seed_path)
    if not p.is_absolute():
        p = _REPO_ROOT / p
    return p


def _load_seed(seed_path: Path | str | None = None) -> dict[str, Any]:
    raw: Any = json.loads(_resolve_seed_path(seed_path).read_text(encoding="utf-8"))
    return dict(raw) if isinstance(raw, dict) else {}


def load_honesty_shapes(seed_path: Path | str | None = None) -> dict[str, str]:
    """Return ``{shape_name: canonical_seed_string}`` from the artifact (single source)."""
    shapes: Any = _load_seed(seed_path).get("honesty_shapes", {})
    if not isinstance(shapes, dict):
        return {}
    return {str(k): str(v) for k, v in shapes.items()}


def _load_banned_patterns(seed_path: Path | str | None = None) -> list[tuple[str, re.Pattern[str]]]:
    """Compile the banned-pattern regexes from the artifact -> ``[(id, compiled)]``."""
    entries: Any = _load_seed(seed_path).get("banned_patterns", [])
    out: list[tuple[str, re.Pattern[str]]] = []
    for entry in entries if isinstance(entries, list) else []:
        item: dict[str, Any] = dict(entry) if isinstance(entry, dict) else {}
        flags = re.IGNORECASE if "i" in str(item.get("flags", "")) else 0
        out.append((str(item["id"]), re.compile(str(item["regex"]), flags)))
    return out


# The three honesty shapes Doc 08 §2.3 bullet 11 fixes — the exact key set the guard
# asserts the artifact carries.
REQUIRED_HONESTY_SHAPES: tuple[str, ...] = ("recuse", "unknown", "partial")


@dataclass(frozen=True)
class LintResult:
    """Result of a copy-guide check. ``exit_code`` is non-zero on any violation."""

    exit_code: int
    violations: tuple[str, ...] = field(default_factory=tuple)


# ── obligation 1 · banned patterns in user-visible copy ──────────────────────
def check_copy(mapping: dict[str, str], seed_path: Path | str | None = None) -> LintResult:
    """Flag any user-visible string that carries a banned pattern (fail-closed).

    ``mapping`` is ``{string_key: user_visible_value}``. Returns a :class:`LintResult`
    whose ``exit_code`` is non-zero when any value matches a banned pattern (naming
    the ``key`` and the pattern id), and 0 otherwise.
    """
    banned = _load_banned_patterns(seed_path)
    violations: list[str] = []
    for key, value in mapping.items():
        for pattern_id, rx in banned:
            if rx.search(value):
                violations.append(f"{key}: banned:{pattern_id}")
    return LintResult(exit_code=1 if violations else 0, violations=tuple(violations))


# ── obligation 2 · the three honesty shapes exist as seed strings ────────────
def check_honesty_shapes(seed_path: Path | str | None = None) -> LintResult:
    """Assert the three honesty shapes exist as canonical seed strings (fail-closed).

    Reads the shapes from the artifact (the single source). Non-zero exit when a
    required shape (recuse / unknown / partial) is missing or empty.
    """
    try:
        shapes = load_honesty_shapes(seed_path)
    except (OSError, ValueError) as exc:
        return LintResult(exit_code=1, violations=(f"seed artifact unreadable: {exc}",))
    missing = [
        name
        for name in REQUIRED_HONESTY_SHAPES
        if not str(shapes.get(name, "")).strip()
    ]
    violations = tuple(f"missing honesty shape: {name}" for name in missing)
    return LintResult(exit_code=1 if violations else 0, violations=violations)


# ── AST scan of committed product source for banned user-visible copy ────────
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


def _receiver_root(node: ast.expr) -> str | None:
    """The left-most name of an attribute chain (``st.sidebar.error`` -> 'st')."""
    cur: ast.expr = node
    while isinstance(cur, ast.Attribute):
        cur = cur.value
    return cur.id if isinstance(cur, ast.Name) else None


def is_user_visible_sink(call: ast.Call) -> bool:
    """True iff this call is a user-visible copy sink (not an internal logger)."""
    func = call.func
    if isinstance(func, ast.Name):
        # A bare call: only the always-visible sinks (toast/say/speak).
        return func.id in _BARE_SINKS
    if isinstance(func, ast.Attribute):
        method = func.attr
        if method in _BARE_SINKS:
            return True
        if method not in _ATTR_SINKS:
            return False
        # Ambiguous method (``warning``/``error``/…): user-visible ONLY on a
        # Streamlit-style receiver, and NEVER on a logger receiver.
        root = _receiver_root(func.value)
        if root is None:
            return False
        low = root.lower()
        if any(h in low for h in _LOGGER_HINTS):
            return False
        return root in _UI_RECEIVERS
    return False


def _iter_user_visible_literals(tree: ast.Module) -> list[tuple[int, str]]:
    """Yield ``(lineno, text)`` for string literals passed to a user-visible sink."""
    docstrings = _docstring_ids(tree)
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not is_user_visible_sink(node):
            continue
        for arg in node.args:
            if (
                isinstance(arg, ast.Constant)
                and isinstance(arg.value, str)
                and id(arg) not in docstrings
            ):
                out.append((getattr(arg, "lineno", 0), arg.value))
    return out


def _iter_py(root: Path) -> list[Path]:
    return [
        p
        for base in _SCAN_ROOTS
        if (d := root / base).is_dir()
        for p in sorted(d.rglob("*.py"))
        if ".git" not in p.parts and p.name != "copy_guide.py"
    ]


def scan_source(root: Path | None = None, seed_path: Path | str | None = None) -> list[str]:
    """Return ``path:line banned:<id>`` hits for banned patterns in committed copy."""
    base = root if root is not None else _REPO_ROOT
    banned = _load_banned_patterns(seed_path)
    hits: list[str] = []
    for path in _iter_py(base):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        for lineno, text in _iter_user_visible_literals(tree):
            for pattern_id, rx in banned:
                if rx.search(text):
                    hits.append(f"{path}:{lineno} banned:{pattern_id} {text!r}")
    return hits


# ── the fail-closed gate: honesty shapes + committed-copy scan ───────────────
def check(root: Path | None = None, seed_path: Path | str | None = None) -> int:
    """Return 0 when clean; raise ``AssertionError`` on any violation (fail-closed)."""
    problems: list[str] = []

    shapes_result = check_honesty_shapes(seed_path)
    problems.extend(shapes_result.violations)

    problems.extend(scan_source(root, seed_path))

    if problems:
        raise AssertionError("copy-guide violations:\n  " + "\n  ".join(problems))
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for CI + pre-commit; exits non-zero on any violation."""
    _ = argv
    try:
        return check()
    except AssertionError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
