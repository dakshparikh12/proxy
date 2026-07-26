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
from .registry import (
    PUBLIC_ROUTES as PUBLIC_ROUTES,
)
from .registry import (
    AuthzCtx as AuthzCtx,
)
from .registry import (
    PublicAuthzCtx as PublicAuthzCtx,
)
from .registry import (
    classify_route as classify_route,
)
from .registry import (
    protected as protected,
)
from .registry import (
    public as public,
)
from .safe_error import (
    install_safe_error_handler as install_safe_error_handler,
)
from .safe_error import (
    safe_error_handler as safe_error_handler,
)
from .webhook import (
    WebhookVerificationError as WebhookVerificationError,
)
from .webhook import (
    verify_github_signature as verify_github_signature,
)
from .webhook import (
    verify_recall_signature as verify_recall_signature,
)

__all__ = [
    "AuthzCtx",
    "Connection",
    "DispatchCtx",
    "PUBLIC_ROUTES",
    "PerConnectionRateLimiter",
    "PublicAuthzCtx",
    "RejectUpgrade",
    "WebhookVerificationError",
    "authorize_upgrade",
    "classify_route",
    "dispatch",
    "install_safe_error_handler",
    "protected",
    "public",
    "resolve_entity_tenant",
    "safe_error_handler",
    "verify_github_signature",
    "verify_recall_signature",
]
