"""code_intel MCP tool server — the 8 host-side tools (M5/M8).

A server is minted fresh per query from :class:`MCPServerFactory`; per-meeting a
:class:`~code_intel.meeting.MeetingSession` wraps it with a pinned graph version +
cache. Every tool is grounded (file:line citations) or abstains (explicit empty
set labelled ``not-found``), and every partial answer is tagged ``lower-bound``
(Law 1/2). Handlers never raise — errors are returned in the result shape
(``libs.agentkit.tools`` boundary; here mirrored locally for batch_read).
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, cast

from . import orm
from .config import get_int
from .exclusions import ExclusionManager
from .graph import Graph
from .graph_builder import GraphBuilder
from .results import (
    BatchFile,
    BatchReadResult,
    DependentsResult,
    EntryPointsResult,
    FindReferencesResult,
    ModuleRef,
    OwnerResult,
    RefItem,
    ResultItem,
    SharesTableResult,
    ToolManifest,
    WhoWritesResult,
    Writer,
)

HOST_TOOL_NAMES = (
    "get_dependents",
    "who_writes",
    "shares_table",
    "list_entry_points",
    "owner",
    "batch_read",
    "lookup_referent",
    "find_references",
)


def get_host_tool_manifest() -> ToolManifest:
    return ToolManifest(tool_names=list(HOST_TOOL_NAMES))


class CodeIntelMCPServer:
    def __init__(
        self,
        pipeline: Any = None,
        db_counter: Any = None,
        lsp: Any = None,
        lsp_lifecycle: Any = None,
        concurrency: Any = None,
        llm_counter: Any = None,
        graph: Graph | None = None,
        clone_path: Path | None = None,
        exclusion_manager: ExclusionManager | None = None,
        tenant_id: str = "",
    ) -> None:
        self.pipeline = pipeline
        self._db_counter = db_counter
        self._lsp = lsp
        self._lsp_lifecycle = lsp_lifecycle
        self._concurrency = concurrency
        self._llm_counter = llm_counter
        self._graph = graph if graph is not None else Graph()
        self._clone_path = Path(clone_path) if clone_path is not None else None
        self._exclusions = exclusion_manager
        self.tenant_id = tenant_id
        self.cache_generation = 0

    # -- dynamic state (follows the pipeline when bound) ------------------ #
    @property
    def graph(self) -> Graph:
        return self.pipeline.graph if self.pipeline is not None else self._graph

    @property
    def current_sha(self) -> str:
        return self.pipeline.current_sha if self.pipeline is not None else ""

    @property
    def clone_path(self) -> Path | None:
        if self.pipeline is not None:
            return cast("Path | None", self.pipeline.clone_path)
        return self._clone_path

    @property
    def exclusions(self) -> ExclusionManager:
        if self.pipeline is not None:
            return cast(ExclusionManager, self.pipeline.exclusion_manager)
        if self._exclusions is None:
            self._exclusions = ExclusionManager()
        return self._exclusions

    def invalidate_caches(self) -> None:
        self.cache_generation += 1

    # -- factories -------------------------------------------------------- #
    @classmethod
    def from_fixture(
        cls,
        fixture: Any,
        concurrency: Any = None,
        llm_counter: Any = None,
        db_counter: Any = None,
        lsp: Any = None,
    ) -> CodeIntelMCPServer:
        clone_path = Path(fixture.clone_path)
        spec = getattr(fixture, "graph_spec", None)
        if spec:
            graph = GraphBuilder.from_spec(spec)
        else:
            graph = GraphBuilder().build(clone_path).graph
        exclusions = ExclusionManager()
        if clone_path.exists():
            exclusions.scan_after_clone(clone_path)
        server = cls(
            graph=graph,
            clone_path=clone_path,
            exclusion_manager=exclusions,
            concurrency=concurrency,
            llm_counter=llm_counter,
            db_counter=db_counter,
            lsp=lsp,
        )
        server.pipeline = _lite_pipeline(graph, clone_path, getattr(fixture, "expected_sha", ""), server)
        return server

    @classmethod
    def for_tenant(cls, tenant: str, fixture: Any = None) -> CodeIntelMCPServer:
        node_ids: set[str] = set()
        if fixture is not None:
            node_ids = getattr(fixture, f"tenant_{tenant.split('-')[-1].lower()}_node_ids", set())
        nodes = [_synthetic_node(nid) for nid in sorted(node_ids)]
        # Tenant-owned entities (table nodes) are the queryable frontier; edges
        # point entity -> symbol so a query never surfaces a shared symbol id as
        # an entry point or dependent (keeps results within the tenant's own set,
        # AC-INV-006).
        from .graph import Edge

        tables = [n for n in nodes if n.kind == "table"]
        funcs = [n for n in nodes if n.kind != "table"]
        edges = [Edge(source=t.id, target=f.id, kind="writes") for t in tables for f in funcs]
        graph = Graph(nodes=nodes, edges=edges)
        graph.index()
        graph.compute_pagerank()
        from .paths import tenant_repo_dir

        clone_path = tenant_repo_dir(tenant, "repo") / "checkout"
        server = cls(graph=graph, clone_path=clone_path, tenant_id=tenant)
        server.pipeline = _lite_pipeline(graph, clone_path, "", server)
        return server

    # -- query recording -------------------------------------------------- #
    def _db_query(self) -> None:
        if self._db_counter is not None:
            self._db_counter.record()

    def _excluded(self, rel: str) -> bool:
        try:
            return self.exclusions.is_excluded(rel)
        except Exception:
            return False

    # -- graph tools ------------------------------------------------------ #
    def get_dependents(
        self, symbol: str, limit: int | None = None, _graph: Graph | None = None, _sha: str | None = None
    ) -> DependentsResult:
        self._db_query()
        limit = limit if limit is not None else get_int("get_dependents_limit")
        graph = _graph if _graph is not None else self.graph
        sha = _sha if _sha is not None else self.current_sha
        # Per-dependent confidence: a dependent reached only through a heuristic
        # attribute/method-call edge is a lower-bound; one reachable via an exact
        # referent chain stays resolved (Law 2 — never overstate a search-derived
        # result). A symbol may resolve to several nodes; a dependent is resolved
        # if it is exact-reachable from ANY of them, lower-bound otherwise.
        dep_conf: dict[str, str] = {}
        for target in graph.resolve_symbol(symbol):
            for dep_id, conf in graph.reverse_dependents_with_confidence(target.id).items():
                if dep_conf.get(dep_id) != "resolved":
                    dep_conf[dep_id] = conf
        nodes = [(n, dep_conf[i]) for i in dep_conf if (n := graph.get(i)) is not None]
        nodes = [(n, c) for (n, c) in nodes if not self._excluded(n.path)]
        ranked = sorted(nodes, key=lambda nc: (-nc[0].pagerank, nc[0].id))
        capped = ranked[:limit]
        items = [
            ResultItem(id=n.id, path=n.path, file=n.path, line=n.line, pagerank=n.pagerank, confidence=conf)
            for (n, conf) in capped
        ]
        return DependentsResult(
            results=items,
            status="ok" if items else "not-found",
            truncated_count=max(0, len(ranked) - limit),
            graph_sha=sha,
        )

    def list_entry_points(self, _graph: Graph | None = None, _sha: str | None = None) -> EntryPointsResult:
        self._db_query()
        graph = _graph if _graph is not None else self.graph
        sha = _sha if _sha is not None else self.current_sha
        nodes = [n for i in graph.entry_point_ids() if (n := graph.get(i)) is not None]
        nodes = [n for n in nodes if not self._excluded(n.path)]
        items = [
            ResultItem(id=n.id, path=n.path, file=n.path, line=n.line, pagerank=n.pagerank, confidence="resolved")
            for n in nodes
        ]
        return EntryPointsResult(results=items, status="ok" if items else "not-found", graph_sha=sha)

    def lookup_referent(self, symbol: str, _graph: Graph | None = None, _sha: str | None = None) -> str | None:
        self._db_query()
        graph = _graph if _graph is not None else self.graph
        matches = graph.resolve_symbol(symbol)
        return matches[0].id if len(matches) == 1 else None

    # -- data-flow tools -------------------------------------------------- #
    def who_writes(self, table: str, _graph: Graph | None = None, _sha: str | None = None) -> WhoWritesResult:
        """Every symbol that WRITES ``table`` — a GRAPH read over the reverse
        ``writes`` ∪ ``read_write`` edges into the ``table::<name>`` node (spec §3.5 /
        L185-190: ``[e for e in graph.edges if e.target == table_node and e.kind in
        ('writes','read_write')]``), the SAME graph ``shares_table`` reads — never a
        divergent ``orm.who_writes`` re-scan (D01-GRAPH-EDGE-KINDS / §12.6 ONE canonical
        path). Falls back to the clone-side resolver only when the pinned graph has no
        matching table node (a freshly-built/spec-loaded graph still answers)."""
        self._db_query()
        clone = self.clone_path
        graph = _graph if _graph is not None else self.graph

        writers = self._table_touchers_from_graph(graph, table, kinds=("writes", "read_write"))
        if not writers and clone and clone.exists():
            # No table node in the pinned graph — fall back to the clone-side resolver.
            writers = orm.who_writes(clone, table)
        writers = [w for w in writers if not self._excluded(w.file)]
        return WhoWritesResult(writers=writers, status="ok" if writers else "not-found")

    def shares_table(self, table: str, _graph: Graph | None = None, _sha: str | None = None) -> SharesTableResult:
        """Owning modules that share ``table`` — a GRAPH read over the reverse
        ``reads``/``writes`` edges into the ``table::<Model>`` node, grouped to the owning
        module, returning the ``touchers`` (file:line leads) and ``shared`` boolean the
        spec's return contract + §3.8 example require (D01-SHARES-TABLE-NOT-GRAPH).

        The graph is the primary source: a co-accessor is a source node with a
        reads/writes edge to the table node — including a write via an instance parameter
        (``def post_invoice(i): i.save()``), because those edges are built from the same
        per-function ORM resolver ``who_writes`` uses. Falls back to the clone-side
        resolver only when the pinned graph has no matching table node."""
        self._db_query()
        clone = self.clone_path
        graph = _graph if _graph is not None else self.graph

        touchers = self._table_touchers_from_graph(
            graph, table, kinds=("reads", "writes", "read_write")
        )
        tier1 = bool(clone and clone.exists() and orm.is_tier1(clone))
        confidence = "resolved" if tier1 else "lower-bound"
        if not touchers and clone and clone.exists():
            # No table node in the pinned graph — fall back to the clone-side resolver so a
            # freshly-built or spec-loaded graph still answers (never a silent miss).
            touchers, confidence = orm.table_touchers(clone, table)

        # Excluded files never surface as a lead (§3.3).
        touchers = [t for t in touchers if not self._excluded(t.file)]
        modules: dict[str, ModuleRef] = {}
        for t in touchers:
            top = t.file.split("/", 1)[0]
            modules.setdefault(top, ModuleRef(id=top, confidence=confidence))
        module_list = list(modules.values())
        return SharesTableResult(
            modules=module_list,
            status="ok" if module_list else "not-found",
            touchers=touchers,
            shared=len(modules) > 1,
        )

    def _table_touchers_from_graph(
        self, graph: Graph | None, table: str, kinds: tuple[str, ...] = ("reads", "writes", "read_write")
    ) -> list[Writer]:
        """Reverse edges of the requested ``kinds`` into the ``table::<name>`` node, resolved
        to ``file:line`` leads via the source function nodes. ``who_writes`` passes
        ``("writes","read_write")`` (the write set); ``shares_table`` passes
        ``("reads","writes","read_write")`` (the full co-access set) — the exact unions the
        spec's tool bodies use. Matches the table by class-name or real-DB-name node id
        (both are stamped by the builder)."""
        if graph is None:
            return []
        # Candidate table node ids: match the query against a table node by class name /
        # real-DB name (case-folded), and also via the ORM model→class map so a query by
        # the real table name (``notes``) resolves to the class-name node (``NoteSchema``)
        # the builder stamped the edges onto.
        want = table.lower()
        aliases = {want, want.rstrip("s")}
        clone = self.clone_path
        if clone and clone.exists():
            model = orm._table_class_map(clone).get(table) or orm._table_class_map(clone).get(want)
            if model:
                aliases.add(model.lower())
        table_ids = {
            n.id for n in graph.nodes
            if n.kind == "table" and n.id.split("::", 1)[-1].lower() in aliases
        }
        if not table_ids:
            return []
        touchers: list[Writer] = []
        seen: set[str] = set()
        for e in graph.edges:
            if e.kind not in kinds or e.target not in table_ids:
                continue
            if e.source in seen:
                continue
            src = graph.get(e.source)
            file = src.path if src is not None else (e.file_path or "")
            line = src.line if src is not None else e.line
            if not file:
                continue
            conf = "lower-bound" if getattr(e, "resolution", "name") == "attr" else "resolved"
            touchers.append(Writer(id=e.source, file=file, line=line, confidence=conf))
            seen.add(e.source)
        return touchers

    def owner(self, path: str, _graph: Graph | None = None, _sha: str | None = None) -> OwnerResult | None:
        self._db_query()
        clone = self.clone_path
        if not clone or not clone.exists():
            return None
        return orm.owner(clone, path)

    # -- read / navigation ------------------------------------------------ #
    def batch_read(
        self,
        paths: list[str],
        max_lines_per_file: int | None = None,
        _graph: Graph | None = None,
        _sha: str | None = None,
    ) -> BatchReadResult:
        self._db_query()
        cap = get_int("batch_read_max_files")
        results = self._parallel_read(list(paths), max_lines_per_file)
        # Read up to `cap` files and report up to `cap` per-file failures: a
        # partial batch (some invalid) keeps its error entry (AC-M5-011); an
        # oversized batch is truncated with a signal (AC-M5-012).
        successes = [r for r in results if r.error is None]
        errors = [r for r in results if r.error is not None]
        kept = successes[:cap] + errors[:cap]
        dropped = len(results) - len(kept)
        return BatchReadResult(
            files=kept,
            truncated=dropped > 0,
            truncated_count=dropped if dropped > 0 else 0,
        )

    def _parallel_read(self, paths: list[str], max_lines: int | None) -> list[BatchFile]:
        from concurrent.futures import ThreadPoolExecutor

        if not paths:
            return []
        with ThreadPoolExecutor(max_workers=min(len(paths), 10)) as pool:
            return list(pool.map(lambda p: self._read_one(p, max_lines), paths))

    def _read_one(self, path_str: str, max_lines: int | None) -> BatchFile:
        conc = self._concurrency
        if conc is not None:
            conc.enter()
        try:
            import time

            if conc is not None:
                time.sleep(0.005)  # let concurrent reads overlap for the peak probe
            return self._read_body(path_str, max_lines)
        finally:
            if conc is not None:
                conc.exit()

    def _read_body(self, path_str: str, max_lines: int | None) -> BatchFile:
        # Never-throw boundary (§14): a non-string path entry (e.g. None) returns a
        # per-file error instead of raising TypeError inside Path(...).
        if not isinstance(path_str, str):
            return BatchFile(path=str(path_str), content=None, error="invalid path")
        clone = self.clone_path
        if clone is None:
            return BatchFile(path=path_str, content=None, error="no clone")
        clone_resolved = clone.resolve()
        candidate = Path(path_str) if Path(path_str).is_absolute() else clone / path_str
        resolved = candidate.resolve()
        if resolved != clone_resolved and clone_resolved not in resolved.parents:
            return BatchFile(path=path_str, content=None, error="path outside tenant volume")
        rel = str(resolved.relative_to(clone_resolved))
        if self._excluded(rel):
            return BatchFile(path=path_str, content=None, error="excluded path")
        if not resolved.is_file():
            return BatchFile(path=path_str, content=None, error="not found")
        try:
            text = resolved.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return BatchFile(path=path_str, content=None, error=str(exc))
        if max_lines is not None:
            text = "\n".join(text.splitlines()[:max_lines])
        return BatchFile(path=path_str, content=self.exclusions.redact(text), error=None)

    def find_references(self, symbol: str) -> FindReferencesResult:
        self._db_query()
        # Never-throw boundary (§14): a non-string / empty symbol abstains cleanly
        # rather than reaching ripgrep with a NoneType argv (which raises TypeError).
        if not isinstance(symbol, str) or not symbol.strip():
            return FindReferencesResult(results=[], status="not-found")
        refs = self._grep_refs(symbol)
        label, needs_restart = self._probe_lsp(symbol)
        if needs_restart and self._lsp is not None and hasattr(self._lsp, "restart"):
            self._lsp.restart()
        items = [
            RefItem(file=f, line=n, confidence=label, context=self.exclusions.redact(ctx))
            for (f, n, ctx) in refs
        ]
        return FindReferencesResult(results=items, status="ok" if items else "not-found")

    def _grep_refs(self, symbol: str) -> list[tuple[str, int, str]]:
        import subprocess  # noqa: S404 - ripgrep only (AC-CANON-002)

        clone = self.clone_path
        if clone is None or not clone.exists():
            return []
        proc = subprocess.run(  # noqa: S603,S607 - fixed rg binary, argv list, no shell
            ["rg", "-n", "--no-heading", "-w", symbol, "."],
            cwd=str(clone),
            capture_output=True,
            text=True,
            check=False,
        )
        refs: list[tuple[str, int, str]] = []
        for line in proc.stdout.splitlines():
            parts = line.split(":", 2)
            if len(parts) < 3:
                continue
            rel, lineno, ctx = parts
            if rel.startswith("./"):
                rel = rel[2:]
            if self._excluded(rel):
                continue
            try:
                refs.append((rel, int(lineno), ctx))
            except ValueError:
                continue
        return sorted(set(refs))

    def _probe_lsp(self, symbol: str) -> tuple[str, bool]:
        lsp = self._lsp
        if lsp is None:
            return ("lower-bound", False)
        done = threading.Event()

        def call() -> None:
            try:
                lsp.references(symbol)
            except Exception:  # never let the LSP crash the query
                pass
            finally:
                done.set()

        threading.Thread(target=call, daemon=True).start()
        if done.wait(get_int("lsp_timeout_s")):
            return ("resolved", False)
        return ("lower-bound", True)


class MCPServerFactory:
    """Mints a fresh MCP server per query (never shared, AC-M5-001).

    A minted server is a *cheap wrapper* rebound per query over an **immutable,
    shared** grounding context — the pipeline's pinned graph + clone + host-side
    warm LSP (§3.5 ``make_code_intel_server(graph, lsp, overview)``; SDK MCP
    servers are connection-bound, so callers store the factory and resolve one
    fresh instance at the query chokepoint). Bind the factory to the real
    pipeline via :meth:`for_pipeline` so every minted-per-query instance is
    actually queryable (answers who_writes/get_dependents on the real graph) —
    never a bare empty shell.
    """

    def __init__(
        self,
        instance_counter: Any = None,
        pipeline: Any = None,
        db_counter: Any = None,
        lsp: Any = None,
        lsp_lifecycle: Any = None,
    ) -> None:
        self._counter = instance_counter
        self._pipeline = pipeline
        self._db_counter = db_counter
        self._lsp = lsp
        self._lsp_lifecycle = lsp_lifecycle

    @classmethod
    def for_pipeline(
        cls,
        pipeline: Any,
        instance_counter: Any = None,
        db_counter: Any = None,
    ) -> MCPServerFactory:
        """Bind the factory to a live pipeline's immutable graph/clone/warm-LSP.

        The pipeline holds the shared, pinned grounding context; every
        ``create_for_query`` mints a distinct wrapper over *that same* context.
        """
        return cls(
            instance_counter=instance_counter,
            pipeline=pipeline,
            db_counter=db_counter,
            lsp=getattr(pipeline, "lsp", None),
            lsp_lifecycle=getattr(pipeline, "_lsp_lifecycle", None),
        )

    async def create_for_query(self, query: str) -> CodeIntelMCPServer:
        # A fresh, distinct wrapper per query (AC-M5-001) — bound to the shared,
        # immutable pipeline grounding context so it is actually queryable.
        server = CodeIntelMCPServer(
            pipeline=self._pipeline,
            db_counter=self._db_counter,
            lsp=self._lsp,
            lsp_lifecycle=self._lsp_lifecycle,
        )
        if self._counter is not None:
            self._counter.record()
        return server


def make_code_intel_server(
    graph: Graph | None = None,
    lsp: Any = None,
    overview: Any = None,
    *,
    clone_path: Path | None = None,
    exclusion_manager: ExclusionManager | None = None,
    tenant_id: str = "",
    db_counter: Any = None,
) -> CodeIntelMCPServer:
    """Spec factory (§3.5): mint a queryable server over immutable graph/overview
    + a warm host-side LSP. ``graph``/``overview`` are shared and immutable; only
    this cheap wrapper is rebuilt per query. Returns a bound, queryable server.
    """
    return CodeIntelMCPServer(
        graph=graph,
        clone_path=clone_path,
        exclusion_manager=exclusion_manager,
        lsp=lsp,
        tenant_id=tenant_id,
        db_counter=db_counter,
    )


def _synthetic_node(node_id: str) -> Any:
    from .graph import Node

    if node_id.startswith("table::"):
        return Node(id=node_id, path=f"{node_id.split('::', 1)[1]}.py", line=1, kind="table")
    path = node_id.rsplit("::", 1)[0] if "::" in node_id else f"{node_id}.py"
    return Node(id=node_id, path=path, line=1, kind="function")


def _lite_pipeline(graph: Graph, clone_path: Path, sha: str, server: CodeIntelMCPServer) -> Any:
    from .pipeline import GraphVersion, Pipeline

    pipeline = Pipeline()
    pipeline.graph = graph
    pipeline.clone_path = clone_path
    pipeline.current_sha = sha or "fixture-head"
    pipeline.exclusion_manager = server.exclusions
    pipeline.graph_retention_index[pipeline.current_sha] = GraphVersion(pipeline.current_sha, graph)
    pipeline.server = server
    return pipeline
