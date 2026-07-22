"""AC-REFM-01 / AC-REFM-07 — lookup_referent makes NO LLM/external call, and no
LLM-based referent-resolution path exists in the matcher (Expansion scope guard).

Static-analysis oracle: scan the module source (the whole reachable surface of
lookup_referent lives in this one module) for any LLM API call, any call_external
seam use, or any agentic/llm referent construct.
"""
from __future__ import annotations

import ast
import inspect

import scribe.referent as referent

# Substrings that would betray a model call or the external-HTTP seam on any path.
FORBIDDEN_CALL_SUBSTRINGS = [
    "anthropic",
    "openai",
    "messages.create",
    "chat.completions",
    "call_external",
    "libs.http",
    "generate",  # generateStructured / model generate
    "llm_referent",
    "agentic_referent",
    "agentic referent",
]


def _module_source() -> str:
    return inspect.getsource(referent)


def test_no_llm_or_external_call_names_in_module_body() -> None:
    # Strip the module docstring: it *documents* these forbidden names to explain
    # the guarantee, so we scan only the executable body via the AST.
    tree = ast.parse(_module_source())
    if ast.get_docstring(tree) is not None:
        tree.body = tree.body[1:]
    body_src = ast.unparse(tree).lower()
    for bad in FORBIDDEN_CALL_SUBSTRINGS:
        assert bad not in body_src, f"forbidden LLM/external construct in body: {bad!r}"
    # sanity: the module obviously references sqlite (local), not the external seam.
    assert "sqlite3" in _module_source()


def test_no_call_to_llm_or_external_in_call_graph() -> None:
    """Walk every Call node in the module AST; none names an LLM API / seam."""
    tree = ast.parse(_module_source())
    called_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            called_names.append(ast.unparse(node.func).lower())
    joined = " ".join(called_names)
    for bad in ["anthropic", "openai", "call_external", "messages.create", "chat.completions"]:
        assert bad not in joined, f"call graph invokes forbidden target: {bad!r}"


def test_no_import_of_llm_or_http_seam() -> None:
    """The module imports neither an LLM SDK nor libs.http (the external seam)."""
    tree = ast.parse(_module_source())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    for mod in imported:
        low = mod.lower()
        assert "anthropic" not in low
        assert "openai" not in low
        assert not low.startswith("libs.http")
        assert "call_external" not in low


def test_no_agentic_referent_symbols_defined() -> None:
    """No function/class named as an agentic/LLM referent resolver (V0 scope)."""
    tree = ast.parse(_module_source())
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name.lower())
    for name in names:
        assert "llm" not in name, f"suspicious symbol name: {name!r}"
        assert "agentic" not in name, f"suspicious symbol name: {name!r}"
