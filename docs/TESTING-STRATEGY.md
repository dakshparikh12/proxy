# Proxy · Testing Strategy — how acceptance criteria relate to real-data testing
### The one idea: the criterion is a stable property; the golden dataset is what grows. The acceptance criterion IS the eval.

## Two arbiters, one set of acceptance criteria
Each component doc §8 lists acceptance criteria in two tiers, tagged by arbiter:
- **[pytest] — code correctness.** Deterministic properties on data built from a real estate ("0 uncited edges," "0 cross-tenant reads"). Run every pass by the build loop. Written once, stable.
- **[eval] — product accuracy on real data.** A stable property + threshold, scored against a LIVING golden dataset of real repos ("≥85% of real blast-radius questions match the senior-engineer answer, cited, <1s"). Run per section by the eval gate.

There is no gap between "the AC we wrote" and "how it's tested": the [eval] criterion names its estate range, its golden key, its grader, and its threshold. Writing the AC *is* writing the test.

## Why "different each day" is not a moving target
The **criterion** never changes: "does the graph name the dependencies a senior engineer would, above threshold, on any real repo." The **golden dataset** grows: you seed ~20-30 real cases before building, and add every real failure you later discover. Same "good," proven against more evidence over time — the suite gets harder to fool, the bar stays fixed.

## Author the eval BEFORE building (eval-driven)
When you lock a component doc, author its golden dataset (real repos + human expected answers) in the same pass. This makes "accurate on real data" concrete from day one and gives the build loop a fixed real target to iterate toward. For the graph, most correctness is deterministically gradeable once a human authors the answer key (blast-radius set match, latency, citation coverage, cross-tenant); only semantic accuracy needs a calibrated LLM-judge.

## The grading stack (per [eval] criterion)
1. **Deterministic grader** wherever a code check confirms the outcome — preferred, cheap, exact.
2. **LLM-as-judge** only after calibration against the human gold set — for fuzzy outcomes (is the answer the one a senior engineer gave? is it bluffing? is the extraction faithful?).
3. **Threshold** — set in the doc before building; the pass bar, per estate.
4. **Diversity = coverage** — the dataset spans messy/single/monorepo/multi-language/docs-heavy, so passing means *generalizes*, not "works on the clean one."

## The iteration loop (until accurate)
build loop green (code) → eval gate (real data) → any criterion below threshold on any estate → fresh context + failure report → fix root cause → rebuild → re-eval → repeat until every criterion clears on every estate. Never weaken a criterion. "Perfect results" includes the messy estate.

## Living dataset discipline
Start small and curated (~20-30). Every production/real failure becomes a new golden case. Version the dataset, thresholds, and results in git with a changelog — when a threshold changes, the reason is documented.

## The eval tooling (industry standard, OSS, self-host)
- **DeepEval** — the eval engine. Pytest-native, so the real-data eval IS failing pytest tests that block on threshold regression — it plugs straight into `verify.sh`/`runner.py`. MIT, free, 50+ metrics + custom G-Eval/DAG scorers (encode each acceptance criterion as a scorer). Used at OpenAI/Google/Microsoft.
- **Langfuse** — self-hosted observability: latency, cost, and trace monitoring; catches what test suites miss.
- **Promptfoo** (optional) — adversarial/red-team rung (500+ attack vectors, GitHub Action).

## The scorer mix (per eval criterion) — never LLM-judge alone
- **60% deterministic** — exact/set match, JSON-schema, **latency threshold**, cost ceiling. Ground truth.
- **30% LLM-as-judge** — calibrated against the human gold set before it may gate (semantic accuracy: "is this the answer a senior engineer would give?", bluff detection, faithfulness).
- **10% human-in-the-loop** — ambiguous cases.

## Gate disciplines (make the eval a reliable build blocker, not a flaky one)
1. **Baseline first, gate on regression.** Score the build on the golden set before setting the bar; below-baseline blocks. Absolute thresholds AND regression both apply.
2. **Stability:** tolerance bands (not exact thresholds), **pin the judge model**, sample a **stable** golden set — so stochastic output doesn't flake CI.
3. **Cadence:** strict eval on the PR/section gate; heavy red-team + model-matrix sweeps nightly. Cache; use a smaller judge where accuracy allows.
4. **Learning loop:** every correction the agent needed → goes into CLAUDE.md / the component's Gotchas. Every production failure → a new golden case. The suite gets harder to fool over time.

## Who authors what
Tool provides: metrics, scorers, runner, CI gate. You author: the golden dataset (real repos + expected senior-engineer answers) + thresholds. ~20–50 real hard cases to start (small+real > big+synthetic); expand from real failures. Calibrate the judge by hand before trusting it.
