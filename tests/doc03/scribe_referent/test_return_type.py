"""AC-REFM-02 — lookup_referent returns node_id | area | None and NOTHING else.

Static oracle: inspect the annotation (Optional[str]) and every return statement
in lookup_referent's body; assert each returns a str-or-None (a name, subscript,
call resolving to str, or the None constant) — never a bool/list/dict literal.
Also a runtime tier over the REAL seeded db: every observed return is str|None.
"""
from __future__ import annotations

import ast
import inspect
import typing

import scribe.referent as referent
from scribe.referent import ReferentCorpus, lookup_referent


def _func_ast(name: str) -> ast.FunctionDef:
    tree = ast.parse(inspect.getsource(referent))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"function {name} not found")


def test_return_annotation_is_optional_str() -> None:
    hints = typing.get_type_hints(lookup_referent)
    ret = hints["return"]
    # Optional[str] == Union[str, None]
    assert ret == typing.Optional[str], f"return type is {ret!r}, expected Optional[str]"


def test_every_return_stmt_is_str_or_none() -> None:
    fn = _func_ast("lookup_referent")
    returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
    assert returns, "lookup_referent has no return statements"
    for r in returns:
        val = r.value
        # bare `return` or `return None`
        if val is None or (isinstance(val, ast.Constant) and val.value is None):
            continue
        # a string constant is fine
        if isinstance(val, ast.Constant):
            assert isinstance(val.value, str), f"non-str constant returned: {val.value!r}"
            continue
        # a Name / Attribute / Subscript / Call / IfExp is a str-valued expr here.
        assert isinstance(
            val, (ast.Name, ast.Attribute, ast.Subscript, ast.Call, ast.IfExp)
        ), f"unexpected return expr type: {ast.dump(val)}"
        # never a list/dict/set/tuple literal.
        assert not isinstance(val, (ast.List, ast.Dict, ast.Set, ast.Tuple))


def test_runtime_returns_are_str_or_none(corpus: ReferentCorpus) -> None:
    for term in ["checkout", "login", "issue_refund", "payments/checkout", "nope_xyz", ""]:
        result = lookup_referent(term, corpus)
        assert result is None or isinstance(result, str)
        assert not isinstance(result, bool)


def test_bound_value_is_real_and_not_a_container(corpus: ReferentCorpus) -> None:
    result = lookup_referent("checkout", corpus)
    assert isinstance(result, str)
    assert not isinstance(result, (list, dict, set, tuple, bool))
