---
name: build-slice
description: Use to execute ONE task from slices/<id>/tasks.json via TDD on real data — write the failing acceptance test that runs the real path, code to green, show evidence, flip passes:true. Never edit tests/bundle/baseline; BLOCKED on stall.
---

# Build (or verify) one slice

Execute exactly ONE pending task from `slices/<id>/tasks.json`. One task per session; `/clear` between tasks. Stable prefix (CLAUDE.md/AGENTS.md/spec) first for prompt-cache; volatile state (tasks.json/diff) last.

## The loop
1. **Pick one task** — the first `passes:false` task whose `depends_on` are all satisfied. Write `slices/<id>/.current` = `<id>:<task_id>` (the Stop gate reads it).
2. **Read the anchor** — the task's `criterion_ids` → the given/when/then behavior + oracle + thresholds in `acceptance/doc<NN>/criteria/criteria.yaml`. That is the intended property.
3. **TDD on the REAL path.** For a `build` task: write the failing acceptance test that exercises the real seam (real transcript → real scribe → real notes → real persistence), watch it fail, then write minimal code to green. For a `verify` task (docs 00–03, already built): run the task's `acceptance.cmd`; if it's already green, the code exists — your job is to CONFIRM it on real/held-out data and show the output, not rewrite it. If it's red, fix the product code (never the test).
4. **Show evidence.** Run the real path and paste the output. Assertion-only checks are banned as acceptance — the real path must have RUN.
5. **Flip the bit.** Only after step 4 passes on real data, set `passes:true` for this task in `tasks.json`. Then clear `.current`.

## Hard rules (guard-enforced)
- **Never edit** `tests/`, `tests/cassettes/`, `acceptance/`, `fixtures/`, `goldens/`, or any `_baseline.json`. These are builder-read-only. `_baseline.json` changes are founder-gated.
- **Never fake green.** A `pytest -k` that selects zero tests is a FAIL, not a pass. Flip `passes:true` only on real evidence.
- **Stall → BLOCKED.** If the same failure repeats N times, append `BLOCKED:<id>:<task_id> <reason>` to `slices/<id>/progress.md` and move on — flag-and-continue, never deadlock, never silently claim done. If the spec itself is the blocker, record `SPEC_BLOCKED` for founder repair.
- Every external call via `libs/http` `call_external`; every model call via `libs/llm`. No secrets in code/logs. User-visible strings carry no internal names.
- Never bare `uv sync` — use `uv run`; if the venv is broken, `uv sync --all-packages` then reinstall the pinned tools.

When the task is green with evidence, hand back. `done-check.sh --spec <id>` decides overall DONE — not you.
