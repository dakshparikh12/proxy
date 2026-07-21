"""Layer 1 — Hypothesis property tests for doc02 (Voice & Transport).

Targets the pure webhook/chat/turn parsers that sit on the *live meeting* input
edge. The spec is emphatic that every signal is derived from a source webhook
field and that the system is "honest about malformed" input — so a parser that
crashes on a malformed payload is a real defect, exactly in the AC-FAIL area the
sealed verdict flagged as toothless/untested. Robustness here means: never raise
an uncontrolled exception on attacker-shaped input; return a well-typed result.
"""
from __future__ import annotations

import pytest
from config.hypothesis_profiles import malformed_mapping, weird_text
from hypothesis import given
from transport import chat as chatmod  # noqa: E402
from transport import events  # noqa: E402


# ---------------------------------------------------------------------------
# Webhook parsers must survive arbitrary, malformed payloads (Recall is upstream,
# but a corrupt/partial delivery must degrade to a decision, not crash the ingest).
# ---------------------------------------------------------------------------
@given(payload=malformed_mapping())
def test_is_meeting_end_never_crashes(payload: dict) -> None:
    result = events.is_meeting_end(payload)
    assert isinstance(result, bool)


@given(payload=malformed_mapping())
def test_is_bot_removed_never_crashes(payload: dict) -> None:
    result = events.is_bot_removed(payload)
    assert isinstance(result, bool)


@given(payload=malformed_mapping())
def test_meeting_metadata_never_crashes(payload: dict) -> None:
    md = events.meeting_metadata(payload)
    # Title is always a string; participants is always a tuple of strings.
    assert isinstance(md.title, str)
    assert isinstance(md.participants, tuple)
    assert all(isinstance(p, str) for p in md.participants)


# ---------------------------------------------------------------------------
# Realistic well-formed events still classify correctly after any hardening.
# ---------------------------------------------------------------------------
def test_meeting_end_true_on_explicit_event() -> None:
    assert events.is_meeting_end({"event": "meeting.end"}) is True
    assert events.is_meeting_end({"event": "participant.join"}) is False


def test_bot_removed_on_terminal_status() -> None:
    assert events.is_bot_removed({"event": "bot.status", "data": {"status": "removed"}}) is True
    assert events.is_bot_removed({"event": "bot.status", "data": {"status": "connected"}}) is False


# ---------------------------------------------------------------------------
# Chat addressing detection must never crash on hostile text and must be a pure
# function of the token's presence (the transcript is DATA, not a command).
# ---------------------------------------------------------------------------
@given(message=weird_text(300))
def test_has_proxy_token_never_crashes_and_is_consistent(message: str) -> None:
    result = chatmod.has_proxy_token(message)
    assert isinstance(result, bool)
    # It keys off the literal @proxy token, case-insensitively; a bare word
    # "proxy" (no @) is not an address. This encodes "detection is about the
    # explicit token, not fuzzy intent" — the anti-injection stance for Layer 1.
    assert result == ("@proxy" in message.casefold())


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
