"""Per-repo SQLite graph store (canonical §12.2 — never Postgres, AC-CANON-003).

The dependency graph and coverage live in per-repo ``.db`` files on the tenant
volume, schema code-managed (never Alembic). A push triggers a *full* rebuild:
DROP before INSERT, never incremental (AC-M4-009). Optional instruments record
which connection type was opened (must be sqlite3) and the DROP/INSERT ordering.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .graph import Graph


class GraphStore:
    def __init__(
        self,
        db_path: Path,
        db_tracer: Any = None,
        db_operation_counter: Any = None,
    ) -> None:
        self.db_path = Path(db_path)
        self._tracer = db_tracer
        self._ops = db_operation_counter

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        if self._tracer is not None:
            self._tracer.record("sqlite3", path=str(self.db_path))
        return conn

    def write_graph(self, graph: Graph, drop_first: bool = False) -> None:
        conn = self._connect()
        try:
            cur = conn.cursor()
            if drop_first:
                if self._ops is not None:
                    self._ops.record("DROP", "graph rebuild")
                # Drop the canonical §3.4 tables AND the pre-schema `nodes`/`edges`
                # tables so a rebuilt DB is clean (no stale rows survive a rebuild).
                cur.execute("DROP TABLE IF EXISTS graph_nodes")
                cur.execute("DROP TABLE IF EXISTS graph_edges")
                cur.execute("DROP TABLE IF EXISTS nodes")
                cur.execute("DROP TABLE IF EXISTS edges")
            # §3.4 canonical tables (per-repo SQLite; NOT the deferred Postgres map_*).
            cur.execute(
                "CREATE TABLE IF NOT EXISTS graph_nodes ("
                "id TEXT PRIMARY KEY, "        # canonical symbol id; table nodes = table::<name>
                "kind TEXT NOT NULL, "         # function|method|class|route|table|module
                "file_path TEXT NOT NULL, "
                "line INTEGER NOT NULL, "
                "exported INTEGER NOT NULL DEFAULT 0, "  # 1 = route/public symbol/table
                "built_at_sha TEXT NOT NULL)"            # commit this node was extracted at
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS graph_edges ("
                "source TEXT NOT NULL, "
                "target TEXT NOT NULL, "
                "kind TEXT NOT NULL, "         # calls|imports|reads|writes|extends|implements
                "file_path TEXT NOT NULL, "    # the site of the edge (file:line lead)
                "line INTEGER NOT NULL)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS graph_edges_target_idx "
                "ON graph_edges (target, kind)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS graph_edges_source_idx ON graph_edges (source)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS graph_nodes_file_idx ON graph_nodes (file_path)"
            )
            if self._ops is not None:
                self._ops.record("INSERT", f"{len(graph.nodes)} nodes")
            cur.executemany(
                "INSERT OR REPLACE INTO graph_nodes "
                "(id, kind, file_path, line, exported, built_at_sha) "
                "VALUES (?,?,?,?,?,?)",
                [
                    (n.id, n.kind, n.path, n.line, n.exported, n.built_at_sha)
                    for n in graph.nodes
                ],
            )
            cur.executemany(
                "INSERT INTO graph_edges (source, target, kind, file_path, line) "
                "VALUES (?,?,?,?,?)",
                [(e.source, e.target, e.kind, e.file_path, e.line) for e in graph.edges],
            )
            conn.commit()
        finally:
            conn.close()
