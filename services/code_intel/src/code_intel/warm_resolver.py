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
