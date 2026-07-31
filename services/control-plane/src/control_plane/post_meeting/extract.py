"""B1 — extract. Read the close output into task records at EXTRACTED.

Criteria: **AC-PME-02, AC-PME-02-NEG** (total failure never touches the close or the
record) and **AC-PME-05, AC-PME-05-NEG** (owner is UNRESOLVED or from the room, never
inferred).

Doc 07 §2 is the boundary this module is built around: *"This doc begins after the record
is written, and only reads it."* Nothing here participates in the ordered close, holds the
bot in the meeting, inserts a step, or writes to the notes object. The close output is
intake — read once, never modified.

The isolation promise is structural, not a convention: :func:`run_extract` is a total
function. It catches every exception, including from the database, and returns an
:class:`ExtractResult` carrying the failure. A caller wired into the post-close path
therefore cannot be made to raise into the close by anything this component does. That is
what AC-PME-02 and AC-PME-02-NEG assert, and it is why the try/except is deliberately
broad rather than narrowed to expected error types — a narrow catch would let an
unforeseen error class through into the close, which is the exact harm.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Protocol

from .models import UNRESOLVED, ExtractedItem, Source, TaskRecord, TaskState

log = logging.getLogger(__name__)


class TaskStore(Protocol):
    """The durable side of B1 — narrow on purpose (Doc 07 §3.8 permits its own record)."""

    async def insert_task(self, task: TaskRecord) -> Any: ...


@dataclass
class ExtractResult:
    """What B1 produced, and — honestly — what it failed to produce."""

    tasks: list[TaskRecord] = field(default_factory=list)
    #: Set when extraction failed. The close is unaffected either way (AC-PME-02).
    error: Optional[BaseException] = None
    #: True when the notes read was partial/stale/malformed (AC-PME-05-NEG).
    read_degraded: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def unresolved_count(self) -> int:
        return sum(1 for t in self.tasks if t.owner == UNRESOLVED)


def resolve_owner(raw_owner: Any) -> tuple[str, bool]:
    """Doc 07 §3.2 — the ONLY place an owner is decided.

    Returns ``(owner, came_from_the_room)``.

    An owner comes from the room or the item is ``UNRESOLVED``. There is deliberately no
    parameter here for seniority, speaking volume, or file authorship, and no access to
    the roster or the transcript: the signature makes inference impossible rather than
    merely discouraged. AC-PME-05 plants exactly those three decoys and asserts none of
    them is ever selected.

    A whitespace-only or empty owner is NOT an owner — it collapses to ``UNRESOLVED``,
    which is a real value distinct from empty (AC-PME-05 asserts the distinction).
    """
    if raw_owner is None:
        return UNRESOLVED, False
    if not isinstance(raw_owner, str):
        # A non-string owner is unreadable, not a hint to be coerced.
        return UNRESOLVED, False
    owner = raw_owner.strip()
    if not owner:
        return UNRESOLVED, False
    if owner == UNRESOLVED:
        return UNRESOLVED, False
    return owner, True


def _item_ref(meeting_id: Any, index: int) -> str:
    """A stable pointer back to the line in the meeting record (Doc 07 §3.4 "why it
    exists, with the meeting reference"). Positional within the close object, which is
    itself immutable once written — so the reference stays valid."""
    return f"{meeting_id}#action_items[{index}]"


def extract_items(
    final_notes: Any,
    *,
    meeting_id: Any,
    source: Source = Source.CLOSE_ITEM,
) -> tuple[list[ExtractedItem], bool]:
    """Lift action items out of a close object. Pure — reads, never writes.

    Returns ``(items, read_degraded)``. ``read_degraded`` is True when the close object
    could not be read cleanly (missing/partial/malformed ``action_items``). Per
    AC-PME-05-NEG a degraded read never *widens* the set of items given a concrete owner:
    an unreadable item yields ``UNRESOLVED``, never a guess drawn from whatever fragment
    survived.
    """
    degraded = False
    raw_items: Iterable[Any]

    got = getattr(final_notes, "action_items", None)
    if got is None:
        # Absent action_items is a degraded read, not an empty meeting.
        return [], True
    try:
        raw_items = list(got)
    except TypeError:
        return [], True

    items: list[ExtractedItem] = []
    for idx, raw in enumerate(raw_items):
        text = getattr(raw, "text", None)
        if not isinstance(text, str) or not text.strip():
            # An item we cannot read is recorded as degraded and skipped — never
            # reconstructed from neighbouring items.
            degraded = True
            continue
        owner, from_room = resolve_owner(getattr(raw, "owner", None))
        items.append(
            ExtractedItem(
                item_ref=_item_ref(meeting_id, idx),
                text=text.strip(),
                owner=owner,
                source=source,
                owner_from_room=from_room,
            )
        )
    return items, degraded


async def run_extract(
    final_notes: Any,
    *,
    meeting_id: Any,
    tenant_id: Any,
    store: TaskStore,
    source: Source = Source.CLOSE_ITEM,
) -> ExtractResult:
    """B1's entry point. **Never raises** (AC-PME-02 / AC-PME-02-NEG).

    Every failure — a malformed close object, an unreachable database, a rejected insert —
    is captured onto the result. The caller is on the post-close path, and Doc 07 §2 says
    that if this component fails entirely the close sequence and the meeting record must be
    unaffected. A raise here would violate that, so there is no path out of this function
    that raises.

    Partial progress is kept: if the third of five inserts fails, the two that succeeded
    are returned alongside the error rather than being silently discarded. Nothing is
    rolled back, because nothing outside ``post_meeting_tasks`` was touched.
    """
    result = ExtractResult()
    try:
        items, degraded = extract_items(final_notes, meeting_id=meeting_id, source=source)
        result.read_degraded = degraded
        for item in items:
            task = TaskRecord(
                task_id=None,
                tenant_id=tenant_id,
                meeting_id=meeting_id,
                source=item.source,
                item_ref=item.item_ref,
                state=TaskState.EXTRACTED,
                tier=None,
                owner=item.owner,
                text=item.text,
                read_degraded=degraded,
            )
            task.task_id = await store.insert_task(task)
            result.tasks.append(task)
    except BaseException as exc:  # noqa: BLE001 — see module docstring; a narrow catch
        # would let an unforeseen error class reach the close, which is the harm itself.
        log.exception("post-meeting extract failed for meeting %s", meeting_id)
        result.error = exc
    return result
