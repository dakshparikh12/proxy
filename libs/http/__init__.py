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
from .src.http.gateway import Connection as Connection
from .src.http.gateway import RejectUpgrade as RejectUpgrade
from .src.http.internal import get_notes as get_notes
from .src.http.internal import internal_notes as internal_notes

__all__ = [
    "Connection",
    "DispatchCtx",
    "RejectUpgrade",
    "authorize_upgrade",
    "dispatch",
    "get_notes",
    "internal_notes",
    "resolve_entity_tenant",
]
