"""services.workroom — durable staged drafts for the sandboxed work.

Proposes changes as durable staged drafts (behind a human click) and accepts them from durable
storage after teardown. (The old SessionDriver/agent_config/big_build machinery was deleted in the
workroom pivot — native Claude drives the session now.)
"""
from __future__ import annotations

from .drafts import accept_draft as accept_draft
from .drafts import propose_change as propose_change

__all__ = [
    "accept_draft",
    "propose_change",
]
