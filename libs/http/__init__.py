"""libs.http facade (src-layout; real code under src/http)."""
from __future__ import annotations

import os as _os

# Extend the package search path to the src-layout module dir so real submodules
# (``libs.http.internal`` — the session-less /internal/notes handler) resolve as
# genuine importable modules. Mirrors the proven ``services.harness`` pattern.
__path__ = [*__path__, _os.path.join(_os.path.dirname(__file__), "src", "http")]

from .src.http import dispatch as dispatch

__all__ = ["dispatch"]
