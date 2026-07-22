"""Smoke tests for the DeepEval real-data eval scaffold.

Two tests:
1. ``test_deepeval_importable_when_opted_in`` — verifies DeepEval is importable
   and exposes the expected public API.  Skips when deepeval is not installed
   (the default offline suite).

2. ``test_deterministic_grader_scores_a_real_golden`` — loads the real
   ``signal_names_golden.json`` from ``fixtures/goldens/`` and asserts that a
   purely deterministic (set-equality) grader scores it correctly.  No LLM call,
   no deepeval required — this test PASSES in the default offline suite.
"""

from __future__ import annotations

import json
import pathlib

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GOLDENS_DIR = pathlib.Path(__file__).parent.parent.parent / "fixtures" / "goldens"

_SIGNAL_NAMES_GOLDEN = _GOLDENS_DIR / "signal_names_golden.json"


# ---------------------------------------------------------------------------
# Test 1 — DeepEval importability (skips without deepeval)
# ---------------------------------------------------------------------------


def test_deepeval_importable_when_opted_in() -> None:
    """Verify deepeval is importable and GEval is accessible.

    Skips cleanly when deepeval is not installed (``uv sync --group eval`` not run).
    """
    deepeval = pytest.importorskip("deepeval", reason="deepeval not installed; run `uv sync --group eval` to opt in")

    # Lazily import a core symbol so collection succeeds without deepeval.
    from deepeval.metrics import GEval  # type: ignore[import-untyped]  # noqa: F401

    # Sanity-check the version attribute exists.
    assert hasattr(deepeval, "__version__"), "deepeval must expose __version__"


# ---------------------------------------------------------------------------
# Test 2 — Deterministic grader (no LLM, always passes)
# ---------------------------------------------------------------------------


def _set_equality_grader(golden: list[str], prediction: list[str]) -> float:
    """Deterministic set-equality grader.

    Returns 1.0 when the prediction set matches the golden set exactly,
    otherwise returns 0.0.  No LLM call, no external dependencies.
    """
    return 1.0 if set(golden) == set(prediction) else 0.0


def test_deterministic_grader_scores_a_real_golden() -> None:
    """Load signal_names_golden.json and assert the deterministic grader scores 1.0.

    This test:
    - Uses a REAL golden from ``fixtures/goldens/signal_names_golden.json``
    - Applies a purely deterministic (set-equality) oracle — no LLM call
    - PASSES without deepeval installed
    - Proves the rung-2 plumbing (golden load + grader invocation) works

    Oracle: the golden is an exact, exhaustive list of signal names.  The
    deterministic oracle treats them as a set and requires an exact match.
    """
    assert _SIGNAL_NAMES_GOLDEN.exists(), (
        f"Golden not found at {_SIGNAL_NAMES_GOLDEN}; fixtures/goldens/ must be present"
    )

    golden: list[str] = json.loads(_SIGNAL_NAMES_GOLDEN.read_text())
    assert isinstance(golden, list) and len(golden) > 0, "golden must be a non-empty list"

    # Simulate a model prediction that correctly reproduces the golden.
    # In a real eval, this would be the output of Proxy's signal-name extractor.
    prediction: list[str] = list(golden)  # perfect prediction — score must be 1.0

    score = _set_equality_grader(golden, prediction)
    assert score == 1.0, f"Expected deterministic grader to score 1.0 on a perfect prediction, got {score}"

    # Also verify the negative case: a wrong prediction scores 0.0.
    wrong_prediction = golden[:-1]  # drop one entry
    wrong_score = _set_equality_grader(golden, wrong_prediction)
    assert wrong_score == 0.0, (
        f"Expected deterministic grader to score 0.0 on an incomplete prediction, got {wrong_score}"
    )
