# Acceptance-Criteria Generator — the system
### Input: one build spec (e.g. `Doc 01`). Output: an exhaustive, arbiter-tagged, validity-checked criteria set that IS the build loop's stop condition. The loop stops only when every criterion is green at pass^k on every real estate.
### This is the Phase-4 engine. It runs in FRESH CONTEXT (never the builder), so criteria are authored by a different mind than the one that will satisfy them.

## The completeness guarantee (why this set has no gaps by construction)
Gaps are eliminated by a GATE, not by a smarter one-shot pass. Four completeness axes, three mechanical, one asymptotic:
1. **Extraction completeness — GUARANTEED.** The spec is parsed into an enumerated list of atomic requirements (one EARS statement each, uniquely ID'd `R-§3.7-a`). A **Requirements Traceability Matrix (RTM)** links requirements↔criteria bidirectionally. **The generator REFUSES to emit until every requirement has ≥1 linked criterion AND every criterion links back to a requirement.** An unlinked clause fails the gate — the "we forgot call_hierarchy" gap becomes impossible to ship. (Precedent: DO-178C / ISO 26262 requirements-based testing.)
2. **Validity — GUARANTEED.** Every criterion names a mutant that must turn it red (mutmut); one that no mutant kills is rejected.
3. **Real-world truth — GUARANTEED empirically.** Real-repo eval at pass^k proves it works on real data, not by luck.
4. **Failure-mode imagination — ASYMPTOTIC (never provably 100%).** Driven to production-grade by: the fixed 7-dimension expansion taxonomy applied uniformly; property-based search (Hypothesis explores the input space); a fresh-context self-audit before emit; and a living catalog (every escaped failure → a permanent new criterion). This is the one axis no tool closes absolutely — the gate makes it *caught-not-shipped*, not *provably-complete*.

**The generator does not hand back a set that fails axes 1–2.** It self-audits, closes the gaps, and only then emits.

## The principle (why this isn't "an LLM writes tests")
The LLM **proposes** the criteria surface; deterministic machinery **disposes**:
1. **EARS expansion** turns each capability claim into its full failure-sibling set (the exhaustiveness *structure*).
2. **Property-based testing** (Hypothesis / Schemathesis) turns enumerated cases into adversarial searches (the exhaustiveness *engine*).
3. **Mutation testing** (mutmut) proves each criterion can go red (the *validity* rule — a criterion that survives all mutants is invalid).
4. **Real-repo eval** (DeepEval scoring + a SWE-bench-style clone→build→grade harness + Langfuse latency) proves it works *in the world*, not just on fixtures.
A criterion is **admitted only if** it (a) passes on the reference-correct build AND (b) is killed by ≥1 targeted mutant. This is the "Assured" rule (Meta TestGen-LLM): never trust an unexecuted, unfalsifiable criterion.

## Step 0 — Parse the spec into an enumerated requirement list (the RTM rows)
Before any criteria: walk the spec top to bottom and emit one row per atomic testable requirement — `{req_id: R-§<clause>-<n>, EARS statement, source_quote}`. Every **SHALL / must / always / never / within Ns / exactly** is its own row. This enumerated list IS the completeness denominator; the RTM gate (Step 6) checks every row is covered. If a clause can't be reduced to an EARS statement, it's a spec ambiguity — flag it, don't guess.

## Step 1 — Extract capability claims from the requirement list
For each requirement row, pull its **exact-behavior / produces / consumes** intent. Each becomes a *capability claim* traced to its `req_id`. Ground every generated criterion to a `req_id` — a criterion with no `req_id` is hallucinated; a `req_id` with no criterion is a coverage gap the gate will reject.

## Step 2 — Expand each claim across the EARS dimensions
For every capability claim, force-generate its siblings (skip a dimension only if the spec makes it structurally impossible, and say so):

| Dimension | The question it answers | EARS form |
|---|---|---|
| **Capability** | Does the happy path produce the correct, cited result? | WHEN <trigger> THE SYSTEM SHALL <response> |
| **Contract** | Does every output validate the declared schema/envelope? | THE SYSTEM SHALL always <emit schema> |
| **Concurrency/order** | Worst interleaving — race, atomic swap, duplicate/out-of-order event? | WHILE <in-flight> WHEN <event> SHALL <consistency> |
| **Adversarial** (per invariant touched) | Injection, cross-tenant, secret leak, token in log, malformed/huge/binary input, force-push? | IF <hostile> THEN SHALL <contain> |
| **Degradation/fallback** | Dependency hangs/crashes → labeled fallback, never hang or lie? | IF <dep fails> THEN SHALL <fallback + tag> |
| **Measurement** | Latency percentile / cost under a pinned protocol? | THE SYSTEM SHALL <pXX ≤ N under protocol> |
| **Negative-result honesty** | Not-found / lower-bound / external-unresolved shapes correct? | WHEN <nothing found> SHALL <bounded, never "doesn't exist"> |

## Step 3 — Arbiter-tag and assign a grader
Every criterion gets exactly one arbiter and one grader:

- `[unit-fixture]` — fast, on a tiny committed fixture repo, **every pass** (rung 1). Grader: deterministic pytest.
- `[unit-property]` — Hypothesis property (races via `RuleBasedStateMachine`, partitions, round-trips). Rung 1.
- `[contract]` — output validates a declared schema (Schemathesis / JSON-Schema-driven Hypothesis). Rung 1.
- `[eval-realrepo]` — clone a **real pinned repo**, build the component, grade against a golden key at a threshold, **section gate** (rung 2). Grader: deterministic where possible, else calibrated DeepEval G-Eval/DAG judge.
- `[adversarial]` — the attack must be contained; zero invariant violations. Rung 1 or 2 per cost.
- `[latency]` — measured under the §M protocol (N samples, warm, pXX, Langfuse). Rung 2.

## Step 4 — Attach the two-stage validity mutant
Every criterion names the **deliberate breakage that must turn it red** (its mutmut target or a hand-authored broken build). If you cannot name one, the criterion is untestable → rewrite it. This is enforced: a per-section negative build must be caught by its graders, else the gate fails regardless of the green.

## Step 5 — Bind to the real-repo matrix and the reliability gates
- **Estate matrix** (author once per spec, pin SHAs): `single` (small clean) · `monorepo` (≥1M LOC scale) · `polyglot` (multi-language) · `deep-history` · `many-tiny-files` · `no-codeowners` · `unsupported-language` · plus controlled forks: `secrets` (planted), `live` (mutate post-clone), `perms`/`tenant` (isolation). Every `[eval-realrepo]` criterion names its estate.
- **pass^k** — each eval case runs k≈3–5×; **all** must be green (flakiness fails the gate).
- **Judge calibration** — any LLM-judge is gated on Cohen's κ ≥ 0.6 (≥0.8 strong) vs a human gold set; prefer deterministic/DAG graders.
- **Grader-validity** — the negative build (Step 4) must be caught 100%.

## Step 6 — The RTM gate (deterministic; this is what makes gaps impossible to ship)
Build the Requirements Traceability Matrix: a bipartite graph of {every `req_id` from Step 0} ↔ {every criterion}. Every criterion carries a machine-readable `covers = [req_id, …]`. The gate **exits non-zero (refuses to emit) if**: any `req_id` has 0 covering criteria (untested clause) OR any criterion cites no `req_id` (orphan/scope-creep). "Linked" means the test actually ran and passed — feed pytest `--junitxml` in, so a stubbed test doesn't count as coverage. This is a closed, decidable check — a hard green/red bit, not a judgment. (Precedent: OpenFastTrace `failBuild`; DO-178C/ISO 26262 bidirectional traceability.)

## Step 7 — Fresh-context self-audit before emit (close the asymptotic axis)
Before handing back the set, spawn a fresh-context adversarial reviewer (it did NOT author the criteria) to hunt for the failure-modes the expansion missed — the same pass that catches real gaps. Its findings are folded back in and Step 6 re-run. The generator only emits after the RTM gate is green AND the self-audit returns no new gap. This turns "audit finds gaps later" into "generator won't hand you an incomplete set."

## The adequacy stack (three green bits ≈ the strongest automatable completeness proof)
1. **RTM (Step 6)** — every clause has ≥1 linked, passing test. *Proves the spec is addressed.*
2. **Branch coverage (`coverage.py --cov-branch --cov-fail-under`)** — the requirement-driven tests reach every branch; uncovered code = missing requirement / missing test / dead code. *Proves the code is exercised.*
3. **Mutation score (`mutmut`, on changed/high-risk modules, "no new surviving mutants")** — the assertions actually fail when behavior changes. *Proves the tests have teeth.*
All three green = every clause tested, every branch reached, tests would notice if it broke.

## Tooling (OSS, self-host)
Authoring atoms: **EARS** (one statement = one `req_id`). Traceability store + JUnit-XML link: **StrictDoc** (Apache-2.0, Python). Gate semantics: **OpenFastTrace** pattern (`failBuild`) — reimplement as a ~40-line Python RTM check or run OFT if a JVM is acceptable. Exhaustiveness: **Hypothesis** (property search) + **Schemathesis** (schema/contract). Validity: **mutmut**. Branch: **coverage.py**. Real-repo eval: **DeepEval** + SWE-bench-style clone/build/grade harness + **Langfuse** (latency/cost) at **pass^k**. Advisory gap-finder only (never the gate): Spec Kit `/speckit.analyze`.

## Output format (one block per criterion)
```
### <ID> · <name>
- Traces: §<clause>          - Arbiter: [tag]        - Estate: <fixture|estate-X>
- GIVEN <state> WHEN <trigger> THEN <observable outcome>
- Grader: <deterministic | Hypothesis | schema | DeepEval G-Eval/DAG (κ-gated) | Langfuse pXX>
- Threshold: <number / pass^k / recall floor>
- Validity mutant (must go red): <the deliberate breakage>
```

## Stop condition (what "fully working" means)
The build loop stops **only** when: every `[unit-*]`/`[contract]`/`[adversarial]` criterion is green in `verify.sh`, AND every `[eval-realrepo]`/`[latency]` criterion clears its threshold at pass^k on **every** estate (including the messy/monorepo ones), AND every criterion's validity mutant is caught, AND every LLM-judge clears its κ floor. Green fixtures alone are never done.
