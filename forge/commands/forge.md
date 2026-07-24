---
description: The forge loop — spec → production-verified code. Comprehend (spec+codebase) → plan-to-integrate → build → verify ($0 sim + real data) → dual fresh-context audit → loop until PRODUCTION-VERIFIED. Flags: --auto --verify --build --budget.
argument-hint: <spec-path|doc-id> [more ids...] [--auto] [--verify|--build] [--budget N]
---

# /forge — spec → production-verified code

Driving the forge loop for: **$ARGUMENTS**. Read `${CLAUDE_PLUGIN_ROOT}/README.md` and the
`forge:forge-loop` skill first. **Agents judge · scripts decide the boolean · hooks are the physics ·
fresh-context agents (that did not build it) are the terminal gate.** Never fake DONE; flip
`passes:true` only after the real path RAN on real data with evidence.

## Invocation & flags
- `/forge <target> [more targets...]` — one or more docs (doc-id like `01`, or a spec path). Multiple
  targets → **independent docs run in PARALLEL, dependent ones in dependency order.**
- `--auto` — unattended/overnight: **auto-approve the whole delegation set; on a genuine blocker,
  RECORD it and move to other work** (never idle-wait); deliver a report at the end. (Default =
  interactive: stop + ask on escalations.)
- `--verify` / `--build` — force the mode (default: auto-detect by whether a sealed bundle exists).
  **The modes differ ONLY in who authors the criteria; the loop below is identical.**
- `--budget <N>` — token ceiling (e.g. `2M`); the loop scales depth to it and stops cleanly.
- **For `--auto` OR multiple targets, LAUNCH THE RUNTIME** (do not drive by hand):
  `Workflow({ scriptPath: "${CLAUDE_PLUGIN_ROOT}/workflows/forge-run.js", args: { targets, auto, budget } })`.
  It runs the loop below per doc — in parallel, in the background, resumably — and returns only when
  every doc is `PRODUCTION-VERIFIED` or honestly `BLOCKED`. For a single interactive doc, drive it inline.

## The ONE standard (both modes)
DONE = **every obligation the spec STATES or IMPLIES is delivered, WIRED into the real product path,
and proven on REAL + MESSY data with the actual OUTPUT inspected against the quality/latency bar the
spec implies — confirmed by fresh agents that did not build it, and with the whole system (docs 0..N)
still green.** Never "it exists"; never "the criteria are green"; always "the running product does the
right thing — fast, accurate, honestly-labeled." A capability that only works when a test injects a
double is NOT done.

## The loop (lean — most volume is deterministic; fresh agents only for judgment)
1. **COMPREHEND (spec + codebase)** — `forge:analyst` (fresh, **scoped** context): the spec's
   obligations (stated + **inferred**, the §8 quality/latency bar) **and a survey of the existing
   system** → an intent brief **+ an INTEGRATION MAP** (what to reuse / extend / wire-into; the
   downstream consumers). Planning is to *integrate*, never greenfield-duplicate.
2. **FRAME** — BUILD: `forge:specify` authors criteria + tasks. VERIFY: load the sealed bundle. BOTH:
   `coverage.py` (id-closure) + a **cheap completeness diff** (bundle vs the obligation list). *The
   deep spec audit is step 6 — don't pay for two.*
3. **PLAN-TO-INTEGRATE** — `streams.py` → file-disjoint streams (contracts first); `forge:planner-
   reviewer` (fresh) **only for non-trivial builds**. Every plan **wires into existing entrypoints**
   (no parallel / duplicate / unwired code — this is the class of bug that kills "done").
4. **BUILD** — `forge:build-slice` per task: a failing acceptance test on the **real product
   entrypoint** → code to green → evidence → flip. **One batched `forge:reviewer` per stream**, not
   per task.
5. **VERIFY (cheap → expensive, stop-early)** — deterministic-first:
   - static + unit + **property/fuzz** (code, no model).
   - **$0 SIMULATION HARNESS** — *hundreds* of real-life scenarios (classes: **normal · messy ·
     fault-injected · adversarial · confident-wrong-bait**) fed through the **real product** with
     external seams replayed from `[reality]` cassettes / deterministic generators. **Mostly
     code-graded**; an LLM-judge ONLY for fuzzy outputs. A fresh oracle knows the spec-expected
     behavior, compares the actual, and iterates. Only scenarios that pass graduate.
   - **regression** — every prior doc's tests still green (the cumulative check).
   - **real-infra + real-data eval + differential/metamorphic** — PAID, **small, only on what passed
     sim**, on the real vendors the spec names.
   - Run `done-check.sh` **in the background + poll — never synchronously** (a silent long command
     stalls the agent; this is the failure mode we fixed).
6. **DUAL TERMINAL AUDIT (fresh, didn't build) + loop** —
   - **SPEC lens** — raw spec vs running code: "is this doc genuinely done, **0 gaps**?"
   - **CODEBASE lens (cumulative, via CERTIFICATES)** — full regression + **deep-audit only the blast
     radius** (doc N + any prior code it changed): "is everything that should exist by now (0..N)
     built, **wired, actually working, 0 errors** — down to minutiae?"
   - **CUSTOMER-ACCEPTANCE judge** — a demanding user in a *real meeting* throws real + messy +
     adversarial inputs: is the output **ship-quality, fast, honest**? (bar **inferred** from the spec).
   - Any finding → fix (loop to 3/4) → re-verify → re-audit. **Terminate only when both lenses = 0,
     all green, eval ≥ the inferred bar.** On clean: write the doc's **audit certificate** (code hash +
     verdict) and add every bug found to the **regression ratchet**.
   - **Convergence guard**: the gap-set must strictly shrink; on a 2-round stall or a genuine infra
     block → honest `BLOCKED` (in `--auto`: record + move on).

## Delegation (autonomous) — auto vs human
Auto-approve: bundle-extension, `_baseline.json`, non-prod fixes. **Human-only (escalate; in `--auto`
record + defer):** prod deploy · destructive/prod migration · `EXTRACTION_COUNT_HALT` · a genuine
`SPEC_BLOCKED`.

## Reliability + token discipline
No agent runs `done-check` or any >2-min command synchronously (background + poll). Every acceptance
test drives the real product entrypoint on real data. Deterministic-first (scripts, not agents, decide
counts/latency/byte-equality/diffs). Scoped fresh context (spec section + relevant files, not the whole
tree). Model-tier: Opus for plan/deep-audit/hard-build · Sonnet for stream review · Haiku/no-model for
sim runs + search. Parallel docs/streams: own Postgres port, shared idempotent clone cache, commit each
green increment, certificates skip unchanged docs.

## Return exactly one state (per doc)
**PRODUCTION-VERIFIED** — step 6 both-lenses-0 + all green + eval ≥ bar on real+messy data, with
evidence — or the exact **BLOCKED / SPEC_BLOCKED** list (including anything infra-blocked). Nothing vague.
