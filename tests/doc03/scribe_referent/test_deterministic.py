"""AC-REFM-08 — lookup_referent is deterministic: 100 calls on a frozen corpus with
the same term return identical values. No random/hash-order tiebreak variance.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from scribe.referent import ReferentCorpus, lookup_referent


def test_100_calls_identical(corpus: ReferentCorpus) -> None:
    results = {lookup_referent("checkout", corpus) for _ in range(100)}
    assert len(results) == 1
    assert next(iter(results)) == "payments/checkout.py::checkout"


def test_100_calls_identical_for_none_case(corpus: ReferentCorpus) -> None:
    results = {lookup_referent("no_such_term_zzz", corpus) for _ in range(100)}
    assert results == {None}


def test_tie_breaks_are_stable(tmp_path: Path) -> None:
    """Two rows that both match a term must always resolve to the same id."""
    db_path = tmp_path / "ties.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE graph_nodes (node_id TEXT, area TEXT, file TEXT, symbol TEXT)")
        # Insert in a deliberately non-sorted order; three rows share symbol 'checkout'.
        rows = [
            ("z/checkout.py::checkout", "z", "z/checkout.py", "checkout"),
            ("a/checkout.py::checkout", "a", "a/checkout.py", "checkout"),
            ("m/checkout.py::checkout", "m", "m/checkout.py", "checkout"),
        ]
        conn.executemany("INSERT INTO graph_nodes VALUES (?, ?, ?, ?)", rows)
        conn.commit()
    finally:
        conn.close()
    corpus = ReferentCorpus(areas=(), db_path=str(db_path))
    results = {lookup_referent("checkout", corpus) for _ in range(100)}
    assert len(results) == 1
    # Deterministic tiebreak = smallest node_id.
    assert next(iter(results)) == "a/checkout.py::checkout"
