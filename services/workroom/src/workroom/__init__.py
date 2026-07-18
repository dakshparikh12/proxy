"""services.workroom — sandboxed Workroom (E2B mutable work).

Proposes changes as durable staged drafts, accepts them from durable storage
after teardown, and recovers interrupted tasks (operation_type='workroom:<id>')
by restarting the coarse unit unless the deliverable already exists.
"""
from __future__ import annotations

from .drafts import accept_draft as accept_draft
from .drafts import propose_change as propose_change
from .recovery import recover_task as recover_task

__all__ = ["accept_draft", "propose_change", "recover_task"]
