"""AC-SCHEMA-01..04 — the four enums reject out-of-set values, accept every member.

Enums are exercised through their owning model fields so the test proves the
validation the runtime actually performs (Pydantic coercion at construction).
"""
import pytest
from pydantic import ValidationError

from scribe.schema import (
    ActionItem,
    Claim,
    Decision,
    DecisionStatus,
    Firmness,
    Provenance,
    Reversibility,
)


def _claim(**overrides):
    base = dict(text="c", speaker="A", said_at_s=1.0, firmness="firm", provenance="observed")
    base.update(overrides)
    return Claim(**base)


def _decision(**overrides):
    base = dict(text="d", status="forming", reversibility="easy-to-reverse")
    base.update(overrides)
    return Decision(**base)


# AC-SCHEMA-01 — Firmness in {firm, hedged, speculative}
def test_firmness_rejects_out_of_enum():
    with pytest.raises(ValidationError):
        _claim(firmness="invalid")


@pytest.mark.parametrize("value", ["firm", "hedged", "speculative"])
def test_firmness_accepts_each_member(value):
    assert _claim(firmness=value).firmness == Firmness(value)


# AC-SCHEMA-02 — Provenance in {observed, inferred}
def test_provenance_rejects_out_of_enum():
    with pytest.raises(ValidationError):
        _claim(provenance="said")


@pytest.mark.parametrize("value", ["observed", "inferred"])
def test_provenance_accepts_each_member(value):
    assert _claim(provenance=value).provenance == Provenance(value)
    assert ActionItem(text="a", provenance=value).provenance == Provenance(value)


# AC-SCHEMA-03 — Reversibility in {easy-to-reverse, hard-to-reverse, irreversible}
def test_reversibility_rejects_out_of_enum():
    with pytest.raises(ValidationError):
        _decision(reversibility="uncertain")


@pytest.mark.parametrize("value", ["easy-to-reverse", "hard-to-reverse", "irreversible"])
def test_reversibility_accepts_each_member(value):
    assert _decision(reversibility=value).reversibility == Reversibility(value)


# AC-SCHEMA-04 — DecisionStatus in {forming, final}
def test_decision_status_rejects_out_of_enum():
    with pytest.raises(ValidationError):
        _decision(status="pending")


@pytest.mark.parametrize("value", ["forming", "final"])
def test_decision_status_accepts_each_member(value):
    assert _decision(status=value).status == DecisionStatus(value)
