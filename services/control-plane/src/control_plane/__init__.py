"""control_plane deployable-assembly + per-meeting runtime boot: webhooks, connect page, API, WS
gateway, auth, and the meeting_runtime server/provisioner. Home: the ``services/control-plane``
workspace member; exposed at the ``services.control_plane`` import path via the repo-root conftest
namespace wiring — never a sixth services/ directory (AC-REPO-006).

Re-exports ``create_app`` (the ASGI app factory in :mod:`control_plane.app`) plus the per-meeting
surface: webhook ingest/drain, sign-in/session resolution, and invite/bot-id resolution. (The old
budget/emit/recovery wrappers were deleted in the workroom pivot — budget lives in ``libs.ops``.)
"""
from __future__ import annotations

from .app import app as app
from .app import create_app as create_app
from .meetings import (
    invite_proxy as invite_proxy,
)
from .meetings import (
    resolve_bot_id as resolve_bot_id,
)
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
    "complete_signin",
    "create_app",
    "drain_pending_webhooks",
    "ingest_webhook",
    "invite_proxy",
    "resolve_bot_id",
    "resolve_session",
]
