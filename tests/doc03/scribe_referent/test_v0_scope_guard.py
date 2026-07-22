"""AC-REFM-07 — no LLM-based referent resolution exists in the V0 referent flow.

Beyond the module-level static checks (test_no_llm.py), assert there is no feature
flag / conditional-import / dead-code path in the matcher that would activate an
LLM referent resolver in the V0 configuration.
"""
from __future__ import annotations

import ast
import inspect

import scribe.referent as referent


def test_no_feature_flag_gating_llm_referent() -> None:
    src = inspect.getsource(referent).lower()
    # No env/flag switch that would route to an LLM path.
    for bad in ["getenv", "environ", "feature_flag", "enable_llm", "use_llm", "if llm"]:
        assert bad not in src, f"suspicious V0-gating construct: {bad!r}"


def test_no_conditional_import_of_llm_sdk() -> None:
    """No import (top-level OR inside a function/if) pulls an LLM SDK."""
    tree = ast.parse(inspect.getsource(referent))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = (node.module or "").lower()
            assert "anthropic" not in mod and "openai" not in mod
        if isinstance(node, ast.Import):
            for alias in node.names:
                low = alias.name.lower()
                assert "anthropic" not in low and "openai" not in low


def test_no_todo_stub_gating_llm_referent() -> None:
    src = inspect.getsource(referent).lower()
    # A TODO/FIXME that promises an LLM referent path would be an overbuild stub.
    for line in src.splitlines():
        if "todo" in line or "fixme" in line:
            assert "llm" not in line and "agentic" not in line, f"LLM referent stub: {line!r}"
