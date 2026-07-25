"""Doc 02 · Milestone 3 — HEAR real attribution oracle (AC-HEAR-06 / -04 / -08 / -09 / -11 / -12).

WHY THIS FILE EXISTS (oracle strengthening, not behavior change)
----------------------------------------------------------------
A fresh audit found two WEAK oracles in the sealed ``test_m3_hear.py``:

* ``test_two_speaker_attribution_correctness`` (AC-HEAR-06, sealed lines 197-225) is a
  **pass-through tautology**: it drives the NON-production ``ingest_passthrough`` entry
  with a ``{words, speaker, start, is_final}`` message, then asserts the same ``speaker``
  string back off a single-consumer fake carrier. Feeding a label in and reading it back
  proves nothing about attribution on the real fan-out path.
* ``test_code_heavy_accuracy_placeholder`` (AC-HEAR-10, sealed lines 308-330) is an
  explicit **placeholder** — it emits one record and asserts a substring; there is no
  WER / golden-corpus / alternative-engine oracle behind it.

This file authors the REAL behavioral oracle for the attribution + fan-out contract, and
it exercises the PRODUCTION integration point — ``HearingStage.ingest_wire_transcript``
(gap DOC02-DOC03-TRANSCRIPT-BRIDGE), which parses the CONFIRMED Recall real-time
transcript passthrough shape via the fail-loud ``transport.wire.parse_transcript`` and
fans onto the SAME :class:`~transport.carrier.SignalCarrier` the Doc 03 notes engine and
the Doc 04 orchestrator subscribe to. The weak oracle never touched this path (it used the
convenience ``ingest_passthrough`` with a different, non-vendor message shape).

WHAT IS GENUINELY PROVEN HERE (all on the real production code path):
  * AC-HEAR-06 — a realistic INTERLEAVED two-speaker wire stream yields records each
    attributed to the correct speaker, proven against a ground-truth interleaving the
    parser cannot have echoed (order + speaker + content must all line up), including a
    strict-alternation ordering check so a swap/merge/drop is caught.
  * AC-HEAR-04 — TWO independent subscribers on ONE real carrier (Doc 03 shape + Doc 04
    shape) receive the IDENTICAL ordered sequence (records, speakers, and timestamps).
  * AC-HEAR-08/09 — Proxy-labelled lines are recorded on the carrier but ZERO are routed
    as asks, while EVERY human line is routed (speaker-scoped self-loop guard, not
    content-scoped) — asserted on the interleaved stream, not a single crafted line.
  * AC-HEAR-11 — an un-transcribed stretch surfaces as an explicit ``TranscriptGap``
    mark-lost record on the carrier (never silently absent), and no buffer-through-outage
    handler is present.
  * AC-HEAR-12 — a malformed / drifted passthrough body raises the real
    :class:`~transport.wire.WireDriftError` LOUDLY through the production entry, never
    fanning a wrong record downstream.

AC-HEAR-10 (code-heavy transcription ACCURACY vs one alternative engine) is DELIBERATELY
NOT faked here. A real WER/accuracy oracle needs a labelled audio golden corpus of
code-heavy engineering speech (identifiers, filenames, acronyms) + real STT + a pinned
alternative engine scored side-by-side (spec §3.2, §3.9 step 3). That is a Phase-3 /
founder golden-corpus + real-infra item; a sim substring check would only re-introduce the
placeholder this file is replacing. It is flagged, not simulated.

All product imports live inside the test bodies (suite convention).
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Coroutine
from typing import Any

import pytest

pytestmark = pytest.mark.simulation


def _run(coro: Coroutine[Any, Any, None]) -> None:
    asyncio.get_event_loop().run_until_complete(coro)


def _wire(words: str, speaker: str, timestamp: float, *, end_of_turn: bool = True) -> dict[str, Any]:
    """One CONFIRMED-schema Recall real-time transcript passthrough body (inner ``data``).

    This is exactly the ``data`` dict ``transport.wire.parse_transcript`` consumes on the
    live path — ``{words, speaker, timestamp, end_of_turn}`` — the same shape the Doc 03
    e2e webhook-drain test drives (``tests/doc03/e2e/test_transcript_bridge_reaches_carrier``).
    Deliberately NOT the ``ingest_passthrough`` ``{start, is_final}`` shape the weak oracle
    used.
    """
    return {"words": words, "speaker": speaker, "timestamp": timestamp, "end_of_turn": end_of_turn}


#: A realistic interleaved two-speaker engineering exchange (code-heavy words on purpose so
#: the attribution is over real identifiers/filenames, not "hello world"). The tuple carries
#: the GROUND TRUTH the parser must reproduce: (words, speaker, timestamp). Alice and Bob
#: strictly alternate turns — a swap, drop, or cross-attribution breaks the ordering check.
_INTERLEAVED_STREAM: tuple[tuple[str, str, float], ...] = (
    ("what is the p95 on IngestPipeline.run_full", "Alice", 0.0),
    ("checking retry_backoff in applier.py now", "Bob", 3.2),
    ("the OAuth callback path or the webhook drain", "Alice", 6.5),
    ("webhook drain, drain_pending_webhooks specifically", "Bob", 9.1),
    ("okay ship the ACL fix behind the flag", "Alice", 12.7),
    ("agreed, opening the PR against main", "Bob", 15.4),
)


class _RecordingSubscriber:
    """A minimal stand-in for a real carrier consumer (Doc 03 notes / Doc 04 orchestrator).

    It drains the ONE real :class:`~transport.carrier.SignalCarrier` and records the ordered
    sequence of signals it observed, so two independent subscribers can be compared for
    identical delivery (AC-HEAR-04) — a real fan-out, not a single-consumer fake.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.transcripts: list[tuple[str, str, float]] = []
        self.gaps: list[tuple[float, float, str]] = []

    async def drain(self, stream: AsyncIterator[Any], *, until: int) -> None:
        from transport.hearing import TranscriptGap
        from transport.signals import Transcript

        async for signal in stream:
            if isinstance(signal, Transcript):
                self.transcripts.append((signal.words, signal.speaker, signal.t))
            elif isinstance(signal, TranscriptGap):
                self.gaps.append((signal.t_start, signal.t_end, signal.reason))
            if len(self.transcripts) + len(self.gaps) >= until:
                return


# ── AC-HEAR-06 + AC-HEAR-04 + AC-HEAR-08/09 — the real attribution + fan-out oracle ──────

def test_interleaved_two_speaker_stream_attributed_and_fanned_identically() -> None:
    """AC-HEAR-06 / AC-HEAR-04 / AC-HEAR-08 / AC-HEAR-09 on the PRODUCTION wire path.

    Drives ``HearingStage.ingest_wire_transcript`` with a realistic interleaved two-speaker
    Recall passthrough stream and proves, against a ground-truth interleaving the parser
    cannot have echoed:

    * each record is attributed to the CORRECT speaker (order + speaker + content align);
    * two independent carrier subscribers (Doc 03 + Doc 04 shape) receive the IDENTICAL
      ordered sequence, speakers and timestamps intact;
    * strict Alice/Bob alternation is preserved (a swap/merge/drop would break it);
    * ZERO Proxy lines route as asks while EVERY human line routes.

    criterion_id: AC-HEAR-06
    """
    from transport.carrier import SignalCarrier
    from transport.hearing import PROXY_SPEAKER, HearingStage

    carrier = SignalCarrier()

    # Interleave a Proxy self-line into the human exchange so the ask-routing gate is proven
    # on a mixed stream (not a single crafted line). Proxy speaks at t=4.0, between turns.
    stream: list[tuple[str, str, float]] = list(_INTERLEAVED_STREAM)
    stream.insert(2, ("p95 is 340ms on the checkout trace", PROXY_SPEAKER, 4.0))
    total = len(stream)  # 7 records: 6 human + 1 Proxy

    routed_asks: list[tuple[str, str]] = []

    def ask_sink(content: str, sender: str) -> None:
        routed_asks.append((content, sender))

    stage = HearingStage(carrier=carrier, ask_sink=ask_sink)

    doc03 = _RecordingSubscriber("doc03-notes")
    doc04 = _RecordingSubscriber("doc04-orchestrator")

    async def run() -> None:
        # Both subscribers attach BEFORE any emit so neither can miss a record.
        sub03 = carrier.subscribe()
        sub04 = carrier.subscribe()
        task03 = asyncio.create_task(doc03.drain(sub03, until=total))
        task04 = asyncio.create_task(doc04.drain(sub04, until=total))

        for words, speaker, timestamp in stream:
            await stage.ingest_wire_transcript(_wire(words, speaker, timestamp))

        carrier.close()
        await asyncio.gather(task03, task04)

    _run(run())

    # ── AC-HEAR-04: BOTH consumers saw the IDENTICAL ordered sequence (not just one fake) ──
    assert doc03.transcripts == doc04.transcripts, (
        "the two carrier consumers must receive the identical ordered transcript sequence"
    )
    fanned = doc03.transcripts
    assert len(fanned) == total, f"expected {total} fanned records, got {len(fanned)}"

    # ── AC-HEAR-06: attribution matches the GROUND TRUTH interleaving exactly ──────────────
    # Order + speaker + content must all line up. Because the parser receives words+speaker
    # separately and the stream strictly alternates humans, a cross-attribution, a swap, or
    # a dropped/merged record breaks this equality — it is not an echo of a single field.
    assert fanned == [(w, s, t) for (w, s, t) in stream], (
        "each record must carry the correct speaker + words + timestamp in original order"
    )

    # Strict human alternation preserved (Proxy line at index 2 excluded): Alice/Bob/Alice…
    human_speakers = [s for (_w, s, _t) in fanned if s != PROXY_SPEAKER]
    assert human_speakers == ["Alice", "Bob", "Alice", "Bob", "Alice", "Bob"], (
        f"two-speaker alternation not preserved: {human_speakers}"
    )

    # ── AC-HEAR-08: ZERO Proxy lines routed as asks (speaker-scoped self-loop guard) ───────
    assert all(sender != PROXY_SPEAKER for (_c, sender) in routed_asks), (
        "a Proxy-labelled line must NEVER be routed as an ask (it must not respond to itself)"
    )
    # But the Proxy line IS present on the carrier (recorded, inert) — AC-HEAR-07 shape.
    assert any(s == PROXY_SPEAKER for (_w, s, _t) in fanned), (
        "the Proxy self-line must still be recorded on the carrier, just never routed"
    )

    # ── AC-HEAR-09: EVERY human line WAS routed as an ask, none dropped by the guard ───────
    routed_by_content = {content for (content, _s) in routed_asks}
    for words, speaker, _t in stream:
        if speaker == PROXY_SPEAKER:
            continue
        assert words in routed_by_content, f"human line was not routed as an ask: {words!r}"
    assert len(routed_asks) == total - 1, (
        f"exactly the {total - 1} human lines route; Proxy's line does not "
        f"(got {len(routed_asks)})"
    )


# ── AC-HEAR-12 — malformed / drifted passthrough raises LOUDLY on the production entry ────

@pytest.mark.parametrize(
    ("bad_body", "why"),
    [
        ({"words": "hello", "timestamp": 0.0}, "missing the confirmed `speaker` field"),
        ({"speaker": "Alice", "timestamp": 0.0}, "missing the confirmed `words` field"),
        ({"words": "hello", "speaker": "Alice"}, "missing the confirmed `timestamp` field"),
        ({"words": "hello", "speaker": "Alice", "timestamp": "0.0"}, "timestamp type drift (str)"),
        ({"words": "  ", "speaker": "Alice", "timestamp": 0.0}, "blank words (incomplete shape)"),
        ({"words": "hello", "speaker": "  ", "timestamp": 0.0}, "blank speaker (incomplete shape)"),
    ],
)
def test_malformed_passthrough_raises_wire_drift_loudly(bad_body: dict[str, Any], why: str) -> None:
    """AC-HEAR-12: a drifted vendor body raises ``WireDriftError`` rather than fanning a
    wrong record downstream — proven through the PRODUCTION ``ingest_wire_transcript`` +
    the real ``transport.wire.parse_transcript`` (not the convenience ``ingest_passthrough``
    the weak oracle used), and NOTHING is emitted onto the carrier for the bad message.

    criterion_id: AC-HEAR-12
    """
    from transport.carrier import SignalCarrier
    from transport.hearing import HearingStage
    from transport.signals import Transcript
    from transport.wire import WireDriftError

    carrier = SignalCarrier()
    emitted: list[object] = []

    async def run() -> None:
        sub = carrier.subscribe()

        async def _probe() -> None:
            async for sig in sub:
                emitted.append(sig)

        probe = asyncio.create_task(_probe())
        stage = HearingStage(carrier=carrier)
        with pytest.raises(WireDriftError):
            await stage.ingest_wire_transcript(bad_body)
        carrier.close()
        probe.cancel()

    _run(run())
    assert not any(isinstance(s, Transcript) for s in emitted), (
        f"a drifted body ({why}) must NOT fan a Transcript downstream"
    )


# ── AC-HEAR-11 — un-transcribed stretch surfaces as an explicit mark-lost gap ─────────────

def test_untranscribed_gap_surfaces_as_explicit_mark_lost_on_carrier() -> None:
    """AC-HEAR-11: an un-transcribed stretch is surfaced as an explicit ``TranscriptGap``
    on the SAME carrier both consumers subscribe to — never silently absent — and Proxy
    never claims it buffered through the outage (the honest BYOK fallback, §3.2/§3.7).

    Drives real records around a real ``mark_lost`` window and proves BOTH consumers see the
    gap in-order between the surrounding transcripts.

    criterion_id: AC-HEAR-11
    """
    from transport.carrier import SignalCarrier
    from transport.hearing import HearingStage, TranscriptGap

    carrier = SignalCarrier()
    gap_callbacks: list[TranscriptGap] = []

    stage = HearingStage(carrier=carrier, on_gap=gap_callbacks.append)

    doc03 = _RecordingSubscriber("doc03-notes")
    doc04 = _RecordingSubscriber("doc04-orchestrator")

    async def run() -> None:
        sub03 = carrier.subscribe()
        sub04 = carrier.subscribe()
        # 3 signals each: one transcript, one gap, one transcript.
        task03 = asyncio.create_task(doc03.drain(sub03, until=3))
        task04 = asyncio.create_task(doc04.drain(sub04, until=3))

        await stage.ingest_wire_transcript(_wire("before the drop", "Alice", 0.0))
        # The external BYOK leg dropped 20s of audio — mark it lost, do not pretend to buffer.
        await stage.mark_lost(2.0, 22.0, reason="stt_gap")
        await stage.ingest_wire_transcript(_wire("after the rejoin", "Bob", 22.0))

        carrier.close()
        await asyncio.gather(task03, task04)

    _run(run())

    # The gap is an EXPLICIT record on the carrier, seen by BOTH consumers, in order.
    assert doc03.gaps == doc04.gaps == [(2.0, 22.0, "stt_gap")], (
        "the un-transcribed stretch must surface as an explicit mark-lost gap to both consumers"
    )
    assert doc03.transcripts == doc04.transcripts == [
        ("before the drop", "Alice", 0.0),
        ("after the rejoin", "Bob", 22.0),
    ]
    # The gap callback fired with the honest window (backfill hook), not a silent swallow.
    assert [(g.t_start, g.t_end, g.reason) for g in gap_callbacks] == [(2.0, 22.0, "stt_gap")]


def test_no_buffer_through_outage_handler_exists() -> None:
    """AC-HEAR-11 (guard): the module owns NO buffer-through / resume-after-gap handler.

    The un-transcribed path is mark-lost ONLY — buffering the external Recall→AssemblyAI
    leg is not ours to promise under BYOK (§3.2). This complements the sealed source guard
    by asserting the honest surface (``mark_lost`` + ``TranscriptGap``) is what exists and
    the over-promising resilience verbs do not.

    criterion_id: AC-HEAR-11
    """
    import inspect

    import transport.hearing as mod

    src = inspect.getsource(mod)
    for verb in ("buffer_through", "resume_after_gap", "buffer_during_outage", "replay_missed"):
        assert verb not in src, f"over-promising STT-resilience handler {verb!r} must not exist"
    assert "mark_lost" in src and "TranscriptGap" in src, "the honest mark-lost path must exist"


# ── AC-HEAR-02 — the production feeder instantiates NO Proxy-side STT client ───────────────

def test_wire_feeder_instantiates_no_proxy_side_stt_client() -> None:
    """AC-HEAR-02: driving the full ``ingest_wire_transcript`` path pulls in NO Proxy-side
    STT SDK/client. STT is AssemblyAI-via-Recall BYOK passthrough; transport only consumes
    the parsed transcript. Asserted statically over the production feeder + its wire parser
    (the modules actually executed on the live path), not merely the hearing module.

    criterion_id: AC-HEAR-02
    """
    import inspect

    import transport.hearing as hear_mod
    import transport.wire as wire_mod

    forbidden = (
        "AssemblyAI(",
        "aai.Client(",
        "aai.Transcriber(",
        "assemblyai.Client(",
        "assemblyai.Transcriber(",
        "import assemblyai",
        "import aai",
    )
    for mod in (hear_mod, wire_mod):
        src = inspect.getsource(mod)
        for token in forbidden:
            assert token not in src, (
                f"Proxy-side STT client {token!r} must not appear in {mod.__name__} "
                "(BYOK passthrough — AC-HEAR-02)"
            )
