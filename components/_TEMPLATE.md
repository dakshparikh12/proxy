# Proxy · Component <ID> — <Name>
### One doc = the spec. Read `AGENTS.md` first. Built via the loop in `docs/BUILD-SYSTEM.md`. Fixtures in `/fixtures/<ID>/`, eval in `/eval/<ID>/`.
### Every acceptance criterion is arbiter-tagged: [pytest] = inner-loop code arbiter · [eval] = section real-data arbiter.

## 1. Goal
<1–3 sentences: what this component turns into what, and the one law that governs it. Prevents over-engineering.>

## 2. Product (context + end goal)
**What it is.** <plain language>
**Features.** <bulleted capabilities a person actually touches>
**Qualitative flow.** <how one input flows through, end to end, so the agent understands intent — not just steps>

## 3. Not-Included (scope boundary)
<what belongs to OTHER components; the agent must not build these>

## 4. Tech stack (this component)
<inherits AGENTS.md §Stack; list only this component's specifics: adopted engines/repos + versions, the store, the seams>

## 5. Architecture & stages (direction-pointers on the HOW; precise on the contracts in §6)
```
<the pipeline in one diagram>
```
<per stage: goal · adopted tool · the one non-negotiable. Agentic where genuinely smarter; deterministic where safety demands.>
**Escalation switches (OFF by default):** <name · what it turns on>

## 6. Data contracts & seams
Uses <types> from `AGENTS.md §Shared Types`. **Seams (get a contract test each):** <adapter interfaces this component introduces — e.g. Source, GraphStore, GraphExtractor, the MCP surface>.

## 7. Gotchas (known wrong turns — the reliability lever; feeds the build skill)
- <the specific mistake an agent tends to make here, and the correct move. e.g. "don't cache verify results — a false cache hit is the core failure.">
- <"entity-resolution string-match fails on abbreviations — use hybrid embedding + structural signals.">
- <one per real failure mode; add more as the build surfaces them.>

## 8. Acceptance criteria (precise + general properties, verified on real data — never "exists")
**Tier-1 — technical [pytest]** (deterministic graders on data built from a real estate; binary pass/fail)
- T1. [pytest] <property, measurable, one condition>
- …
**Tier-2 — product [eval]** (golden-dataset eval + threshold; on diverse real estates — messy/single/big/multi-lang/docs-heavy)
- P1. [eval] GIVEN <any real estate> WHEN <capability invoked> THEN <outcome the golden key defines> at **≥<threshold>**. *Grader: <deterministic|calibrated-LLM-judge>.*
- …
**Done** = all Tier-1 pass + all Tier-2 clear threshold on every estate + adversarial suite clean (§10).

## 9. Real-data eval (the product arbiter; the part that proves it works)
**Golden dataset (`/fixtures/<ID>/`).** Clone real, diverse, public/licensed estates spanning the range in §8. Author ~100 human-reviewed goldens (expected outcomes). Living asset — real failures get added back.
**Procedure.** Build the component on each estate → run each [eval] criterion as a scored scenario → deterministic graders where a check confirms it, calibrated LLM-judge (vs the human gold set) for fuzzy outcomes. Metrics: correctness, groundedness, completeness, honesty, latency, cost. Thresholds (§8) are the bar.
**Tool.** DeepEval (pytest-native, plugs into the loop as rung 2) scores each [eval] criterion; Langfuse traces latency/cost; Promptfoo runs §10 adversarial.
**Scorer mix (per criterion).** 60% deterministic (set-match/latency/schema/cost) + 30% calibrated LLM-judge + 10% human. Never LLM-judge alone.
**Gate.** Threshold AND no-regression-vs-baseline; tolerance bands; pinned judge model; stable golden set.
**Iterate.** Any metric below threshold → fresh context + failure report → fix root cause → rebuild → repeat until accurate on real data. Never weaken a criterion to pass.

## 10. Adversarial suite (must find zero invariant violation)
<the attacks specific to this component: injection, cross-tenant, permission bypass, stale-cache, fabricated-citation, silent-gap, hostile inputs>

## 11. Definition of Done
All [pytest] green with conformance cases · all [eval] clear threshold on every estate · adversarial clean · ruff/mypy/bandit clean · no invariant-violating path (test-proven) · evidence folder committed · both founders signed off. **Done means the product promises are proven on real data — not that the code compiles.**
