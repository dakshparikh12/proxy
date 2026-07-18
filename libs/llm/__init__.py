"""libs.llm facade (src-layout; real code under src/llm).

Extends the package search path to the src-layout module dir so the metered
gateway submodules (``libs.llm.routing`` — the canonical seat table — and
``libs.llm.client`` — the call surface + global in-flight semaphore) resolve as
genuine importable modules.
"""
from __future__ import annotations

import os as _os

__path__ = [*__path__, _os.path.join(_os.path.dirname(__file__), "src", "llm")]
