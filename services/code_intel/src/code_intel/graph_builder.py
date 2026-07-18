"""Structural substrate build — tree-sitter-style declaration/edge extraction (M4).

The graph build is deterministic and model-free (AC-M4-004). Python declarations
and their call edges are extracted with the stdlib ``ast`` walker; Django models
become canonical ``table::<Name>`` nodes. Non-Python / grammarless files are
flagged (``unsupported-language``) but remain ripgrep-searchable. A synthetic
``graph_spec`` (from a fixture) can be loaded directly via :meth:`from_spec`.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .coverage import CoverageRow
from .graph import Edge, Graph, Node

_DJANGO_MARKERS = ("django.db", "from django")


@dataclass
class BuildResult:
    graph: Graph
    coverage_rows: list[CoverageRow] = field(default_factory=list)
    table_map: dict[str, str] = field(default_factory=dict)  # table_name -> ClassName


class _DeclVisitor(ast.NodeVisitor):
    def __init__(self, rel: str) -> None:
        self.rel = rel
        self.nodes: list[Node] = []
        self.edges: list[Edge] = []
        self.tables: dict[str, str] = {}
        self._func_stack: list[str] = []

    def _visit_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        node_id = f"{self.rel}::{node.name}"
        self.nodes.append(Node(id=node_id, path=self.rel, line=node.lineno, kind="function"))
        self._func_stack.append(node_id)
        self.generic_visit(node)
        self._func_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._visit_func(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._visit_func(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        if _is_model(node):
            table = _db_table(node)
            self.tables[table] = node.name
            self.nodes.append(
                Node(id=f"table::{node.name}", path=self.rel, line=node.lineno, kind="table")
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        if self._func_stack and isinstance(node.func, ast.Name):
            src = self._func_stack[-1]
            self.edges.append(Edge(source=src, target=node.func.id, kind="calls"))
        self.generic_visit(node)


def _is_model(node: ast.ClassDef) -> bool:
    for base in node.bases:
        text = ast.unparse(base) if hasattr(ast, "unparse") else ""
        if "Model" in text:
            return True
    return False


def _db_table(node: ast.ClassDef) -> str:
    for item in node.body:
        if isinstance(item, ast.ClassDef) and item.name == "Meta":
            for stmt in item.body:
                if isinstance(stmt, ast.Assign):
                    for tgt in stmt.targets:
                        if (
                            isinstance(tgt, ast.Name)
                            and tgt.id == "db_table"
                            and isinstance(stmt.value, ast.Constant)
                        ):
                            return str(stmt.value.value)
    return node.name.lower()


class GraphBuilder:
    def __init__(self, git_interceptor: Any = None) -> None:
        # The interceptor is accepted so the build shares the never-push seam; the
        # build itself runs no git and executes no repository code.
        self._interceptor = git_interceptor

    def build(
        self,
        clone_path: Path,
        is_excluded: Callable[[str], bool] | None = None,
    ) -> BuildResult:
        clone_path = Path(clone_path)
        nodes: list[Node] = []
        raw_edges: list[Edge] = []
        rows: list[CoverageRow] = []
        table_map: dict[str, str] = {}

        for p in sorted(clone_path.rglob("*")):
            if not p.is_file() or ".git" in p.parts:
                continue
            rel = str(p.relative_to(clone_path))
            if is_excluded is not None and is_excluded(rel):
                continue
            if p.suffix == ".py":
                parsed = _parse_python(p, rel)
                if parsed is None:
                    rows.append(CoverageRow(rel, "flagged", "parse-error"))
                    continue
                fnodes, fedges, ftables = parsed
                nodes.extend(fnodes)
                raw_edges.extend(fedges)
                table_map.update(ftables)
                rows.append(CoverageRow(rel, "indexed"))
            else:
                rows.append(CoverageRow(rel, "flagged", "unsupported-language"))

        graph = _assemble(nodes, raw_edges)
        return BuildResult(graph=graph, coverage_rows=rows, table_map=table_map)

    @classmethod
    def from_spec(cls, graph_spec: dict[str, Any]) -> Graph:
        nodes = [
            Node(
                id=n["id"],
                path=n.get("path", ""),
                line=int(n.get("line", 1)),
                kind=n.get("kind", "function"),
                pagerank=float(n["pagerank"]) if "pagerank" in n else 0.0,
            )
            for n in graph_spec.get("nodes", [])
        ]
        edges = [
            Edge(source=e["source"], target=e["target"], kind=e.get("kind", "calls"))
            for e in graph_spec.get("edges", [])
        ]
        graph = Graph(nodes=nodes, edges=edges)
        graph.index()
        if any("pagerank" in n for n in graph_spec.get("nodes", [])):
            # explicit fixture pageranks are authoritative
            pass
        else:
            graph.compute_pagerank()
        return graph


def _parse_python(path: Path, rel: str) -> tuple[list[Node], list[Edge], dict[str, str]] | None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, OSError):
        return None
    visitor = _DeclVisitor(rel)
    visitor.visit(tree)
    return visitor.nodes, visitor.edges, visitor.tables


def _assemble(nodes: list[Node], raw_edges: list[Edge]) -> Graph:
    """Resolve call-edge targets (bare names) to declared node ids."""
    by_name: dict[str, list[str]] = {}
    ids = {n.id for n in nodes}
    for n in nodes:
        by_name.setdefault(n.id.rsplit("::", 1)[-1], []).append(n.id)
    resolved: list[Edge] = []
    for e in raw_edges:
        if e.target in ids:
            resolved.append(e)
            continue
        candidates = by_name.get(e.target, [])
        # prefer a callee in a different file (cross-module call), else any.
        target_id = None
        src_file = e.source.rsplit("::", 1)[0]
        for cid in candidates:
            if cid.rsplit("::", 1)[0] != src_file:
                target_id = cid
                break
        if target_id is None and candidates:
            target_id = candidates[0]
        if target_id and target_id != e.source:
            resolved.append(Edge(source=e.source, target=target_id, kind=e.kind))
    graph = Graph(nodes=nodes, edges=resolved)
    graph.index()
    graph.compute_pagerank()
    return graph
