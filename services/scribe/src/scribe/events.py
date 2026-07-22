"""The event emitter — material-change events to Doc 04 (03-MEETING-UNDERSTANDING §3.5).

On applying a committed note-delta, this layer emits **material-change events** to a
Doc 04 sink — one of exactly the seven kinds in the CLOSED
:class:`contracts.MaterialChangeKind` registry (``claim-landed-checkable``,
``decision-forming``, ``decision-final``, ``contradiction``, ``action-item``,
``question-open``, ``question-closed``). Each event carries the **triggering entry**
plus a **focused context slice** — the relevant surrounding notes, never the full
meeting context (§3.5). **Chitchat (a ``ContextLine`` add) and running-context
updates (a delta whose only signal is ``current_goal``) emit NOTHING.**

The layer is a **pipe, never the mouth** (CANONICAL §12.3): it *supplies* events to
Doc 04 and holds no room-delivery authority. It never calls ``speak`` / ``send_chat``
/ ``show_screen``; all room delivery is Proxy's wake-turn tools. Persistence of the
committed delta is orthogonal to emission — a ``ContextLine`` still lands in the
``note_deltas`` ledger even though it fires no event (AC-EVENT-10).

**The decision/action chat-record line (§3.5 / CANONICAL §12.12)** is a *deterministic
harness formatter keyed on the committed note-delta* — **never a Proxy wake, never
model-generated**. It **dedupes by ``meeting_revision``** (a re-fold of the same
committed delta posts nothing new) and **honors the room's disable toggle**. The
formatter is pure and byte-deterministic; the posting decision is a separate,
model-free gate.

STRICT SCOPE: this module imports the Entry types from :mod:`scribe.schema` and the
closed event-kind enum from :mod:`contracts`. It invents no new wire type (the
contracts registry stays closed) and touches no other component.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

from contracts import MaterialChangeKind

from .schema import (
    ActionItem,
    AddOp,
    Claim,
    CloseOp,
    ContextLine,
    Decision,
    DecisionStatus,
    Entry,
    NoteDelta,
    OpenQuestion,
    PatchOp,
)

__all__ = [
    "MaterialChangeEvent",
    "Doc04Sink",
    "CollectingSink",
    "DeltaPersister",
    "TargetResolver",
    "classify_add_entry",
    "classify_closed_entry",
    "classify_op",
    "focused_context_slice",
    "emit_events_for_delta",
    "apply_delta",
    "format_chat_line",
    "ChatLinePoster",
    "ChatRecorder",
    "record_chat_lines",
]


# --------------------------------------------------------------------------- #
# The event payload (AC-EVENT-02: entry + focused context slice).             #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MaterialChangeEvent:
    """One material-change event handed to Doc 04 (§3.5).

    Carries the full **triggering entry** and a **focused context slice** — the
    relevant surrounding notes, deliberately NOT the full meeting context. The
    ``meeting_revision`` is the committed-delta revision the event rode in on; the
    chat-record dedupe (AC-EVENT-08) keys on it.
    """

    kind: MaterialChangeKind
    entry: Entry
    context_slice: tuple[Entry, ...]
    meeting_revision: int

    def is_complete(self) -> bool:
        """A payload is complete only with a real entry AND a non-empty slice.

        AC-EVENT-02: an event with a missing entry or an empty context slice is
        not a complete payload and must not be emitted as one.
        """
        return self.entry is not None and len(self.context_slice) > 0


@runtime_checkable
class Doc04Sink(Protocol):
    """The other side of the seam — Doc 04's material-change sink.

    A test double IS the correct boundary here (the sink is the seam's far side,
    not a faked vendor). Production wires the real Doc 04 consumer; both satisfy
    this one method.
    """

    def emit(self, event: MaterialChangeEvent) -> None: ...


@dataclass
class CollectingSink:
    """A fake Doc 04 sink that records every emitted event (unit-test double)."""

    events: list[MaterialChangeEvent] = field(default_factory=list)

    def emit(self, event: MaterialChangeEvent) -> None:
        self.events.append(event)

    @property
    def count(self) -> int:
        return len(self.events)


# --------------------------------------------------------------------------- #
# Deterministic op → event-kind classification (Law 4: physics in code,       #
# never model judgment). Chitchat / running-context return None (no event).   #
# --------------------------------------------------------------------------- #
def _entry_of(op: AddOp | PatchOp | CloseOp) -> Optional[Entry]:
    """The entry an op operates on, if the op carries one inline (AddOp only)."""
    if isinstance(op, AddOp):
        return op.entry
    return None


def classify_add_entry(entry: Entry) -> Optional[MaterialChangeKind]:
    """Map a freshly-added entry to its material-change kind, or None (no event).

    The mapping is total and deterministic:

    * ``Claim`` — a checkable claim landed → ``CLAIM_LANDED_CHECKABLE``. If it also
      carries a ``contradicts`` link, the contradiction is the material change →
      ``CONTRADICTION`` (a detected conflict outranks the bare landing).
    * ``Decision`` — ``forming`` → ``DECISION_FORMING``; ``final`` → ``DECISION_FINAL``.
    * ``ActionItem`` — ``ACTION_ITEM``.
    * ``OpenQuestion`` — an added question opens → ``QUESTION_OPEN`` (a
      question added already ``resolved`` closes → ``QUESTION_CLOSED``).
    * ``ContextLine`` — chitchat/color → **None**: the emitter fires nothing
      (AC-EVENT-03 / AC-EVENT-10), even though the line is still persisted.
    """
    if isinstance(entry, Claim):
        if entry.contradicts is not None:
            return MaterialChangeKind.CONTRADICTION
        return MaterialChangeKind.CLAIM_LANDED_CHECKABLE
    if isinstance(entry, Decision):
        if entry.status is DecisionStatus.final:
            return MaterialChangeKind.DECISION_FINAL
        return MaterialChangeKind.DECISION_FORMING
    if isinstance(entry, ActionItem):
        return MaterialChangeKind.ACTION_ITEM
    if isinstance(entry, OpenQuestion):
        if entry.resolved:
            return MaterialChangeKind.QUESTION_CLOSED
        return MaterialChangeKind.QUESTION_OPEN
    if isinstance(entry, ContextLine):
        return None
    return None


def classify_closed_entry(entry: Entry) -> Optional[MaterialChangeKind]:
    """Map the concrete entry a ``CloseOp`` resolves to its close-event kind.

    * ``OpenQuestion`` — a closed/answered question → ``QUESTION_CLOSED``.
    * ``Decision`` — a finalized decision → ``DECISION_FINAL``.
    Any other entry kind has no close-event (returns None).
    """
    if isinstance(entry, OpenQuestion):
        return MaterialChangeKind.QUESTION_CLOSED
    if isinstance(entry, Decision):
        return MaterialChangeKind.DECISION_FINAL
    return None


def classify_op(op: AddOp | PatchOp | CloseOp) -> Optional[MaterialChangeKind]:
    """Deterministic op → event-kind, or None when the op is not material-change.

    * ``AddOp`` — classified by its entry (see :func:`classify_add_entry`).
    * ``CloseOp`` — carries only a ``target_id``, so its concrete entry (and
      thus its kind) is resolved by the caller against the notes state; this
      function returns None for a bare ``CloseOp`` (the resolved-entry emit path
      in :func:`emit_events_for_delta` carries it via a ``resolve_target`` hook).
    * ``PatchOp`` — an in-place field change (forming→final, firmness bump, a
      question resolved) is applied by the store; on its own it produces no new
      material-change event here (the add/close ops carry the material signal).
      Returns None so patches never over-emit.
    """
    if isinstance(op, AddOp):
        return classify_add_entry(op.entry)
    # CloseOp / PatchOp: no standalone inline-entry event from this function.
    return None


# --------------------------------------------------------------------------- #
# The context slice (AC-EVENT-02): relevant surrounding notes, focused —      #
# never the full meeting context.                                             #
# --------------------------------------------------------------------------- #
def focused_context_slice(
    triggering: Entry,
    surrounding: Sequence[Entry],
    *,
    window: int = 3,
) -> tuple[Entry, ...]:
    """A focused, non-empty slice of surrounding notes for the triggering entry.

    Returns at most ``window`` of the most recent surrounding entries plus the
    triggering entry itself, so the slice is always non-empty and always strictly
    narrower than a large meeting context (AC-EVENT-02: scoped, not full-context).
    The triggering entry is always included, so an event on an empty notes state
    still carries a real, non-empty slice.
    """
    tail = tuple(surrounding[-window:]) if surrounding else ()
    # De-dupe the triggering entry if it already sits in the tail, keeping order.
    slice_entries: list[Entry] = [e for e in tail if e is not triggering]
    slice_entries.append(triggering)
    return tuple(slice_entries)


# --------------------------------------------------------------------------- #
# The emit path — walk a committed delta, fire events, keep persistence        #
# orthogonal (AC-EVENT-01/03/04/10).                                           #
# --------------------------------------------------------------------------- #
@runtime_checkable
class DeltaPersister(Protocol):
    """Persists a committed op to the ``note_deltas`` ledger (§3.3).

    Emission and persistence are orthogonal: a ``ContextLine`` add fires no event
    but is still persisted (AC-EVENT-10). Injected so the pure emit logic is
    unit-testable; the real Postgres append is exercised at the store tier.
    """

    def __call__(self, op: AddOp | PatchOp | CloseOp) -> None: ...


# A resolver from a ``CloseOp.target_id`` to the concrete entry it closes, so the
# close path can emit a fully-populated event (question-closed / decision-final).
TargetResolver = Callable[[str], Optional[Entry]]


def _resolve_close(
    op: CloseOp, resolve_target: TargetResolver | None
) -> tuple[Optional[MaterialChangeKind], Optional[Entry]]:
    """Resolve a ``CloseOp`` to (kind, entry) via the injected target resolver."""
    if resolve_target is None:
        return None, None
    entry = resolve_target(op.target_id)
    if entry is None:
        return None, None
    return classify_closed_entry(entry), entry


def emit_events_for_delta(
    delta: NoteDelta,
    *,
    sink: Doc04Sink,
    meeting_revision: int,
    surrounding: Sequence[Entry] | None = None,
    resolve_target: TargetResolver | None = None,
) -> list[MaterialChangeEvent]:
    """Emit material-change events for every material op in a committed delta.

    Walks ``delta.ops`` in order. Each op that maps to a material-change kind
    produces exactly one event carrying the triggering entry + a focused context
    slice; ops that do not (``ContextLine`` adds, patches, a bare ``current_goal``
    running-context update) emit nothing. Returns the events emitted (also handed
    to the sink), in op order.

    A ``CloseOp`` (a question answered / a decision finalized) emits via the
    injected ``resolve_target`` hook, which maps its ``target_id`` to the concrete
    entry; without a resolver a bare ``CloseOp`` emits nothing (it carries no
    inline entry to put in the payload).

    A running-context update — a delta whose signal is only ``current_goal`` with
    no material op — emits nothing (AC-EVENT-04): there is no material op to walk.
    """
    surrounding = list(surrounding) if surrounding is not None else []
    emitted: list[MaterialChangeEvent] = []
    for op in delta.ops:
        if isinstance(op, CloseOp):
            kind, entry = _resolve_close(op, resolve_target)
        else:
            kind = classify_op(op)
            entry = _entry_of(op)
        if kind is None or entry is None:
            # Not a material change (chitchat / patch / running-context), OR a
            # close with no resolvable entry — no event from this path.
            continue
        context_slice = focused_context_slice(entry, surrounding)
        event = MaterialChangeEvent(
            kind=kind,
            entry=entry,
            context_slice=context_slice,
            meeting_revision=meeting_revision,
        )
        # Never emit an incomplete payload (AC-EVENT-02).
        if not event.is_complete():
            continue
        sink.emit(event)
        emitted.append(event)
        # A newly-added entry becomes part of the surrounding context for any
        # later op in the same delta.
        surrounding.append(entry)
    return emitted


def apply_delta(
    delta: NoteDelta,
    *,
    sink: Doc04Sink,
    meeting_revision: int = 0,
    surrounding: Sequence[Entry] | None = None,
    persist: DeltaPersister | None = None,
    resolve_target: TargetResolver | None = None,
) -> list[MaterialChangeEvent]:
    """Apply a committed delta: persist every op, emit material-change events.

    This is the emitter's public entry point (AC-EVENT-10 references it by name).
    Persistence and emission are decoupled: EVERY op is persisted (if a persister
    is wired), but only material ops emit — so a ``ContextLine``-only delta lands
    in ``note_deltas`` yet fires zero events.
    """
    if persist is not None:
        for op in delta.ops:
            persist(op)
    return emit_events_for_delta(
        delta,
        sink=sink,
        meeting_revision=meeting_revision,
        surrounding=surrounding,
        resolve_target=resolve_target,
    )


# --------------------------------------------------------------------------- #
# The deterministic chat-record line (§3.5 / CANONICAL §12.12).                #
# Pure formatter, keyed on the committed delta — never a wake, never a model. #
# --------------------------------------------------------------------------- #
def _decision_line(entry: Decision) -> str:
    return f"✓ Decision: {entry.text.strip()}"


def _action_line(entry: ActionItem) -> str:
    who = (entry.owner or "").strip()
    task = entry.text.strip()
    if who:
        return f"☐ Action: {who} — {task}"
    return f"☐ Action: {task}"


def format_chat_line(event: MaterialChangeEvent) -> Optional[str]:
    """Deterministic chat-record line for a decision-final / action-item event.

    Pure and byte-deterministic — the SAME committed delta always yields the SAME
    bytes (AC-EVENT-06). Only ``decision-final`` and ``action-item`` events drive a
    record line (§3.5); every other kind returns None (no line). Output never
    contains an internal component name (naming lint) and is produced with **no**
    model call and **no** wake turn — it is a plain string format keyed on the
    committed entry.
    """
    entry = event.entry
    if event.kind is MaterialChangeKind.DECISION_FINAL and isinstance(entry, Decision):
        return _decision_line(entry)
    if event.kind is MaterialChangeKind.ACTION_ITEM and isinstance(entry, ActionItem):
        return _action_line(entry)
    return None


@runtime_checkable
class ChatLinePoster(Protocol):
    """The room's chat-record surface — posts one factual record line.

    This is NOT ``speak`` / ``send_chat`` / ``show_screen`` (those are Proxy's
    wake-turn tools, §3.5). It is the deterministic factual-record post the
    harness owns; the poster is injected so the emitter never holds room-delivery
    authority itself.
    """

    def __call__(self, line: str) -> None: ...


@dataclass
class ChatRecorder:
    """Posts deterministic chat-record lines, deduped by ``meeting_revision``.

    Honors the room's disable toggle and suppresses a duplicate line for a
    re-folded committed delta at the same revision:

    * ``enabled=False`` — the formatter may still be called, but NO line is ever
      posted (AC-EVENT-09).
    * a ``(meeting_revision)`` already posted for the SAME formatted line is
      suppressed on a re-fold (AC-EVENT-08): total posts per revision stay at 1.

    The poster (``post``) is injected — the recorder holds no delivery authority
    and makes no model call; it only ever hands a pre-formatted deterministic
    string to the room's factual-record surface.
    """

    post: ChatLinePoster
    enabled: bool = True
    formatter: Callable[[MaterialChangeEvent], Optional[str]] = format_chat_line
    _posted: set[tuple[int, str]] = field(default_factory=set)

    def record(self, event: MaterialChangeEvent) -> Optional[str]:
        """Format + post the chat-record line for ``event`` if it warrants one.

        Returns the posted line, or None when nothing was posted (not a
        record-worthy kind, disabled toggle, or a deduped re-fold). The formatter
        is ALWAYS pure; the toggle/dedupe gate the *posting*, never the format.
        """
        line = self.formatter(event)
        if line is None:
            return None
        if not self.enabled:
            # Toggle off: formatter may run, but the line is not sent (AC-EVENT-09).
            return None
        key = (event.meeting_revision, line)
        if key in self._posted:
            # Re-fold of the same committed delta at the same revision (AC-EVENT-08).
            return None
        self._posted.add(key)
        self.post(line)
        return line

    def posts_for_revision(self, meeting_revision: int) -> int:
        """How many distinct record lines were posted for a given revision."""
        return sum(1 for rev, _line in self._posted if rev == meeting_revision)


def record_chat_lines(
    events: Iterable[MaterialChangeEvent], recorder: ChatRecorder
) -> list[str]:
    """Drive a recorder over a stream of events, returning the lines posted."""
    posted: list[str] = []
    for event in events:
        line = recorder.record(event)
        if line is not None:
            posted.append(line)
    return posted
