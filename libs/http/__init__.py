"""libs.http facade (src-layout; real code under src/http)."""
from __future__ import annotations

import os as _os

# Extend the package search path to the src-layout module dir so real submodules
# (``libs.http.internal`` — the session-less /internal/notes handler) resolve as
# genuine importable modules. Mirrors the proven ``services.harness`` pattern.
__path__ = [*__path__, _os.path.join(_os.path.dirname(__file__), "src", "http")]

from .src.http import (
    authorize_upgrade as authorize_upgrade,
)
from .src.http import (
    dispatch as dispatch,
)
from .src.http import (
    resolve_entity_tenant as resolve_entity_tenant,
)
from .src.http.dispatch import DispatchCtx as DispatchCtx

# The funnel coroutine re-exported under a SECOND, non-colliding name. ``dispatch`` (above)
# shares its name with the ``libs.http.dispatch`` SUBMODULE, so importing that submodule
# anywhere overwrites the ``dispatch`` attribute on this package with the module — a live
# caller doing ``from libs.http import dispatch`` can then get the module, not the coroutine.
# ``run_dispatch`` has no such shadow (no submodule owns the name): the LIVE control_plane
# funnel-drive imports it from HERE (the clean package seam) and is shadow-proof.
from .src.http.dispatch import dispatch as run_dispatch
from .src.http.gateway import Connection as Connection
from .src.http.gateway import RejectUpgrade as RejectUpgrade

# The one inbound handler, re-exported off the clean package seam so the live mount binds
# it without reaching through the ``src`` deep path (the package-boundary seam, §4.4).
from .src.http.handlers.channel_action import handle_channel_action as handle_channel_action
from .src.http.internal import get_notes as get_notes
from .src.http.internal import internal_notes as internal_notes
from .src.http.registry import PUBLIC_ROUTES as PUBLIC_ROUTES

# The §4.6 contract-registry HTTP wrappers + safeError, re-exported off the clean
# package seam so the live control_plane mount binds them without the ``src`` deep path.
from .src.http.registry import AuthzCtx as AuthzCtx
from .src.http.registry import PublicAuthzCtx as PublicAuthzCtx
from .src.http.registry import SessionResolver as SessionResolver
from .src.http.registry import classify_route as classify_route
from .src.http.registry import mark_internal_scoped as mark_internal_scoped
from .src.http.registry import protected as protected
from .src.http.registry import public as public
from .src.http.safe_error import install_safe_error_handler as install_safe_error_handler
from .src.http.safe_error import safe_error_handler as safe_error_handler

# The §4.6 Recall + §3.6 GitHub webhook HMAC verifiers, re-exported off the clean
# package seam so the live control_plane webhook routes bind them without the ``src``
# deep path.
from .src.http.webhook import WebhookVerificationError as WebhookVerificationError
from .src.http.webhook import verify_github_signature as verify_github_signature
from .src.http.webhook import verify_recall_signature as verify_recall_signature

__all__ = [
    "AuthzCtx",
    "Connection",
    "DispatchCtx",
    "PUBLIC_ROUTES",
    "PublicAuthzCtx",
    "RejectUpgrade",
    "SessionResolver",
    "WebhookVerificationError",
    "authorize_upgrade",
    "classify_route",
    "dispatch",
    "get_notes",
    "handle_channel_action",
    "install_safe_error_handler",
    "internal_notes",
    "mark_internal_scoped",
    "protected",
    "public",
    "resolve_entity_tenant",
    "run_dispatch",
    "safe_error_handler",
    "verify_github_signature",
    "verify_recall_signature",
]
