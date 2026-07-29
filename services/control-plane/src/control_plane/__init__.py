"""control_plane deployable-assembly + per-meeting runtime boot: webhooks, connect
page, API, WS gateway, auth, and the meeting_runtime server/provisioner. Home:
the ``services/control-plane`` workspace member; exposed at the
``services.control_plane`` import path via the repo-root conftest namespace
wiring — never a sixth services/ directory (AC-REPO-006).

Re-exports ``create_app`` (the ASGI app factory in :mod:`control_plane.app`) so the
live app — including the §12.9 WS ``/ws`` upgrade gateway mounted there — is importable
as ``services.control_plane.create_app``, plus the per-meeting harness surface the
Doc-00 substrate suite pins (gated emit frontier, crash recovery, webhook
ingest/drain, budget/seam-cost writes, sign-in/session resolution, invite/bot-id
resolution).
"""
from __future__ import annotations

from .app import app as app
from .app import create_app as create_app
from .budget import (
    check_meeting_budget as check_meeting_budget,
)
from .budget import (
    record_seam_cost as record_seam_cost,
)
from .emit import build_emitter as build_emitter
from .meetings import (
    invite_proxy as invite_proxy,
)
from .meetings import (
    resolve_bot_id as resolve_bot_id,
)
from .recovery import recover_meeting_harness as recover_meeting_harness
from .session import (
    complete_signin as complete_signin,
)
from .session import (
    resolve_session as resolve_session,
)
from .webhooks import (
    drain_pending_webhooks as drain_pending_webhooks,
)
from .webhooks import (
    ingest_webhook as ingest_webhook,
)

__all__ = [
    "app",
    "build_emitter",
    "check_meeting_budget",
    "complete_signin",
    "create_app",
    "drain_pending_webhooks",
    "ingest_webhook",
    "invite_proxy",
    "recover_meeting_harness",
    "record_seam_cost",
    "resolve_bot_id",
    "resolve_session",
]
