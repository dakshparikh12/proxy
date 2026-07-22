"""Pytest configuration for the tests/eval/ real-data eval scaffold.

The ``held_out`` fixture enforces the anti-gaming rule: the build agent must
never see the held-out eval inputs during default runs.  The golden inputs are
exposed ONLY when ``PROXY_HELD_OUT=1`` is set in the environment (founder-gated
eval environment only).
"""

from __future__ import annotations

import json
import pathlib

import pytest

# Resolve the goldens directory relative to this file, staying inside the repo.
_GOLDENS_DIR = pathlib.Path(__file__).parent.parent.parent / "fixtures" / "goldens"


@pytest.fixture
def held_out() -> list[dict]:
    """Return the real golden inputs from fixtures/goldens/.

    Skips unless PROXY_HELD_OUT=1 is set, enforcing the anti-gaming rule.
    """
    from tests.eval.deepeval_config import held_out_enabled

    if not held_out_enabled():
        pytest.skip("held-out inputs are not exposed to the build agent")

    goldens: list[dict] = []
    for path in sorted(_GOLDENS_DIR.glob("*.json")):
        try:
            goldens.append(json.loads(path.read_text()))
        except json.JSONDecodeError:
            pass
    return goldens
