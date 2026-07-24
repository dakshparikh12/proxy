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

from . import langs
from .coverage import CoverageRow
from .gitio import list_tracked_files
from .graph import Edge, Graph, Node

_DJANGO_MARKERS = ("django.db", "from django")


@dataclass
class BuildResult:
    graph: Graph
    coverage_rows: list[CoverageRow] = field(default_factory=list)
    table_map: dict[str, str] = field(default_factory=dict)  # table_name -> ClassName


def _module_name(rel: str) -> str:
    """Dotted module name for a source path relative to the build root.

    Mirrors ``tools/derive_goldens.py``: ``__init__.py`` collapses to its package
    (``flask/__init__.py`` → ``flask``; ``flask/app.py`` → ``flask.app``). The
    build root should be the source root (e.g. a ``src/`` dir) so names are the
    importable dotted paths, matching how imports reference them.
    """
    parts = list(Path(rel).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


class _DeclVisitor(ast.NodeVisitor):
    def __init__(self, rel: str) -> None:
        self.rel = rel
        self.module = _module_name(rel)
        self.is_init = Path(rel).name == "__init__.py"
        self.nodes: list[Node] = []
        self.edges: list[Edge] = []
        self.tables: dict[str, str] = {}
        self._func_stack: list[str] = []

    def _visit_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        node_id = f"{self.rel}::{node.name}"
        # A module-level def whose name does not start with '_' is public surface
        # (route/public symbol) → exported=1 (§3.4). Nested defs are not top-level.
        exported = 1 if (not self._func_stack and not node.name.startswith("_")) else 0
        self.nodes.append(
            Node(id=node_id, path=self.rel, line=node.lineno, kind="function", exported=exported)
        )
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
            # A table node is ALWAYS exported (part of the public surface, §3.4).
            # Canonical id is ``table::<ClassName>`` (AC-M4-008). Additionally stamp a
            # node keyed to the REAL DB table name so a schema-change lookup by the real
            # table (``table::shop_order`` — Django default ``<app_label>_<model>``, or an
            # explicit ``Meta.db_table``) also lands on a graph node. Both ids point at the
            # same declaration; the canonical class-name node is never removed.
            table_ids = [f"table::{node.name}"]
            real_table_id = f"table::{_real_table_name(node, self.rel)}"
            if real_table_id not in table_ids:
                table_ids.append(real_table_id)
            for tid in table_ids:
                self.nodes.append(
                    Node(
                        id=tid, path=self.rel, line=node.lineno,
                        kind="table", exported=1,
                    )
                )
        # A module-level class whose name does not start with '_' is public surface.
        exported = 1 if (not self._func_stack and not node.name.startswith("_")) else 0
        class_id = f"{self.rel}::{node.name}"
        self.nodes.append(
            Node(id=class_id, path=self.rel, line=node.lineno,
                 kind="class", exported=exported)
        )
        # extends / implements edges (§2.2 / §3.4 edge vocabulary,
        # D01-GRAPH-EDGE-KINDS): a subclass -> its base classes, so the class
        # hierarchy is part of the ONE graph and ``get_dependents(Base)`` returns
        # the transitive subclass blast radius over the extends/implements closure
        # (R-DOC01-3.5-02). The PRIMARY base is the ``extends`` (single-inheritance
        # spine); any additional bases are ``implements`` (mixed-in contracts /
        # interfaces). Targets are the trailing base name (``models.Model`` ->
        # ``Model``); ``_assemble`` binds it to the in-repo class node when one
        # exists and drops it (external base) otherwise — the same name resolution
        # every other edge kind uses. Tagged resolution="attr" for a qualified base
        # (``pkg.Base``) so a dependent reached only through a heuristically-bound
        # base is a lower-bound, never a silent wrong-exact (Law 2).
        for i, base in enumerate(node.bases):
            base_name, qualified = _base_name(base)
            if base_name is None:
                continue
            self.edges.append(
                Edge(
                    source=class_id,
                    target=base_name,
                    kind="extends" if i == 0 else "implements",
                    file_path=self.rel,
                    line=node.lineno,
                    resolution="attr" if qualified else "name",
                )
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        if self._func_stack:
            src = self._func_stack[-1]
            if isinstance(node.func, ast.Name):
                # Direct call `foo()` — an exact syntactic referent (resolution="name").
                self.edges.append(
                    Edge(source=src, target=node.func.id, kind="calls",
                         file_path=self.rel, line=node.lineno)
                )
            elif isinstance(node.func, ast.Attribute):
                # Method / qualified call `self.foo()` / `obj.method()` / `pkg.func()`
                # (ast.Attribute). Previously DROPPED — a systematic lower-bound on
                # the call graph (blast-radius under-report). Recover the trailing
                # attr name as the target (the same name-based resolution _assemble
                # already applies to import/name edges) and tag the edge
                # resolution="attr" so the tool boundary reports any dependent
                # reached through it as `lower-bound`, never `resolved` (Law 2:
                # a heuristic-derived edge may bind the wrong same-named symbol).
                self.edges.append(
                    Edge(source=src, target=node.func.attr, kind="calls",
                         file_path=self.rel, line=node.lineno, resolution="attr")
                )
        self.generic_visit(node)

    # -- import edges (spec §2.2/§3.4: `imports` edges + `module` nodes) ------- #
    # Emit an `imports` edge from this module to every imported module name;
    # _assemble keeps only the edges whose target is an in-repo module node
    # (external stdlib/third-party imports have no node and drop out) — the same
    # in-repo-only set tools/derive_goldens.py computes for the eval golden.
    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        if self.module:
            for a in node.names:
                self.edges.append(
                    Edge(source=self.module, target=a.name, kind="imports",
                         file_path=self.rel, line=node.lineno)
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        if not self.module:
            self.generic_visit(node)
            return
        if node.level and node.module is not None:
            # relative import: anchor on this module's package (derive_goldens semantics)
            this_pkg = self.module.split(".")
            keep = len(this_pkg) - node.level + (1 if self.is_init else 0)
            anchor = this_pkg[: max(keep, 0)]
            target = ".".join([*anchor, node.module])
            self.edges.append(
                Edge(source=self.module, target=target, kind="imports",
                     file_path=self.rel, line=node.lineno)
            )
        elif node.module and not node.level:
            self.edges.append(
                Edge(source=self.module, target=node.module, kind="imports",
                     file_path=self.rel, line=node.lineno)
            )
        self.generic_visit(node)


def _base_name(base: ast.expr) -> tuple[str | None, bool]:
    """The target class name for a base expression + whether it was qualified.

    ``BaseHandler`` -> ``("BaseHandler", False)`` (bare name, exact referent);
    ``models.Model`` / ``django.db.models.Model`` -> ``("Model", True)`` (qualified —
    the trailing attr is the class name, resolved heuristically like a method call);
    a subscripted / call base (``Generic[T]``, ``metaclass=...``) has no plain class
    name -> ``(None, False)`` (skipped). Mirrors the name-based resolution
    ``visit_Call`` uses for attribute-qualified targets so ``_assemble`` can bind it."""
    if isinstance(base, ast.Name):
        return base.id, False
    if isinstance(base, ast.Attribute):
        return base.attr, True
    return None, False


def _is_model(node: ast.ClassDef) -> bool:
    """A DB model whose class becomes a ``table::<Name>`` node — recognised STRUCTURALLY
    via the same detectors ``orm`` uses (Django ``models.Model`` base OR SQLAlchemy
    declarative ``Base`` subclass / ``__tablename__``), so the graph's table nodes and
    the data-flow tools' model→table map agree. A plain Pydantic ``BaseModel`` (whose base
    merely *contains* the substring "Model") is NOT a DB table and must not become a node."""
    from . import orm

    return orm._is_django_model_base(node) or orm._is_sqlalchemy_model(node)


def _explicit_db_table(node: ast.ClassDef) -> str | None:
    """The model's explicit ``Meta.db_table`` string literal, or ``None`` (default)."""
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
    return None


def _sqlalchemy_tablename(node: ast.ClassDef) -> str | None:
    """The SQLAlchemy declarative ``__tablename__ = "notes"`` literal, or ``None``."""
    for item in node.body:
        if isinstance(item, ast.Assign) and isinstance(item.value, ast.Constant):
            if any(isinstance(t, ast.Name) and t.id == "__tablename__" for t in item.targets):
                return str(item.value.value)
    return None


def _db_table(node: ast.ClassDef) -> str:
    explicit = _explicit_db_table(node) or _sqlalchemy_tablename(node)
    return explicit if explicit is not None else node.name.lower()


def _real_table_name(node: ast.ClassDef, rel: str) -> str:
    """The REAL DB table name for a Django model — the explicit ``Meta.db_table`` when
    set, else Django's default ``<app_label>_<model_lower>`` (``shop/models.py::Order``
    -> ``shop_order``). ``app_label`` is the app-package directory that holds the model's
    ``models`` module (mirrors ``orm._django_app_label`` so the graph node and the
    ``who_writes`` table map agree on the same real name)."""
    explicit = _explicit_db_table(node) or _sqlalchemy_tablename(node)
    if explicit is not None:
        return explicit
    parts = Path(rel).parts
    # parts[-1] is the file (``models.py`` / ``orders.py``); the app dir is the parent,
    # or the grandparent when the model lives in a ``models/`` package.
    if len(parts) >= 2 and parts[-2] == "models":
        app_label = parts[-3] if len(parts) >= 3 else parts[-2]
    elif len(parts) >= 2:
        app_label = parts[-2]
    else:
        app_label = ""
    return f"{app_label}_{node.name.lower()}" if app_label else node.name.lower()


class GraphBuilder:
    def __init__(self, git_interceptor: Any = None) -> None:
        # The interceptor is accepted so the build shares the never-push seam; the
        # build itself runs no git and executes no repository code.
        self._interceptor = git_interceptor

    def build(
        self,
        clone_path: Path,
        is_excluded: Callable[[str], bool] | None = None,
        built_at_sha: str = "",
    ) -> BuildResult:
        clone_path = Path(clone_path)
        nodes: list[Node] = []
        raw_edges: list[Edge] = []
        rows: list[CoverageRow] = []
        table_map: dict[str, str] = {}

        for p, rel in _file_universe(clone_path, self._interceptor):
            if is_excluded is not None and is_excluded(rel):
                # Excluded files (secrets/noise, §3.3) are still ACCOUNTED for in the
                # coverage record — flagged `excluded`, never silently dropped
                # (§3.4 invariant: indexed + flagged == every tracked file, which is
                # the readiness gate AC-M4-006/AC-M6-002).
                rows.append(CoverageRow(rel, "flagged", "excluded"))
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
            elif langs.supported(p.suffix):
                # Every other supported grammar → tree-sitter tag extraction (§2.2):
                # the graph is multi-language, not Python-only.
                extracted = langs.extract(rel, p.read_bytes(), langs.LANG_BY_SUFFIX[p.suffix.lower()])
                if extracted is None:
                    rows.append(CoverageRow(rel, "flagged", "parse-error"))
                    continue
                enodes, eedges = extracted
                nodes.extend(enodes)
                raw_edges.extend(eedges)
                rows.append(CoverageRow(rel, "indexed"))
            else:
                # Grammarless languages: flagged but ripgrep-searchable (§3.4).
                rows.append(CoverageRow(rel, "flagged", "unsupported-language"))

        # reads/writes edges into the ``table::<Model>`` nodes (§2.2 edge kinds /
        # D01-GRAPH-EDGE-KINDS): a co-accessor function -> the table it touches. Built
        # from the SAME per-function ORM resolver ``who_writes`` uses, so the graph edge
        # set and the data-flow tools agree by construction and an instance-param write
        # (``i.save()``) is a real edge, never a substring miss. Only for a tier-1 ORM
        # clone (the exact-supported stacks) — a non-ORM repo has no table nodes.
        # The ORM analysis is best-effort: it must never break the structural build,
        # so a failure here degrades to the pre-existing edge set (Law 1: the graph is
        # still sound, just without the data-flow edges) rather than raising.
        if table_map:
            try:
                raw_edges.extend(_table_access_edges(clone_path, table_map))
            except Exception:  # pragma: no cover - defensive; structural build must survive
                pass

        # Stamp every node with the commit it was extracted at (§3.4 freshness).
        for n in nodes:
            n.built_at_sha = built_at_sha
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


def _table_access_edges(clone_path: Path, table_map: dict[str, str]) -> list[Edge]:
    """``writes``/``reads`` edges from each touching function to the ``table::<Model>`` node.

    For every table in the build's ``table_map`` (real-DB-name and class-name keys both map
    to the class), enumerate the functions that write it (``kind="writes"``) and those that
    only read it (``kind="reads"``) via the ORM resolver, and emit an edge
    ``file::func -> table::<Model>``. The source id is the SAME ``file::func`` node id the
    declaration visitor stamped, and the target ``table::<Model>`` node already exists, so
    ``_assemble`` keeps these edges verbatim. Importing ``orm`` lazily keeps the pure-AST
    declaration pass import-light and avoids a cycle."""
    from . import orm

    # Use the ORM's authoritative model→table map (recognises SQLAlchemy ``__tablename__`` /
    # Rails pluralisation), not just the visitor's Django-style ``_db_table`` keys — so a
    # SQLAlchemy model whose real table name differs from its class name still gets edges.
    # Pick ONE representative table key per model class (prefer the real table name over the
    # class-name alias) so each toucher is enumerated once.
    class_to_key: dict[str, str] = {}
    for key, model in orm._table_class_map(clone_path).items():
        # Prefer a key that is NOT just the lowercased class name (the real table name).
        if model not in class_to_key or key.lower() != model.lower():
            class_to_key.setdefault(model, key)
            if key.lower() != model.lower():
                class_to_key[model] = key
    edges: list[Edge] = []
    for model in sorted(class_to_key):
        table_key = class_to_key[model]
        target = f"table::{model}"  # the class-name table node the visitor always stamps
        writer_ids = {w.id for w in orm.who_writes(clone_path, table_key)}
        reader_ids = orm.table_readers(clone_path, table_key)
        touchers, _conf = orm.table_touchers(clone_path, table_key)
        for t in touchers:
            writes = t.id in writer_ids
            reads = t.id in reader_ids
            # A function that BOTH reads and writes the same table gets ONE
            # ``read_write`` edge (spec §12.6 / L190,L203 kind-per-verb), never a
            # duplicate reads+writes pair; a pure writer -> ``writes``, a pure reader
            # -> ``reads``. ``who_writes`` (the graph read below) then unions
            # writes ∪ read_write, and ``shares_table`` unions reads ∪ writes ∪
            # read_write, exactly as the spec's tool bodies do.
            if writes and reads:
                kind = "read_write"
            elif writes:
                kind = "writes"
            else:
                kind = "reads"
            edges.append(Edge(source=t.id, target=target, kind=kind, file_path=t.file, line=t.line))
    return edges


def _file_universe(clone_path: Path, interceptor: Any) -> list[tuple[Path, str]]:
    """The set of files the build indexes/flags, as ``(abs_path, rel)`` pairs.

    Driven by the SAME ``git ls-files`` tracked set the readiness coverage gate
    counts against (single source of truth), so ``indexed + flagged ==
    len(tracked)`` is structurally guaranteed, never merely incidental — an
    untracked/generated file that lands in the checkout is outside this universe
    and can never inflate the count past the gate's denominator (G8).

    Degradation (identical to the gate's ``not tracked`` branch): when the tracked
    set is UNAVAILABLE (no ``.git`` / git failure — e.g. an unreachable-upstream
    clone that returned an empty checkout, or a synthetic fixture dir walked
    directly) we fall back to the on-disk walk so the build still classifies every
    present file rather than indexing nothing.
    """
    tracked = list_tracked_files(clone_path, interceptor)
    if tracked is not None:
        pairs: list[tuple[Path, str]] = []
        for rel in sorted(tracked):
            p = clone_path / rel
            # ls-files can list a tracked path that a checkout of a specific SHA
            # did not materialise (e.g. sparse/partial); skip absent/non-files so
            # the walk only classifies real on-disk files.
            if p.is_file():
                pairs.append((p, rel))
        return pairs
    return [
        (p, str(p.relative_to(clone_path)))
        for p in sorted(clone_path.rglob("*"))
        if p.is_file() and ".git" not in p.parts
    ]


def _parse_python(path: Path, rel: str) -> tuple[list[Node], list[Edge], dict[str, str]] | None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, OSError):
        return None
    visitor = _DeclVisitor(rel)
    visitor.visit(tree)
    nodes = visitor.nodes
    if visitor.module:  # a `module` node so imports/blast-radius resolve to it
        nodes = [Node(id=visitor.module, path=rel, line=1, kind="module"), *nodes]
    return nodes, visitor.edges, visitor.tables


def _assemble(nodes: list[Node], raw_edges: list[Edge]) -> Graph:
    """Resolve call-edge targets (bare names) to declared node ids."""
    by_name: dict[str, list[str]] = {}
    ids = {n.id for n in nodes}
    for n in nodes:
        by_name.setdefault(n.id.rsplit("::", 1)[-1], []).append(n.id)
    kind_by_id = {n.id: n.kind for n in nodes}
    resolved: list[Edge] = []
    for e in raw_edges:
        if e.target in ids:
            resolved.append(e)
            continue
        candidates = by_name.get(e.target, [])
        # A ``calls``/``extends``/``implements`` edge targets a CODE symbol (function /
        # class), NEVER a ``table::`` node — a bare name that matches both a class and its
        # ``table::<Name>`` node (an ORM model class ``Order`` + its table) must bind the
        # class, so a constructor call ``Order(...)`` / a base ``class X(Order)`` resolves
        # to the class, not spuriously to the table (which would forge a fake calls/extends
        # edge into the table node). Only the ORM-built reads/writes/read_write edges target
        # ``table::`` nodes, and those already carry a resolved ``table::`` target id.
        if e.kind in ("calls", "extends", "implements"):
            candidates = [c for c in candidates if kind_by_id.get(c) != "table"]
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
            resolved.append(
                Edge(source=e.source, target=target_id, kind=e.kind,
                     file_path=e.file_path, line=e.line, resolution=e.resolution)
            )
    graph = Graph(nodes=nodes, edges=resolved)
    graph.index()
    graph.compute_pagerank()
    return graph
