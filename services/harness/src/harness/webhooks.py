"""Webhook ingest/drain — durable INSERT then 200; drain pending idempotently.

Ingest returns 200 immediately after the durable INSERT, BEFORE processing.
Pending rows are drained on boot + periodically; processing is idempotent.
webhook_events is the only external-callback durability surface (no event bus).

The drain is also the meeting-join seam (DOC03-SCRIBE-RUNTIME-NEVER-STARTED): a
Recall ``in_call`` callback is where a live bot has actually entered the room, so
that is where the harness STARTS the per-meeting notes engine — it resolves the
webhook's ``bot_id`` back to its meeting, then calls
``registry.start_meeting(header, carrier)`` so the Doc 03 serial consumer runs on
ONE ``SignalCarrier`` (Doc 02's emit end binds to the same carrier). A
``call_ended``/bot-removed callback ENDS that runtime (``registry.end_meeting``).
The registry is the boot-time singleton stashed on ``app.state.meeting_runtimes``.
"""
from __future__ import annotations

import logging
from typing import Any

from libs.db import Database, repos

# Recall bot-status event names that mean "the bot is now IN the room, listening"
# (start the notes engine) and "the call is over / bot removed" (tear it down). The
# drain matches on these; anything else is a durable no-op (still marked processed).
_IN_CALL_EVENTS = frozenset({"bot.in_call", "in_call", "bot.in_call_recording", "bot.joining_call"})
_CALL_ENDED_EVENTS = frozenset(
    {"bot.call_ended", "call_ended", "bot.done", "done", "bot.removed", "meeting_end"}
)
# Recall real-time transcript passthrough event names (AssemblyAI Universal-Streaming
# via Recall BYOK). On these the drain feeds the passthrough body onto the meeting's
# live carrier (transport's emit end) so the transcript reaches the notes engine — the
# load-bearing Doc02->Doc03 bridge (gap DOC02-DOC03-TRANSCRIPT-BRIDGE-UNWIRED).
_TRANSCRIPT_EVENTS = frozenset(
    {"transcript.data", "transcript", "bot.transcript", "transcript.partial_data"}
)
# Recall roster + bot-status event names (§3.1/§3.7). On these the drain routes the
# durably-persisted payload through the meeting's ONE ``WebhookProcessor`` bound to its
# carrier so the roster (present/join/leave) and bot-status (connected/dropped/rejoined)
# signals reach the live Scribe + Orchestrator subscribers — the C-SIGNALWIRE binding.
# Before this, these producers existed but had NO live caller (the drain dropped the events).
_ROSTER_EVENTS = frozenset({"participant.join", "participant.leave", "participant.update"})
_BOT_STATUS_EVENTS = frozenset({"bot.status"})


def ingest_webhook(event: dict[str, Any], *, store: Any) -> int:
    """Durably record the delivery, then return 200 (processing happens later)."""
    store.insert(event)
    return 200


def _event_name(payload: dict[str, Any]) -> str:
    """The Recall event name (``event``/``type``), lower-cased; '' when absent."""
    name = payload.get("event") or payload.get("type") or ""
    return str(name).strip().lower()


def _meeting_end_reason(payload: dict[str, Any]) -> str:
    """The meeting-end reason DERIVED from the webhook payload (§3.1, C-ENDOFTURN).

    Recall's terminal callback carries the real cause: prefer an explicit ``data.reason``
    (e.g. ``bot_removed``/``call_ended``); else fall back to the event name itself
    (``bot.removed``/``call_ended``/``meeting_end``). Never a hard-coded synthesized string,
    so the emitted ``MeetingEnd`` reflects what actually ended the meeting rather than a
    fabricated ``call_ended`` for every path.
    """
    data = payload.get("data")
    if isinstance(data, dict):
        reason = data.get("reason")
        if reason:
            return str(reason)
    event = _event_name(payload)
    return event or "call_ended"


def _bot_id(payload: dict[str, Any]) -> str | None:
    """The Recall ``bot_id`` from the callback body (top-level or nested ``data``)."""
    data = payload.get("data")
    if isinstance(data, dict) and data.get("bot_id"):
        return str(data["bot_id"])
    if payload.get("bot_id"):
        return str(payload["bot_id"])
    return None


async def _resolve_referent_corpus(resolved: dict[str, Any], *, db: Database) -> Any:
    """Build the meeting's referent corpus from its repo — or ``None`` if unavailable.

    The gap DOC03-REFERENT-CORPUS-UNWIRED-IN-PRODUCTION: the sole join path started the
    Scribe with NO corpus, so every §3.4 referent stayed ``binding_status='unbound'`` and
    the Workroom read zero code orientation off ``/internal/notes``. The resolved bot row
    already carries ``repo_id``; here we resolve that repo's ``full_name``, locate its
    per-tenant ``graph.db`` (the exact artifact ``code_intel.graph_store`` writes — Doc 01's
    index for that repo), and build a :class:`~scribe.referent.ReferentCorpus` pointed at it
    so the applier binds ``checkout -> payments/checkout.py::checkout`` on a real meeting.

    Fail closed to ``None`` (referents stay honestly named-but-unbound) whenever the repo is
    unknown or its index has not been built yet — never a raise on the join path.
    """
    from code_intel.paths import repo_name_from_url, tenant_repo_dir
    from scribe.referent import ReferentCorpus

    repo_id = resolved.get("repo_id")
    if repo_id is None:
        return None
    async with db.acquire() as conn:
        repo = await repos.meetings.get_repo_by_id(conn, repo_id)
    if repo is None or not repo.get("full_name"):
        return None
    repo_name = repo_name_from_url(str(repo["full_name"]))
    graph_db = tenant_repo_dir(str(repo["tenant_id"]), repo_name) / "graph.db"
    if not graph_db.exists():
        # The repo's Doc 01 index has not been built yet — start honestly unbound.
        return None
    return ReferentCorpus(db_path=str(graph_db))


def _transcript_body(payload: dict[str, Any]) -> dict[str, Any]:
    """The Recall real-time transcript passthrough body (``data`` if nested, else top).

    The confirmed wire shape (``words``/``speaker``/``timestamp``) lives under ``data``
    on Recall's callback envelope; a flat body is passed through as-is. The fail-loud
    wire parser (``transport.wire.parse_transcript``, reached via
    ``HearingStage.ingest_wire_transcript``) validates the shape — drift raises there.
    """
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    return payload


async def _dispatch_meeting_event(
    payload: dict[str, Any],
    *,
    db: Database,
    registry: Any,
    launch: Any | None = None,
) -> None:
    """Start/stop a meeting's notes engine from a Recall bot-status callback.

    On an ``in_call`` event we resolve the bot back to its meeting and START the
    per-meeting runtime on a fresh ``SignalCarrier`` (idempotent — a duplicate
    delivery returns the already-running runtime). On a ``call_ended``/removed
    event we END it. Any other event, or an unresolvable bot, is a safe no-op so
    the drain still marks the row processed (never a poison row).

    ``launch`` is the ``meeting_runtime`` deployable's provisioner seam (§3.6/§3.2):
    when supplied, an ``in_call`` event is routed THROUGH it — atomic-claim the meeting
    and launch the full run-loop spine — instead of the control_plane's Scribe-only
    ``start_meeting``. ``launch=None`` keeps the control_plane drain behaviour (notes
    engine only), so the two deployables share this one drain without either changing.
    """
    from transport.events import is_meeting_end

    name = _event_name(payload)
    is_start = name in _IN_CALL_EVENTS
    # A terminal bot-status (removed/call_ended/done) is a meeting-end, NOT a live bot-status
    # signal — route it through the end path, never the roster/bot-status carrier binding.
    is_end = name in _CALL_ENDED_EVENTS or is_meeting_end(payload)
    is_transcript = name in _TRANSCRIPT_EVENTS
    # Roster + non-terminal bot-status feed the meeting's ONE carrier via the WebhookProcessor
    # binding (C-SIGNALWIRE). A terminal bot-status already counted as ``is_end`` above is
    # excluded so it closes the meeting rather than emitting a live bot-status signal.
    is_signal = (name in _ROSTER_EVENTS or name in _BOT_STATUS_EVENTS) and not is_end
    if not (is_start or is_end or is_transcript or is_signal):
        return

    # The meeting_runtime deployable: an in_call claims + launches the full harness
    # through the provisioner (atomic claim, one-scope assembly, loop launch). The
    # provisioner resolves the bot itself and no-ops on a loss / unknown bot.
    if is_start and launch is not None:
        await launch(payload)
        return

    bot_id = _bot_id(payload)
    if bot_id is None:
        return

    async with db.acquire() as conn:
        resolved = await repos.meetings.get_by_bot_id(conn, bot_id)
    if resolved is None:
        return  # fail closed — an unknown bot never starts/ends a runtime

    meeting_id = str(resolved["id"])

    if is_start:
        # Import lazily so this module imports without transport/scribe resolved.
        from scribe.prefix import MeetingHeader
        from transport.carrier import SignalCarrier
        from transport.events import meeting_metadata

        # Populate the frozen §3.2 header from the SAME Recall webhook envelope this drain
        # already processes: transport.events.meeting_metadata reads data.title (agenda) +
        # data.participants (each {name}) verbatim from the callback (AC-EVENTS-05, never
        # synthesized) — the fix for DOC03-MEETING-HEADER-EMPTY-IN-PRODUCTION. Frozen at
        # join and byte-stable (render_header stable-sorts), so it never busts the Segment A
        # cache (§3.2). Absent metadata falls back to the empty head (honestly (none)).
        metadata = meeting_metadata(payload)
        header = MeetingHeader(
            meeting_id=meeting_id,
            agenda=metadata.title,
            participants=metadata.participants,
        )
        carrier = SignalCarrier()
        # Resolve the repo's Doc 01 index into a referent corpus so the Scribe starts with
        # code orientation (§3.4) — the fix for DOC03-REFERENT-CORPUS-UNWIRED-IN-PRODUCTION.
        # Threads start_meeting -> MeetingRuntime -> build_real_seams -> the applier.
        referent_corpus = await _resolve_referent_corpus(resolved, db=db)
        runtime = registry.start_meeting(header, carrier, referent_corpus=referent_corpus)
        # Open the consent hard-gate on this (Scribe-only) live path too (§3.1, AC-JOIN-04):
        # an ``in_call`` event means the bot joined and posted the consent notice first, so
        # the live HearingStage may observe. Without this the notes bridge would drop every
        # transcript (fail-closed by default) — the grant is what turns a confirmed join into
        # a recording meeting, and it never defaults to always-allow.
        runtime.grant_consent()
    elif is_transcript:
        # The live transcript reaches the notes engine: feed the passthrough body onto
        # the meeting's carrier (transport's emit end) so it flows carrier->coalescer->
        # Scribe->note_deltas. A transcript before in_call started the runtime is a safe
        # no-op (fail closed) — the notes engine only exists once the bot is in the room.
        runtime = registry.get(meeting_id)
        if runtime is not None:
            from transport.wire import WireDriftError

            try:
                await runtime.ingest_transcript(_transcript_body(payload))
            except WireDriftError as drift:
                # Fail LOUD but never poison the drain: a single drifted passthrough
                # message is logged for a human (CANONICAL §11.10 — no silent wire
                # assumption) and the row is still drained, so one bad message never
                # deadlocks the whole webhook queue (never a poison row).
                logging.getLogger(__name__).error(
                    "transcript wire drift on meeting %s: %s", meeting_id, drift
                )
    elif is_signal:
        # Route the durably-persisted roster / bot-status payload through the meeting's ONE
        # WebhookProcessor bound to its carrier (C-SIGNALWIRE): the derived roster
        # (present/join/leave) and bot-status (connected/dropped/rejoined) signals fan onto
        # the SAME stream the Scribe + Orchestrator subscribe to. ``_emit_for`` is the pure
        # emit step — the row is already durable, so this never re-persists. A signal before
        # in_call started the runtime is a safe no-op (fail closed — no live consumer yet).
        runtime = registry.get(meeting_id)
        if runtime is not None:
            await runtime.webhook_processor()._emit_for(payload)
    else:  # is_end
        # Derive the meeting-end reason from the ACTUAL webhook payload (§3.1, AC-TURN
        # end-of-turn single-source): the emitted ``MeetingEnd`` must carry the real cause
        # (``data.reason`` if Recall supplies one, else the event name — e.g. ``bot.removed``
        # vs ``call_ended``), never a hard-coded synthesized string. This is the C-ENDOFTURN
        # "live meeting-end reason synthesized not payload-derived" fix.
        await registry.end_meeting(meeting_id, reason=_meeting_end_reason(payload))


async def drain_pending_webhooks(
    db: Database, *, registry: Any | None = None, launch: Any | None = None
) -> int:
    """Drain every pending webhook_events row (idempotent processing).

    When a ``registry`` (the boot ``MeetingRuntimeRegistry``) is supplied, a Recall
    ``in_call`` callback STARTS the meeting's notes engine and a ``call_ended``
    callback ENDS it — this is the ONE production caller of ``start_meeting`` on the
    real join path. Processing then marks the row processed regardless (idempotent;
    a dispatch that no-ops still drains). ``registry=None`` keeps the pure-drain
    behaviour for callers that only need durability accounting.

    ``launch`` (the ``meeting_runtime`` deployable's provisioner seam) routes an
    ``in_call`` through the atomic-claim + loop-launch provisioner instead of the
    control_plane's Scribe-only start; ``launch=None`` preserves the existing drain.
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
