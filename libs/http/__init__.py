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

__all__ = [
    "Connection",
    "DispatchCtx",
    "RejectUpgrade",
    "authorize_upgrade",
    "dispatch",
    "get_notes",
    "handle_channel_action",
    "internal_notes",
    "resolve_entity_tenant",
    "run_dispatch",
]
