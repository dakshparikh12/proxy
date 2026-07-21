"""Receipt of the forced output — extract, then re-validate with Pydantic.

The JSON-schema constraint at generation is good, not a guarantee, so on receipt
we extract the forced ``tool_use`` block and re-validate its payload with the same
``NoteDelta`` model (belt-and-suspenders). The two failure modes are typed errors,
both **non-retryable at the window level** (§3.1: a dropped window is fine, a
stalled meeting is not):

* ``ScribeMaxTokensError`` — ``stop_reason == "max_tokens"``: the window/output was
  too big; this window is a skip.
* ``ScribeNoDeltaError`` — the model did not call the tool (a free-text / malformed
  turn); this window is a skip.

This module also carries the applier-side integrity checks the schema alone cannot
express: a ``patch`` supersedes-not-erases the old value, and a ``contradicts`` (or
patch/close ``target_id``) pointing at a non-existent entry is a referential
integrity violation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .schema import AddOp, Claim, CloseOp, NoteDelta, PatchOp


class ScribeMaxTokensError(Exception):
    """stop_reason == "max_tokens": window/output too big — shrink window, this one is a skip."""


class ScribeNoDeltaError(Exception):
    """model didn't call the tool — malformed turn, skip this window."""


class ReferentialIntegrityError(Exception):
    """An op references an entry id that does not exist in the note store."""


def parse_scribe_result(resp: Any) -> NoteDelta:
    """Extract the forced ``emit_notes_delta`` tool_use block, then Pydantic-re-validate.

    Order matters: a ``max_tokens`` truncation is detected *before* any attempt to
    read the (necessarily incomplete) content, so a truncated turn never falls
    through to the free-text path.
    """
    if resp.stop_reason == "max_tokens":
        raise ScribeMaxTokensError("truncated at max_tokens; window too large")
    block = next(
        (b for b in resp.content if b.type == "tool_use" and b.name == "emit_notes_delta"),
        None,
    )
    if block is None:
        raise ScribeNoDeltaError("no emit_notes_delta tool_use in response")
    # belt-and-suspenders: schema-shaped at generation, then Pydantic-enforced here.
    return NoteDelta.model_validate(block.input)


# ---------------------------------------------------------------------------
# Applier-side integrity — the checks the field-level schema cannot express.
# ---------------------------------------------------------------------------


@dataclass
class Entry:
    """A stored note entry with its supersede history (superseded-not-erased)."""

    id: str
    current: dict[str, Any]
    history: list[dict[str, Any]] = field(default_factory=list)


class NoteStore:
    """A minimal note store: known entry ids + supersede-preserving patch application.

    This is the applier surface the ops act against — enough to enforce the two
    integrity invariants (dangling reference detection; supersede-not-erase) that
    live above the per-field schema.
    """

    def __init__(self, entries: dict[str, Entry] | None = None) -> None:
        self._entries: dict[str, Entry] = dict(entries or {})

    def __contains__(self, entry_id: object) -> bool:
        return entry_id in self._entries

    def get(self, entry_id: str) -> Entry:
        return self._entries[entry_id]

    def add(self, entry_id: str, current: dict[str, Any]) -> Entry:
        entry = Entry(id=entry_id, current=dict(current))
        self._entries[entry_id] = entry
        return entry

    def apply(self, op: AddOp | PatchOp | CloseOp, *, entry_id: str | None = None) -> None:
        """Apply one op, enforcing referential integrity and supersede-not-erase."""
        if isinstance(op, AddOp):
            note_entry = op.entry
            # A Claim may reference an existing entry it contradicts; a dangling
            # reference is a referential integrity violation.
            if isinstance(note_entry, Claim) and note_entry.contradicts is not None:
                if note_entry.contradicts not in self._entries:
                    raise ReferentialIntegrityError(
                        f"contradicts references unknown entry id {note_entry.contradicts!r}"
                    )
            new_id = entry_id if entry_id is not None else f"e{len(self._entries) + 1}"
            self.add(new_id, note_entry.model_dump())
            return

        if isinstance(op, PatchOp):
            if op.target_id not in self._entries:
                raise ReferentialIntegrityError(f"patch target_id {op.target_id!r} does not exist")
            self._apply_patch(op)
            return

        if isinstance(op, CloseOp):
            if op.target_id not in self._entries:
                raise ReferentialIntegrityError(f"close target_id {op.target_id!r} does not exist")
            stored = self._entries[op.target_id]
            stored.history.append(dict(stored.current))
            stored.current = {**stored.current, "resolved": True, "resolution": op.resolution}
            return

        raise TypeError(f"unknown op type: {type(op)!r}")

    def _apply_patch(self, op: PatchOp) -> None:
        entry = self._entries[op.target_id]
        # The old value is kept superseded-not-erased (§3.6): snapshot before mutate.
        entry.history.append(dict(entry.current))
        entry.current = {**entry.current, **op.changes}


def check_referential_integrity(delta: NoteDelta, store: NoteStore) -> None:
    """Raise ``ReferentialIntegrityError`` if any op in the delta dangles.

    A read-only pre-check over a delta against a store: every ``contradicts`` and
    every patch/close ``target_id`` must name an entry the store already knows.
    """
    for op in delta.ops:
        if isinstance(op, AddOp):
            note_entry = op.entry
            if isinstance(note_entry, Claim) and note_entry.contradicts is not None:
                if note_entry.contradicts not in store:
                    raise ReferentialIntegrityError(
                        f"contradicts references unknown entry id {note_entry.contradicts!r}"
                    )
        elif isinstance(op, (PatchOp, CloseOp)):
            if op.target_id not in store:
                raise ReferentialIntegrityError(
                    f"op target_id {op.target_id!r} does not exist"
                )


# ---------------------------------------------------------------------------
# Window pipeline — a typed Scribe error is non-retryable at the window level.
# ---------------------------------------------------------------------------


@dataclass
class WindowResult:
    """The outcome of processing one transcript window."""

    delta: NoteDelta | None
    skipped: bool
    reason: str | None = None


def process_window(call_scribe: Any) -> WindowResult:
    """Process one window: call the Scribe once, parse, and skip (never retry) on a typed error.

    ``call_scribe`` is a zero-arg callable that performs exactly one Scribe API
    call and returns the raw response. Both ``ScribeMaxTokensError`` and
    ``ScribeNoDeltaError`` mark the window skipped WITHOUT a second call — the
    pipeline advances to the next window rather than retrying a doomed one.
    """
    resp = call_scribe()
    try:
        delta = parse_scribe_result(resp)
    except ScribeMaxTokensError:
        return WindowResult(delta=None, skipped=True, reason="max_tokens")
    except ScribeNoDeltaError:
        return WindowResult(delta=None, skipped=True, reason="no_delta")
    return WindowResult(delta=delta, skipped=False)


__all__ = [
    "ScribeMaxTokensError",
    "ScribeNoDeltaError",
    "ReferentialIntegrityError",
    "parse_scribe_result",
    "Entry",
    "NoteStore",
    "check_referential_integrity",
    "WindowResult",
    "process_window",
]
