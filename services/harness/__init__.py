"""services.harness — dotted package facade (real code under src/harness).

Exposes the per-meeting harness surface: the gated emit frontier, crash recovery,
webhook ingest/drain, budget/seam-cost writes, sign-in/session resolution, and
meeting invite/bot-id resolution.
"""
from __future__ import annotations

from .src.harness.budget import (
    check_meeting_budget as check_meeting_budget,
)
from .src.harness.budget import (
    record_seam_cost as record_seam_cost,
)
from .src.harness.emit import build_emitter as build_emitter
from .src.harness.meetings import (
    invite_proxy as invite_proxy,
)
from .src.harness.meetings import (
    resolve_bot_id as resolve_bot_id,
)
from .src.harness.recovery import recover_meeting_harness as recover_meeting_harness
from .src.harness.session import (
    complete_signin as complete_signin,
)
from .src.harness.session import (
    resolve_session as resolve_session,
)
from .src.harness.webhooks import (
    drain_pending_webhooks as drain_pending_webhooks,
)
from .src.harness.webhooks import (
    ingest_webhook as ingest_webhook,
)

__all__ = [
    "build_emitter",
    "check_meeting_budget",
    "complete_signin",
    "drain_pending_webhooks",
    "ingest_webhook",
    "invite_proxy",
    "recover_meeting_harness",
    "record_seam_cost",
    "resolve_bot_id",
    "resolve_session",
]
