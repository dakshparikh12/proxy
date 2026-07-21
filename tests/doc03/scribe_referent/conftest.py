"""Shared fixtures for the referent-matcher tests.

Builds a REAL on-disk SQLite ``graph_nodes`` corpus (SQLite is a file — no server),
plus the in-memory overview-areas structure, so the db:sqlite criteria run for real
against the actual seam rather than a stub.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scribe.referent import OverviewArea, ReferentCorpus

# The seed rows: (node_id, area, file, symbol). The spec's worked example is
# an area "payments/checkout" and a file "checkout.py" (AC-REFM-04).
SEED_ROWS = [
    ("payments/checkout.py::checkout", "payments/checkout", "payments/checkout.py", "checkout"),
    ("payments/refund.py::issue_refund", "payments/refund", "payments/refund.py", "issue_refund"),
    ("auth/login.py::login", "auth/login", "auth/login.py", "login"),
]

AREAS = (
    OverviewArea(name="payments/checkout"),
    OverviewArea(name="auth"),
    OverviewArea(name="observability"),
)


def _create_graph_nodes(db_path: Path, rows: list[tuple[str, str, str, str]]) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE graph_nodes ("
            "  node_id TEXT PRIMARY KEY,"
            "  area TEXT,"
            "  file TEXT,"
            "  symbol TEXT"
            ")"
        )
        conn.executemany(
            "INSERT INTO graph_nodes (node_id, area, file, symbol) VALUES (?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def seeded_db(tmp_path: Path) -> Path:
    """A real SQLite db file with a populated graph_nodes table."""
    db_path = tmp_path / "graph.db"
    _create_graph_nodes(db_path, SEED_ROWS)
    return db_path


@pytest.fixture
def empty_db(tmp_path: Path) -> Path:
    """A real SQLite db file whose graph_nodes table exists but has zero rows."""
    db_path = tmp_path / "empty.db"
    _create_graph_nodes(db_path, [])
    return db_path


@pytest.fixture
def missing_db(tmp_path: Path) -> Path:
    """A path to a db file that does NOT exist (absent corpus)."""
    return tmp_path / "does_not_exist.db"


@pytest.fixture
def corpus(seeded_db: Path) -> ReferentCorpus:
    return ReferentCorpus(areas=AREAS, db_path=str(seeded_db))
