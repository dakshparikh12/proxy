"""services.workroom — durable staged drafts + recovery for the sandboxed work.

Proposes changes as durable staged drafts (behind a human click), accepts them from durable storage
after teardown, and recovers interrupted tasks. (The old SessionDriver/agent_config/big_build
machinery was deleted in the workroom pivot — native Claude drives the session now.)
"""
from __future__ import annotations

from .drafts import accept_code_change_draft as accept_code_change_draft
from .drafts import accept_draft as accept_draft
from .drafts import propose_change as propose_change
from .envelope import build_envelope as build_envelope
from .envelope import emit_tool_boundary_progress as emit_tool_boundary_progress
from .envelope import failure_envelope as failure_envelope
from .envelope import map_status_verification as map_status_verification
from .envelope import progress_event_for_chunk as progress_event_for_chunk
from .recovery import recover_task as recover_task

__all__ = [
    "accept_code_change_draft",
    "accept_draft",
    "build_envelope",
    "emit_tool_boundary_progress",
    "failure_envelope",
    "map_status_verification",
    "progress_event_for_chunk",
    "propose_change",
    "recover_task",
]
