"""AC-REFM-03 / AC-REFM-03-NEG — the matcher reads ONLY graph_nodes + overview areas.

Static: the module names exactly one table (graph_nodes) in its SQL. No git walk,
no other table, no external index.
Simulation (real db, query logging): a db carrying graph_nodes AND unrelated
tables is queried; assert 0 queries touch any table other than graph_nodes/
sqlite_master.
"""
from __future__ import annotations

import ast
import inspect
import re
import sqlite3
from pathlib import Path

import scribe.referent as referent
from scribe.referent import OverviewArea, ReferentCorpus, lookup_referent


def _body_source() -> str:
    """The module's EXECUTABLE source, with every docstring stripped out.

    Docstrings legitimately *describe* out-of-scope things ("no git tree, no other
    table") to state the guarantee; the scope guard must look at code, not prose.
    """
    tree = ast.parse(inspect.getsource(referent))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body[0] = ast.Expr(value=ast.Constant(value=""))
    return ast.unparse(tree)


def test_only_graph_nodes_table_named_in_sql() -> None:
    src = _body_source()
    # The graph_nodes read is parameterised on a single module constant
    # (auditable — one table name, one place), so its FROM target unparses as the
    # constant name; the existence-probe reads sqlite_master. Every FROM/JOIN
    # target must be one of {the constant, graph_nodes literal, sqlite_master}.
    tables = re.findall(r"(?:FROM|JOIN)\s+\{?([A-Za-z_][A-Za-z0-9_]*)\}?", src)
    allowed = {"graph_nodes", "sqlite_master", "GRAPH_NODES_TABLE"}
    for t in tables:
        assert t in allowed, f"out-of-scope table referenced in SQL: {t!r}"
    # The one table constant the SELECT is bound to resolves to exactly graph_nodes.
    assert referent.GRAPH_NODES_TABLE == "graph_nodes"
    assert "GRAPH_NODES_TABLE" in tables or "graph_nodes" in tables


def test_no_git_walk_or_full_scan_constructs() -> None:
    # Scan executable code only — docstrings that mention "git tree" as a thing we
    # DON'T do must not trip the guard (the guarantee text is not a code construct).
    src = _body_source().lower()
    for bad in ["subprocess", "os.walk", "glob(", "rglob", "git.repo", "gitpython"]:
        assert bad not in src, f"out-of-scope filesystem/git construct: {bad!r}"


def _seed_multi_table(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE graph_nodes (node_id TEXT, area TEXT, file TEXT, symbol TEXT)")
        conn.execute(
            "INSERT INTO graph_nodes VALUES "
            "('payments/checkout.py::checkout', 'payments/checkout', 'payments/checkout.py', 'checkout')"
        )
        # An unrelated table whose rows would NAIVELY match the term "checkout".
        conn.execute("CREATE TABLE secrets (name TEXT)")
        conn.execute("INSERT INTO secrets VALUES ('checkout')")
        conn.execute("CREATE TABLE git_tree (path TEXT)")
        conn.execute("INSERT INTO git_tree VALUES ('checkout')")
        conn.commit()
    finally:
        conn.close()


def test_no_query_to_unrelated_tables_real_db(tmp_path: Path) -> None:
    """Real SQLite query logging: assert only graph_nodes/sqlite_master are queried."""
    db_path = tmp_path / "multi.db"
    _seed_multi_table(db_path)

    logged: list[str] = []
    orig_connect = sqlite3.connect

    def _tracing_connect(*args, **kwargs):  # type: ignore[no-untyped-def]
        conn = orig_connect(*args, **kwargs)
        conn.set_trace_callback(logged.append)
        return conn

    corpus = ReferentCorpus(areas=(OverviewArea(name="payments/checkout"),), db_path=str(db_path))
    sqlite3.connect = _tracing_connect  # type: ignore[assignment]
    try:
        result = lookup_referent("checkout", corpus)
    finally:
        sqlite3.connect = orig_connect  # type: ignore[assignment]

    assert result is not None  # it DID match, via graph_nodes
    joined = " ".join(logged).lower()
    assert "secrets" not in joined, f"queried unrelated table 'secrets': {logged}"
    assert "git_tree" not in joined, f"queried unrelated table 'git_tree': {logged}"
    for stmt in logged:
        low = stmt.lower()
        if "from" in low:
            assert ("graph_nodes" in low) or ("sqlite_master" in low), f"unexpected FROM: {stmt}"


def test_match_derived_solely_from_graph_nodes(tmp_path: Path) -> None:
    """The bound id is a real graph_nodes id — not the unrelated-table value."""
    db_path = tmp_path / "multi2.db"
    _seed_multi_table(db_path)
    corpus = ReferentCorpus(areas=(), db_path=str(db_path))
    result = lookup_referent("checkout", corpus)
    assert result == "payments/checkout.py::checkout"
