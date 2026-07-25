"""Doc 08 · §4.6 — the Recall webhook HMAC-signature gate (``experience.hmac-webhook-verify``).

The webhook receiver in ``control_plane/webhooks.py`` durably lands a delivery
(INSERT-on-conflict→200→drain) but must NOT trust the caller. This node adds
``libs/http/webhook.py``'s ``verify_recall_signature``: a constant-time
``hmac.compare_digest`` over the RAW request body, wired AHEAD of the durable
insert. A public route earns its exemption by PROVING the caller — the HMAC IS the
gate.

The Recall webhook signature is Svix-based (confirmed against live docs, §11.10):
  * headers ``webhook-id``/``svix-id``, ``webhook-timestamp``/``svix-timestamp``,
    ``webhook-signature``/``svix-signature``;
  * signed content = ``f"{id}.{timestamp}.{raw_body}"``;
  * secret is ``whsec_<base64>``; the HMAC key is the base64-decoded remainder;
  * HMAC-SHA256, base64-encoded digest;
  * the signature header is a space-delimited list of ``v1,<base64sig>`` entries
    (multiple during a secret rotation).

These tests run on the REAL path (the real verifier, and the real control_plane
app for the live-route facts). Product imports live inside the test bodies so the
module COLLECTS clean and fails RED before the code exists.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Any

import pytest


# --------------------------------------------------------------------------- #
# Helpers — construct a Svix-shaped signature exactly as Recall would.
# --------------------------------------------------------------------------- #
_SECRET = "whsec_" + base64.b64encode(b"proxy-recall-test-key-0123456789").decode()


def _sign(secret: str, msg_id: str, timestamp: str, body: bytes) -> str:
    """Produce a valid ``webhook-signature`` header value for the given raw body."""
    key = base64.b64decode(secret.split("_", 1)[1])
    signed_content = f"{msg_id}.{timestamp}.{body.decode('utf-8')}".encode("utf-8")
    digest = hmac.new(key, signed_content, hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode("utf-8")


def _headers(sig: str, msg_id: str = "msg_abc", ts: str = "1700000000") -> dict[str, str]:
    return {
        "webhook-id": msg_id,
        "webhook-timestamp": ts,
        "webhook-signature": sig,
    }


# --------------------------------------------------------------------------- #
# 1 · the verifier itself — over the RAW body, constant-time, fail-closed
# --------------------------------------------------------------------------- #
def test_valid_signature_passes() -> None:
    """A correctly-signed raw body verifies True against the secret."""
    from libs.http.webhook import verify_recall_signature

    body = b'{"event":"bot.status_change","data":{"bot_id":"b1"}}'
    sig = _sign(_SECRET, "msg_abc", "1700000000", body)
    assert (
        verify_recall_signature(_SECRET, headers=_headers(sig), raw_body=body) is True
    )


def test_signature_is_computed_over_the_raw_body_not_reparsed() -> None:
    """Even one byte of difference in the raw body invalidates the signature
    (the sig is over the exact bytes, never a re-serialised dict)."""
    from libs.http.webhook import verify_recall_signature

    body = b'{"event":"bot.status_change","data":{"bot_id":"b1"}}'
    sig = _sign(_SECRET, "msg_abc", "1700000000", body)
    tampered = body + b" "  # a whitespace byte a JSON re-encode would drop
    with pytest.raises(Exception) as ei:
        verify_recall_signature(_SECRET, headers=_headers(sig), raw_body=tampered)
    assert getattr(ei.value, "status_code", None) == 401


def test_bad_signature_raises_401() -> None:
    """A mismatched signature is rejected with a 401-carrying error (never True)."""
    from libs.http.webhook import verify_recall_signature

    body = b'{"event":"bot.status_change"}'
    bad = "v1," + base64.b64encode(b"not-the-right-digest-000000000000").decode()
    with pytest.raises(Exception) as ei:
        verify_recall_signature(_SECRET, headers=_headers(bad), raw_body=body)
    assert getattr(ei.value, "status_code", None) == 401


def test_missing_signature_header_raises_401() -> None:
    """A delivery with NO signature header is rejected 401 — never silently allowed."""
    from libs.http.webhook import verify_recall_signature

    body = b'{"event":"bot.status_change"}'
    with pytest.raises(Exception) as ei:
        verify_recall_signature(
            _SECRET,
            headers={"webhook-id": "msg_abc", "webhook-timestamp": "1700000000"},
            raw_body=body,
        )
    assert getattr(ei.value, "status_code", None) == 401


def test_empty_signature_header_raises_401() -> None:
    """An empty signature header string is a missing signature — 401."""
    from libs.http.webhook import verify_recall_signature

    body = b'{"event":"bot.status_change"}'
    with pytest.raises(Exception) as ei:
        verify_recall_signature(_SECRET, headers=_headers(""), raw_body=body)
    assert getattr(ei.value, "status_code", None) == 401


def test_svix_prefixed_headers_are_accepted() -> None:
    """The legacy ``svix-*`` header spelling verifies identically to ``webhook-*``."""
    from libs.http.webhook import verify_recall_signature

    body = b'{"event":"bot.joined"}'
    sig = _sign(_SECRET, "msg_xyz", "1699999999", body)
    headers = {
        "svix-id": "msg_xyz",
        "svix-timestamp": "1699999999",
        "svix-signature": sig,
    }
    assert verify_recall_signature(_SECRET, headers=headers, raw_body=body) is True


def test_id_and_timestamp_are_part_of_the_signed_content() -> None:
    """The signed content binds id+timestamp: a sig valid for one id must NOT
    verify when replayed under a different webhook-id (anti-replay binding)."""
    from libs.http.webhook import verify_recall_signature

    body = b'{"event":"bot.joined"}'
    sig = _sign(_SECRET, "msg_original", "1700000000", body)
    # same body + same signature, but a swapped id → the signed content differs.
    with pytest.raises(Exception) as ei:
        verify_recall_signature(
            _SECRET, headers=_headers(sig, msg_id="msg_swapped"), raw_body=body
        )
    assert getattr(ei.value, "status_code", None) == 401


def test_multiple_space_delimited_signatures_one_valid_passes() -> None:
    """During a secret rotation Recall sends several ``v1,<sig>`` entries; if ANY
    matches, the delivery verifies (the real header carries space-delimited sigs)."""
    from libs.http.webhook import verify_recall_signature

    body = b'{"event":"bot.done"}'
    good = _sign(_SECRET, "msg_abc", "1700000000", body)
    junk = "v1," + base64.b64encode(b"old-rotated-out-key-000000000000").decode()
    combined = f"{junk} {good}"  # junk first, valid second — space-delimited
    assert (
        verify_recall_signature(_SECRET, headers=_headers(combined), raw_body=body)
        is True
    )


def test_verify_uses_constant_time_compare() -> None:
    """The comparison MUST be constant-time (``hmac.compare_digest``) — a plain
    ``==`` is a timing-attack surface (node DoD: 'NOT done if compare is
    non-constant-time'). We assert the module calls compare_digest by spying on it."""
    import libs.http.webhook as webhook_mod

    body = b'{"event":"bot.status_change"}'
    sig = _sign(_SECRET, "msg_abc", "1700000000", body)

    calls: list[Any] = []
    real = hmac.compare_digest

    def _spy(a: Any, b: Any) -> bool:
        calls.append((a, b))
        return real(a, b)

    orig = webhook_mod.hmac.compare_digest
    webhook_mod.hmac.compare_digest = _spy  # type: ignore[assignment]
    try:
        webhook_mod.verify_recall_signature(
            _SECRET, headers=_headers(sig), raw_body=body
        )
    finally:
        webhook_mod.hmac.compare_digest = orig  # type: ignore[assignment]

    assert calls, "verify_recall_signature must use hmac.compare_digest (constant-time)"


# --------------------------------------------------------------------------- #
# 2 · the LIVE route wiring — verify BEFORE the durable row lands
# --------------------------------------------------------------------------- #
class _RecordingConn:
    """A stand-in DB connection that records whether the durable INSERT ran."""

    def __init__(self) -> None:
        self.inserts: list[tuple[Any, ...]] = []

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        if "INSERT INTO webhook_events" in sql:
            self.inserts.append(args)
        return {"id": "row-1"} if self.inserts else None

    async def execute(self, sql: str, *args: Any) -> Any:
        return None


class _AcquireCtx:
    def __init__(self, conn: _RecordingConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _RecordingConn:
        return self._conn

    async def __aexit__(self, *a: Any) -> None:
        return None


class _FakeDB:
    def __init__(self, conn: _RecordingConn) -> None:
        self._conn = conn

    def acquire(self) -> _AcquireCtx:
        return _AcquireCtx(self._conn)


@pytest.fixture()
def live_app_and_conn(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The REAL control_plane app with the Recall webhook route mounted, plus a
    recording DB handle on ``app.state.db`` so we can prove NO row lands on a bad sig."""
    monkeypatch.setenv("RECALL_WEBHOOK_SECRET", _SECRET)
    from services.control_plane import create_app

    app = create_app()
    conn = _RecordingConn()
    app.state.db = _FakeDB(conn)
    return app, conn


def test_route_is_mounted_and_public_allowlisted(live_app_and_conn: Any) -> None:
    """POST /webhooks/recall is a real mounted route AND classifies as public (it is
    in PUBLIC_ROUTES) — never ``raw``, never protected() (a webhook has no session)."""
    app, _conn = live_app_and_conn
    from libs.http import PUBLIC_ROUTES, classify_route
    from libs.http.registry import route_key

    keys = {route_key(r): r for r in app.routes if route_key(r) is not None}
    assert "POST /webhooks/recall" in keys, "the Recall webhook route must be mounted"
    assert "POST /webhooks/recall" in PUBLIC_ROUTES
    assert classify_route(keys["POST /webhooks/recall"]) == "public"


def test_bad_signature_401_and_no_row_lands(live_app_and_conn: Any) -> None:
    """A forged delivery is rejected 401 and NEVER reaches the durable insert —
    a forged body must not dedupe-poison webhook_events (node risk #2)."""
    from fastapi.testclient import TestClient

    app, conn = live_app_and_conn
    client = TestClient(app)
    body = b'{"event":"bot.status_change","data":{"bot_id":"evil"}}'
    bad = "v1," + base64.b64encode(b"forged-digest-0000000000000000000").decode()
    resp = client.post(
        "/webhooks/recall",
        content=body,
        headers={**_headers(bad), "content-type": "application/json"},
    )
    assert resp.status_code == 401
    assert conn.inserts == [], "a bad-signature delivery must land NO row (verify first)"


def test_missing_signature_401_and_no_row_lands(live_app_and_conn: Any) -> None:
    """A delivery with no signature header at all is 401 with no row landed."""
    from fastapi.testclient import TestClient

    app, conn = live_app_and_conn
    client = TestClient(app)
    resp = client.post(
        "/webhooks/recall",
        content=b'{"event":"bot.status_change"}',
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 401
    assert conn.inserts == []


def test_valid_signature_proceeds_to_durable_insert_then_200(live_app_and_conn: Any) -> None:
    """A validly-signed delivery passes the gate and reaches the durable insert,
    returning 200 (the §12.10 INSERT-on-conflict→200→drain intake)."""
    from fastapi.testclient import TestClient

    app, conn = live_app_and_conn
    client = TestClient(app)
    body = b'{"event":"bot.status_change","data":{"bot_id":"b-good"}}'
    sig = _sign(_SECRET, "msg_good", "1700000000", body)
    resp = client.post(
        "/webhooks/recall",
        content=body,
        headers={**_headers(sig, msg_id="msg_good"), "content-type": "application/json"},
    )
    assert resp.status_code == 200
    assert len(conn.inserts) == 1, "a valid delivery must land exactly one durable row"


def test_secret_is_read_from_settings_not_hardcoded(live_app_and_conn: Any) -> None:
    """The secret comes from config (env/Secret Manager), never a literal in code —
    invariant: 'secrets only from Secret Manager, never hard-coded'. We prove the
    route rejects when the configured secret does not match the signer's."""
    import base64 as _b64

    from fastapi.testclient import TestClient

    app, conn = live_app_and_conn
    client = TestClient(app)
    other = "whsec_" + _b64.b64encode(b"a-completely-different-secret-key").decode()
    body = b'{"event":"bot.status_change"}'
    sig = _sign(other, "msg_x", "1700000000", body)  # signed with the WRONG secret
    resp = client.post(
        "/webhooks/recall",
        content=body,
        headers={**_headers(sig, msg_id="msg_x"), "content-type": "application/json"},
    )
    assert resp.status_code == 401
    assert conn.inserts == []
