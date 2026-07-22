"""DeepEval scaffold configuration — importable without deepeval installed.

DeepEval is an OPT-IN dependency (``uv sync --group eval``).  This module
intentionally avoids a top-level ``import deepeval`` so the rest of the eval
scaffold can be imported in the default offline suite without errors.
"""

import os

# Pinned judge model (Sonnet, no Haiku per build-loop directive §7 / AMENDMENT-01).
# Update the date suffix when a new stable snapshot is available.
JUDGE_MODEL: str = "claude-sonnet-4-5-20251001"

# Cohen's κ floor from BUILD-LOOP §7 / AMENDMENT-01.
KAPPA_FLOOR: float = 0.6


def held_out_enabled() -> bool:
    """Return True iff the held-out eval gate is active.

    The build agent must never see held-out inputs during normal runs.
    Set ``PROXY_HELD_OUT=1`` only in the founder-gated eval environment.
    """
    return os.environ.get("PROXY_HELD_OUT") == "1"
