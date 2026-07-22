"""AC-REFM-02-NEG — empty/absent graph_nodes -> None for every term, no exception,
no fabricated node_id. Real db fault (empty table / missing file), not a stub.
"""
from __future__ import annotations

from pathlib import Path

from scribe.referent import ReferentCorpus, bind_referent, lookup_referent

TERMS = ["checkout", "login", "payments/checkout", "anything", "xqzjflk"]


def test_empty_graph_nodes_returns_none_for_every_term(empty_db: Path) -> None:
    corpus = ReferentCorpus(areas=(), db_path=str(empty_db))
    for term in TERMS:
        assert lookup_referent(term, corpus) is None


def test_missing_db_file_returns_none_no_exception(missing_db: Path) -> None:
    corpus = ReferentCorpus(areas=(), db_path=str(missing_db))
    for term in TERMS:
        # Must not raise, must return None (honest unbound).
        assert lookup_referent(term, corpus) is None


def test_no_db_path_at_all_returns_none() -> None:
    corpus = ReferentCorpus(areas=(), db_path=None)
    for term in TERMS:
        assert lookup_referent(term, corpus) is None


def test_empty_corpus_records_plain_unbound_no_fabrication(empty_db: Path) -> None:
    corpus = ReferentCorpus(areas=(), db_path=str(empty_db))
    for term in TERMS:
        ref = bind_referent(term, corpus)
        assert ref.bound is False
        assert ref.binding is None
        # never an empty-string stand-in for a real id.
        assert ref.binding != ""
