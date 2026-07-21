"""AC-REFM-05 — a nonsense term returns None and is recorded as a plain (named-but-
unbound) referent: no fabricated node_id, no invented area, no empty-string stand-in.
"""
from __future__ import annotations

from scribe.referent import ReferentCorpus, bind_referent, lookup_referent

NONSENSE = "xqzjflk_nonsense_abc123"


def test_nonsense_term_returns_none(corpus: ReferentCorpus) -> None:
    assert lookup_referent(NONSENSE, corpus) is None


def test_nonsense_recorded_as_plain_unbound(corpus: ReferentCorpus) -> None:
    ref = bind_referent(NONSENSE, corpus)
    assert ref.term == NONSENSE  # name preserved (honest: named-but-unbound)
    assert ref.bound is False
    assert ref.binding is None


def test_no_empty_string_binding(corpus: ReferentCorpus) -> None:
    ref = bind_referent(NONSENSE, corpus)
    # Explicitly NOT an empty-string stand-in.
    assert ref.binding != ""
    assert ref.binding is None


def test_whitespace_and_empty_terms_are_unbound(corpus: ReferentCorpus) -> None:
    for term in ["", "   ", "\t"]:
        assert lookup_referent(term, corpus) is None
        ref = bind_referent(term, corpus)
        assert ref.bound is False
        assert ref.binding is None
