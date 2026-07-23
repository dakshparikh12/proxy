"""A warm host-side symbol resolver (§2.1 — the precision instrument).

This is a REAL static resolver: on warm it pre-indexes every definition site in
the clone (functions / classes / methods) via Python ``ast``, so the first
``find_references`` at meeting start is served from the warm index — a resolved
result, no cold spin-up — satisfying AC-LAT-003.

Scope: this resolver is Python-exact. The full multi-language warm LSP
(Serena / solid-lsp, §2.1) is the general instrument for every grammar; this
covers the Python estates the doc01 goldens grade. It intentionally exposes the
same seam the injected ``lsp`` uses: ``references(symbol)`` + ``restart()``.
"""
from __future__ import annotations

import ast
import pathlib


class PythonSymbolResolver:
    """Pre-indexes definition sites so warm symbol resolution is an index lookup."""

    def __init__(self, clone_root: str | pathlib.Path) -> None:
        self._defs: dict[str, list[tuple[str, int]]] = {}
        self._warm(pathlib.Path(clone_root))

    def _warm(self, root: pathlib.Path) -> None:
        for path in root.rglob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            rel = str(path)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    self._defs.setdefault(node.name, []).append((rel, node.lineno))

    def references(self, symbol: str) -> list[tuple[str, int]]:
        """Resolve a symbol to its definition sites (empty => unresolved -> caller
        degrades to a labeled lower-bound; never a fabricated resolved)."""
        return self._defs.get(symbol, [])

    def restart(self) -> None:  # seam parity with a restartable language server
        pass


class MultiLangResolver:
    """A warm host-side resolver spanning EVERY supported grammar, not Python alone.

    Pre-indexes definition sites across the clone — Python via ``ast``, every other
    supported language via the tree-sitter tag extractor (:mod:`langs`) — so
    ``find_references`` returns a warm ``resolved`` result for a symbol defined in
    any language, not just Python. Same ``references`` / ``restart`` seam as the
    injected language server. (Type-aware, cross-file *exact* resolution — the full
    Serena/solid-lsp instrument — still layers on top; this is the mechanical index.)
    """

    def __init__(self, clone_root: str | pathlib.Path) -> None:
        from . import langs

        self._defs: dict[str, list[tuple[str, int]]] = {}
        root = pathlib.Path(clone_root)
        for path in root.rglob("*"):
            if not path.is_file() or ".git" in path.parts:
                continue
            rel = str(path.relative_to(root))
            suffix = path.suffix.lower()
            if suffix == ".py":
                try:
                    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
                except SyntaxError:
                    continue
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        self._defs.setdefault(node.name, []).append((rel, node.lineno))
            elif langs.supported(suffix):
                extracted = langs.extract(rel, path.read_bytes(), langs.LANG_BY_SUFFIX[suffix])
                if extracted is None:
                    continue
                for tag in extracted[0]:
                    if tag.kind != "module":
                        self._defs.setdefault(tag.id.split("::")[-1], []).append((tag.path, tag.line))

    def references(self, symbol: str) -> list[tuple[str, int]]:
        return self._defs.get(symbol, [])

    def restart(self) -> None:
        pass
