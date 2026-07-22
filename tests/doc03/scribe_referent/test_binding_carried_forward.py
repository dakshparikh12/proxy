"""AC-REFM-06 — a bound referent's node_id survives the notes fold + read-back:
it is not stripped, nulled, or lost.
"""
from __future__ import annotations

from scribe.referent import (
    FoldedReferents,
    ReferentCorpus,
    bind_referent,
    bind_referents,
)


def test_bound_node_id_survives_fold_and_readback(corpus: ReferentCorpus) -> None:
    ref = bind_referent("checkout", corpus)
    assert ref.bound is True
    node_id = ref.binding
    assert node_id == "payments/checkout.py::checkout"

    folded = FoldedReferents.fold([ref])
    # Read it back the way a foreign consumer (the Workroom) would.
    assert folded.binding_for("checkout") == node_id
    assert folded.binding_for("checkout") is not None  # not stripped/nulled


def test_mixed_bound_and_unbound_preserved(corpus: ReferentCorpus) -> None:
    refs = bind_referents(["checkout", "xqzjflk_nope", "login"], corpus)
    folded = FoldedReferents.fold(refs)
    assert folded.binding_for("checkout") == "payments/checkout.py::checkout"
    assert folded.binding_for("login") == "auth/login.py::login"
    # The unbound one is present-but-None (named-but-unbound), not fabricated.
    assert folded.binding_for("xqzjflk_nope") is None


def test_binding_not_lost_across_repeated_fold(corpus: ReferentCorpus) -> None:
    ref = bind_referent("issue_refund", corpus)
    # Fold twice (a re-fold of the same committed delta) — binding stable.
    folded1 = FoldedReferents.fold([ref])
    folded2 = FoldedReferents.fold([ref, ref])
    assert folded1.binding_for("issue_refund") == "payments/refund.py::issue_refund"
    assert folded2.binding_for("issue_refund") == "payments/refund.py::issue_refund"
