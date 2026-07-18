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
from .gitio import run_git
from .graph import Graph
from .graph_builder import GraphBuilder
from .graph_store import GraphStore
from .readiness import ReadinessRecord, now_indexed_at

if TYPE_CHECKING:  # pragma: no cover
    from .meeting import MeetingSession


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
    def rebuild_graph(self) -> Graph:
        builder = GraphBuilder()
        result = builder.build(self.clone_path, is_excluded=self._is_excluded)
        graph = result.graph
        self._table_map = result.table_map
        if self._store is not None:
            self._store.write_graph(graph, drop_first=True)
        return graph

    def apply_push(self, new_sha: str, num_commits: int = 1) -> None:
        if self._cloner is not None and self.clone_path and self.clone_path.exists():
            self._cloner.pull_delta(self.clone_path)
        if self.clone_path and self.clone_path.exists():
            self.graph = self.rebuild_graph()
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

    _emit(readiness_listener, "indexing")
    builder = GraphBuilder(git_interceptor=git_interceptor)
    build = builder.build(clone_path, is_excluded=pipeline._is_excluded)
    pipeline.graph = build.graph
    pipeline._table_map = build.table_map

    coverage = CoverageRecord(build.coverage_rows)
    pipeline.coverage_record = coverage

    store = GraphStore(pipeline.graph_db_path, db_tracer=db_tracer, db_operation_counter=db_operation_counter)
    store.write_graph(build.graph, drop_first=False)
    pipeline._store = store
    _touch_coverage_db(pipeline.coverage_db_path, db_tracer)

    pinned_sha = _resolve_head(clone_path) or (sha or "")
    pipeline.current_sha = pinned_sha
    if pinned_sha:
        pipeline.graph_retention_index[pinned_sha] = GraphVersion(pinned_sha, build.graph)

    _warm_lsp(pipeline, loc_provider, lsp_lifecycle)

    indexed = coverage.count_by_status("indexed")
    flagged = coverage.count_by_status("flagged")
    gate_ok = _coverage_gate_ok(clone_path, indexed, flagged) and not simulate_coverage_gap
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
    from .mcp_server import CodeIntelMCPServer

    pipeline.server = CodeIntelMCPServer(pipeline=pipeline, db_counter=db_counter, lsp_lifecycle=lsp_lifecycle)
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
    gitdir = clone_path.parent / ".git"
    res = run_git(["--git-dir", str(gitdir), "ls-files"], check=False)
    tracked = [ln for ln in res.stdout.splitlines() if ln.strip()]
    if not tracked:
        return indexed + flagged > 0
    return indexed + flagged == len(tracked)


def _warm_lsp(pipeline: Pipeline, loc_provider: Any, lsp_lifecycle: Any) -> None:
    if loc_provider is None or lsp_lifecycle is None:
        return
    loc = loc_provider.count() if hasattr(loc_provider, "count") else 0
    if loc >= get_int("lsp_warm_loc_threshold") and hasattr(lsp_lifecycle, "mark_connected"):
        lsp_lifecycle.mark_connected()


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
