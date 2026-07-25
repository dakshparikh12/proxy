"""services.workroom — sandboxed Workroom (E2B mutable work).

Proposes changes as durable staged drafts, accepts them from durable storage
after teardown, and recovers interrupted tasks (operation_type='workroom:<id>')
by restarting the coarse unit unless the deliverable already exists.
"""
from __future__ import annotations

from .drafts import accept_code_change_draft as accept_code_change_draft
from .drafts import accept_draft as accept_draft
from .drafts import propose_change as propose_change
from .recovery import recover_task as recover_task
from .session import SessionDriver as SessionDriver
from .session import stable_prefix_cache_ttl_seconds as stable_prefix_cache_ttl_seconds
from .session import workroom_op_type as workroom_op_type

__all__ = [
    "SessionDriver",
    "accept_code_change_draft",
    "accept_draft",
    "propose_change",
    "recover_task",
    "stable_prefix_cache_ttl_seconds",
    "workroom_op_type",
]
