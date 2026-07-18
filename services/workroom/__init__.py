"""services.workroom — dotted package facade (real code under src/workroom)."""
from __future__ import annotations

from .src.workroom import accept_draft as accept_draft
from .src.workroom import propose_change as propose_change
from .src.workroom import recover_task as recover_task

__all__ = ["accept_draft", "propose_change", "recover_task"]
