"""Chat-link capability tokens — read-only notes, never accept (AC-INV-012).

A capability token is signed, short-TTL, and meeting-scoped. It grants read-only
notes for exactly its meeting and NEVER grants accept (or any world-touching
action). Expired or wrong-meeting tokens are refused. The mint returns a
structured, non-string token object (not a bare JWT string), so the read-only
frontier is enforced in code, not by trusting an opaque bearer blob.
"""
from __future__ import annotations

import hmac
import secrets
import time
from dataclasses import dataclass
from hashlib import sha256

# Per-process signing key. Mint and authorize run in the same host process, so a
# per-process random key is sufficient to make forgery infeasible; it is never a
# fleet-shared constant.
_SIGNING_KEY = secrets.token_bytes(32)

# The only action a capability token may ever grant.
_READ_ACTION = "notes:read"


@dataclass(frozen=True)
class CapabilityToken:
    """A signed, meeting-scoped, short-TTL read-only capability.

    Intentionally exposes NO ``jti`` attribute and is not a string: the
    read-only frontier is checked structurally against these fields.
    """

    meeting_id: str
    scope: str
    expires_at: float
    signature: str


@dataclass(frozen=True)
class AuthzDecision:
    """The outcome of authorizing an action with a capability token."""

    allowed: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.allowed


def _sign(meeting_id: str, scope: str, expires_at: float) -> str:
    payload = f"{meeting_id}|{scope}|{expires_at:.6f}".encode()
    return hmac.new(_SIGNING_KEY, payload, sha256).hexdigest()


def mint_capability_token(
    *, meeting_id: str, scope: str, ttl_seconds: int
) -> CapabilityToken:
    """Mint a signed, meeting-scoped, short-TTL capability token."""
    expires_at = time.time() + float(ttl_seconds)
    return CapabilityToken(
        meeting_id=str(meeting_id),
        scope=str(scope),
        expires_at=expires_at,
        signature=_sign(str(meeting_id), str(scope), expires_at),
    )


def _valid_signature(token: CapabilityToken) -> bool:
    expected = _sign(token.meeting_id, token.scope, token.expires_at)
    return hmac.compare_digest(expected, token.signature)


def authorize(
    *, token: CapabilityToken, action: str, meeting_id: str
) -> AuthzDecision:
    """Authorize ``action`` on ``meeting_id`` with ``token``.

    Grants ONLY ``notes:read`` on the token's own meeting, with a valid signature
    and an unexpired TTL. Every other action (notably ``draft:accept``), any other
    meeting, and any expired/tampered token is refused.
    """
    if not isinstance(token, CapabilityToken) or not _valid_signature(token):
        return AuthzDecision(False, "invalid_token")
    if time.time() >= token.expires_at:
        return AuthzDecision(False, "expired")
    if token.meeting_id != str(meeting_id):
        return AuthzDecision(False, "wrong_meeting")
    if token.scope != _READ_ACTION or action != _READ_ACTION:
        return AuthzDecision(False, "not_permitted")
    return AuthzDecision(True, "granted")
