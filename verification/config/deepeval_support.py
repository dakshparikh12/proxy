"""Reusable DeepEval wiring (Layer 2) — Claude judge + the four metric factories.

Runs under the ISOLATED tool env (``verification/tools/.venv``) so DeepEval's
heavy dependency tree never touches the product ``uv.lock``. The judge is Claude
via DeepEval's native ``AnthropicModel`` — no OpenAI key, no new vendor, reusing
the existing ``ANTHROPIC_API_KEY``. Every metric is defined once here and reused
across docs; nothing metric-shaped is duplicated per doc.
"""
from __future__ import annotations

import os
from pathlib import Path

from deepeval.metrics import (
    GEval,
    HallucinationMetric,
    TaskCompletionMetric,
    ToolCorrectnessMetric,
)
from deepeval.models import AnthropicModel
from deepeval.test_case import LLMTestCaseParams

# Cheap, fast judge — Layer 2 runs many short judgements; Haiku keeps cost low.
JUDGE_MODEL_ID = os.environ.get("PROXY_DEEPEVAL_MODEL", "claude-haiku-4-5-20251001")


def load_api_key() -> str:
    """ANTHROPIC_API_KEY from the environment, else from the repo/parent .env.

    The worktree checkout omits the git-ignored .env, so fall back to the primary
    repo's .env. The key is never logged.
    """
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    here = Path(__file__).resolve()
    for base in [here.parents[2], *here.parents[2].parent.glob("proxy")]:
        env = base / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    return line.split("=", 1)[1].strip().strip("'\"")
    raise RuntimeError("ANTHROPIC_API_KEY not found in env or .env")


def judge_model() -> AnthropicModel:
    return AnthropicModel(model=JUDGE_MODEL_ID, api_key=load_api_key(), temperature=0)


def generate_text(prompt: str, *, temperature: float = 0.4, max_tokens: int = 600) -> str:
    """One-shot Claude completion via DeepEval's AnthropicModel (single gateway).

    Used by Layer 2 dataset generation. Routing through AnthropicModel keeps the
    raw ``anthropic`` client out of framework code (the same gateway discipline the
    product enforces) and reuses the one ANTHROPIC_API_KEY.
    """
    model = AnthropicModel(model=JUDGE_MODEL_ID, api_key=load_api_key(),
                           temperature=temperature,
                           generation_kwargs={"max_tokens": max_tokens})
    out = model.generate(prompt)
    return out[0] if isinstance(out, tuple) else str(out)


# ---------------------------------------------------------------------------
# The four reusable metrics named by the spec.
# ---------------------------------------------------------------------------
def hallucination_metric(threshold: float = 0.5) -> HallucinationMetric:
    """Flags an answer that contradicts / is not supported by its context."""
    return HallucinationMetric(threshold=threshold, model=judge_model(),
                               include_reason=True, async_mode=False)


def citation_geval(threshold: float = 0.7) -> GEval:
    """G-Eval for Proxy's 'grounded or silent' promise: the answer may only
    assert facts present in the retrieval context — no invented file paths,
    numbers, or claims. This is the citation-correctness scorer."""
    return GEval(
        name="CitationGrounding",
        criteria=(
            "Determine whether the ACTUAL_OUTPUT is fully grounded in and supported "
            "by the RETRIEVAL_CONTEXT. Award a high score only if every factual claim "
            "(file paths, identifiers, numbers, behaviours) appears in or is directly "
            "entailed by the context. Penalise any invented or unsupported claim — the "
            "'grounded or silent' rule: cite from the context or say it is unknown."
        ),
        evaluation_params=[LLMTestCaseParams.ACTUAL_OUTPUT, LLMTestCaseParams.RETRIEVAL_CONTEXT],
        model=judge_model(),
        threshold=threshold,
        async_mode=False,
    )


def task_completion_metric(threshold: float = 0.5) -> TaskCompletionMetric:
    """Did the answer actually accomplish the asked task (vs. dodge it)."""
    return TaskCompletionMetric(threshold=threshold, model=judge_model(),
                                include_reason=True, async_mode=False)


def tool_correctness_metric() -> ToolCorrectnessMetric:
    """Deterministic (no LLM): did the run call the expected tools?"""
    return ToolCorrectnessMetric()
