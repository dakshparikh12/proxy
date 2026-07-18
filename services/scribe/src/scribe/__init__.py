"""services.scribe — meeting-notes scribe (Scribe/gates seat).

Records model spend (with the prompt-cache read/creation split) and applies note
deltas atomically with the transcript comprehension flip.
"""
from __future__ import annotations

from .cost import record_scribe_cost as record_scribe_cost
from .notes import apply_note_delta as apply_note_delta

__all__ = ["apply_note_delta", "record_scribe_cost"]
