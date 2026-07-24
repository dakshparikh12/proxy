---
description: Run the forge loop on any spec — understand → complete-the-bundle → plan → build → done → fresh-context spec-audit loop → PRODUCTION-VERIFIED.
argument-hint: <spec-path | doc-id>
---

# /forge — spec → production-verified code

You are driving the **forge** loop for: **$ARGUMENTS**.

Read `${CLAUDE_PLUGIN_ROOT}/README.md` and the skill `forge:forge-loop` first. **You provide the
judgment; the gates provide the boolean; the hooks provide the physics; fresh-context agents provide
the maker≠checker.** Never fake DONE; never edit tests/goldens to pass; flip `passes:true` only after
the real path RAN on real data with evidence shown.

## The ONE standard (identical for both modes)
DONE means **every obligation the spec STATES or IMPLIES is delivered, WIRED into the real product
path, and proven on REAL + MESSY data with the actual OUTPUT inspected against the spec's quality/
latency bar — confirmed by fresh-context agents that did not build it.** Never "the code exists";
never "the criteria are green"; always "the running product does the right thing on real data — fast,
accurate, and honestly-labeled (`resolved`/`lower-bound`/`not-found-by-this-method`)." A capability
that only works when a test injects a double is NOT done.

## Resolve the target (a spec PATH or a doc-id — works for any spec/any repo)
- **A path** (e.g. `product/v0-spec/04-*.md`): that file is the spec; derive a short `<id>`; bundle at `acceptance/<id>/`.
- **A doc-id** (e.g. `00`, `04`): spec = `product/v0-spec/<NN>-*.md`, bundle = `acceptance/doc<NN>/`, slice = `slices/<NN>/`.
State your resolution (spec + bundle dir + id) before proceeding.

## Mode — the ONLY difference is who authors the criteria (everything else is identical)
- **BUILD** (no sealed bundle — e.g. 04–09): phase ② **authors** the criteria from the spec.
- **VERIFY** (sealed bundle exists — e.g. 00–03): phase ② **loads** the bundle, then **re-audits it
  against the raw spec and EXTENDS it where incomplete.** VERIFY never blindly trusts the seal — a
  sealed bundle can under-specify its spec (that is the exact failure this loop closes), so the
  criteria-auditor runs in BOTH modes.
Both modes then run ③④⑤⑥ identically. State the mode you detected before proceeding.

## Autonomy & delegation (when run unattended)
**Auto-approve** (the loop proceeds without asking): extending the bundle with a missing criterion the
auditor derived from the spec; creating/updating `_baseline.json`; any non-prod code/test fix.
**Escalate and STOP** (these stay human, always): **prod deploy · destructive/prod migration ·
`EXTRACTION_COUNT_HALT` · a genuine `SPEC_BLOCKED` spec contradiction.** Surface each with a
recommendation and wait; never auto-approve these.

## Show your work
Live TODO per phase→stream→task-batch; announce each phase transition (`▶ ⑥ SPEC-AUDIT round 2`);
print every gate result (the `coverage.py` line, the full `done-check` table, the ⑥ gap list); surface
every escalation immediately and stop; on `BLOCKED:<id>:<task> <reason>` say what you do next. Prefer a
real artifact (a diff, a test result, an output sample) over prose.

## The phases

**① UNDERSTAND** — dispatch `forge:analyst` (fresh) on the raw spec → an intent brief: real intent;
hidden/derived/**inferred** obligations; edge & negative cases; **and the production bar** (the §8
accuracy/latency thresholds and the messy-estate cases that define "customer quality"). Anchors all of ②–⑥.

**② COMPLETE THE BUNDLE (spec ⇔ criteria closure)** —
- BUILD: `forge:specify` → criteria (EARS: behavior + oracle + threshold + evidence_class) + atomic
  tasks bound to criteria.
- VERIFY: load `acceptance/doc<NN>/`.
- **BOTH, then loop until SEAL-READY:** run `python3 "${CLAUDE_PLUGIN_ROOT}/gates/coverage.py" <id>`
  (id-closure, exit 0) **and** dispatch `forge:criteria-auditor` (fresh — reads the RAW spec + intent
  brief, not the criteria's reasoning). It must report **zero uncovered clauses, zero missed hidden
  obligations, and that a `[eval]` criterion exists for every §8 output-quality / latency / messy-data
  bar.** On REWORK: a **different** fresh agent authors the missing criteria (maker≠checker), extend
  the bundle (auto in autonomous mode; else founder-seal), re-run coverage + auditor.

**③ PLAN** — `python3 "${CLAUDE_PLUGIN_ROOT}/gates/streams.py" <id>` → file-disjoint streams
(contracts first). Per stream, `forge:planner-reviewer` (fresh) locks the plan before any code. Streams
run concurrently in isolated worktrees — **symlink `.venv` into each worktree so tests run there.**

**④ BUILD + VERIFY (the ladder, through the PRODUCT PATH)** — per task, `forge:build-slice`: write the
failing acceptance test that drives the **real product entrypoint** (e.g. `run_full_pipeline` → the
real tool / the real service API) on real data — **never an injected double** — then code to green,
show evidence, flip `passes:true`. After each green task, `forge:reviewer` (fresh) checks the diff vs
the criterion + invariants **AND that it is WIRED into the production path** (an unwired seam is a
fail). The ladder (static → unit → property → real-infra → real-data) is enforced by the hooks +
`forge:eval-gate`, which runs every `[eval]` criterion on **real AND messy estates** and scores the
**output** (correctness, groundedness, completeness, honesty, latency, cost) vs the §8 thresholds
(deterministic graders where possible; a human-calibrated judge only for fuzzy outcomes). On repeated
failure `/clear` + retry fresh; after N stalls, `BLOCKED` + continue other streams.

**⑤ DONE** — `bash "${CLAUDE_PLUGIN_ROOT}/gates/done-check.sh" --spec <id>` (5 conjuncts incl.
real-data eval ≥ baseline). **Run it in the BACKGROUND and poll — never synchronously inside a build
step** (a silent long command trips the agent watchdog; this is what stalled prior runs).

**⑥ SPEC-AUDIT LOOP (the terminal gate — both modes)** — dispatch fresh-context agents that did **not**
build this doc. **Partition the spec into sections and audit in parallel:** each re-derives its
section's obligations from the RAW spec and checks the **running code** — build via the real product
entrypoint on **real + messy** estates, call the real tools, **inspect the actual output against the
quality/latency bar, confirm wiring (no injected seam), measure performance.** Synthesize one gap list.
- **If gaps:** author criteria (fresh) → extend the bundle → loop back to ③/④ for those gaps → re-run
  ⑤ → re-audit ⑥.
- **TERMINATE only when** ⑥ returns zero gaps **AND** ⑤ is green **AND** eval ≥ bar on the messy estate.
- **Convergence guard:** the gap-set must **strictly shrink** each round; if it stalls for 2 rounds, or
  a gap is genuinely infra-blocked (e.g. a language-server binary that cannot be installed here), STOP
  and return the honest `BLOCKED` list — never loop forever, never fake convergence.

## Reliability (baked in from the failure modes)
- **No agent runs `done-check` or any >2-min command synchronously** — background + poll.
- **Every acceptance test drives the real product entrypoint on real data;** ⑥ rejects any capability
  that only works when a test injects it.
- **Parallel docs / streams:** each on its own Postgres port; shared idempotent clone cache; commit
  each green increment (durability across a long run).
- Keep the standing fixes: `done-check` shlex/C4, decompose-preserve, self-healing fixture cache.

## Return exactly one state
**PRODUCTION-VERIFIED** — ⑤ green **and** ⑥ zero-gap **and** eval ≥ bar on real+messy data, with
evidence — or the exact **BLOCKED / SPEC_BLOCKED** list (including anything infra-blocked). Nothing vague.
