---
name: forge-loop
description: The forge methodology — the 5-phase spec→production-verified loop, the one governing rule, and the four guarantees. Read at the start of any /forge run.
---

# forge — the build loop

Take **any spec** to **production-verified code** (every behavior proven on real data + real infra),
or a specific `BLOCKED`/`SPEC_BLOCKED` list. Never "it compiles"; never a vague "done."

## The one rule
**Agents do judgment · deterministic scripts do the boolean · hooks do the physics.**
Claude's *judgment* drives the reasoning (understand, author criteria, plan, review, debug); code does
only the un-gameable math + the DONE boolean; hooks enforce what must never be skipped. So forge is
model-driven, not a rigid template.

## Four guarantees (every simplification preserves these)
(a) 100% spec coverage · (b) maker ≠ checker in fresh context · (c) real-data verification · (d) no false-DONE.

## The 5 phases
1. **UNDERSTAND** — `forge:analyst` agent (fresh) → intent brief.
2. **SPECIFY** — `forge:specify` skill → criteria (EARS) + tasks; `gates/coverage.py` proves closure;
   `forge:criteria-auditor` agent proves nothing's missed / every criterion is good; founder seals.
3. **PLAN** — `gates/streams.py` partitions tasks into file-disjoint streams (contracts first);
   per-stream plan → `forge:planner-reviewer` agent (fresh) → lock.
4. **BUILD+VERIFY** — 2–4 worktree-isolated streams; per task the `forge:build-slice` skill (TDD on the
   real path, flip `passes:true` only on shown real evidence); after each green task the `forge:reviewer`
   agent (fresh) reviews the diff. The ladder (static → unit → property → real-infra → real-data) is
   enforced by the hooks + `forge:eval-gate`. On repeated failure: `/clear` + retry fresh; after N
   stalls flag `BLOCKED` and continue (never deadlock).
5. **DONE** — `gates/done-check.sh --spec <id>` (5 conjuncts incl. real-data). Return one state:
   production-verified (with evidence), or a specific BLOCKED/SPEC_BLOCKED list.

## Physics (hooks — can't be skipped)
PreToolUse (tests/bundle read-only) · PostToolUse (ruff/mypy/bandit signal) · Stop (done-check must be
green to end; force-ends after 8 → BLOCKED) · SubagentStop (no out-of-scope write, no secret).

## Founder-gated (never auto-approve)
Bundle seal · `_baseline.json` · `EXTRACTION_COUNT_HALT` · migrations · prod deploy.

## Model tiering + context hygiene
Opus 4.8 `xhigh` for build/plan · Sonnet for judge/reviewer · Haiku for explore/search subagents.
`/clear` between tasks; after 2 failed corrections, `/clear` + rewrite the prompt.
