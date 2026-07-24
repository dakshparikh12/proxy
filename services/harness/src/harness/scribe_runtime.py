"""The live notes engine — assembled in the harness, one serial consumer per meeting.

Doc 03 §3.1/§2: a real meeting maintains the notes ledger through ONE serial
consumer. ``scribe.pipeline.run_scribe`` is that consumer, but nothing in the
product assembled it — the coalescer→scribe→applier→gap chain only composed inside
tests (DOC03-SCRIBE-PIPELINE-UNWIRED). This module is the missing assembly: it is
imported by the harness (the ``meeting_runtime`` process) and, on meeting join,

    * subscribes to the Doc 02 ``SignalCarrier`` — the ONE in-process transcript
      stream (no bus, no socket): ``carrier.subscribe()`` yields the live signals;
    * feeds each ``Transcript`` signal into a real ``Coalescer`` that cuts natural
      windows (speaker-turn / pause / cap — §3.1 physics, code owns only physics);
    * enqueues each cut window onto an ``asyncio.Queue`` and launches the real
      ``run_scribe`` serial consumer over that queue; and
    * on ``MeetingEnd`` flushes the trailing partial window and pushes the ``None``
      sentinel so the consumer drains and stops (never inferred from silence, §3.1).

The three seams ``run_scribe`` takes (``scribe_call`` / ``apply_delta`` /
``mark_gap``) are the vendor/Postgres boundary. :func:`launch_scribe_runtime` takes
them as parameters so the assembly is drivable off a live carrier in a test, while
:func:`build_real_seams` binds the REAL ones — ``scribe.call.scribe_call`` through
the single ``libs.http.call_external`` funnel + the real Anthropic client, the
transactional ``apply_note_delta`` applier, and the comprehension-gap writer — for
the production wiring in :func:`start_meeting_scribe`.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from scribe.call import scribe_call as _real_scribe_call
from scribe.coalescer import Coalescer, TranscriptSegment, Window
from scribe.notes_reader import read_notes
from scribe.pipeline import DeltaApplier, GapRecorder, HostBudget, ScribeCaller, run_scribe
from scribe.prefix import MeetingHeader
from scribe.referent import ReferentCorpus, lookup_referent
from scribe.rolling_summary import (
    SummaryState,
    maybe_refresh_in_background,
    regenerate_rolling_summary,
)


# A transcript word count → token estimate (physics only): ~1 token per word is a
# conservative, deterministic proxy the coalescer's token cap can measure against.
# The real byte-exact token accounting lives in the vendor call; here we only need
# a monotone count so window cutting is deterministic (Law 4: code owns physics).
def _estimate_tokens(words: str) -> int:
    return max(1, len(words.split()))


# A trailing transcript with no successor carries no natural end; give it a nominal
# span so the last window still has a positive duration. unit: seconds.
_TRAILING_SPAN_S: float = 1.0


@dataclass
class ScribeRuntimeHandle:
    """A live meeting's notes engine — the pump task + the serial consumer task."""

    meeting_id: str
    _pump: "asyncio.Task[None]"
    _consumer: "asyncio.Task[None]"

    async def wait(self) -> None:
        """Await the whole engine draining (pump finished → consumer drained)."""
        await asyncio.gather(self._pump, self._consumer)

    async def aclose(self) -> None:
        """Cancel the engine (host teardown) — best-effort, never raises."""
        for task in (self._pump, self._consumer):
            task.cancel()
        await asyncio.gather(self._pump, self._consumer, return_exceptions=True)


async def _pump_transcripts(
    stream: Any,
    coalescer: Coalescer,
    queue: "asyncio.Queue[Window | None]",
) -> None:
    """Drive the live carrier stream into the window queue (§3.1 coalescing).

    Consumes the ONE in-process carrier's signal iterator, turns each ``Transcript``
    signal into a coalescer ``TranscriptSegment`` (its end is the next word's start;
    the last word gets a nominal span), and enqueues every window the coalescer
    cuts. On ``MeetingEnd`` it flushes the trailing partial window and enqueues the
    ``None`` sentinel so the serial consumer drains and stops — end is explicit,
    never inferred from silence.

    The subscription itself is registered synchronously in
    :func:`launch_scribe_runtime` (before any emit can race the pump task's first
    run), so no early ``Transcript`` is ever dropped on the floor.
    """
    # Import lazily so the module imports without the transport package resolved.
    from transport.signals import MeetingEnd, Transcript

    pending: TranscriptSegment | None = None
    async for signal in stream:
        if isinstance(signal, Transcript):
            if not signal.is_final:
                continue  # partial hypotheses do not cut windows (final words only)
            if pending is not None:
                # The previous word ends where this one begins (speaker-attributed span).
                seg = _close_segment(pending, end_s=signal.t)
                for window in coalescer.feed(seg):
                    await queue.put(window)
            pending = TranscriptSegment(
                speaker=signal.speaker,
                text=signal.words,
                start_s=signal.t,
                end_s=signal.t,  # provisional; closed when the next word arrives
                token_count=_estimate_tokens(signal.words),
            )
        elif isinstance(signal, MeetingEnd):
            if pending is not None:
                seg = _close_segment(pending, end_s=pending.start_s + _TRAILING_SPAN_S)
                for window in coalescer.feed(seg):
                    await queue.put(window)
                pending = None
            for window in coalescer.flush():
                await queue.put(window)
            await queue.put(None)  # sentinel: drain the serial consumer and stop
            return


def _close_segment(seg: TranscriptSegment, *, end_s: float) -> TranscriptSegment:
    """Fix a provisional segment's end (>= start, never a zero/negative span)."""
    from dataclasses import replace

    fixed_end = end_s if end_s > seg.start_s else seg.start_s + _TRAILING_SPAN_S
    return replace(seg, end_s=fixed_end)


def launch_scribe_runtime(
    header: MeetingHeader,
    carrier: Any,
    *,
    scribe_call: ScribeCaller,
    apply_delta: DeltaApplier,
    mark_gap: GapRecorder,
    host_budget: HostBudget | None = None,
    coalescer: Coalescer | None = None,
) -> ScribeRuntimeHandle:
    """Assemble + launch the live notes engine for ONE meeting off the live carrier.

    Builds the window queue, launches the transcript pump (carrier → coalescer →
    queue) and the ``run_scribe`` serial consumer over that queue, and returns a
    handle. This is the ONE production caller of ``run_scribe`` — the assembly the
    audit rule requires (a capability that only works when a test injects it is NOT
    done). The three seams are parameters so the SAME assembly runs live in a test
    and in production; :func:`start_meeting_scribe` binds the real ones.
    """
    queue: "asyncio.Queue[Window | None]" = asyncio.Queue()
    coal = coalescer if coalescer is not None else Coalescer()

    # Register the subscription SYNCHRONOUSLY here — before returning — so an emit
    # that happens before the pump task first runs is still delivered (the carrier
    # only fans to already-registered subscribers).
    stream = carrier.subscribe()
    pump = asyncio.ensure_future(_pump_transcripts(stream, coal, queue))
    consumer = asyncio.ensure_future(
        run_scribe(
            header.meeting_id,
            queue,
            scribe_call=scribe_call,
            apply_delta=apply_delta,
            mark_gap=mark_gap,
            host_budget=host_budget,
        )
    )
    return ScribeRuntimeHandle(meeting_id=header.meeting_id, _pump=pump, _consumer=consumer)


# ---------------------------------------------------------------------------
# The REAL seams (vendor + Postgres) — bound for the production wiring.
# ---------------------------------------------------------------------------

@dataclass
class _SummaryHolder:
    """The live, mutable Segment B text the ``scribe_call`` closure reads each window.

    §3.2/§4: the rolling summary IS cached Segment B — it carries the meeting's
    history into every micro-call. The rolling-summary cadence task swaps a freshly
    regenerated summary into :attr:`text`; the very next ``scribe_call`` reads it, so
    the Scribe sees the meeting's cross-time structure (back-references, a number at
    min 3 vs min 20, a decision's forming->final progression) instead of the fixed
    head + the single newest window. Starts empty (window-local) until the first
    refresh fires.
    """

    text: str = ""


@dataclass(frozen=True)
class RealSeams:
    """The bound production seams ``run_scribe`` consumes, plus the rolling-summary wiring.

    The three seams (``scribe_call`` / ``apply_delta`` / ``mark_gap``) are what
    ``run_scribe`` calls. The rolling-summary members are the beside-the-loop cadence
    that keeps Segment B live: :attr:`summary_holder` is the mutable text the
    ``scribe_call`` closure reads; :attr:`summary_state` is the per-meeting cadence
    (delta count + clock); :attr:`refresh_summary` folds the live notes and swaps the
    holder. ``apply_delta`` drives the cadence off the hot path — a test can also
    drive :attr:`refresh_summary` directly to assert the wiring on the real path.
    """

    scribe_call: ScribeCaller
    apply_delta: DeltaApplier
    mark_gap: GapRecorder
    summary_holder: _SummaryHolder
    summary_state: SummaryState
    refresh_summary: Callable[[str], Awaitable[None]]


# Kind → the stable id prefix a minted entry carries. A kind-prefixed id (``c3``,
# ``d1`` …) is human-legible in the notes object AND lets the per-kind counter be
# reconstructed from the ledger, so an id the model references across windows
# resolves to the same entry (the fold keys every op on ``entry_id``).
_KIND_PREFIX: dict[str, str] = {
    "claim": "c",
    "decision": "d",
    "action": "a",
    "open_question": "q",
    "context": "x",
}


def _kind_counters(existing: list[dict[str, Any]]) -> dict[str, int]:
    """Reconstruct the per-kind max minted-id counter from the meeting's ledger.

    Scans every prior ``add`` row's ``entry_id`` (``<prefix><n>``) and records the
    highest ``n`` seen per prefix, so the NEXT minted id for that kind is
    ``max + 1`` — stable across windows and collision-free with any prior add.
    """
    max_by_prefix: dict[str, int] = {}
    for row in existing:
        if str(row.get("op")) != "add":
            continue
        eid = str(row.get("entry_id", ""))
        # split the trailing integer off the alpha prefix (e.g. "c12" -> "c", 12).
        i = len(eid)
        while i > 0 and eid[i - 1].isdigit():
            i -= 1
        prefix, digits = eid[:i], eid[i:]
        if not digits:
            continue
        n = int(digits)
        if n > max_by_prefix.get(prefix, 0):
            max_by_prefix[prefix] = n
    return max_by_prefix


def _bind_referents(
    entry: Any, corpus: "ReferentCorpus | None"
) -> dict[str, dict[str, Any]]:
    """Deterministically bind an entry's referent candidates to real code nodes.

    §3.4 / AC-REFM-06 / AC-FAIL-07(-NEG): for each ``referents`` term the Scribe
    marked, run the deterministic, no-LLM :func:`scribe.referent.lookup_referent`
    over the meeting's ``graph_nodes`` + overview-areas corpus and record the outcome
    as ``{term: {"binding": node_id|area|None, "binding_status": "bound"|"unbound"}}``.

    A term that matches a real node/area is ``bound`` with the REAL id from the corpus
    (never fabricated). A term that matches nothing — or ANY term when no corpus is
    configured for the meeting — stays honestly ``unbound`` with ``binding=None``
    (§3.8: the notes never fabricate to fill a hole). The original ``referents`` names
    are always kept verbatim on the entry; this map rides ALONGSIDE them as the
    resolved binding the Workroom (Doc 05) reads off ``/internal/notes``.
    """
    terms = getattr(entry, "referents", None) or []
    bindings: dict[str, dict[str, Any]] = {}
    for term in terms:
        binding = lookup_referent(term, corpus) if corpus is not None else None
        bindings[term] = {
            "binding": binding,
            "binding_status": "bound" if binding is not None else "unbound",
        }
    return bindings


def _canonical_row(
    op: Any, counters: dict[str, int], corpus: "ReferentCorpus | None" = None
) -> tuple[str, str, dict[str, Any]]:
    """Turn ONE delta op into the ``(entry_id, op, payload)`` the fold reader expects.

    The shape MUST match ``scribe.notes_reader.Notes.fold_all``:

    * ``add`` — mint a stable kind-prefixed id (``counters`` is mutated in place so a
      multi-add window gets distinct ids); the payload IS the entry's fields at top
      level (``op.entry.model_dump``), never the ``{op, entry}`` envelope. When the
      entry carries ``referents``, the deterministic matcher binds each term over the
      meeting's ``corpus`` and the resolved ``referent_bindings`` map rides on the
      payload (AC-REFM-06) — surviving the fold verbatim to the Workroom read.
    * ``patch`` — keyed on ``op.target_id``; payload ``{changes, supersede_reason}``
      (the fold reads ``payload["changes"]``, superseded-not-erased).
    * ``close`` — keyed on ``op.target_id``; payload ``{resolution}`` (the fold sets
      ``resolved=True`` and copies the resolution).
    """
    op_name = getattr(op, "op", "add")
    if op_name == "add":
        entry = op.entry
        kind = getattr(entry, "kind", "context")
        prefix = _KIND_PREFIX.get(kind, "e")
        counters[prefix] = counters.get(prefix, 0) + 1
        entry_id = f"{prefix}{counters[prefix]}"
        payload = entry.model_dump(mode="json")
        # Bind the referent candidates deterministically (no LLM) and attach the
        # resolved bindings so the cross-service /internal/notes read is code-oriented.
        if getattr(entry, "referents", None):
            payload["referent_bindings"] = _bind_referents(entry, corpus)
        return entry_id, "add", payload
    if op_name == "patch":
        return (
            op.target_id,
            "patch",
            {"changes": op.changes, "supersede_reason": op.supersede_reason},
        )
    if op_name == "close":
        return op.target_id, "close", {"resolution": op.resolution}
    # Forward-compatible: an unknown op is stored raw under a phantom-free id so the
    # fold's unknown-op branch ignores it rather than crashing the applier.
    return (
        getattr(op, "target_id", "_unknown"),
        op_name,
        op.model_dump(mode="json") if hasattr(op, "model_dump") else {"raw": str(op)},
    )


def build_real_seams(
    header: MeetingHeader,
    db: Any,
    *,
    summary_client: Any | None = None,
    call_external: Any | None = None,
    referent_corpus: ReferentCorpus | None = None,
) -> RealSeams:
    """Bind the REAL vendor/Postgres seams for a live meeting (production wiring).

    * ``scribe_call`` — the single ``scribe.call.scribe_call`` micro-call, routed
      through the ONE ``libs.http.call_external`` funnel (retry + cost telemetry)
      with the real Anthropic client. It reads the LIVE rolling summary from
      ``summary_holder.text`` on every window — so once the cadence has refreshed
      Segment B, the Scribe sees the meeting's history (back-references, a number at
      min 3 vs min 20, a decision's forming->final arc), not just the newest window.
    * ``apply_delta`` — appends each op to the append-only ``note_deltas`` ledger in
      one transaction (the durable notes object is the left-fold of this ledger).
      Before append, for each add op whose entry carries ``referents`` it runs the
      deterministic no-LLM matcher (``scribe.referent.lookup_referent``) over the
      meeting's ``referent_corpus`` and attaches the resolved ``referent_bindings``
      onto the payload (§3.4 / AC-REFM-06) — so the cross-service ``/internal/notes``
      read the Workroom consumes is code-oriented; an unmatched term (or any term
      when no corpus is configured) stays honestly unbound. It then drives the
      rolling-summary cadence OFF the hot path: it bumps
      ``summary_state`` and, on ``rolling_summary_due`` (N≈20 deltas OR ≈90s),
      schedules ``refresh_summary`` as a fire-and-forget task (AC-SCRIBE-07) so the
      next window never waits on the summary regen call.
    * ``mark_gap`` — records a dropped span as an explicit comprehension gap on the
      transcript plane (a ``status='gap'`` segment), never a silent miss (§3.1/§3.3).
    * ``refresh_summary`` — folds the meeting's LIVE notes object from ``note_deltas``
      (the same canonical ``read_notes`` fold the room reads), renders it stable-ordered,
      regenerates the compact Segment B via ``regenerate_rolling_summary`` (through the
      ONE ``call_external`` seam), and swaps the new text into ``summary_holder`` — the
      very next micro-call reads it.
    """
    # The vendor client is built LAZILY on the first micro-call — never at
    # bind time — so the Postgres-only seams (apply_delta / mark_gap) neither
    # require the Anthropic SDK to be importable nor open a client for a meeting
    # that produces no window. One client is constructed per meeting and reused.
    _client_box: dict[str, Any] = {}
    holder = _SummaryHolder()
    summary_state = SummaryState()

    def _client() -> Any:
        from libs.http.src.http.external import anthropic_client

        client = _client_box.get("client")
        if client is None:
            client = anthropic_client()
            _client_box["client"] = client
        return client

    def _call_external() -> Any:
        # The ONE retry+cost-telemetry funnel (§14). Resolved lazily so the vendor SDK
        # is imported only when a real call fires; ``call_external`` overrides it for
        # the offline integration tier (the vendor boundary, never a product double).
        if call_external is not None:
            return call_external
        from libs.http.src.http.external import call_external as real_call_external

        return real_call_external

    async def scribe_call(meeting_id: str, window: Window) -> Any:
        # Read the LIVE rolling summary — Segment B carries the meeting's history.
        return await _real_scribe_call(
            header, holder.text, window, call_external=_call_external(), client=_client()
        )

    async def refresh_summary(meeting_id: str) -> None:
        """Fold the live notes, regenerate Segment B, swap it into the holder.

        Reads the CURRENT notes object off the durable ``note_deltas`` fold (never the
        raw transcript, §3.2), renders it stable-ordered, and regenerates the compact
        summary through the one external-call seam. On an empty ledger (no deltas yet)
        there is nothing to summarise, so the holder is left untouched. The summary
        model uses the same Anthropic client as the Scribe (``summary_client`` overrides
        only for the deterministic wiring test — never a production double).
        """
        notes = await read_notes(meeting_id, db=db)
        if notes.is_empty:
            return
        notes_text = notes.render_for_summary()
        new_summary = await regenerate_rolling_summary(
            notes_text,
            call_external=_call_external(),
            client=summary_client if summary_client is not None else _client(),
        )
        holder.text = new_summary

    async def apply_delta(meeting_id: str, window: Window, delta: Any) -> None:
        from db.repos import notes as notes_repo

        ops = getattr(delta, "ops", None) or []
        current_goal = getattr(delta, "current_goal", None)
        async with db.acquire() as conn:
            async with conn.transaction():
                # Derive the per-kind minted-id counter from the meeting's EXISTING
                # note_deltas in the SAME tx, so an id the LLM references across
                # windows resolves and a fresh add never collides with a prior one.
                existing = await notes_repo.load_deltas(conn, meeting_id)
                counters = _kind_counters(existing)
                first = True
                for op in ops:
                    entry_id, op_name, payload = _canonical_row(
                        op, counters, referent_corpus
                    )
                    # The one-line goal/blocker signal rides the first written row's
                    # payload; the fold (Notes.fold_all) reads current_goal off ANY
                    # row, so one carrier is enough (§3.3.1).
                    if first and current_goal is not None:
                        payload = {**payload, "current_goal": current_goal}
                        first = False
                    await notes_repo.append_delta(
                        conn,
                        meeting_id=meeting_id,
                        entry_id=entry_id,
                        op=op_name,
                        payload=payload,
                        window_start_s=window.start_s,
                    )
                # A delta that carries ONLY a goal (no ops) still records it, so the
                # room's goal/blocker line updates even on an op-less window.
                if first and current_goal is not None:
                    await notes_repo.append_delta(
                        conn,
                        meeting_id=meeting_id,
                        entry_id="_goal",
                        op="patch",
                        payload={"current_goal": current_goal},
                        window_start_s=window.start_s,
                    )
        # Beside-the-loop rolling-summary cadence (§3.2): count the applied deltas and,
        # if a refresh is now due (N≈20 deltas OR ≈90s), regenerate Segment B OFF the
        # hot path — the tx is already committed, so the fold reads the fresh notes and
        # the serial consumer proceeds to the next window without awaiting the regen.
        applied_ops = max(1, len(ops))  # a goal-only (op-less) window still counts as one
        summary_state.note_delta_applied(applied_ops)
        maybe_refresh_in_background(
            summary_state,
            lambda: refresh_summary(meeting_id),
            now_s=asyncio.get_event_loop().time(),
        )

    async def mark_gap(
        meeting_id: str, start_s: float, end_s: float, *, reason: str
    ) -> None:
        from db import repos

        async with db.acquire() as conn:
            row = await repos.notes.insert_segment(
                conn,
                meeting_id=meeting_id,
                text=f"[comprehension gap: {reason}]",
                start_s=start_s,
                end_s=end_s,
                status="gap",
            )
        del row  # inserted for the close-pass backfill; id not needed here

    return RealSeams(
        scribe_call=scribe_call,
        apply_delta=apply_delta,
        mark_gap=mark_gap,
        summary_holder=holder,
        summary_state=summary_state,
        refresh_summary=refresh_summary,
    )


def start_meeting_scribe(
    header: MeetingHeader,
    carrier: Any,
    db: Any,
    *,
    host_budget: HostBudget | None = None,
    referent_corpus: ReferentCorpus | None = None,
) -> ScribeRuntimeHandle:
    """Production entrypoint — launch the live notes engine with the REAL seams.

    Called on meeting join (alongside the Recall bot launch) so a live meeting
    actually maintains the ledger: the Doc 02 transcript stream flows through the
    coalescer into the real serial Scribe consumer, whose applied deltas are the
    durable notes object read cross-service at ``GET /internal/notes/{meeting_id}``.

    ``referent_corpus`` is the meeting's code-index handle (Doc 01's overview areas +
    per-repo ``graph_nodes``); when supplied the applier binds each marked referent to
    a real code node so the notes the Workroom reads are code-oriented. Absent (a
    meeting with no built index) referents stay honestly named-but-unbound.
    """
    seams = build_real_seams(header, db, referent_corpus=referent_corpus)
    return launch_scribe_runtime(
        header,
        carrier,
        scribe_call=seams.scribe_call,
        apply_delta=seams.apply_delta,
        mark_gap=seams.mark_gap,
        host_budget=host_budget,
    )


__all__ = [
    "ScribeRuntimeHandle",
    "RealSeams",
    "launch_scribe_runtime",
    "build_real_seams",
    "start_meeting_scribe",
]

# Silence "imported but unused" for the re-exported protocol types used in signatures.
_ = (Callable, Awaitable, DeltaApplier, GapRecorder, ScribeCaller)
