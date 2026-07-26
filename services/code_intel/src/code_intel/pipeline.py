"""The end-to-end code_intel pipeline: connect → clone → scan → index → graph.

``run_full_pipeline`` drives the whole build and returns a :class:`Pipeline`
carrying every artifact the tools and freshness layers read. The pipeline also
owns per-SHA graph-version retention (M11): each active meeting pins a SHA and
keeps answering against that version while newer pushes advance the head; a
version is GC'd once no live meeting pins it.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .cloner import Cloner
from .config import get_int
from .coverage import CoverageRecord
from .exclusions import ExclusionManager
from .gitio import list_tracked_files, run_git
from .graph import Graph
from .graph_builder import GraphBuilder
from .graph_store import GraphStore
from .readiness import ReadinessRecord, now_indexed_at

if TYPE_CHECKING:  # pragma: no cover
    from .meeting import MeetingSession

# The graph smoke check (§3.7) samples this many known symbols and confirms each
# resolves to a real file:line through the live path before ``ready`` is granted.
_SMOKE_SAMPLE_SIZE = 25


@dataclass
class GraphVersion:
    sha: str
    graph: Graph


class Pipeline:
    def __init__(self) -> None:
        self.tenant_id: str = ""
        self.repo_url: str = ""
        self.clone_path: Path = Path()
        self.exclusion_manager: ExclusionManager = ExclusionManager()
        self.exclusion_set: set[str] = set()
        self.graph: Graph = Graph()
        self.coverage_record: CoverageRecord = CoverageRecord()
        self.readiness_record: ReadinessRecord | None = None
        self.graph_db_path: Path = Path()
        self.coverage_db_path: Path = Path()
        self.current_sha: str = ""
        self.graph_retention_index: dict[str, GraphVersion] = {}
        self.server: Any = None
        self.server_factory: Any = None
        # The warm HOST-SIDE precision resolver (§2.1 / §3.5 / CANONICAL §12.2):
        # a warm_resolver.MultiLangResolver pre-indexed over the pinned clone so
        # find_references answers 'resolved' the first query at meeting start. The
        # v0 seam is the AST/tree-sitter resolver; the full Serena/solid-lsp pool
        # (type-aware cross-file exact resolution) layers on top as a future
        # enhancement. NEVER runs in the E2B sandbox — host-side only.
        self.lsp: Any = None
        # ONE persistent webhook handler per (long-lived) host. Its bounded dedup
        # cache is per-host state, so it must outlive individual pushes — it is
        # created once here and reused, never re-minted per delivery (which would
        # defeat both dedup and its bound).
        self.webhook_handler: Any = None
        self._store: GraphStore | None = None
        self._table_map: dict[str, str] = {}
        self._live_sessions: list[MeetingSession] = []
        self._drift: Any = None
        self._num_commits_last: int = 1
        self._cloner: Any = None
        self._loc_provider: Any = None
        self._lsp_lifecycle: Any = None

    # -- graph versioning ------------------------------------------------- #
    def graph_for(self, sha: str) -> Graph:
        version = self.graph_retention_index.get(sha)
        return version.graph if version else self.graph

    def advance_to_sha(self, sha: str) -> None:
        if sha not in self.graph_retention_index:
            self.graph_retention_index[sha] = GraphVersion(sha, self.graph)
        self.current_sha = sha

    def register_pin(self, session: MeetingSession) -> None:
        self._live_sessions.append(session)

    def unregister_pin(self, session: MeetingSession) -> None:
        if session in self._live_sessions:
            self._live_sessions.remove(session)

    def _pinned_shas(self) -> set[str]:
        return {s.pinned_sha for s in self._live_sessions if s.pinned_sha}

    def gc(self) -> None:
        keep = self._pinned_shas() | {self.current_sha}
        for sha in list(self.graph_retention_index):
            if sha not in keep:
                del self.graph_retention_index[sha]

    # -- freshness -------------------------------------------------------- #
    def rebuild_graph(self, built_at_sha: str = "") -> Graph:
        # Prefer an explicit sha (the push's new HEAD); else re-resolve HEAD from
        # the clone so a rebuilt graph still stamps the commit it was built at.
        sha = built_at_sha or _resolve_head(self.clone_path) or self.current_sha
        builder = GraphBuilder()
        result = builder.build(self.clone_path, is_excluded=self._is_excluded, built_at_sha=sha)
        graph = result.graph
        self._table_map = result.table_map
        if self._store is not None:
            self._store.write_graph(graph, drop_first=True)
        return graph

    def apply_push(self, new_sha: str, num_commits: int = 1, pull: bool = True) -> None:
        # ``pull=False`` when the caller (the webhook handler) already pulled the
        # delta itself carrying the push's ``changed_files`` — re-pulling here
        # would be a redundant ``git fetch origin`` AND, worse, would re-scan with
        # ``changed_files=None``. Exactly one pull happens per push (AC-M7-008).
        if pull and self._cloner is not None and self.clone_path and self.clone_path.exists():
            self._cloner.pull_delta(self.clone_path)
        if self.clone_path and self.clone_path.exists():
            self.graph = self.rebuild_graph(built_at_sha=new_sha)
            # Re-warm the precision resolver on the new HEAD so find_references
            # answers 'resolved' for symbols the push added/moved — a stale warm
            # index would silently down-grade them to a grep lower-bound.
            self._rewarm_resolver()
        self.current_sha = new_sha
        self.graph_retention_index[new_sha] = GraphVersion(new_sha, self.graph)
        self._num_commits_last = num_commits
        self._warm_lsp_on_push()
        if self.server is not None:
            self.server.invalidate_caches()
        for session in list(self._live_sessions):
            session.on_repo_advanced(num_commits)

    def _warm_lsp_on_push(self) -> None:
        if self._loc_provider is None or self._lsp_lifecycle is None:
            return
        loc = self._loc_provider.count() if hasattr(self._loc_provider, "count") else 0
        if loc >= get_int("lsp_warm_loc_threshold") and hasattr(self._lsp_lifecycle, "mark_pushed"):
            self._lsp_lifecycle.mark_pushed()

    def _rewarm_resolver(self) -> None:
        """Re-index the warm precision resolver over the current clone HEAD.

        Rebuilding into a fresh instance means a query mid-rebuild still reads a
        consistent (old-but-complete) index — never a half-populated one — and
        both the pipeline-bound server and the per-query factory read
        ``pipeline.lsp`` live, so they pick up the new instance on the next query.
        A build failure leaves the previous warm index intact (degrade, never go
        dark). The resolver never runs in the sandbox — host-side only (§12.2).
        """
        new_lsp = _build_warm_resolver(self.clone_path)
        if new_lsp is not None:
            self.lsp = new_lsp

    def uninstall_delete(self) -> None:
        import shutil

        repo_dir = self.clone_path.parent if self.clone_path else None
        if repo_dir and repo_dir.exists():
            shutil.rmtree(repo_dir, ignore_errors=True)

    def _is_excluded(self, rel: str) -> bool:
        return rel in self.exclusion_set or self.exclusion_manager.is_excluded(rel)

    @classmethod
    def from_drift_fixture(cls, drift: Any) -> Pipeline:
        pipeline = cls()
        pipeline._drift = drift
        pipeline.current_sha = getattr(drift, "remote_tip", "") or ""
        return pipeline


def run_full_pipeline(
    tenant_id: str = "",
    repo_url: str = "",
    sha: str | None = None,
    policy_globs: list[str] | None = None,
    llm_call_counter: Any = None,
    db_operation_counter: Any = None,
    db_tracer: Any = None,
    db_counter: Any = None,
    loc_provider: Any = None,
    lsp_lifecycle: Any = None,
    readiness_listener: Any = None,
    git_interceptor: Any = None,
    simulate_coverage_gap: bool = False,
) -> Pipeline:
    pipeline = Pipeline()
    pipeline.tenant_id = tenant_id
    pipeline.repo_url = repo_url

    _emit(readiness_listener, "connecting")

    exclusions = ExclusionManager(policy_globs=policy_globs)
    pipeline.exclusion_manager = exclusions

    _emit(readiness_listener, "cloning")
    cloner = Cloner(git_interceptor=git_interceptor, exclusion_manager=exclusions)
    clone_path = cloner.clone(tenant_id=tenant_id, repo_url=repo_url, sha=sha)
    pipeline.clone_path = clone_path
    pipeline._cloner = cloner
    pipeline._loc_provider = loc_provider
    pipeline._lsp_lifecycle = lsp_lifecycle

    repo_dir = clone_path.parent
    pipeline.graph_db_path = repo_dir / "graph.db"
    pipeline.coverage_db_path = repo_dir / "coverage.db"

    pipeline.exclusion_set = exclusions.get_excluded_paths(clone_path)

    # Resolve HEAD BEFORE the build so every node is stamped with the commit it
    # was extracted at (§3.4 built_at_sha, the freshness-deference anchor).
    pinned_sha = _resolve_head(clone_path) or (sha or "")

    _emit(readiness_listener, "indexing")
    builder = GraphBuilder(git_interceptor=git_interceptor)
    build = builder.build(clone_path, is_excluded=pipeline._is_excluded, built_at_sha=pinned_sha)
    pipeline.graph = build.graph
    pipeline._table_map = build.table_map

    coverage = CoverageRecord(build.coverage_rows)
    pipeline.coverage_record = coverage

    store = GraphStore(pipeline.graph_db_path, db_tracer=db_tracer, db_operation_counter=db_operation_counter)
    # drop_first=True even on the INITIAL write (defense-in-depth, G6): graph_edges
    # uses a plain INSERT, so a stale graph.db surviving at repo_dir/graph.db would
    # accumulate duplicate edges + orphan nodes. The clean DB must be *guaranteed*
    # by the write itself — never merely a side effect of Cloner.clone()'s rmtree —
    # matching the drop-before-insert rebuild invariant (AC-M4-009).
    store.write_graph(build.graph, drop_first=True)
    pipeline._store = store
    _touch_coverage_db(pipeline.coverage_db_path, db_tracer)

    pipeline.current_sha = pinned_sha
    if pinned_sha:
        pipeline.graph_retention_index[pinned_sha] = GraphVersion(pinned_sha, build.graph)

    _warm_lsp(pipeline, loc_provider, lsp_lifecycle)

    # Prepare-ahead: build the warm HOST-SIDE precision resolver (§2.1 / §3.5 /
    # CANONICAL §12.2) over the pinned clone NOW, at build time — so the first
    # find_references at meeting start is served 'resolved' from a warm index, not
    # a cold spin-up that would down-grade to a grep lower-bound (AC-LAT-003). The
    # v0 seam is warm_resolver.MultiLangResolver (AST + tree-sitter); the full
    # Serena/solid-lsp pool (type-aware, cross-file exact) layers on top later.
    pipeline.lsp = _build_warm_resolver(clone_path)

    indexed = coverage.count_by_status("indexed")
    flagged = coverage.count_by_status("flagged")
    # The §3.7 gate is a conjunction: 100% classification (coverage) AND the graph
    # smoke check. A repo whose classification is complete but whose graph came back
    # empty / unresolvable is NOT joinable — withhold ``ready`` (never a silent join
    # over a broken graph). ``simulate_coverage_gap`` forces the coverage arm false.
    gate_ok = (
        _coverage_gate_ok(clone_path, indexed, flagged)
        and not simulate_coverage_gap
        and _graph_smoke_ok(pipeline.graph, indexed)
    )
    if gate_ok:
        pipeline.readiness_record = ReadinessRecord(
            indexed_at=now_indexed_at(),
            pinned_sha=pinned_sha,
            coverage_pct=(indexed / (indexed + flagged)) if (indexed + flagged) else 1.0,
        )
        _emit(readiness_listener, "ready")
    else:
        pipeline.readiness_record = ReadinessRecord(pinned_sha=pinned_sha)
        _emit(readiness_listener, "not_ready")

    # a server bound to the pipeline so meeting/webhook lifecycles share state
    from .mcp_server import CodeIntelMCPServer, MCPServerFactory

    pipeline.server = CodeIntelMCPServer(
        pipeline=pipeline, db_counter=db_counter, lsp=pipeline.lsp, lsp_lifecycle=lsp_lifecycle
    )
    # the per-query factory (§3.5): callers store this and mint one fresh,
    # queryable wrapper per query over the pipeline's immutable graph/clone/LSP.
    pipeline.server_factory = MCPServerFactory.for_pipeline(pipeline, db_counter=db_counter)

    # ONE persistent webhook handler bound to this long-lived host: its bounded
    # LRU dedup cache is the per-host recent-duplicate window (see
    # webhook_handler.WEBHOOK_DEDUP_MAXLEN). Reusing this single instance across
    # every delivered push is what keeps memory O(maxlen) instead of leaking.
    from .webhook_handler import WebhookHandler

    pipeline.webhook_handler = WebhookHandler(
        cloner=pipeline._cloner,
        pipeline=pipeline,
        server=pipeline.server,
        git_interceptor=git_interceptor,
    )
    return pipeline


def _emit(listener: Any, state: str) -> None:
    if listener is not None:
        listener.emit(state)


def _resolve_head(clone_path: Path) -> str | None:
    if not clone_path.exists():
        return None
    gitdir = clone_path.parent / ".git"
    res = run_git(["--git-dir", str(gitdir), "rev-parse", "HEAD"], check=False)
    sha = res.stdout.strip()
    return sha or None


def _coverage_gate_ok(clone_path: Path, indexed: int, flagged: int) -> bool:
    if not clone_path.exists():
        return False
    # Same ``git ls-files`` universe the build walk enumerates (single source of
    # truth, G8): the equality below is structural, not incidental. ``None`` /
    # empty tracked set (git unavailable, or a directly-walked fixture) degrades
    # to "anything classified is enough", matching the build's rglob fallback.
    tracked = list_tracked_files(clone_path)
    if not tracked:
        return indexed + flagged > 0
    return indexed + flagged == len(tracked)


def _graph_smoke_ok(graph: Graph, indexed: int) -> bool:
    """The §3.7 graph smoke check as a real gate (not a no-op, per the node risk).

    A repo with indexed (parsed) files MUST yield a queryable graph: known symbols
    resolve to a real ``file:line`` through the live resolution path. If ``indexed``
    is zero the build parsed nothing (a fully-flagged repo — e.g. only grammarless
    or generated files); that is a legitimately joinable classified repo with no
    symbols to smoke, so the smoke arm is vacuously satisfied. Otherwise the graph
    must be non-empty AND a deterministic sample of its nodes must each resolve to a
    node carrying a truthy ``path`` and a positive ``line`` through ``resolve_symbol``
    (the same live path the tools query). Deterministic and never-throw: a broken
    graph fails closed to ``not_ready`` (Law 1), never crashes the pipeline.
    """
    if indexed <= 0:
        return True
    nodes = graph.nodes
    if not nodes:
        return False
    # Deterministic sample: the first N nodes by id (stable ordering), each must
    # resolve to a concrete location through the live resolution path.
    sample = sorted(nodes, key=lambda n: n.id)[:_SMOKE_SAMPLE_SIZE]
    for node in sample:
        try:
            resolved = graph.resolve_symbol(node.id)
        except Exception:  # noqa: BLE001 - a raising resolver is itself a failed smoke
            return False
        if not resolved:
            return False
        if not all(getattr(r, "path", "") and getattr(r, "line", 0) for r in resolved):
            return False
    return True


def _warm_lsp(pipeline: Pipeline, loc_provider: Any, lsp_lifecycle: Any) -> None:
    if loc_provider is None or lsp_lifecycle is None:
        return
    loc = loc_provider.count() if hasattr(loc_provider, "count") else 0
    if loc >= get_int("lsp_warm_loc_threshold") and hasattr(lsp_lifecycle, "mark_connected"):
        lsp_lifecycle.mark_connected()


def _build_warm_resolver(clone_path: Path) -> Any:
    """Pre-index the warm HOST-SIDE precision resolver over the pinned clone.

    The v0 warm-resolver seam (§2.1) is ``warm_resolver.MultiLangResolver``: a
    real static resolver that pre-indexes every definition site (Python via
    ``ast``, every other grammar via tree-sitter tags) so ``find_references`` at
    meeting start is a warm index lookup tagged ``resolved`` — never a cold spin.
    The full Serena/solid-lsp language-server pool (type-aware, cross-file exact
    resolution) is a documented FUTURE ENHANCEMENT that layers on this same
    ``references`` / ``restart`` seam.

    Never-throw + degrade-honestly: a missing clone or a resolver build failure
    returns ``None`` (the caller keeps any prior warm index, or find_references
    falls back to the grep lower-bound) — the pipeline never crashes and never
    goes dark on a resolver hiccup (Law 1 / §3.8 honest-failure).
    """
    if clone_path is None or not clone_path.exists():
        return None
    from .warm_resolver import MultiLangResolver

    try:
        return MultiLangResolver(clone_path)
    except Exception:  # a resolver build hiccup must never crash the build
        return None


def _touch_coverage_db(coverage_db_path: Path, db_tracer: Any) -> None:
    import sqlite3

    conn = sqlite3.connect(str(coverage_db_path))
    if db_tracer is not None:
        db_tracer.record("sqlite3", path=str(coverage_db_path))
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS coverage (path TEXT, status TEXT, flag_reason TEXT)")
        conn.commit()
    finally:
        conn.close()
