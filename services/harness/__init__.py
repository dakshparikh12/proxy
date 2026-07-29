"""services.harness — dotted package facade (real code under src/harness).

Old-brain residual surface only (``wake_turn``/``behaviors``/``wake``/
``orchestrator``/``dispatch``/``direct_answer``/``provider``); the runtime boot
and control_plane assembly moved to ``services/control-plane`` (exposed as
``services.control_plane`` by the repo-root conftest namespace wiring).
"""
from __future__ import annotations

import os as _os

# Extend the package search path to the src-layout module dir so the residual
# submodules (``services.harness.wake`` / ``services.harness.orchestrator`` ...)
# resolve as genuine importable modules for the doc00-era suites that still
# import them by their historical dotted names.
__path__ = [*__path__, _os.path.join(_os.path.dirname(__file__), "src", "harness")]
