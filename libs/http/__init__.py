"""libs.http facade (src-layout; real code under src/http)."""
from __future__ import annotations

import os as _os

# Extend the package search path to the src-layout module dir so real submodules
# (``libs.http.registry`` / ``libs.http.webhook`` ...) resolve as genuine importable
# modules. Mirrors the proven ``services.harness`` pattern.
__path__ = [*__path__, _os.path.join(_os.path.dirname(__file__), "src", "http")]

from .src.http.registry import PUBLIC_ROUTES as PUBLIC_ROUTES

# The §4.6 route-authz classifier + safeError, re-exported off the clean package seam so
# the live control_plane mount binds them without reaching through the ``src`` deep path.
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
    "PUBLIC_ROUTES",
    "PublicAuthzCtx",
    "SessionResolver",
    "WebhookVerificationError",
    "classify_route",
    "install_safe_error_handler",
    "mark_internal_scoped",
    "protected",
    "public",
    "safe_error_handler",
    "verify_github_signature",
    "verify_recall_signature",
]
