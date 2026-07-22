"""Live corrections — the trust behavior + the notes-injection gate (§3.6).

Anyone in the room can fix the record in the moment ("Proxy, we decided Friday,
not Monday" / a chat correction). The ask arrives via Doc 04 (a reactive ask) and
this layer applies it as an **immediate, attributed patch**: the entry updates,
the correction is noted (who corrected, when), and the old value is kept
**superseded-not-erased** — the record never silently rewrites history. The next
Scribe call sees the corrected notes in its prefix, because corrections land as
ordinary rows on the append-only ``note_deltas`` ledger (§3.3) and every read is
the deterministic left-fold of that ledger.

Two enforcement points beyond a plain apply live here:

* **Attribution is mandatory (§3.6 / R-doc03-CORR-02).** A correction missing the
  corrector identity or the correction timestamp is rejected with
  :class:`AttributionError` **before** any row is written — the entry is never
  silently patched with a dangling attribution.

* **The notes-injection gate (CANONICAL §10.3 / §11.11).** Most corrections apply
  silently-and-immediately — the low-friction path for the ordinary case. A narrow,
  high-stakes class does **not** apply silently: a correction that sets
  ``Decision.status = final``, sets ``Reversibility = irreversible``, or
  closes/finalizes an entry already carrying those properties writes the
  *deliverable* the meeting produces, and the transcript is untrusted input. That
  class applies (still immediate, still attributed, still superseded-not-erase)
  **and** emits a one-line spoken acknowledgement — an **audible receipt**, never a
  blocking approval prompt. Ordinary firmness bumps, action-item tweaks,
  forming-decision leans and open-question edits stay on the silent-immediate path;
  the gate must not add friction to the common case.

The apply ordering is **patch-first**: the row commits to Postgres, and only then
— and only for the high-stakes class — is the receipt spoken. A failed commit
emits no receipt and propagates no success state (no false confirm). The pipeline
never blocks waiting for a human to answer the receipt (§3.6: audible receipt, not
a modal).

Design boundary: the persist seam is ``db.repos.notes.append_delta`` (the §3.3
append-only ledger). A ``patch``/``close`` row is a *new* row; the prior
``add``/``patch`` rows for the same entry are never updated-in-place or deleted, so
the prior value stays queryable as a superseded record and the current value is the
fold. The speech seam is injected (Doc 02's mouth, owned by Doc 04's turn) — this
layer is a pipe, never the mouth: it hands the one confirm line to the caller's
speak callable and holds no delivery authority of its own.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional, Protocol, Union

from .schema import (
    CloseOp,
    DecisionStatus,
    PatchOp,
    Reversibility,
)

# The op that carries a correction: a field-level patch or a close/finalize.
CorrectionOp = Union[PatchOp, CloseOp]


class AttributionError(ValueError):
    """A correction is missing its corrector identity or its timestamp (§3.6).

    Raised **before** any row is written so the entry is never silently patched
    with a dangling attribution (R-doc03-CORR-02 / F-CORR-MISSING-ATTRIBUTION).
    A ``ValueError`` subclass so ``except ValueError`` callers still catch it.
    """


# ---------------------------------------------------------------------------
# The correction value object
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Correction:
    """One in-the-moment fix to the record, arriving via Doc 04.

    ``op`` is the field-level ``PatchOp`` (or ``CloseOp``) targeting an existing
    entry. ``corrector`` + ``corrected_at`` are the mandatory attribution — who
    corrected, when — validated at construction so an unattributed correction can
    never reach the apply path.

    ``prior_entry`` is the entry's state *before* this correction (as folded from
    the ledger), used by the gate to detect the "closes/finalizes an entry that is
    ALREADY final/irreversible" trigger — a case the ``changes`` dict alone cannot
    reveal.
    """

    target_id: str
    op: CorrectionOp
    corrector: str
    corrected_at: datetime
    prior_entry: Optional[dict[str, Any]] = None

    def __post_init__(self) -> None:
        # Attribution is mandatory — reject BEFORE any persist attempt (R-CORR-02).
        if self.corrector is None or (
            isinstance(self.corrector, str) and self.corrector.strip() == ""
        ):
            raise AttributionError("correction missing corrector identity")
        if self.corrected_at is None:
            raise AttributionError("correction missing correction timestamp")
        if not isinstance(self.corrected_at, datetime):
            raise AttributionError(
                "correction timestamp must be a datetime, "
                f"got {type(self.corrected_at).__name__}"
            )
        if self.target_id != self.op.target_id:
            raise ValueError(
                "correction target_id must match the op's target_id "
                f"({self.target_id!r} != {self.op.target_id!r})"
            )


# ---------------------------------------------------------------------------
# Classification — the notes-injection gate trigger (§3.6, narrow + high-stakes)
# ---------------------------------------------------------------------------
def _sets_decision_final(changes: dict[str, Any]) -> bool:
    """The patch sets ``Decision.status = final`` (in either the enum or its value)."""
    if "status" not in changes:
        return False
    value = changes["status"]
    return value in (DecisionStatus.final, DecisionStatus.final.value)


def _sets_irreversible(changes: dict[str, Any]) -> bool:
    """The patch sets ``Reversibility = irreversible`` (enum or its string value)."""
    if "reversibility" not in changes:
        return False
    value = changes["reversibility"]
    return value in (Reversibility.irreversible, Reversibility.irreversible.value)


def _entry_is_already_high_stakes(prior_entry: Optional[dict[str, Any]]) -> bool:
    """The entry ALREADY carries ``status=final`` or ``reversibility=irreversible``.

    Closing/finalizing such an entry is itself the gate trigger (§3.6), even when
    the correction's ``changes`` re-affirm rather than newly set the property —
    the entry's final/irreversible status is the trigger condition, so this case
    must not be double-suppressed into the silent path.
    """
    if not prior_entry:
        return False
    status = prior_entry.get("status")
    if status in (DecisionStatus.final, DecisionStatus.final.value):
        return True
    reversibility = prior_entry.get("reversibility")
    if reversibility in (Reversibility.irreversible, Reversibility.irreversible.value):
        return True
    return False


def is_high_stakes(correction: Correction) -> bool:
    """The notes-injection gate trigger — narrow, high-stakes only (§3.6).

    Fires iff the correction:

    * sets ``Decision.status = final``, OR
    * sets ``Reversibility = irreversible``, OR
    * closes/finalizes an entry that is ALREADY final/irreversible.

    Ordinary firmness bumps, action-item tweaks, forming-decision leans and
    open-question edits are NOT high-stakes and stay on the silent-immediate path
    (F-CORR-ORDINARY-GATE-FIRES: the gate must add no friction to the common case).
    """
    op = correction.op

    if isinstance(op, CloseOp):
        # A close/finalize is high-stakes only when it closes an already
        # final/irreversible entry (that status IS the trigger). Closing an open
        # question or a still-forming entry is an ordinary edit.
        return _entry_is_already_high_stakes(correction.prior_entry)

    # PatchOp: high-stakes if it newly sets final/irreversible, OR if it touches an
    # entry that is already final/irreversible (re-finalizing / re-affirming it).
    changes = op.changes
    if _sets_decision_final(changes):
        return True
    if _sets_irreversible(changes):
        return True
    if _entry_is_already_high_stakes(correction.prior_entry):
        return True
    return False


# ---------------------------------------------------------------------------
# The persist + speak seams (injected — real Postgres, real Doc 02 mouth)
# ---------------------------------------------------------------------------
class SupportsAcquire(Protocol):
    """The narrow slice of ``libs.db.Database`` this layer needs — an async
    connection acquirer. Kept as a Protocol so the real ``Database`` satisfies it
    structurally with no import-time coupling to the whole facade."""

    def acquire(self) -> Any: ...  # returns an async context manager over a conn


# The speech seam: Doc 02's mouth, owned by Doc 04's turn. Async so it composes
# with the real turn machinery; this layer only ever hands it ONE line.
SpeakFn = Callable[[str], Awaitable[None]]


def acknowledgement_line(correction: Correction) -> str:
    """The one-line spoken receipt for a high-stakes correction (§3.6).

    A light, factual receipt — "what final/irreversible fact was just written" —
    never a modal "are you sure?". Deterministic, keyed on the committed change, so
    the room hears exactly what landed and can object immediately if it is wrong.
    """
    op = correction.op
    detail: str
    if isinstance(op, CloseOp):
        detail = op.resolution
    else:
        text = op.changes.get("text")
        if isinstance(text, str) and text:
            detail = text
        elif _sets_decision_final(op.changes):
            detail = "decision final"
        elif _sets_irreversible(op.changes):
            detail = "marked irreversible"
        else:
            detail = "final record updated"
    return f"— corrected: {detail}, noted."


# ---------------------------------------------------------------------------
# The apply result — an honest, inspectable receipt of what happened
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ApplyResult:
    """What the apply path did — success only; a failure PROPAGATES, never a
    false-success object (F-CORR-FALSE-CONFIRM / F-CORR-UNCOMMITTED-STICKS).

    ``committed`` is always ``True`` on a returned result (the row is on the
    ledger). ``gate_fired`` says the notes-injection gate classified this as
    high-stakes. ``spoken`` is the exact receipt line emitted (``None`` for the
    silent-immediate path). ``delta_id`` is the ledger row id, so a caller can
    prove the patch is durable.
    """

    committed: bool
    gate_fired: bool
    spoken: Optional[str]
    delta_id: Optional[int] = None
    superseded_prior: bool = False


def _payload_for(correction: Correction) -> dict[str, Any]:
    """The jsonb payload persisted for the correction row.

    Carries the field changes AND the attribution (corrector, when) and the
    supersede reason — so the ledger row is a *complete* attributed record, and the
    fold sees the corrected value while the prior rows stay as superseded records.
    """
    op = correction.op
    payload: dict[str, Any] = {
        "corrector": correction.corrector,
        "corrected_at": correction.corrected_at.isoformat(),
    }
    if isinstance(op, CloseOp):
        payload["closed"] = True
        payload["resolution"] = op.resolution
    else:
        # Merge the field changes so the fold updates the entry's current value.
        for key, value in op.changes.items():
            payload[key] = _jsonable(value)
        payload["supersede_reason"] = op.supersede_reason
    return payload


def _jsonable(value: Any) -> Any:
    """Coerce enum members to their wire value so the payload is jsonb-clean."""
    if isinstance(value, DecisionStatus):
        return value.value
    if isinstance(value, Reversibility):
        return value.value
    return value


async def apply_correction(
    db: SupportsAcquire,
    correction: Correction,
    *,
    meeting_id: Any,
    speak: Optional[SpeakFn] = None,
    window_start_s: float | None = None,
) -> ApplyResult:
    """Apply ONE correction: immediate, attributed, superseded-not-erased (§3.6).

    Patch-first ordering, and honest on failure:

    1. Classify via the notes-injection gate (:func:`is_high_stakes`).
    2. Append the ``patch``/``close`` row to the append-only ``note_deltas``
       ledger — the prior value's rows stay (superseded-not-erased); the fold now
       yields the corrected value. This is the commit point.
    3. **Only after** the commit succeeds, **and only** for the high-stakes class,
       emit exactly one spoken receipt via ``speak``. Ordinary corrections never
       touch ``speak`` and never invoke the gate's speech path.

    A DB write failure raises out of :func:`db.repos.notes.append_delta` (the real
    asyncpg error) and is left to propagate — no receipt is spoken, no success
    object is returned (F-CORR-FALSE-CONFIRM). The pipeline is never blocked on a
    human response to the receipt: ``speak`` is a fire-once emit, not an await on an
    approval (F-CORR-BLOCKING-CONFIRM).

    ``correction`` is already-attributed by construction (the :class:`Correction`
    ``__post_init__` rejects a missing corrector/timestamp), so this path never
    writes an incomplete record.
    """
    # (1) Classify BEFORE the write so ordering is explicit and testable.
    gate_fired = is_high_stakes(correction)

    op = correction.op
    op_name = "close" if isinstance(op, CloseOp) else "patch"
    payload = _payload_for(correction)

    # (2) COMMIT the patch — append-only, so the prior rows are superseded-not-erased.
    #     Any DB fault raises here and propagates: no speak, no success state.
    #     The notes repo is imported by its src-layout walk name so the strict type
    #     walk resolves it (mirrors the sibling ``notes.py`` -> ``libs.db`` seam).
    from libs.db.src.db.repos import notes as notes_repo

    async with db.acquire() as conn:
        row = await notes_repo.append_delta(
            conn,
            meeting_id=meeting_id,
            entry_id=correction.target_id,
            op=op_name,
            payload=payload,
            window_start_s=window_start_s,
        )

    delta_id: Optional[int] = None
    if row is not None:
        delta_id = int(row["id"])

    # (3) ONLY after commit, and ONLY for the high-stakes class, speak ONE receipt.
    spoken: Optional[str] = None
    if gate_fired and speak is not None:
        spoken = acknowledgement_line(correction)
        await speak(spoken)

    return ApplyResult(
        committed=True,
        gate_fired=gate_fired,
        spoken=spoken,
        delta_id=delta_id,
        superseded_prior=correction.prior_entry is not None,
    )


# ---------------------------------------------------------------------------
# Gate scope boundary — the notes gate governs NOTES corrections ONLY (§3.6)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class WorldTouchingAction:
    """A world-touching action (staged PR, calendar invite, apply) — the analog
    class the notes-injection gate must NOT touch. These already require a human
    click (staged-drafts-only, Law 3 / Doc 00 §15); the notes gate is a *different*
    floor and must not route them or speak a receipt for them."""

    kind: str
    detail: str = ""


def is_notes_correction(obj: object) -> bool:
    """Only a :class:`Correction` is in the notes-injection gate's scope (§3.6).

    A :class:`WorldTouchingAction` (or anything else) is out of scope — it flows
    through Law 3's human-click approval path, never this gate
    (F-CORR-GATE-SCOPE-EXCEEDED)."""
    return isinstance(obj, Correction)


def notes_gate_governs(obj: object) -> bool:
    """Public predicate: does the notes-injection gate govern ``obj``?

    Returns ``True`` for a notes :class:`Correction`, ``False`` for a
    :class:`WorldTouchingAction` or any non-notes request — the gate's boundary is
    *notes corrections only*, so a world-touching action is never routed through it
    and never draws a notes-gate spoken confirm."""
    return is_notes_correction(obj)


__all__ = [
    "AttributionError",
    "Correction",
    "CorrectionOp",
    "ApplyResult",
    "WorldTouchingAction",
    "SpeakFn",
    "SupportsAcquire",
    "is_high_stakes",
    "acknowledgement_line",
    "apply_correction",
    "is_notes_correction",
    "notes_gate_governs",
]
