# v2 readiness — doc00–03 verification journey (go/no-go)

_Generated 2026-07-22 at `8675b0b` (Phase 6, Task 6.1). This is the go/no-go for
the 4-track parallel verification journey (§10.6). Per-conjunct tables from
`./done-check.sh --spec <id>`; binding analysis from decompose output vs. the
live collected pytest node ids._

## Verdict summary

| Track | Doc | Verdict | Blocker |
|-------|-----|---------|---------|
| Foundation  | 00 | **READY** | none to start; work items below are the journey's job |
| Code-Intel  | 01 | **READY (caveats)** | 16/91 acceptance patterns drift; bundle YAML strict-parse quirk |
| Voice       | 02 | **BLOCKED** | 164/164 acceptance cmds bind to **zero** tests (decompose selector mismatch) |
| Notes       | 03 | **BLOCKED** | 50 criteria empty `test_ids` + ~150 descriptive-name mismatches |

## Per-doc DONE-predicate tables

All four are NOT DONE, as expected (these are *verification* tracks — the passes
flags are false until the journey runs each acceptance cmd). Conjuncts:

| # | conjunct | 00 | 01 | 02 | 03 | meaning of the non-PASS |
|---|----------|----|----|----|----|----|
| 1 | coverage(req↔crit) | PASS | PASS | PASS | PASS | — |
| 2 | tasks(crit↔task)   | PASS | PASS | PASS | PASS | — |
| 3 | all-tasks-pass     | FAIL | FAIL | FAIL | FAIL | **expected** — passes:false until the journey verifies each task |
| 4 | offline suite+ruff | FAIL | FAIL | FAIL | FAIL | ruff CLEAN; **2 pre-existing known-benign fails** (not v2 regressions) — see below |
| 5 | integration(Doc09§2) | PASS | PASS | PASS | PASS | — |
| 6 | invariants         | DEFERRED | DEFERRED | DEFERRED | DEFERRED | pre-commit not installed; ops-module fallback unavailable |
| 7 | eval≥baseline      | FAIL | FAIL | FAIL | FAIL | **no `_baseline.json`** — real-data eval not yet run per doc (not fabricated) |
| 8 | mutation-spotcheck | PASS | PASS | **FAIL** | PASS | doc02 first task's cmd collected no tests (exit 5) — the binding gap, caught |
| 9 | regressions ratchet| PASS | PASS | PASS | PASS | — |
| 10| mechanical(pip-audit) | FAIL | FAIL | FAIL | FAIL | **3 `gitpython 3.1.50` CVEs** — founder-gated dep bump |

### C4 detail (identical every run)
`ruff: All checks passed!` · pytest: **774 passed / 2 failed / 1 skipped / 1 xfailed**.
The 2 failures are the documented pre-existing known-benign baseline fails:
- `tests/doc00/test_m03_sub.py::test_sub_034_...` — mis-tiered DB test needing live
  Postgres; also carries a **cross-doc schema contradiction** (see doc00 work items).
- `tests/reality/test_cassette_hygiene.py::test_no_secret_leaks...` — false-positive.

Neither was introduced by the v2 build-loop work (baseline held 772/2 throughout).

## The binding finding (why 02/03 are BLOCKED)

`decompose.py` derives each acceptance cmd as `pytest -k "<fragment>"`, where the
fragment comes from the criterion's `test_ids` (`T-CANVAS-09` → `canvas_09`). This
binds a task to a real test **only if the physical test function name embeds that
fragment**. Measured against the live collected node ids (921 tests):

| Doc | tasks | unbound (`-k` matches 0 tests) | why |
|-----|-------|-------------------------------|-----|
| 00 | 157 | **0** | test functions are named `test_cmp_001`, `test_sub_034` — fragment matches |
| 01 | 91  | 16 | test_id ↔ function-name drift (e.g. `T-M3-007`→`m3_009`; dup `m1_006`) |
| 02 | 164 | **164** | tests are named descriptively (`test_camera_and_screen_mutually_exclusive`); the criterion is bound only via the docstring `criterion_id: AC-CANVAS-09`, which `pytest -k` cannot select |
| 03 | 309 | ~150 unbound + 50 `cmd:None` | mix of empty `test_ids` and descriptive-name mismatch |

doc02/doc03 **are built** — the code and tests exist and pass; the criterion→test
map lives in test **docstrings**, not function names. The gap is purely in how
decompose emits the selector.

## Recommended remediation (before/at the journey)

1. **Engine — decompose binding (unblocks 02, most of 03).** Resolve each
   `test_id`/`criterion_id` to a real pytest node by scanning test docstrings for
   `criterion_id: AC-XXX` (the convention doc02/03 already use) and emit
   `pytest <file>::<func>` (or a nodeids file), instead of a name-fragment `-k`.
   Keep the name-fragment path as a fallback (doc00/01 rely on it). TDD + reviewer.
2. **Engine — done-check conjunct 8 depth.** It spot-checks only the *first* task
   with a cmd, so a doc with 1 bound + 163 unbound tasks would still PASS C8. Make
   it sample N tasks (or assert every task's cmd collects ≥1 test). This is the
   check that *did* catch doc02 — it just needs breadth.
3. **Bundle — doc03 `test_ids`.** 50 criteria have empty `test_ids`; founder-gated
   bundle fix to populate them (or confirm they are genuinely unbuilt → build tasks).
4. **doc00 in-track item.** Resolve `test_sub_034`: the test inserts
   `transcript_segments(meeting_id=NULL)` but §3.3 seals `meeting_id uuid NOT NULL`
   (CANONICAL §11.2). Sealed-test-vs-canon contradiction — founder adjudication
   (amend the test's incidental NULL to a uuid, preserving the criterion's real
   intent: status-default + atomic flip).
5. **Baselines (C7).** Run the real-data DeepEval per doc to produce honest
   `_baseline.json` regression floors (human-gated thereafter).
6. **Invariants (C6).** `pip install pre-commit` (or make the ops-module fallback
   importable) so invariants are enforced, not DEFERRED.
7. **Deps (C10, founder-gated).** Bump `gitpython` past the 3 CVEs.

## Bottom line

- **doc00 (Foundation) is READY** to verify now: coverage closed, 157/157 tasks
  bind to real tests, integration contracts green.
- **doc01 (Code-Intel) is READY** after reconciling 16 drifted patterns.
- **doc02/doc03 are BLOCKED** on the decompose binding fix (item 1) — a well-scoped
  engine increment, not a spec/founder decision. Do it first, then 02/03 rejoin.
