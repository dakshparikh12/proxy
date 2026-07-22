"""AC-REFM-04 — a known term binds to the correct REAL node (positive match), and
AC-REFM-04-NEG — a real db:sqlite fault (missing target / garbage rows) degrades
honestly (None in lenient mode; a surfaced error in strict mode) — no corruption,
no fabricated id.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scribe.referent import (
    OverviewArea,
    ReferentCorpus,
    ReferentCorpusError,
    lookup_referent,
)

# The real ids seeded by conftest.SEED_ROWS.
KNOWN_NODE_IDS = {
    "payments/checkout.py::checkout",
    "payments/refund.py::issue_refund",
    "auth/login.py::login",
}
KNOWN_AREAS = {"payments/checkout", "auth", "observability"}


def test_checkout_binds_to_real_checkout_node(corpus: ReferentCorpus) -> None:
    result = lookup_referent("checkout", corpus)
    assert result is not None
    # It is a REAL id present in the corpus (not fabricated).
    assert result in KNOWN_NODE_IDS
    assert result == "payments/checkout.py::checkout"


def test_area_term_binds_to_real_area_when_no_node(seeded_db: Path) -> None:
    # A term that matches only an overview area (no graph node): 'observability'.
    corpus = ReferentCorpus(
        areas=(OverviewArea(name="observability"),), db_path=str(seeded_db)
    )
    result = lookup_referent("observability", corpus)
    assert result == "observability"
    assert result in KNOWN_AREAS


def test_symbol_term_binds_to_real_node(corpus: ReferentCorpus) -> None:
    result = lookup_referent("issue_refund", corpus)
    assert result == "payments/refund.py::issue_refund"
    assert result in KNOWN_NODE_IDS


def test_result_is_never_fabricated(corpus: ReferentCorpus) -> None:
    for term in ["checkout", "login", "issue_refund", "observability"]:
        result = lookup_referent(term, corpus)
        assert result is None or result in (KNOWN_NODE_IDS | KNOWN_AREAS)


# ---- AC-REFM-04-NEG: real db fault injection -------------------------------- #

def test_missing_target_degrades_to_none_lenient(missing_db: Path) -> None:
    corpus = ReferentCorpus(areas=(), db_path=str(missing_db))
    # Lenient (default): missing target -> honest None, no corruption, no raise.
    assert lookup_referent("checkout", corpus) is None


def test_missing_target_surfaces_error_strict(missing_db: Path) -> None:
    corpus = ReferentCorpus(areas=(), db_path=str(missing_db))
    # Strict: the broken handle is surfaced, not silently proceeded past.
    with pytest.raises(ReferentCorpusError):
        lookup_referent("checkout", corpus, strict=True)


def test_garbage_rows_degrade_honestly(tmp_path: Path) -> None:
    """A graph_nodes table with NULL/garbage cells -> no corrupt binding."""
    db_path = tmp_path / "garbage.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE graph_nodes (node_id TEXT, area TEXT, file TEXT, symbol TEXT)")
        # A row whose node_id is NULL and cells are junk -> must NOT become a binding.
        conn.execute("INSERT INTO graph_nodes VALUES (NULL, NULL, NULL, 'checkout')")
        conn.commit()
    finally:
        conn.close()
    corpus = ReferentCorpus(areas=(), db_path=str(db_path))
    # The only "match" would be a symbol==checkout on a NULL-id row: it is dropped,
    # so the honest result is None (no empty-string / None-id binding fabricated).
    result = lookup_referent("checkout", corpus)
    assert result is None


def test_malformed_table_shape_degrades_to_none_lenient(tmp_path: Path) -> None:
    """graph_nodes with the WRONG column count -> lenient None, strict raises."""
    db_path = tmp_path / "wrongshape.db"
    conn = sqlite3.connect(str(db_path))
    try:
        # Only two columns — our SELECT names four, so SQLite errors on the read.
        conn.execute("CREATE TABLE graph_nodes (a TEXT, b TEXT)")
        conn.execute("INSERT INTO graph_nodes VALUES ('checkout', 'x')")
        conn.commit()
    finally:
        conn.close()
    corpus = ReferentCorpus(areas=(), db_path=str(db_path))
    assert lookup_referent("checkout", corpus) is None
    with pytest.raises(ReferentCorpusError):
        lookup_referent("checkout", corpus, strict=True)
