"""AC-SCHEMA-15 — Claim.contradicts pointing at a non-existent id is a violation.

A state-machine assertion: apply an AddOp carrying a Claim whose contradicts is a
dangling id against a store with known ids {A, B}; the applier flags a referential
integrity violation. contradicts naming an existing id, and contradicts=None,
both apply cleanly.
"""
import pytest

from scribe.parse import Entry, NoteStore, ReferentialIntegrityError, check_referential_integrity
from scribe.schema import AddOp, Claim, NoteDelta


def _store_ab():
    return NoteStore({
        "A": Entry(id="A", current={"kind": "claim"}),
        "B": Entry(id="B", current={"kind": "claim"}),
    })


def _claim(**overrides):
    base = dict(text="c", speaker="A", said_at_s=1.0, firmness="firm", provenance="observed")
    base.update(overrides)
    return Claim(**base)


def test_dangling_contradicts_flagged_on_apply():
    store = _store_ab()
    with pytest.raises(ReferentialIntegrityError):
        store.apply(AddOp(entry=_claim(contradicts="C")))


def test_existing_contradicts_accepted():
    store = _store_ab()
    store.apply(AddOp(entry=_claim(contradicts="A")))  # A exists -> no raise


def test_none_contradicts_accepted():
    store = _store_ab()
    store.apply(AddOp(entry=_claim(contradicts=None)))


def test_check_over_delta_flags_dangling():
    store = _store_ab()
    delta = NoteDelta(ops=[AddOp(entry=_claim(contradicts="C"))])
    with pytest.raises(ReferentialIntegrityError):
        check_referential_integrity(delta, store)
    ok = NoteDelta(ops=[AddOp(entry=_claim(contradicts="A"))])
    check_referential_integrity(ok, store)  # no raise
