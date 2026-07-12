# Proxy · FINAL OVERVIEW — the whole build system in one page
Read `AGENTS.md` (constitution) and `docs/BUILD-SYSTEM.md` (the loop) for detail. This is the map.

## What this repo is
A loop-engineering environment. You store one **component doc** per part of Proxy; a Claude Code build reads it and runs **one agentic loop** — plan → build → verify → iterate — that stops only when the product is **provably correct on real data**. You run components one at a time.

## The one loop
```
PLAN (once): brainstorm → plan → planner-reviewer subagent → lock
  → BUILD one increment (TDD; runner.py, fresh session per pass)
  → VERIFY (one step, two rungs, cheap-first):
       rung 1  code check  = verify.sh (ruff/mypy/bandit + milestone pytest, all [pytest] AC)  [every pass]
       rung 2  real-data eval = DeepEval on real repos vs golden keys + thresholds (all [eval] AC) [when rung 1 green]
  → both green? no → fix (fresh context on repeat) → loop ; yes → DONE (evidence + dual signoff → merge)
```
Maker ≠ checker: the builder never grades its own done — the test suite and DeepEval graders decide.

## The stack (adopt / keep / skip)
Adopt: **Spec Kit** (spec/plan/tasks + constitution) · **Superpowers** (brainstorm→TDD→subagent review) · **DeepEval** (pytest-native eval engine — the product gate) · **Langfuse** (latency/cost/trace observability) · **Promptfoo** (adversarial). Keep: your hardened harness (guard/runner/verify + CI + branch protection). Skip: Ruflo, BMAD (scale/team you don't have). Vet every marketplace skill (guard.py default-deny is the defense).

## Testing (the part that decides "done")
- Acceptance criteria are two-tier, arbiter-tagged: **[pytest]** technical (deterministic, every pass) · **[eval]** product (real repos, thresholds, per section).
- Scorer mix per criterion: **60% deterministic + 30% calibrated LLM-judge + 10% human.**
- You author a small (~20–50) golden dataset of **real repos + expected senior-engineer answers**; expand from real failures; calibrate the judge by hand.
- Gate on **threshold AND regression-vs-baseline**; tolerance bands + pinned judge for stability. Corrections feed back into CLAUDE.md/Gotchas.
- The loop terminates on the **real-data** eval, not the written AC — latency, accuracy, and cost are scored metrics.

## The component-doc structure (what every `/components/<id>.md` contains — the template)
1 Goal (1–3 sentences) · 2 Product (what/features/qualitative flow) · 3 Not-Included · 4 Tech stack ·
5 Architecture & stages (direction-pointers) · 6 Data contracts & seams · 7 Gotchas (known wrong turns) ·
8 Acceptance criteria — Tier-1 [pytest] + Tier-2 [eval] (precise + general properties) ·
9 Real-data eval (golden dataset, scorer mix, thresholds, gate) · 10 Adversarial suite · 11 Definition of Done.
See `components/_TEMPLATE.md`. Every future component doc follows this exactly.

## To run
```
bash harness/scripts/bootstrap.sh
python3 runner.py --component <id>      # one loop: build → rung 1 (verify.sh) → rung 2 (DeepEval eval) → iterate → done
```
Before running: lock the doc, seed `fixtures/<id>/golden/` with real repos + human answers, drop your hardened harness over the scaffolds. See `RUNBOOK.md`.

## What's next (your plan)
Download → push to GitHub → we generate the remaining component docs (1.3, 1.4, 1.5, 02–06) into `components/` one at a time in this exact structure → you run them one by one. Foundation/constitution reconciliation happens last, before the first real build.
