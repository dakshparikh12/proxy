"""Static-analysis verifier — a first-class deliverable (Law 2 evidence gate).

Two surfaces:

* :class:`StaticAnalysisVerifier` — programmatic checks the canonical-contract
  tests run over the *product* tree (git-host calls confined to ``RepoProvider``,
  ripgrep-only text search, sqlite-not-postgres, no SHA-versioned tables).
* A CLI (``python -m services.code_intel.verifier <path>``) that scans an
  arbitrary build tree for planted invariant violations and exits non-zero with a
  diagnostic — the pre-build negative oracle reused by three negative builds
  (RepoProvider bypass, LLM-in-graph, fabricated ``resolved``).
"""
from __future__ import annotations

import ast
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

_PRODUCT_ROOT = "services/code_intel"
_PROVIDER_MODULES = {"repo_provider.py"}
# libraries that reach a git host directly (must be behind RepoProvider)
_GIT_HOST_HINTS = ("api.github.com", "github.com/api", "githubusercontent.com")
_GIT_HOST_LIBS = {"github", "PyGithub", "ghapi"}
_LLM_LIBS = {"anthropic", "openai", "cohere", "google.generativeai", "litellm"}
_LLM_CALL_ATTRS = {"create", "complete", "completions", "messages"}


@dataclass(frozen=True)
class Violation:
    file: str
    detail: str
    binary: str = ""


def _iter_py(root: Path) -> "Iterator[Path]":
    for p in sorted(root.rglob("*.py")):
        if ".git" in p.parts:
            continue
        yield p


def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, OSError):
        return None


def _string_constants(tree: ast.AST) -> list[str]:
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _imported_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            names.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            names.add(n.module)
    return names


class StaticAnalysisVerifier:
    """Programmatic invariant checks over the product source tree."""

    def __init__(self, root: str | Path = ".") -> None:
        self.root = Path(root)

    # -- git-host confinement (AC-M1-004) --------------------------------- #
    def find_git_host_calls_outside_provider(self) -> list[Violation]:
        tree_root = self.root / _PRODUCT_ROOT
        found: list[Violation] = []
        if not tree_root.exists():
            return found
        for path in _iter_py(tree_root):
            if path.name in _PROVIDER_MODULES or path.name == "verifier.py":
                continue
            tree = _parse(path)
            if tree is None:
                continue
            imports = _imported_names(tree)
            if imports & _GIT_HOST_LIBS:
                found.append(Violation(str(path), "direct git-host client import"))
                continue
            for s in _string_constants(tree):
                if any(h in s for h in _GIT_HOST_HINTS):
                    found.append(Violation(str(path), f"direct git-host URL {s!r}"))
                    break
        return found

    # -- import / subprocess scanning (AC-CANON-002/003) ------------------ #
    def find_imports_of(self, module: str) -> list[Violation]:
        found: list[Violation] = []
        for path in _iter_py(self.root):
            if ".venv" in path.parts or "site-packages" in path.parts:
                continue
            tree = _parse(path)
            if tree is None:
                continue
            for name in _imported_names(tree):
                if name == module or name.startswith(module + "."):
                    found.append(Violation(str(path), f"import of {module}"))
                    break
        return found

    def find_subprocess_calls_with(self, binary: str) -> list[Violation]:
        return [v for v in self._text_search_calls() if v.binary == binary]

    def find_all_text_search_calls(self) -> list[Violation]:
        return self._text_search_calls()

    def _text_search_calls(self) -> list[Violation]:
        """Subprocess invocations of a text-search binary in product code."""
        search_binaries = {"rg", "grep", "ag", "ack", "zoekt", "elasticsearch"}
        found: list[Violation] = []
        tree_root = self.root / _PRODUCT_ROOT
        if not tree_root.exists():
            return found
        for path in _iter_py(tree_root):
            tree = _parse(path)
            if tree is None:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                argv0 = _subprocess_argv0(node)
                if argv0 in search_binaries:
                    found.append(Violation(str(path), f"text-search subprocess {argv0!r}", binary=argv0))
        return found

    # -- SHA-versioned schema (AC-CANON-003) ------------------------------ #
    def find_sha_versioned_table_schema(self) -> list[Violation]:
        found: list[Violation] = []
        tree_root = self.root / _PRODUCT_ROOT
        if not tree_root.exists():
            return found
        for path in _iter_py(tree_root):
            tree = _parse(path)
            if tree is None:
                continue
            for s in _string_constants(tree):
                low = s.lower()
                if "create table" in low and ("_sha" in low or "sha_" in low or "versioned" in low):
                    found.append(Violation(str(path), f"SHA-versioned table schema {s!r}"))
        return found


def _subprocess_argv0(node: ast.Call) -> str | None:
    """If ``node`` is a subprocess call with a list first arg, return argv[0]."""
    func = node.func
    is_sub = (
        isinstance(func, ast.Attribute) and func.attr in {"run", "Popen", "call", "check_output", "check_call"}
    ) or (isinstance(func, ast.Name) and func.id in {"run", "Popen"})
    if not is_sub or not node.args:
        return None
    first = node.args[0]
    if isinstance(first, (ast.List, ast.Tuple)) and first.elts:
        head = first.elts[0]
        if isinstance(head, ast.Constant) and isinstance(head.value, str):
            return head.value
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value.split()[0] if first.value.split() else None
    return None


# --------------------------------------------------------------------------- #
# CLI — pre-build negative oracle                                             #
# --------------------------------------------------------------------------- #


def _scan_tree_for_violations(root: Path) -> list[str]:
    violations: list[str] = []
    for path in _iter_py(root):
        tree = _parse(path)
        if tree is None:
            continue
        rel = path.name
        imports = _imported_names(tree)
        strings = _string_constants(tree)

        # (a) RepoProvider bypass — direct git-host reach outside the provider.
        if path.name not in _PROVIDER_MODULES:
            if imports & (_GIT_HOST_LIBS | {"requests", "urllib", "urllib.request", "httpx", "http.client"}):
                if any(any(h in s for h in ("github.com", "api.github")) for s in strings):
                    violations.append(
                        f"VIOLATION: RepoProvider bypass — {rel} reaches the git host directly"
                    )

        # (b) LLM/model call inside the structural graph build.
        if "graph" in path.name or "index" in path.name or "build" in path.name:
            if imports & _LLM_LIBS:
                violations.append(
                    f"VIOLATION: LLM/model call in the structural graph build ({rel})"
                )
            else:
                for node in ast.walk(tree):
                    if isinstance(node, ast.Attribute) and node.attr in {"messages", "completions"}:
                        violations.append(
                            f"VIOLATION: LLM/model call in the structural graph build ({rel})"
                        )
                        break

        # (c) Fabricated absolute 'resolved' confidence with no support.
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == "resolved":
                if _is_hardcoded_confidence(tree, node):
                    violations.append(
                        f"VIOLATION: mislabeled confidence 'resolved' fabricated in {rel}"
                    )
                    break
    return violations


def _is_hardcoded_confidence(tree: ast.AST, target: ast.Constant) -> bool:
    """True when a ``'confidence': 'resolved'`` pair is a hard-coded literal."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if (
                    isinstance(k, ast.Constant)
                    and k.value == "confidence"
                    and isinstance(v, ast.Constant)
                    and v.value == "resolved"
                ):
                    return True
    return False


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: python -m services.code_intel.verifier <path>", file=sys.stderr)
        return 2
    root = Path(argv[1])
    if not root.exists():
        print(f"VIOLATION: path does not exist: {root}", file=sys.stderr)
        return 2
    violations = _scan_tree_for_violations(root)
    if violations:
        for v in violations:
            print(v)
        return 1
    print("OK: no violations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
