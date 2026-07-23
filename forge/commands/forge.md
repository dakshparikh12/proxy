---
description: Run the forge build loop on any spec — understand → specify → plan → build → verify → DONE.
argument-hint: <spec-path | doc-id>
---

# /forge — spec → production-verified code

You are driving the **forge** build loop for the target: **$ARGUMENTS**.

## Resolve the target (accepts a spec PATH or a doc-id — works for any spec)
- **A path to a spec file** (e.g. `product/v0-spec/04-*.md`, or any `path/to/spec.md` in any repo):
  that file is the spec. Derive a short `<id>` from its name; the bundle lives at
  `acceptance/<id>/` (create it in BUILD mode, or find the existing sealed one).
- **A bare doc-id** (e.g. `00`, `03`) — this repo's convention: spec = `product/v0-spec/<NN>-*.md`,
  bundle = `acceptance/doc<NN>/`, slice = `slices/<NN>/`.
If the argument is ambiguous, state your resolution (spec file + bundle dir + id) before proceeding.

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

## Show your work (live progress — the founder is watching, for iteration)
Keep the founder oriented at all times. Concretely:
- **Maintain a live TODO list** (TodoWrite) — one item per phase, then per stream, then a running
  task-batch counter. Mark items in_progress / completed as you go, so the founder sees the checklist move.
- **Announce every phase transition** in one line: e.g. `▶ Phase ② SPECIFY (VERIFY mode, doc00)`.
- **After the stream partition, print the streams** (module + task count each) so they see the shape.
- **Report progress in batches**, not silently: e.g. `verified 40/157 tasks green · 1 red (T-AC-SUB-034) · 0 blocked`.
  Show the actual test output for a task you just fixed (evidence, not assertion).
- **Print every gate's result** — the `coverage.py` line and the full `done-check --spec` 5-conjunct table.
- **Surface every founder gate immediately and stop** — show the exact decision (e.g. the SUB-034
  sealed-test contradiction) with a recommendation, and wait. Never proceed past a founder gate silently.
- **On any BLOCKED**, print `BLOCKED:<id>:<task> <reason>` and what you're doing next (continue other streams).
- End with a clear **status line**: production-verified (with the done-check table), or the exact BLOCKED list.
Prefer showing a small, real artifact (a diff, a test result, a table) over prose claims.

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
