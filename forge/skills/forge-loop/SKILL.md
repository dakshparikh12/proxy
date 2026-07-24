---
name: forge-loop
description: The forge methodology — the 6-phase spec→production-verified loop where VERIFY and BUILD converge on a terminal fresh-context spec-vs-code audit, the one rule, and the five guarantees. Read at the start of any /forge run.
---

# forge — the loop

Take **any spec** to **production-verified code**: every obligation the spec **states OR implies**,
delivered, **wired into the real product**, and proven on **REAL + MESSY data with the output inspected
against the spec's quality/latency bar** — or a specific `BLOCKED`/`SPEC_BLOCKED` list. Never "it
compiles"; never "the criteria are green"; never a vague "done."

## The one rule
**Agents do judgment · deterministic scripts do the boolean · hooks do the physics · fresh-context
agents do maker≠checker.** Claude's judgment drives reasoning; code does the un-gameable math + the
DONE boolean; hooks enforce what must never be skipped; agents that did NOT build a thing decide
whether it meets the spec. Model-driven, not a rigid template.

## Five guarantees (every simplification preserves these)
(a) **100% spec coverage** — stated AND inferred obligations, re-checked against the RAW spec, not just the criteria.
(b) **maker ≠ checker in fresh context** — at every level: criteria, code, and the terminal spec audit.
(c) **real-data + real-infra verification** — through the product path, never an injected double.
(d) **no false-DONE** — the terminal gate is the spec judged against the RUNNING code, not self-authored criteria.
(e) **production quality** — proven on real AND messy data with the OUTPUT inspected (correct, honestly-labeled, fast) against the §8 bar — not "the capability exists."

## VERIFY and BUILD are the SAME loop
They differ in exactly one place — **who authors the criteria.** BUILD authors them from the spec (②).
VERIFY loads the sealed bundle, then **re-audits it against the spec and extends it** (a seal can
under-specify its spec — never trust it blindly; that is the failure this loop closes). Both then run
③④⑤⑥ identically, and both end at the same terminal gate: the spec judged against the running code by
agents that didn't build it.

## The 6 phases
1. **UNDERSTAND** — `forge:analyst` (fresh) → intent brief: real intent, hidden/derived/**inferred**
   obligations, edge/negative cases, and the **production bar** (§8 accuracy/latency + the messy estates).
2. **COMPLETE THE BUNDLE** — author (BUILD) or load (VERIFY) criteria; then `gates/coverage.py`
   (id-closure) **+ `forge:criteria-auditor` (fresh, vs raw spec)** must be SEAL-READY: zero uncovered
   clauses, zero missed hidden obligations, a `[eval]` criterion for every §8 quality/latency/messy bar.
   REWORK → a **different** fresh agent authors the gap → extend bundle → re-audit.
3. **PLAN** — `gates/streams.py` file-disjoint streams (contracts first); `forge:planner-reviewer`
   (fresh) locks each. Streams run concurrent in worktrees (symlink `.venv` in so tests run).
4. **BUILD + VERIFY** — `forge:build-slice` per task: a failing acceptance test on the **real product
   entrypoint** → green → flip. `forge:reviewer` (fresh) per task: correct AND **wired into production**.
   Ladder static→unit→property→real-infra→real-data + `forge:eval-gate` on **real + messy** estates,
   scoring the output vs §8.
5. **DONE** — `gates/done-check.sh --spec <id>` (5 conjuncts incl. real-data eval ≥ baseline). Run it
   **background + poll — never synchronous** (a silent long command trips the watchdog).
6. **SPEC-AUDIT LOOP (terminal, both modes)** — fresh agents that did NOT build partition the spec,
   re-derive obligations, and check the **running code on real + messy data through the product path** —
   output vs bar, wiring, performance. Gaps → author + extend + loop ③/④ → re-⑤ → re-⑥. **Terminate
   only when ⑥ zero-gap AND ⑤ green AND eval ≥ bar.** Convergence guard: the gap-set must **strictly
   shrink**; on a 2-round stall or a genuine infra block → return the honest `BLOCKED` list, never loop
   forever, never fake convergence.

## Physics (hooks — can't be skipped)
PreToolUse (tests/bundle read-only) · PostToolUse (ruff/mypy/bandit signal) · Stop (done-check must be
green to end) · SubagentStop (no out-of-scope write, no secret).

## Delegation (autonomous) — auto vs human
**Auto-approve:** bundle-extension, `_baseline.json`, non-prod fixes. **Human-only (always escalate):**
prod deploy · destructive/prod migration · `EXTRACTION_COUNT_HALT` · a genuine `SPEC_BLOCKED`.

## Reliability + model tiering
No agent runs `done-check`/any >2-min command synchronously (background + poll). Every acceptance test
drives the real product entrypoint on real data. Parallel docs/streams: own Postgres port, shared
idempotent clone cache, commit each green increment. Opus `xhigh` for build/plan/audit · Sonnet for the
per-task reviewer · Haiku for explore/search. `/clear` between tasks; after 2 failed corrections,
`/clear` + rewrite the prompt.
