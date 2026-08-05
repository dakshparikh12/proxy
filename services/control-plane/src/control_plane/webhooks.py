"""Webhook drain — process pending deliveries idempotently.

The durable INSERT-then-200 intake lives in ``webhook_routes`` (the HMAC-gated Recall
route); this module owns the DRAIN. Pending rows are drained on boot + periodically;
processing is idempotent. ``webhook_events`` is the only external-callback durability
surface (no event bus).

The drain is the reactive-workroom meeting spine (SPEC §0/§3). On a Recall ``in_call``
callback it CLAIMS + provisions the meeting through the provisioner (``launch``): a
per-meeting E2B workroom + the host-side meeting connection, wired to the transcript→wake→
respond loop. On a ``transcript.data`` callback it feeds each FINAL line into that loop
(``runtime.ingest_line`` — the workroom's ``MEETING_NOTES.md`` gets it, and the cheap wake
gate decides whether to run a reactive turn). On a ``call_ended``/bot-removed callback it
ENDS the meeting (``registry.end_meeting`` — drain in-flight turns + tear the workroom
down). The registry is the boot-time singleton stashed on ``app.state.meeting_runtimes``.
"""
from __future__ import annotations

import logging
from typing import Any

from libs.db import Database, repos

_log = logging.getLogger(__name__)

# Recall bot-status event names that mean "the bot is now IN the room" (claim + provision
# the workroom) and "the call is over / bot removed" (tear it down). The drain matches on
# these; anything else is a durable no-op (still marked processed).
_IN_CALL_EVENTS = frozenset({"bot.in_call", "in_call", "bot.in_call_recording", "bot.joining_call"})
_CALL_ENDED_EVENTS = frozenset(
    {"bot.call_ended", "call_ended", "bot.done", "done", "bot.removed", "meeting_end"}
)
# Recall real-time transcript passthrough event names (AssemblyAI Universal-Streaming via
# Recall BYOK). On a FINAL line the drain feeds it into the meeting's reactive loop.
_TRANSCRIPT_EVENTS = frozenset({"transcript.data", "transcript", "bot.transcript"})
# Recall's REAL meeting-chat event name — ``participant_events.chat_message`` (docs.recall.ai
# "Real-Time Event Payloads"): the participant-events family; payload nests
# data.data.participant{ id,name,... } + data.data.data{ text,to }. A chat line feeds the
# reactive loop too (the wake gate scans it for ``@proxy``).
_CHAT_EVENTS = frozenset({"participant_events.chat_message"})


def _event_name(payload: dict[str, Any]) -> str:
    """The Recall event name (``event``/``type``), lower-cased; '' when absent."""
    name = payload.get("event") or payload.get("type") or ""
    return str(name).strip().lower()


def _meeting_end_reason(payload: dict[str, Any]) -> str:
    """The meeting-end reason DERIVED from the webhook payload (§3.1).

    Recall's terminal callback carries the real cause: prefer an explicit ``data.reason``
    (e.g. ``bot_removed``/``call_ended``); else fall back to the event name itself. Never a
    hard-coded synthesized string."""
    data = payload.get("data")
    if isinstance(data, dict):
        reason = data.get("reason")
        if reason:
            return str(reason)
    event = _event_name(payload)
    return event or "call_ended"


def _bot_id(payload: dict[str, Any]) -> str | None:
    """The Recall ``bot_id`` from the callback body (top-level or nested ``data``).

    Recall's real-time participant-events envelope (e.g. the chat event) carries the bot as
    an OBJECT — ``data.bot.id`` — so that shape resolves too (additive; the flat ``bot_id``
    forms stay first)."""
    data = payload.get("data")
    if isinstance(data, dict) and data.get("bot_id"):
        return str(data["bot_id"])
    if isinstance(data, dict):
        bot = data.get("bot")
        if isinstance(bot, dict) and bot.get("id"):
            return str(bot["id"])
    if payload.get("bot_id"):
        return str(payload["bot_id"])
    return None


def _transcript_body(payload: dict[str, Any]) -> dict[str, Any]:
    """Descend to the dict that actually carries the transcript fields.

    Recall nests the real-time transcript under ``data`` — and for the ``assembly_ai_v3_streaming``
    provider, under ``data.data`` (the ``words`` array + ``participant`` sit there, NOT at the first
    ``data`` level). Unwrap nested ``data`` envelopes (bounded) until we reach the level that has
    ``words``/``text``/``transcript`` (or ``participant``); a flat body is returned as-is. This is
    what lets the REAL Recall payload parse, not only the flat unit-stub shape — without it every
    live transcript is dropped and Proxy never wakes (verified against Recall's documented payload)."""
    body: Any = payload
    for _ in range(3):  # bounded unwrap of nested ``data`` envelopes
        if not isinstance(body, dict):
            break
        if any(k in body for k in ("words", "text", "transcript")) or "participant" in body:
            return body
        nxt = body.get("data")
        if not isinstance(nxt, dict):
            return body
        body = nxt
    return body if isinstance(body, dict) else payload


def _transcript_ts(body: dict[str, Any]) -> float:
    """The line's timestamp (seconds). Recall's word objects carry ``start_timestamp.relative``;
    a flat body may carry ``timestamp``. Missing → 0.0 (a benign ordering value)."""
    ts = body.get("timestamp")
    if ts is not None:
        try:
            return float(ts)
        except (TypeError, ValueError):
            pass
    words = body.get("words")
    if isinstance(words, list) and words and isinstance(words[0], dict):
        st = words[0].get("start_timestamp")
        if isinstance(st, dict):
            try:
                return float(st.get("relative"))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                pass
    return 0.0


def _transcript_text(body: dict[str, Any]) -> str:
    """The utterance text from a transcript body, tolerant of BOTH real Recall shapes.

    Recall's AssemblyAI passthrough is NOT settled to one shape and we have no live cassette
    to pin it (``WIRE_SCHEMA_PROVENANCE = "@build-confirm"``), so we accept both documented
    forms rather than assume one and silently drop every voice line (the "Proxy never wakes"
    failure mode):

    * ``words`` as a plain string — the whole utterance (the shape the unit stubs use).
    * ``words`` as a LIST of word objects — ``[{"text"/"word": "hi", ...}, ...]`` (AssemblyAI
      Universal-Streaming's real shape) — joined into the utterance.
    * a fallback ``text``/``transcript`` string key when ``words`` is absent.

    Returns "" when there is nothing intelligible to feed (a safe no-op upstream)."""
    words = body.get("words")
    if isinstance(words, str):
        return words
    if isinstance(words, list):
        parts = [
            str(w.get("text") or w.get("word") or "")
            for w in words
            if isinstance(w, dict)
        ]
        joined = " ".join(p for p in parts if p).strip()
        if joined:
            return joined
    for key in ("text", "transcript"):
        val = body.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return ""


def _transcript_line(body: dict[str, Any]) -> tuple[str, str, float] | None:
    """Adapt one transcript body to ``(speaker, text, ts)``, or ``None`` if it has no words.

    Field mapping: ``{words|text|transcript, speaker|participant.name, timestamp}`` →
    ``(speaker, text, ts)``. Empty/absent text → ``None`` (nothing to feed — a safe no-op).
    Never raises."""
    text = _transcript_text(body)
    if not text.strip():
        return None
    timestamp = _transcript_ts(body)
    speaker = body.get("speaker")
    if not speaker:
        # AssemblyAI/Recall nests the talker under ``participant`` on some shapes.
        participant = body.get("participant")
        if isinstance(participant, dict):
            speaker = participant.get("name") or participant.get("id")
    return str(speaker or ""), text, timestamp


def _chat_line(payload: dict[str, Any]) -> tuple[str, str] | None:
    """Adapt one Recall chat event to ``(sender, text)``, or ``None`` if it has no text.

    The documented ``participant_events.chat_message`` envelope nests
    ``data.data.participant`` (the sender) and ``data.data.data.text`` (the message). A
    flatter body (``sender``/``message`` or ``text`` directly under ``data``) adapts too."""
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    raw_inner = data.get("data")
    inner: dict[str, Any] = raw_inner if isinstance(raw_inner, dict) else data
    raw_leaf = inner.get("data")
    leaf: dict[str, Any] = raw_leaf if isinstance(raw_leaf, dict) else inner
    text = leaf.get("text") if isinstance(leaf.get("text"), str) else leaf.get("message")
    if not isinstance(text, str) or not text.strip():
        return None
    raw_participant = inner.get("participant")
    participant: dict[str, Any] = raw_participant if isinstance(raw_participant, dict) else {}
    sender = participant.get("name") or participant.get("id") or inner.get("sender") or ""
    return str(sender), text


async def _dispatch_meeting_event(
    payload: dict[str, Any],
    *,
    db: Database,
    registry: Any,
    launch: Any | None = None,
) -> None:
    """Claim/feed/end a meeting's reactive workroom from a Recall callback.

    On an ``in_call`` event, if ``launch`` is supplied, the meeting is CLAIMED + provisioned
    through the provisioner (atomic claim + workroom + connection + loop). On a
    ``transcript.data`` FINAL line the drain feeds it into the meeting's reactive loop
    (``runtime.ingest_line``). On a chat line it feeds the same loop (the wake gate scans for
    ``@proxy``). On a ``call_ended``/removed event it ENDS the meeting. Any other event, or
    an unresolvable bot, is a safe no-op so the drain still marks the row processed (never a
    poison row). ``launch=None`` keeps a pure-drain behaviour for callers that only need
    durability accounting.
    """
    name = _event_name(payload)
    is_start = name in _IN_CALL_EVENTS
    is_end = name in _CALL_ENDED_EVENTS
    is_transcript = name in _TRANSCRIPT_EVENTS
    is_chat = name in _CHAT_EVENTS
    if not (is_start or is_end or is_transcript or is_chat):
        return

    # An in_call claims + provisions the workroom runtime through the provisioner. The
    # provisioner resolves the bot itself and no-ops on a loss / unknown bot.
    if is_start:
        if launch is not None:
            await launch(payload)
        return

    bot_id = _bot_id(payload)
    if bot_id is None:
        return

    async with db.acquire() as conn:
        resolved = await repos.meetings.get_by_bot_id(conn, bot_id)
    if resolved is None:
        return  # fail closed — an unknown bot never feeds/ends a runtime
    meeting_id = str(resolved["id"])

    if is_transcript:
        # Feed each FINAL line into the meeting's reactive loop: the workroom's MEETING_NOTES.md
        # recovery record gets it (continuous), and the cheap wake gate decides whether to run a
        # reactive turn (recall itself is resident in the warm session's cache, fed the delta per
        # wake — not this file). A transcript before the runtime is provisioned is a safe
        # no-op (fail closed — ingest_line no-ops when the session is unwired).
        runtime = registry.get(meeting_id)
        if runtime is None:
            return
        line = _transcript_line(_transcript_body(payload))
        if line is None:
            return
        speaker, text, ts = line
        try:
            await runtime.ingest_line(speaker, text, ts=ts)
        except Exception:  # noqa: BLE001 - the feed path is designed never-raise; an escape is
            # logged for a human and the drain continues, so one bad line never leaves the
            # row unprocessed (never a poison row).
            _log.exception(
                "transcript feed failed on meeting %s (never-raise boundary escaped) — the "
                "row still drains",
                meeting_id,
            )
    elif is_chat:
        # Meeting chat → the same reactive loop (the wake gate scans for ``@proxy``). A chat
        # before the runtime is provisioned is a safe no-op (fail closed).
        runtime = registry.get(meeting_id)
        if runtime is None:
            return
        chat = _chat_line(payload)
        if chat is None:
            return
        sender, text = chat
        try:
            await runtime.ingest_line(sender, text, is_chat=True)
        except Exception:  # noqa: BLE001 - same never-raise boundary as the transcript feed.
            _log.exception(
                "chat feed failed on meeting %s (never-raise boundary escaped) — the row "
                "still drains",
                meeting_id,
            )
    else:  # is_end
        # Derive the meeting-end reason from the ACTUAL webhook payload (§3.1): drain
        # in-flight turns + tear the workroom down + drop the runtime (idempotent).
        await registry.end_meeting(meeting_id, reason=_meeting_end_reason(payload))


async def drain_pending_webhooks(
    db: Database, *, registry: Any | None = None, launch: Any | None = None
) -> int:
    """Drain every pending webhook_events row (idempotent processing).

    When a ``registry`` (the boot ``MeetingRuntimeRegistry``) is supplied, a Recall
    ``in_call`` callback CLAIMS + provisions the meeting's workroom, ``transcript.data``/chat
    feed the reactive loop, and ``call_ended`` ENDS it. Processing then marks the row
    processed regardless (idempotent; a dispatch that no-ops still drains). ``registry=None``
    keeps the pure-drain behaviour for callers that only need durability accounting.

    ``launch`` routes an ``in_call`` through the atomic-claim + loop-launch provisioner;
    ``launch=None`` still drains but starts no meeting.
    """
    drained = 0
    async with db.acquire() as conn:
        pending = await repos.webhooks.list_pending(conn)

    for event in pending:
        if registry is not None:
            payload = event.get("payload") or {}
            if isinstance(payload, str):
                import json

                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = {}
            await _dispatch_meeting_event(
                payload, db=db, registry=registry, launch=launch
            )
        async with db.acquire() as conn:
            await repos.webhooks.mark_processed(conn, event["id"])
        drained += 1
    return drained
