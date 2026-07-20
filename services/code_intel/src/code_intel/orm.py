"""Deterministic ORM / ownership analysis for the data-flow tools (M5).

``who_writes`` / ``shares_table`` recognise tier-1 ORM stacks (Django) and label
results ``resolved``; on any non-tier-1 stack they degrade to ``lower-bound`` and
never fabricate an exact answer (Law 2, AC-M5-005/006). ``owner`` resolves via
CODEOWNERS (``resolved``) then git-blame (``lower-bound``). All model-free.
"""
from __future__ import annotations

import ast
import fnmatch
from pathlib import Path

from .gitio import run_git
from .results import ModuleRef, OwnerResult, Writer

_WRITE_METHODS = {"create", "save", "delete", "update", "bulk_create", "get_or_create", "update_or_create", "insert"}
_READ_METHODS = {"all", "filter", "get", "first", "last", "count", "exists"}


def _py_files(clone_path: Path) -> list[Path]:
    return [p for p in sorted(clone_path.rglob("*.py")) if ".git" not in p.parts]


def is_tier1(clone_path: Path) -> bool:
    """True iff the clone is a Django-ORM stack, detected **structurally** — an actual
    ``import django`` / ``from django…`` statement in the AST, never a substring scan.

    Law 2 (never a silent wrong-exact): the previous ``"django.db" in text`` scan matched
    a stray "django" in a comment, docstring, requirements note, or migration history and
    then labelled ``who_writes`` results ``resolved`` on a repo that is not a Django stack.
    Comments/strings are absent from the AST, so an import-node check cannot be fooled by
    prose. SQLAlchemy and Rails ActiveRecord are tier-1 in the spec's support matrix, but
    their exhaustive write-path detection is **not** implemented in this module — so we do
    NOT report them ``resolved`` here (a Django-shaped, incomplete write set tagged
    ``resolved`` would itself be a silent wrong-exact). They fall through to ``lower-bound``,
    honestly labelled, until real per-ORM write detection lands (§4 tiering / §12.6).
    """
    for p in _py_files(clone_path):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and node.module.split(".")[0] == "django":
                    return True
            elif isinstance(node, ast.Import):
                if any(alias.name.split(".")[0] == "django" for alias in node.names):
                    return True
    return False


def _table_class_map(clone_path: Path) -> dict[str, str]:
    """Map ``db_table`` (and lowercased class name) -> model class name."""
    mapping: dict[str, str] = {}
    for p in _py_files(clone_path):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and any("Model" in ast.unparse(b) for b in node.bases):
                table = _db_table(node)
                mapping[table] = node.name
                mapping[node.name.lower()] = node.name
    return mapping


def _db_table(node: ast.ClassDef) -> str:
    for item in node.body:
        if isinstance(item, ast.ClassDef) and item.name == "Meta":
            for stmt in item.body:
                if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Constant):
                    if any(isinstance(t, ast.Name) and t.id == "db_table" for t in stmt.targets):
                        return str(stmt.value.value)
    return node.name.lower()


def _models_in_file(tree: ast.Module) -> set[str]:
    models: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and "models" in node.module:
            models.update(a.name for a in node.names)
        if isinstance(node, ast.ClassDef) and any("Model" in ast.unparse(b) for b in node.bases):
            models.add(node.name)
    return models


def _write_calls(func: ast.AST) -> list[tuple[str, str | None]]:
    """Return (write-method, table-string-literal-or-None) inside ``func``."""
    hits: list[tuple[str, str | None]] = []
    for node in ast.walk(func):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            if attr in _WRITE_METHODS:
                hits.append((attr, _table_literal(node)))
    return hits


def _table_literal(call: ast.Call) -> str | None:
    """For ``x.table('orders').insert(...)`` recover the 'orders' literal."""
    cur: ast.AST | None = call.func
    while isinstance(cur, ast.Attribute):
        cur = cur.value
        if isinstance(cur, ast.Call) and isinstance(cur.func, ast.Attribute) and cur.func.attr == "table":
            if cur.args and isinstance(cur.args[0], ast.Constant):
                return str(cur.args[0].value)
    return None


def who_writes(clone_path: Path, table: str) -> list[Writer]:
    tier1 = is_tier1(clone_path)
    table_map = _table_class_map(clone_path)
    model = table_map.get(table) or table_map.get(table.lower())
    confidence = "resolved" if tier1 else "lower-bound"
    writers: list[Writer] = []
    for p in _py_files(clone_path):
        rel = str(p.relative_to(clone_path))
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        file_models = _models_in_file(tree)
        for func in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            for method, table_lit in _write_calls(func):
                if _write_targets_table(method, table_lit, table, model, file_models):
                    writers.append(
                        Writer(
                            id=f"{rel}::{func.name}",
                            file=rel,
                            line=func.lineno,
                            confidence=confidence,
                        )
                    )
                    break
    return writers


def _write_targets_table(
    method: str,
    table_lit: str | None,
    table: str,
    model: str | None,
    file_models: set[str],
) -> bool:
    if table_lit is not None:
        return table_lit == table
    if model is not None and model in file_models:
        return True
    # non-tier-1 / untyped write against a matching table name in scope
    return model is None and bool(file_models) is False


def shares_table(clone_path: Path, table: str) -> list[ModuleRef]:
    tier1 = is_tier1(clone_path)
    table_map = _table_class_map(clone_path)
    model = table_map.get(table) or table_map.get(table.lower())
    confidence = "resolved" if tier1 else "lower-bound"
    modules: dict[str, ModuleRef] = {}
    if model is None:
        return []
    for p in _py_files(clone_path):
        rel = str(p.relative_to(clone_path))
        text = p.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        # a co-accessor references the model AND queries it (``.objects``)
        if _defines_model(tree, model):
            continue
        if model in text and f"{model}.objects" in text:
            top = rel.split("/", 1)[0]
            modules.setdefault(top, ModuleRef(id=top, confidence=confidence))
    return list(modules.values())


def _defines_model(tree: ast.Module, model: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == model:
            if any("Model" in ast.unparse(b) for b in node.bases):
                return True
    return False


def owner(clone_path: Path, path: str) -> OwnerResult:
    codeowners = clone_path / "CODEOWNERS"
    if codeowners.is_file():
        match = _match_codeowners(codeowners.read_text(encoding="utf-8", errors="replace"), path)
        if match is not None:
            return OwnerResult(owner=match, confidence="resolved", file="CODEOWNERS", line=None)
    # git-blame fallback — grounded but not authoritative
    blame = run_git(
        ["--git-dir", str(clone_path / ".git"), "log", "-1", "--format=%an", "--", path],
        check=False,
    )
    author = blame.stdout.strip() or "(unknown)"
    return OwnerResult(owner=author, confidence="lower-bound", file=path, line=1)


def _match_codeowners(text: str, path: str) -> str | None:
    result: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        pattern, owners = parts[0], parts[1:]
        if not owners:
            continue
        if _codeowners_match(pattern, path):
            result = owners[0]
    return result


def _codeowners_match(pattern: str, path: str) -> bool:
    if pattern.endswith("/"):
        return path.startswith(pattern)
    if pattern.startswith("/"):
        pattern = pattern[1:]
    base = path.rsplit("/", 1)[-1]
    return fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(base, pattern) or path.startswith(pattern)
