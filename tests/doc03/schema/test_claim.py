"""AC-SCHEMA-05,06,07,08,25 — the Claim field contract.

Covers: said_at_s wall-clock rejection, verified default, referents<=8,
text<=1000, and the composite AC-SCHEMA-25 contract (kind fixed, caps, defaults).
"""
import pytest
from pydantic import ValidationError

from scribe.schema import Claim, Firmness, Provenance


def _claim(**overrides):
    base = dict(text="c", speaker="A", said_at_s=1.0, firmness="firm", provenance="observed")
    base.update(overrides)
    return Claim(**base)


# AC-SCHEMA-05 — said_at_s is meeting-relative; wall-clock epoch is a violation
def test_said_at_s_rejects_wall_clock_epoch():
    with pytest.raises(ValidationError):
        _claim(said_at_s=1_700_000_000.0)


def test_said_at_s_accepts_meeting_relative():
    assert _claim(said_at_s=47.3).said_at_s == 47.3


def test_said_at_s_rejects_negative():
    with pytest.raises(ValidationError):
        _claim(said_at_s=-1.0)


# AC-SCHEMA-06 — verified defaults to False on every new Claim
def test_verified_defaults_false():
    assert _claim().verified is False


def test_verified_default_across_varied_inputs():
    for txt, spk, t in (("a", "X", 0.0), ("b" * 500, "Y", 42.5), ("z", "Z", 3599.0)):
        assert Claim(
            text=txt, speaker=spk, said_at_s=t, firmness="hedged", provenance="inferred"
        ).verified is False


# AC-SCHEMA-07 — referents rejects lists longer than 8 elements
def test_referents_rejects_nine():
    with pytest.raises(ValidationError):
        _claim(referents=[str(i) for i in range(9)])


def test_referents_accepts_boundary_and_below():
    assert len(_claim(referents=[str(i) for i in range(8)]).referents) == 8
    assert _claim(referents=[]).referents == []
    assert len(_claim(referents=[str(i) for i in range(7)]).referents) == 7


# AC-SCHEMA-08 — text rejects strings longer than 1000 characters
def test_text_rejects_1001():
    with pytest.raises(ValidationError):
        _claim(text="x" * 1001)


def test_text_accepts_exactly_1000():
    assert len(_claim(text="x" * 1000).text) == 1000


# AC-SCHEMA-25 — composite Claim contract
def test_kind_is_fixed_to_claim():
    with pytest.raises(ValidationError):
        Claim(kind="x", text="c", speaker="A", said_at_s=1.0, firmness="firm", provenance="observed")
    assert _claim().kind == "claim"


def test_firmness_and_provenance_are_required():
    with pytest.raises(ValidationError):
        Claim(text="c", speaker="A", said_at_s=1.0, provenance="observed")
    with pytest.raises(ValidationError):
        Claim(text="c", speaker="A", said_at_s=1.0, firmness="firm")


def test_said_at_s_accepts_float():
    c = _claim(said_at_s=12.75)
    assert isinstance(c.said_at_s, float)
    assert c.firmness == Firmness.firm and c.provenance == Provenance.observed


def test_claim_25_bundle():
    # every bullet of AC-SCHEMA-25 in one place
    with pytest.raises(ValidationError):
        _claim(kind="x")
    with pytest.raises(ValidationError):
        _claim(text="x" * 1001)
    assert _claim().verified is False
    with pytest.raises(ValidationError):
        _claim(referents=[str(i) for i in range(9)])
