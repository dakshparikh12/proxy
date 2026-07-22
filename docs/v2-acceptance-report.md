# v2 build-loop — acceptance report (does it actually work?)

_Generated 2026-07-22 at `8e8573f`. Method: 5 fresh-context ADVERSARIAL validators,
each required to RUN its subsystem and try to break it (the v1 antidote: independent
execution, not trust). Every claim below is backed by a command a validator ran and
quoted. Cross-checked against ground truth: offline suite 774 pass / 2 fail, ruff clean._

## Bottom line

**v2 is NOT 100%.** The scaffolding is real and much of it genuinely works when run —
but there are **2 CRITICAL anti-gaming holes** (one of which is literally the v1
"claims-to-work-but-doesn't" failure mode) plus several HIGH/MEDIUM defects. **Do not
trust this loop to build/verify doc00–09 until at least the CRITICAL + HIGH items are
fixed.** The good news: everything found is fixable, all in unprotected code
(`done-check.sh`, `.claude/hooks/`, `scripts/`), and the trustworthy core (`--task`
mode, the gates, deletions/salvage/wiring) is proven.

## What is PROVEN working (ran + can-fail verified)

- **`done-check.sh --task` mode is SOUND** — genuinely runs the acceptance cmd; forced
  probes caught passes:false, a real red cmd (exit 1), and exit-5-no-tests. This is the
  loop's trustworthy unit.
- **done-check conjuncts C1/C4/C5/C9/C10 are real + can-fail** — incl. C10's CVE
  ordering (3 gitpython CVEs correctly not masked by "could not be audited").
- **Engine scripts** `lib_spec`, `coverage_gate`, `task_coverage`, `cost_log`, `journey`
  all run and were each forced RED on synthetic breakage (they can fail — not vacuous).
- **v1 fully deleted** from the tracked tree (`d9e60db`); **salvage works**
  (`coverage_gate`/`extraction_count_gate` clean of orchestrate deps); **founder gate
  never auto-approves** (injecting 2× count → `HALT: MATERIAL_DISAGREEMENT`); **`spike/`
  kept** (3 bld tests pass); **`archive/v1/` intact**.
- **Protected authoring** — 2 agents + 2 skills well-formed; **decompose skill ↔ script
  ↔ done-check schema is consistent**; CLAUDE.md commands accurate; **settings.json wires
  all 4 hooks to existing files**; **eval scaffold runs (2 passed)**; judge model
  `claude-sonnet-4-6` valid; naming lint real + can-fail.
- **Stop-gate fix (this session) independently verified** — blocks passes:false, allows
  passes:true, 3-stall flag-and-continue.
- **The 2 offline test failures are PRE-EXISTING, not v2 regressions** — proven by git
  archaeology (byte-identical test files at pre-v2 merge-base `131715b`, same 2 failures
  reproduced there). They match the documented known-benign baseline.

## Defect ledger (ranked)

### 🔴 CRITICAL — must fix before trusting the loop

**C-1. `done-check --spec` trusts the `passes` flags for all-but-one task.**
`[3] all-tasks-pass` is a pure flag read (`all(t.get("passes"))` — never runs a test).
`[8] mutation-spotcheck` only exercises the FIRST task with a cmd. Validator B built
`slices/tmpc8lie`: 157 real doc00 criterion IDs, task 1 green, **156 flagged
`passes:true` but actually RED (exit 5)** → C2 PASS, C3 PASS, C8 PASS while task #50's
real cmd returns exit 5. **A spec can report all-green with 156/157 tasks unproven.**
Only `--task` runs the real path (per-task via the Stop gate). _This is the v1 gap._
→ **Fix:** make `--spec` re-derive truth — run every task's acceptance cmd (or C8 samples
all/N, and/or C3 executes each passes:true task's cmd) instead of trusting flags.

**C-2. `subagent_stop.sh` secret scan is DEAD on macOS.** The scanner uses `grep -P`
(PCRE); BSD `/usr/bin/grep` (what `/bin/bash` resolves) rejects `-P` (rc=2), so every
secret `if` is false. Validator C ran a real `sk-proj-…` token through the hook via
`/bin/bash` → **ALLOW** (unblocked). Passed its unit tests only because the harness PATH
had GNU grep. The protected-path arm (`grep -E`) is fine.
→ **Fix:** replace `grep -P` with a portable engine (`python3 -c` regex).

### 🟠 HIGH

**H-3. `pretool_guard.py` NotebookEdit bypass.** Reads only `tool_input["file_path"]`, but
`NotebookEdit` uses `notebook_path`. A `NotebookEdit` to `tests/x.ipynb` → **ALLOW**
(notebooks execute Python). NotebookEdit is in the matcher but effectively unguarded.
→ **Fix:** also read `notebook_path` (and any edit-family path key).

**H-4. done-check `[6] invariants` is inert.** DEFERRED because pre-commit isn't installed
and `ops` isn't importable outside pytest → checks **zero** invariants today; even the
fallback `check_secret_bindings` is a no-op on empty input. Honest (DEFERRED blocks DONE)
but the invariant wall isn't actually enforced by done-check.
→ **Fix:** install pre-commit or make the ops fallback import + run real inputs.

**H-5. done-check `[7] eval≥baseline` — the "≥baseline" isn't implemented.** Existence of
`_baseline.json` is the only gate; `tests/eval` has no baseline reference and `-k "00"`
matches no eval node, so C7 can never go green for doc00. Fails honestly (never lies) but
provides no real-data signal yet.
→ **Fix:** implement the numeric floor + per-doc eval tests (needs real eval runs).

**H-6. decompose acceptance binding fails for doc02/doc03.** `pytest -k "<test_id-fragment>"`
only binds when the test FUNCTION name embeds the id. doc00 = fully bound (121/157 offline
+ 36 correctly integration-gated, 0 name-mismatch). **doc02 = 0/164** — its tests are named
descriptively and cite the criterion only in a docstring (`criterion_id: AC-CANVAS-09`),
which `-k` can't match. doc03 partial. Not a false-green (exit-5 is caught) but makes
02/03 **un-verifiable** through the loop.
→ **Fix:** resolve test_ids via docstring scan → emit `pytest <file>::<func>`.

### 🟡 MEDIUM

**M-7. `stop_gate.sh` stall-hash degraded on bash 3.2.** `${CHECK_OUT: -1500}` returns empty
for output < 1500 chars → distinct short failures hash identically → "3 *identical*
failures" degrades to "3 stops of any failure." Fails **safe** (flags sooner, never
deadlocks). → **Fix:** `tail -c 1500`.

**M-8. `drive.sh` cost ceiling has no producer.** It reads logged spend and aborts on
`V2_COST_CEILING_USD`, but nothing in drive.sh appends spend, so the ceiling can't trip
unless another layer logs cost. Enforcement wired; producer missing.
→ **Fix:** append per-phase spend, or document the producer contract.

**M-9. `journey.py` contracts allow-list by whole line, not path.** `check_contracts_resolve_to_libs`
skips a re-declaration if `"contracts" not in line` where `line` is `path:lineno:source`
— a re-declaration with "contracts" in a comment or path escapes detection. Loose.
→ **Fix:** match against the path field only.

### ⚪ LOW / hygiene

**L-10. decompose `-k` substring over-match** — `-k "sub_03"` collects `sub_030/034/…`.
No live collision (doc00 ids zero-padded); latent for future un-padded ids.
**L-11. `orchestrator/state/` + empty `harness/`** linger on disk as untracked cruft.

## Go / no-go

- **doc00 (Foundation):** the code+tests are real and bound, but the loop's DONE predicate
  can't yet be trusted at `--spec` level (C-1) — **NO-GO until C-1 fixed.**
- **doc01:** as doc00 + 16 binding drifts.
- **doc02 / doc03:** **NO-GO** — un-verifiable through decompose until H-6.
- **doc04–09:** depend on the same loop → gated behind CRITICAL + HIGH fixes.

**Recommended sequence:** fix C-1, C-2, H-3, M-7 (core trust + guards; quick, ratcheted) →
re-run this validation → then H-6 (unblock 02/03) → then the journey.
