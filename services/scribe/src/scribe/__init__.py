"""services.scribe — meeting-notes scribe (Scribe/gates seat).

Records model spend (with the prompt-cache read/creation split) and applies note
deltas atomically with the transcript comprehension flip.
"""
from __future__ import annotations

from .cost import record_scribe_cost as record_scribe_cost
from .notes import apply_note_delta as apply_note_delta
from .parse import ScribeMaxTokensError as ScribeMaxTokensError
from .parse import ScribeNoDeltaError as ScribeNoDeltaError
from .parse import parse_scribe_result as parse_scribe_result
from .schema import NoteDelta as NoteDelta
from .tool import NOTE_DELTA_TOOL as NOTE_DELTA_TOOL

__all__ = [
    "apply_note_delta",
    "record_scribe_cost",
    "NoteDelta",
    "NOTE_DELTA_TOOL",
    "parse_scribe_result",
    "ScribeMaxTokensError",
    "ScribeNoDeltaError",
]
