"""libs.http — the one dispatch() funnel + the WS upgrade gateway (§4.3/§12.9)."""
from __future__ import annotations

from .dispatch import (
    DispatchCtx as DispatchCtx,
)
from .dispatch import (
    PerConnectionRateLimiter as PerConnectionRateLimiter,
)
from .dispatch import (
    dispatch as dispatch,
)
from .dispatch import (
    resolve_entity_tenant as resolve_entity_tenant,
)
from .gateway import (
    Connection as Connection,
)
from .gateway import (
    RejectUpgrade as RejectUpgrade,
)
from .gateway import (
    authorize_upgrade as authorize_upgrade,
)

__all__ = [
    "Connection",
    "DispatchCtx",
    "PerConnectionRateLimiter",
    "RejectUpgrade",
    "authorize_upgrade",
    "dispatch",
    "resolve_entity_tenant",
]
