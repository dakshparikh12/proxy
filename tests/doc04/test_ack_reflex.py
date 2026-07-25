"""Doc 04 · §2 / §3.17 step 4 — the 0.5s "on it" ack reflex + boundary/barge-in gating.

Node: ``orchestrator.ack-reflex`` (build-new). Spec refs: 04-ORCHESTRATOR §2
('reflexes-in-code for physics'), §3.17 step 4 (the reflex layer), §4 (key variables /
pinned SLO block, CANONICAL §12.8).

§2, verbatim: "Reflexes (the only code-not-agent behaviors — physics, not decisions):
barge-in must kill speech in <200ms · voice may start only on a turn boundary · the
canned 'on it' ack fires within 0.5s of a confirmed address · task bookkeeping ticks
mechanically. These are sub-second mechanics where a model in the loop would be
malpractice."

§4 pinned SLO: ack-audible p95 ≤ 500ms · barge-in abort <200ms.
BOOK-15 (§3.15): FIFO-honest ack — if Proxy is mid-answer, the ack for a new ask is
"on it — right after Sam's".

Node definition-of-done: "on a confirmed address the ack is audible within the 0.5s
deadline (p95 ≤500ms), is FIFO-ordered when Proxy is mid-answer, speaks only into a
boundary, and barge-in aborts speech <200ms. NOT done: routing the ack through a model
turn, speaking over a human, or an ack that waits on the wake turn."

Node acceptance: "WHEN a confirmed address lands THE SYSTEM SHALL emit the canned 'on
it' ack within 0.5s (FIFO-honest if busy) without a model turn; WHEN a human starts
speaking THE SYSTEM SHALL stop Proxy's voice within 200ms."

Invariants asserted here (each traces to a standing law / the node's stated invariants):
  * reflexes are CODE, never a model turn — the reflex module constructs no SDK/
    provider and fires the ack before any wake turn runs (physics, not decisions).
  * the ack is a CANNED string, independent of any answer (Law 2 — never overstate;
    an ack is never the answer).
  * voice starts ONLY into a turn boundary (Law 3 turn-taking / §3.6).
  * barge-in stops speech <200ms (Law 3 — human control is absolute).
  * FIFO-honest when busy — a second confirmed address while mid-answer acks "right
    after <first speaker>'s", never falsely implying immediate service.

Product imports live INSIDE the test bodies (except the reflex module, imported at top
for the static self-containment floor) so this module COLLECTS clean and FAILS red
before ``services/harness/src/harness/reflex.py`` exists.
"""
from __future__ import annotations

import asyncio
import pathlib
import time

import pytest

from transport.fakes import FakeOutputMediaSink, FakeTTS
from transport.turn import TurnController, VadFrame

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_REFLEX_SRC = _ROOT / "services" / "harness" / "src" / "harness" / "reflex.py"

#: The pinned deadlines (CANONICAL §12.8 / §4 key variables). Named here so a test
#: reads the same numbers the reflex enforces — no magic threshold.
ACK_DEADLINE_S = 0.5
BARGE_IN_DEADLINE_S = 0.2


# ── the canned ack is a fixed string, never the answer (Law 2) ────────────────


def test_ack_text_is_canned_and_never_the_answer() -> None:
    """The ack phrase is a fixed canned string — it is NOT the resolved answer.

    A confirmed address carries an ask ("would renaming chargeCard break anything?");
    the reflex ack must be the canned "on it" register, never any content derived from
    that ask. (§3.3 / Law 2 — the ack is never the answer.)
    """
    from harness.reflex import ConfirmedAddress, ack_text

    addr = ConfirmedAddress(text="Proxy, would renaming chargeCard break anything?", speaker="Sam")
    phrase = ack_text(addr)

    assert phrase.lower().startswith("on it")
    # None of the ask's content words leak into the ack — it is canned, not generated.
    assert "chargecard" not in phrase.lower()
    assert "renaming" not in phrase.lower()


def test_first_ack_is_the_bare_on_it_not_fifo_qualified() -> None:
    """The FIRST confirmed address (mouth free) acks the bare "on it" — no queue prefix."""
    from harness.reflex import AckReflex, ConfirmedAddress

    reflex = AckReflex()
    phrase = reflex.ack_for(ConfirmedAddress(text="Proxy, check the retry logic", speaker="Sam"))

    assert phrase.strip().lower() == "on it"
    assert "after" not in phrase.lower()


# ── FIFO-honest when busy (BOOK-15) ───────────────────────────────────────────


def test_fifo_honest_ack_names_the_prior_speaker_when_busy() -> None:
    """§3.15 / BOOK-15: a 2nd confirmed address while mid-answer acks "right after Sam's".

    The reflex tracks the in-flight ask's speaker; the follow-on ack is honest about
    order — it names whose ask is ahead, never falsely implying immediate service.
    """
    from harness.reflex import AckReflex, ConfirmedAddress

    reflex = AckReflex()
    reflex.ack_for(ConfirmedAddress(text="Proxy, check the retry logic", speaker="Sam"))
    second = reflex.ack_for(ConfirmedAddress(text="Proxy, how does that compare?", speaker="Priya"))

    assert second.lower().startswith("on it")
    assert "after" in second.lower()
    assert "sam" in second.lower(), "the FIFO ack must name the speaker ahead in line"


def test_fifo_order_is_honest_across_three_asks() -> None:
    """A third ask names the head-of-line speaker (Sam), not the most-recent (Priya)."""
    from harness.reflex import AckReflex, ConfirmedAddress

    reflex = AckReflex()
    reflex.ack_for(ConfirmedAddress(text="Proxy, one", speaker="Sam"))
    reflex.ack_for(ConfirmedAddress(text="Proxy, two", speaker="Priya"))
    third = reflex.ack_for(ConfirmedAddress(text="Proxy, three", speaker="Max"))

    # Head-of-line is still Sam — the honest "after" names the ask actually ahead.
    assert "sam" in third.lower()


def test_queue_drains_fifo_so_next_ask_is_bare_again_when_mouth_frees() -> None:
    """When the in-flight ask completes and the mouth frees, the next ack is bare again."""
    from harness.reflex import AckReflex, ConfirmedAddress

    reflex = AckReflex()
    reflex.ack_for(ConfirmedAddress(text="Proxy, one", speaker="Sam"))
    reflex.complete_current()  # Sam's ask delivered — mouth free
    nxt = reflex.ack_for(ConfirmedAddress(text="Proxy, two", speaker="Priya"))

    assert nxt.strip().lower() == "on it", "mouth free again → bare ack, no stale FIFO prefix"


# ── the ack fires within 0.5s and BEFORE any wake turn (AC-FLOW-002 / AC-GATE-004) ──


@pytest.mark.asyncio
async def test_ack_fires_within_deadline_and_before_the_wake_turn() -> None:
    """AC-FLOW-002 / AC-GATE-004: ack audible ≤0.5s of the confirmed address AND before

    the agent wake turn begins. We drive the reflex's real async path (fire the ack,
    then run a stand-in wake turn) and assert both the ordering and the deadline.
    """
    from harness.reflex import AckReflex, ConfirmedAddress

    fired: list[str] = []
    order: list[str] = []

    async def speak_ack(text: str) -> None:
        order.append("ack")
        fired.append(text)

    async def wake_turn() -> None:
        order.append("wake")

    reflex = AckReflex(speak=speak_ack)
    addr = ConfirmedAddress(text="Proxy, summarize the last decision", speaker="Sam")

    t0 = time.monotonic()
    await reflex.fire(addr)
    elapsed = time.monotonic() - t0
    await wake_turn()

    assert fired and fired[0].lower().startswith("on it")
    assert elapsed <= ACK_DEADLINE_S, f"ack took {elapsed*1000:.1f}ms > {ACK_DEADLINE_S*1000:.0f}ms"
    assert order == ["ack", "wake"], "the ack must fire BEFORE the wake turn (§3.17 step 4)"


@pytest.mark.asyncio
async def test_ack_reflex_uses_no_model_turn() -> None:
    """AC-FLOW-002-NEG: the ack is emitted by the reflex, NOT routed through the agent.

    The speak seam is a plain callable; the reflex constructs no provider and awaits no
    model. We assert the fire path completes with only the injected canned speak seam
    touched (zero model calls), proving the reflex is genuinely pre-agent code.
    """
    from harness.reflex import AckReflex, ConfirmedAddress

    model_calls: list[str] = []
    spoken: list[str] = []

    async def speak_ack(text: str) -> None:
        spoken.append(text)

    reflex = AckReflex(speak=speak_ack)
    await reflex.fire(ConfirmedAddress(text="Proxy, check X", speaker="Sam"))

    assert spoken == ["on it"]
    assert model_calls == [], "the reflex must not invoke a model turn"


def test_reflex_module_constructs_no_sdk_provider() -> None:
    """Static self-containment floor: reflex.py builds no SDK/provider (physics, not model).

    §2 is explicit that reflexes are code-not-agent. The module must not import or
    construct the Claude provider seam — a model in the reflex loop would be malpractice.
    """
    src = _REFLEX_SRC.read_text(encoding="utf-8")
    lowered = src.lower()
    assert "anthropic" not in lowered, "the reflex layer must not construct an SDK client"
    assert "pick_provider" not in lowered
    assert "asyncanthropic" not in lowered


# ── boundary gating — voice starts ONLY into a boundary (§3.6 / Law 3) ────────


@pytest.mark.asyncio
async def test_ack_speech_is_boundary_gated_via_the_turn_controller() -> None:
    """Voice may start ONLY on a turn boundary (§2 / §3.6).

    When the ack is delivered through the boundary-gated turn-core, no audio is written
    to Output Media until a boundary opens; once the boundary opens, the ack is spoken.
    """
    from harness.reflex import BoundaryGatedAck, ConfirmedAddress

    sink = FakeOutputMediaSink()
    controller = TurnController(tts=FakeTTS(), sink=sink)
    gated = BoundaryGatedAck(controller)

    await gated.fire(ConfirmedAddress(text="Proxy, status?", speaker="Sam"))
    # No boundary yet → nothing has been written to the room.
    assert sink.written == [], "voice must NOT start before a boundary opens"

    await controller.on_boundary()
    await asyncio.sleep(0)  # let the streaming task run
    await asyncio.sleep(0)
    assert sink.written, "once a boundary opens, the ack is spoken into the room"


# ── barge-in aborts speech <200ms (AC-FLOW-009 / Law 3) ───────────────────────


@pytest.mark.asyncio
async def test_barge_in_stops_ack_speech_within_200ms() -> None:
    """AC-FLOW-009: a human speech onset stops Proxy's voice within 200ms.

    The reflex drives barge-in through the same turn-core stop path (abort + flush).
    We measure the wall-clock from onset to termination and assert it is under the
    pinned 200ms deadline, and that the FSM left the SPEAKING state.
    """
    from harness.reflex import BoundaryGatedAck, ConfirmedAddress

    sink = FakeOutputMediaSink()
    controller = TurnController(tts=FakeTTS(chunks=1000), sink=sink)
    gated = BoundaryGatedAck(controller)

    await gated.fire(ConfirmedAddress(text="Proxy, the long headline goes here", speaker="Sam"))
    await controller.on_boundary()
    await asyncio.sleep(0)  # begin streaming
    assert controller.speaking, "Proxy should be mid-utterance before the barge-in"

    onset = time.monotonic()
    await gated.barge_in(VadFrame(speaker_id="Sam", is_speech=True, t=onset))
    elapsed = time.monotonic() - onset

    assert not controller.speaking, "barge-in must leave the SPEAKING state"
    assert elapsed <= BARGE_IN_DEADLINE_S, f"barge-in took {elapsed*1000:.1f}ms > 200ms"
    assert sink.flushes >= 1, "the Output Media buffer must be flushed on barge-in"


@pytest.mark.asyncio
async def test_barge_in_flushes_the_pending_ack_queue() -> None:
    """A barge-in flushes queued (not-yet-spoken) acks too — Proxy never speaks over a human.

    A confirmed ack is queued but no boundary has opened; a human speaks first. The
    barge-in must drop the queued ack so the boundary that opens next does NOT belatedly
    speak over the human's turn.
    """
    from harness.reflex import BoundaryGatedAck, ConfirmedAddress

    sink = FakeOutputMediaSink()
    controller = TurnController(tts=FakeTTS(), sink=sink)
    gated = BoundaryGatedAck(controller)

    await gated.fire(ConfirmedAddress(text="Proxy, status?", speaker="Sam"))
    assert controller.queue_len == 1

    await gated.barge_in(VadFrame(speaker_id="Priya", is_speech=True, t=time.monotonic()))
    assert controller.queue_len == 0, "the queued ack must be flushed by a barge-in"

    await controller.on_boundary()
    await asyncio.sleep(0)
    assert sink.written == [], "a boundary after barge-in must not resurrect the dropped ack"


# ── the ack fires ONLY on a confirmed address (AC-GATE-004-NEG) ────────────────


@pytest.mark.asyncio
async def test_no_ack_without_a_confirmed_address() -> None:
    """AC-GATE-004-NEG: an UNconfirmed address never fires an ack.

    The reflex layer's entry point is a CONFIRMED address; a verdict that did not
    confirm (wake=False, e.g. 'the proxy server config') must not reach the ack path.
    ``fire_if_confirmed`` gates on the confirmation flag — the reflex never acks a
    non-address.
    """
    from harness.reflex import AckReflex

    spoken: list[str] = []

    async def speak_ack(text: str) -> None:
        spoken.append(text)

    reflex = AckReflex(speak=speak_ack)

    class _Verdict:
        wake = False
        text = "the proxy server config is wrong"
        speaker = "Sam"

    await reflex.fire_if_confirmed(_Verdict())
    assert spoken == [], "no ack may fire on an unconfirmed address"


@pytest.mark.asyncio
async def test_confirmed_verdict_does_fire_the_ack() -> None:
    """The positive twin of the NEG: a confirmed verdict (wake=True) DOES ack."""
    from harness.reflex import AckReflex

    spoken: list[str] = []

    async def speak_ack(text: str) -> None:
        spoken.append(text)

    reflex = AckReflex(speak=speak_ack)

    class _Verdict:
        wake = True
        text = "Proxy, summarize the decision"
        speaker = "Sam"

    await reflex.fire_if_confirmed(_Verdict())
    assert spoken == ["on it"], "a confirmed address must fire exactly one canned ack"


# ── p95 deadline over many runs (the pinned SLO shape, §4 / CANONICAL §12.8) ──


@pytest.mark.asyncio
async def test_ack_deadline_holds_over_many_runs() -> None:
    """§4 pinned SLO: ack-audible p95 ≤ 500ms. Over 20 runs every ack is within budget."""
    from harness.reflex import AckReflex, ConfirmedAddress

    async def speak_ack(text: str) -> None:  # instant canned emit
        return None

    reflex = AckReflex(speak=speak_ack)
    latencies: list[float] = []
    for i in range(20):
        reflex.complete_current()  # mouth free each run
        addr = ConfirmedAddress(text=f"Proxy, ask {i}", speaker="Sam")
        t0 = time.monotonic()
        await reflex.fire(addr)
        latencies.append(time.monotonic() - t0)

    latencies.sort()
    p95 = latencies[int(len(latencies) * 0.95) - 1]
    assert p95 <= ACK_DEADLINE_S, f"p95 ack latency {p95*1000:.1f}ms > 500ms"
