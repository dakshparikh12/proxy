"""services.workroom — durable staged drafts + recovery for the sandboxed work.

Proposes changes as durable staged drafts (behind a human click), accepts them from durable storage
after teardown, and recovers interrupted tasks. (The old SessionDriver/agent_config/big_build
machinery was deleted in the workroom pivot — native Claude drives the session now.)
"""
from __future__ import annotations

from .drafts import accept_code_change_draft as accept_code_change_draft
from .drafts import accept_draft as accept_draft
from .drafts import propose_change as propose_change
from .recovery import recover_task as recover_task

__all__ = [
    "accept_code_change_draft",
    "accept_draft",
    "propose_change",
    "recover_task",
]
