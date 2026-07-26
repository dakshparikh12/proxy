"""Recall webhook HMAC-signature verifier — a public route earns its exemption (§4.6).

The ``POST /webhooks/recall`` route is on the PUBLIC_ROUTES allowlist because a
webhook caller has no session. That exemption is not trust: it must PROVE it is
Recall. The HMAC IS the gate — it is our equivalent of the sibling repo's "a public
route must supply a reason". :func:`verify_recall_signature` recomputes the signature
over the **RAW request body** and refuses a missing/mismatched signature with a
401 via a **constant-time** :func:`hmac.compare_digest`, wired AHEAD of the durable
``webhook_events`` insert so a forged delivery can never dedupe-poison the table.

Wire shape — confirmed against live Recall docs at build (CANONICAL §11.10), not
guessed. Recall's webhooks are Svix-based:

* Headers (either spelling): ``webhook-id``/``svix-id``,
  ``webhook-timestamp``/``svix-timestamp``, ``webhook-signature``/``svix-signature``.
* Signed content = ``f"{id}.{timestamp}.{raw_body}"`` (the id + timestamp are part
  of the signed content, so a signature cannot be replayed under a different id).
* The signing secret is ``whsec_<base64>``; the HMAC key is the base64-decoded
  bytes AFTER the ``whsec_`` prefix.
* Algorithm: HMAC-SHA256, digest base64-encoded.
* The signature header is a **space-delimited** list of ``v1,<base64sig>`` entries
  (more than one appears during a secret rotation); the delivery is valid iff ANY
  entry's signature matches.

Sources: https://docs.recall.ai/docs/authenticating-requests-from-recallai and
https://docs.svix.com/receiving/verifying-payloads/how-manual (both confirm the
raw-body requirement and the constant-time-compare recommendation).

This module lives in ``libs/http`` (no dependency on any service) so the verifier
is a pure function the control_plane route calls; the secret is passed IN by the
caller (read from Secret Manager via settings), never read from a literal here.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
from collections.abc import Mapping
from typing import Union

# The header names Recall/Svix may use, in preference order (workspace spelling
# first, legacy svix spelling second). All lowercase — HTTP header lookups are
# case-insensitive and we normalise before reading.
_ID_HEADERS = ("webhook-id", "svix-id")
_TIMESTAMP_HEADERS = ("webhook-timestamp", "svix-timestamp")
_SIGNATURE_HEADERS = ("webhook-signature", "svix-signature")

_WHSEC_PREFIX = "whsec_"


class WebhookVerificationError(Exception):
    """A webhook whose signature is missing or does not verify — a 401 to the caller.

    Carries ``status_code = 401`` so the route (and the §4.6 ``safe_error_handler``)
    map it to a fixed ``Unauthorized`` body with NO internal detail leaked. The
    message is for our logs only; ``safeError`` collapses it before it reaches the
    external caller.
    """

    status_code = 401

    def __init__(self, detail: str = "bad signature") -> None:
        super().__init__(detail)
        self.detail = detail


def _lower_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Case-fold header keys so ``Webhook-Signature`` and ``webhook-signature`` match."""
    return {str(k).lower(): v for k, v in headers.items()}


def _pick(headers: Mapping[str, str], names: tuple[str, ...]) -> str | None:
    """Return the first present, non-empty header among ``names`` (else None)."""
    for name in names:
        value = headers.get(name)
        if value:
            return value
    return None


def _secret_key(secret: str) -> bytes:
    """Derive the raw HMAC key from a ``whsec_<base64>`` signing secret.

    The key material is the base64-decoded bytes after the ``whsec_`` prefix. A
    secret WITHOUT the prefix is treated as raw UTF-8 key bytes (a defensive
    fallback for a non-Svix secret); a malformed base64 body after the prefix
    raises a verification error rather than crashing the request path.
    """
    if secret.startswith(_WHSEC_PREFIX):
        b64 = secret[len(_WHSEC_PREFIX):]
        try:
            return base64.b64decode(b64)
        except (binascii.Error, ValueError) as exc:  # malformed secret config
            raise WebhookVerificationError("malformed signing secret") from exc
    return secret.encode("utf-8")


def _expected_signature(key: bytes, msg_id: str, timestamp: str, raw_body: bytes) -> str:
    """Compute the base64 HMAC-SHA256 of ``{id}.{timestamp}.{raw_body}``."""
    signed_content = msg_id.encode("utf-8") + b"." + timestamp.encode("utf-8") + b"." + raw_body
    digest = hmac.new(key, signed_content, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _candidate_signatures(header_value: str) -> list[str]:
    """Extract the base64 signature portion of every ``v1,<sig>`` entry.

    The header is space-delimited; each entry is ``<version>,<signature>``. We keep
    only ``v1`` signatures (the sole version Svix defines) and return their base64
    signature bodies. An entry without a comma is ignored (malformed, not a match).
    """
    sigs: list[str] = []
    for entry in header_value.split():
        version, _, sig = entry.partition(",")
        if not sig:
            continue
        if version == "v1":
            sigs.append(sig)
    return sigs


def verify_recall_signature(
    secret: str,
    *,
    headers: Mapping[str, str],
    raw_body: Union[bytes, bytearray],
) -> bool:
    """Verify a Recall webhook's HMAC signature over the RAW body — 401 on failure.

    Recomputes ``HMAC-SHA256({id}.{timestamp}.{raw_body})`` with the base64-decoded
    ``whsec_`` key and compares it — in **constant time** via
    :func:`hmac.compare_digest` — against every ``v1,<sig>`` entry in the signature
    header. Returns ``True`` on the first match. A missing id/timestamp/signature
    header, or a header with no matching signature, raises
    :class:`WebhookVerificationError` (``status_code = 401``).

    The comparison is over the **raw bytes** passed in — the caller MUST hand the
    exact request body it received, never a re-serialised dict (a JSON round-trip
    would drop/reorder bytes and break the signature).

    Args:
        secret: the ``whsec_<base64>`` signing secret (from Secret Manager, never a
            literal). An empty/unset secret is a misconfiguration → 401 (fail-closed:
            we never accept an unverifiable delivery).
        headers: the request headers (case-insensitive; ``webhook-*`` or ``svix-*``).
        raw_body: the exact raw request-body bytes as received.

    Returns:
        ``True`` when a signature matches.

    Raises:
        WebhookVerificationError: on any missing header or signature mismatch (401).
    """
    if not secret:
        # No configured secret ⇒ we cannot prove the caller ⇒ fail closed. Never
        # treat an unverifiable delivery as trusted.
        raise WebhookVerificationError("webhook secret not configured")

    low = _lower_headers(headers)
    msg_id = _pick(low, _ID_HEADERS)
    timestamp = _pick(low, _TIMESTAMP_HEADERS)
    signature_header = _pick(low, _SIGNATURE_HEADERS)
    if not msg_id or not timestamp or not signature_header:
        raise WebhookVerificationError("missing signature headers")

    key = _secret_key(secret)
    body = bytes(raw_body)
    expected = _expected_signature(key, msg_id, timestamp, body)

    candidates = _candidate_signatures(signature_header)
    if not candidates:
        raise WebhookVerificationError("no v1 signature present")

    matched = False
    for candidate in candidates:
        # Constant-time compare EVERY candidate (do not short-circuit on the first
        # match with a non-constant-time branch) — hmac.compare_digest is itself
        # constant-time, and we OR the results so the loop's timing does not leak
        # which entry matched.
        matched |= hmac.compare_digest(candidate, expected)
    if not matched:
        raise WebhookVerificationError("bad signature")
    return True


# --------------------------------------------------------------------------- #
# GitHub webhook HMAC verifier — the freshness push ingress earns its exemption. #
# --------------------------------------------------------------------------- #
# GitHub signs a webhook with ``X-Hub-Signature-256: sha256=<hexdigest>`` where the
# digest is HMAC-SHA256 over the EXACT raw request body, keyed by the App's webhook
# secret used as raw UTF-8 bytes (NOT a whsec_/base64 secret — that is Svix/Recall).
# Wire shape confirmed against GitHub docs (Securing your webhooks / validating
# deliveries): the header carries the lowercase-hex digest after ``sha256=``, and the
# recommended verification is a constant-time compare over the raw body.
_GITHUB_SIGNATURE_HEADER = "x-hub-signature-256"
_GITHUB_SIG_PREFIX = "sha256="


def verify_github_signature(
    secret: str,
    *,
    headers: Mapping[str, str],
    raw_body: Union[bytes, bytearray],
) -> bool:
    """Verify a GitHub webhook's ``X-Hub-Signature-256`` over the RAW body — 401 on fail.

    Recomputes ``HMAC-SHA256(secret, raw_body)`` (secret as raw UTF-8 key bytes,
    lowercase-hex digest) and compares it in **constant time** via
    :func:`hmac.compare_digest` against the ``sha256=<hex>`` header. A missing secret,
    a missing/malformed header, or a mismatch raises :class:`WebhookVerificationError`
    (``status_code = 401``) — we NEVER accept an unverifiable delivery (fail closed).

    The comparison is over the exact raw bytes passed in; the caller MUST hand the
    request body it received, never a re-serialised dict (a JSON round-trip would
    reorder bytes and break the signature).
    """
    if not secret:
        # No configured secret ⇒ we cannot prove the caller ⇒ fail closed.
        raise WebhookVerificationError("webhook secret not configured")

    low = _lower_headers(headers)
    header = low.get(_GITHUB_SIGNATURE_HEADER)
    if not header:
        raise WebhookVerificationError("missing signature header")
    if not header.startswith(_GITHUB_SIG_PREFIX):
        raise WebhookVerificationError("malformed signature header")
    provided = header[len(_GITHUB_SIG_PREFIX):]

    key = secret.encode("utf-8")
    body = bytes(raw_body)
    expected = hmac.new(key, body, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(provided, expected):
        raise WebhookVerificationError("bad signature")
    return True


__all__ = [
    "verify_recall_signature",
    "verify_github_signature",
    "WebhookVerificationError",
]
