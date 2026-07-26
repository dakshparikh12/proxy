"""control_plane webhook intake — verify the caller, THEN durable-insert, then 200.

An inbound provider webhook (GitHub/Recall) is deduped and made durable in a
single INSERT-on-conflict into ``webhook_events`` BEFORE the 200 is returned; no
processing happens on the request path. A duplicate delivery (same
``delivery_guid``) is a no-op. Pending rows are drained idempotently on boot.
There is deliberately NO general fan-out stream — ``webhook_events`` is the only
external-callback durability (the substrate has no broker).

The Recall route (``POST /webhooks/recall``, §4.6) is on the PUBLIC_ROUTES
allowlist because a webhook caller has no session — but that exemption is EARNED,
not trusted: the HMAC signature is verified over the RAW request body via a
constant-time compare (``libs.http.verify_recall_signature``) BEFORE the durable
row lands. A forged/missing signature is a 401 and NO row lands — a forged
delivery can never dedupe-poison ``webhook_events``. The signing secret comes from
Secret Manager (``settings.recall_webhook_secret``), never a literal, and the
route fails CLOSED (401) when it is unset.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from psycopg.types.json import Json
from starlette.requests import Request

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastapi import FastAPI

# The route the §4.6 allowlist names; kept as a constant so the mount and the
# allowlist entry can never drift by a typo.
RECALL_WEBHOOK_PATH = "/webhooks/recall"


@dataclass(frozen=True)
class WebhookResponse:
    """The immediate webhook ack (returned before any processing)."""

    status: int


def ingest(
    conn: Any,
    *,
    delivery_guid: str,
    body: dict[str, Any],
    on_step: Callable[[str], None] | None = None,
) -> WebhookResponse:
    """Durably land a webhook (dedup), then return 200 — no processing yet.

    ``provider`` is named EXPLICITLY (never left to the schema DEFAULT): it is derived
    'github'|'recall' from the delivery body via the ONE canonical classifier
    (``db.repos.webhooks._derive_provider`` — a GitHub push carries ``ref``/``after``/
    ``commits``; a Recall callback carries ``event``/``bot_id``). Sharing that classifier
    keeps the sync intake and the async ``insert_event`` repo from drifting on how a
    delivery is classified. The CHECK domain still rejects any out-of-domain value.
    """
    from db.repos.webhooks import _derive_provider

    step = on_step if on_step is not None else (lambda _s: None)
    provider = _derive_provider(body)
    conn.execute(
        """
        INSERT INTO webhook_events (provider, delivery_guid, payload, status)
        VALUES (%s, %s, %s, 'pending')
        ON CONFLICT (delivery_guid) DO NOTHING
        """,
        (provider, delivery_guid, Json(body)),
    )
    step("inserted")
    # The durable INSERT precedes the 200; processing is deferred to drain.
    step("returned_200")
    return WebhookResponse(status=200)


def drain_pending(conn: Any) -> int:
    """Idempotently process every pending webhook row; return how many drained."""
    rows = conn.execute(
        "SELECT id FROM webhook_events WHERE status = 'pending'"
    ).fetchall()
    for (row_id,) in rows:
        conn.execute(
            "UPDATE webhook_events SET status = 'processed', processed_at = now() "
            "WHERE id = %s AND status = 'pending'",
            (row_id,),
        )
    return len(rows)


# --------------------------------------------------------------------------- #
# The LIVE Recall webhook route — verify the caller (§4.6), THEN land the row.
# --------------------------------------------------------------------------- #
def _recall_webhook_secret() -> str:
    """The Recall signing secret from Secret Manager via settings (never a literal).

    Read at request time (not import time) so a rotated secret is picked up and a
    test can set ``RECALL_WEBHOOK_SECRET`` before building the app. An unset secret
    is an empty string, which makes :func:`verify_recall_signature` fail CLOSED
    (401) — an unverifiable delivery is never accepted.
    """
    try:
        from harness.settings import Settings

        return str(Settings().recall_webhook_secret)
    except Exception:  # pragma: no cover - settings unavailable ⇒ fail closed below
        import os

        return os.environ.get("RECALL_WEBHOOK_SECRET", "")


def _delivery_guid(headers: Any, payload: dict[str, Any]) -> str:
    """The dedup key for a Recall delivery: the Svix message id (``webhook-id``).

    Recall's Svix ``webhook-id``/``svix-id`` is the unique per-delivery identifier —
    the natural ``delivery_guid`` for the INSERT-on-conflict dedup. It is part of
    the signed content, so by the time we read it here the signature has already
    proven it is authentic. Falls back to a payload id only if the header is absent
    (it never should be — verification would have 401'd first).
    """
    for name in ("webhook-id", "svix-id"):
        value = headers.get(name)
        if value:
            return str(value)
    return str(payload.get("id") or payload.get("bot_id") or "")


async def _land_recall_delivery(db: Any, delivery_guid: str, payload: dict[str, Any]) -> None:
    """Durably INSERT-on-conflict the (already-verified) Recall delivery.

    Uses the canonical async ``webhook_events`` repo (provider derived as 'recall')
    through the live ``app.state.db`` acquirer — the §12.10 durable-landing intake.
    Runs ONLY after the signature verified; a forged delivery never reaches here.
    """
    from db.repos import webhooks as webhook_repo

    async with db.acquire() as conn:
        await webhook_repo.insert_event(
            conn, delivery_guid, payload, provider="recall"
        )


def install_recall_webhook_route(app: "FastAPI") -> None:
    """Mount ``POST /webhooks/recall`` — HMAC-gated, public-allowlisted (§4.6).

    The route is on :data:`libs.http.PUBLIC_ROUTES` (a webhook caller has no
    session), but it earns that exemption: the signature is verified over the RAW
    request body via a constant-time compare BEFORE any durable row lands. A missing
    or mismatched signature is a 401 with NO row written; only a valid signature
    proceeds to the §12.10 durable insert-then-200 intake.

    The handler never throws: a bad signature raises an ``HTTPException(401)`` that
    the §4.6 ``safe_error_handler`` collapses to a fixed ``Unauthorized`` body (no
    internal detail leaks); an absent DB handle degrades to an honest 503.
    """
    import json

    from fastapi import HTTPException
    from starlette.responses import JSONResponse

    from libs.http import WebhookVerificationError, verify_recall_signature

    @app.post(RECALL_WEBHOOK_PATH, include_in_schema=True)
    async def recall_webhook(request: Request) -> Any:
        # Read the RAW body FIRST — the signature is over these exact bytes, never a
        # re-serialised dict. Verification happens BEFORE any parse/DB touch.
        raw_body = await request.body()
        secret = _recall_webhook_secret()
        try:
            verify_recall_signature(
                secret, headers=request.headers, raw_body=raw_body
            )
        except WebhookVerificationError as exc:
            # Fail CLOSED — a forged/missing signature is 401 and NO row lands. The
            # detail is for our logs; safeError returns only the fixed fallback body.
            raise HTTPException(status_code=401, detail=exc.detail) from exc

        # Signature proven ⇒ safe to parse + land. A non-JSON body from an
        # authenticated Recall caller is a 400 (its own bad input), never a 500.
        try:
            payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(status_code=400, detail="invalid body") from exc
        if not isinstance(payload, dict):
            payload = {"payload": payload}

        db = getattr(request.app.state, "db", None)
        if db is None:  # no substrate handle → honest 503, never a fabricated 200
            raise HTTPException(status_code=503, detail="substrate unavailable")

        delivery_guid = _delivery_guid(request.headers, payload)
        await _land_recall_delivery(db, delivery_guid, payload)
        # The durable INSERT precedes the 200; processing is deferred to the drain.
        return JSONResponse({"status": "ok"}, status_code=200)
