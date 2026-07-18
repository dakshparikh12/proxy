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
                cur.execute("DROP TABLE IF EXISTS nodes")
                cur.execute("DROP TABLE IF EXISTS edges")
            cur.execute(
                "CREATE TABLE IF NOT EXISTS nodes "
                "(id TEXT PRIMARY KEY, path TEXT, line INTEGER, kind TEXT, pagerank REAL)"
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS edges (source TEXT, target TEXT, kind TEXT)"
            )
            if self._ops is not None:
                self._ops.record("INSERT", f"{len(graph.nodes)} nodes")
            cur.executemany(
                "INSERT OR REPLACE INTO nodes VALUES (?,?,?,?,?)",
                [(n.id, n.path, n.line, n.kind, n.pagerank) for n in graph.nodes],
            )
            cur.executemany(
                "INSERT INTO edges VALUES (?,?,?)",
                [(e.source, e.target, e.kind) for e in graph.edges],
            )
            conn.commit()
        finally:
            conn.close()
