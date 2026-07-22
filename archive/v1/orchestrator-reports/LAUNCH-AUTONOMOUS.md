# LAUNCH — the autonomous 00→09 run
## Preconditions (all true as of this commit)
guard.py = the REAL 98-line default-deny (verified blocking) · venv has pytest/hypothesis/ruff/mypy/bandit ·
`claude` CLI logged in on the Max subscription · coverage gate runs (50/50 on doc01) · staging/ model:
agents write staging/, ONLY the conductor promotes into protected trees.

## Launch (one command; leave the lid open, charger in)
    caffeinate -i python3 orchestrator/orchestrate.py --from doc00 2>&1 | tee -a orchestrator/console.log

## What it does per doc (halts loudly rather than guessing)
gen criteria → adversarial review (≤3 cycles) → gen evidence (tests+fixtures+sims+goldens) →
RTM coverage gate → PROMOTE+SEAL (hash) → plan+review → build loop (fresh session/pass, scoped
verify: ruff+pytest blocking per pass; mypy+bandit block at doc-end) → independent verifier
(refute; tamper-checked) → completeness sweep (≤3 cycles, extends criteria if gaps) →
cross-doc regression (accumulated test scope) → tag → next doc.

## Honest notes for the morning triage
- HALT reasons are printed and final: SPEC_BLOCKED (a spec bug — fix the spec, rerun --from that
  doc), CRITERIA_NOT_APPROVED / COVERAGE_GATE_FAILED (generator needs a founder look),
  VERIFIER_REFUTED / SWEEP_UNRESOLVED (real gaps found — read evidence/<doc>-*.md), STALL /
  MAX_PASSES / WALL_CLOCK / INTEGRITY_VIOLATION (runner-class stops).
- rung-2 real-data eval is NOT in tonight's gates (eval_runner is a stub) — tonight's bar is the
  sealed criteria + sims + independent verification. Real-data rung comes later, per plan.
- Mutation gate auto-skips if mutmut is absent (logged). mypy --strict debt can consume the
  doc-end remediation round — that is intended, not a hang.
- FIRST EXECUTION: this conductor has never run end-to-end before tonight. The caps, the guard,
  the integrity hash, and loud halts bound the blast radius; expect to triage something in the
  morning. Read: orchestrator/run.log · PROGRESS.md · evidence/ · git log --oneline.
