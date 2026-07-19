"""Recall webhook → live signal derivation (§3.1 / §3.9-2, AC-EVENTS-01..14).

Recall webhooks (participant join/leave/update, bot-status, meeting-end) are the ONLY
source of roster/meeting-end/bot-status signals — every emitted signal is traceable to a
field in the source payload, never synthesized (AC-EVENTS-04). Each delivery lands
**durably first**: an idempotent ``INSERT ... ON CONFLICT (delivery_guid) DO NOTHING``
(doc00 ``libs/db`` ``webhook_events``), the endpoint returns 200, and only a *newly*
inserted delivery is processed — a duplicate ``delivery_guid`` is a no-op, so downstream
signals fire exactly once (AC-EVENTS-09/10). Meeting-end is emitted ONLY on the explicit
webhook, never inferred from silence (AC-EVENTS-06/07); it triggers the close sequence
*after* the meeting-end signal (AC-EVENTS-08). Roster/meeting-end/bot-status are internal
over-transport events, outside the client ``ProxyMessage`` registry (AC-EVENTS-11).
"""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from contracts.registry import assert_registry_closed

from .carrier import SignalCarrier
from .signals import BotStatus, MeetingEnd, MeetingMetadata, RosterEvent, Signal

# Confirmed Recall webhook event tags (pinned at build, §3.9-2).
_EVT_JOIN = "participant.join"
_EVT_LEAVE = "participant.leave"
_EVT_UPDATE = "participant.update"
_EVT_BOT_STATUS = "bot.status"
_EVT_MEETING_END = "meeting.end"
_EVT_BOT_REMOVED = "bot.removed"
_VALID_BOT_STATUS = {"connected", "dropped", "rejoined"}
# Terminal bot-status codes that mean the bot was removed from the call (not a transient
# drop that rejoins) — these close the meeting exactly like an explicit meeting-end (§3.1).
_REMOVED_BOT_STATUS = {"removed", "call_ended", "done"}


class DurableStore(Protocol):
    """Injected durability seam over doc00 ``webhook_events.insert_event``."""

    async def insert_event(self, delivery_guid: str, payload: dict[str, Any]) -> bool:
        """Durably record a delivery; return True if newly inserted (else a dedup no-op)."""
        ...


@dataclass
class ProcessResult:
    """Outcome of one webhook delivery — honest about persist/dedupe/emit."""

    persisted: bool
    duplicate: bool
    emitted: list[Signal] = field(default_factory=list)


def is_bot_removed(payload: dict[str, Any]) -> bool:
    """True for an explicit bot-removed webhook (dedicated event or a terminal bot-status).

    A removal is terminal — the bot is out of the call for good — unlike a transient
    ``dropped`` that rejoins; it closes the meeting exactly like a meeting-end (§3.1).
    """
    event = payload.get("event")
    if event == _EVT_BOT_REMOVED:
        return True
    if event == _EVT_BOT_STATUS:
        return payload.get("data", {}).get("status") in _REMOVED_BOT_STATUS
    return False


def is_meeting_end(payload: dict[str, Any]) -> bool:
    """True ONLY for an explicit meeting-closed OR bot-removed webhook — never inferred
    from silence (AC-EVENTS-06/07; R-doc02-EVENTS-07: "meeting closes / bot removed")."""
    return payload.get("event") == _EVT_MEETING_END or is_bot_removed(payload)


def meeting_metadata(payload: dict[str, Any]) -> MeetingMetadata:
    """Title + participant list passed through verbatim from the source (AC-EVENTS-05)."""
    data = payload.get("data", {})
    parts = tuple(p.get("name", "") for p in data.get("participants", []))
    return MeetingMetadata(title=str(data.get("title", "")), participants=parts)


class WebhookProcessor:
    """Derives + emits live signals from durable Recall webhooks for one meeting."""

    def __init__(
        self,
        carrier: SignalCarrier,
        *,
        now: Callable[[], float] = time.monotonic,
        on_meeting_end: Callable[[MeetingEnd], Awaitable[None]] | None = None,
        on_metadata: Callable[[MeetingMetadata], Awaitable[None]] | None = None,
    ) -> None:
        self._carrier = carrier
        self._now = now
        self._on_meeting_end = on_meeting_end
        # Meeting title + participant-list context is delivered to the Orchestrator through
        # a dedicated init hook (AC-EVENTS-05), NOT the §3.10 nine-signal carrier surface —
        # metadata is not one of the nine emitted signals, so it must never widen that set.
        self._on_metadata = on_metadata
        self._names: dict[str, str] = {}
        self._snapshot_done = False
        self._metadata_done = False
        # Title and the initial participant list can ride on SEPARATE payloads; both are
        # accumulated here so neither is ever dropped before delivery (AC-EVENTS-05).
        self._meta_title = ""
        self._meta_participants: tuple[str, ...] = ()

    async def process(self, payload: dict[str, Any], delivery_guid: str, *, store: DurableStore) -> ProcessResult:
        """Persist durably, return 200, then process exactly once (skip duplicates)."""
        newly = await store.insert_event(delivery_guid, payload)
        if not newly:  # duplicate delivery_guid → no-op; no double signal (AC-EVENTS-10)
            return ProcessResult(persisted=True, duplicate=True)
        emitted = await self._emit_for(payload)
        return ProcessResult(persisted=True, duplicate=False, emitted=emitted)

    async def _emit_for(self, payload: dict[str, Any]) -> list[Signal]:
        emitted: list[Signal] = []

        # Meeting metadata (title + participant list) is initialized as Orchestrator
        # context once, off the signal surface (AC-EVENTS-05).
        await self._deliver_metadata(payload)

        # Initial present-set snapshot precedes the live deltas (AC-EVENTS-14).
        for ev in self._initial_present_set(payload):
            await self._carrier.emit(ev)
            emitted.append(ev)

        # Name-change cache update so subsequent roster events carry current name (AC-EVENTS-13).
        self._update_names(payload)

        # Live roster join/leave deltas, traceable to the payload (AC-EVENTS-01..04).
        for ev in self._roster_deltas(payload):
            await self._carrier.emit(ev)
            emitted.append(ev)

        # bot-status routed to the harness after durable persist (AC-EVENTS-12).
        bot_status = self._bot_status(payload)
        if bot_status is not None:
            await self._carrier.emit(bot_status)
            emitted.append(bot_status)

        # Meeting-end only on the explicit webhook; close sequence AFTER it (AC-EVENTS-06/08).
        if is_meeting_end(payload):
            end = MeetingEnd(reason=str(payload.get("data", {}).get("reason", "meeting_end")))
            await self._carrier.emit(end)
            emitted.append(end)
            if self._on_meeting_end is not None:
                await self._on_meeting_end(end)

        return emitted

    async def _deliver_metadata(self, payload: dict[str, Any]) -> None:
        """Hand the title + participant-list context to the Orchestrator exactly once.

        The title and the initial participant list can arrive on DIFFERENT payloads (the
        roster may ride on bot-join/connected while the title lands on a later
        meeting-init). Both fields are accumulated across payloads and delivered together
        the moment both are known, so a split title/roster never loses a field — the exact
        AC-EVENTS-05 case (given a KNOWN title; metadata_fields_dropped_allowed=0;
        F-METADATA-NOT-PASSED). Delivered once, never re-sent, and never on the §3.10
        nine-signal carrier surface. NOTE: a meeting that never carries a title (an untitled
        ad-hoc call — outside the AC-EVENTS-05 given) defers this context until a title
        arrives; that ordering is arbitrated by the sealed T-EVENTS-05, not asserted here.
        """
        if self._metadata_done or self._on_metadata is None:
            return
        data = payload.get("data", {})
        title = data.get("title")
        if title and not self._meta_title:
            self._meta_title = str(title)
        participants = data.get("participants")
        if participants and not self._meta_participants:
            self._meta_participants = tuple(str(p.get("name", "")) for p in participants)
        # Deliver only once BOTH fields are in hand — accumulating across payloads means a
        # split title/roster never loses a field (the prior first-either-field gate dropped
        # whichever field the first payload lacked).
        if self._meta_title and self._meta_participants:
            self._metadata_done = True
            await self._on_metadata(
                MeetingMetadata(title=self._meta_title, participants=self._meta_participants)
            )

    def _initial_present_set(self, payload: dict[str, Any]) -> list[RosterEvent]:
        # The present-set snapshot fires on the FIRST payload carrying a full participant
        # roster, regardless of the event tag — a meeting Proxy joins mid-session delivers
        # its already-present roster on bot-join/connected (or a meeting-init), not only on
        # a per-participant `participant.join` delta. Gating solely on participant.join
        # would silently omit everyone already in the room (AC-EVENTS-14,
        # present_participants_missing_from_initial_snapshot_allowed=0; F-INITIAL-PRESENT-SET-MISSING).
        if self._snapshot_done:
            return []
        participants = payload.get("data", {}).get("participants")
        if not participants:
            return []
        self._snapshot_done = True
        snapshot: list[RosterEvent] = []
        for p in participants:
            pid, name = str(p.get("id", "")), str(p.get("name", ""))
            self._names[pid] = name
            snapshot.append(RosterEvent(kind="present", name=name, participant_id=pid))
        return snapshot

    def _update_names(self, payload: dict[str, Any]) -> None:
        if payload.get("event") != _EVT_UPDATE:
            return
        p = payload.get("data", {}).get("participant", {})
        pid, name = str(p.get("id", "")), p.get("name")
        if pid and name:
            self._names[pid] = str(name)

    def _roster_deltas(self, payload: dict[str, Any]) -> list[RosterEvent]:
        event = payload.get("event")
        if event not in (_EVT_JOIN, _EVT_LEAVE):
            return []
        p = payload.get("data", {}).get("participant")
        if not p:
            return []
        pid = str(p.get("id", ""))
        name = self._names.get(pid) or str(p.get("name", ""))
        if not name:  # a join/leave with no resolvable name is not a complete signal
            return []
        self._names[pid] = name
        if event == _EVT_JOIN:
            return [RosterEvent(kind="join", name=name, participant_id=pid)]
        return [RosterEvent(kind="leave", name=name, participant_id=pid)]

    def _bot_status(self, payload: dict[str, Any]) -> BotStatus | None:
        if payload.get("event") != _EVT_BOT_STATUS:
            return None
        status = payload.get("data", {}).get("status")
        if status not in _VALID_BOT_STATUS:
            return None
        return BotStatus(status=status, t=self._now())


def registry_excludes_signal_surface() -> bool:
    """Self-check: the client registry closes WITHOUT the transport signal surface (AC-EVENTS-11)."""
    assert_registry_closed()  # raises if a signal leaked into the client registry
    return True
