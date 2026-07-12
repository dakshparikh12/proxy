# Proxy · Build System — the full loop-engineering environment
### How we build every component. Read this to understand *how we build*; read a component doc to understand *what to build*.

## 0. Philosophy — Loop Engineering
We don't prompt turn-by-turn; we **design loops that prompt, verify, retry, and stop the agent.** Every component is built by the same loop, driven by a doc that defines "done," gated by two arbiters: a deterministic code arbiter (per pass) and a real-data eval arbiter (per section). **Done = the product is provably correct on real systems**, not that the code compiles.

## 1. The four layers (each owns a phase; clean handoffs)
```
1 SPEC         the component doc (/components/) is the source of truth:
               goal · product · contracts+seams · gotchas · acceptance(2-tier) · real-data eval.
2 WORKFLOW     Superpowers discipline: brainstorm → plan → TDD (red-green-refactor)
               → subagent-driven execution → multi-subagent review → finish-branch.
3 EXECUTION    the hardened harness (/harness): guard.py (default-deny) + runner.py (overnight loop)
               + verify.sh (SOLE code arbiter: ruff/mypy/bandit + milestone-ordered pytest).
4 VERIFICATION real-data eval gate (/eval): pull real repos → build → score vs golden keys +
               thresholds → iterate until accurate. Proves the PRODUCT, not the code.
```
Layers 1–3 = disciplined, safe code. Layer 4 = a correct product. Layer 4 is ours — no framework does it.

## 2. The ONE loop (per component)
There is a single agentic loop. Plan once at the front; then build → verify → iterate until done. "Testing" is the verify step inside the loop, with two rungs (cheap code check, then real-data eval).

```
PLAN (once, attended): brainstorm -> plan -> planner-reviewer subagent -> lock the plan
   |
   +--> BUILD one increment (agentic: TDD; runner.py spawns a fresh session per pass)
   |       |
   |    VERIFY (one step, two rungs, cheap-first):
   |       1. CODE CHECK  = verify.sh : ruff/mypy/bandit + milestone pytest (all [pytest] AC)     [every pass]
   |       2. REAL-DATA EVAL = eval on real repos vs golden keys + thresholds (all [eval] AC)      [when rung 1 is green]
   |       |
   |    both green? -- no --> read the failure, fix (fresh context on repeated failure) --+
   |       | yes                                                                          |
   |     DONE (evidence + dual signoff -> merge)                                          |
   +--------------------------------------------------------------------------------------+  (same loop)
```
- **It is one loop.** The agent builds, checks the code, checks it on real data, and keeps looping until both rungs pass. Never weaken a criterion to pass.
- **Rung 2 fires when rung 1 is green** — a cost optimization (the eval is slow/expensive), NOT a second loop. The expensive real-world check runs after the cheap code signals pass, then on fail the loop restarts. Same loop.
- **Maker != checker (non-negotiable):** the builder never grades its own "done." Rung 1 is the test suite; rung 2 is the eval graders + a fresh-context verifier subagent. The builder proposes done; the checker decides it. (Models systematically fail to flag their own low-correctness output.)
- **Front of the loop:** plan mode + a planner-reviewer subagent — pour effort into the plan so the build passes converge fast.

## 3. What "done" means
Done = the loop's verify step passes BOTH rungs: every [pytest] acceptance criterion green AND every [eval] criterion at/above threshold on every real estate (incl. the messy one) AND the adversarial suite clean. Green code alone is not done; the loop only stops when the real-data eval passes.

## 4. The Claude Code environment
- **`AGENTS.md`** — cross-tool constitution (method, 11 invariants, stack, types). Read first.
- **`CLAUDE.md`** — Claude Code context + style constitution (points to AGENTS.md).
- **Skills** (`.claude/skills/`, each with a **gotchas** section — the reliability lever): `proxy-component-build` (the loop), `eval-gate` (real-data verification), `spec-compliance-review` (fresh-context diff review).
- **Subagents** (`.claude/agents/`, fresh-context — stop the "grader = author" self-approval failure): `planner-reviewer`, `verifier`, `eval-runner`.
- **Hooks** (`.claude/settings.json`, code-enforced guarantees, not requests): PreToolUse→guard.py, Stop→stop_verify.py, PostToolUse→quick test pass.
- **MCP** — for the *product's* tools (estate graph, connectors) via one gateway (tool-search + allow-list + injection scan). For the *build*, keep MCP lean (eval/observability only).
- **Plan mode** — mandatory before the overnight loop (pour effort into the plan → the loop one-shots).
- **Worktrees** — parallelize *independent* components (2–3 max, review-capacity-bound).

## 5. Tools — adopt / keep / skip
**Adopt:** GitHub Spec Kit (spec/plan/tasks + constitution + gates) · Superpowers (brainstorm→TDD→review; audited, official marketplace) · **DeepEval** (pytest-native eval engine — the real-data gate; MIT, self-host) · **Langfuse** (self-host observability: latency/cost/traces) · **Promptfoo** (optional: adversarial/red-team). Scorer mix per criterion: 60% deterministic + 30% calibrated LLM-judge + 10% human. Gate on threshold AND regression-vs-baseline; pin the judge; tolerance bands.
**Keep (hardened harness):** guard.py, runner.py, verify.sh, stop_verify.py, bootstrap.sh, pass_prompt.md, CI + branch protection.
**Skip:** Ruflo/claude-flow (100+-agent swarm — scale you don't have) · BMAD (12+ personas — team size you don't have).
**Security:** vet every adopted skill (13%+ marketplace skills carry critical vulns; ~36% injection in one registry). Keep guard.py default-deny; never pull unvetted skills into an unattended loop.

## 6. Setup order (once)
1 harness kit (guard/runner/verify/hooks) — `/harness/HARNESS.md`. 2 `specify init`; `/speckit.constitution` → point at AGENTS.md. 3 install Superpowers; add bridge rule to the constitution. 4 drop in `.claude/skills` + `.claude/agents`. 5 wire `/eval` (DeepEval/Braintrust) + seed `/fixtures` with real repos + golden keys. 6 CI: verify.yml (Tier-1) + nightly eval job (Tier-2); branch protection requires both.
