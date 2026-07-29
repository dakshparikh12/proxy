"""Acceptance tests for Task M1 — the no-model notes store (``in_meeting.notes``).

The store is Proxy's memory of the meeting: cleaned transcript lines (as the
transport stream delivers them: text, speaker, timestamp, end_of_turn)
accumulate in arrival order with NO model anywhere on the path (SPEC §2:
"store it raw as the notes. No model on the transcript.").

These tests exercise the real behavior, not the rendering format:
- in-order reconstruction of an arbitrary speaker-attributed sequence,
- a later append is reflected at the tail,
- a recent-window read returns exactly the tail,
- a static proof that the module imports/invokes no LLM provider or SDK.
"""
from __future__ import annotations

import ast
import inspect

import pytest

from in_meeting.notes import NotesStore, TranscriptLine

# An arbitrary multi-speaker exchange (any stream of lines works — nothing in
# the store may depend on these particular values).
_SPOKEN: list[tuple[str, str, float]] = [
    ("Priya", "Let's review the payments incident from Friday.", 12.4),
    ("Marcus", "The retry queue backed up right after the deploy.", 15.9),
    ("Priya", "Which service owns that queue?", 21.3),
    ("Devon", "billing-worker. The consumer config changed in the same release.", 24.0),
    ("Marcus", "I can revert the config if we agree it's the cause.", 29.7),
]


def _filled(store: NotesStore) -> NotesStore:
    for speaker, text, t in _SPOKEN:
        store.append(TranscriptLine(text=text, speaker=speaker, timestamp=t, end_of_turn=True))
    return store


def test_in_order_reconstruction() -> None:
    """Feeding a sequence of lines reconstructs them in order, speaker-attributed."""
    rendered = _filled(NotesStore()).transcript()

    # Every spoken text appears exactly once...
    for _, text, _ in _SPOKEN:
        assert rendered.count(text) == 1, f"line missing or duplicated: {text!r}"
    # ...in exactly arrival order...
    positions = [rendered.index(text) for _, text, _ in _SPOKEN]
    assert positions == sorted(positions), "lines are not in arrival order"
    # ...and each rendered line carries its speaker attribution.
    for speaker, text, _ in _SPOKEN:
        line = next(ln for ln in rendered.splitlines() if text in ln)
        assert speaker in line, f"line lost its speaker label: {line!r}"


def test_later_append_is_reflected() -> None:
    """An append AFTER a read shows up at the tail; earlier content is untouched."""
    store = _filled(NotesStore())
    before = store.transcript()

    store.append(TranscriptLine(text="Agreed — revert it.", speaker="Priya", timestamp=33.1, end_of_turn=True))
    after = store.transcript()

    assert after.startswith(before), "append rewrote earlier transcript content"
    tail = after[len(before):]
    assert "Agreed — revert it." in tail and "Priya" in tail


def test_recent_window_returns_the_tail() -> None:
    """recent(n) returns exactly the last n lines — no more, no less."""
    store = _filled(NotesStore())
    window = store.recent(2)

    for speaker, text, _ in _SPOKEN[-2:]:
        assert text in window and speaker in window
    for _, text, _ in _SPOKEN[:-2]:
        assert text not in window, f"recent window leaked an older line: {text!r}"
    # Tail order is preserved inside the window.
    assert window.index(_SPOKEN[-2][1]) < window.index(_SPOKEN[-1][1])


def test_recent_window_larger_than_store_is_everything() -> None:
    """A window wider than the store degrades to the full transcript."""
    store = _filled(NotesStore())
    assert store.recent(len(_SPOKEN) * 10) == store.transcript()


def test_recent_window_rejects_nonpositive() -> None:
    """A zero/negative window is a caller bug — fail loud, never return the wrong slice."""
    store = _filled(NotesStore())
    with pytest.raises(ValueError):
        store.recent(0)
    with pytest.raises(ValueError):
        store.recent(-3)


def test_lines_snapshot_preserves_fields_and_order() -> None:
    """The raw-line read keeps every transport field, in arrival order."""
    store = _filled(NotesStore())
    lines = store.lines()

    assert len(lines) == len(store) == len(_SPOKEN)
    for line, (speaker, text, t) in zip(lines, _SPOKEN):
        assert (line.text, line.speaker, line.timestamp, line.end_of_turn) == (text, speaker, t, True)


def test_empty_store_reads_empty() -> None:
    """A meeting with nothing said yet reads as empty text, not an error."""
    store = NotesStore()
    assert store.transcript() == ""
    assert store.recent(5) == ""
    assert store.lines() == ()
    assert len(store) == 0


def test_no_model_on_the_notes_path() -> None:
    """Zero model/LLM involvement: only stdlib imports, no provider/SDK/model-call token."""
    import in_meeting.notes as notes  # noqa: PLC0415

    source = inspect.getsource(notes)

    # Every import in the module must come from this small stdlib whitelist —
    # by construction no provider, no SDK, no workspace llm seam can be reached.
    allowed_import_roots = {"__future__", "collections", "dataclasses", "typing"}
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= allowed_import_roots, f"non-stdlib import on the notes path: {imported - allowed_import_roots}"

    # And the acceptance grep: no model-call pattern anywhere in the module.
    lowered = source.lower()
    for pattern in ("messages.create", "model_for", "query(", "anthropic", "claude", "llm", "openai", "model="):
        assert pattern not in lowered, f"model-call pattern {pattern!r} found in the notes store"
