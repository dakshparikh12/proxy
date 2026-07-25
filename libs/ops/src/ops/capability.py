"""Chat-link capability tokens — read-only notes, never accept (AC-INV-012).

A capability token is signed, short-TTL, meeting-scoped, and **revocable**. It
grants read-only notes for exactly its meeting and NEVER grants accept (or any
world-touching action). Expired, wrong-meeting, tampered, or revoked tokens are
refused. The mint returns a structured, non-string token object (not a bare JWT
string), so the read-only frontier is enforced in code, not by trusting an opaque
bearer blob.

Revocation (§2.8 / §4.6) has two levers, both live here:

* **per-token revocation** — :func:`revoke_capability_token` records a token's
  signature in a revoked set; :func:`authorize` refuses any token in it. This is
  the ``revoked_tokens`` check §2.8 names.
* **per-meeting token epoch** — every token carries the meeting's current epoch in
  its signed body; :func:`bump_meeting_epoch` increments a meeting's epoch, which
  invalidates every token minted before the bump for THAT meeting (and only that
  meeting) without bricking future mints. This is the "per-meeting token epoch
  bumped on demand" lever §2.8 names.

The signing key is per-process: mint and authorize run in the same control_plane
process (CANONICAL §12.9), so a per-process random key makes forgery infeasible
without a fleet-shared secret. The revoked-set and the epoch map are likewise
per-process — a deliberate V0 scope (CANONICAL §12.9: "minimal, real"); a
multi-process deployment would back them with the durable substrate, but the
contract — a revoked/epoch-bumped token is refused — is proven here.
"""
from __future__ import annotations

import base64
import binascii
import hmac
import json
import secrets
import sys
import time
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

# The only action a capability token may ever grant.
_READ_ACTION = "notes:read"

# ── Process-global shared state (import-path-stable; §2.8 / §12.9) ────────────
# This module is reachable under two import identities in the workspace src-layout
# (``ops.capability`` top-level AND ``libs.ops.src.ops.capability`` via the facade).
# Those are DISTINCT module objects, so a naïve module-level ``_SIGNING_KEY`` /
# revoked-set would DUPLICATE — a token minted or revoked on one identity would be
# invisible to the other (mint on the close-line path, verify on the read route).
# We anchor the signing key + revocation stores in ONE process-global dict held in
# ``sys.modules`` under a stable sentinel, so EVERY import identity shares the SAME
# key and the SAME revoked-set/epoch-map. Mint, authorize, and revoke therefore
# always agree, whichever path imported them (CANONICAL §12.9: minimal, real).
_STATE_KEY = "__proxy_capability_token_state__"


def _shared_state() -> dict[str, Any]:
    """The one process-global state pool, created once and shared across identities.

    Holds ``signing_key`` (per-process random HMAC key), ``revoked`` (the
    ``revoked_tokens`` set §2.8 names), and ``epoch`` (the per-meeting token epoch).
    Anchored in ``sys.modules`` so a second import identity of this file resolves
    the SAME dict rather than minting a second, divergent key.
    """
    holder = sys.modules.get(_STATE_KEY)
    if holder is None:
        holder = type(sys)(_STATE_KEY)  # a bare module object as the shared container
        holder.state = {  # type: ignore[attr-defined]
            "signing_key": secrets.token_bytes(32),
            "revoked": set(),
            "epoch": {},
        }
        sys.modules[_STATE_KEY] = holder
    state: dict[str, Any] = holder.state
    return state


def _signing_key() -> bytes:
    """The per-process HMAC signing key (shared across import identities)."""
    key: bytes = _shared_state()["signing_key"]
    return key


def _revoked_signatures() -> set[str]:
    """The revoked-token signature set — the ``revoked_tokens`` check (§2.8)."""
    revoked: set[str] = _shared_state()["revoked"]
    return revoked


def _meeting_epoch() -> dict[str, int]:
    """The per-meeting token epoch map — bumped on demand to revoke en masse."""
    epoch: dict[str, int] = _shared_state()["epoch"]
    return epoch


def _current_epoch(meeting_id: str) -> int:
    """The meeting's current token epoch (0 until first bumped)."""
    return _meeting_epoch().get(str(meeting_id), 0)


@dataclass(frozen=True)
class CapabilityToken:
    """A signed, meeting-scoped, short-TTL, revocable read-only capability.

    Intentionally exposes NO ``jti`` attribute and is not a string: the read-only
    frontier is checked structurally against these fields. ``epoch`` is the
    meeting's token epoch captured at mint — an epoch bump invalidates every token
    minted at an older epoch (the per-meeting revocation lever).
    """

    meeting_id: str
    scope: str
    expires_at: float
    signature: str
    epoch: int = 0


@dataclass(frozen=True)
class AuthzDecision:
    """The outcome of authorizing an action with a capability token."""

    allowed: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.allowed


def _sign(meeting_id: str, scope: str, expires_at: float, epoch: int) -> str:
    """The HMAC over the WHOLE authority-bearing body — including the epoch, so a
    token's epoch cannot be edited without breaking the signature."""
    payload = f"{meeting_id}|{scope}|{expires_at:.6f}|{epoch}".encode()
    return hmac.new(_signing_key(), payload, sha256).hexdigest()


def mint_capability_token(
    *, meeting_id: str, scope: str, ttl_seconds: int
) -> CapabilityToken:
    """Mint a signed, meeting-scoped, short-TTL, revocable capability token.

    The token captures the meeting's CURRENT epoch, so a later
    :func:`bump_meeting_epoch` for this meeting revokes it.
    """
    mid = str(meeting_id)
    scope_s = str(scope)
    expires_at = time.time() + float(ttl_seconds)
    epoch = _current_epoch(mid)
    return CapabilityToken(
        meeting_id=mid,
        scope=scope_s,
        expires_at=expires_at,
        signature=_sign(mid, scope_s, expires_at, epoch),
        epoch=epoch,
    )


def _valid_signature(token: CapabilityToken) -> bool:
    """True iff the token's signature is the exact HMAC over its body.

    Defensive against a malformed (non-hex / empty) signature: ``compare_digest``
    over two str is safe, and a length mismatch simply compares unequal — never a
    crash, never a grant.
    """
    try:
        expected = _sign(token.meeting_id, token.scope, token.expires_at, token.epoch)
        return hmac.compare_digest(expected, token.signature)
    except (TypeError, ValueError):
        return False


def revoke_capability_token(token: CapabilityToken) -> None:
    """Revoke a single token (the ``revoked_tokens`` check, §2.8).

    Records the token's signature; :func:`authorize` refuses it thereafter, even
    though its signature, TTL, and meeting are otherwise valid.
    """
    _revoked_signatures().add(token.signature)


def is_revoked(token: CapabilityToken) -> bool:
    """True iff the token has been individually revoked OR its meeting's epoch has
    been bumped past the token's embedded epoch."""
    if token.signature in _revoked_signatures():
        return True
    return token.epoch < _current_epoch(token.meeting_id)


def bump_meeting_epoch(meeting_id: str) -> int:
    """Bump a meeting's token epoch — revokes EVERY outstanding token for it.

    Every token minted before the bump carries an older epoch and is refused by
    :func:`authorize`; a fresh mint after the bump carries the new epoch and is
    honoured. Per-meeting: bumping A never touches B. Returns the new epoch.
    """
    mid = str(meeting_id)
    epoch = _meeting_epoch()
    epoch[mid] = _current_epoch(mid) + 1
    return epoch[mid]


def authorize(
    *, token: CapabilityToken, action: str, meeting_id: str
) -> AuthzDecision:
    """Authorize ``action`` on ``meeting_id`` with ``token``.

    Grants ONLY ``notes:read`` on the token's own meeting, with a valid signature,
    an unexpired TTL, and no revocation. Every other action (notably
    ``draft:accept``), any other meeting, any expired/tampered token, and any
    revoked or epoch-bumped token is refused. Order is fail-closed: signature →
    revocation → expiry → meeting → scope/action.
    """
    if not isinstance(token, CapabilityToken) or not _valid_signature(token):
        return AuthzDecision(False, "invalid_token")
    if is_revoked(token):
        return AuthzDecision(False, "revoked")
    if time.time() >= token.expires_at:
        return AuthzDecision(False, "expired")
    if token.meeting_id != str(meeting_id):
        return AuthzDecision(False, "wrong_meeting")
    if token.scope != _READ_ACTION or action != _READ_ACTION:
        return AuthzDecision(False, "not_permitted")
    return AuthzDecision(True, "granted")


# ── The route-facing adapter — over the STRING token in the URL (§4.6) ────────
def encode_capability_token(token: CapabilityToken) -> str:
    """Serialise a token to the compact URL string the chat/close link carries.

    Base64url over the JSON body + signature. The signature still binds the body,
    so a re-encoded/edited string is caught by :func:`authorize`'s signature check —
    the string form adds NO trust, it is only transport.
    """
    body = {
        "meeting_id": token.meeting_id,
        "scope": token.scope,
        "expires_at": token.expires_at,
        "epoch": token.epoch,
        "signature": token.signature,
    }
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode()


def decode_capability_token(token_str: str | None) -> CapabilityToken | None:
    """Parse a URL string back into a :class:`CapabilityToken`, or ``None``.

    A never-throw boundary: any malformed input (bad base64, bad JSON, missing
    fields, wrong types) returns ``None`` — never an exception that could 500 the
    public read route.
    """
    if not isinstance(token_str, str) or not token_str:
        return None
    try:
        raw = base64.urlsafe_b64decode(token_str.encode())
        body = json.loads(raw)
        if not isinstance(body, dict):
            return None
        return CapabilityToken(
            meeting_id=str(body["meeting_id"]),
            scope=str(body["scope"]),
            expires_at=float(body["expires_at"]),
            signature=str(body["signature"]),
            epoch=int(body.get("epoch", 0)),
        )
    except (
        binascii.Error,
        ValueError,
        TypeError,
        KeyError,
        json.JSONDecodeError,
    ):
        return None


def verify_capability_token(
    token_str: str | None, meeting_id: str
) -> AuthzDecision | None:
    """The route-facing verifier the ``GET /m/{meeting_id}`` read path calls.

    Takes the URL string (or ``None``) + the path ``meeting_id`` and returns a
    granting :class:`AuthzDecision` for a valid, unexpired, unrevoked,
    same-meeting ``notes:read`` token, or ``None`` for every other case
    (missing/garbage/wrong-meeting/expired/tampered/revoked). The route treats
    ``None`` as "no grant" and falls to its 404/session path.

    Never throws — a malformed string decodes to ``None`` and returns ``None``, so
    the public read route can never be 500'd by a hostile token string.
    """
    if token_str is None:
        return None
    token = decode_capability_token(token_str)
    if token is None:
        return None
    decision = authorize(token=token, action=_READ_ACTION, meeting_id=str(meeting_id))
    return decision if decision.allowed else None
