"""services.code_intel facade (src-layout; real code under src/code_intel).

The dotted package extends its own search path to the src-layout module dir so
real submodules (``services.code_intel.cloner`` / ``services.code_intel.graph_builder``
/ ``services.code_intel.mcp_server`` ...) resolve as genuine importable modules —
the acceptance suite imports them as ``from services.code_intel.<mod> import ...``.
Mirrors the self-extension at ``services/harness/__init__.py`` and
``libs/http/__init__.py`` (NOT the conftest parent-namespace trick).
"""

from __future__ import annotations

import os as _os

__path__ = [*__path__, _os.path.join(_os.path.dirname(__file__), "src", "code_intel")]

from .src.code_intel.direct import answer_direct as answer_direct
