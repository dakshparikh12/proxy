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
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any

log = logging.getLogger(__name__)

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
    from scribe.coalescer import ChatMessage as _ScribeChat
    from transport.signals import ChatMessage, MeetingEnd, Transcript

    pending: TranscriptSegment | None = None
    async for signal in stream:
        if isinstance(signal, ChatMessage):
            # Inbound meeting chat rides the SAME carrier the Scribe pump consumes
            # (transport.chat.ChatChannel.dispatch_inbound emits it here). Fold it into
            # the coalescer so the window whose span holds it carries it — meeting chat
            # is never dropped from the notes record (§3.1 / AC-COAL-04). The transport
            # ChatMessage carries no timestamp, so it lands at the current stream
            # position (the last final word seen; 0.0 before the first word), which the
            # coalescer folds into the current/next window (or sweeps at flush).
            ts_s = pending.start_s if pending is not None else 0.0
            coalescer.push_chat(
                _ScribeChat(sender=signal.sender, text=signal.message, ts_s=ts_s)
            )
            continue
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
            flushed = coalescer.flush()
            # Sweep any chat that landed after the last window's end (the meeting ended
            # on silence, so flush produced no trailing buffer to fold it into) onto the
            # last window — meeting chat is never lost from the notes record (§3.1).
            leftover = coalescer.drain_trailing_chat()
            if leftover and flushed:
                flushed[-1] = replace(
                    flushed[-1], chat_messages=flushed[-1].chat_messages + leftover
                )
            for window in flushed:
                await queue.put(window)
            await queue.put(None)  # sentinel: drain the serial consumer and stop
            return


def _close_segment(seg: TranscriptSegment, *, end_s: float) -> TranscriptSegment:
    """Fix a provisional segment's end (>= start, never a zero/negative span)."""
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

    async def _on_usage(usage: Any) -> None:
        # Derive this window's spend from the response usage token counts against the
        # Haiku rate card and write it straight to meeting_cost.model_usd + the cache
        # split (§3.9 / AC-COST-02). No provider seam in the cost path (AC-COST-11):
        # this is a direct Postgres write, not routed through call_external.
        from scribe.cost import record_scribe_cost_from_usage

        await record_scribe_cost_from_usage(db, meeting_id=header.meeting_id, usage=usage)

    async def scribe_call(meeting_id: str, window: Window) -> Any:
        # Read the LIVE rolling summary — Segment B carries the meeting's history.
        return await _real_scribe_call(
            header,
            holder.text,
            window,
            call_external=_call_external(),
            client=_client(),
            on_usage=_on_usage,
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


# ---------------------------------------------------------------------------
# The CLOSE PASS wiring — run scribe.close.run_close_pass on the real meeting end.
# ---------------------------------------------------------------------------
#
# Gap DOC03-CLOSE-PASS-UNWIRED: ``scribe.close.run_close_pass`` (the permanent
# notes deliverable — Sonnet enrichment over the folded ledger + gap/pending
# backfill -> markdown -> GCS create-only write -> chat link -> teardown, IN THAT
# ORDER, §3.7) had ZERO production callers. On a real meeting end the webhook
# ``call_ended`` path only drained the Scribe consumer and tore the runtime down,
# so the meeting's CORE deliverable was never produced live. This section is the
# missing assembly: it folds the durable ``note_deltas`` ledger, reduces it through
# the close model, and runs the ordered close BEFORE the runtime is torn down.
#
# Exactly as ``build_real_seams`` injects ``call_external`` / clients for the
# offline tier, :class:`CloseConfig` injects the three VENDOR edges (the Sonnet
# structured caller, the GCS bucket, the chat-link poster) so the PRODUCT
# orchestration runs for real while the recordable vendor boundary stays a seam.


@dataclass(frozen=True)
class CloseConfig:
    """The vendor/infra edges the close pass needs, injected at the boot seam.

    ``bucket``/``bucket_name`` are the GCS finalized-notes target; ``post_chat_link``
    posts the notes URL in the meeting chat; ``close_caller`` is the strong-model
    ``generateStructured`` surface (defaults to the real Anthropic-native caller);
    ``call_external`` is the ONE retry+cost telemetry funnel (defaults to the real
    ``libs.http.call_external``). Every field is a seam so the same orchestration
    runs live in a test (recordable caller + create-only bucket double) and in
    production (real Anthropic + real GCS) — never a product double.
    """

    bucket: Any
    bucket_name: str
    post_chat_link: Any
    close_caller: Any | None = None
    call_external: Any | None = None
    #: SEAM 1 (Doc 07 §2) — invoked with the finalized ``FinalNotes`` AFTER the ordered
    #: close has completed. Production supplies
    #: ``post_meeting.wire.make_intake_hook(db)``; ``server._build_close_config`` is the
    #: only place that construction happens.
    #:
    #: ``None`` is still permitted, because Doc 07 §2 requires the close to be identical
    #: whether or not post-meeting execution exists — but it is now an ERROR-logged
    #: condition, not a quiet default. It was a quiet default until this commit, and the
    #: result was a seam that ran on every close and did nothing.
    #:
    #: Whatever this is, it is called through :func:`_run_post_meeting_intake`, which
    #: cannot let it raise into the close.
    post_meeting_intake: Any | None = None


class _MeetingCloseOpSink:
    """The single ``operation_runs`` meeting-close row, DB-backed (AC-CLOSE-07).

    Maps the close-pass sink protocol onto the ONE durable ops table (there is NO
    close_jobs table): ``start`` claims a ``operation_type='meeting-close'`` running
    row keyed on ``scope_id=meeting_id``; ``mark_succeeded`` flips it to ``completed``
    and write-throughs the SDK-reported spend to ``meeting_cost.model_usd`` (§3.9);
    ``mark_failed`` flips it to ``failed`` and records the ``error_type`` on the row's
    ``error`` column. The status vocabulary is the schema's
    (running/completed/failed/interrupted) — ``succeeded`` maps to ``completed``.
    """

    def __init__(self, db: Any) -> None:
        self._db = db
        self._run_id: Any = None

    async def start(self, meeting_id: Any) -> None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO operation_runs (scope_id, operation_type, status, created_by)
                VALUES ($1, 'meeting-close', 'running', $2)
                ON CONFLICT (scope_id, operation_type) WHERE status = 'running'
                DO NOTHING
                RETURNING id
                """,
                str(meeting_id),
                getattr(self._db, "instance_id", "harness"),
            )
        if row is None:
            raise RuntimeError(
                f"meeting-close already running for {meeting_id!r} (crash-recovery guard)"
            )
        self._run_id = row["id"]

    async def mark_succeeded(self, meeting_id: Any, *, total_cost_usd: float | None) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                "UPDATE operation_runs SET status = 'completed', completed_at = now() "
                "WHERE id = $1 AND status = 'running'",
                self._run_id,
            )
            if total_cost_usd is not None:
                # Cost write-through to meeting_cost.model_usd (§3.9). ADD so the
                # close spend accumulates onto the live-meeting model spend rather
                # than clobbering it; the row is created lazily on first spend.
                await conn.execute(
                    """
                    INSERT INTO meeting_cost (meeting_id, model_usd, updated_at)
                    VALUES ($1, $2, now())
                    ON CONFLICT (meeting_id)
                    DO UPDATE SET model_usd = meeting_cost.model_usd + EXCLUDED.model_usd,
                                  updated_at = now()
                    """,
                    meeting_id,
                    float(total_cost_usd),
                )

    async def mark_failed(self, meeting_id: Any, *, error_type: str) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                "UPDATE operation_runs SET status = 'failed', completed_at = now(), error = $2 "
                "WHERE id = $1 AND status = 'running'",
                self._run_id,
                error_type,
            )


async def run_meeting_close(
    header: MeetingHeader,
    db: Any,
    close_config: CloseConfig,
    *,
    teardown: Callable[[], Awaitable[None]],
) -> Any:
    """Produce the permanent notes record on meeting end (the wired close pass, §3.7).

    The ONE production caller of ``scribe.close.run_close_pass``. Runs AFTER the
    serial Scribe consumer has drained (the ledger is complete) and BEFORE the
    runtime is torn down, in the mandatory order (render -> GCS create-only write ->
    chat link -> teardown):

      1. Fold the durable ``note_deltas`` ledger via the canonical ``read_notes``
         fold — the same object the room reads at ``/internal/notes``. An empty
         ledger (a meeting that produced no notes) is a no-op: nothing to finalize.
      2. Read the gap/pending transcript backfill (``status IN ('gap','pending')``)
         — the ONLY raw transcript the close pass pulls (§3.7 / AC-CLOSE-04).
      3. Reduce the folded ledger + backfill into ONE ``FinalNotes`` through the
         strong-model ``reduce_close`` (Sonnet seat, one pass under threshold).
      4. Run ``run_close_pass``: render markdown -> GCS ``write_finalized_notes``
         (create-only, ``if_generation_match=0``) -> post the chat link -> teardown,
         each step confirmed before the next, all on ONE ``meeting-close``
         operation_runs row.

    Returns the ``CloseResult`` (``None`` if the ledger was empty, so teardown is
    still the caller's responsibility). Never re-raises a close failure past the
    op-row bookkeeping — the sink records the honest failed row and the error
    surfaces so a human operator observes the blocked teardown (§3.8).
    """
    from scribe.close import (
        OPERATION_TYPE,
        CloseInput,
        anthropic_structured_caller,
        reduce_close,
        resolve_close_model,
        run_close_pass,
    )
    from scribe.notes_reader import read_notes

    meeting_id = header.meeting_id

    # 1) Fold the durable ledger. An empty ledger has nothing to finalize.
    notes = await read_notes(meeting_id, db=db)
    if notes.is_empty:
        return None

    folded_ledger = notes.render_for_summary()

    # 2) Gap/pending backfill — the only raw transcript the close pass reads.
    from scribe.close import fetch_gap_pending_spans

    async with db.acquire() as conn:
        spans = await fetch_gap_pending_spans(conn, meeting_id)

    close_input = CloseInput(folded_ledger=folded_ledger, gap_pending_spans=spans)

    # 3) Reduce into ONE FinalNotes through the strong-model close call.
    caller = (
        close_config.close_caller
        if close_config.close_caller is not None
        else anthropic_structured_caller()
    )
    if close_config.call_external is not None:
        call_external = close_config.call_external
    else:
        from libs.http.src.http.external import call_external as call_external  # lazy vendor seam

    model = resolve_close_model()
    reduced = await reduce_close(
        close_input, model=model, caller=caller, call_external=call_external
    )

    # 4) The ordered close (render -> GCS -> chat link -> teardown) on ONE op row.
    _ = OPERATION_TYPE  # documents the operation_runs.operation_type this sink writes
    sink = _MeetingCloseOpSink(db)
    close_result = await run_close_pass(
        meeting_id,
        reduced.final_notes,
        reduced.total_cost_usd,
        bucket=close_config.bucket,
        bucket_name=close_config.bucket_name,
        post_chat_link=close_config.post_chat_link,
        teardown=teardown,
        op_sink=sink,
    )

    # 5) SEAM 1 (Doc 07 §2) — the record is written; post-meeting execution may now read
    #    it. Strictly AFTER run_close_pass returns, so nothing here can hold the bot,
    #    insert a step into the ordered close, or touch the notes object. The close's
    #    return value is computed above and is returned unchanged below whatever happens.
    await _run_post_meeting_intake(close_config, meeting_id, reduced.final_notes)
    return close_result


async def _run_post_meeting_intake(
    close_config: CloseConfig, meeting_id: Any, final_notes: Any
) -> None:
    """Call Doc 07's intake, and NEVER let it affect the close (Doc 07 §2).

    The hook is optional and the call is total: a missing hook, a hook that raises, a hook
    that hangs on a bad import — none of it changes the close's outcome or its return
    value. The failure is logged and dropped here rather than propagating, because the
    close pass has already written the permanent record by this point and there is nothing
    left for the caller to do differently.
    """
    hook = getattr(close_config, "post_meeting_intake", None)
    if hook is None:
        # LOUD. This used to `return` silently, and that silence is why the seam shipped
        # dead: _build_close_config never set the hook, every production close took this
        # branch, and nothing anywhere said so. A seam that looks connected, proves itself
        # against an injected stand-in, and no-ops in production is the same false-green
        # shape as a store-backed test skipping when the DSN is absent.
        #
        # Still not raised: Doc 07 §2 requires the close and the meeting record to be
        # identical whether or not post-meeting execution exists, so the close continues.
        # The cost of being wrong here is one ERROR line; the cost of being quiet was every
        # action item in every meeting silently never becoming a task.
        log.error(
            "post-meeting intake NOT wired for meeting %s: CloseConfig.post_meeting_intake "
            "is None, so no action item from this meeting will become a task. The close "
            "itself is unaffected. Fix: pass post_meeting_intake=make_intake_hook(db) "
            "where CloseConfig is constructed (server._build_close_config).",
            meeting_id,
        )
        return
    try:
        await hook(final_notes, meeting_id=meeting_id)
    except BaseException:  # noqa: BLE001 - Doc 07 §2: the close is unaffected, whatever
        # class of failure post-meeting execution produces.
        log.exception(
            "post-meeting intake failed for meeting %s; close and record unaffected",
            meeting_id,
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
    "CloseConfig",
    "launch_scribe_runtime",
    "build_real_seams",
    "start_meeting_scribe",
    "run_meeting_close",
]

# Silence "imported but unused" for the re-exported protocol types used in signatures.
_ = (Callable, Awaitable, DeltaApplier, GapRecorder, ScribeCaller)
