"""LIVE subscription-judge smoke (task JUDGE, AC #2) — run MANUALLY, never in the offline suite.

Proves the judge scores end-to-end on the Claude Max subscription: a real deepeval
``GEval`` metric with ``model=subscription_judge()`` scores ONE trivial known case
("What is 2+2?" -> "4") and the score lands in [0, 1].

Run:  .venv/bin/python tests/eval/live_judge_smoke.py
(The filename deliberately has no ``test_`` prefix so pytest never collects it offline.)
"""

from __future__ import annotations

import os
import pathlib
import sys

# Subscription CLI auth only — pop the paid key BEFORE any deepeval/SDK import.
os.environ.pop("ANTHROPIC_API_KEY", None)

# Runnable as a plain file path: put the repo root on sys.path for the tests.eval import.
_REPO_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from deepeval.metrics import GEval
from deepeval.test_case import LLMTestCase, LLMTestCaseParams

from tests.eval.subscription_judge import subscription_judge


def main() -> None:
    metric = GEval(
        name="Arithmetic correctness",
        criteria="Determine whether the actual output is the arithmetically correct answer to the input.",
        evaluation_params=[LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
        model=subscription_judge(),
        async_mode=False,  # one deterministic sync pass for the smoke
    )
    test_case = LLMTestCase(input="What is 2+2?", actual_output="4")

    metric.measure(test_case)

    score = metric.score
    assert score is not None, "GEval produced no score"
    assert 0.0 <= score <= 1.0, f"score out of range: {score}"
    print(f"LIVE_JUDGE_SMOKE: score={score} reason={metric.reason!r}")
    print("LIVE_JUDGE_SMOKE: OK (scored on the subscription judge)")


if __name__ == "__main__":
    main()
