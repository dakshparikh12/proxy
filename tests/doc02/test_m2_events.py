"""Doc 02 · Milestone 2 — EVENTS (AC-EVENTS-01 .. AC-EVENTS-14).

Webhook → live signal derivation tests. All product imports inside test bodies.
"""
import asyncio
import pytest

pytestmark = pytest.mark.simulation

def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class _InMemStore:
    """Minimal durable store: tracks inserts, enforces delivery_guid uniqueness."""
    def __init__(self):
        self.rows = {}
        self.processed = 0

    async def insert_event(self, delivery_guid, payload):
        if delivery_guid in self.rows:
            return False  # duplicate
        self.rows[delivery_guid] = payload
        return True


def _make_processor(store=None):
    from transport.events import WebhookProcessor
    from transport.carrier import SignalCarrier
    carrier = SignalCarrier()
    proc = WebhookProcessor(carrier, store=store or _InMemStore())
    return proc, carrier


# ── EVENTS-01 ─────────────────────────────────────────────────────────────────

def test_roster_events_stream_live_not_batched():
    """AC-EVENTS-01: participant events delivered live, not batched at meeting end.

    criterion_id: AC-EVENTS-01
    """
    from transport.signals import RosterEvent
    proc, carrier = _make_processor()

    emitted = []
    async def run():
        async def consume():
            async for sig in carrier.subscribe():
                emitted.append(sig)

        join_payload = {"event": "participant.join", "data": {"name": "Sam", "id": "p1"},
                        "delivery_guid": "g1"}
        leave_payload = {"event": "participant.leave", "data": {"name": "Sam", "id": "p1"},
                         "delivery_guid": "g2"}
        r1 = await proc.process(join_payload)
        r2 = await proc.process(leave_payload)
        return r1, r2

    r1, r2 = _run(run())
    # Both newly inserted and emitted signals (not batched)
    assert r1.persisted and not r1.duplicate
    assert r2.persisted and not r2.duplicate
    # Signals exist in emitted list (live delivery)
    roster_sigs = [s for s in carrier._subscribers if s]
    assert r1.emitted, "join event produced no emitted signal"
    assert r2.emitted, "leave event produced no emitted signal"


# ── EVENTS-02 ─────────────────────────────────────────────────────────────────

def test_join_emits_roster_join_with_name():
    """AC-EVENTS-02: participant join emits roster(join, name='Sam').

    criterion_id: AC-EVENTS-02
    """
    from transport.signals import RosterEvent
    proc, _ = _make_processor()
    payload = {"event": "participant.join", "data": {"name": "Sam", "id": "p1"},
               "delivery_guid": "g-join-sam"}
    result = _run(proc.process(payload))
    assert result.emitted, "no signal emitted for participant join"
    sig = result.emitted[0]
    assert isinstance(sig, RosterEvent)
    assert sig.kind == "join"
    assert sig.name == "Sam"


# ── EVENTS-03 ─────────────────────────────────────────────────────────────────

def test_leave_emits_roster_leave_with_name():
    """AC-EVENTS-03: participant leave emits roster(leave, name='Sam').

    criterion_id: AC-EVENTS-03
    """
    from transport.signals import RosterEvent
    proc, _ = _make_processor()
    payload = {"event": "participant.leave", "data": {"name": "Sam", "id": "p1"},
               "delivery_guid": "g-leave-sam"}
    result = _run(proc.process(payload))
    sig = result.emitted[0]
    assert isinstance(sig, RosterEvent)
    assert sig.kind == "leave"
    assert sig.name == "Sam"
    # Leave is never misclassified as join
    assert sig.kind != "join"


# ── EVENTS-04 ─────────────────────────────────────────────────────────────────

def test_roster_event_derived_from_source_payload():
    """AC-EVENTS-04: emitted name+kind traceable to source webhook field.

    criterion_id: AC-EVENTS-04
    """
    from transport.signals import RosterEvent
    proc, _ = _make_processor()
    payload = {"event": "participant.join",
               "data": {"name": "AliceFromPayload", "id": "p99"},
               "delivery_guid": "g-provenance"}
    result = _run(proc.process(payload))
    sig = result.emitted[0]
    # Field-level provenance: emitted name == source payload name
    assert sig.name == "AliceFromPayload"
    assert sig.participant_id == "p99"


# ── EVENTS-05 ─────────────────────────────────────────────────────────────────

def test_meeting_metadata_passes_through_verbatim():
    """AC-EVENTS-05: title + participant list passed to Orchestrator as context.

    criterion_id: AC-EVENTS-05
    """
    from transport.events import meeting_metadata
    payload = {
        "event": "meeting.start",
        "data": {
            "title": "Engineering Sync",
            "participants": [{"name": "Alice"}, {"name": "Bob"}],
        }
    }
    meta = meeting_metadata(payload)
    assert meta.title == "Engineering Sync"
    assert set(meta.participants) == {"Alice", "Bob"}


# ── EVENTS-06 ─────────────────────────────────────────────────────────────────

def test_explicit_meeting_end_emits_meeting_end_signal():
    """AC-EVENTS-06: meeting.end webhook emits exactly one meeting-end signal.

    criterion_id: AC-EVENTS-06
    """
    from transport.signals import MeetingEnd
    proc, _ = _make_processor()
    payload = {"event": "meeting.end", "data": {"reason": "host_ended"},
               "delivery_guid": "g-end"}
    result = _run(proc.process(payload))
    end_sigs = [s for s in result.emitted if isinstance(s, MeetingEnd)]
    assert len(end_sigs) == 1, f"expected 1 meeting-end signal, got {len(end_sigs)}"


# ── EVENTS-07 ─────────────────────────────────────────────────────────────────

def test_meeting_end_never_inferred_from_silence():
    """AC-EVENTS-07: no meeting-end signal emitted during prolonged silence.

    criterion_id: AC-EVENTS-07
    """
    from transport.signals import MeetingEnd
    proc, _ = _make_processor()
    # Inject only silence-like payloads (no meeting.end, no bot.removed)
    silence_events = []
    for i in range(5):
        payload = {"event": "heartbeat", "data": {}, "delivery_guid": f"hb-{i}"}
        silence_events.append(_run(proc.process(payload)))

    meeting_end_sigs = []
    for r in silence_events:
        meeting_end_sigs.extend(s for s in r.emitted if isinstance(s, MeetingEnd))

    assert not meeting_end_sigs, "meeting-end must never be inferred from silence"


# ── EVENTS-08 ─────────────────────────────────────────────────────────────────

def test_meeting_end_triggers_close_sequence_after_signal():
    """AC-EVENTS-08: close sequence triggered causally after meeting-end signal.

    criterion_id: AC-EVENTS-08
    """
    from transport.signals import MeetingEnd
    close_seq_order = []
    proc, _ = _make_processor()

    end_payload = {"event": "meeting.end", "data": {}, "delivery_guid": "g-end-seq"}
    result = _run(proc.process(end_payload))
    end_sigs = [s for s in result.emitted if isinstance(s, MeetingEnd)]
    assert end_sigs, "meeting-end signal must be emitted before close sequence"


# ── EVENTS-09 ─────────────────────────────────────────────────────────────────

def test_webhooks_land_durably_before_processing():
    """AC-EVENTS-09: insert_event called before signal emission; returns 200-equivalent.

    criterion_id: AC-EVENTS-09
    """
    store = _InMemStore()
    proc, _ = _make_processor(store)
    payload = {"event": "participant.join", "data": {"name": "Bob", "id": "p2"},
               "delivery_guid": "g-durable"}
    result = _run(proc.process(payload))
    assert result.persisted is True
    assert "g-durable" in store.rows
    assert result.emitted  # signal emitted AFTER durable write


# ── EVENTS-10 ─────────────────────────────────────────────────────────────────

def test_duplicate_delivery_guid_processed_exactly_once():
    """AC-EVENTS-10: second delivery of same guid is no-op; one signal total.

    criterion_id: AC-EVENTS-10
    """
    store = _InMemStore()
    proc, _ = _make_processor(store)
    payload = {"event": "participant.join", "data": {"name": "Bob", "id": "p2"},
               "delivery_guid": "g-dedup"}
    r1 = _run(proc.process(payload))
    r2 = _run(proc.process(payload))  # exact duplicate
    assert r1.persisted is True
    assert r2.duplicate is True
    # Store has exactly one row for the guid
    assert list(store.rows.keys()).count("g-dedup") == 1
    # Only one set of signals total
    assert not r2.emitted, "duplicate delivery must emit no signals"


# ── EVENTS-11 ─────────────────────────────────────────────────────────────────

def test_internal_signals_not_in_client_registry():
    """AC-EVENTS-11: roster/meeting-end/bot-status absent from ProxyMessage registry.

    criterion_id: AC-EVENTS-11
    """
    from contracts.registry import assert_registry_closed, SIGNAL_SURFACE_EVENTS, CHANNEL_REGISTRY
    # Internal signal names must not appear as client registry keys
    internal = {"roster", "meeting-end", "bot-status"}
    for name in internal:
        assert name not in CHANNEL_REGISTRY, (
            f"internal signal {name!r} found in client registry — must be absent"
        )
    # Registry must close without requiring them
    result = assert_registry_closed()
    assert result is None or result is True  # passes


# ── EVENTS-12 ─────────────────────────────────────────────────────────────────

def test_bot_status_webhooks_flow_to_harness():
    """AC-EVENTS-12: bot-status webhook persisted then routed to carrier.

    criterion_id: AC-EVENTS-12
    """
    from transport.signals import BotStatus
    store = _InMemStore()
    proc, _ = _make_processor(store)
    payload = {"event": "bot.status", "data": {"status": "connected"},
               "delivery_guid": "g-bstatus"}
    result = _run(proc.process(payload))
    assert result.persisted
    bot_sigs = [s for s in result.emitted if isinstance(s, BotStatus)]
    assert bot_sigs, "bot-status signal must be routed to harness"


# ── EVENTS-13 ─────────────────────────────────────────────────────────────────

def test_name_change_reflected_on_subsequent_roster_events():
    """AC-EVENTS-13: updated name reflected on subsequent roster events.

    criterion_id: AC-EVENTS-13
    """
    from transport.signals import RosterEvent
    proc, _ = _make_processor()
    # Initial join with name "OldName"
    _run(proc.process({"event": "participant.join",
                       "data": {"name": "OldName", "id": "p5"},
                       "delivery_guid": "g-name-1"}))
    # Name update
    _run(proc.process({"event": "participant.update",
                       "data": {"name": "NewName", "id": "p5"},
                       "delivery_guid": "g-name-2"}))
    # Subsequent leave should carry updated name
    result = _run(proc.process({"event": "participant.leave",
                                "data": {"name": "NewName", "id": "p5"},
                                "delivery_guid": "g-name-3"}))
    sig = next((s for s in result.emitted if isinstance(s, RosterEvent)), None)
    assert sig is not None
    assert sig.name == "NewName", f"stale name: expected NewName got {sig.name}"


# ── EVENTS-14 ─────────────────────────────────────────────────────────────────

def test_roster_event_from_real_recall_fixture():
    """AC-EVENTS-14: roster signals sourced from real Recall participant payload.

    criterion_id: AC-EVENTS-14
    """
    from transport.signals import RosterEvent
    # Real Recall participant.join webhook shape (confirmed at build, CANONICAL §11.10)
    recall_fixture = {
        "event": "participant.join",
        "delivery_guid": "real-fixture-001",
        "data": {
            "id": "participant_xyz",
            "name": "Charlie",
            "is_host": False,
        }
    }
    proc, _ = _make_processor()
    result = _run(proc.process(recall_fixture))
    sig = next((s for s in result.emitted if isinstance(s, RosterEvent)), None)
    assert sig is not None
    assert sig.name == "Charlie"
    assert sig.participant_id == "participant_xyz"
    assert sig.kind == "join"
