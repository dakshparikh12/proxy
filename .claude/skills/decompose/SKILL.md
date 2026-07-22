---
name: decompose
description: Use to turn a sealed acceptance bundle (acceptance/doc<NN>/) into slices/<id>/tasks.json — atomic, dependency-ordered, criterion-linked tasks each carrying an execution-based acceptance check. Runs the RTM-denominator founder gate first.
---

# Decompose a spec into tasks (doc-agnostic)

Turn a doc's sealed acceptance bundle into a build/verify plan. You do NOT parse spec prose — the requirements and per-requirement acceptance criteria are already distilled in `acceptance/doc<NN>/{requirements,criteria}/*.yaml`.

## Steps
1. **Guard the denominator first (founder-gated).** Run `python3 scripts/extraction_count_gate.py doc<NN>`. If it HALTs (`MATERIAL_DISAGREEMENT`), STOP and surface it for founder review — the bundle may be missing whole obligations. Never auto-regenerate or proceed past a HALT. (See `evidence/doc<NN>-extraction-count.md`.)
2. **Emit tasks.** Run `python3 scripts/decompose.py <id>`. This writes `slices/<id>/tasks.json`: one task per criterion, criticality-ordered (P0→P1→P2), each with `criterion_ids`, `requirement_ids`, and an `acceptance.cmd` derived from the criterion's `test_ids` (so it selects the REAL test, e.g. `T-CMP-001` → `pytest -k cmp_001`). A criterion with no `test_ids` gets `acceptance.cmd:null` + a `note` — a legible BLOCKED, never a zero-match false-green.
3. **Close coverage both ways.** Run `python3 scripts/coverage_gate.py doc<NN>` and `python3 scripts/task_coverage.py <id>` (or dispatch the `coverage-auditor` agent). Both must be green. A gap is a founder-gated **bundle** fix, not an agent edit.
4. **Mode.** For docs whose `services/`+`libs/` code already exists (00–03), tasks are `mode:"verify"` (acceptance = "the existing tests are green"), NOT rebuilds — per BUILD-LOOP §9 "no rebuild of working code". Only genuinely-unbuilt requirements are `mode:"build"`.

## Rules
- `tasks.json` and `slices/` are writable; the acceptance bundle is builder-read-only. Never edit `acceptance/`.
- Never bare `uv sync` — use `uv run`.
- The output is a plan, not a claim of doneness. `drive.sh` + `build-slice` execute it; `done-check.sh` decides DONE.
