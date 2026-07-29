"""services.premeeting facade (src-layout; real code under src/premeeting).

The dotted package extends its own search path to the src-layout module dir so real submodules
(``services.premeeting.cloner`` / ``services.premeeting.map_build`` / ``services.premeeting.
repo_context`` …) resolve as genuine importable modules — and so a mypy walk sees ONE module
name per file (``premeeting.<mod>``), not the dual ``services.premeeting.src.premeeting.<mod>``
collision. Mirrors the self-extension at ``services/code_intel/__init__.py`` /
``services/harness/__init__.py`` (NOT the conftest parent-namespace trick).
"""
from __future__ import annotations

import os as _os

__path__ = [*__path__, _os.path.join(_os.path.dirname(__file__), "src", "premeeting")]
