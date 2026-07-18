"""services.scribe — dotted package facade (real code under src/scribe)."""
from __future__ import annotations

from .src.scribe import apply_note_delta as apply_note_delta
from .src.scribe import record_scribe_cost as record_scribe_cost

__all__ = ["apply_note_delta", "record_scribe_cost"]
