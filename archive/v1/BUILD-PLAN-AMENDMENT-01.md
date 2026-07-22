# BUILD-PLAN AMENDMENT 01 — Generator v2 Adoption

*Status: overrides `docs/BUILD-SYSTEM.md` and `RUNBOOK.md` where they conflict. Scope: small — five deltas. Everything else in the build plan stands.*

## Why

The v1 generator's Step-2 flat 7-dimension expansion was over-engineered for a 2-person V0: ~30 requirements × 7 forced sibling-dimensions ≈ 210 criteria, each needing a validity mutant and founder review — making review the bottleneck (external research assessment, 2026-07-16, confirmed against Meta ACH/TestGen-LLM practice: exhaustive expansion is reserved for invariant-critical requirements; branch coverage + mutation score carry completeness for the rest). Generator **v2** replaces flat expansion with **applicability-triggered dimensions (Phase D) + risk-tiered depth (§1.5)** and adds sealed-bundle independence. This amendment records what the build plan must change to match.

## The five deltas

**1. Criteria are no longer authored per-doc by founders — they are generated as a sealed Acceptance Bundle.**
RUNBOOK Step 1.2–1.3 ("write §8 in the 1.1-ingest format, grade A/B/C by hand") is superseded. The flow is now: freeze spec → run Generator v2 in fresh context → founder reviews the *bundle* (requirements.yaml, dispositions, criteria) → seal + hash. Founder review moves from authoring criteria to approving the RTM denominator and ambiguity dispositions — the highest-leverage review point per the false-confidence research (mechanical coverage of wrong requirements is the residual RTM risk).

**2. The bundle is builder-read-only, enforced by the existing harness.**
Add `acceptance/` to `guard.py` PROTECTED (alongside tests/, fixtures/, eval/). The v2 principle "the builder cannot modify tests, thresholds, estates, goldens, verifier code, or evidence" (§1.1) is exactly what guard.py + the protected-tree hash check already enforce — no new machinery, one tuple entry.

**3. One verify.sh, not two.**
v2's bundle layout places a `verifier/verify.sh` inside the bundle. We keep **`harness/verify.sh` as the sole rung-1 arbiter** (constitution rule); the bundle's verifier directory holds the generated test/evaluator *sources*, which verify.sh discovers via the normal pytest path. The RTM/junit-xml evidence gate (v2 Phase K) runs **after** verify.sh in CI, consuming `--junitxml` output — not inside it. `assert_registry_closed()` stays inside verify.sh (import-time contract check, every pass).

**4. `SPEC_BLOCKED` is a first-class loop outcome.**
v2 §1.3: a derived obligation the spec omits either amends the spec or halts with `SPEC_BLOCKED`. Wire this into the runner the same way the pass_prompt already treats untestable criteria: the loop stops, PROGRESS.md records the spec gap, a founder repairs the spec, the bundle re-generates (new version, prior evidence invalidated). Never patch criteria mid-run.

**5. Verification depth follows risk, not uniformity — and the supporting tool changes.**
Full-depth expansion applies only where Phase D triggers fire and §1.5 risk classes hold (tenant isolation, irreversible writes, recovery paths, concurrency, cost/latency promises, escaped defects). Adopted from the research pass alongside this: mutation tooling moves **mutmut → cosmic-ray** (scoped to changed/high-risk modules, nightly job, not per-pass); internal contract fuzzing uses **hypothesis-jsonschema** against `libs/contracts` schemas (Schemathesis reserved for the control_plane HTTP surface); LLM-judge calibration uses **weighted kappa ≥0.6** with deterministic/DAG graders preferred so the judge gate rarely fires.

## Unchanged
Two-rung arbiter design · maker≠checker · fresh-session-per-pass · pre-authored-evidence-only (now: pre-*generated*, sealed) · milestone-ordered pytest · pass^k on eval criteria · estate matrix as rung-2 fixtures (start at 3–4 pinned repos, not 7–8) · stall detection, caps, evidence folders, dual signoff.
