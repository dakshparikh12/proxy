"""Doc 02 · M1 — the consent hard-gate is WIRED into the LIVE hearing path.

criterion_id: AC-JOIN-04 (live-path enforcement; F-RECORD-BEFORE-CONSENT)

The isolation-only wiring gap an adversarial audit found: ``test_m1_join.py``'s
``test_consent_gate_actually_drops_pre_consent_transcript`` proves the gate works when a
``HearingStage`` is *hand-built* with ``can_observe=``. But the LIVE ``HearingStage`` the
harness constructs (``MeetingRuntime.start`` → ``HearingStage(...)``) had NO ``can_observe=``
— so on the real runtime the gate defaulted to always-allow and a pre-consent transcript
was silently observed/recorded (records_before_consent_allowed > 0), violating Law 3.

These tests construct the runtime the way the provisioner does (``registry.start_meeting``
and ``runtime.ingest_transcript``) and prove a pre-consent transcript is DROPPED on the LIVE
path — not merely in an isolated stage. They fail RED until the gate is wired into
``MeetingRuntime``'s live ``HearingStage`` and default-closed (fail-closed).

All product imports live inside test bodies so collection stays clean.
"""
from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.simulation


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _NullScribe:
    """A no-op stand-in for the Scribe runtime handle so ``MeetingRuntime.start`` wires the
    live ``HearingStage`` without a real notes engine. We only exercise the HEAR gate here."""

    async def wait(self) -> None:
        return None

    async def aclose(self) -> None:
        return None


def _live_runtime(monkeypatch):
    """Build a ``MeetingRuntime`` EXACTLY as the harness/provisioner does — via
    ``MeetingRuntimeRegistry.start_meeting`` (which calls ``runtime.start()`` and thereby
    constructs the LIVE ``HearingStage``). We stub only the Scribe consumer so no DB/notes
    engine is needed; the transport ``HearingStage`` + its consent gate are the REAL ones.
    """
    from harness import meeting_runtime as mr
    from scribe.prefix import MeetingHeader
    from transport.carrier import SignalCarrier

    # Stub the Scribe serial consumer so start_meeting wires the runtime without a DB.
    monkeypatch.setattr(mr, "start_meeting_scribe", lambda *a, **k: _NullScribe())

    registry = mr.MeetingRuntimeRegistry(db=None)
    header = MeetingHeader(meeting_id="m-live-1", agenda="a", participants=())
    carrier = SignalCarrier()
    runtime = registry.start_meeting(header, carrier)
    return runtime, carrier


def test_live_hearing_stage_is_gated_not_always_allow(monkeypatch):
    """The LIVE ``HearingStage`` must carry a real consent predicate — never ``can_observe=None``.

    criterion_id: AC-JOIN-04
    ``can_observe=None`` on the live stage means the gate defaults to always-allow (the exact
    bug). Assert the runtime's live stage has a bound, callable consent gate that starts CLOSED.
    """
    runtime, _carrier = _live_runtime(monkeypatch)
    # The live stage was constructed by start_meeting → start(); it must exist and be gated.
    hearing = runtime._hearing
    assert hearing is not None, "runtime.start() did not build the live HearingStage"
    assert hearing._can_observe is not None, (
        "live HearingStage has can_observe=None → defaults to always-allow (F-RECORD-BEFORE-CONSENT)"
    )
    # Fail-closed: before consent is granted, the live gate denies observation.
    assert hearing._can_observe() is False, "live consent gate must start CLOSED (fail-closed)"


def test_live_runtime_drops_pre_consent_transcript(monkeypatch):
    """A transcript fed to the LIVE runtime BEFORE consent is granted must be DROPPED.

    criterion_id: AC-JOIN-04
    records_before_consent_allowed == 0 on the real ``MeetingRuntime.ingest_transcript`` path.
    """
    runtime, carrier = _live_runtime(monkeypatch)

    # A subscriber on the SAME carrier the Scribe/Orchestrator would read: nothing must reach
    # it before consent posts.
    from transport.signals import Transcript

    received: list[Transcript] = []

    async def _collect():
        agen = carrier.subscribe()
        # Feed a pre-consent transcript through the LIVE production emit end (the CONFIRMED
        # Recall real-time wire shape: words/speaker/timestamp — the same body the webhook drain
        # hands ``runtime.ingest_transcript``).
        await runtime.ingest_transcript(
            {"words": "what's the p95?", "speaker": "Alice", "timestamp": 0.0, "is_final": True}
        )
        # Give the carrier a tick; nothing should have been emitted (record dropped).
        carrier.close()
        async for sig in agen:
            if isinstance(sig, Transcript):
                received.append(sig)

    _run(_collect())
    assert received == [], "a pre-consent transcript reached the carrier on the LIVE path"
    assert runtime._hearing.emitted == [], (
        "a pre-consent transcript was observed/recorded on the LIVE runtime "
        "(records_before_consent_allowed > 0)"
    )


def test_live_runtime_observes_after_consent_granted(monkeypatch):
    """After consent is granted on the runtime, the identical transcript flows onto the carrier.

    criterion_id: AC-JOIN-04
    Proves the gate is a real precondition (opens on consent), not a permanent block.
    """
    runtime, carrier = _live_runtime(monkeypatch)

    from transport.signals import Transcript

    received: list[Transcript] = []

    async def _collect():
        agen = carrier.subscribe()
        # Grant consent the way the provisioner does once the join posted the notice.
        runtime.grant_consent()
        await runtime.ingest_transcript(
            {"words": "what's the p95?", "speaker": "Alice", "timestamp": 1.0, "is_final": True}
        )
        carrier.close()
        async for sig in agen:
            if isinstance(sig, Transcript):
                received.append(sig)

    _run(_collect())
    assert len(received) == 1, "a post-consent transcript must reach the carrier on the LIVE path"
    assert received[0].words == "what's the p95?"
    assert len(runtime._hearing.emitted) == 1


def test_provisioner_grants_consent_on_in_call(monkeypatch):
    """The provisioner's live assembly must GRANT consent so the live gate opens (in_call means
    the bot joined + posted the consent notice first, by construction of JoinSession.join).

    criterion_id: AC-JOIN-04
    Without this the whole live path would deadlock closed; without the gate it would leak
    pre-consent records. This proves the provisioner opens the gate on the confirmed-join event.
    """
    from harness import meeting_runtime as mr
    from harness import provisioner as prov
    from scribe.prefix import MeetingHeader
    from transport.carrier import SignalCarrier

    monkeypatch.setattr(mr, "start_meeting_scribe", lambda *a, **k: _NullScribe())

    # A fake live_brain assembly so _assemble_runtime does not need the real model seam.
    monkeypatch.setattr(prov, "_assemble_runtime", prov._assemble_runtime)

    registry = mr.MeetingRuntimeRegistry(db=None)

    # Drive just the assembly step the way provision_meeting does on a WON claim.
    payload = {"event": "bot.in_call", "data": {"bot_id": "bot-xyz"}}
    resolved = {"id": "m-live-2", "tenant_id": "t", "repo_id": None}

    # Stub the live-brain assembly to a no-op so we isolate the consent-grant wiring.
    import harness.live_brain as lb
    monkeypatch.setattr(lb, "assemble_live_brain", lambda runtime, provider=None: None)

    runtime = prov._assemble_runtime(
        payload, resolved, db=None, registry=registry, handle=None, provider=None
    )
    # The live gate must be OPEN after the provisioner assembled the runtime on in_call.
    assert runtime._hearing is not None
    assert runtime._hearing._can_observe() is True, (
        "provisioner did not grant consent on the confirmed in_call join → live gate stays closed"
    )
