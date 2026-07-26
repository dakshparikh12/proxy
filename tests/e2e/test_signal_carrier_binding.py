"""C-SIGNALWIRE — the 9-signal transport→carrier binding reaches its LIVE consumer.

Doc 02 §3.10 names nine signals: transcript · chat · roster · speaking · boundary ·
barge-in · bot-status · meeting-end · channel-report. The producers all existed, but the
Doc-02 master hole was that several (roster, bot-status, meeting-end) had NO live caller —
the harness webhook drain classified only in_call/transcript/call_ended and DROPPED
participant-join/leave + non-terminal bot-status webhooks, so those signals never reached
the live carrier the Scribe + Orchestrator subscribe to.

This proves the binding: a per-meeting ``WebhookProcessor`` bound to the meeting's ONE
``SignalCarrier`` (``MeetingRuntime.webhook_processor``) fans roster + bot-status +
meeting-end onto the SAME stream a subscriber reads. No DB / vendor / network — the
in-process carrier is the whole seam (§2), so this runs offline in the composition tier.
"""
from __future__ import annotations

import asyncio

def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _runtime():
    from scribe.pipeline import HostBudget
    from scribe.prefix import MeetingHeader
    from transport.carrier import SignalCarrier

    from harness.meeting_runtime import MeetingRuntime

    header = MeetingHeader(meeting_id="m-signalwire", agenda="", participants=())
    carrier = SignalCarrier()
    # db=None: this seam is the carrier fan-out only; the Scribe consumer is not started.
    return MeetingRuntime(header=header, carrier=carrier, db=None, host_budget=HostBudget(limit=1))


def test_roster_and_bot_status_reach_the_live_carrier_through_the_one_binding():
    """Roster (present/join/leave) + bot-status (connected/dropped/rejoined) + meeting-end all
    fan onto the meeting's ONE carrier via ``MeetingRuntime.webhook_processor`` (C-SIGNALWIRE).
    """
    from transport.signals import BotStatus, MeetingEnd, RosterEvent, signal_name

    runtime = _runtime()

    received: list = []

    async def run():
        sub = runtime.carrier.subscribe()

        async def drain():
            async for sig in sub:
                received.append(sig)

        pump = asyncio.ensure_future(drain())
        proc = runtime.webhook_processor()

        # 1) initial present-set snapshot (roster 'present' for each already-in-room member)
        await proc._emit_for(
            {"event": "meeting.init", "data": {"participants": [{"id": "p1", "name": "Sam"}]}}
        )
        # 2) a live join delta
        await proc._emit_for(
            {"event": "participant.join", "data": {"participant": {"id": "p2", "name": "Maya"}}}
        )
        # 3) a live leave delta
        await proc._emit_for(
            {"event": "participant.leave", "data": {"participant": {"id": "p2", "name": "Maya"}}}
        )
        # 4) a non-terminal bot-status (a transient drop, not a removal)
        await proc._emit_for({"event": "bot.status", "data": {"status": "dropped"}})
        # 5) an explicit meeting-end
        await proc._emit_for({"event": "meeting.end", "data": {"reason": "call_ended"}})

        runtime.carrier.close()
        await asyncio.sleep(0)
        for _ in range(20):
            if not received or len(received) >= 5:
                break
            await asyncio.sleep(0)
        pump.cancel()

    _run(run())

    kinds = [signal_name(s) for s in received]
    # The SAME binding carried every one of the wired signals onto the live carrier.
    assert "roster" in kinds, f"roster never reached the live carrier: {kinds}"
    assert "bot-status" in kinds, f"bot-status never reached the live carrier: {kinds}"
    assert "meeting-end" in kinds, f"meeting-end never reached the live carrier: {kinds}"

    rosters = [s for s in received if isinstance(s, RosterEvent)]
    assert {r.kind for r in rosters} == {"present", "join", "leave"}, (
        f"roster present/join/leave not all bound: {[(r.kind, r.name) for r in rosters]}"
    )
    assert any(isinstance(s, BotStatus) and s.status == "dropped" for s in received)
    assert any(isinstance(s, MeetingEnd) for s in received)


def test_processor_is_one_per_meeting_dedupe_state_persists():
    """The binding builds ONE processor per runtime so its once-only state (present-snapshot,
    meeting-end-once, name cache) persists across the meeting's webhooks — never a fresh one
    per event (which would re-emit the present snapshot and re-run the close each webhook).
    """
    runtime = _runtime()
    assert runtime.webhook_processor() is runtime.webhook_processor()


def test_drain_classification_routes_roster_and_bot_status_not_drops_them():
    """The live drain classifies roster + non-terminal bot-status as a signal to bind, and a
    terminal bot-status as a meeting-end — the exact routing the C-SIGNALWIRE binding needs.
    """
    from harness import webhooks as wh

    assert "participant.join" in wh._ROSTER_EVENTS
    assert "participant.leave" in wh._ROSTER_EVENTS
    assert "bot.status" in wh._BOT_STATUS_EVENTS
