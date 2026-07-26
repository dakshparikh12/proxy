"""scribe/close.py — the CLOSE PASS (the permanent record, §3.7).

On Doc 02's meeting-end signal the Scribe runs ONE strong-model pass
(Sonnet-class, ``PROXY_MODEL_SCRIBE_CLOSE``) that **reduces over the FOLDED
note-delta ledger + the gap/pending transcript backfill** — never the full raw
transcript as a map-reduce (CANONICAL §12.10). The folded ledger is already the
comprehension, so the pass reduces over it and pulls raw transcript **only** for
the ``status='gap'`` / ``status='pending'`` spans no live window ever
comprehended. It chunk-reduces ONLY when the folded ledger exceeds a token
threshold — a normal meeting folds in one pass.

Unlike the hot Scribe loop, the close pass is **not latency-bound**, so it runs
as the Agent SDK's ``generateStructured`` (``outputFormat:{type:'json_schema'}``
→ Pydantic re-validate) and inherits the real terminal subtypes as typed errors
— ``error_max_turns`` and ``error_max_structured_output_retries`` — plus
``total_cost_usd`` on the result.

V0 close deliverable (this is the ENTIRE close output, run as a single
``operation_runs`` row ``operation_type='meeting-close'`` — NO ``close_jobs``
table): render the final object through the notes template to markdown, write it
to GCS via :func:`scribe.notes_artifact.write_finalized_notes` with
``if_generation_match=0`` (create-only, exactly once), post the link in the
meeting chat, THEN tear down — **in that order**, so a durable notes URL exists
before teardown and the bot never leaves without the record posted.

Build-time SDK-surface pin (AC-CLOSE-15 / CANONICAL §11.10)
----------------------------------------------------------
``generateStructured`` / ``outputFormat`` / ``json_schema`` and the terminal-error
subtypes are pinned from the **TypeScript** ``~/platform`` Agent SDK
(``AgentService.ts:772-777``). The close pass runs on the **Python**
``claude_agent_sdk``, whose structured-output surface MUST be confirmed against
the live ``claude_agent_sdk`` docs at build — a design doc cannot pin a
third-party wire shape. **Build realization:** the ``claude_agent_sdk`` Python
wrapper was deferred (a design doc cannot pin its surface), so the concrete
:func:`anthropic_structured_caller` realizes ``generateStructured`` NATIVELY on the
installed ``anthropic`` Messages API — a forced-tool call whose ``input_schema`` IS
the FinalNotes JSON Schema, so the ``tool_use`` input is the structured payload
(exactly ``outputFormat:{type:'json_schema'}`` semantics). It is driven through the
real ``libs.http.call_external`` seam — never a ``Mock()`` — and the vendor (reality)
tier now runs for real against a recorded cassette (``tests/doc03/close``). The pinned
TS names recorded here for parity: method ``generateStructured``, parameter
``outputFormat={"type": "json_schema", "schema": <JSONSchema>}``, terminal
subtypes ``error_max_turns`` / ``error_max_structured_output_retries``, result
field ``total_cost_usd``. Swapping in ``claude_agent_sdk`` later means only
supplying a different :class:`StructuredCaller` — the close logic is unchanged.

Import discipline: this module NEVER imports the vendor SDK (``anthropic`` /
``claude_agent_sdk``) or ``libs.http.external`` (which imports ``anthropic`` at
module scope) at module top-level — the caller injects the seam and the
structured caller, exactly like :mod:`scribe.call` injects ``call_external`` and
lazily constructs its client. That keeps the pure ordering / composition /
tier-guard logic importable and unit-testable on a host without the vendor SDK.
"""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Protocol

from pydantic import BaseModel, Field

from ._seams import CallExternal as CallExternal
from .notes_artifact import (
    NotesGenerationConflictError as NotesGenerationConflictError,
)
from .notes_artifact import (
    write_finalized_notes,
)

# ── Constants (physics, not judgment) ───────────────────────────────────────

# The close-pass model seat (§3.7). Resolved from PROXY_MODEL_SCRIBE_CLOSE via
# the ONE canonical seat table; the seat default is Sonnet-class. Never a
# hard-coded model string.
CLOSE_SEAT = "SCRIBE_CLOSE"

# operation_runs.operation_type for the close pass. There is NO close_jobs table
# and no other dedicated close-tracking table (AC-CLOSE-07): the close pass is
# one coarse durable unit on the ONE ops table.
OPERATION_TYPE = "meeting-close"

# Telemetry service label handed to the cost seam.
_SERVICE = "anthropic"

# Chunk-reduce token threshold (§3.7 / AC-CLOSE-05/05B). A folded ledger BELOW
# this reduces in exactly one model call; ABOVE it enters the chunk-reduce path.
# A physics constant — the situation→action mapping (whether THIS meeting chunks)
# is pure arithmetic on the ledger's token count, not a hard-coded per-meeting
# rule. Approx-token accounting (~4 chars/token) is enough for a coarse gate.
CHUNK_REDUCE_TOKEN_THRESHOLD: int = 150_000
_CHARS_PER_TOKEN: int = 4

# Bounded retry for the chat-link post (AC-CLOSE-10 / -10-NEG). The bot never
# leaves without the record posted, so a transient post failure is retried;
# teardown never runs between the failure and the successful retry, and a
# PERMANENT failure (retries exhausted) blocks teardown and surfaces the error.
CHAT_POST_MAX_ATTEMPTS: int = 3


# ── Typed terminal errors (never a bare Exception, never swallowed) ──────────

class CloseError(Exception):
    """Base for every close-pass terminal error (§3.8 — failures spoken plainly)."""


class ConfigurationError(CloseError):
    """A misconfiguration detected BEFORE any model call is issued (fail-fast)."""


class HaikuModelRejectedError(ConfigurationError):
    """PROXY_MODEL_SCRIBE_CLOSE resolved to a Haiku-class model (AC-CLOSE-03-NEG).

    The close pass is a strong-model (Sonnet-class) pass by definition (§3.7);
    a Haiku-class seat is rejected at start, before the model is ever invoked.
    """


class CloseMaxTurnsError(CloseError):
    """The SDK terminal subtype ``error_max_turns`` (AC-CLOSE-06-NEG).

    Caught as its OWN typed subtype — never swallowed, never collapsed into a
    generic ``Exception`` first. ``error_type`` is the wire tag recorded on the
    operation_runs row.
    """

    error_type = "error_max_turns"


class CloseMaxStructuredRetriesError(CloseError):
    """The SDK terminal subtype ``error_max_structured_output_retries``
    (AC-CLOSE-06B-NEG) — DISTINCT from :class:`CloseMaxTurnsError`, handled in a
    separate except branch, recorded as its own ``error_type``.
    """

    error_type = "error_max_structured_output_retries"


class CloseVendorError(CloseError):
    """Any other honest vendor:anthropic failure surfaced at the real seam
    (AC-CLOSE-01-NEG / -12-NEG): 5xx, timeout, or malformed/garbage body that
    fails Pydantic re-validation. No silent proceed, no corruption.
    """


class DatabaseConnectionError(CloseError):
    """The gap/pending backfill read could not reach Postgres (AC-CLOSE-04-NEG).

    The close pass surfaces this rather than silently skipping the backfill and
    proceeding with an incomplete input; the operation_runs row is marked failed.
    """


class ChatLinkPostError(CloseError):
    """The chat-link post failed permanently — all retries exhausted
    (AC-CLOSE-10-NEG). Teardown does NOT proceed; the error is surfaced and the
    operation_runs row is marked failed.
    """


# ── The final notes object (publishable form) ───────────────────────────────

class FinalActionItem(BaseModel):
    text: str = Field(max_length=1000)
    owner: Optional[str] = None
    due: Optional[str] = None


class FinalDecision(BaseModel):
    text: str = Field(max_length=1000)  # what was decided
    # §2.6: a forwarded decision carries what/who/when AND the moment it landed.
    # All three are OPTIONAL so a Doc-03 close object built with only ``text``
    # still validates and renders the historical bytes (backwards-compatible).
    owner: Optional[str] = None  # who owns / drove it
    when: Optional[str] = None  # when it takes effect (as said, e.g. "by Fri")
    landed_moment: Optional[str] = Field(default=None, max_length=1000)  # the moment it landed
    # A close-pass decision is DEFINITIVE — the live-tagging ``contradicts`` link
    # is resolved away here (§3.7 / AC-CLOSE-12). There is deliberately NO
    # ``contradicts`` field on the final object: an unresolved contradiction
    # cannot be represented, so it cannot leak into the permanent record.


class FinalOpenQuestion(BaseModel):
    text: str = Field(max_length=1000)


class ProxyAction(BaseModel):
    """One line of the §2.6 'what Proxy did' section — an ask handled, work
    performed, or a draft staged — EACH carrying its receipt (Law 1: grounded or
    silent). ``kind`` is a plain label ('ask' | 'work' | 'draft'); ``receipt`` is
    the grounding (a ``file:line`` citation, an evidence pointer, a draft handle)
    that lets a forwarded reader trust the claim rather than take it on faith.
    """

    kind: str = Field(max_length=32)  # 'ask' | 'work' | 'draft' (a human label)
    text: str = Field(max_length=1000)  # what Proxy did
    receipt: str = Field(max_length=2000)  # the grounding for this item (never empty)


class FinalNotes(BaseModel):
    """The close pass's structured output — deduped, conflicts resolved,
    confidence-weighted, human-polished, in publishable form (§3.7).

    This is the Pydantic model the ``generateStructured`` result is re-validated
    against (``outputFormat:{type:'json_schema'}`` → ``model_validate``,
    AC-CLOSE-06). It carries NO ``contradicts`` links (AC-CLOSE-12): the final
    record is definitive by construction.
    """

    # §2.6 header — title/date/attendees. All OPTIONAL so a Doc-03 close object
    # built with only ``summary`` renders the historical "# Meeting notes" bytes.
    title: Optional[str] = Field(default=None, max_length=300)
    meeting_date: Optional[str] = Field(default=None, max_length=64)
    attendees: list[str] = Field(default_factory=list)

    # §2.6 worst-news-first: any live blocker/risk LEADS the five-line summary,
    # rendered ABOVE the happy summary line (never buried below the decisions).
    blockers_risks: list[str] = Field(default_factory=list)

    summary: str = Field(max_length=20_000)
    decisions: list[FinalDecision] = Field(default_factory=list)
    action_items: list[FinalActionItem] = Field(default_factory=list)
    open_questions: list[FinalOpenQuestion] = Field(default_factory=list)

    # §2.6 'what Proxy did' — each ask/work/draft with its receipt (Law 1) — and a
    # pointer to the raw transcript. Both OPTIONAL/empty by default so the Doc-03
    # render (which has neither) is byte-identical.
    proxy_actions: list[ProxyAction] = Field(default_factory=list)
    transcript_ref: Optional[str] = Field(default=None, max_length=2000)

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        """The JSON Schema handed to ``outputFormat={'type':'json_schema', ...}``.

        Derived from THIS model so the wire schema and the re-validation model can
        never drift (the same single source of truth the Scribe uses, §3.2).
        """
        return cls.model_json_schema()


def _decision_suffix(d: FinalDecision) -> str:
    """The who/when/moment-it-landed suffix on a §2.6 decision line.

    Each part is emitted ONLY when present, in a fixed order, so the render is
    deterministic and a Doc-03 decision (``text`` only) renders as the historical
    bare ``- <text>`` line — no fabricated who/when.
    """
    parts: list[str] = []
    if d.owner:
        parts.append(f"— {d.owner.strip()}")
    if d.when:
        parts.append(f"({d.when.strip()})")
    if d.landed_moment:
        parts.append(f"— {d.landed_moment.strip()}")
    return (" " + " ".join(parts)) if parts else ""


def render_markdown(notes: FinalNotes) -> str:
    """Render the final object through the §2.6 notes template to markdown (Step 1).

    The designed artifact forwarded to the VP (§2.6): title/date/ATTENDEES → a
    five-line summary with any live blocker/risk LEADING (worst news first) →
    decisions (what/who/when + the moment it landed) → action items (who/what/when)
    → open questions → a 'what Proxy did' section (each ask/work/draft with its
    receipt, Law 1) → a pointer to the raw transcript.

    DETERMINISTIC — no model call, no clock, no set/dict-order dependence: the same
    ``FinalNotes`` renders byte-identical bytes every time. This is the exact bytes
    written to GCS. Every §2.6 addition is emitted ONLY when its field is populated,
    so a Doc-03 close object (summary/decisions/action-items/open-questions only)
    renders the historical bytes unchanged — one renderer, single source.
    """
    # ── Header: title/date/attendees (§2.6) ────────────────────────────────────
    title = (notes.title or "Meeting notes").strip()
    lines: list[str] = [f"# {title}", ""]
    if notes.meeting_date:
        lines += [f"**Date:** {notes.meeting_date.strip()}", ""]
    if notes.attendees:
        lines += ["## Attendees", ""]
        lines += [f"- {name.strip()}" for name in notes.attendees]
        lines += [""]

    # ── Summary — worst news FIRST: blockers/risks lead the happy summary ───────
    lines += ["## Summary", ""]
    if notes.blockers_risks:
        # A live blocker/risk leads the five-line summary so a forwarded reader
        # sees what's at stake before what went well (§2.6). Rendered ABOVE the
        # happy summary line, never buried below the decisions.
        for risk in notes.blockers_risks:
            lines.append(f"- ⚠️ {risk.strip()}")
        lines.append("")
    lines += [notes.summary.strip(), ""]

    # ── Decisions — what/who/when + the moment it landed (§2.6) ─────────────────
    lines += ["## Decisions", ""]
    if notes.decisions:
        lines += [f"- {d.text.strip()}{_decision_suffix(d)}" for d in notes.decisions]
    else:
        lines += ["_None recorded._"]

    # ── Action items — who/what/when ───────────────────────────────────────────
    lines += ["", "## Action items", ""]
    if notes.action_items:
        for a in notes.action_items:
            who = f" — {a.owner}" if a.owner else ""
            when = f" ({a.due})" if a.due else ""
            lines += [f"- [ ] {a.text.strip()}{who}{when}"]
    else:
        lines += ["_None recorded._"]

    # ── Open questions ─────────────────────────────────────────────────────────
    lines += ["", "## Open questions", ""]
    if notes.open_questions:
        lines += [f"- {q.text.strip()}" for q in notes.open_questions]
    else:
        lines += ["_None._"]

    # ── What Proxy did — each ask/work/draft WITH its receipt (§2.6 / Law 1) ────
    # Emitted only when there is at least one action, so the Doc-03 render (no
    # actions) stays byte-identical. Every item carries its receipt — grounded or
    # silent: an action without a receipt is never fabricated here.
    if notes.proxy_actions:
        lines += ["", "## What Proxy did", ""]
        for act in notes.proxy_actions:
            label = act.kind.strip()
            prefix = f"**{label}** " if label else ""
            lines += [f"- {prefix}{act.text.strip()}", f"  - receipt: {act.receipt.strip()}"]

    # ── Raw-transcript pointer (§2.6) ──────────────────────────────────────────
    if notes.transcript_ref:
        lines += ["", "## Raw transcript", "", f"- {notes.transcript_ref.strip()}"]

    lines += [""]
    return "\n".join(lines)


def _entry_str(payload: dict[str, Any], key: str) -> Optional[str]:
    """A trimmed non-empty string field off a folded entry, else None (deterministic)."""
    val = payload.get(key)
    if isinstance(val, str) and val.strip():
        return val.strip()
    return None


def final_notes_from_folded(folded: Any, *, transcript_ref: str | None = None) -> FinalNotes:
    """Build the §2.6 :class:`FinalNotes` DETERMINISTICALLY from the folded note_deltas.

    ``folded`` is a :class:`scribe.notes_reader.Notes` — the deterministic left-fold
    of ``note_deltas`` in ``id`` order (CANONICAL §11.4). This is a PURE projection
    of that object into the publishable §2.6 shape: no model call, no clock, no
    set/dict-iteration nondeterminism — it walks ``folded.order`` (the stable fold
    order) and classifies each entry by its ``kind``. So the same fold in yields a
    byte-identical rendered artifact out (the DoD's "deterministically, from the
    folded note_deltas object").

    Mapping (each field emitted only when the source is present, never fabricated):

    * ``current_goal`` — the one-line goal/blocker signal (§3) — leads the summary
      as the worst-news-first blocker/risk.
    * ``kind == 'decision'`` → :class:`FinalDecision`: ``text``; ``owner`` from the
      "for" voters in ``leans`` (sorted, deterministic); ``landed_moment`` from the
      close ``resolution`` when the decision resolved (the moment it landed).
    * ``kind == 'action'`` → :class:`FinalActionItem` (text/owner/due).
    * ``kind == 'open_question'`` → :class:`FinalOpenQuestion` (unresolved ones).

    The 'what Proxy did' section and the transcript pointer are NOT in ``note_deltas``
    (they ride task telemetry + the persisted draft rows + the transcript ref, §2.6):
    the caller passes ``proxy_actions`` in separately (via :class:`FinalNotes`) and
    ``transcript_ref`` here. This function fills only what the fold can ground.
    """
    entries: dict[str, dict[str, Any]] = getattr(folded, "entries", {})
    order: list[str] = getattr(folded, "order", [])
    current_goal: Optional[str] = getattr(folded, "current_goal", None)

    blockers_risks: list[str] = []
    if isinstance(current_goal, str) and current_goal.strip():
        blockers_risks.append(current_goal.strip())

    decisions: list[FinalDecision] = []
    action_items: list[FinalActionItem] = []
    open_questions: list[FinalOpenQuestion] = []
    summary_bits: list[str] = []

    for eid in order:
        payload = entries.get(eid, {})
        kind = str(payload.get("kind", "")).strip()
        text = _entry_str(payload, "text")
        if text is None:
            continue
        if kind == "decision":
            leans = payload.get("leans")
            owner: Optional[str] = None
            if isinstance(leans, dict):
                fors = sorted(k for k, v in leans.items() if v == "for" and isinstance(k, str))
                if fors:
                    owner = ", ".join(fors)
            landed = _entry_str(payload, "resolution") if payload.get("resolved") else None
            decisions.append(
                FinalDecision(text=text, owner=owner, landed_moment=landed)
            )
        elif kind == "action":
            action_items.append(
                FinalActionItem(
                    text=text,
                    owner=_entry_str(payload, "owner"),
                    due=_entry_str(payload, "due"),
                )
            )
        elif kind == "open_question":
            if payload.get("resolved") is not True:
                open_questions.append(FinalOpenQuestion(text=text))
        elif kind == "claim":
            # A claim's gist rides the summary body (deterministic fold order).
            summary_bits.append(text)

    summary = " ".join(summary_bits) if summary_bits else "See decisions and action items below."
    return FinalNotes(
        blockers_risks=blockers_risks,
        summary=summary,
        decisions=decisions,
        action_items=action_items,
        open_questions=open_questions,
        transcript_ref=transcript_ref,
    )


# ── Model tier guard (fail-fast, before any SDK call) ───────────────────────

def resolve_close_model(model_for: Callable[[str], str] | None = None) -> str:
    """Resolve PROXY_MODEL_SCRIBE_CLOSE via the ONE seat table (§3.7 / AC-CLOSE-03).

    ``model_for`` is injected so the seat table is exercised for real in tests
    without a hard dependency wiring here; it defaults to the canonical
    ``libs.llm.routing.model_for`` (imported lazily so this module stays
    importable even where ``libs.llm`` is not on the path).
    """
    if model_for is None:
        from libs.llm.src.llm.routing import model_for as _model_for  # lazy
        model_for = _model_for
    return model_for(CLOSE_SEAT)


def assert_not_haiku(model: str) -> str:
    """Fail-fast if the close seat resolves to a Haiku-class model (AC-CLOSE-03-NEG).

    The close pass is Sonnet-class by definition. A Haiku-class model id is
    rejected with :class:`HaikuModelRejectedError` BEFORE the model is invoked —
    the pass never proceeds with Haiku (AC-CLOSE-03).
    """
    if "haiku" in model.lower():
        raise HaikuModelRejectedError(
            f"PROXY_MODEL_SCRIBE_CLOSE resolved to a Haiku-class model {model!r}; "
            "the close pass requires a Sonnet-class (strong) model"
        )
    return model


# ── Chunk-reduce threshold decision (pure arithmetic) ───────────────────────

def approx_tokens(text: str) -> int:
    """Coarse token estimate (~4 chars/token) — enough for the chunk gate."""
    return len(text) // _CHARS_PER_TOKEN


def should_chunk_reduce(
    folded_ledger: str, *, threshold: int = CHUNK_REDUCE_TOKEN_THRESHOLD
) -> bool:
    """True IFF the folded ledger exceeds the token threshold (§3.7 / AC-CLOSE-05).

    A normal meeting folds in ONE pass (below threshold → single model call, no
    map-reduce loop). The mapping is pure arithmetic on the ledger's token count
    — never a hard-coded per-meeting rule (Law 4).
    """
    return approx_tokens(folded_ledger) > threshold


def chunk_folded_ledger(
    folded_ledger: str, *, threshold: int = CHUNK_REDUCE_TOKEN_THRESHOLD
) -> list[str]:
    """Split an over-threshold folded ledger into sub-threshold map chunks (§3.7).

    Each chunk is at most ``threshold`` tokens (~``threshold * _CHARS_PER_TOKEN``
    chars), so every map call stays under the model's context budget. Chunk
    boundaries prefer line breaks so a ledger entry is not split mid-line; an
    over-long single line is still hard-split so no chunk ever exceeds the budget.
    A below-threshold ledger returns a single chunk (the caller then makes ONE
    map call, i.e. AC-CLOSE-05's single-pass). Pure arithmetic — no model call.
    """
    max_chars = threshold * _CHARS_PER_TOKEN
    if len(folded_ledger) <= max_chars:
        return [folded_ledger]
    chunks: list[str] = []
    current = ""
    for line in folded_ledger.splitlines(keepends=True):
        while len(line) > max_chars:
            # A single over-long line cannot fit any chunk — hard-split it so no
            # chunk exceeds the budget (still deterministic, still no model call).
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:max_chars])
            line = line[max_chars:]
        if len(current) + len(line) > max_chars:
            chunks.append(current)
            current = line
        else:
            current += line
    if current:
        chunks.append(current)
    return chunks


# ── Input composition (folded ledger + gap/pending ONLY) ────────────────────

@dataclass(frozen=True)
class GapPendingSpan:
    """One raw-transcript span backfilled at close — only ``gap``/``pending``."""

    segment_id: str
    text: str
    status: str  # 'gap' | 'pending'


@dataclass(frozen=True)
class CloseInput:
    """The exact input the close pass reduces over (§3.7).

    ``folded_ledger`` is the PRIMARY input (the already-comprehended object).
    ``gap_pending_spans`` is the raw-transcript backfill for the spans no live
    window comprehended — and is the ONLY raw transcript included. Comprehended
    segments' raw text is NEVER carried here (AC-CLOSE-04).
    """

    folded_ledger: str
    gap_pending_spans: tuple[GapPendingSpan, ...]
    # On the over-threshold reduce step the ``folded_ledger`` is NOT a raw comprehension
    # ledger — it is ``render_markdown`` of the already-merged per-chunk partials, i.e.
    # sections that are ALREADY resolved (deduped/carried, incl. header, blockers_risks,
    # proxy_actions, decision provenance). This flag tells the reduce prompt to dedup and
    # re-summarise those resolved sections rather than re-derive them from scratch (§3.7).
    resolved_sections: bool = False

    def to_prompt(self) -> str:
        """Assemble the model prompt: folded ledger block + gap/pending spans.

        Contains the folded-ledger block and EXACTLY the gap+pending raw spans;
        no comprehended-segment raw text ever appears (AC-CLOSE-04 oracle). On the
        reduce step (``resolved_sections``) the block is announced as already-resolved
        sections so the model consolidates rather than re-derives (§3.7 chunk-reduce).
        """
        ledger_tag = "resolved_sections" if self.resolved_sections else "folded_ledger"
        parts: list[str] = []
        if self.resolved_sections:
            parts.append(
                "The following are ALREADY-RESOLVED notes sections merged from the "
                "meeting's chunks (header, blockers/risks, decisions with owner/when, "
                "action items, open questions, and what Proxy did — each with its "
                "receipt). Consolidate and de-duplicate them into ONE final record; "
                "do not drop any section, and do not re-derive facts already present."
            )
        parts += [f"<{ledger_tag}>", self.folded_ledger, f"</{ledger_tag}>", ""]
        parts += ["<gap_pending_backfill>"]
        for s in self.gap_pending_spans:
            parts.append(f"[{s.status} {s.segment_id}] {s.text}")
        parts += ["</gap_pending_backfill>"]
        return "\n".join(parts)


async def fetch_gap_pending_spans(conn: Any, meeting_id: Any) -> tuple[GapPendingSpan, ...]:
    """Read raw transcript for ONLY ``status IN ('gap','pending')`` (§3.7 / AC-CLOSE-04).

    Meeting-scoped by construction (tenant isolation). Comprehended segments are
    NOT selected — their raw text never enters the model call. A connectivity
    failure is surfaced as :class:`DatabaseConnectionError` (AC-CLOSE-04-NEG):
    the close pass never silently skips the backfill and proceeds with an
    incomplete input.

    ``conn`` is a borrowed asyncpg connection (the caller owns the transaction
    boundary, exactly like the sibling ``db.repos`` functions). This module owns
    THIS read — it does not mutate the shared repo module.
    """
    try:
        rows = await conn.fetch(
            """
            SELECT id, text, status
              FROM transcript_segments
             WHERE meeting_id = $1
               AND status IN ('gap', 'pending')
             ORDER BY id
            """,
            meeting_id,
        )
    except Exception as exc:  # asyncpg/OS connectivity errors — surface, never skip
        raise DatabaseConnectionError(
            f"could not read gap/pending spans for meeting {meeting_id}: {exc}"
        ) from exc
    return tuple(
        GapPendingSpan(segment_id=str(r["id"]), text=r["text"], status=r["status"])
        for r in rows
    )


async def connect_and_fetch_gap_pending_spans(
    dsn: str, meeting_id: Any, *, timeout: float = 5.0
) -> tuple[GapPendingSpan, ...]:
    """Open a real Postgres connection and read the gap/pending backfill (§3.7).

    The DB-path wiring for :func:`run_close_pass`'s ``fetch_backfill``: it opens a
    real asyncpg connection to ``dsn`` and delegates the read to
    :func:`fetch_gap_pending_spans`, closing the connection afterward. A failure
    to CONNECT (unreachable host/port, refused connection) is surfaced as
    :class:`DatabaseConnectionError` exactly like a failed read — the close pass
    never silently skips the backfill and proceeds with an incomplete input
    (AC-CLOSE-04-NEG). ``asyncpg`` is imported lazily so this module stays
    importable on a host without it.
    """
    import asyncpg  # lazy: dev dependency, not needed to import this module

    try:
        conn = await asyncpg.connect(dsn, timeout=timeout)
    except Exception as exc:  # connection refused / unreachable — surface, never skip
        raise DatabaseConnectionError(
            f"could not connect to Postgres for meeting {meeting_id} backfill: {exc}"
        ) from exc
    try:
        return await fetch_gap_pending_spans(conn, meeting_id)
    finally:
        await conn.close()


# ── The ordered close sequence trace (render → GCS → chat → teardown) ────────

class CloseStep(str, Enum):
    RENDER = "render"
    GCS_WRITE = "gcs_write"
    CHAT_LINK = "chat_link"
    TEARDOWN = "teardown"


@dataclass
class CloseEvent:
    step: CloseStep
    ts: float
    detail: str = ""


@dataclass
class CloseTrace:
    """Ordered event log — the oracle for the mandatory sequence (AC-CLOSE-09).

    Every world-touching step records a monotonic-time confirm event. The
    assertions read that render < gcs_write < chat_link < teardown, and that no
    teardown event precedes or coincides with the chat-link confirm.
    """

    events: list[CloseEvent] = field(default_factory=list)

    def record(self, step: CloseStep, detail: str = "") -> CloseEvent:
        ev = CloseEvent(step=step, ts=time.monotonic(), detail=detail)
        self.events.append(ev)
        return ev

    def steps(self) -> list[CloseStep]:
        return [e.step for e in self.events]

    def has(self, step: CloseStep) -> bool:
        return any(e.step is step for e in self.events)


# ── Injected boundaries (the mock-boundary seams) ───────────────────────────

@dataclass(frozen=True)
class StructuredResult:
    """The shape the close pass reads off a ``generateStructured`` result.

    ``data`` is the raw structured payload (re-validated into :class:`FinalNotes`
    by :func:`generate_structured_close`). ``total_cost_usd`` is read straight off
    the result — never recomputed from token arithmetic (§3.9 / AC-CLOSE-11).
    """

    data: dict[str, Any]
    total_cost_usd: float | None


class StructuredCaller(Protocol):
    """Structural type of the Python ``claude_agent_sdk`` structured-output call.

    The injected concrete implementation performs the real ``generateStructured``
    round-trip through the ``call_external`` seam. Tests drive a caller that
    honours THIS contract against a recorded body (a cassette) — they MUST NOT
    replace the ``call_external`` seam or request construction with ``Mock()``.
    It MAY raise the SDK terminal subtypes; :func:`generate_structured_close` maps
    those to the typed close errors.
    """

    async def __call__(
        self, *, model: str, prompt: str, output_schema: dict[str, Any]
    ) -> StructuredResult: ...


class ChatPoster(Protocol):
    """Structural type of the meeting-chat link poster (vendor:recall, §3.7)."""

    async def __call__(self, url: str) -> None: ...


class OperationRunSink(Protocol):
    """The single ``operation_runs`` row for this close pass (AC-CLOSE-07).

    A null/mockable boundary (the criterion's mock_boundary): the unit tier drives
    an in-memory sink that records the SAME row shape the real DB-backed sink
    writes (operation_type='meeting-close', status, error_type, total_cost_usd).
    The integration tier supplies a real Postgres-backed sink. There is NO
    close_jobs table — this is the only close-tracking record.
    """

    async def start(self, meeting_id: Any) -> None: ...
    async def mark_succeeded(self, meeting_id: Any, *, total_cost_usd: float | None) -> None: ...
    async def mark_failed(self, meeting_id: Any, *, error_type: str) -> None: ...


@dataclass
class InMemoryOperationRunSink:
    """A null-boundary ``operation_runs`` sink for the unit tier (AC-CLOSE-07).

    Records exactly one row's worth of state per meeting — start → succeeded |
    failed — so the unit tier can assert the row shape without a live DB, while
    the SAME calls hit a Postgres-backed sink at the integration tier. It records
    at most ONE terminal state per meeting (a second start for a meeting that
    already has a row is rejected, mirroring the crash-recovery duplicate guard).
    """

    rows: dict[str, dict[str, Any]] = field(default_factory=dict)

    async def start(self, meeting_id: Any) -> None:
        key = str(meeting_id)
        if key in self.rows:
            raise RuntimeError(
                f"operation_runs row for meeting {key} already exists — "
                "a second meeting-close is not started (AC-CLOSE-07-NEG)"
            )
        self.rows[key] = {
            "operation_type": OPERATION_TYPE,
            "status": "running",
            "error_type": None,
            "total_cost_usd": None,
        }

    async def mark_succeeded(self, meeting_id: Any, *, total_cost_usd: float | None) -> None:
        self.rows[str(meeting_id)].update(status="succeeded", total_cost_usd=total_cost_usd)

    async def mark_failed(self, meeting_id: Any, *, error_type: str) -> None:
        self.rows[str(meeting_id)].update(status="failed", error_type=error_type)


# ── The structured close call (generateStructured → Pydantic re-validate) ────

# Approximate Sonnet-class per-token USD prices, applied at the SDK-adapter boundary.
# The raw Messages API returns token usage, not a dollar figure (the Agent SDK computes
# it internally); this adapter fills StructuredResult.total_cost_usd from REAL usage so
# the close layer READS cost off the result and never does token arithmetic (AC-CLOSE-11).
_CLOSE_MAX_TOKENS = 8192
_USD_PER_INPUT_TOKEN = 3.0 / 1_000_000
_USD_PER_OUTPUT_TOKEN = 15.0 / 1_000_000


def _lazy_anthropic_client(**kwargs: Any) -> Any:
    """Construct the raw Anthropic client via the single libs.http construction site.

    Deferred import so the pure ordering/composition logic stays importable on a host
    without the vendor SDK; the reality tier drives real construction through this path.
    """
    from libs.http.src.http.external import anthropic_client  # deferred: vendor SDK only here

    return anthropic_client(**kwargs)


def anthropic_structured_caller(client: Any | None = None) -> StructuredCaller:
    """The concrete ``generateStructured`` surface, realized NATIVELY on the Anthropic
    Messages API: a forced-tool call whose ``input_schema`` IS the ``output_schema``, so
    the returned ``tool_use`` input is the structured payload — exactly the
    ``outputFormat:{type:'json_schema'}`` semantics (AC-CLOSE-06). The ``claude_agent_sdk``
    wrapper named in §3.9 was deferred ("a design doc cannot pin its surface"); this is the
    dependency-light, vcr-recordable realization the close pass actually uses. It makes ONE
    round-trip and returns a :class:`StructuredResult` (data + real cost) — the retry + cost
    seam is applied ONE level up in :func:`generate_structured_close`.
    """

    async def _call(*, model: str, prompt: str, output_schema: dict[str, Any]) -> StructuredResult:
        c = client if client is not None else _lazy_anthropic_client()
        tool = {
            "name": "emit_final_notes",
            "description": "Emit the finalized meeting notes as ONE structured object.",
            "input_schema": output_schema,
        }
        resp = await c.messages.create(
            model=model,
            max_tokens=_CLOSE_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
            tools=[tool],
            tool_choice={"type": "tool", "name": "emit_final_notes"},  # force json_schema output
        )
        block = next(
            (
                b for b in resp.content
                if getattr(b, "type", None) == "tool_use" and getattr(b, "name", None) == "emit_final_notes"
            ),
            None,
        )
        if block is None:  # generate_structured_close wraps this as CloseVendorError
            raise ValueError("close generateStructured returned no emit_final_notes tool_use block")
        usage = getattr(resp, "usage", None)
        cost: float | None = None
        if usage is not None:
            cost = (getattr(usage, "input_tokens", 0) or 0) * _USD_PER_INPUT_TOKEN + (
                getattr(usage, "output_tokens", 0) or 0
            ) * _USD_PER_OUTPUT_TOKEN
        return StructuredResult(data=dict(block.input), total_cost_usd=cost)

    return _call


async def generate_structured_close(
    close_input: CloseInput,
    *,
    model: str,
    caller: StructuredCaller,
    call_external: CallExternal,
) -> tuple[FinalNotes, float | None]:
    """Issue the ONE ``generateStructured`` call and re-validate into FinalNotes.

    Runs the injected ``caller`` through the real ``call_external`` seam (retry +
    cost telemetry live in the seam, §14) with ``outputFormat`` derived from
    :meth:`FinalNotes.json_schema` (AC-CLOSE-06). The result is passed through
    Pydantic ``model_validate`` before use. Terminal SDK subtypes are caught as
    their OWN typed errors — ``error_max_turns`` and
    ``error_max_structured_output_retries`` in DISTINCT branches, never collapsed
    into a generic ``Exception`` first (AC-CLOSE-06-NEG / -06B-NEG). Any other
    vendor fault or a body that fails re-validation surfaces as
    :class:`CloseVendorError` — honest degradation, no corruption
    (AC-CLOSE-01-NEG / -12-NEG). Returns ``(FinalNotes, total_cost_usd)``.
    """
    prompt = close_input.to_prompt()
    schema = FinalNotes.json_schema()

    async def _op() -> StructuredResult:
        return await caller(model=model, prompt=prompt, output_schema=schema)

    try:
        outcome = await call_external(_op, service=_SERVICE)
    except CloseMaxTurnsError:
        # DISTINCT typed branch — recorded as error_max_turns (AC-CLOSE-06-NEG).
        raise
    except CloseMaxStructuredRetriesError:
        # DISTINCT typed branch — recorded as its own subtype (AC-CLOSE-06B-NEG).
        raise
    except CloseError:
        raise
    except Exception as exc:  # 5xx / timeout / transport — honest surface, no proceed
        raise CloseVendorError(f"close generateStructured failed: {exc}") from exc

    result: StructuredResult = getattr(outcome, "value", outcome)
    try:
        final = FinalNotes.model_validate(result.data)  # Pydantic re-validate (AC-CLOSE-06)
    except Exception as exc:  # malformed / garbage body — no corruption
        raise CloseVendorError(
            f"close result failed schema re-validation: {exc}"
        ) from exc
    return final, result.total_cost_usd


# ── The chunk-reduce map-reduce (one pass under threshold, N+1 over it) ───────

@dataclass
class ReduceResult:
    """The outcome of reducing the folded ledger into ONE final notes object.

    ``final_notes`` is always a SINGLE unified object regardless of how many map
    calls ran. ``model_call_count`` is the real number of ``generateStructured``
    calls made: exactly 1 below the token threshold (AC-CLOSE-05), and
    ``len(chunks) + 1`` above it — the per-chunk map calls plus the one merge
    reduce (AC-CLOSE-05B, ``model_call_count > 1``). ``total_cost_usd`` sums the
    per-call SDK-reported costs (None if every call reported None), never
    recomputed from token arithmetic (§3.9 / AC-CLOSE-11).
    """

    final_notes: FinalNotes
    model_call_count: int
    total_cost_usd: float | None


def _first_present(values: Iterable[Optional[str]]) -> Optional[str]:
    """The first non-empty value across the partials (header fields don't concatenate)."""
    for v in values:
        if v is not None and str(v).strip():
            return v
    return None


def _dedupe_preserve_order(values: Iterable[str]) -> list[str]:
    """De-duplicate a flat string list while preserving first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        key = v.strip()
        if key and key not in seen:
            seen.add(key)
            out.append(v)
    return out


def _merge_final_notes(partials: list[FinalNotes]) -> FinalNotes:
    """Deterministically fold per-chunk partials into ONE object before the reduce.

    Concatenates the section lists and joins the chunk summaries, and — critically —
    carries **every** FinalNotes field through, not just summary/decisions/actions/
    questions: the §2.6 header (title/date/attendees), the worst-news-first
    ``blockers_risks``, ``proxy_actions``, and ``transcript_ref`` (§2.6 / AC-CLOSE-12).
    Decision/action/question provenance rides intact because the ``FinalDecision`` /
    ``FinalActionItem`` / ``FinalOpenQuestion`` objects are carried WHOLE (owner, when,
    landed_moment, receipts) — never flattened to bare text. Dropping any of these on
    the over-threshold reduce path would silently lose a live blocker or a Proxy-action
    receipt from a large meeting's final notes; carrying them means the reduce prompt
    (fed ``render_markdown(merged)``) sees the already-resolved sections and only
    dedups/re-summarises, never re-derives them from scratch. This is the map-side
    accumulation; the final reduce model call (below) folds the merged whole into the
    definitive record. No model call here.
    """
    return FinalNotes(
        # §2.6 header — first present wins (a header is a single meeting fact, not a
        # per-chunk list); a chunk that never saw the header contributes None.
        title=_first_present(p.title for p in partials),
        meeting_date=_first_present(p.meeting_date for p in partials),
        attendees=_dedupe_preserve_order(a for p in partials for a in p.attendees),
        # §2.6 worst-news-first — carried, deduped, so a blocker in ANY chunk survives.
        blockers_risks=_dedupe_preserve_order(
            b for p in partials for b in p.blockers_risks
        ),
        summary="\n\n".join(p.summary for p in partials if p.summary.strip()),
        decisions=[d for p in partials for d in p.decisions],
        action_items=[a for p in partials for a in p.action_items],
        open_questions=[q for p in partials for q in p.open_questions],
        # §2.6 'what Proxy did' + raw-transcript pointer — carried WHOLE (each action
        # keeps its Law-1 receipt); transcript_ref takes the first present pointer.
        proxy_actions=[act for p in partials for act in p.proxy_actions],
        transcript_ref=_first_present(p.transcript_ref for p in partials),
    )


def _sum_costs(costs: list[float | None]) -> float | None:
    seen = [c for c in costs if c is not None]
    return sum(seen) if seen else None


async def reduce_close(
    close_input: CloseInput,
    *,
    model: str,
    caller: StructuredCaller,
    call_external: CallExternal,
    threshold: int = CHUNK_REDUCE_TOKEN_THRESHOLD,
) -> ReduceResult:
    """Reduce the folded ledger + gap/pending backfill into ONE FinalNotes (§3.7).

    Below the token threshold this makes EXACTLY ONE ``generateStructured`` call
    over the whole input and returns its result verbatim — a normal meeting folds
    in one pass, no map-reduce loop (AC-CLOSE-05, ``model_call_count == 1``).

    Above the threshold it enters the real chunk-reduce path (AC-CLOSE-05B): the
    folded ledger is split into sub-threshold chunks; each chunk is mapped through
    its OWN ``generateStructured`` call (MORE THAN ONE model call), the partials
    are folded, and ONE final reduce call merges them — carrying the gap/pending
    backfill — into a SINGLE unified FinalNotes. ``model_call_count`` is
    ``len(chunks) + 1 > 1``; the output is one merged object, not a list.

    Each call goes through :func:`generate_structured_close`, so the same real
    ``call_external`` seam, Pydantic re-validation, and typed terminal-error
    surfacing apply to every map AND the reduce (AC-CLOSE-06/-06-NEG).
    """
    if not should_chunk_reduce(close_input.folded_ledger, threshold=threshold):
        final, cost = await generate_structured_close(
            close_input, model=model, caller=caller, call_external=call_external
        )
        return ReduceResult(final_notes=final, model_call_count=1, total_cost_usd=cost)

    # ── Map: one generateStructured call PER chunk (> 1 model call) ───────────
    chunks = chunk_folded_ledger(close_input.folded_ledger, threshold=threshold)
    partials: list[FinalNotes] = []
    costs: list[float | None] = []
    for chunk in chunks:
        # Map inputs carry NO gap/pending backfill — the raw spans are reduced in
        # exactly once at the merge step, never duplicated across every chunk.
        map_input = CloseInput(folded_ledger=chunk, gap_pending_spans=())
        part, cost = await generate_structured_close(
            map_input, model=model, caller=caller, call_external=call_external
        )
        partials.append(part)
        costs.append(cost)

    # ── Reduce: ONE merge call folds the partials + backfill into one object ──
    merged = _merge_final_notes(partials)
    reduce_input = CloseInput(
        folded_ledger=render_markdown(merged),
        gap_pending_spans=close_input.gap_pending_spans,
        resolved_sections=True,  # the merged block is already-resolved sections (§3.7)
    )
    final, reduce_cost = await generate_structured_close(
        reduce_input, model=model, caller=caller, call_external=call_external
    )
    costs.append(reduce_cost)

    return ReduceResult(
        final_notes=final,  # a SINGLE merged object, not a list
        model_call_count=len(chunks) + 1,  # per-chunk maps + one reduce (> 1)
        total_cost_usd=_sum_costs(costs),
    )


# ── The orchestrated close pass (render → GCS → chat → teardown) ─────────────

@dataclass
class CloseResult:
    meeting_id: str
    notes_url: str
    generation: int
    total_cost_usd: float | None
    trace: CloseTrace
    final_notes: FinalNotes


def _notes_url(bucket_name: str, meeting_id: Any) -> str:
    from .notes_artifact import NOTES_OBJECT_TEMPLATE
    key = NOTES_OBJECT_TEMPLATE.format(meeting_id=meeting_id)
    return f"gs://{bucket_name}/{key}"


async def run_close_pass(
    meeting_id: Any,
    final_notes: FinalNotes,
    total_cost_usd: float | None,
    *,
    bucket: Any,
    bucket_name: str,
    post_chat_link: ChatPoster,
    teardown: Callable[[], Awaitable[None]],
    op_sink: OperationRunSink,
    fetch_backfill: Callable[[], Awaitable[Any]] | None = None,
    already_started: bool = False,
    chat_post_max_attempts: int = CHAT_POST_MAX_ATTEMPTS,
) -> CloseResult:
    """The V0 close sequence, in the MANDATORY order (§3.7 / AC-CLOSE-09).

    Step 0 gap/pending backfill read → Step 1 render → Step 2 GCS create-only
    write (``if_generation_match=0``) → Step 3 chat-link post (retried) → Step 4
    teardown. Each step's confirm is recorded on the :class:`CloseTrace`, and NO
    later step runs until the prior step confirms:

    * If the gap/pending backfill read cannot reach Postgres, the close pass does
      NOT silently skip it and proceed with an incomplete input: the
      :class:`DatabaseConnectionError` is surfaced and the operation_runs row is
      marked failed, not succeeded — no render, no GCS write, no chat post, no
      teardown (AC-CLOSE-04-NEG). ``fetch_backfill`` is the injected read (the DB
      path passes :func:`fetch_gap_pending_spans` bound to a live connection);
      when omitted, the backfill was performed by the caller and this step is a
      no-op.
    * If the GCS write fails, Step 3 and Step 4 do NOT proceed; the error is
      surfaced and the operation_runs row is marked failed (AC-CLOSE-08-NEG).
    * A second close pass (crash recovery) hits ``if_generation_match=0`` →
      :class:`NotesGenerationConflictError`; the existing object is NOT
      overwritten and the existing URL is reused to post the link if not yet
      posted (AC-CLOSE-07-NEG / -14).
    * The chat-link post is retried on transient failure; teardown never runs
      between attempts, and a permanent failure blocks teardown and surfaces
      :class:`ChatLinkPostError` (AC-CLOSE-10 / -10-NEG).
    * total_cost_usd is recorded from the SDK result on the operation_runs row —
      never recomputed (AC-CLOSE-11).

    ``op_sink`` owns the single operation_runs row; ``already_started`` lets the
    caller pass a sink whose row is already open (the DB path) so this function
    does not double-start it.
    """
    trace = CloseTrace()
    if not already_started:
        await op_sink.start(meeting_id)

    # ── Step 0: gap/pending backfill read — never silently skipped ──────────
    # If the read cannot reach Postgres the close pass surfaces the failure and
    # marks the row failed rather than proceeding with an incomplete input
    # (AC-CLOSE-04-NEG). Nothing world-touching (render/GCS/chat/teardown) runs.
    if fetch_backfill is not None:
        try:
            await fetch_backfill()
        except DatabaseConnectionError:
            await op_sink.mark_failed(meeting_id, error_type="db_backfill_failed")
            raise

    # ── Step 1: render (deterministic, no model call) ───────────────────────
    markdown = render_markdown(final_notes)
    trace.record(CloseStep.RENDER, "rendered markdown")

    # ── Step 2: GCS write — create-only (if_generation_match=0), EXACTLY once ─
    try:
        generation = write_finalized_notes(
            bucket, str(meeting_id), markdown, if_generation_match=0
        )
    except NotesGenerationConflictError:
        # Crash-recovery: the finalized object already exists. Do NOT overwrite
        # (no second GCS object). Reuse the existing URL to post the link if the
        # earlier run crashed before posting (AC-CLOSE-14). Generation is unknown
        # here (we did not read it) — reported as 0 and the URL is reused.
        generation = 0
        trace.record(CloseStep.GCS_WRITE, "existing object detected (create-only rejected)")
    except CloseError:
        await op_sink.mark_failed(meeting_id, error_type="gcs_write_failed")
        raise
    except Exception as exc:  # storage error (bucket unavailable, etc.)
        # Step 3 + Step 4 do NOT proceed (AC-CLOSE-08-NEG / -09-NEG). Honest
        # surface, operation_runs row marked failed, no chat post, no teardown.
        await op_sink.mark_failed(meeting_id, error_type="gcs_write_failed")
        raise CloseVendorError(f"GCS finalized-notes write failed: {exc}") from exc
    else:
        trace.record(CloseStep.GCS_WRITE, f"generation={generation}")

    url = _notes_url(bucket_name, meeting_id)

    # ── Step 3: post the chat link BEFORE teardown — retried, never abandoned ─
    posted = False
    last_exc: Exception | None = None
    for attempt in range(1, chat_post_max_attempts + 1):
        try:
            await post_chat_link(url)
        except Exception as exc:  # transient — retry; teardown never runs between
            last_exc = exc
            continue
        posted = True
        trace.record(CloseStep.CHAT_LINK, f"posted (attempt {attempt})")
        break

    if not posted:
        # Permanent post failure: teardown is BLOCKED, error surfaced, row failed
        # (AC-CLOSE-10-NEG). A human operator observes the blocked teardown.
        await op_sink.mark_failed(meeting_id, error_type="chat_link_post_failed")
        raise ChatLinkPostError(
            f"chat-link post for meeting {meeting_id} failed after "
            f"{chat_post_max_attempts} attempts: {last_exc}"
        )

    # ── Step 4: teardown — ONLY after the chat-link post confirmed success ────
    await teardown()
    trace.record(CloseStep.TEARDOWN, "teardown started")

    # The single operation_runs row records success + the SDK-reported cost.
    await op_sink.mark_succeeded(meeting_id, total_cost_usd=total_cost_usd)

    return CloseResult(
        meeting_id=str(meeting_id),
        notes_url=url,
        generation=generation,
        total_cost_usd=total_cost_usd,
        trace=trace,
        final_notes=final_notes,
    )


__all__ = [
    "CLOSE_SEAT",
    "OPERATION_TYPE",
    "CHUNK_REDUCE_TOKEN_THRESHOLD",
    "CHAT_POST_MAX_ATTEMPTS",
    "CloseError",
    "ConfigurationError",
    "HaikuModelRejectedError",
    "CloseMaxTurnsError",
    "CloseMaxStructuredRetriesError",
    "CloseVendorError",
    "DatabaseConnectionError",
    "ChatLinkPostError",
    "NotesGenerationConflictError",
    "FinalNotes",
    "FinalDecision",
    "FinalActionItem",
    "FinalOpenQuestion",
    "ProxyAction",
    "render_markdown",
    "final_notes_from_folded",
    "resolve_close_model",
    "assert_not_haiku",
    "approx_tokens",
    "should_chunk_reduce",
    "chunk_folded_ledger",
    "ReduceResult",
    "reduce_close",
    "GapPendingSpan",
    "CloseInput",
    "fetch_gap_pending_spans",
    "connect_and_fetch_gap_pending_spans",
    "CloseStep",
    "CloseEvent",
    "CloseTrace",
    "StructuredResult",
    "StructuredCaller",
    "ChatPoster",
    "OperationRunSink",
    "InMemoryOperationRunSink",
    "CallExternal",
    "anthropic_structured_caller",
    "generate_structured_close",
    "CloseResult",
    "run_close_pass",
]
