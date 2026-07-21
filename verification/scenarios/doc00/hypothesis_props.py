"""Layer 1 — Hypothesis property tests for doc00 (Foundation / contracts).

doc00 owns the wire contracts and the closed message registry. The two properties
that matter here are law-shaped:

* ``validate_inbound_message`` is a *closed funnel* over untrusted tile input: for
  ANY payload it must either return a registered ``ProxyMessage`` or reject with a
  controlled ``TypeError``/``ValueError`` — never leak an uncontrolled exception,
  never accept an unregistered type. This is the "contracts registry closed" rule.
* The Pydantic models round-trip losslessly (validate∘dump == identity), so the
  substrate can persist and replay them without drift.
"""
from __future__ import annotations

import pytest
from config.hypothesis_profiles import malformed_json, weird_text
from contracts import registry  # noqa: E402
from contracts.bundle import Bundle  # noqa: E402
from contracts.channels import ChannelReport  # noqa: E402
from contracts.readiness import ReadinessReport  # noqa: E402
from hypothesis import given
from hypothesis import strategies as st

_REGISTERED_TYPES = {"connect-repo", "approve-draft", "invite-proxy"}
_READINESS = ("connecting", "cloning", "indexing", "ready", "not_ready")


@given(payload=st.one_of(malformed_json(),
                         st.dictionaries(st.text(max_size=12), malformed_json(), max_size=5)))
def test_validate_inbound_message_is_a_closed_funnel(payload: object) -> None:
    """Untrusted input either validates to a registered message or is rejected
    with a controlled error — nothing else escapes."""
    try:
        msg = registry.validate_inbound_message(payload)
    except (TypeError, ValueError):
        return  # the only sanctioned rejection channel
    # If it returned, it MUST be a registered, discriminated message.
    assert msg.type in _REGISTERED_TYPES


def test_validate_accepts_each_registered_shape() -> None:
    import uuid
    assert registry.validate_inbound_message(
        {"type": "connect-repo", "repo_full_name": "acme/widgets"}).type == "connect-repo"
    assert registry.validate_inbound_message(
        {"type": "approve-draft", "draft_id": str(uuid.uuid4())}).type == "approve-draft"
    assert registry.validate_inbound_message(
        {"type": "invite-proxy", "meeting_id": str(uuid.uuid4())}).type == "invite-proxy"


@given(
    ask=weird_text(120),
    speaker=weird_text(40),
    timestamp=st.datetimes(),
    notes_ref=st.uuids(),
    transcript_tail=st.lists(weird_text(60), max_size=8),
    task_id=st.uuids(),
)
def test_bundle_round_trips(ask, speaker, timestamp, notes_ref, transcript_tail, task_id) -> None:
    b = Bundle(ask=ask, speaker=speaker, timestamp=timestamp, notes_ref=notes_ref,
               transcript_tail=transcript_tail, task_id=task_id)
    assert Bundle.model_validate(b.model_dump()) == b


@given(status=st.sampled_from(_READINESS),
       coverage_pct=st.floats(min_value=0.0, max_value=1.0),
       gaps=st.lists(weird_text(40), max_size=8))
def test_readiness_report_round_trips(status, coverage_pct, gaps) -> None:
    r = ReadinessReport(status=status, coverage_pct=coverage_pct, gaps=gaps)
    assert ReadinessReport.model_validate(r.model_dump()) == r


@given(dm_available=st.booleans())
def test_channel_report_round_trips(dm_available: bool) -> None:
    c = ChannelReport(dm_available=dm_available)
    assert ChannelReport.model_validate(c.model_dump()) == c


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
