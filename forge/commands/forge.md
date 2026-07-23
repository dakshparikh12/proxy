---
description: Run the forge build loop on a spec/doc — understand → specify → plan → build → verify → DONE.
argument-hint: <doc-id or spec-path>
---

# /forge — spec → production-verified code

You are driving the **forge** build loop for the target: **$ARGUMENTS**.

Read `${CLAUDE_PLUGIN_ROOT}/README.md` and the skill `forge:forge-loop` first. **You provide the
judgment; the gates provide the boolean; the hooks provide the physics.** Never fake DONE; never
edit tests/bundle/goldens; flip `passes:true` only after the real path RAN on real data with
evidence shown.

## FIRST — determine the mode (this decides which phases run)
Check whether `acceptance/doc<NN>/` already exists and is **sealed**:

- **VERIFY mode** (docs already built — e.g. 00–03): the bundle is sealed and code exists. **Do NOT
  regenerate criteria.** Skip phases ① and ② except to *confirm* coverage
  (`gates/coverage.py <id>` must pass). Then go straight to phase ④ in **verify** posture: for each
  task run its real acceptance test — if green, confirm + flip `passes:true`; if red, fix the
  **product code, never the test** (a test that contradicts the sealed spec is a founder-gated
  repair → surface it, don't guess). End at ⑤. This is "verify + fix what's already there."
  *(Only regenerate a sealed bundle if the founder explicitly asks — e.g. the proving-ground
  comparison of forge's criteria vs. the existing bundle.)*

- **BUILD mode** (no bundle yet — e.g. docs 04–09): run all five phases; phase ② generates the
  criteria + tasks from the spec and the founder seals the bundle before any code.

State which mode you detected before proceeding.

## The phases

## The phases (each names the skill / agent / gate that does it)

1. **UNDERSTAND** — dispatch the `forge:analyst` agent (fresh context) on the spec → an intent
   brief (real intent, hidden/derived obligations, risks, edge/negative cases). This anchors
   everything downstream.

2. **SPECIFY** — invoke the `forge:specify` skill: parallel section-extractors → acceptance
   criteria (EARS-phrased: behavior + oracle + threshold + evidence_class) + the atomic task
   list, each task bound to a criterion. Then GUARANTEE coverage:
   - run the coverage gate: `python3 "${CLAUDE_PLUGIN_ROOT}/gates/coverage.py" <doc>` (must exit 0);
   - dispatch the `forge:criteria-auditor` agent (fresh context) → it must report zero uncovered
     clauses AND zero untestable/weak criteria. If it finds real ambiguity, ask the founder ≤5
     questions and encode the answers. Then the founder SEALS the bundle (it becomes immutable).

3. **PLAN** — partition tasks into file-disjoint streams:
   `python3 "${CLAUDE_PLUGIN_ROOT}/gates/streams.py" <doc>`. For each stream, draft a plan and
   have the `forge:planner-reviewer` agent (fresh context) review it before any code.

4. **BUILD + VERIFY** — for each stream (2–4 concurrent, in isolated git worktrees), invoke the
   `forge:build-slice` skill per task: write the failing acceptance test on the real path → code
   to green → show evidence → flip `passes:true`. After each green task, dispatch the
   `forge:reviewer` agent (fresh context) to review the diff vs the criterion + invariants. The
   verification ladder (static → unit → property → real-infra → real-data) is enforced by the
   hooks + the eval skill. On repeated failure, `/clear` and retry fresh; after N stalls, flag
   `BLOCKED` and continue.

5. **DONE** — run `bash "${CLAUDE_PLUGIN_ROOT}/gates/done-check.sh" --spec <doc>`. Return to the
   founder in exactly one state: **all conjuncts green → production-verified (with evidence)**,
   or a specific **BLOCKED / SPEC_BLOCKED** list. Nothing vague.

## Founder-gated (never auto-approve)
Bundle seal · `_baseline.json` · `EXTRACTION_COUNT_HALT` · migrations · prod deploy.
