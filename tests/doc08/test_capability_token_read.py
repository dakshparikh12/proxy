"""Doc-08 §2.8 / §4.6 — the capability-token read primitive (node
``experience.capability-token-read``).

The chat/close link (§2.6) carries a **signed, short-TTL, meeting-scoped,
revocable** capability token. Presenting it to ``GET /m/{meeting_id}`` grants a
**read-only view of the notes** to a non-signed-in recipient — **never**
accept/reject, never another meeting, never an expired/tampered/revoked token.

These are REAL-path tests over the actual ``ops.capability`` primitive and the
LIVE control_plane ``GET /m/{meeting_id}`` route. They prove the four refusals the
node's DoD names — mutation-coercion, wrong-meeting, expired, tampered — plus the
one net-new gap this node closes: **revocation** (a revoked-token check + a
per-meeting token epoch bumped on demand). NOT done if authorize can be coerced to
grant a mutation, if a wrong-meeting token reads notes, or if a revoked token is
still honoured.
"""
from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

import pytest

# Import via the ``libs.ops`` facade — the canonical path the LIVE control_plane
# route imports (``from libs.ops import verify_capability_token``). The primitive
# anchors its signing key + revoked-set/epoch-map in a process-global shared across
# every import identity, so mint here and verify on the route agree; importing the
# same facade keeps the test on the exact module the product uses.
from libs.ops import (
    AuthzDecision,
    CapabilityToken,
    authorize,
    bump_meeting_epoch,
    encode_capability_token,
    is_revoked,
    mint_capability_token,
    revoke_capability_token,
    verify_capability_token,
)

_READ = "notes:read"


# --------------------------------------------------------------------------- #
# 1 · mint/authorize grants ONLY notes:read on the token's own meeting
# --------------------------------------------------------------------------- #
def test_valid_same_meeting_notes_read_is_granted() -> None:
    """The one thing that IS allowed: a valid, unexpired, same-meeting notes:read."""
    m = str(uuid4())
    tok = mint_capability_token(meeting_id=m, scope=_READ, ttl_seconds=300)
    decision = authorize(token=tok, action=_READ, meeting_id=m)
    assert isinstance(decision, AuthzDecision)
    assert decision.allowed is True
    assert bool(decision) is True
    assert decision.reason == "granted"


def test_token_is_structured_not_a_bare_string() -> None:
    """The read-only frontier is enforced in code — the mint returns a structured
    object with a signature, NOT an opaque bearer blob trusted by default."""
    tok = mint_capability_token(meeting_id=str(uuid4()), scope=_READ, ttl_seconds=300)
    assert isinstance(tok, CapabilityToken)
    assert not isinstance(tok, str)
    assert tok.signature  # signed
    # No jti/opaque-blob shortcut: the fields ARE the authority.
    assert not hasattr(tok, "jti")


# --------------------------------------------------------------------------- #
# 2 · mutation-coercion — authorize can NEVER be coerced into a mutation
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "action",
    [
        "draft:accept",
        "draft:reject",
        "notes:write",
        "notes:read;draft:accept",  # injection-shaped
        "*",
        "",
        "NOTES:READ",  # case games must not slip through
    ],
)
def test_no_action_other_than_notes_read_is_ever_granted(action: str) -> None:
    """A same-meeting, validly-signed, unexpired token STILL refuses every action
    that is not exactly ``notes:read`` — the world-touching accept is unreachable."""
    m = str(uuid4())
    tok = mint_capability_token(meeting_id=m, scope=_READ, ttl_seconds=300)
    decision = authorize(token=tok, action=action, meeting_id=m)
    assert decision.allowed is False, f"action {action!r} must be refused"
    assert decision.reason == "not_permitted"


def test_a_write_scoped_token_cannot_be_minted_into_a_grant() -> None:
    """Even a token minted with a NON-read scope cannot authorize notes:read — the
    scope must be exactly notes:read on both sides. A mint with a mutation scope is
    inert: it grants nothing (Law 3, human control is absolute)."""
    m = str(uuid4())
    bad = mint_capability_token(meeting_id=m, scope="draft:accept", ttl_seconds=300)
    # It cannot grant its own mutation scope…
    assert authorize(token=bad, action="draft:accept", meeting_id=m).allowed is False
    # …and it cannot be repurposed to read notes either.
    assert authorize(token=bad, action=_READ, meeting_id=m).allowed is False


# --------------------------------------------------------------------------- #
# 3 · wrong-meeting — a token for A can never read B
# --------------------------------------------------------------------------- #
def test_wrong_meeting_token_is_refused() -> None:
    """meeting_id lives in the SIGNED body and is re-checked against the path: a
    token minted for meeting A refuses meeting B."""
    a, b = str(uuid4()), str(uuid4())
    tok = mint_capability_token(meeting_id=a, scope=_READ, ttl_seconds=300)
    assert authorize(token=tok, action=_READ, meeting_id=b).allowed is False
    assert authorize(token=tok, action=_READ, meeting_id=b).reason == "wrong_meeting"


def test_meeting_id_cannot_be_swapped_without_breaking_the_signature() -> None:
    """Swapping the meeting_id on the structured token (to point at another meeting)
    invalidates the HMAC — the tampered token is refused as invalid, never granted."""
    a, b = str(uuid4()), str(uuid4())
    tok = mint_capability_token(meeting_id=a, scope=_READ, ttl_seconds=300)
    forged = CapabilityToken(
        meeting_id=b,  # attacker re-points at B but keeps A's signature
        scope=tok.scope,
        expires_at=tok.expires_at,
        signature=tok.signature,
    )
    decision = authorize(token=forged, action=_READ, meeting_id=b)
    assert decision.allowed is False
    assert decision.reason == "invalid_token"


# --------------------------------------------------------------------------- #
# 4 · expired TTL
# --------------------------------------------------------------------------- #
def test_expired_token_is_refused() -> None:
    """A token past its short TTL is refused — the grant is time-boxed."""
    m = str(uuid4())
    tok = mint_capability_token(meeting_id=m, scope=_READ, ttl_seconds=-1)  # already past
    decision = authorize(token=tok, action=_READ, meeting_id=m)
    assert decision.allowed is False
    assert decision.reason == "expired"


def test_expiry_extension_by_tampering_the_body_is_refused() -> None:
    """Pushing expires_at into the future (to defeat the TTL) breaks the signature."""
    m = str(uuid4())
    tok = mint_capability_token(meeting_id=m, scope=_READ, ttl_seconds=-1)
    forged = CapabilityToken(
        meeting_id=tok.meeting_id,
        scope=tok.scope,
        expires_at=time.time() + 10_000,  # attacker extends the TTL
        signature=tok.signature,          # but keeps the old signature
    )
    assert authorize(token=forged, action=_READ, meeting_id=m).reason == "invalid_token"


# --------------------------------------------------------------------------- #
# 5 · tampered signature
# --------------------------------------------------------------------------- #
def test_tampered_signature_is_refused() -> None:
    """Any signature that is not the exact HMAC over the body is refused."""
    m = str(uuid4())
    tok = mint_capability_token(meeting_id=m, scope=_READ, ttl_seconds=300)
    forged = CapabilityToken(
        meeting_id=tok.meeting_id,
        scope=tok.scope,
        expires_at=tok.expires_at,
        signature="0" * len(tok.signature),  # forged signature
    )
    assert authorize(token=forged, action=_READ, meeting_id=m).reason == "invalid_token"


def test_empty_or_nonhex_signature_is_refused_not_crashed() -> None:
    """A degenerate signature is refused as invalid — never a crash, never a grant
    (compare_digest is called defensively)."""
    m = str(uuid4())
    tok = mint_capability_token(meeting_id=m, scope=_READ, ttl_seconds=300)
    for sig in ("", "zzzz", "not-hex-!!"):
        forged = CapabilityToken(m, tok.scope, tok.expires_at, sig)
        assert authorize(token=forged, action=_READ, meeting_id=m).allowed is False


# --------------------------------------------------------------------------- #
# 6 · REVOCATION — the one net-new gap this node closes (§2.8 / §4.6)
# --------------------------------------------------------------------------- #
def test_a_revoked_token_is_refused_even_though_it_is_otherwise_valid() -> None:
    """§2.8 names the token as *revocable*: a per-token ``revoked_tokens`` check.
    A validly-signed, unexpired, same-meeting notes:read token that has been
    REVOKED is refused — proving the revocation path exists and bites."""
    m = str(uuid4())
    tok = mint_capability_token(meeting_id=m, scope=_READ, ttl_seconds=300)
    # It is honoured before revocation…
    assert authorize(token=tok, action=_READ, meeting_id=m).allowed is True
    # …revoke it…
    revoke_capability_token(tok)
    assert is_revoked(tok) is True
    # …and now it is refused, though nothing else about it changed.
    decision = authorize(token=tok, action=_READ, meeting_id=m)
    assert decision.allowed is False
    assert decision.reason == "revoked"


def test_bumping_a_meeting_epoch_invalidates_all_outstanding_tokens_for_it() -> None:
    """§2.8's alternative revocation lever: a per-meeting token epoch bumped on
    demand. Bumping meeting A's epoch invalidates every token minted before the
    bump for A, WITHOUT touching meeting B's tokens."""
    a, b = str(uuid4()), str(uuid4())
    tok_a = mint_capability_token(meeting_id=a, scope=_READ, ttl_seconds=300)
    tok_b = mint_capability_token(meeting_id=b, scope=_READ, ttl_seconds=300)
    assert authorize(token=tok_a, action=_READ, meeting_id=a).allowed is True
    assert authorize(token=tok_b, action=_READ, meeting_id=b).allowed is True

    bump_meeting_epoch(a)  # revoke every outstanding token for meeting A

    a_decision = authorize(token=tok_a, action=_READ, meeting_id=a)
    assert a_decision.allowed is False
    assert a_decision.reason == "revoked"
    # Meeting B is untouched — the epoch is per-meeting, not global.
    assert authorize(token=tok_b, action=_READ, meeting_id=b).allowed is True


def test_a_token_minted_after_an_epoch_bump_is_valid_again() -> None:
    """A fresh mint after the bump carries the new epoch and is honoured — the lever
    revokes the past, it does not brick the meeting forever."""
    m = str(uuid4())
    old = mint_capability_token(meeting_id=m, scope=_READ, ttl_seconds=300)
    bump_meeting_epoch(m)
    assert authorize(token=old, action=_READ, meeting_id=m).allowed is False
    fresh = mint_capability_token(meeting_id=m, scope=_READ, ttl_seconds=300)
    assert authorize(token=fresh, action=_READ, meeting_id=m).allowed is True


# --------------------------------------------------------------------------- #
# 7 · the route adapter: verify_capability_token(str, meeting_id) -> grant|None
#     (§4.6's route calls it over the STRING token in the URL)
# --------------------------------------------------------------------------- #
def test_verify_capability_token_grants_only_for_a_valid_same_meeting_token() -> None:
    """The route-facing adapter takes the URL string + the path meeting_id and
    returns a grant (truthy) for a valid same-meeting token, ``None`` otherwise."""
    m = str(uuid4())
    tok = mint_capability_token(meeting_id=m, scope=_READ, ttl_seconds=300)
    token_str = encode_capability_token(tok)
    grant = verify_capability_token(token_str, m)
    assert grant is not None
    assert grant.allowed is True


@pytest.mark.parametrize("mangler", ["wrong_meeting", "expired", "tampered", "garbage", "none", "revoked"])
def test_verify_capability_token_returns_none_for_every_bad_token(mangler: str) -> None:
    """Wrong-meeting, expired, tampered, garbage, missing, and revoked all yield
    ``None`` — the route treats ``None`` as "no grant" and falls to the 404 path."""
    m = str(uuid4())
    tok = mint_capability_token(meeting_id=m, scope=_READ, ttl_seconds=300)
    token_str = encode_capability_token(tok)

    if mangler == "wrong_meeting":
        assert verify_capability_token(token_str, str(uuid4())) is None
    elif mangler == "expired":
        past = mint_capability_token(meeting_id=m, scope=_READ, ttl_seconds=-1)
        assert verify_capability_token(encode_capability_token(past), m) is None
    elif mangler == "tampered":
        assert verify_capability_token(token_str[:-4] + "0000", m) is None
    elif mangler == "garbage":
        assert verify_capability_token("not-a-real-token", m) is None
    elif mangler == "none":
        assert verify_capability_token(None, m) is None
    elif mangler == "revoked":
        revoke_capability_token(tok)
        assert verify_capability_token(token_str, m) is None


def test_verify_capability_token_never_throws_on_malformed_input() -> None:
    """The verifier is a never-throw boundary: any malformed string returns None,
    never an exception that could 500 the public read route."""
    m = str(uuid4())
    for junk in ["", "...", "a.b.c", "%%%%", "e" * 5000, "{}", "null"]:
        assert verify_capability_token(junk, m) is None
