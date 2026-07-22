"""AC-SCHEMA-09,10,11,23,24 — per-entry caps and the Decision.leans stance enum.

Decision / ActionItem / OpenQuestion text<=1000, OpenQuestion.positions<=12,
ContextLine.text<=500 (NOT 1000), Decision.referents<=8, Decision.leans stances.
"""
import pytest
from pydantic import ValidationError

from scribe.schema import ActionItem, ContextLine, Decision, OpenQuestion


def _decision(**overrides):
    base = dict(text="d", status="forming", reversibility="easy-to-reverse")
    base.update(overrides)
    return Decision(**base)


# AC-SCHEMA-09 — Decision / ActionItem / OpenQuestion text each reject >1000
def test_decision_text_cap():
    with pytest.raises(ValidationError):
        _decision(text="x" * 1001)
    assert len(_decision(text="x" * 1000).text) == 1000


def test_action_text_cap():
    with pytest.raises(ValidationError):
        ActionItem(text="x" * 1001)
    assert len(ActionItem(text="x" * 1000).text) == 1000


def test_open_question_text_cap():
    with pytest.raises(ValidationError):
        OpenQuestion(text="x" * 1001)
    assert len(OpenQuestion(text="x" * 1000).text) == 1000


# AC-SCHEMA-10 — OpenQuestion.positions rejects >12
def test_positions_rejects_thirteen():
    with pytest.raises(ValidationError):
        OpenQuestion(text="q", positions=[str(i) for i in range(13)])


def test_positions_accepts_twelve():
    assert len(OpenQuestion(text="q", positions=[str(i) for i in range(12)]).positions) == 12


# AC-SCHEMA-11 — ContextLine.text limit is 500, NOT 1000
def test_context_line_rejects_501():
    with pytest.raises(ValidationError):
        ContextLine(text="x" * 501)


def test_context_line_accepts_500():
    assert len(ContextLine(text="x" * 500).text) == 500


def test_context_line_rejects_1000_not_silently_accepted():
    # the limit MUST be 500 — a 1000-char string is rejected, proving no 1000 cap leak
    with pytest.raises(ValidationError):
        ContextLine(text="x" * 1000)


# AC-SCHEMA-23 — Decision.referents rejects >8
def test_decision_referents_rejects_nine():
    with pytest.raises(ValidationError):
        _decision(referents=[str(i) for i in range(9)])


def test_decision_referents_accepts_eight():
    assert len(_decision(referents=[str(i) for i in range(8)]).referents) == 8


# AC-SCHEMA-24 — Decision.leans stance in {for, against, silent}
def test_leans_rejects_invalid_stance():
    with pytest.raises(ValidationError):
        _decision(leans={"Priya": "maybe"})
    with pytest.raises(ValidationError):
        _decision(leans={"Priya": "abstain"})
    with pytest.raises(ValidationError):
        _decision(leans={"Priya": ""})


@pytest.mark.parametrize("stance", ["for", "against", "silent"])
def test_leans_accepts_valid_stance(stance):
    assert _decision(leans={"Priya": stance}).leans["Priya"] == stance
